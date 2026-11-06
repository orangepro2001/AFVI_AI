import unittest
from collections import Counter
from datetime import datetime, timezone
from xml.etree import ElementTree

from afvi_ai.limits import analyze, change_evidence, lot_limits, regimes, to_xml
from afvi_ai.propose import LotHistogram


def lot(keyed, other):
    return LotHistogram(Counter(keyed), Counter(other))


class LimitEvidenceTests(unittest.TestCase):
    def test_lot_limits_skip_sparse_lots(self):
        lots = {"A": lot({30: 10}, {30: 300}), "B": lot({200: 1}, {200: 5})}

        self.assertEqual(lot_limits(lots, min_points=200), {"A": 30})

    def test_lot_limits_ignore_minimum_of_untruncated_distribution(self):
        broad = lot({}, {7216: 1, **{value: 3 for value in range(20_000, 90_000, 1000)}})
        truncated = lot({30: 5}, {30: 50, 31: 40, 80: 300})

        self.assertEqual(lot_limits({"BROAD": broad, "CUT": truncated}, min_points=200), {"CUT": 30})

    def test_regimes_group_close_limits(self):
        groups = regimes({"A": 20, "B": 21, "C": 120, "D": 125, "E": 30})

        self.assertEqual([group["limit"] for group in groups], [20, 30, 120])
        self.assertEqual(groups[0]["lots"], ["A", "B"])
        self.assertEqual(groups[2]["max_limit"], 125)

    def test_change_evidence_measures_points_and_keyed_below_high_limit(self):
        lots = {
            "LOW1": lot({25: 20, 150: 80}, {25: 700, 150: 200}),
            "LOW2": lot({25: 60, 150: 40}, {25: 300, 150: 600}),
        }
        evidence = change_evidence(lots, ["LOW1", "LOW2"], 120)

        self.assertEqual(evidence["lots"], 2)
        self.assertAlmostEqual(evidence["point_share_below"], (720 / 1000 + 360 / 1000) / 2)
        self.assertAlmostEqual(evidence["keyed_share_below"], (0.2 + 0.6) / 2)
        self.assertAlmostEqual(evidence["keyed_rate_below"], 80 / 1080)
        self.assertAlmostEqual(evidence["keyed_rate_above"], 120 / 920)

    def test_analyze_reports_only_conditions_with_distinct_regimes(self):
        changed = {f"H{i}": lot({130: 50}, {120: 300}) for i in range(3)}
        changed.update({"L1": lot({25: 30, 130: 70}, {20: 400, 130: 100})})
        steady = {f"S{i}": lot({40: 50}, {30: 300}) for i in range(3)}
        cells = {
            ("FAM", "BC", "0", "2", "2", ""): changed,
            ("FAM", "FC", "1", "2", "2", ""): steady,
        }
        dates = {name: datetime(2026, 6, index + 1, tzinfo=timezone.utc) for index, name in enumerate(sorted({*changed, *steady}))}

        results = analyze(cells, dates)

        self.assertEqual(len(results), 1)
        entry = results[0]
        self.assertEqual([group["limit"] for group in entry["regimes"]], [20, 120])
        change = entry["changes"][0]
        self.assertEqual((change["low_limit"], change["high_limit"]), (20, 120))
        self.assertAlmostEqual(change["keyed_share_below"], 0.3)

        root = ElementTree.fromstring(ElementTree.tostring(to_xml(results, basis={"feature": "area_max"})))
        self.assertEqual(root.get("status"), "DRAFT_ANALYSIS_NOT_A_SPEC")
        self.assertEqual(root.find("./Condition/Change").get("keyedShareBelow"), "0.3000")
        self.assertEqual(len(root.findall("./Condition/Regime")), 2)


if __name__ == "__main__":
    unittest.main()
