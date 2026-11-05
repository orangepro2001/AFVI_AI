from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import random
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .baseline import (
    DEFAULT_NEGATIVE_WEIGHT,
    FEATURE_SPECS,
    MAX_REPORTED_SAMPLES,
    ModelConfig,
    build_report,
    fit_logistic,
    model_to_dict,
)
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


def input_files(path: Path) -> tuple[Path, ...]:
    """Return one JSONL file, or every ``*.jsonl`` below a directory in sorted order."""
    source = Path(path)
    if source.is_dir():
        files = tuple(sorted(candidate for candidate in source.rglob("*.jsonl") if candidate.is_file()))
        if not files:
            raise ValueError(f"no .jsonl files below {source}")
        return files
    return (source,)


def label_state(sample: Any) -> str:
    """Sampling stratum inside a LOT; keeps every label state represented."""
    if sample.excluded:
        return "conflicting"
    if sample.label is None:
        return "unlabeled"
    return "positive" if sample.label == 1 else "assumed_good"


def load_sampled(
    paths: tuple[Path, ...],
    *,
    max_rows_per_lot_state: int,
    seed: int = 0,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    image_index: ManifestIndex | None = None,
    verify_images: bool = False,
) -> tuple[tuple[Any, ...], dict[str, object]]:
    """Stream exports and keep a seeded reservoir sample per (LOT, label state).

    Each kept row carries ``weight = stratum_total / kept`` so LOT-level class
    proportions and coverage totals match the full export.
    """
    if max_rows_per_lot_state < 1 or max_line_bytes < 1:
        raise ValueError("input limits must be positive")
    reservoirs: dict[tuple[str, str], list[Any]] = defaultdict(list)
    totals: dict[tuple[str, str], int] = defaultdict(int)
    generators: dict[tuple[str, str], random.Random] = {}
    seen_keys: set[object] = set()
    for sample in _stream(paths, max_line_bytes=max_line_bytes, image_index=image_index, verify_images=verify_images):
        if sample.key in seen_keys:
            raise ValueError(f"duplicate sample key: {sample.key}")
        seen_keys.add(sample.key)
        stratum = (sample.lot, label_state(sample))
        totals[stratum] += 1
        reservoir = reservoirs[stratum]
        if len(reservoir) < max_rows_per_lot_state:
            reservoir.append(sample)
            continue
        generator = generators.get(stratum)
        if generator is None:
            digest = hashlib.sha256(f"{seed}\0{stratum[0]}\0{stratum[1]}".encode("utf-8")).digest()
            generator = generators[stratum] = random.Random(int.from_bytes(digest[:8], "big"))
        slot = generator.randrange(totals[stratum])
        if slot < max_rows_per_lot_state:
            reservoir[slot] = sample
    samples = []
    for stratum in sorted(reservoirs):
        kept = reservoirs[stratum]
        weight = totals[stratum] / len(kept)
        samples.extend(dataclasses.replace(sample, weight=weight) for sample in kept)
    sampling = {
        "method": "seeded_reservoir_per_lot_label_state",
        "max_rows_per_lot_state": max_rows_per_lot_state,
        "input_files": len(paths),
        "input_rows": sum(totals.values()),
        "kept_rows": len(samples),
        "sampled_strata": sum(1 for stratum, total in totals.items() if total > len(reservoirs[stratum])),
    }
    return tuple(samples), sampling


def _stream(
    paths: tuple[Path, ...],
    *,
    max_line_bytes: int,
    image_index: ManifestIndex | None,
    verify_images: bool,
) -> Iterator[Any]:
    for path in paths:
        with Path(path).open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if len(line.encode("utf-8")) > max_line_bytes:
                    raise ValueError(f"{path}: JSONL row {line_number} exceeds {max_line_bytes} bytes")
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}: invalid JSONL row {line_number}: {error}") from error
                if not isinstance(row, dict):
                    raise ValueError(f"{path}: JSONL row {line_number} must be an object")
                try:
                    yield normalize_export_record(row, image_index=image_index, verify_image=verify_images)
                except (TypeError, ValueError) as error:
                    raise ValueError(f"{path}: invalid JSONL row {line_number}: {error}") from error


