from django.test import SimpleTestCase
import pandas as pd

from icea_core.components import (
    compute_missingness_burden,
    compute_ood_burden,
    compute_quality_raw,
    robust_z,
    utility_transform,
)
from icea_core.specs import build_default_icea_plus_spec


class ICEAPlusComponentsTests(SimpleTestCase):
    def test_outcome_sign_for_adverse_events(self):
        values = pd.Series([1.0, 2.0, 3.0])
        transformed = utility_transform(values, "lower_is_better")
        self.assertEqual(list(transformed), [-1.0, -2.0, -3.0])

    def test_robust_normalization_falls_back_when_reference_constant(self):
        spec = build_default_icea_plus_spec()
        normalized, info = robust_z(pd.Series([3.0, 5.0]), pd.Series([3.0, 3.0, 3.0]), spec=spec)
        self.assertIsNotNone(normalized)
        self.assertEqual(info["method"], "identity_fallback")
        self.assertEqual(float(normalized.iloc[0]), 0.0)
        self.assertGreater(float(normalized.iloc[1]), 0.0)

    def test_missingness_and_quality_are_visible(self):
        spec = build_default_icea_plus_spec()
        df = pd.DataFrame(
            {
                "nurse_proc_count": [2.0, 0.0],
                "nurse_proc_count_det": [2.0, 0.0],
                "missing_vs_hr_last": [0.0, 1.0],
                "missing_loinc_85556_9_t0": [0.0, 1.0],
                "missing_loinc_85556_9_t1": [0.0, 1.0],
            }
        )
        missingness = compute_missingness_burden(df)
        quality = compute_quality_raw(df, spec)
        self.assertLess(float(missingness.iloc[0]), float(missingness.iloc[1]))
        self.assertGreater(float(quality.raw.iloc[0]), float(quality.raw.iloc[1]))

    def test_ood_penalty_detects_outlier_row(self):
        spec = build_default_icea_plus_spec()
        df = pd.DataFrame({"ri_initial": [50.0, 150.0], "nurse_hppd": [3.0, 20.0]})
        penalty = compute_ood_burden(
            df,
            features=["ri_initial", "nurse_hppd"],
            feature_stats={"mean": {"ri_initial": 50.0, "nurse_hppd": 3.0}, "std": {"ri_initial": 5.0, "nurse_hppd": 1.0}},
            spec=spec,
        )
        self.assertIsNotNone(penalty)
        self.assertEqual(float(penalty.iloc[0]), 0.0)
        self.assertGreater(float(penalty.iloc[1]), 0.0)
