import json
import tempfile
import unittest
from pathlib import Path

from datetime import datetime, timezone

from afvi_ai.baseline import build_report, model_from_dict
from afvi_ai.train import final_model_artifact, input_files, label_state, load_sampled


def record(lot, index, positive):
    return {
        "machine": "AFVI13",
        "result_group": f"MODEL/{lot}/1",
        "lot": lot,
        "strip": "0001",
        "side": "FC",
        "unit_idx": 0,
        "img_merge_idx": index,
        "features": {"area_total": 5.0, "length_max": 2.0, "width_max": 1.0},
        "vrs_observed": True,
        "ivs_1": "OT15" if positive else "0",
        "inspected_at": "2026-09-01T00:00:00+09:00",
    }


class SampledLoaderTests(unittest.TestCase):
    def write_exports(self, root):
        (root / "AFVI13/MODEL/LOT-A").mkdir(parents=True)
        (root / "AFVI13/MODEL/LOT-B").mkdir(parents=True)
        with (root / "AFVI13/MODEL/LOT-A/1.jsonl").open("w") as stream:
            for index in range(1, 101):
                stream.write(json.dumps(record("LOT-A", index, positive=index <= 5)) + "\n")
        with (root / "AFVI13/MODEL/LOT-B/1.jsonl").open("w") as stream:
            for index in range(1, 11):
                stream.write(json.dumps(record("LOT-B", index, positive=False)) + "\n")

    def test_reservoir_caps_each_lot_state_and_weights_restore_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_exports(root)

            samples, sampling = load_sampled(input_files(root), max_rows_per_lot_state=20, seed=3)

            counts = {}
            weights = {}
            for sample in samples:
                stratum = (sample.lot, label_state(sample))
                counts[stratum] = counts.get(stratum, 0) + 1
                weights[stratum] = weights.get(stratum, 0.0) + sample.weight
            self.assertEqual(counts[("LOT-A", "assumed_good")], 20)
            self.assertEqual(counts[("LOT-A", "positive")], 5)
            self.assertEqual(counts[("LOT-B", "assumed_good")], 10)
            self.assertAlmostEqual(weights[("LOT-A", "assumed_good")], 95.0)
            self.assertAlmostEqual(weights[("LOT-A", "positive")], 5.0)
            self.assertEqual(sampling["input_files"], 2)
            self.assertEqual(sampling["input_rows"], 110)
            self.assertEqual(sampling["kept_rows"], 35)
            self.assertEqual(sampling["sampled_strata"], 1)

    def test_sampling_is_deterministic_for_a_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_exports(root)

            first, _ = load_sampled(input_files(root), max_rows_per_lot_state=7, seed=1)
            second, _ = load_sampled(input_files(root), max_rows_per_lot_state=7, seed=1)

            self.assertEqual([row.key for row in first], [row.key for row in second])

    def test_duplicate_keys_across_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = json.dumps(record("LOT-A", 1, positive=False)) + "\n"
            (root / "a.jsonl").write_text(row)
            (root / "b.jsonl").write_text(row)

            with self.assertRaises(ValueError):
                load_sampled(input_files(root), max_rows_per_lot_state=5)


class FinalModelTests(unittest.TestCase):
    def test_final_model_is_refit_on_every_labeled_lot_with_the_report_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for lot_index in range(12):
                lot = f"LOT-{lot_index:02d}"
                inspected_at = f"2026-{9 if lot_index < 6 else 10:02d}-{lot_index + 1:02d}T00:00:00+09:00"
                lot_dir = root / "AFVI13" / "MODEL" / lot
                lot_dir.mkdir(parents=True)
                with (lot_dir / "1.jsonl").open("w") as stream:
                    for index in range(1, 31):
                        row = record(lot, index, positive=index % 2 == 0)
                        row["features"]["area_total"] = 20.0 if index % 2 == 0 else 1.0
                        row["inspected_at"] = inspected_at
                        stream.write(json.dumps(row) + "\n")
            samples, _ = load_sampled(input_files(root), max_rows_per_lot_state=50)
            report = build_report(
                samples,
                cutoff=datetime(2026, 10, 1, tzinfo=timezone.utc),
                data_source={"export": "fixture"},
            )

            artifact = json.loads(json.dumps(final_model_artifact(samples, report)))
            model = model_from_dict(artifact)

            self.assertEqual(len(artifact["training_lots"]), 12)
            self.assertEqual(artifact["feature_spec"]["name"], report["model"]["feature_set"])
            self.assertEqual(artifact["l2_strength"], report["model"]["l2_strength"])
            self.assertEqual(artifact["metadata"]["evaluation_status"], "evaluated")
            positive = next(row for row in samples if row.label == 1)
            negative = next(row for row in samples if row.label == 0)
            self.assertGreater(model.score(positive), model.score(negative))


if __name__ == "__main__":
    unittest.main()
