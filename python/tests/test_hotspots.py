import unittest
from xml.etree import ElementTree

from afvi_ai.contracts import normalize_export_record
from afvi_ai.hotspots import BIN_PX, collect, summarize, to_xml


def row(index, unit, x, y, keyed):
    return normalize_export_record(
        {
            "machine": "AFVI13",
            "result_group": "FAM/L1/1",
            "lot": "L1",
            "strip": "0001",
            "side": "FC",
            "unit_idx": unit,
            "img_merge_idx": index,
            "features": {"strip_x": float(x), "strip_y": float(y), "area_total": 10.0, "length_max": 1.0, "width_max": 1.0},
            "vrs_observed": True,
            "ivs_1": "OT12" if keyed else "0",
            "inspected_at": "2026-09-01T00:00:00+09:00",
        }
    )


class HotspotTests(unittest.TestCase):
    def build(self):
        rows = []
        index = 0
        for unit, origin in ((0, 0), (1, 10_000)):
            for step in range(10):
                for corner in range(10):
                    index += 1
                    keyed = step == 5 and corner == 5
                    rows.append(row(index, unit, origin + step * BIN_PX, corner * BIN_PX, keyed))
            for _ in range(60):
                index += 1
                rows.append(row(index, unit, origin, 0, False))
        for _ in range(10):
            index += 1
            rows.append(row(index, -1, 50_000, 50_000, True))
        return rows

    def test_unit_relative_grid_finds_edge_false_calls_and_dummy_rate(self):
        bins, codes, lots = collect(self.build())
        results = summarize(bins, lots)

        self.assertEqual(len(results), 1)
        surface = results[0]
        self.assertEqual(surface["points"], 2 * (100 + 60) + 10)
        self.assertEqual(surface["dummy"]["keyed"], 10)
        corner = next(cell for cell in surface["grid"] if (cell["gx"], cell["gy"]) == (0, 0))
        self.assertEqual(corner["points"], 2 * 61)
        self.assertEqual(corner["keyed"], 0)
        self.assertIn(corner, surface["false_call_hotspots"])
        center = next(cell for cell in surface["grid"] if (cell["gx"], cell["gy"]) == (5, 5))
        self.assertEqual(center["keyed"], 2)

        root = ElementTree.fromstring(ElementTree.tostring(to_xml(results, basis={"grid": "10"})))
        self.assertEqual(root.get("status"), "DRAFT_ANALYSIS_NOT_A_SPEC")
        self.assertEqual(root.find("./Surface/FalseCallHotspot").get("gx"), "0")
        self.assertEqual(len(root.findall("./Surface/UnitGrid/Cell")), 100)


if __name__ == "__main__":
    unittest.main()
