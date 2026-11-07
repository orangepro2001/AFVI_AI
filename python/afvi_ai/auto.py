"""Automatic retraining once enough new labeled data has accumulated (the user, 2026-11-07:
"只要新增数据量开始积累到一定程度就开始做训练").

One run (a nightly systemd timer, deploy/afvi-auto-train.*; everything below is read-only on the shares):

1. Inventory every configured result root (``--root HOST=PIXEL_SHARE:MACHINE[,MACHINE]``) with the bounded
   ``inventory`` walk, and export the groups that are new or changed since their last export, once they have been
   quiet for ``--settle-days`` (the review station's labels come after the inspection). Exports live in
   ``<home>/auto/exports/<machine>/<model>/<lot>/<group>.jsonl``; the first run links the existing manual exports
   instead of exporting them again.
2. Count, per LOT, the reviewed points of each class (cached per export file). A LOT is *new* when it is labeled
   (``--min-class-rows`` positives and assumed-goods) and was not seen by an earlier run or the current models.
3. With ``--min-new-lots`` new LOTs (the same support ``afvi_ai.confirm`` needs to give a verdict), train:
   the current structured model is confirmed on exactly the new LOTs; a candidate is trained with the new LOTs as
   its held-out set (``train --test-lots``); the image set is extended (pximaged's JPEG cache first, then the
   share) and an image model is trained on the same split, the current image model scored on the same rows.
4. A candidate replaces the current model only when, on the new LOTs, its ROC-AUC bootstrap lower bound is above
   0.5 and its point estimate is not below the current model's. Otherwise it is kept beside it with its report.
   The new LOTs count as seen either way, so the next run waits for fresh ones.

The state, a log of decisions and every report stay under ``<home>/auto`` (outside Git). A second run while one is
running exits at once (a lock file).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .coverage import lot_coverage
from .inventory import GroupEntry, inventory

MIN_NEW_LOTS = 5
MIN_CLASS_ROWS = 20
SETTLE_DAYS = 3.0
EXPORT_TIMEOUT = 1800  # seconds for one group
STATE_VERSION = 1


@dataclass(frozen=True)
class Root:
    host: str
    share: Path  # the pixel share root (contains ResultData)
    machines: tuple[str, ...]

    @property
    def data(self) -> Path:
        return self.share / "ResultData" / "Data"


def parse_root(value: str) -> Root:
    host, sep, rest = value.partition("=")
    share, sep2, machines = rest.rpartition(":")
    if not sep or not sep2 or not host or not share or not machines:
        raise argparse.ArgumentTypeError("expected HOST=PIXEL_SHARE:MACHINE[,MACHINE]")
    names = tuple(m for m in machines.split(",") if m)
    return Root(host=host, share=Path(share), machines=names)


class Auto:
    def __init__(self, home: Path, roots: list[Root], *, repo: Path, python: Path, vision_python: Path, exporter: Path,
                 cache_root: Path | None, now: datetime | None = None, log=print) -> None:
        self.home = home
        self.dir = home / "auto"
        self.roots = roots
        self.repo = repo
        self.python = python
        self.vision_python = vision_python
        self.exporter = exporter
        self.cache_root = cache_root
        self.now = now or datetime.now(timezone.utc)
        self.log = log
        self.exports = self.dir / "exports"
        self.state_path = self.dir / "state.json"
        self.state: dict[str, Any] = {"version": STATE_VERSION, "groups": {}, "files": {}, "seen_lots": [], "runs": []}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))

    # ---- state -----------------------------------------------------------------------------------------------

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".part")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    def seed_seen(self, models: list[Path], datasets: list[Path]) -> None:
        """The LOTs the current models were trained or evaluated on (once, on the first run)."""
        if self.state.get("seen_lots"):
            return
        seen: set[str] = set()
        for path in models:
            if path.exists():
                seen.update(json.loads(path.read_text(encoding="utf-8")).get("training_lots", []))
        for path in datasets:
            manifest = path / "manifest.jsonl"
            if manifest.exists():
                with manifest.open(encoding="utf-8") as stream:
                    seen.update(json.loads(line)["lot"] for line in stream if line.strip())
        self.state["seen_lots"] = sorted(seen)
        self.log(json.dumps({"seeded_seen_lots": len(seen)}))

    def seed_exports(self, previous: list[Path]) -> int:
        """Link the manual exports (``<dir>/<machine>/<model>/<lot>/<group>.jsonl``) instead of exporting them again."""
        linked = 0
        for base in previous:
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.jsonl")):
                rel = path.relative_to(base).as_posix()
                target = self.exports / rel
                if rel in self.state["groups"] or target.exists() or path.stat().st_size == 0:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(path.resolve())
                # the export's own time stands for the group's state when it was made
                self.state["groups"][rel] = {"exported_at": _iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)),
                                             "seeded": str(path.resolve())}
                linked += 1
        return linked

    # ---- 1. inventory and exports -------------------------------------------------------------------------------

    def groups(self) -> list[tuple[Root, GroupEntry]]:
        out = []
        for root in self.roots:
            entries, skipped = inventory(root.data, list(root.machines))
            self.log(json.dumps({"inventory": str(root.data), "groups": len(entries), "skipped": skipped}))
            out.extend((root, entry) for entry in entries)
        return out

    def due(self, entries: list[tuple[Root, GroupEntry]], settle: timedelta) -> list[tuple[Root, GroupEntry]]:
        """Groups to export now: quiet for ``settle``, and never exported or changed after their export."""
        due = []
        for root, entry in entries:
            modified = datetime.fromisoformat(entry.modified_at)
            if self.now - modified < settle:
                continue
            done = self.state["groups"].get(_rel(entry))
            if done is not None and datetime.fromisoformat(done["exported_at"]) >= modified:
                continue
            due.append((root, entry))
        due.sort(key=lambda item: item[1].modified_at)  # oldest first: they are the most likely to be fully reviewed
        return due

    def export(self, due: list[tuple[Root, GroupEntry]], *, max_groups: int, deadline: float) -> dict[str, int]:
        counts = {"exported": 0, "failed": 0, "left": 0}
        for i, (root, entry) in enumerate(due):
            if counts["exported"] + counts["failed"] >= max_groups or time.monotonic() > deadline:
                counts["left"] = len(due) - i
                break
            rel = _rel(entry)
            out = self.exports / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            part = out.with_name(out.name + ".part")
            started = datetime.now(timezone.utc)
            try:
                with part.open("wb") as stream:
                    subprocess.run([str(self.exporter), "-machine", entry.machine, "-group-rel", entry.group_rel,
                                    "-group-dir", entry.group_dir], stdout=stream, stderr=subprocess.PIPE,
                                   check=True, timeout=EXPORT_TIMEOUT)
                if part.stat().st_size == 0:
                    raise RuntimeError("empty export")
                if out.is_symlink():
                    out.unlink()
                os.replace(part, out)
            except (OSError, subprocess.SubprocessError, RuntimeError) as error:
                part.unlink(missing_ok=True)
                counts["failed"] += 1
                detail = getattr(error, "stderr", b"") or b""
                self.log(json.dumps({"export_failed": rel, "error": str(error), "stderr": detail.decode(errors="replace")[-300:]}))
                continue
            # the group's time as it was read: a group written to while exporting is exported again next run
            self.state["groups"][rel] = {"exported_at": _iso(started), "host": root.host}
            counts["exported"] += 1
            if counts["exported"] % 20 == 0:
                self.save()
        return counts

    # ---- 2. the labeled LOTs ----------------------------------------------------------------------------------

    def lot_counts(self) -> dict[str, dict[str, int]]:
        """Per LOT: positive and assumed_good rows over every export (each file counted once until it changes)."""
        files = self.state.setdefault("files", {})
        present = set()
        totals: dict[str, dict[str, int]] = {}
        for path in sorted(self.exports.rglob("*.jsonl")):
            rel = path.relative_to(self.exports).as_posix()
            present.add(rel)
            st = path.stat()
            stamp = f"{st.st_size}:{int(st.st_mtime)}"
            cached = files.get(rel)
            if cached is None or cached.get("stamp") != stamp:
                lots = {}
                for row in lot_coverage((path,)):
                    lots[row["lot"]] = {"positive": row["positive"], "assumed_good": row["assumed_good"]}
                cached = files[rel] = {"stamp": stamp, "lots": lots}
            for lot, c in cached["lots"].items():
                t = totals.setdefault(lot, {"positive": 0, "assumed_good": 0, "files": 0})
                t["positive"] += c["positive"]
                t["assumed_good"] += c["assumed_good"]
                t["files"] += 1
        for rel in list(files):
            if rel not in present:
                del files[rel]
        return totals

    def new_lots(self, totals: dict[str, dict[str, int]], min_class_rows: int) -> list[str]:
        seen = set(self.state.get("seen_lots", []))
        return sorted(lot for lot, c in totals.items()
                      if lot not in seen and c["positive"] >= min_class_rows and c["assumed_good"] >= min_class_rows)

    def labeled_lots(self, totals: dict[str, dict[str, int]], min_class_rows: int) -> list[str]:
        return sorted(lot for lot, c in totals.items() if c["positive"] >= min_class_rows and c["assumed_good"] >= min_class_rows)

    # ---- 3. training ------------------------------------------------------------------------------------------

    def holdout_dir(self, run: Path, lots: set[str]) -> Path:
        """Links to the export files of exactly the new LOTs (the path's LOT folder is the export's LOT)."""
        out = run / "exports-new"
        for path in self.exports.rglob("*.jsonl"):
            rel = path.relative_to(self.exports)
            if len(rel.parts) >= 4 and rel.parts[2] in lots:
                target = out / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(path.resolve())
        return out

    def module(self, python: Path, name: str, *args: str) -> None:
        env = {**os.environ, "PYTHONPATH": str(self.repo / "python")}
        cmd = [str(python), "-m", name, *args]
        self.log(json.dumps({"run": name, "args": list(args)}))
        subprocess.run(cmd, check=True, cwd=self.repo, env=env)

    def train(self, new: list[str], *, current_model: Path, current_vision: Path, dataset_seed: Path | None,
              feature_set: str, l2: float, per_lot_class: int, epochs: int) -> dict[str, Any]:
        stamp = self.now.astimezone().strftime("%Y%m%d-%H%M")
        run = self.dir / "runs" / stamp
        run.mkdir(parents=True, exist_ok=True)
        (run / "new-lots.txt").write_text("\n".join(new) + "\n", encoding="utf-8")
        result: dict[str, Any] = {"run": stamp, "new_lots": new}
        holdout = self.holdout_dir(run, set(new))

        # the current structured model on exactly the new LOTs
        if current_model.exists():
            self.module(self.python, "afvi_ai.confirm", "--model", str(current_model), "--input", str(holdout),
                        "--output", str(run / "confirm-current.json"))
            result["current_structured"] = _auc(json.loads((run / "confirm-current.json").read_text(encoding="utf-8")))

        # a candidate with the new LOTs held out, refit on everything for use
        candidate = self.home / "models" / f"afvi-risk-auto-{stamp}.json"
        self.module(self.python, "afvi_ai.train", "--input", str(self.exports), "--output", str(run / "structured.json"),
                    "--cutoff", "2100-01-01T00:00:00+00:00", "--test-lots", str(run / "new-lots.txt"),
                    "--max-rows-per-lot-state", "1500", "--max-rows", "2000000", "--seed", "0",
                    "--feature-set", feature_set, "--l2", str(l2), "--final-model", str(candidate),
                    "--holdout-note", f"automatic run {stamp}: held-out = the {len(new)} LOTs new since the last run, evaluated once")
        report = json.loads((run / "structured.json").read_text(encoding="utf-8"))
        result["candidate_structured"] = _auc(report["evaluation"])
        result["structured_promoted"] = promote(result["candidate_structured"], result.get("current_structured"))
        if result["structured_promoted"]:
            _relink(self.home / "models" / "risk-current", candidate)

        # the image set (kept across runs: an image already made is reused) and the image model on the same split
        dataset = self.dir / "vision-dataset"
        if not dataset.exists() and dataset_seed is not None and (dataset_seed / "images").is_dir():
            dataset.mkdir(parents=True)
            subprocess.run(["cp", "-al", str(dataset_seed / "images"), str(dataset / "images")], check=True)
        args = ["--input", str(self.exports), "--split-report", str(run / "structured.json"), "--output-dir", str(dataset),
                "--per-lot-class", str(per_lot_class), "--workers", "4", "--max-mbps", "50"]
        for root in self.roots:
            for machine in root.machines:
                args += ["--machine-root", f"{machine}={root.share}"]
                if self.cache_root is not None:
                    cache = self.cache_root / f"{root.host}-{machine}"
                    if cache.is_dir():
                        args += ["--cache", f"{machine}={cache}"]
        self.module(self.vision_python, "afvi_ai.vision.dataset", *args)
        vision_dir = self.home / "models" / f"vision-auto-{stamp}"
        vargs = ["--dataset", str(dataset), "--output-dir", str(vision_dir), "--report", str(run / "vision.json"),
                 "--epochs", str(epochs)]
        if (current_vision / "model.pt").exists():
            vargs += ["--compare-model", str(current_vision.resolve())]
        self.module(self.vision_python, "afvi_ai.vision.train_image", *vargs)
        vision = json.loads((run / "vision.json").read_text(encoding="utf-8"))
        result["candidate_vision"] = _auc(vision["held_out"])
        result["current_vision"] = _auc(vision.get("compared_model_on_held_out"))
        result["vision_promoted"] = promote(result["candidate_vision"], result["current_vision"])
        if result["vision_promoted"]:
            _relink(current_vision, vision_dir)
            reports = self.home / "reports" / "vision"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / f"vision-auto-{stamp}.json").write_text((run / "vision.json").read_text(encoding="utf-8"), encoding="utf-8")
            _relink(reports / "latest.json", reports / f"vision-auto-{stamp}.json")
            subprocess.run(["systemctl", "--user", "restart", "afvi-vision-worker"], check=False)
        return result


def promote(candidate: dict[str, Any] | None, current: dict[str, Any] | None) -> bool:
    """A candidate replaces the current model: lower bound above 0.5 on the new LOTs, and not worse than the current
    model there (a current model without a measurement on them does not block)."""
    if not candidate or candidate.get("roc_auc") is None or candidate.get("low") is None:
        return False
    if candidate["low"] <= 0.5:
        return False
    if current and current.get("roc_auc") is not None and candidate["roc_auc"] < current["roc_auc"]:
        return False
    return True


def _auc(block: dict[str, Any] | None) -> dict[str, Any] | None:
    if not block or not block.get("metrics"):
        return None
    intervals = ((block.get("bootstrap") or {}).get("intervals") or {}).get("roc_auc") or {}
    return {"roc_auc": block["metrics"].get("roc_auc"), "low": intervals.get("low"), "high": intervals.get("high")}


def _relink(link: Path, target: Path) -> None:
    tmp = link.with_name(link.name + ".new")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target.name if target.parent == link.parent else target)
    os.replace(tmp, link)


def _rel(entry: GroupEntry) -> str:
    return f"{entry.machine}/{entry.group_rel}.jsonl"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export new result groups and retrain once enough new labeled LOTs exist.")
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("AFVI_DATA_HOME", "/home/tony/afvi-ai-data")))
    parser.add_argument("--root", action="append", type=parse_root, required=True,
                        help="HOST=PIXEL_SHARE:MACHINE[,MACHINE], e.g. 192.168.1.204=/srv/pxshares/192.168.1.204/pixel:AFVI1,AFVI2")
    parser.add_argument("--cache-root", type=Path, default=Path("/var/lib/pximages"), help="pximaged's cache (<host>-<machine>)")
    parser.add_argument("--seed-exports", action="append", type=Path, default=[], help="manual export directories to link on the first run")
    parser.add_argument("--seed-dataset", type=Path, help="an earlier image set whose images are reused (hard links)")
    parser.add_argument("--current-model", type=Path, help="structured model in use (default <home>/models/risk-current)")
    parser.add_argument("--current-vision", type=Path, help="image model in use (default <home>/models/vision-current)")
    parser.add_argument("--feature-set", default="structured_code")
    parser.add_argument("--l2", type=float, default=0.0001)
    parser.add_argument("--min-new-lots", type=int, default=MIN_NEW_LOTS)
    parser.add_argument("--min-class-rows", type=int, default=MIN_CLASS_ROWS)
    parser.add_argument("--settle-days", type=float, default=SETTLE_DAYS)
    parser.add_argument("--max-export-groups", type=int, default=400, help="groups exported per run (the rest next run)")
    parser.add_argument("--export-hours", type=float, default=4.0, help="time for exports per run")
    parser.add_argument("--per-lot-class", type=int, default=900)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--vision-python", type=Path, help="Python with torch (default <home>/venv-vision/bin/python)")
    parser.add_argument("--exporter", type=Path, help="afvi-export binary (default <home>/bin/afvi-export)")
    parser.add_argument("--check", action="store_true", help="only inventory and count; export and train nothing")
    parser.add_argument("--force", action="store_true", help="train now if there is at least one new labeled LOT")
    args = parser.parse_args(argv)

    home = args.home.resolve()
    repo = Path(__file__).resolve().parents[2]
    lock_path = home / "auto" / "lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"skipped": "another run holds the lock"}))
        return 0

    log_file = (home / "auto" / "log.jsonl").open("a", encoding="utf-8")

    def log(line: str) -> None:
        print(line, flush=True)
        log_file.write(json.dumps({"at": _iso(datetime.now(timezone.utc)), **json.loads(line)}, ensure_ascii=False) + "\n")
        log_file.flush()

    current_model = args.current_model or home / "models" / "risk-current"
    current_vision = args.current_vision or home / "models" / "vision-current"
    auto = Auto(home, args.root, repo=repo, python=Path(sys.executable),
                vision_python=args.vision_python or home / "venv-vision" / "bin" / "python",
                exporter=args.exporter or home / "bin" / "afvi-export",
                cache_root=args.cache_root if args.cache_root.is_dir() else None, log=log)
    auto.seed_seen([current_model], [args.seed_dataset] if args.seed_dataset else [])
    linked = auto.seed_exports(args.seed_exports)
    if linked:
        log(json.dumps({"seeded_exports": linked}))
    auto.save()

    entries = auto.groups()
    due = auto.due(entries, timedelta(days=args.settle_days))
    log(json.dumps({"groups": len(entries), "due_for_export": len(due)}))
    if not args.check:
        counts = auto.export(due, max_groups=args.max_export_groups, deadline=time.monotonic() + args.export_hours * 3600)
        log(json.dumps({"exports": counts}))
        auto.save()

    totals = auto.lot_counts()
    auto.save()
    new = auto.new_lots(totals, args.min_class_rows)
    labeled = auto.labeled_lots(totals, args.min_class_rows)
    log(json.dumps({"labeled_lots": len(labeled), "seen_lots": len(auto.state["seen_lots"]), "new_labeled_lots": len(new),
                    "needed": args.min_new_lots, "new": new[:50]}))
    if args.check or not new or (len(new) < args.min_new_lots and not args.force):
        auto.state["runs"].append({"at": _iso(auto.now), "trained": False, "new_labeled_lots": len(new)})
        auto.state["runs"] = auto.state["runs"][-200:]
        auto.save()
        return 0

    try:
        result = auto.train(new, current_model=current_model, current_vision=current_vision,
                            dataset_seed=args.seed_dataset, feature_set=args.feature_set, l2=args.l2,
                            per_lot_class=args.per_lot_class, epochs=args.epochs)
    except subprocess.CalledProcessError as error:
        log(json.dumps({"train_failed": str(error)}))
        auto.state["runs"].append({"at": _iso(auto.now), "trained": False, "error": str(error), "new_labeled_lots": len(new)})
        auto.save()
        return 1
    # the new LOTs have been a held-out set once: they train from now on, the next run waits for fresh ones
    auto.state["seen_lots"] = sorted(set(auto.state["seen_lots"]) | set(new))
    auto.state["runs"].append({"at": _iso(auto.now), "trained": True, **result})
    auto.state["runs"] = auto.state["runs"][-200:]
    auto.save()
    log(json.dumps({"trained": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
