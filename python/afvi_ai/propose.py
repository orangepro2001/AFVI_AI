"""Draft minimum-defect-size proposals from detected merge points (not a machine spec file).

For each model family and inspection condition (side, light, channel, SR/metal region),
find the largest lower limit on ``area_max`` (largest member blob area, pixels) whose
LOT-averaged loss of VRS-keyed points stays within a budget on training LOTs, then
measure the same limit on held-out LOTs. Only raising the limit can be evaluated:
points below the machine's current limit were never detected.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from .train import DEFAULT_MAX_LINE_BYTES, _stream, input_files

SIX_DIGIT_CODE = re.compile(r"^\d{6}$")
DEFAULT_BUDGETS = (0.005, 0.01)
MIN_TRAIN_LOTS = 3
MIN_KEYED_POINTS = 50

CellKey = tuple[str, str, str, str, str]
"""(family, side, light_id, channel_idx, sr_metal)."""


@dataclass
class LotHistogram:
    keyed: Counter
    other: Counter
    _prefix: tuple[list[int], list[int], list[int]] | None = None

    def totals(self) -> tuple[int, int]:
        return sum(self.keyed.values()), sum(self.other.values())

    def below(self, limit: int) -> tuple[int, int]:
        """Keyed and other counts strictly below ``limit`` (histograms are frozen once queried)."""
        if self._prefix is None:
            values = sorted(set(self.keyed) | set(self.other))
            keyed_prefix = [0]
            other_prefix = [0]
            for value in values:
                keyed_prefix.append(keyed_prefix[-1] + self.keyed.get(value, 0))
                other_prefix.append(other_prefix[-1] + self.other.get(value, 0))
            self._prefix = (values, keyed_prefix, other_prefix)
        values, keyed_prefix, other_prefix = self._prefix
        index = bisect.bisect_left(values, limit)
        return keyed_prefix[index], other_prefix[index]


def aggregate(samples: Iterable[Any]) -> tuple[dict[CellKey, dict[str, LotHistogram]], dict[str, Counter]]:
    """Exact per-cell, per-LOT histograms of area_max for reviewed points, plus keyed codes per family."""
    cells: dict[CellKey, dict[str, LotHistogram]] = defaultdict(dict)
    codes: dict[str, Counter] = defaultdict(Counter)
    for sample in samples:
        if sample.excluded or not sample.vrs_observed or sample.label not in (0, 1):
            continue
        area = sample.features.get("area_max")
        if isinstance(area, bool) or not isinstance(area, (int, float)) or area < 0:
            continue
        family = sample.key.result_group.split("/", 1)[0]
        key = (
            family,
            sample.key.side,
            str(sample.features.get("light_id")),
            str(sample.features.get("channel_idx")),
            str(sample.features.get("sr_metal")),
        )
        histogram = cells[key].get(sample.lot)
        if histogram is None:
            histogram = cells[key][sample.lot] = LotHistogram(Counter(), Counter())
        value = int(round(float(area)))
        if sample.label == 1:
            histogram.keyed[value] += 1
            codes[family][sample.ivs_1 or ""] += 1
        else:
            histogram.other[value] += 1
    return cells, codes


def unverified_families(codes: dict[str, Counter]) -> set[str]:
    """Families whose keyed codes are mostly six-digit numbers; their code meaning is not established."""
    flagged = set()
    for family, counter in codes.items():
        total = sum(counter.values())
        six_digit = sum(count for code, count in counter.items() if SIX_DIGIT_CODE.match(code))
        if total and six_digit / total >= 0.5:
            flagged.add(family)
    return flagged


def lot_averaged_effect(lots: dict[str, LotHistogram], limit: int) -> tuple[float | None, float | None, int]:
    """Average over LOTs of (keyed share below limit, reviewed share below limit); LOTs need keyed points."""
    losses: list[float] = []
    reductions: list[float] = []
    for histogram in lots.values():
        keyed_total, other_total = histogram.below(sys.maxsize)
        if keyed_total == 0:
            continue
        keyed_below, other_below = histogram.below(limit)
        losses.append(keyed_below / keyed_total)
        reductions.append((keyed_below + other_below) / (keyed_total + other_total))
    if not losses:
        return None, None, 0
    return sum(losses) / len(losses), sum(reductions) / len(reductions), len(losses)


def largest_limit(lots: dict[str, LotHistogram], budget: float) -> int:
    """Largest observed area value usable as a strict lower limit within the loss budget."""
    candidates = sorted({value for histogram in lots.values() for value in (*histogram.keyed, *histogram.other)})
    best = candidates[0] if candidates else 0
    for value in candidates:
        loss, _, _ = lot_averaged_effect(lots, value)
        if loss is None or loss > budget:
            break
        best = value
    return best


def propose(
    cells: dict[CellKey, dict[str, LotHistogram]],
    *,
    train_lots: set[str],
    test_lots: set[str],
    budgets: tuple[float, ...] = DEFAULT_BUDGETS,
    skipped_families: set[str] = frozenset(),
) -> list[dict[str, object]]:
    results = []
    for key in sorted(cells):
        family, side, light, channel, region = key
        lots = cells[key]
        train = {lot: histogram for lot, histogram in lots.items() if lot in train_lots}
        test = {lot: histogram for lot, histogram in lots.items() if lot in test_lots}
        train_keyed_lots = [histogram for histogram in train.values() if histogram.totals()[0] > 0]
        train_keyed = sum(histogram.totals()[0] for histogram in train.values())
        observed = [value for histogram in lots.values() for value in (*histogram.keyed, *histogram.other)]
        entry: dict[str, object] = {
            "family": family,
            "side": side,
            "light_id": light,
            "channel_idx": channel,
            "sr_metal": region,
            "observed_min_area": min(observed) if observed else None,
            "reviewed_points": sum(sum(h.totals()) for h in lots.values()),
            "keyed_points": sum(h.totals()[0] for h in lots.values()),
            "train_lots_with_keyed": len(train_keyed_lots),
            "test_lots": len(test),
            "status": "proposed",
            "proposals": [],
        }
        if family in skipped_families:
            entry["status"] = "skipped_code_meaning_unverified"
        elif len(train_keyed_lots) < MIN_TRAIN_LOTS or train_keyed < MIN_KEYED_POINTS:
            entry["status"] = "keep_current_insufficient_data"
        else:
            for budget in budgets:
                limit = largest_limit(train, budget)
                train_loss, train_reduction, _ = lot_averaged_effect(train, limit)
                test_loss, test_reduction, test_lot_count = lot_averaged_effect(test, limit)
                entry["proposals"].append(
                    {
                        "loss_budget": budget,
                        "min_area": limit,
                        "raises_limit": entry["observed_min_area"] is not None and limit > entry["observed_min_area"],
                        "train_keyed_loss": train_loss,
                        "train_review_reduction": train_reduction,
                        "heldout_lots": test_lot_count,
                        "heldout_keyed_loss": test_loss,
                        "heldout_review_reduction": test_reduction,
                    }
                )
            if not any(proposal["raises_limit"] for proposal in entry["proposals"]):
                entry["status"] = "keep_current_no_safe_increase"
        results.append(entry)
    return results


def to_xml(results: list[dict[str, object]], *, basis: dict[str, str]) -> ElementTree.Element:
    root = ElementTree.Element(
        "AfviParameterProposal",
        {
            "version": "0.1",
            "status": "DRAFT_NOT_FOR_MACHINE_USE",
            "generated": datetime.now(timezone.utc).isoformat(),
        },
    )
    ElementTree.SubElement(root, "Basis", basis)
    notes = ElementTree.SubElement(root, "Notes")
    for text in (
        "설비 스펙 파일이 아닙니다. InspectionSpec.xml 에 직접 넣지 마십시오.",
        "minArea = 각 merge point 의 가장 큰 블롭 면적(area_max, pixel) 하한 후보입니다. 대응 스펙 항목 후보: 1024/1025 Min Defect Size(Pixel), 1002 Bright / 1011 Dark Defect Size(Pixel) — 대응 여부 미검증.",
        "하한을 올리는 방향만 평가 가능합니다. 현재 하한보다 작은 포인트는 데이터에 없습니다.",
        "keyedLoss = VRS 에서 작업자가 코드를 입력한 포인트 중 하한 미만으로 빠지는 비율(LOT 평균). 실제 불량이면 유출됩니다.",
        "train = 학습 LOT 에서 하한을 정함, heldOut = 학습에 쓰지 않은 LOT 에서 같은 하한을 적용한 결과.",
        "light/channel/srMetal 은 ResultData 에 기록된 원값입니다(LIGHT 폴더 번호, R/G/B, SR/Metal 대응은 미확인).",
        "적용 전 같은 프레임·마스크·스펙으로 설비 결과를 재현하는 리플레이 검증과 품질팀의 허용 손실 승인이 필요합니다.",
    ):
        ElementTree.SubElement(notes, "Note").text = text
    by_family: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in results:
        by_family[str(entry["family"])].append(entry)
    for family in sorted(by_family):
        family_element = ElementTree.SubElement(root, "Model", {"name": family})
        for entry in by_family[family]:
            condition = ElementTree.SubElement(
                family_element,
                "Condition",
                {
                    "side": str(entry["side"]),
                    "light": str(entry["light_id"]),
                    "channel": str(entry["channel_idx"]),
                    "srMetal": str(entry["sr_metal"]),
                    "status": str(entry["status"]),
                    "observedMinArea": "" if entry["observed_min_area"] is None else str(entry["observed_min_area"]),
                    "reviewedPoints": str(entry["reviewed_points"]),
                    "keyedPoints": str(entry["keyed_points"]),
                    "trainLotsWithKeyed": str(entry["train_lots_with_keyed"]),
                },
            )
            for proposal in entry["proposals"]:
                ElementTree.SubElement(
                    condition,
                    "MinDefectArea",
                    {
                        "lossBudget": f"{proposal['loss_budget']:.3f}",
                        "minArea": str(proposal["min_area"]),
                        "unit": "pixel",
                        "raisesLimit": "true" if proposal["raises_limit"] else "false",
                        "trainKeyedLoss": _ratio(proposal["train_keyed_loss"]),
                        "trainReviewReduction": _ratio(proposal["train_review_reduction"]),
                        "heldOutLots": str(proposal["heldout_lots"]),
                        "heldOutKeyedLoss": _ratio(proposal["heldout_keyed_loss"]),
                        "heldOutReviewReduction": _ratio(proposal["heldout_review_reduction"]),
                    },
                )
    ElementTree.indent(root, space="  ")
    return root


def _ratio(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draft AFVI minimum-defect-size proposals as XML (not a machine spec).")
    parser.add_argument("--input", required=True, type=Path, help="export JSONL file or directory")
    parser.add_argument("--split-report", required=True, type=Path, help="evaluation report whose train/test LOT lists are reused")
    parser.add_argument("--output", required=True, type=Path, help="proposal XML destination outside Git")
    parser.add_argument("--budget", type=float, action="append", help="keyed-loss budget; repeatable (default 0.005 and 0.01)")
    args = parser.parse_args(argv)
    budgets = tuple(args.budget) if args.budget else DEFAULT_BUDGETS
    if any(not 0 < budget < 1 for budget in budgets):
        parser.error("--budget must be between 0 and 1")
    repository_root = Path(__file__).resolve().parents[2]
    destination = args.output.expanduser().resolve()
    if destination == repository_root or repository_root in destination.parents:
        parser.error("--output must be outside the Git repository")

    try:
        report = json.loads(args.split_report.read_text(encoding="utf-8"))
        split = report["evaluation"]["split"]
        cells, codes = aggregate(
            _stream(input_files(args.input), max_line_bytes=DEFAULT_MAX_LINE_BYTES, image_index=None, verify_images=False)
        )
        skipped = unverified_families(codes)
        results = propose(
            cells,
            train_lots=set(split["train_lots"]),
            test_lots=set(split["test_lots"]),
            budgets=budgets,
            skipped_families=skipped,
        )
        root = to_xml(
            results,
            basis={
                "exports": args.input.name,
                "splitReport": args.split_report.name,
                "splitCutoff": str(split.get("cutoff")),
                "trainLots": str(len(split["train_lots"])),
                "heldOutLots": str(len(split["test_lots"])),
                "feature": "area_max",
                "lotWeighting": "each LOT with keyed points weighs equally",
                "minTrainLots": str(MIN_TRAIN_LOTS),
                "minKeyedPoints": str(MIN_KEYED_POINTS),
                "skippedFamilies": ",".join(sorted(skipped)),
            },
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        ElementTree.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"afvi-propose: {error}", file=sys.stderr)
        return 2
    counts = Counter(str(entry["status"]) for entry in results)
    print(json.dumps({"conditions": len(results), "status": dict(counts), "output": str(destination)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
