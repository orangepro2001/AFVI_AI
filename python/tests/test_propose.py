import unittest
from collections import Counter
from xml.etree import ElementTree

from afvi_ai.propose import (
    LotHistogram,
    aggregate,
    largest_limit,
    lot_averaged_effect,
    propose,
    to_xml,
    unverified_families,
)
from afvi_ai.contracts import normalize_export_record


def lot(keyed, other):
    return LotHistogram(Counter(keyed), Counter(other))


class ProposeTests(unittest.TestCase):
    def test_lot_averaged_effect_weighs_each_lot_equally(self):
        lots = {
            "A": lot({5: 1, 50: 99}, {5: 100}),
            "B": lot({5: 10, 50: 10}, {5: 0}),
        }
        loss, reduction, count = lot_averaged_effect(lots, 10)

        self.assertEqual(count, 2)
        self.assertAlmostEqual(loss, (0.01 + 0.5) / 2)
        self.assertAlmostEqual(reduction, (101 / 200 + 10 / 20) / 2)

    def test_largest_limit_stops_before_budget_is_exceeded(self):
        lots = {
            "A": lot({10: 2, 20: 98}, {3: 500, 10: 50}),
            "B": lot({20: 100}, {3: 400}),
            "C": lot({20: 100}, {3: 300}),
        }

        self.assertEqual(largest_limit(lots, 0.005), 10)
        self.assertEqual(largest_limit(lots, 0.01), 20)

    def test_propose_requires_support_and_reports_held_out_effect(self):
        train = {f"T{i}": lot({20: 100}, {3: 300}) for i in range(3)}
        cells = {
            ("FAM", "FC", "1", "1", "2", ""): {**train, "H1": lot({4: 5, 20: 95}, {3: 300})},
            ("FAM", "BC", "1", "1", "2", ""): {"T0": lot({20: 100}, {3: 300})},
        }
        results = propose(cells, train_lots={"T0", "T1", "T2"}, test_lots={"H1"}, budgets=(0.01,))

        by_side = {entry["side"]: entry for entry in results}
        self.assertEqual(by_side["BC"]["status"], "keep_current_insufficient_data")
        proposal = by_side["FC"]["proposals"][0]
        self.assertEqual(by_side["FC"]["status"], "proposed")
        self.assertEqual(proposal["min_area"], 20)
        self.assertTrue(proposal["raises_limit"])
        self.assertAlmostEqual(proposal["heldout_keyed_loss"], 0.05)

    def test_recommendation_requires_held_out_budget(self):
        train = {f"T{i}": lot({20: 100}, {3: 300}) for i in range(3)}
        cells = {
            ("FAM", "FC", "1", "1", "1", ""): {**train, "H1": lot({20: 100}, {3: 300})},
            ("FAM", "FC", "2", "1", "1", ""): {**train, "H1": lot({4: 5, 20: 95}, {3: 300})},
            ("FAM", "FC", "3", "1", "1", ""): dict(train),
        }
        results = propose(cells, train_lots={"T0", "T1", "T2"}, test_lots={"H1"}, budgets=(0.01,))
        by_light = {entry["light_id"]: entry for entry in results}

        self.assertEqual(by_light["1"]["recommendation"], "candidate")
        self.assertEqual(by_light["1"]["recommended_min_area"], 20)
        self.assertEqual(by_light["2"]["recommendation"], "do_not_apply_heldout_exceeds_budget")
        self.assertEqual(by_light["2"]["heldout_observed_min"], 3)
        self.assertEqual(by_light["3"]["recommendation"], "unverified_no_heldout")

    def test_six_digit_code_families_are_flagged(self):
        codes = {"B-SERIES": Counter({"850508": 8, "450142": 4}), "7M99": Counter({"OT12": 9, "EN02": 3})}

        self.assertEqual(unverified_families(codes), {"B-SERIES"})

    def test_aggregate_uses_reviewed_points_and_area_max(self):
        rows = [
            normalize_export_record(
                {
                    "machine": "AFVI13",
                    "result_group": "FAM/L1/1",
                    "lot": "L1",
                    "strip": "0001",
                    "side": "FC",
                    "unit_idx": 0,
                    "img_merge_idx": index,
                    "features": {"area_total": 99.0, "area_max": area, "light_id": 1, "channel_idx": 2, "sr_metal": 1},
                    "vrs_observed": observed,
                    "ivs_1": code,
                    "inspected_at": "2026-09-01T00:00:00+09:00",
                }
            )
            for index, (area, observed, code) in enumerate(((7.0, True, "OT12"), (3.0, True, "0"), (5.0, False, None)), start=1)
        ]
        cells, codes, lot_dates = aggregate(rows)

        histogram = cells[("FAM", "FC", "1", "2", "1", "")]["L1"]
        self.assertEqual(histogram.keyed, Counter({7: 1}))
        self.assertEqual(histogram.other, Counter({3: 1}))
        self.assertEqual(codes["FAM"], Counter({"OT12": 1}))
        self.assertEqual(lot_dates["L1"].isoformat(), "2026-09-01T00:00:00+09:00")
        by_code, _, _ = aggregate(
            [row.__class__(**{**row.__dict__, "features": {**row.features, "code_main": "10207"}}) for row in rows],
            by_code=True,
        )
        self.assertIn(("FAM", "FC", "1", "2", "1", "10207"), by_code)

    def test_xml_is_marked_draft_and_lists_conditions(self):
        results = [
            {
                "family": "FAM",
                "side": "FC",
                "light_id": "1",
                "channel_idx": "1",
                "sr_metal": "2",
                "observed_min_area": 3,
                "reviewed_points": 10,
                "keyed_points": 2,
                "train_lots_with_keyed": 3,
                "test_lots": 1,
                "status": "proposed",
                "proposals": [
                    {
                        "loss_budget": 0.01,
                        "min_area": 8,
                        "raises_limit": True,
                        "train_keyed_loss": 0.009,
                        "train_review_reduction": 0.2,
                        "heldout_lots": 1,
                        "heldout_keyed_loss": None,
                        "heldout_review_reduction": None,
                    }
                ],
            }
        ]
        root = ElementTree.fromstring(ElementTree.tostring(to_xml(results, basis={"feature": "area_max"})))

        self.assertEqual(root.get("status"), "DRAFT_NOT_FOR_MACHINE_USE")
        area = root.find("./Model[@name='FAM']/Condition[@side='FC']/MinDefectArea")
        self.assertEqual(area.get("minArea"), "8")
        self.assertEqual(area.get("heldOutKeyedLoss"), "")


if __name__ == "__main__":
    unittest.main()
