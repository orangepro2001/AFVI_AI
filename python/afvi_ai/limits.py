"""Evidence from LOTs that ran with different effective lower limits (natural experiments).

Points below a machine's active size limit are never written, so the smallest area_max a
LOT shows for one inspection condition approximates that LOT's limit. When some LOTs ran
with a lower limit, the points they kept between the low and the high limit show what
raising the limit would remove, or what lowering it would add, including VRS-keyed points.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

from .propose import CellKey, LotHistogram, aggregate, unverified_families
from .train import DEFAULT_MAX_LINE_BYTES, _stream, input_files, write_report

MIN_LOT_POINTS = 200
SAME_REGIME_RATIO = 1.10
MIN_CHANGE_RATIO = 1.20


def lot_limits(lots: dict[str, LotHistogram], *, min_points: int = MIN_LOT_POINTS) -> dict[str, int]:
    """Inferred limit (smallest observed area) for LOTs with enough points in this condition."""
    limits = {}
    for lot, histogram in lots.items():
        keyed_total, other_total = histogram.below(sys.maxsize)
        if keyed_total + other_total < min_points:
            continue
        values = [value for value in (*histogram.keyed, *histogram.other)]
        limits[lot] = min(values)
    return limits


def regimes(limits: dict[str, int]) -> list[dict[str, object]]:
    """Group LOTs whose limits are within SAME_REGIME_RATIO of the group's smallest limit."""
    groups: list[dict[str, object]] = []
    for lot, limit in sorted(limits.items(), key=lambda item: (item[1], item[0])):
        if groups and limit <= int(groups[-1]["limit"]) * SAME_REGIME_RATIO:
            groups[-1]["lots"].append(lot)
            groups[-1]["max_limit"] = max(int(groups[-1]["max_limit"]), limit)
        else:
            groups.append({"limit": limit, "max_limit": limit, "lots": [lot]})
    return groups


def change_evidence(
    lots: dict[str, LotHistogram], low_lots: Iterable[str], high_limit: int
) -> dict[str, float | int | None]:
    """In LOTs that ran with the low limit, LOT-averaged shares of points and keyed points below the high limit."""
    point_shares: list[float] = []
    keyed_shares: list[float] = []
    keyed_below_total = other_below_total = keyed_above_total = other_above_total = 0
    for lot in low_lots:
        histogram = lots[lot]
        keyed_total, other_total = histogram.below(sys.maxsize)
        keyed_below, other_below = histogram.below(high_limit)
        point_shares.append((keyed_below + other_below) / (keyed_total + other_total))
        if keyed_total:
            keyed_shares.append(keyed_below / keyed_total)
        keyed_below_total += keyed_below
        other_below_total += other_below
        keyed_above_total += keyed_total - keyed_below
        other_above_total += other_total - other_below
    below_total = keyed_below_total + other_below_total
    above_total = keyed_above_total + other_above_total
    return {
        "lots": len(point_shares),
        "lots_with_keyed": len(keyed_shares),
        "point_share_below": sum(point_shares) / len(point_shares) if point_shares else None,
        "keyed_share_below": sum(keyed_shares) / len(keyed_shares) if keyed_shares else None,
        "keyed_rate_below": keyed_below_total / below_total if below_total else None,
        "keyed_rate_above": keyed_above_total / above_total if above_total else None,
        "points_below": below_total,
        "keyed_below": keyed_below_total,
    }


def analyze(
    cells: dict[CellKey, dict[str, LotHistogram]],
    lot_dates: dict[str, datetime],
    *,
    skipped_families: set[str] = frozenset(),
    min_points: int = MIN_LOT_POINTS,
) -> list[dict[str, object]]:
    results = []
    for key in sorted(cells):
        family, side, light, channel, region, code = key
        lots = cells[key]
        groups = regimes(lot_limits(lots, min_points=min_points))
        if len(groups) < 2:
            continue
        for group in groups:
            dates = sorted(lot_dates[lot] for lot in group["lots"] if lot in lot_dates)
            group["first_date"] = dates[0].date().isoformat() if dates else None
            group["last_date"] = dates[-1].date().isoformat() if dates else None
        changes = []
        for low_index, low in enumerate(groups):
            for high in groups[low_index + 1 :]:
                if int(high["limit"]) < int(low["max_limit"]) * MIN_CHANGE_RATIO:
                    continue
                changes.append(
                    {
                        "low_limit": low["limit"],
                        "high_limit": high["limit"],
                        **change_evidence(lots, low["lots"], int(high["limit"])),
                    }
                )
        if not changes:
            continue
        results.append(
            {
                "family": family,
                "side": side,
                "light_id": light,
                "channel_idx": channel,
                "sr_metal": region,
                "code_main": code,
                "code_meaning_unverified": family in skipped_families,
                "regimes": groups,
                "changes": changes,
            }
        )
    return results


