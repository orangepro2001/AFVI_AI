"""Score never-seen LOTs with a frozen model artifact and apply the pre-registered verdict.

Nothing is refitted. LOTs named in the artifact's ``training_lots`` are dropped before
scoring, so every evaluated LOT is new to the model.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .baseline import (
    MIN_CLASS_LOTS,
    MIN_CLASS_ROWS,
    MIN_TEST_LOTS,
    REFERENCE_NAME,
    LogisticModel,
    _breakdown,
    _class_counts,
    _class_lot_counts,
    _has_both_classes,
    _lot_bootstrap,
    _paired_auc_bootstrap,
    _usable_label,
    _weighted_metrics,
    model_from_dict,
    reference_score,
)
from .train import DEFAULT_MAX_LINE_BYTES, input_files, load_sampled, write_report


def confirm(model: LogisticModel, samples: Sequence[Any], *, training_lots: set[str], seed: int = 0) -> dict[str, object]:
    new_rows = [row for row in samples if row.lot not in training_lots]
    skipped_lots = sorted({row.lot for row in samples if row.lot in training_lots})
    labeled = [row for row in new_rows if _usable_label(row)]
    class_lots = _class_lot_counts(labeled)
    class_rows = _class_counts(labeled)
    lots = sorted({row.lot for row in labeled})
    reasons = []
    if len(lots) < MIN_TEST_LOTS:
        reasons.append(f"Need at least {MIN_TEST_LOTS} new labeled LOTs.")
    if not _has_both_classes(labeled):
        reasons.append("New LOTs do not contain both label classes.")
    for name, count in class_lots.items():
        if count < MIN_CLASS_LOTS:
            reasons.append(f"New {name} labels need at least {MIN_CLASS_LOTS} independent LOTs.")
    if min(class_rows.values()) < MIN_CLASS_ROWS:
        reasons.append(f"New LOTs need at least {MIN_CLASS_ROWS} rows from each label class.")

    result: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "new_lots": lots,
        "skipped_seen_lots": skipped_lots,
        "class_rows": class_rows,
        "class_lots": class_lots,
        "status": "insufficient_support" if reasons else "evaluated",
        "reasons": reasons,
        "metrics": None,
        "bootstrap": None,
        "reference": None,
        "breakdown": None,
        "verdict": None,
    }
    if reasons:
        return result

    paired = []
    for row in labeled:
        score = model.score(row)
        reference = reference_score(row)
        if score is not None and reference is not None:
            paired.append((row, score, reference))
    scored = [(row, score) for row, score, _ in paired]
    metrics = _weighted_metrics(scored)
    reference_metrics = _weighted_metrics([(row, reference) for row, _, reference in paired])
    bootstrap = _lot_bootstrap(scored, seed=seed)
    paired_bootstrap = _paired_auc_bootstrap(paired, seed=seed)
    lower = (bootstrap or {}).get("intervals") or {}
    auc_low = lower.get("roc_auc", {}).get("low") if lower else None
    beats_random = auc_low is not None and auc_low > 0.5
    beats_reference = metrics["roc_auc"] > reference_metrics["roc_auc"]
    result.update(
        metrics={**metrics, "evaluated_rows": len(scored)},
        bootstrap=bootstrap,
        reference={"name": REFERENCE_NAME, "metrics": reference_metrics, "paired_bootstrap": paired_bootstrap},
        breakdown={
            "machine": _breakdown(paired, lambda row: row.key.machine),
            "model": _breakdown(paired, lambda row: row.key.result_group.split("/", 1)[0]),
        },
        verdict={
            "qualified": beats_random and beats_reference,
            "auc_lower_bound_above_0_5": beats_random,
            "beats_reference_point_estimate": beats_reference,
        },
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confirm a frozen AFVI model on LOTs it has never seen.")
    parser.add_argument("--model", required=True, type=Path, help="model JSON written by afvi_ai.train --final-model")
    parser.add_argument("--input", required=True, type=Path, help="export JSONL file or directory with new LOTs")
    parser.add_argument("--output", required=True, type=Path, help="confirmation JSON destination outside Git")
    parser.add_argument("--max-rows-per-lot-state", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.max_rows_per_lot_state < 1:
        parser.error("--max-rows-per-lot-state must be positive")

    try:
        artifact = json.loads(args.model.read_text(encoding="utf-8"))
        model = model_from_dict(artifact)
        samples, sampling = load_sampled(
            input_files(args.input),
            max_rows_per_lot_state=args.max_rows_per_lot_state,
            seed=args.seed,
            max_line_bytes=DEFAULT_MAX_LINE_BYTES,
        )
        result = confirm(model, samples, training_lots=set(artifact["training_lots"]), seed=args.seed)
        result["model"] = {"path": args.model.name, "version": artifact["version"], "feature_set": artifact["feature_spec"]["name"]}
        result["sampling"] = sampling
        write_report(args.output, result)
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"afvi-confirm: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "new_lots": len(result["new_lots"]), "verdict": result["verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
