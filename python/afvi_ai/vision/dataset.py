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
    share_root: Path,
    output_dir: Path,
    split_of: dict[str, str],
    size: int = DEFAULT_SIZE,
    workers: int = 4,
    max_mbps: float = DEFAULT_MAX_MBPS,
) -> dict[str, int]:
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(max_mbps * 1024 * 1024)
    counts = {"written": 0, "existing": 0, "missing": 0, "failed": 0}
    lock = threading.Lock()
    rows: list[dict[str, object]] = []

    def work(sample: Any) -> None:
        key = image_key(sample)
        relative = f"images/{key[:2]}/{key}.jpg"
        destination = output_dir / relative
        status = "existing"
        if not destination.exists():
            source = source_path(share_root, sample)
            try:
                if not source.is_file():
                    status = "missing"
                else:
                    length = source.stat().st_size
                    if length > MAX_SOURCE_BYTES:
                        raise ValueError("source image too large")
                    limiter.consume(length)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the downscaled defect-image set for the AFVI image model.")
    parser.add_argument("--input", required=True, type=Path, help="v2 export JSONL file or directory")
    parser.add_argument("--split-report", required=True, type=Path, help="report whose train/test LOT lists define the split")
    parser.add_argument("--share-root", required=True, type=Path, help="read-only share root containing ResultData")
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
