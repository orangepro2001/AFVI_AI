import json
import unittest
from datetime import datetime, timedelta, timezone

from afvi_ai.baseline import STRUCTURED, fit_logistic, model_from_dict, model_to_dict
from afvi_ai.confirm import confirm
from afvi_ai.contracts import normalize_export_record


def make_rows(lot_names, *, points_per_lot=40, invert=False):
    start = datetime(2026, 11, 10, tzinfo=timezone.utc)
    rows = []
    for lot_index, lot in enumerate(lot_names):
        for point in range(points_per_lot):
            positive = point % 2 == 1
            large = positive != invert
            size = 20.0 if large else 1.0
            rows.append(
                normalize_export_record(
                    {
                        "machine": "AFVI13",
                        "result_group": f"MODEL/{lot}/1",
                        "lot": lot,
                        "strip": "0001",
                        "side": "FC",
                        "unit_idx": 0,
                        "img_merge_idx": point + 1,
                        "features": {
                            "area_total": size,
                            "length_max": size,
                            "width_max": size,
                            "area_max": size,
                            "is_dummy": False,
                            "def_side": "F",
                            "light_id": 1,
                            "channel_idx": 1,
                            "sr_metal": 1,
                        },
                        "vrs_observed": True,
                        "ivs_1": "OT15" if positive else "0",
                        "inspected_at": (start + timedelta(days=lot_index)).isoformat(),
                    }
                )
            )
    return tuple(rows)


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        training = make_rows([f"OLD-{i}" for i in range(6)])
        model = fit_logistic(training, l2=0.01, spec=STRUCTURED)
        self.artifact = json.loads(
            json.dumps(model_to_dict(model, training_lots=sorted({row.lot for row in training}), metadata={}))
        )
        self.model = model_from_dict(self.artifact)

    def test_round_trip_preserves_scores(self):
        row = make_rows(["NEW-0"], points_per_lot=2)[1]
        training = make_rows([f"OLD-{i}" for i in range(6)])
        original = fit_logistic(training, l2=0.01, spec=STRUCTURED)
        self.assertAlmostEqual(original.score(row), self.model.score(row), places=12)

    def test_new_lots_meeting_gates_get_a_verdict_and_seen_lots_are_dropped(self):
        rows = make_rows([f"NEW-{i}" for i in range(6)]) + make_rows(["OLD-0"])
        result = confirm(self.model, rows, training_lots=set(self.artifact["training_lots"]))

        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["skipped_seen_lots"], ["OLD-0"])
        self.assertEqual(len(result["new_lots"]), 6)
        self.assertGreater(result["metrics"]["roc_auc"], 0.99)
        self.assertTrue(result["verdict"]["auc_lower_bound_above_0_5"])
        json.dumps(result, allow_nan=False)

    def test_too_few_new_lots_fail_closed(self):
        result = confirm(self.model, make_rows(["NEW-0", "NEW-1"]), training_lots=set(self.artifact["training_lots"]))

        self.assertEqual(result["status"], "insufficient_support")
        self.assertIsNone(result["verdict"])
        self.assertIn("Need at least 5 new labeled LOTs.", result["reasons"])

    def test_inverted_relationship_is_not_qualified(self):
        rows = make_rows([f"NEW-{i}" for i in range(6)], invert=True)
        result = confirm(self.model, rows, training_lots=set(self.artifact["training_lots"]))

        self.assertEqual(result["status"], "evaluated")
        self.assertFalse(result["verdict"]["qualified"])


if __name__ == "__main__":
    unittest.main()
