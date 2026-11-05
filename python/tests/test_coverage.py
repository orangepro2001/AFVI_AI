import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from afvi_ai.coverage import cutoff_support, lot_coverage
from afvi_ai.train import input_files


def record(lot, index, ivs, inspected_at):
    return {
        "machine": "AFVI13",
        "result_group": f"MODEL/{lot}/1",
        "lot": lot,
        "strip": "0001",
        "side": "FC",
        "unit_idx": 0,
        "img_merge_idx": index,
        "features": {"area_total": 5.0, "length_max": 2.0, "width_max": 1.0},
        "vrs_observed": ivs is not None,
        "ivs_1": ivs,
        "inspected_at": inspected_at,
    }


class CoverageTests(unittest.TestCase):
    def test_counts_label_states_and_assigns_lots_to_cutoff_sides(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.jsonl"
            rows = [
                record("OLD", 1, "OT15", "2026-08-01T00:00:00+09:00"),
                record("OLD", 2, "0", "2026-08-02T00:00:00+09:00"),
                record("OLD", 3, None, "2026-08-03T00:00:00+09:00"),
                record("NEW", 1, "0", "2026-09-30T00:00:00+09:00"),
                record("NEW", 2, "0", "2026-10-02T00:00:00+09:00"),
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))

            coverage = lot_coverage(input_files(path))
            support = cutoff_support(coverage, datetime(2026, 10, 1, tzinfo=timezone.utc))

            self.assertEqual([row["lot"] for row in coverage], ["OLD", "NEW"])
            self.assertEqual(
                (coverage[0]["positive"], coverage[0]["assumed_good"], coverage[0]["unlabeled"]), (1, 1, 1)
            )
            self.assertEqual(support["sides"]["before"]["lots"], 1)
            self.assertEqual(support["sides"]["before"]["positive_lots"], 1)
            self.assertEqual(support["sides"]["after"]["lots"], 1)
            self.assertEqual(support["sides"]["after"]["assumed_good"], 2)
            self.assertEqual(support["sides"]["after"]["positive_lots"], 0)


if __name__ == "__main__":
    unittest.main()
