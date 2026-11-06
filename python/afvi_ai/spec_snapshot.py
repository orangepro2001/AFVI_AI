"""Attach the machines' current size parameters to each drafted condition (read-only).

For every proposed condition in the draft XML(s), the matching model's InspectionSpec.xml is
read from each Vision's PxInventory share: FC rows are compared with the TOP Visions (FM1, FM2)
and BC rows with the BOTTOM Vision (BM) - an assumed side mapping. Only the channel the result
data names (1 R, 2 G, 3 B) of the size keys is collected per node, so the dashboard can show the
draft next to what the machines use today. Nothing is written to the shares.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from .train import write_report

SIZE_KEYS = ("1002", "1011", "1006", "1015")
NOISE_CNODES = {"12"}
"""CNODE 12 is DMG, treated as noise (the user, 2026-11-07); key 1024 only appears there."""
CHANNEL_LETTER = {"1": "R", "2": "G", "3": "B"}
SIDE_VISIONS = {"FC": (("FM1", "TOP"), ("FM2", "TOP")), "BC": (("BM", "BOTTOM"),)}
PROPOSED_STATES = {"candidate", "unverified_no_heldout", "do_not_apply_heldout_exceeds_budget"}


def model_folder(inspect_spec: Path, family: str) -> str | None:
    """The production model folder for a family: '<family>-00', case-insensitive, no copies or backups."""
    wanted = f"{family}-00".lower()
    try:
        names = [entry.name for entry in inspect_spec.iterdir() if entry.is_dir()]
    except OSError:
        return None
    for name in sorted(names):
        if name.lower() == wanted:
            return name
    return None


def size_values(spec_file: Path, channel: str) -> list[dict[str, object]]:
    letter = CHANNEL_LETTER.get(channel)
    if letter is None:
        return []
    root = ElementTree.parse(spec_file).getroot()
    values = []
    for gp in root.iter("GPNODE"):
        for p in gp.iter("PNODE"):
            for c in p.iter("CNODE"):
                if c.get("ID") in NOISE_CNODES:
                    continue
                for element in c:
                    key = element.get("ParamKey")
                    if element.tag != "INSPECTION" or key not in SIZE_KEYS:
                        continue
                    raw = element.get(f"Val{letter}")
                    try:
                        value = float(raw) if raw is not None else None
                    except ValueError:
                        value = None
                    values.append(
                        {
                            "key": key,
                            "node": f"{gp.get('ID')}-{p.get('ID')}-{c.get('ID')}",
                            "value": value,
                            "node_enabled": p.get("NodeCheck") != "0" and c.get("NodeCheck") != "0",
                        }
                    )
    return values


def snapshot_condition(condition: dict[str, str], inventories: dict[str, Path], para_url: str) -> list[dict[str, object]]:
    machines = []
    for machine, side_dir in SIDE_VISIONS.get(condition["side"], ()):
        root = inventories.get(machine)
        entry: dict[str, object] = {"machine": machine, "side_dir": side_dir, "model": None, "values": [], "error": None}
        if root is None:
            entry["error"] = "inventory not configured"
            machines.append(entry)
            continue
        folder = model_folder(root / "INSPECT_SPEC", condition["model"])
        if folder is None:
            entry["error"] = "model folder not found"
            machines.append(entry)
            continue
        entry["model"] = folder
        spec_file = root / "INSPECT_SPEC" / folder / side_dir / f"LIGHT{condition['light']}" / "InspectionSpec.xml"
        entry["link"] = f"{para_url}#{quote(machine)}/{quote(folder)}/{quote(condition['light'])}"
        try:
            entry["values"] = size_values(spec_file, condition["channel"])
            entry["file_modified"] = datetime.fromtimestamp(spec_file.stat().st_mtime).astimezone().isoformat()
        except (OSError, ElementTree.ParseError) as error:
            entry["error"] = f"{error.__class__.__name__}: {error}"
        machines.append(entry)
    return machines


def read_conditions(xml_path: Path) -> list[dict[str, object]]:
    root = ElementTree.parse(xml_path).getroot()
    rows = []
    for model in root.findall("Model"):
        for condition in model.findall("Condition"):
            if condition.get("recommendation") not in PROPOSED_STATES:
                continue
            rows.append(
                {
                    "model": model.get("name"),
                    **{key: condition.get(key, "") for key in (
                        "side", "light", "channel", "srMetal", "codeMain", "recommendation", "recommendedMinArea",
                        "observedMinArea", "trainObservedMin", "heldOutObservedMin", "reviewedPoints", "keyedPoints",
                        "trainLotsWithKeyed",
                    )},
                    "proposals": [dict(area.attrib) for area in condition.findall("MinDefectArea")],
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attach current InspectionSpec size values to drafted conditions.")
    parser.add_argument("--proposals", required=True, type=Path, action="append", help="draft XML from afvi_ai.propose")
    parser.add_argument("--inventory", required=True, action="append", help="NAME=PATH of a Vision's PxInventory (read-only)")
    parser.add_argument("--para-url", default="/afvi/para/", help="PxSpecView base URL for deep links")
    parser.add_argument("--output", required=True, type=Path, help="JSON destination outside Git")
    args = parser.parse_args(argv)
    inventories = {}
    for item in args.inventory:
        name, _, path = item.partition("=")
        if not name or not path:
            parser.error("--inventory must be NAME=PATH")
        inventories[name] = Path(path)

    try:
        drafts = []
        for xml_path in args.proposals:
            conditions = read_conditions(xml_path)
            for condition in conditions:
                condition["spec"] = snapshot_condition(condition, inventories, args.para_url)
            meta = ElementTree.parse(xml_path).getroot()
            drafts.append(
                {
                    "file": xml_path.name,
                    "generated": meta.get("generated"),
                    "status": meta.get("status"),
                    "basis": dict(meta.find("Basis").attrib) if meta.find("Basis") is not None else {},
                    "notes": [note.text or "" for note in meta.iter("Note")],
                    "conditions": conditions,
                }
            )
        write_report(
            args.output,
            {
                "generated_at": datetime.now().astimezone().isoformat(),
                "side_mapping": "FC -> TOP (FM1, FM2), BC -> BOTTOM (BM); assumed, not verified",
                "size_keys": {"1002": "Bright Defect Size(Pixel)", "1011": "Dark Defect Size(Pixel)", "1006": "Bright Defect Offset2 Size(Pixel)", "1015": "Dark Defect Offset2 Size(Pixel)"},
                "noise_nodes": "CNODE 12 (DMG) is excluded as noise",
                "drafts": drafts,
            },
        )
    except (OSError, ElementTree.ParseError, ValueError) as error:
        print(f"afvi-spec-snapshot: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"drafts": len(drafts), "conditions": sum(len(d["conditions"]) for d in drafts)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
