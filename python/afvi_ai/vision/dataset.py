"""Build a local, downscaled image set of VRS-reviewed defect crops.

Merge points are sampled per (LOT, label) from the exports with the same seeded reservoir as
training. Each point's main defect image (``p<merge>.bmp`` below its strip) is read once from
the read-only share under a byte-rate limit, converted to RGB, resized, and stored as JPEG
outside Git. A manifest records label, LOT, inspection condition and the evaluation split.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from ..train import input_files, load_sampled

DEFAULT_SIZE = 192
DEFAULT_PER_LOT_CLASS = 900
DEFAULT_MAX_MBPS = 50.0
MAX_SOURCE_BYTES = 8 * 1024 * 1024


class RateLimiter:
    """Shared byte budget: callers sleep so the average read rate stays under the limit."""

    def __init__(self, bytes_per_second: float) -> None:
        self.rate = bytes_per_second
        self.start = time.monotonic()
        self.consumed = 0
        self.lock = threading.Lock()

    def consume(self, amount: int) -> None:
        with self.lock:
            self.consumed += amount
            ahead = self.consumed / self.rate - (time.monotonic() - self.start)
        if ahead > 0:
            time.sleep(ahead)


def image_key(sample: Any) -> str:
    key = sample.key
    text = f"{key.machine}|{key.result_group}|{key.strip}|{key.side}|{key.unit_idx}|{key.img_merge_idx}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def source_path(share_root: Path, sample: Any) -> Path:
    return share_root / "ResultData" / "Data" / sample.key.machine / sample.key.result_group / sample.image_rel


def source_candidates(
    sample: Any,
    *,
    share_root: Path | None,
    machine_roots: dict[str, Path] | None = None,
    caches: dict[str, Path] | None = None,
) -> list[tuple[Path, bool]]:
    """Where a point's image may be, best first, each with whether reading it touches a share.

    1. the host's JPEG cache of that machine (pximaged, PxSpecTool docs/features/result-archive-jpg.md §6:
       ``<cache>/<result_group>/<image_rel>`` with ``.jpg``): local, no share load;
    2. the BMP on the machine's share (``machine_roots`` names a share root per machine, else ``share_root``);
    3. the JPEG beside it on the share: a LOT older than five months may have been replaced on the NAS.
    """
    machine = sample.key.machine
    rel = sample.image_rel
    jpg_rel = rel[:-4] + ".jpg" if rel.lower().endswith(".bmp") else None
    out: list[tuple[Path, bool]] = []
    cache = (caches or {}).get(machine)
    if cache is not None and jpg_rel:
        out.append((cache / sample.key.result_group / jpg_rel, False))
    root = (machine_roots or {}).get(machine, share_root)
    if root is not None:
        base = root / "ResultData" / "Data" / machine / sample.key.result_group
        out.append((base / rel, True))
        if jpg_rel:
            out.append((base / jpg_rel, True))
    return out


def convert(data: bytes, size: int) -> bytes:
    with Image.open(BytesIO(data)) as image:
        image.load()
        rgb = image.convert("RGB")
    resized = rgb.resize((size, size), Image.Resampling.BILINEAR)
    buffer = BytesIO()
    resized.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def manifest_row(sample: Any, relative: str, split: str) -> dict[str, object]:
    features = sample.features
    return {
        "image": relative,
        "lot": sample.lot,
        "family": sample.key.result_group.split("/", 1)[0],
        "machine": sample.key.machine,
        "result_group": sample.key.result_group,
        "side": sample.key.side,
        "light_id": features.get("light_id"),
        "channel_idx": features.get("channel_idx"),
        "sr_metal": features.get("sr_metal"),
        "code_main": features.get("code_main"),
        "is_dummy": features.get("is_dummy"),
        "ivs_1": sample.ivs_1,
        "label": sample.label,
        "weight": sample.weight,
        "split": split,
    }


def build(
    samples: list[Any],
    *,
    share_root: Path | None,
    output_dir: Path,
    split_of: dict[str, str],
    size: int = DEFAULT_SIZE,
    workers: int = 4,
    max_mbps: float = DEFAULT_MAX_MBPS,
    machine_roots: dict[str, Path] | None = None,
    caches: dict[str, Path] | None = None,
) -> dict[str, int]:
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(max_mbps * 1024 * 1024)
    counts = {"written": 0, "existing": 0, "missing": 0, "failed": 0, "from_cache": 0}
    lock = threading.Lock()
    rows: list[dict[str, object]] = []

    def work(sample: Any) -> None:
        key = image_key(sample)
        relative = f"images/{key[:2]}/{key}.jpg"
        destination = output_dir / relative
        status = "existing"
        cached = False
        if not destination.exists():
            candidates = source_candidates(sample, share_root=share_root, machine_roots=machine_roots, caches=caches)
            source = candidates[0][0] if candidates else Path(sample.image_rel)
            try:
                found = next(((path, shared) for path, shared in candidates if path.is_file()), None)
                if found is None:
                    status = "missing"
                else:
                    source, shared = found
                    length = source.stat().st_size
                    if length > MAX_SOURCE_BYTES:
                        raise ValueError("source image too large")
                    if shared:
                        limiter.consume(length)
                    cached = not shared
                    encoded = convert(source.read_bytes(), size)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_suffix(".part")
                    temporary.write_bytes(encoded)
                    temporary.replace(destination)
                    status = "written"
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                status = "failed"
                print(f"failed {source}: {error}", file=sys.stderr)
        with lock:
            counts[status] += 1
            if cached and status == "written":
                counts["from_cache"] += 1
            if status in ("written", "existing"):
                rows.append(manifest_row(sample, relative, split_of.get(sample.lot, "excluded")))
            done = sum(counts.values())
            if done % 5000 == 0:
                print(json.dumps({"progress": done, **counts}), flush=True)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(work, samples))

    rows.sort(key=lambda row: str(row["image"]))
    with (output_dir / "manifest.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return counts


def _pairs(values: list[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for value in values:
        machine, sep, directory = value.partition("=")
        if not sep or not machine or not directory:
            raise ValueError(f"expected MACHINE=DIR, got {value!r}")
        out[machine] = Path(directory)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the downscaled defect-image set for the AFVI image model.")
    parser.add_argument("--input", required=True, type=Path, help="v2 export JSONL file or directory")
    parser.add_argument("--split-report", required=True, type=Path, help="report whose train/test LOT lists define the split")
    parser.add_argument("--share-root", type=Path, help="read-only share root containing ResultData (machines without --machine-root)")
    parser.add_argument("--machine-root", action="append", default=[], metavar="MACHINE=DIR", help="share root of one machine; repeatable")
    parser.add_argument("--cache", action="append", default=[], metavar="MACHINE=DIR", help="pximaged JPEG cache of one machine; repeatable")
    parser.add_argument("--output-dir", required=True, type=Path, help="dataset directory outside Git")
    parser.add_argument("--per-lot-class", type=int, default=DEFAULT_PER_LOT_CLASS)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-mbps", type=float, default=DEFAULT_MAX_MBPS, help="average source read limit in MiB/s")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[3]
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir == repository_root or repository_root in output_dir.parents:
        parser.error("--output-dir must be outside the Git repository")
    if args.per_lot_class < 1 or args.size < 32 or args.workers < 1 or args.max_mbps <= 0:
        parser.error("invalid limits")
    try:
        machine_roots = _pairs(args.machine_root)
        caches = _pairs(args.cache)
    except ValueError as error:
        parser.error(str(error))
    if args.share_root is None and not machine_roots:
        parser.error("give --share-root or --machine-root")

    report = json.loads(args.split_report.read_text(encoding="utf-8"))
    split = report["evaluation"]["split"]
    split_of = {lot: "train" for lot in split["train_lots"]}
    split_of.update({lot: "test" for lot in split["test_lots"]})
    samples, sampling = load_sampled(input_files(args.input), max_rows_per_lot_state=args.per_lot_class, seed=args.seed)
    labeled = [
        sample
        for sample in samples
        if sample.label in (0, 1) and not sample.excluded and sample.vrs_observed and sample.image_rel
    ]
    print(json.dumps({"sampled": len(samples), "labeled_with_image_path": len(labeled)}), flush=True)
    counts = build(
        labeled,
        share_root=args.share_root,
        output_dir=output_dir,
        split_of=split_of,
        size=args.size,
        workers=args.workers,
        max_mbps=args.max_mbps,
        machine_roots=machine_roots,
        caches=caches,
    )
    (output_dir / "dataset.json").write_text(
        json.dumps(
            {
                "size": args.size,
                "per_lot_class": args.per_lot_class,
                "seed": args.seed,
                "split_report": args.split_report.name,
                "sampling": sampling,
                "counts": counts,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )
    print(json.dumps(counts), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
