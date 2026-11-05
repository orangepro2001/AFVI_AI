from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .baseline import MAX_REPORTED_SAMPLES, build_report
from .contracts import normalize_export_record, validate_unique_keys
from .manifest import ManifestIndex

DEFAULT_MAX_ROWS = 250_000
DEFAULT_MAX_LINE_BYTES = 1_000_000


def load_jsonl(
    path: Path,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    image_index: ManifestIndex | None = None,
    verify_images: bool = False,
) -> tuple[Any, ...]:
    if max_rows < 1 or max_line_bytes < 1:
        raise ValueError("input limits must be positive")
    samples = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if len(line.encode("utf-8")) > max_line_bytes:
                raise ValueError(f"JSONL row {line_number} exceeds {max_line_bytes} bytes")
            if not line.strip():
                continue
            if len(samples) >= max_rows:
                raise ValueError(f"input exceeds row limit ({max_rows}); no rows were silently dropped")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL row {line_number}: {error}") from error
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row {line_number} must be an object")
            try:
                samples.append(
                    normalize_export_record(row, image_index=image_index, verify_image=verify_images)
                )
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid JSONL row {line_number}: {error}") from error
    validate_unique_keys(samples)
    return tuple(samples)


def write_report(path: Path, report: dict[str, object]) -> None:
    destination = Path(path).expanduser().resolve()
    repository_root = Path(__file__).resolve().parents[2]
    if destination == repository_root or repository_root in destination.parents:
        raise ValueError("report output must be outside the Git repository")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, prefix=".afvi-report-", delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _cutoff(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        cutoff = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError("cutoff must be an ISO timestamp") from error
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise argparse.ArgumentTypeError("cutoff must include a timezone offset")
    return cutoff


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a conservative AFVI historical-VRS risk ranking report.")
    parser.add_argument("--input", required=True, type=Path, help="PxSpecTool merge-point JSONL export")
    parser.add_argument("--output", required=True, type=Path, help="external JSON report path, e.g. AFVI_DATA_HOME/reports/latest.json")
    parser.add_argument("--cutoff", required=True, type=_cutoff, help="timezone-aware LOT/time split cutoff")
    parser.add_argument("--manifest", type=Path, help="existing JPG scanner manifest JSONL")
    parser.add_argument("--jpg-root", type=Path, help="JPG output root from the scanner")
    parser.add_argument("--image-label", help="single directory label beneath jpg-root")
    parser.add_argument("--verify-images", action="store_true", help="check JPG file existence without reading image bytes")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--max-line-bytes", type=int, default=DEFAULT_MAX_LINE_BYTES)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--report-sample-limit", type=int, default=MAX_REPORTED_SAMPLES)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    manifest_args = (args.manifest, args.jpg_root, args.image_label)
    if any(value is not None for value in manifest_args) and not all(value is not None for value in manifest_args):
        parser.error("--manifest, --jpg-root, and --image-label must be used together")
    if args.verify_images and args.manifest is None:
        parser.error("--verify-images requires manifest options")
    if args.max_rows < 1 or args.max_line_bytes < 1 or args.report_sample_limit < 1:
        parser.error("row, line, and report sample limits must be positive")
    if not 0 <= args.test_fraction < 1:
        parser.error("--test-fraction must be in [0, 1)")

    image_index = None
    if args.manifest is not None:
        image_index = ManifestIndex.load(args.manifest, args.jpg_root, args.image_label)
    try:
        samples = load_jsonl(
            args.input,
            max_rows=args.max_rows,
            max_line_bytes=args.max_line_bytes,
            image_index=image_index,
            verify_images=args.verify_images,
        )
        report = build_report(
            samples,
            cutoff=args.cutoff,
            data_source={
                "export": args.input.name,
                "manifest": args.manifest.name if args.manifest is not None else "not_joined",
                "machines": ",".join(sorted({sample.key.machine for sample in samples})),
            },
            test_fraction=args.test_fraction,
            seed=args.seed,
            report_sample_limit=args.report_sample_limit,
        )
        write_report(args.output, report)
    except (OSError, TypeError, ValueError) as error:
        print(f"afvi-train: {error}", file=sys.stderr)
        return 2

    evaluation = report["evaluation"]
    model = report["model"]
    print(
        json.dumps(
            {
                "samples": len(samples),
                "evaluation": evaluation["status"],
                "model": model["status"],
                "report": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
