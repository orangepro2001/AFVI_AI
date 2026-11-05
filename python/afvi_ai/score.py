"""Rank merge points of exported LOTs with a frozen model artifact for VRS review.

Writes one CSV per LOT (highest risk first) plus summary.json. Each row carries the
model family's held-out evidence from the selection report, so reviewers can see
where the ranking was validated. Scores are review priorities, not pass/fail calls.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baseline import LogisticModel, model_from_dict
from .train import DEFAULT_MAX_LINE_BYTES, _stream, input_files, write_report

CSV_FIELDS = (
    "rank",
    "score",
    "top_percent",
    "machine",
    "model_family",
    "result_group",
    "strip",
    "side",
    "unit_idx",
    "img_merge_idx",
    "image_ref",
    "result_data_image",
    "image_saved",
    "area_total",
    "length_max",
    "width_max",
    "light_id",
    "sr_metal",
    "is_dummy",
    "vrs_code",
    "family_in_training",
    "family_heldout_roc_auc",
    "family_heldout_recall_at_30",
)


def family_evidence(artifact: dict[str, Any]) -> dict[str, dict[str, object]]:
    metadata = artifact.get("metadata") or {}
    evidence: dict[str, dict[str, object]] = {}
    for entry in ((metadata.get("held_out_breakdown") or {}).get("model") or []):
        evidence[str(entry["name"])] = {
            "heldout_roc_auc": entry.get("model_roc_auc"),
            "heldout_recall_at_30": entry.get("model_recall_at_30"),
            "heldout_lots": entry.get("lots"),
        }
    return evidence


def rank_lots(
    model: LogisticModel,
    paths: tuple[Path, ...],
    *,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
) -> tuple[dict[str, list[tuple[float, Any]]], dict[str, int]]:
    """Score every row; return per-LOT lists sorted by descending score, and unscored counts."""
    by_lot: dict[str, list[tuple[float, Any]]] = defaultdict(list)
    unscored: dict[str, int] = defaultdict(int)
    for sample in _stream(paths, max_line_bytes=max_line_bytes, image_index=None, verify_images=False):
        score = model.score(sample)
        if score is None:
            unscored[sample.lot] += 1
            continue
        by_lot[sample.lot].append((score, sample))
    for rows in by_lot.values():
        rows.sort(key=lambda item: item[0], reverse=True)
    return by_lot, unscored


def source_image_path(sample: Any) -> str:
    """Defect image path relative to the ResultData share root, as written by the inspection program."""
    if not sample.image_rel:
        return ""
    return f"ResultData/Data/{sample.key.machine}/{sample.key.result_group}/{sample.image_rel}"


def image_saved(share_root: Path | None, sample: Any) -> str:
    """Stat (never read) the defect image below the share root; the inspector does not save every image."""
    relative = source_image_path(sample)
    if share_root is None or not relative:
        return ""
    return "yes" if (share_root / relative).is_file() else "no"


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write per-LOT VRS review priority lists from a frozen AFVI model.")
    parser.add_argument("--model", required=True, type=Path, help="model JSON written by afvi_ai.train --final-model")
    parser.add_argument("--input", required=True, type=Path, help="export JSONL file or directory")
    parser.add_argument("--output-dir", required=True, type=Path, help="destination directory outside Git")
    parser.add_argument("--share-root", type=Path, help="read-only share root that contains ResultData, for --check-images")
    parser.add_argument("--check-images", type=int, default=0, help="stat the image file for the top N points of each LOT")
    args = parser.parse_args(argv)

    if args.check_images < 0:
        parser.error("--check-images must not be negative")
    if args.check_images and args.share_root is None:
        parser.error("--check-images requires --share-root")
    repository_root = Path(__file__).resolve().parents[2]
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir == repository_root or repository_root in output_dir.parents:
        parser.error("--output-dir must be outside the Git repository")
    try:
        artifact = json.loads(args.model.read_text(encoding="utf-8"))
        model = model_from_dict(artifact)
        evidence = family_evidence(artifact)
        training_families = set((artifact.get("metadata") or {}).get("training_families") or [])
        training_lots = set(artifact.get("training_lots") or [])
        by_lot, unscored = rank_lots(model, input_files(args.input))
        output_dir.mkdir(parents=True, exist_ok=True)
        lots_summary = []
        for lot in sorted(by_lot):
            rows = by_lot[lot]
            families = sorted({sample.key.result_group.split("/", 1)[0] for _, sample in rows})
            path = output_dir / f"{_safe_name(lot)}.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(CSV_FIELDS)
                total = len(rows)
                for rank, (score, sample) in enumerate(rows, start=1):
                    family = sample.key.result_group.split("/", 1)[0]
                    family_info = evidence.get(family, {})
                    features = sample.features
                    writer.writerow(
                        (
                            rank,
                            f"{score:.6f}",
                            f"{100.0 * rank / total:.2f}",
                            sample.key.machine,
                            family,
                            sample.key.result_group,
                            sample.key.strip,
                            sample.key.side,
                            sample.key.unit_idx,
                            sample.key.img_merge_idx,
                            sample.image_ref or "",
                            source_image_path(sample),
                            image_saved(args.share_root, sample) if rank <= args.check_images else "",
                            features.get("area_total"),
                            features.get("length_max"),
                            features.get("width_max"),
                            features.get("light_id"),
                            features.get("sr_metal"),
                            features.get("is_dummy"),
                            sample.ivs_1 if sample.vrs_observed else "",
                            "yes" if family in training_families else "no",
                            "" if family_info.get("heldout_roc_auc") is None else f"{family_info['heldout_roc_auc']:.3f}",
                            ""
                            if family_info.get("heldout_recall_at_30") is None
                            else f"{family_info['heldout_recall_at_30']:.3f}",
                        )
                    )
            lots_summary.append(
                {
                    "lot": lot,
                    "file": path.name,
                    "scored_points": len(rows),
                    "unscored_points": unscored.get(lot, 0),
                    "families": families,
                    "lot_in_model_training": lot in training_lots,
                    "families_without_heldout_evidence": [family for family in families if family not in evidence],
                    "families_not_in_training": [family for family in families if family not in training_families],
                }
            )
        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": {"file": args.model.name, "version": artifact.get("version"), "feature_set": artifact["feature_spec"]["name"]},
            "note": "Scores rank already-detected merge points for VRS review priority; they are not pass/fail decisions or machine parameters.",
            "family_evidence": evidence,
            "lots": lots_summary,
        }
        write_report(output_dir / "summary.json", summary)
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"afvi-score: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"lots": len(lots_summary), "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
