"""Where reviewed points pile up without VRS keying: unit-relative and per-unit hotspots.

Strip coordinates are binned (BIN_PX), each unit's extent is estimated from its own bins, and
points are mapped onto a GRID x GRID unit-relative grid per model family and side. Cells with
many points but a low keyed rate are false-call hotspots (candidates for mask / edge-removal
review); cells with a high keyed rate are defect hotspots. Dummy points and unit indices are
summarised the same way. Counts are exact over the reviewed points of the exports.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from .propose import unverified_families
from .train import DEFAULT_MAX_LINE_BYTES, _stream, input_files, write_report

BIN_PX = 100
GRID = 10
MIN_CELL_SHARE = 0.02
LOW_RATIO = 0.5
HIGH_RATIO = 2.0


def collect(samples: Iterable[Any]) -> tuple[dict[tuple, list[int]], dict[str, Counter], dict[tuple, set[str]]]:
    """Counts [keyed, other] per (family, side, unit_idx, x_bin, y_bin), keyed codes, and LOTs per family-side."""
    bins: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    codes: dict[str, Counter] = defaultdict(Counter)
    lots: dict[tuple, set[str]] = defaultdict(set)
    for sample in samples:
        if sample.excluded or not sample.vrs_observed or sample.label not in (0, 1):
            continue
        x = sample.features.get("strip_x")
        y = sample.features.get("strip_y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or isinstance(x, bool) or isinstance(y, bool):
            continue
        family = sample.key.result_group.split("/", 1)[0]
        key = (family, sample.key.side, sample.key.unit_idx, int(x // BIN_PX), int(y // BIN_PX))
        bins[key][0 if sample.label == 1 else 1] += 1
        lots[(family, sample.key.side)].add(sample.lot)
        if sample.label == 1:
            codes[family][sample.ivs_1 or ""] += 1
    return bins, codes, lots


def summarize(
    bins: dict[tuple, list[int]], lots: dict[tuple, set[str]], *, skipped_families: set[str] = frozenset()
) -> list[dict[str, object]]:
    extents: dict[tuple, list[int]] = {}
    for family, side, unit, bx, by in bins:
        if unit < 0:
            continue
        extent = extents.setdefault((family, side, unit), [bx, bx, by, by])
        extent[0] = min(extent[0], bx)
        extent[1] = max(extent[1], bx)
        extent[2] = min(extent[2], by)
        extent[3] = max(extent[3], by)

    grids: dict[tuple, dict[tuple[int, int], list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    units: dict[tuple, dict[int, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    totals: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    dummy: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    for (family, side, unit, bx, by), (keyed, other) in bins.items():
        group = (family, side)
        totals[group][0] += keyed
        totals[group][1] += other
        if unit < 0:
            dummy[group][0] += keyed
            dummy[group][1] += other
            continue
        units[group][unit][0] += keyed
        units[group][unit][1] += other
        min_x, max_x, min_y, max_y = extents[(family, side, unit)]
        gx = min(GRID - 1, (bx - min_x) * GRID // (max_x - min_x + 1))
        gy = min(GRID - 1, (by - min_y) * GRID // (max_y - min_y + 1))
        grids[group][(gx, gy)][0] += keyed
        grids[group][(gx, gy)][1] += other

    results = []
    for group in sorted(totals):
        keyed_total, other_total = totals[group]
        points = keyed_total + other_total
        if points == 0 or keyed_total == 0:
            continue
        base_rate = keyed_total / points
        cells = []
        for (gx, gy), (keyed, other) in sorted(grids[group].items()):
            count = keyed + other
            cells.append(_cell({"gx": gx, "gy": gy}, keyed, count, points, base_rate))
        unit_rows = [
            _cell({"unit": unit}, keyed, keyed + other, points, base_rate)
            for unit, (keyed, other) in sorted(units[group].items())
        ]
        dummy_keyed, dummy_other = dummy[group]
        results.append(
            {
                "family": group[0],
                "side": group[1],
                "lots": len(lots.get(group, ())),
                "points": points,
                "keyed": keyed_total,
                "keyed_rate": base_rate,
                "code_meaning_unverified": group[0] in skipped_families,
                "dummy": _cell({}, dummy_keyed, dummy_keyed + dummy_other, points, base_rate),
                "grid": cells,
                "units": unit_rows,
                "false_call_hotspots": _hotspots(cells, low=True) + _hotspots(unit_rows, low=True),
                "defect_hotspots": _hotspots(cells, low=False) + _hotspots(unit_rows, low=False),
            }
        )
    return results


def _cell(labels: dict[str, int], keyed: int, count: int, points: int, base_rate: float) -> dict[str, object]:
    rate = keyed / count if count else None
    return {
        **labels,
        "points": count,
        "keyed": keyed,
        "point_share": count / points if points else 0.0,
        "keyed_rate": rate,
        "rate_ratio": rate / base_rate if rate is not None and base_rate else None,
    }


def _hotspots(rows: list[dict[str, object]], *, low: bool) -> list[dict[str, object]]:
    selected = []
    for row in rows:
        ratio = row["rate_ratio"]
        if ratio is None or row["point_share"] < MIN_CELL_SHARE:
            continue
        if (low and ratio <= LOW_RATIO) or (not low and ratio >= HIGH_RATIO):
            selected.append(row)
    return sorted(selected, key=lambda row: row["point_share"], reverse=True)[:8]


def to_xml(results: list[dict[str, object]], *, basis: dict[str, str]) -> ElementTree.Element:
    root = ElementTree.Element(
        "AfviHotspots", {"version": "0.1", "status": "DRAFT_ANALYSIS_NOT_A_SPEC", "generated": datetime.now().astimezone().isoformat()}
    )
    ElementTree.SubElement(root, "Basis", basis)
    notes = ElementTree.SubElement(root, "Notes")
    for text in (
        "설비 스펙 파일이 아닙니다. 리뷰된 포인트가 unit 안 어디에 몰리는지와 그 위치의 키인율을 정리한 분석입니다.",
        f"gx, gy = unit 안 상대 위치 {GRID}x{GRID} 격자(0 = strip 좌표가 작은 쪽). unit 범위는 데이터에서 추정했습니다.",
        "rateRatio = 그 칸의 키인율 / 계열·면 전체 키인율. FalseCallHotspot = 포인트 비중 2% 이상이면서 rateRatio 0.5 이하.",
        "FalseCallHotspot 은 마스크·엣지 제거 계열 파라미터(Adjust Mask, Remove Metal/SR/Hole/VIA Edge 등) 검토 후보입니다.",
        "DefectHotspot = 포인트 비중 2% 이상이면서 rateRatio 2 이상(실제 불량이 몰리는 위치).",
        "codeMeaningUnverified=true 인 계열은 키인 = 실제 불량인지 확인되지 않았습니다.",
    ):
        ElementTree.SubElement(notes, "Note").text = text
    for entry in results:
        attributes = {
            "model": str(entry["family"]),
            "side": str(entry["side"]),
            "lots": str(entry["lots"]),
            "points": str(entry["points"]),
            "keyed": str(entry["keyed"]),
            "keyedRate": f"{entry['keyed_rate']:.4f}",
        }
        if entry["code_meaning_unverified"]:
            attributes["codeMeaningUnverified"] = "true"
        element = ElementTree.SubElement(root, "Surface", attributes)
        ElementTree.SubElement(element, "Dummy", _attributes(entry["dummy"]))
        for tag, key in (("FalseCallHotspot", "false_call_hotspots"), ("DefectHotspot", "defect_hotspots")):
            for row in entry[key]:
                ElementTree.SubElement(element, tag, _attributes(row))
        grid = ElementTree.SubElement(element, "UnitGrid", {"size": str(GRID)})
        for row in entry["grid"]:
            ElementTree.SubElement(grid, "Cell", _attributes(row))
        unit_element = ElementTree.SubElement(element, "Units")
        for row in entry["units"]:
            ElementTree.SubElement(unit_element, "Unit", _attributes(row))
    ElementTree.indent(root, space="  ")
    return root


def _attributes(row: dict[str, object]) -> dict[str, str]:
    attributes = {}
    for key in ("gx", "gy", "unit"):
        if key in row:
            attributes[key] = str(row[key])
    attributes["points"] = str(row["points"])
    attributes["keyed"] = str(row["keyed"])
    attributes["pointShare"] = f"{row['point_share']:.4f}"
    attributes["keyedRate"] = "" if row["keyed_rate"] is None else f"{row['keyed_rate']:.4f}"
    attributes["rateRatio"] = "" if row["rate_ratio"] is None else f"{row['rate_ratio']:.2f}"
    return attributes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find unit-relative false-call and defect hotspots in AFVI exports.")
    parser.add_argument("--input", required=True, type=Path, help="export JSONL file or directory")
    parser.add_argument("--output", required=True, type=Path, help="hotspot XML destination outside Git")
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[2]
    destination = args.output.expanduser().resolve()
    if destination == repository_root or repository_root in destination.parents:
        parser.error("--output must be outside the Git repository")
    try:
        bins, codes, lots = collect(
            _stream(input_files(args.input), max_line_bytes=DEFAULT_MAX_LINE_BYTES, image_index=None, verify_images=False)
        )
        results = summarize(bins, lots, skipped_families=unverified_families(codes))
        root = to_xml(results, basis={"exports": args.input.name, "binPx": str(BIN_PX), "grid": str(GRID)})
        destination.parent.mkdir(parents=True, exist_ok=True)
        ElementTree.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
        write_report(destination.with_suffix(".json"), {"results": results})
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"afvi-hotspots: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"surfaces": len(results), "output": str(destination)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
