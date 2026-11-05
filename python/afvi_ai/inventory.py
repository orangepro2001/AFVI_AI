"""Bounded, read-only inventory of ResultData groups.

The layout is ``<data-root>/<machine>/<model>/<lot>/<group>/<strip>``. Only the
first four directory levels are listed; strip directories are counted with one
``scandir`` per group and are never descended into.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ATOM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STRIP = re.compile(r"^\d{4}$")
EXCLUDED_NAME = re.compile(r"test|dry-run", re.IGNORECASE)
"""Model or LOT names that mark trial, test, or dry runs rather than production LOTs."""


@dataclass(frozen=True)
class GroupEntry:
    machine: str
    model: str
    lot: str
    group: str
    group_rel: str
    group_dir: str
    strip_count: int
    modified_at: str


def _directories(path: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(path) as entries:
            return sorted(
                (entry for entry in entries if entry.is_dir(follow_symlinks=False)),
                key=lambda entry: entry.name,
            )
    except OSError:
        return []


def _strip_count(path: Path) -> int:
    return sum(1 for entry in _directories(path) if STRIP.match(entry.name))


def inventory(data_root: Path, machines: list[str] | None = None) -> tuple[list[GroupEntry], dict[str, int]]:
    """List candidate groups below ``data_root`` and count skipped directories."""
    skipped = {"excluded_lot_name": 0, "invalid_name": 0, "no_strips": 0}
    groups: list[GroupEntry] = []
    for machine_entry in _directories(data_root):
        machine = machine_entry.name
        if machines and machine not in machines:
            continue
        for model_entry in _directories(Path(machine_entry.path)):
            model = model_entry.name
            if EXCLUDED_NAME.search(model):
                skipped["excluded_lot_name"] += 1
                continue
            if not ATOM.match(model):
                skipped["invalid_name"] += 1
                continue
            for lot_entry in _directories(Path(model_entry.path)):
                lot = lot_entry.name
                if EXCLUDED_NAME.search(lot):
                    skipped["excluded_lot_name"] += 1
                    continue
                if not ATOM.match(lot):
                    skipped["invalid_name"] += 1
                    continue
                for group_entry in _directories(Path(lot_entry.path)):
                    group = group_entry.name
                    if not ATOM.match(group):
                        skipped["invalid_name"] += 1
                        continue
                    strips = _strip_count(Path(group_entry.path))
                    if strips == 0:
                        skipped["no_strips"] += 1
                        continue
                    modified = datetime.fromtimestamp(group_entry.stat().st_mtime, tz=timezone.utc)
                    groups.append(
                        GroupEntry(
                            machine=machine,
                            model=model,
                            lot=lot,
                            group=group,
                            group_rel=f"{model}/{lot}/{group}",
                            group_dir=group_entry.path,
                            strip_count=strips,
                            modified_at=modified.isoformat(),
                        )
                    )
    return groups, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List ResultData groups without walking strip trees.")
    parser.add_argument("--data-root", required=True, type=Path, help="ResultData/Data directory (read-only)")
    parser.add_argument("--output", required=True, type=Path, help="groups.jsonl destination outside Git")
    parser.add_argument("--machine", action="append", help="limit to a machine name; repeatable")
    args = parser.parse_args(argv)

    groups, skipped = inventory(args.data_root, args.machine)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for entry in groups:
            stream.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    lots = {(entry.machine, entry.model, entry.lot) for entry in groups}
    summary = {"groups": len(groups), "lots": len(lots), "skipped": skipped}
    print(json.dumps(summary, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
