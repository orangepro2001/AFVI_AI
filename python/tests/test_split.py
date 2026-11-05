import unittest
from datetime import datetime, timezone

from afvi_ai.contracts import LabelConfidence, LabelSource, Sample, SampleKey
from afvi_ai.split import split_by_lot_time


def sample(lot, group, timestamp):
    key = SampleKey("AFVI13", f"MODEL/{lot}/{group}", "0001", "FC", 1, 1)
    return Sample(
        key=key,
        lot=lot,
        features={},
        image_ref=None,
        ivs_1="0",
        vrs_observed=True,
        label=0,
        label_source=LabelSource.UNKEYED_ASSUMED_GOOD,
        label_confidence=LabelConfidence.LOW,
        inspected_at=timestamp,
    )


class SplitTests(unittest.TestCase):
    def test_lot_is_kept_whole_and_post_cutoff_data_only_enters_test(self):
        cutoff = datetime(2026, 10, 1, tzinfo=timezone.utc)
        rows = [
            sample("LOT-OLD", "001", datetime(2026, 9, 1, tzinfo=timezone.utc)),
            sample("LOT-NEW", "001", datetime(2026, 9, 1, tzinfo=timezone.utc)),
            sample("LOT-NEW", "002", datetime(2026, 10, 2, tzinfo=timezone.utc)),
        ]
        split = split_by_lot_time(rows, cutoff=cutoff, test_fraction=0.0, seed=9)
        train_lots = {row.lot for row in split.train}
        test_lots = {row.lot for row in split.test}
        self.assertEqual(train_lots, {"LOT-OLD"})
        self.assertEqual(test_lots, {"LOT-NEW"})
        self.assertFalse(train_lots & test_lots)
        self.assertEqual(len(split.test), 2)

    def test_unassigned_old_lots_are_deterministic_and_grouped(self):
        cutoff = datetime(2026, 10, 1, tzinfo=timezone.utc)
        rows = [
            sample(f"LOT-{i:02d}", "001", datetime(2026, 9, 1, tzinfo=timezone.utc))
            for i in range(30)
        ]
        first = split_by_lot_time(rows, cutoff=cutoff, test_fraction=0.3, seed=42)
        second = split_by_lot_time(rows, cutoff=cutoff, test_fraction=0.3, seed=42)
        self.assertEqual([r.key for r in first.test], [r.key for r in second.test])
        self.assertGreater(len(first.test), 0)
        self.assertGreater(len(first.train), 0)
        self.assertFalse({r.lot for r in first.train} & {r.lot for r in first.test})

    def test_unknown_timestamps_are_excluded(self):
        cutoff = datetime(2026, 10, 1, tzinfo=timezone.utc)
        split = split_by_lot_time(
            [sample("LOT-X", "001", None)], cutoff=cutoff, test_fraction=0.2, seed=1
        )
        self.assertEqual(len(split.train), 0)
        self.assertEqual(len(split.test), 0)
        self.assertEqual(len(split.excluded), 1)

    def test_naive_timestamps_are_untrusted_and_exclude_the_whole_lot(self):
        cutoff = datetime(2026, 10, 1, tzinfo=timezone.utc)
        split = split_by_lot_time(
            [
                sample("LOT-X", "001", datetime(2026, 9, 1)),
                sample("LOT-X", "002", datetime(2026, 9, 2, tzinfo=timezone.utc)),
            ],
            cutoff=cutoff,
            test_fraction=0.2,
            seed=1,
        )
        self.assertEqual(len(split.train), 0)
        self.assertEqual(len(split.test), 0)
        self.assertEqual(len(split.excluded), 2)

    def test_cutoff_requires_a_timezone(self):
        with self.assertRaisesRegex(ValueError, "cutoff must include a timezone offset"):
            split_by_lot_time([], cutoff=datetime(2026, 10, 1), test_fraction=0.2)


if __name__ == "__main__":
    unittest.main()
