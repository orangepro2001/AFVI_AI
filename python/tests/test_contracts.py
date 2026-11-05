import unittest

from afvi_ai.contracts import (
    LabelConfidence,
    LabelSource,
    Sample,
    SampleKey,
    label_from_ivs,
    label_from_cluster,
    normalize_export_record,
    validate_unique_keys,
)


class LabelTests(unittest.TestCase):
    def test_numeric_and_alpha_codes_are_positive_and_preserved(self):
        for code in ("12", "OT15"):
            with self.subTest(code=code):
                evidence = label_from_ivs(code, vrs_observed=True)
                self.assertEqual(evidence.ivs_1, code)
                self.assertEqual(evidence.label, 1)
                self.assertEqual(evidence.source, LabelSource.VRS_CODE)
                self.assertEqual(evidence.confidence, LabelConfidence.HIGH)

    def test_zero_and_missing_code_in_vrs_are_low_confidence_assumed_good(self):
        for code in ("0", None, ""):
            with self.subTest(code=code):
                evidence = label_from_ivs(code, vrs_observed=True)
                self.assertEqual(evidence.ivs_1, code)
                self.assertEqual(evidence.label, 0)
                self.assertEqual(evidence.source, LabelSource.UNKEYED_ASSUMED_GOOD)
                self.assertEqual(evidence.confidence, LabelConfidence.LOW)

    def test_default_zero_without_vrs_rewrite_is_unobserved(self):
        evidence = label_from_ivs("0", vrs_observed=False)
        self.assertEqual(evidence.ivs_1, "0")
        self.assertIsNone(evidence.label)
        self.assertEqual(evidence.source, LabelSource.NO_VRS_FILE)
        self.assertEqual(evidence.confidence, LabelConfidence.UNOBSERVED)

    def test_conflicting_codes_are_ambiguous_and_excluded(self):
        evidence = label_from_cluster(("OT15", "0"), vrs_observed=True)
        self.assertIsNone(evidence.label)
        self.assertTrue(evidence.excluded)
        self.assertEqual(evidence.confidence, LabelConfidence.AMBIGUOUS)
        self.assertEqual(evidence.ivs_1_values, ("OT15", "0"))

    def test_missing_and_zero_are_both_unkeyed_not_a_conflict(self):
        evidence = label_from_cluster((None, "0"), vrs_observed=True)
        self.assertEqual(evidence.label, 0)
        self.assertFalse(evidence.excluded)
        self.assertEqual(evidence.ivs_1_values, (None, "0"))

    def test_cluster_without_vrs_rewrite_is_unobserved(self):
        evidence = label_from_cluster(("OT15", "0"), vrs_observed=False)
        self.assertIsNone(evidence.label)
        self.assertFalse(evidence.excluded)
        self.assertEqual(evidence.source, LabelSource.NO_VRS_FILE)

    def test_natural_key_duplicate_is_rejected(self):
        key = SampleKey("AFVI13", "LOT/1/001", "0001", "FC", 17, 2)
        first = Sample(key, "LOT", {}, None, "0", True, 0, LabelSource.UNKEYED_ASSUMED_GOOD, LabelConfidence.LOW)
        second = Sample(key, "LOT", {}, None, "0", True, 0, LabelSource.UNKEYED_ASSUMED_GOOD, LabelConfidence.LOW)
        with self.assertRaisesRegex(ValueError, "duplicate sample key"):
            validate_unique_keys((first, second))

    def test_pxspec_export_is_normalized_without_promoting_non_vrs_zero(self):
        row = {
            "machine": "AFVI13",
            "result_group": "25FBO015-15/LOT-A/0298",
            "lot": "LOT-A",
            "strip": "0001",
            "side": "FC",
            "unit_idx": 17,
            "img_merge_idx": 2,
            "features": {"area_sum": 12.5, "light_id": 1},
            "image_rel": "0001/Unit/U17/FC/p2.bmp",
            "vrs_observed": False,
            "ivs_1": "0",
            "member_count": 1,
            "inspected_at": "2026-09-10T10:30:00+09:00",
            "missing_feature_bits": 0,
        }
        sample = normalize_export_record(row)
        self.assertEqual(sample.key.machine, "AFVI13")
        self.assertEqual(sample.lot, "LOT-A")
        self.assertEqual(sample.key.img_merge_idx, 2)
        self.assertEqual(sample.features["area_sum"], 12.5)
        self.assertEqual(sample.ivs_1, "0")
        self.assertIsNone(sample.label)
        self.assertEqual(sample.label_source, LabelSource.NO_VRS_FILE)
        self.assertFalse(sample.vrs_observed)

    def test_export_requires_an_explicit_lot_id(self):
        row = {
            "machine": "AFVI13",
            "result_group": "25FBO015-15/D07834026H3112/3",
            "strip": "0001",
            "side": "FC",
            "unit_idx": 17,
            "img_merge_idx": 1,
            "features": {},
            "vrs_observed": True,
            "ivs_1": "0",
        }
        with self.assertRaisesRegex(ValueError, "lot must be a non-empty string"):
            normalize_export_record(row)

    def test_export_lot_must_match_real_result_path_component(self):
        row = {
            "machine": "AFVI13",
            "result_group": "25FBO015-15/D07834026H3112/3",
            "lot": "25FBO015-15",
            "strip": "0001",
            "side": "FC",
            "unit_idx": 17,
            "img_merge_idx": 1,
            "features": {},
            "vrs_observed": True,
            "ivs_1": "0",
        }
        with self.assertRaisesRegex(ValueError, "second result_group path component"):
            normalize_export_record(row)

    def test_timezone_is_required_for_export_timestamps(self):
        row = {
            "machine": "AFVI13",
            "result_group": "25FBO015-15/D07834026H3112/3",
            "lot": "D07834026H3112",
            "strip": "0001",
            "side": "FC",
            "unit_idx": 17,
            "img_merge_idx": 1,
            "features": {},
            "vrs_observed": True,
            "ivs_1": "0",
            "inspected_at": "2026-09-15T18:26:19",
        }
        with self.assertRaisesRegex(ValueError, "timezone offset"):
            normalize_export_record(row)


if __name__ == "__main__":
    unittest.main()