def to_xml(results: list[dict[str, object]], *, basis: dict[str, str]) -> ElementTree.Element:
    root = ElementTree.Element(
        "AfviLimitChangeEvidence",
        {"version": "0.1", "status": "DRAFT_ANALYSIS_NOT_A_SPEC", "generated": datetime.now().astimezone().isoformat()},
    )
    ElementTree.SubElement(root, "Basis", basis)
    notes = ElementTree.SubElement(root, "Notes")
    for text in (
        "설비 스펙 파일이 아닙니다. LOT 마다 실제로 적용된 하한이 달랐던 구간을 비교한 분석입니다.",
        "Regime.limit = 그 LOT 들에서 관측된 최소 area_max(pixel) = 추정 실효 하한. 포인트가 적은 LOT 는 제외했습니다.",
        "Change: 낮은 하한으로 돌았던 LOT 에서 높은 하한 미만 구간에 있던 포인트·키인 불량 비율(LOT 평균).",
        "  하한을 low→high 로 올리면 pointShareBelow 만큼 리뷰 포인트가 줄고 keyedShareBelow 만큼 키인 불량이 빠집니다.",
        "  하한을 high→low 로 내리면 그 구간의 포인트와 키인 불량이 새로 올라옵니다(keyedRateBelow = 그 구간의 키인율).",
        "한 결함이 다른 조명·채널에서도 잡혔을 수 있으므로, 이 조건에서 빠지는 것이 곧 유출은 아닙니다.",
        "light = LIGHT<n>, channel 1/2/3 = R/G/B, srMetal 1 = SR(NonMetal), 2 = Metal.",
        "codeMeaningUnverified=true 인 계열(6 자리 VRS 코드)은 키인 = 실제 불량인지 확인되지 않았습니다.",
    ):
        ElementTree.SubElement(notes, "Note").text = text
    for entry in results:
        attributes = {
            "model": str(entry["family"]),
            "side": str(entry["side"]),
            "light": str(entry["light_id"]),
            "channel": str(entry["channel_idx"]),
            "srMetal": str(entry["sr_metal"]),
        }
        if entry["code_main"]:
            attributes["codeMain"] = str(entry["code_main"])
        if entry["code_meaning_unverified"]:
            attributes["codeMeaningUnverified"] = "true"
        condition = ElementTree.SubElement(root, "Condition", attributes)
        for group in entry["regimes"]:
            ElementTree.SubElement(
                condition,
                "Regime",
                {
                    "limit": str(group["limit"]),
                    "maxLimit": str(group["max_limit"]),
                    "lots": str(len(group["lots"])),
                    "firstDate": str(group.get("first_date") or ""),
                    "lastDate": str(group.get("last_date") or ""),
                    "lotNames": ",".join(group["lots"]),
                },
            )
        for change in entry["changes"]:
            ElementTree.SubElement(
                condition,
                "Change",
                {
                    "lowLimit": str(change["low_limit"]),
                    "highLimit": str(change["high_limit"]),
                    "evidenceLots": str(change["lots"]),
                    "pointShareBelow": _ratio(change["point_share_below"]),
                    "keyedShareBelow": _ratio(change["keyed_share_below"]),
                    "keyedRateBelow": _ratio(change["keyed_rate_below"]),
                    "keyedRateAbove": _ratio(change["keyed_rate_above"]),
                    "pointsBelow": str(change["points_below"]),
                    "keyedBelow": str(change["keyed_below"]),
                },
            )
    ElementTree.indent(root, space="  ")
    return root


def _ratio(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare LOTs that ran with different effective size limits.")
    parser.add_argument("--input", required=True, type=Path, help="export JSONL file or directory")
    parser.add_argument("--output", required=True, type=Path, help="evidence XML destination outside Git")
    parser.add_argument("--by-code", action="store_true", help="also split conditions by the inspection code_main")
    parser.add_argument("--min-lot-points", type=int, default=MIN_LOT_POINTS)
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[2]
    destination = args.output.expanduser().resolve()
    if destination == repository_root or repository_root in destination.parents:
        parser.error("--output must be outside the Git repository")

    try:
        cells, codes, lot_dates = aggregate(
            _stream(input_files(args.input), max_line_bytes=DEFAULT_MAX_LINE_BYTES, image_index=None, verify_images=False),
            by_code=args.by_code,
        )
        results = analyze(cells, lot_dates, skipped_families=unverified_families(codes), min_points=args.min_lot_points)
        root = to_xml(
            results,
            basis={
                "exports": args.input.name,
                "feature": "area_max",
                "minLotPoints": str(args.min_lot_points),
                "sameRegimeRatio": str(SAME_REGIME_RATIO),
                "minChangeRatio": str(MIN_CHANGE_RATIO),
                "byCode": "true" if args.by_code else "false",
            },
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        ElementTree.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
        write_report(destination.with_suffix(".json"), {"results": results})
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"afvi-limits: {error}", file=sys.stderr)
        return 2
    families = Counter(str(entry["family"]) for entry in results)
    print(json.dumps({"conditions_with_limit_changes": len(results), "families": dict(families), "output": str(destination)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
