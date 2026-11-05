"""Per-LOT label coverage of merge-point exports, used to fix a split cutoff before training.

Only label states and timestamps are read; no model is fitted and no score is
computed, so choosing a cutoff from this table cannot leak held-out performance.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .baseline import MIN_CLASS_LOTS, MIN_CLASS_ROWS, MIN_TEST_LOTS, MIN_TRAIN_LOTS
from .train import DEFAULT_MAX_LINE_BYTES, _stream, input_files, label_state


def lot_coverage(paths: tuple[Path, ...], *, max_line_bytes: int = DEFAULT_MAX_LINE_BYTES) -> list[dict[str, object]]:
    """Return one row per LOT with label-state counts and first/last inspection time."""
    lots: dict[str, dict[str, object]] = {}
    for sample in _stream(paths, max_line_bytes=max_line_bytes, image_index=None, verify_images=False):
        entry = lots.setdefault(
            sample.lot,
            {
                "lot": sample.lot,
                "machines": set(),
                "models": set(),
                "positive": 0,
                "assumed_good": 0,
                "unlabeled": 0,
                "conflicting": 0,
                "untimed": 0,
                "first_at": None,
                "last_at": None,
            },
        )
        entry["machines"].add(sample.key.machine)
        entry["models"].add(sample.key.result_group.split("/", 1)[0])
        entry[label_state(sample)] += 1
        if sample.inspected_at is None or sample.inspected_at.utcoffset() is None:
            entry["untimed"] += 1
            continue
        if entry["first_at"] is None or sample.inspected_at < entry["first_at"]:
            entry["first_at"] = sample.inspected_at
        if entry["last_at"] is None or sample.inspected_at > entry["last_at"]:
            entry["last_at"] = sample.inspected_at
    rows = []
    for entry in sorted(lots.values(), key=lambda item: (item["first_at"] is None, item["first_at"] or datetime.max)):
        rows.append(
            {
                **entry,
                "machines": sorted(entry["machines"]),
                "models": sorted(entry["models"]),
                "first_at": entry["first_at"].isoformat() if entry["first_at"] else None,
                "last_at": entry["last_at"].isoformat() if entry["last_at"] else None,
            }
        )
    return rows


def cutoff_support(rows: list[dict[str, object]], cutoff: datetime) -> dict[str, object]:
    """Count LOT support on each side of a cutoff, before the random pre-cutoff holdout.

    A LOT with any point at or after the cutoff goes wholly to the post-cutoff side,
    matching ``split_by_lot_time``; LOTs with untimed rows are excluded.
    """
    sides: dict[str, dict[str, int]] = {
        name: {"lots": 0, "positive": 0, "assumed_good": 0, "positive_lots": 0, "assumed_good_lots": 0}
        for name in ("before", "after")
    }
    for row in rows:
        last_at = row["last_at"]
        if last_at is None:
            continue
        if datetime.fromisoformat(last_at) >= cutoff:
            side = sides["after"]
        elif row["untimed"]:
            continue
        else:
            side = sides["before"]
        side["lots"] += 1
        for label in ("positive", "assumed_good"):
            side[label] += int(row[label])
            side[f"{label}_lots"] += int(row[label] > 0)
    gates = {
        "min_train_lots": MIN_TRAIN_LOTS,
        "min_test_lots": MIN_TEST_LOTS,
        "min_class_rows": MIN_CLASS_ROWS,
        "min_class_lots": MIN_CLASS_LOTS,
    }
    return {"cutoff": cutoff.isoformat(), "sides": sides, "gates": gates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize per-LOT VRS label coverage of AFVI exports.")
    parser.add_argument("--input", required=True, type=Path, help="export JSONL file or directory")
    parser.add_argument("--output", required=True, type=Path, help="coverage JSON destination outside Git")
    parser.add_argument("--cutoff", action="append", default=[], help="candidate ISO cutoff with offset; repeatable")
    args = parser.parse_args(argv)

    rows = lot_coverage(input_files(args.input))
    candidates = []
    for value in args.cutoff:
        cutoff = datetime.fromisoformat(value)
        if cutoff.utcoffset() is None:
            parser.error("--cutoff must include a timezone offset")
        candidates.append(cutoff_support(rows, cutoff))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"lots": rows, "cutoffs": candidates}, ensure_ascii=False, indent=1) + "\n")
    labeled = [row for row in rows if row["positive"] or row["assumed_good"]]
    print(
        json.dumps(
            {
                "lots": len(rows),
                "labeled_lots": len(labeled),
                "positive_lots": sum(1 for row in rows if row["positive"]),
                "cutoffs": [{"cutoff": item["cutoff"], **item["sides"]} for item in candidates],
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
