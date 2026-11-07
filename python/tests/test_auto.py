import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from afvi_ai.auto import Auto, parse_root, promote
from afvi_ai.split import split_by_lot_time
from afvi_ai.train import input_files, load_sampled

NOW = datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc)


def record(lot, index, ivs):
    return {
        "machine": "AFVI1",
        "result_group": f"M1/{lot}/1",
        "lot": lot,
        "strip": "0001",
        "side": "FC",
        "unit_idx": 0,
        "img_merge_idx": index,
        "features": {"area_total": 5.0, "length_max": 2.0, "width_max": 1.0},
        "vrs_observed": True,
        "ivs_1": ivs,
        "inspected_at": "2026-11-01T00:00:00+09:00",
    }


def write_export(path, lot, positives, goods):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [record(lot, 1 + i, "OT15") for i in range(positives)] + [record(lot, 1 + positives + i, "0") for i in range(goods)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def share(base, lots_age_days):
    """<base>/pixel/ResultData/Data/AFVI1/M1/<lot>/1/0001 with the group folder's time set."""
    for lot, days in lots_age_days.items():
        group = base / "pixel" / "ResultData" / "Data" / "AFVI1" / "M1" / lot / "1"
        (group / "0001").mkdir(parents=True)
        at = (NOW - timedelta(days=days)).timestamp()
        os.utime(group, (at, at))


class AutoTests(unittest.TestCase):
    def auto(self, base):
        root = parse_root(f"192.168.1.204={base / 'pixel'}:AFVI1")
        return Auto(base / "home", [root], repo=base, python=Path("python3"), vision_python=Path("python3"),
                    exporter=Path("/nonexistent"), cache_root=None, now=NOW, log=lambda line: None)

    def test_parse_root(self):
        r = parse_root("192.168.1.204=/srv/pxshares/192.168.1.204/pixel:AFVI1,AFVI2")
        self.assertEqual((r.host, r.share, r.machines), ("192.168.1.204", Path("/srv/pxshares/192.168.1.204/pixel"), ("AFVI1", "AFVI2")))
        self.assertEqual(r.data, Path("/srv/pxshares/192.168.1.204/pixel/ResultData/Data"))

    def test_due_waits_for_quiet_groups_and_reexports_changed_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            share(base, {"OLD": 10, "FRESH": 1, "DONE": 10, "GREW": 5})
            auto = self.auto(base)
            auto.state["groups"]["AFVI1/M1/DONE/1.jsonl"] = {"exported_at": (NOW - timedelta(days=8)).isoformat()}
            auto.state["groups"]["AFVI1/M1/GREW/1.jsonl"] = {"exported_at": (NOW - timedelta(days=8)).isoformat()}
            due = auto.due(auto.groups(), timedelta(days=3))
            self.assertEqual([entry.lot for _, entry in due], ["OLD", "GREW"])

    def test_new_lots_need_both_classes_and_are_not_seen(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            auto = self.auto(base)
            write_export(auto.exports / "AFVI1/M1/A/1.jsonl", "A", 25, 30)
            write_export(auto.exports / "AFVI1/M1/A/2.jsonl", "A", 0, 5)  # a LOT over two groups adds up
            write_export(auto.exports / "AFVI1/M1/B/1.jsonl", "B", 5, 40)  # too few positives
            write_export(auto.exports / "AFVI1/M1/C/1.jsonl", "C", 30, 30)
            auto.state["seen_lots"] = ["C"]
            totals = auto.lot_counts()
            self.assertEqual(totals["A"]["assumed_good"], 35)
            self.assertEqual(auto.new_lots(totals, 20), ["A"])
            self.assertEqual(auto.labeled_lots(totals, 20), ["A", "C"])
            # counted once: a file that did not change keeps its cached counts, a removed one goes
            stamp = auto.state["files"]["AFVI1/M1/A/1.jsonl"]["stamp"]
            (auto.exports / "AFVI1/M1/B/1.jsonl").unlink()
            auto.lot_counts()
            self.assertEqual(auto.state["files"]["AFVI1/M1/A/1.jsonl"]["stamp"], stamp)
            self.assertNotIn("AFVI1/M1/B/1.jsonl", auto.state["files"])

    def test_seed_links_manual_exports_once(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manual = base / "manual"
            write_export(manual / "AFVI1/M1/A/1.jsonl", "A", 1, 1)
            auto = self.auto(base)
            self.assertEqual(auto.seed_exports([manual]), 1)
            self.assertTrue((auto.exports / "AFVI1/M1/A/1.jsonl").is_symlink())
            self.assertEqual(auto.seed_exports([manual]), 0)

    def test_holdout_dir_has_exactly_the_new_lots(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            auto = self.auto(base)
            for lot in ("A", "B", "C"):
                write_export(auto.exports / f"AFVI1/M1/{lot}/1.jsonl", lot, 1, 1)
            out = auto.holdout_dir(base / "run", {"A", "C"})
            self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob("*.jsonl")), ["AFVI1/M1/A/1.jsonl", "AFVI1/M1/C/1.jsonl"])

    def test_test_lots_split_holds_out_exactly_those(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for lot in ("A", "B", "C"):
                write_export(base / f"{lot}.jsonl", lot, 2, 2)
            samples, _ = load_sampled(input_files(base), max_rows_per_lot_state=10, seed=0)
            split = split_by_lot_time(samples, cutoff=datetime(2100, 1, 1, tzinfo=timezone.utc), test_lots=["B"])
            self.assertEqual((set(split.train_lots), set(split.test_lots)), ({"A", "C"}, {"B"}))

    def test_promote(self):
        self.assertTrue(promote({"roc_auc": 0.8, "low": 0.7}, {"roc_auc": 0.78, "low": 0.6}))
        self.assertTrue(promote({"roc_auc": 0.8, "low": 0.7}, None))  # nothing measured to beat
        self.assertFalse(promote({"roc_auc": 0.8, "low": 0.7}, {"roc_auc": 0.81, "low": 0.7}))  # worse than in use
        self.assertFalse(promote({"roc_auc": 0.6, "low": 0.49}, None))  # not above chance
        self.assertFalse(promote(None, None))


if __name__ == "__main__":
    unittest.main()
