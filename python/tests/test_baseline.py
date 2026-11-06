import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from afvi_ai.baseline import (
    DEFAULT_NEGATIVE_WEIGHT,
    STRUCTURED,
    STRUCTURED_CODE,
    ModelConfig,
    review_recall,
    _weighted_metrics,
    build_report,
    fit_logistic,
)
from afvi_ai.contracts import normalize_export_record


class BaselineReportTests(unittest.TestCase):
    cutoff = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def make_samples(
        self, lot_count, *, points_per_lot=10, both_classes=True, positive_lots=None
    ):
        samples = []
        for lot_index in range(lot_count):
            lot = f"LOT-{lot_index:03d}"
            after_cutoff = lot_index >= lot_count // 2
            inspected_at = self.cutoff + timedelta(days=lot_index + 1) if after_cutoff else self.cutoff - timedelta(days=1)
            for point_index in range(points_per_lot):
                positive = (
                    both_classes
                    and point_index % 2 == 1
                    and (positive_lots is None or lot in positive_lots)
                )
                size = 20.0 if positive else 1.0
                row = {
                    "machine": "AFVI13",
                    "result_group": f"MODEL/{lot}/001",
                    "lot": lot,
                    "strip": "0001",
                    "side": "FC",
                    "unit_idx": 0,
                    "img_merge_idx": point_index + 1,
                    "features": {
                        "area_total": size,
                        "length_max": size,
                        "width_max": size,
                    },
                    "vrs_observed": True,
                    "ivs_1": "OT15" if positive else "0",
                    "inspected_at": inspected_at.isoformat(),
                }
                samples.append(normalize_export_record(row))
        return tuple(samples)

    def test_one_lot_is_exploratory_and_has_no_validation_metrics(self):
        report = build_report(
            self.make_samples(1),
            cutoff=self.cutoff,
            data_source={"export": "fixture.jsonl"},
        )

        self.assertEqual(report["model"]["status"], "exploratory_only")
        self.assertEqual(report["evaluation"]["status"], "insufficient_support")
        self.assertIsNone(report["evaluation"]["metrics"])
        self.assertIsNone(report["evaluation"]["bootstrap"])
        self.assertEqual(report["model"]["negative_weight"], DEFAULT_NEGATIVE_WEIGHT)
        self.assertEqual(
            {row["prediction_role"] for row in report["samples"]},
            {"exploratory_in_sample"},
        )

    def test_independent_train_and_held_out_lots_produce_metrics(self):
        report = build_report(
            self.make_samples(10, points_per_lot=40),
            cutoff=self.cutoff,
            test_fraction=0,
            data_source={"export": "fixture.jsonl"},
        )

        self.assertEqual(report["evaluation"]["status"], "evaluated")
        self.assertEqual(report["model"]["status"], "validated_candidate")
        self.assertEqual(len(report["evaluation"]["split"]["train_lots"]), 5)
        self.assertEqual(len(report["evaluation"]["split"]["test_lots"]), 5)
        self.assertGreater(report["evaluation"]["metrics"]["roc_auc"], 0.9)
        self.assertGreater(report["evaluation"]["metrics"]["average_precision"], 0.9)
        test_lots = set(report["evaluation"]["split"]["test_lots"])
        train_lots = set(report["evaluation"]["split"]["train_lots"])
        self.assertEqual({row["lot"] for row in report["samples"]}, test_lots)
        self.assertTrue(all(row["prediction_role"] == "held_out" for row in report["samples"]))
        self.assertFalse({row["lot"] for row in report["samples"]} & train_lots)
        self.assertIn("each represented LOT receives equal weight", report["evaluation"]["metric_weighting"])
        self.assertEqual(
            report["evaluation"]["bootstrap"]["weighting"],
            "equal_class_then_equal_lot_within_class",
        )

    def test_duplicating_a_giant_training_lot_does_not_change_the_fit(self):
        training_rows = tuple(
            row for row in self.make_samples(10, points_per_lot=20) if row.lot < "LOT-005"
        )
        training = []
        for row in training_rows:
            if row.lot == "LOT-000":
                inverted_features = dict(row.features)
                size = 1.0 if row.label == 1 else 20.0
                inverted_features.update(
                    area_total=size,
                    length_max=size,
                    width_max=size,
                )
                row = replace(row, features=inverted_features)
            training.append(row)
        training = tuple(training)
        giant_lot = tuple(row for row in training if row.lot == "LOT-000")
        duplicated_giant_lot = training + tuple(
            replace(row, key=replace(row.key, img_merge_idx=row.key.img_merge_idx + 100_000 * (copy + 1)))
            for copy in range(50)
            for row in giant_lot
        )

        original = fit_logistic(training)
        duplicated = fit_logistic(duplicated_giant_lot)

        for original_value, duplicated_value in zip(original.means, duplicated.means):
            self.assertAlmostEqual(original_value, duplicated_value, places=12)
        for original_value, duplicated_value in zip(original.scales, duplicated.scales):
            self.assertAlmostEqual(original_value, duplicated_value, places=12)
        for original_value, duplicated_value in zip(original.coefficients, duplicated.coefficients):
            self.assertAlmostEqual(original_value, duplicated_value, places=12)
        self.assertAlmostEqual(original.intercept, duplicated.intercept, places=12)

    def test_duplicating_points_in_one_lot_does_not_change_primary_metrics(self):
        samples = self.make_samples(2, points_per_lot=2)
        scored = (
            (samples[0], 0.1),
            (samples[1], 0.9),
            (samples[2], 0.9),
            (samples[3], 0.1),
        )
        repeated_lot = scored[:2] * 100

        original = _weighted_metrics(scored)
        duplicated = _weighted_metrics(repeated_lot + scored[2:])

        self.assertAlmostEqual(original["roc_auc"], 0.5)
        self.assertAlmostEqual(original["roc_auc"], duplicated["roc_auc"])
        self.assertAlmostEqual(original["average_precision"], duplicated["average_precision"])

    def test_class_concentrated_in_one_lot_fails_closed(self):
        positive_lots = {"LOT-000", "LOT-005", "LOT-006", "LOT-007", "LOT-008", "LOT-009"}
        report = build_report(
            self.make_samples(10, points_per_lot=40, positive_lots=positive_lots),
            cutoff=self.cutoff,
            test_fraction=0,
            data_source={"export": "fixture.jsonl"},
        )

        self.assertEqual(report["evaluation"]["status"], "insufficient_support")
        self.assertEqual(
            report["evaluation"]["reason"],
            "Training positive labels need at least 3 independent LOTs.",
        )
        self.assertIsNone(report["evaluation"]["metrics"])

    def test_evaluated_report_includes_reference_and_breakdown(self):
        report = build_report(
            self.make_samples(20, points_per_lot=50),
            cutoff=self.cutoff,
            data_source={"export": "fixture.jsonl"},
        )

        evaluation = report["evaluation"]
        self.assertEqual(evaluation["status"], "evaluated")
        self.assertEqual(evaluation["reference"]["name"], "log1p_area_total")
        self.assertAlmostEqual(evaluation["reference"]["metrics"]["roc_auc"], 1.0)
        difference = evaluation["reference"]["paired_bootstrap"]["roc_auc_difference"]
        self.assertAlmostEqual(difference["low"], 0.0)
        self.assertAlmostEqual(difference["high"], 0.0)
        self.assertEqual([entry["name"] for entry in evaluation["breakdown"]["model"]], ["MODEL"])
        self.assertEqual([entry["name"] for entry in evaluation["breakdown"]["machine"]], ["AFVI13"])

    def test_sampling_weight_restores_lot_class_ratio_in_fit(self):
        full = self.make_samples(6, points_per_lot=40)
        kept = []
        for row in full:
            if row.label == 0:
                kept.append(replace(row, weight=2.0) if row.key.img_merge_idx % 4 == 1 else None)
            else:
                kept.append(row)
        kept = tuple(row for row in kept if row is not None)

        full_model = fit_logistic(full)
        sampled_model = fit_logistic(kept)

        self.assertAlmostEqual(full_model.intercept, sampled_model.intercept, places=6)
        for left, right in zip(full_model.coefficients, sampled_model.coefficients):
            self.assertAlmostEqual(left, right, places=6)

    def test_coverage_counts_use_sampling_weights(self):
        rows = tuple(replace(row, weight=3.0) for row in self.make_samples(1, points_per_lot=10))
        report = build_report(rows, cutoff=self.cutoff, data_source={"export": "fixture.jsonl"})

        self.assertEqual(report["coverage"]["samples"], 30)
        self.assertEqual(report["coverage"]["loaded_rows"], 10)
        self.assertEqual(report["coverage"]["positive"], 15)

    def with_structured_fields(self, rows):
        enriched = []
        for row in rows:
            features = dict(row.features)
            features.update(
                area_max=features["area_total"],
                is_dummy=False,
                def_side="B",
                light_id=2 if row.label == 1 else 1,
                channel_idx=1,
                sr_metal=1,
            )
            enriched.append(replace(row, features=features))
        return tuple(enriched)

    def test_structured_spec_learns_one_hot_levels_from_training_rows(self):
        rows = self.with_structured_fields(self.make_samples(6, points_per_lot=20))
        model = fit_logistic(rows, l2=0.01, spec=STRUCTURED)

        self.assertIn("light_id=1", model.column_names)
        self.assertIn("light_id=2", model.column_names)
        unseen = replace(rows[0], features={**rows[0].features, "light_id": 9})
        self.assertIsNotNone(model.score(unseen))
        positive = next(row for row in rows if row.label == 1)
        negative = next(row for row in rows if row.label == 0)
        self.assertGreater(model.score(positive), model.score(negative))

    def test_tuned_report_selects_config_by_training_lot_cv(self):
        rows = self.with_structured_fields(self.make_samples(20, points_per_lot=30))
        report = build_report(
            rows,
            cutoff=self.cutoff,
            data_source={"export": "fixture.jsonl"},
            tune=True,
            holdout_history="second evaluation of this held-out LOT set",
        )

        selection = report["model"]["selection"]
        self.assertEqual(selection["method"], "lot_grouped_cv_training_lots_only")
        self.assertEqual(len(selection["candidates"]), 8)
        self.assertIn(selection["chosen"]["feature_set"], {"size3", "structured"})
        self.assertEqual(report["model"]["feature_set"], selection["chosen"]["feature_set"])
        self.assertEqual(report["model"]["l2_strength"], selection["chosen"]["l2_strength"])
        self.assertEqual(report["evaluation"]["holdout_history"], "second evaluation of this held-out LOT set")
        json.dumps(report, allow_nan=False)

    def test_fixed_config_with_inspection_codes(self):
        rows = []
        for row in self.with_structured_fields(self.make_samples(20, points_per_lot=30)):
            features = dict(row.features)
            features.update(area_min=features["area_total"], code_main="10207" if row.label == 1 else "2", code_sub="11")
            rows.append(replace(row, features=features))
        report = build_report(
            tuple(rows),
            cutoff=self.cutoff,
            data_source={"export": "fixture.jsonl"},
            config=ModelConfig(STRUCTURED_CODE, 0.0001),
        )

        self.assertEqual(report["model"]["feature_set"], "structured_code")
        self.assertEqual(report["model"]["l2_strength"], 0.0001)
        self.assertIsNone(report["model"]["selection"])
        self.assertIn("code_main=10207", report["model"]["coefficients"])
        with self.assertRaises(ValueError):
            build_report(tuple(rows), cutoff=self.cutoff, data_source={}, tune=True, config=ModelConfig())

    def test_untuned_report_keeps_frozen_size3_config(self):
        report = build_report(
            self.make_samples(20, points_per_lot=30), cutoff=self.cutoff, data_source={"export": "fixture.jsonl"}
        )

        self.assertIsNone(report["model"]["selection"])
        self.assertEqual(report["model"]["feature_set"], "size3")
        self.assertEqual(report["model"]["l2_strength"], 2.0)

    def test_review_recall_uses_weights_ties_and_lot_averaging(self):
        rows = self.make_samples(2, points_per_lot=4)
        lot_a = [row for row in rows if row.lot == "LOT-000"]
        lot_b = [row for row in rows if row.lot == "LOT-001"]
        scored = []
        for row in lot_a:
            scored.append((row, 1.0 if row.label == 1 else 0.0))
        for row in lot_b:
            scored.append((replace(row, weight=3.0) if row.label == 0 else row, 0.5))

        recall = review_recall(scored, (0.25, 0.5, 1.0))

        self.assertAlmostEqual(recall[0], (0.5 + 0.25) / 2)
        self.assertAlmostEqual(recall[1], (1.0 + 0.5) / 2)
        self.assertAlmostEqual(recall[2], 1.0)

    def test_evaluated_report_includes_review_curve(self):
        report = build_report(
            self.make_samples(20, points_per_lot=50), cutoff=self.cutoff, data_source={"export": "fixture.jsonl"}
        )

        curve = report["evaluation"]["review_curve"]
        self.assertEqual(curve["fractions"], [0.1, 0.2, 0.3, 0.5])
        self.assertAlmostEqual(curve["model"][2], 0.6)
        self.assertEqual(curve["random"], [0.1, 0.2, 0.3, 0.5])
        self.assertIn("model_recall_at_30", report["evaluation"]["breakdown"]["model"][0])

    def test_report_is_strict_json_serializable(self):
        report = build_report(
            self.make_samples(1),
            cutoff=self.cutoff,
            data_source={"export": "fixture.jsonl"},
        )

        encoded = json.dumps(report, allow_nan=False)
        decoded = json.loads(encoded)
        self.assertTrue(decoded["historical_vrs_only"])
        self.assertEqual(decoded["model"]["threshold_policy"], "ranking_only_no_operational_cutoff")


if __name__ == "__main__":
    unittest.main()
