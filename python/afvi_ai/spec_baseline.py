"""Parameter baseline: how the production models on each Vision set their InspectionSpec values.

Every production model folder below a Vision's INSPECT_SPEC is read (read-only). For each row -
light, node (GPNODE-PNODE-CNODE), element (MASTER / SUBMASTER / INSPECTION), parameter key and,
for INSPECTION, colour channel - the values across models are summarised: how many models have
the row, how many have the node enabled, the most common value and its share, the spread, and the
DEFAULT_SPEC value. An INSPECTION channel value of 0 means the channel does not take part in that
check, so its share is reported separately and the value statistics cover only active channels.
Each model is also compared with the majority, and the AI evidence for its family is attached.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from .train import write_report

CHANNELS = ("R", "G", "B")
EXCLUDED_MODEL = re.compile(r"[\s()]|[가-힣]|test|practice|^bk|bk$", re.IGNORECASE)
CONSENSUS_SHARE = 0.6
MIN_CONSENSUS_MODELS = 5
NOISE_NODE_NAMES = ("DMG",)
"""CNODE names treated as noise (the user, 2026-11-07: DMG is an extremely unimportant parameter)."""


def production_models(inspect_spec: Path) -> list[str]:
    names = []
    for entry in sorted(inspect_spec.iterdir(), key=lambda item: item.name.lower()):
        if not entry.is_dir() or entry.name.upper() == "DEFAULT_SPEC" or EXCLUDED_MODEL.search(entry.name):
            continue
        names.append(entry.name)
    return names


def definitions(root: Path) -> dict[str, object]:
    params: dict[str, dict[str, str]] = defaultdict(dict)
    tree = ElementTree.parse(root / "SpecParameter.xml").getroot()
    for pack in tree.findall("stringpack"):
        language = pack.get("LangCode", "0")
        for item in pack.findall("string"):
            params[item.get("ParamKey", "")][language] = item.get("Text", "")
    nodes: dict[str, dict[str, str]] = {"gp": {}, "p": {}, "c": {}}
    tree = ElementTree.parse(root / "SpecTreeNode.xml").getroot()
    for level, tag in (("gp", "gpnode"), ("p", "pnode"), ("c", "cnode")):
        group = tree.find(tag)
        for node in group if group is not None else []:
            nodes[level][node.get("ID", "")] = node.get("Name", "")
    paths = set()
    tree = ElementTree.parse(root / "SpecTreeNodeList.xml").getroot()
    for gp in tree.iter("gpnode"):
        for p in gp.findall("pnode"):
            for c in p.findall("cnode"):
                paths.add(f"{gp.get('ID')}-{p.get('ID')}-{c.get('ID')}")
    return {"params": dict(params), "nodes": nodes, "tree_paths": paths}


def read_spec(path: Path) -> dict[tuple, tuple[float | None, bool]]:
    """Row key -> (value, node enabled) for one InspectionSpec.xml."""
    rows: dict[tuple, tuple[float | None, bool]] = {}
    root = ElementTree.parse(path).getroot()
    for gp in root.findall("GPNODE"):
        for p in gp.findall("PNODE"):
            for c in p.findall("CNODE"):
                node = f"{gp.get('ID')}-{p.get('ID')}-{c.get('ID')}"
                enabled = p.get("NodeCheck") != "0" and c.get("NodeCheck") != "0"
                for element in c:
                    key = element.get("ParamKey")
                    if key is None:
                        continue
                    if element.tag == "INSPECTION":
                        for channel in CHANNELS:
                            rows[(node, element.tag, key, channel)] = (_number(element.get(f"Val{channel}")), enabled)
                    elif element.tag in ("MASTER", "SUBMASTER"):
                        rows[(node, element.tag, key, "")] = (_number(element.get("Val")), enabled)
    return rows


def _number(raw: str | None) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def summarize(values: list[float]) -> dict[str, object]:
    ordered = sorted(values)
    counts = Counter(ordered)
    mode, mode_count = counts.most_common(1)[0]
    return {
        "mode": mode,
        "mode_share": mode_count / len(ordered),
        "median": statistics.median(ordered),
        "p10": ordered[int(0.1 * (len(ordered) - 1))],
        "p90": ordered[int(round(0.9 * (len(ordered) - 1)))],
        "min": ordered[0],
        "max": ordered[-1],
        "distinct": len(counts),
        "top_values": [[value, count] for value, count in counts.most_common(5)],
    }


def noise_cnodes(defs: dict[str, object], names: tuple[str, ...] = NOISE_NODE_NAMES) -> set[str]:
    wanted = {name.lower() for name in names}
    return {node_id for node_id, name in defs["nodes"]["c"].items() if name.lower() in wanted}


def baseline_for_vision(root: Path, side: str, defs: dict[str, object]) -> dict[str, object]:
    inspect_spec = root / "INSPECT_SPEC"
    noise = noise_cnodes(defs)
    models = production_models(inspect_spec)
    per_light: dict[str, dict[tuple, dict[str, tuple[float | None, bool]]]] = defaultdict(lambda: defaultdict(dict))
    errors = []
    for model in models:
        side_dir = inspect_spec / model / side
        if not side_dir.is_dir():
            continue
        for light_dir in sorted(side_dir.glob("LIGHT*")):
            spec = light_dir / "InspectionSpec.xml"
            if not re.fullmatch(r"LIGHT\d+", light_dir.name) or not spec.is_file():
                continue
            try:
                for row, value in read_spec(spec).items():
                    if row[0].rsplit("-", 1)[-1] in noise:
                        continue
                    per_light[light_dir.name[5:]][row][model] = value
            except (OSError, ElementTree.ParseError) as error:
                errors.append({"model": model, "light": light_dir.name, "error": str(error)})
    defaults: dict[str, dict[tuple, tuple[float | None, bool]]] = {}
    default_side = inspect_spec / "DEFAULT_SPEC" / "Default" / side
    for light_dir in sorted(default_side.glob("LIGHT*")) if default_side.is_dir() else []:
        spec = light_dir / "InspectionSpec.xml"
        if spec.is_file():
            try:
                defaults[light_dir.name[5:]] = read_spec(spec)
            except (OSError, ElementTree.ParseError) as error:
                errors.append({"model": "DEFAULT_SPEC/Default", "light": light_dir.name, "error": str(error)})

    params, nodes, tree_paths = defs["params"], defs["nodes"], defs["tree_paths"]
    lights: dict[str, list[dict[str, object]]] = {}
    deviations: dict[str, list[dict[str, object]]] = defaultdict(list)
    for light, rows in sorted(per_light.items()):
        output = []
        for (node, tag, key, channel), by_model in sorted(rows.items(), key=lambda item: (item[0][0], item[0][1], int(item[0][2]) if item[0][2].isdigit() else 0, item[0][3])):
            enabled = {model: value for model, (value, on) in by_model.items() if on and value is not None}
            active = {model: value for model, value in enabled.items() if not (tag == "INSPECTION" and value == 0)}
            gp, p, c = node.split("-")
            default = defaults.get(light, {}).get((node, tag, key, channel))
            entry: dict[str, object] = {
                "node": node,
                "node_name": " / ".join(filter(None, (nodes["gp"].get(gp), nodes["p"].get(p), nodes["c"].get(c)))) or node,
                "in_tree": node in tree_paths,
                "tag": tag,
                "key": key,
                "name_en": params.get(key, {}).get("0", ""),
                "name_ko": params.get(key, {}).get("1", ""),
                "channel": channel,
                "models": len(by_model),
                "enabled": len(enabled),
                "active": len(active),
                "inactive_share": (len(enabled) - len(active)) / len(enabled) if enabled and tag == "INSPECTION" else None,
                "default": None if default is None else default[0],
                "stats": summarize(list(active.values())) if active else None,
            }
            output.append(entry)
            stats = entry["stats"]
            if stats and len(active) >= MIN_CONSENSUS_MODELS and stats["mode_share"] >= CONSENSUS_SHARE:
                for model, value in active.items():
                    if value != stats["mode"]:
                        deviations[model].append(
                            {
                                "light": light,
                                "node": node,
                                "node_name": entry["node_name"],
                                "key": key,
                                "name_ko": entry["name_ko"],
                                "name_en": entry["name_en"],
                                "channel": channel,
                                "value": value,
                                "mode": stats["mode"],
                                "mode_share": stats["mode_share"],
                                "active": len(active),
                            }
                        )
        lights[light] = output
    model_values: dict[str, dict[str, dict[str, list]]] = defaultdict(lambda: defaultdict(dict))
    for light, rows in per_light.items():
        for (node, tag, key, channel), by_model in rows.items():
            row_id = f"{node}|{tag}|{key}|{channel}"
            for model, (value, on) in by_model.items():
                model_values[model][light][row_id] = [value, on]
    return {"models": models, "lights": lights, "deviations": dict(deviations), "errors": errors, "model_values": model_values}


def ai_evidence(limit_json: Path | None, proposal_xml: Path | None) -> dict[str, list[dict[str, object]]]:
    """Per model family: limit regimes seen in the data and drafted lower limits."""
    by_family: dict[str, list[dict[str, object]]] = defaultdict(list)
    if limit_json is not None and limit_json.is_file():
        for entry in json.loads(limit_json.read_text(encoding="utf-8")).get("results", []):
            by_family[entry["family"]].append(
                {
                    "kind": "limit_change",
                    "side": entry["side"],
                    "light": entry["light_id"],
                    "channel": entry["channel_idx"],
                    "sr_metal": entry["sr_metal"],
                    "code_main": entry.get("code_main", ""),
                    "code_meaning_unverified": entry.get("code_meaning_unverified", False),
                    "regimes": [{"limit": g["limit"], "lots": len(g["lots"]), "first_date": g.get("first_date"), "last_date": g.get("last_date")} for g in entry["regimes"]],
                    "changes": entry["changes"],
                }
            )
    if proposal_xml is not None and proposal_xml.is_file():
        root = ElementTree.parse(proposal_xml).getroot()
        for model in root.findall("Model"):
            for condition in model.findall("Condition"):
                if condition.get("recommendation") not in ("candidate", "do_not_apply_heldout_exceeds_budget", "unverified_no_heldout"):
                    continue
                by_family[model.get("name", "")].append(
                    {
                        "kind": "draft",
                        "side": condition.get("side"),
                        "light": condition.get("light"),
                        "channel": condition.get("channel"),
                        "sr_metal": condition.get("srMetal"),
                        "code_main": condition.get("codeMain", ""),
                        "recommendation": condition.get("recommendation"),
                        "recommended_min_area": condition.get("recommendedMinArea"),
                        "observed_min_area": condition.get("observedMinArea"),
                        "proposals": [dict(area.attrib) for area in condition.findall("MinDefectArea")],
                    }
                )
    return dict(by_family)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise how production models set their InspectionSpec parameters.")
    parser.add_argument("--inventory", required=True, action="append", help="NAME=PATH:SIDE, e.g. FM1=/srv/.../PxInventory:TOP (read-only)")
    parser.add_argument("--output-dir", required=True, type=Path, help="destination directory outside Git")
    parser.add_argument("--limit-evidence", type=Path, help="JSON written by afvi_ai.limits")
    parser.add_argument("--proposals", type=Path, help="draft XML written by afvi_ai.propose")
    parser.add_argument("--para-url", default="/afvi/para/")
    args = parser.parse_args(argv)
    visions = []
    for item in args.inventory:
        name, _, rest = item.partition("=")
        path, _, side = rest.rpartition(":")
        if not name or not path or side not in ("TOP", "BOTTOM"):
            parser.error("--inventory must be NAME=PATH:TOP|BOTTOM")
        visions.append((name, Path(path), side))

    try:
        defs = definitions(visions[0][1])
        index = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "para_url": args.para_url,
            "visions": [],
            "consensus_share": CONSENSUS_SHARE,
            "min_consensus_models": MIN_CONSENSUS_MODELS,
            "noise_nodes": [{"cnode": node_id, "name": defs["nodes"]["c"][node_id]} for node_id in sorted(noise_cnodes(defs))],
        }
        for name, root, side in visions:
            result = baseline_for_vision(root, side, defs)
            for light, rows in result["lights"].items():
                write_report(args.output_dir / f"{name}-L{light}.json", {"vision": name, "side": side, "light": light, "models": len(result["models"]), "rows": rows})
            write_report(args.output_dir / f"{name}-models.json", {"vision": name, "side": side, "models": result["models"], "deviations": result["deviations"], "errors": result["errors"]})
            for model, values in result["model_values"].items():
                write_report(args.output_dir / f"{name}-model" / f"{model}.json", {"vision": name, "side": side, "model": model, "lights": values})
            index["visions"].append({"name": name, "side": side, "models": len(result["models"]), "lights": sorted(result["lights"]), "errors": len(result["errors"])})
        write_report(args.output_dir / "ai-evidence.json", ai_evidence(args.limit_evidence, args.proposals))
        write_report(args.output_dir / "index.json", index)
    except (OSError, ElementTree.ParseError, ValueError) as error:
        print(f"afvi-spec-baseline: {error}", file=sys.stderr)
        return 2
    print(json.dumps(index["visions"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