def final_model_artifact(samples: tuple[Any, ...], report: dict[str, object]) -> dict[str, object]:
    """Refit the report's selected configuration on all labeled LOTs for scoring future LOTs."""
    model_info = report["model"]
    config = ModelConfig(FEATURE_SPECS[model_info["feature_set"]], float(model_info["l2_strength"]))
    labeled = [row for row in samples if row.label in (0, 1) and not row.excluded and row.vrs_observed]
    model = fit_logistic(labeled, negative_weight=DEFAULT_NEGATIVE_WEIGHT, l2=config.l2, spec=config.spec)
    evaluation = report["evaluation"]
    return model_to_dict(
        model,
        training_lots=sorted({row.lot for row in labeled}),
        metadata={
            "generated_at": datetime.now().astimezone().isoformat(),
            "selection_report_generated_at": report["generated_at"],
            "selection": model_info.get("selection"),
            "evaluation_status": evaluation["status"],
            "held_out_metrics": evaluation["metrics"],
            "held_out_bootstrap": evaluation["bootstrap"],
            "held_out_lots": evaluation["split"]["test_lots"],
            "holdout_history": evaluation.get("holdout_history"),
            "held_out_breakdown": evaluation.get("breakdown"),
            "review_curve": evaluation.get("review_curve"),
            "training_families": sorted({row.key.result_group.split("/", 1)[0] for row in labeled}),
            "sampling": report.get("sampling"),
            "data_source": report["data_source"],
            "note": "Refit on all labeled LOTs with the selected settings; confirm only on LOTs absent from training_lots.",
        },
    )


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
    parser.add_argument("--input", required=True, type=Path, help="PxSpecTool merge-point JSONL export, or a directory of them")
    parser.add_argument("--output", required=True, type=Path, help="external JSON report path, e.g. AFVI_DATA_HOME/reports/latest.json")
    parser.add_argument("--cutoff", required=True, type=_cutoff, help="timezone-aware LOT/time split cutoff")
    parser.add_argument("--manifest", type=Path, help="existing JPG scanner manifest JSONL")
    parser.add_argument("--jpg-root", type=Path, help="JPG output root from the scanner")
    parser.add_argument("--image-label", help="single directory label beneath jpg-root")
    parser.add_argument("--verify-images", action="store_true", help="check JPG file existence without reading image bytes")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument(
        "--max-rows-per-lot-state",
        type=int,
        help="seeded reservoir sample size per (LOT, label state); kept rows are re-weighted to the full totals",
    )
    parser.add_argument("--max-line-bytes", type=int, default=DEFAULT_MAX_LINE_BYTES)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--report-sample-limit", type=int, default=MAX_REPORTED_SAMPLES)
    parser.add_argument(
        "--tune",
        action="store_true",
        help="choose feature set and L2 by LOT-grouped cross-validation on training LOTs only",
    )
    parser.add_argument("--tuning-workers", type=int, default=1, help="parallel processes for --tune")
    parser.add_argument("--holdout-note", help="record how often this held-out LOT set has been evaluated before")
    parser.add_argument(
        "--final-model",
        type=Path,
        help="also refit the selected configuration on every labeled LOT and save the model JSON (outside Git)",
    )
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
    if args.tuning_workers < 1:
        parser.error("--tuning-workers must be positive")
    if args.max_rows_per_lot_state is not None and args.max_rows_per_lot_state < 1:
        parser.error("--max-rows-per-lot-state must be positive")
    if args.input.is_dir() and args.max_rows_per_lot_state is None:
        parser.error("a directory --input requires --max-rows-per-lot-state")
    sampling: dict[str, object] | None = None
    try:
        if args.max_rows_per_lot_state is None:
            samples = load_jsonl(
                args.input,
                max_rows=args.max_rows,
                max_line_bytes=args.max_line_bytes,
                image_index=image_index,
                verify_images=args.verify_images,
            )
        else:
            samples, sampling = load_sampled(
                input_files(args.input),
                max_rows_per_lot_state=args.max_rows_per_lot_state,
                seed=args.seed,
                max_line_bytes=args.max_line_bytes,
                image_index=image_index,
                verify_images=args.verify_images,
            )
            if len(samples) > args.max_rows:
                raise ValueError(f"sampled rows ({len(samples)}) exceed --max-rows ({args.max_rows})")
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
            sampling=sampling,
            tune=args.tune,
            tuning_workers=args.tuning_workers,
            holdout_history=args.holdout_note,
        )
        write_report(args.output, report)
        if args.final_model is not None:
            write_report(args.final_model, final_model_artifact(samples, report))
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
