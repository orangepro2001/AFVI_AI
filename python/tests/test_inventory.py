import tempfile
import unittest
from pathlib import Path

from afvi_ai.inventory import inventory


class InventoryTests(unittest.TestCase):
    def test_lists_groups_with_strips_and_skips_test_lots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AFVI13/MODEL-A/LOT1/1/0001/FC").mkdir(parents=True)
            (root / "AFVI13/MODEL-A/LOT1/1/0002").mkdir(parents=True)
            (root / "AFVI13/MODEL-A/LOT1/2").mkdir(parents=True)
            (root / "AFVI13/MODEL-A/TEST3/1/0001").mkdir(parents=True)
            (root / "AFVI13/MODEL-A/MarkMCName_SH-VRS-31.txt").write_text("x")
            (root / "AFVI14/MODEL-B/LOT2/RH1-/0001").mkdir(parents=True)
            (root / "AFVI14/TEST0825-1/LOT3/1/0001").mkdir(parents=True)
            (root / "AFVI14/MODEL-B/DRY-RUN/1/0001").mkdir(parents=True)
            (root / "AFVI14/MODEL-B/0424-TEST/1/0001").mkdir(parents=True)

            groups, skipped = inventory(root)

            self.assertEqual(
                [(g.machine, g.group_rel, g.strip_count) for g in groups],
                [("AFVI13", "MODEL-A/LOT1/1", 2), ("AFVI14", "MODEL-B/LOT2/RH1-", 1)],
            )
            self.assertEqual(skipped["excluded_lot_name"], 4)
            self.assertEqual(skipped["no_strips"], 1)

    def test_machine_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AFVI13/M/L/1/0001").mkdir(parents=True)
            (root / "AFVI14/M/L/1/0001").mkdir(parents=True)

            groups, _ = inventory(root, ["AFVI14"])

            self.assertEqual([g.machine for g in groups], ["AFVI14"])


if __name__ == "__main__":
    unittest.main()
