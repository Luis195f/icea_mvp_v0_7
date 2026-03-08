from django.test import SimpleTestCase

from icea_core.formula import ICEAPlusComponentValue, ICEAPlusLineage, compute_row_score
from icea_core.specs import build_default_icea_plus_spec


class ICEAPlusFormulaTests(SimpleTestCase):
    def setUp(self):
        self.spec = build_default_icea_plus_spec()
        self.lineage = ICEAPlusLineage(
            formula_version="icea_plus_v1",
            formula_protocol_hash="hash",
            model_id="model-1",
            model_version="v1",
            baseline_model_id=None,
            causal_spec_hash=None,
            outcome="delta_ri",
            outcome_goal="higher_is_better",
            treatment="nurse_hppd",
            nurse_cols=["nurse_hppd"],
            transformations={"utility": "identity"},
            source={"grain": "episode"},
        )

    def _components(self, *, b=0.0, a=0.0, c=0.0, q=0.0, u=0.0, causal_available=True):
        return {
            "benefit": ICEAPlusComponentValue(raw=b, normalized=b, available=True),
            "attribution": ICEAPlusComponentValue(raw=a, normalized=a, available=True),
            "causal": ICEAPlusComponentValue(raw=c, normalized=c, available=causal_available),
            "quality": ICEAPlusComponentValue(raw=q, normalized=q, available=True),
            "uncertainty": ICEAPlusComponentValue(raw=u, normalized=u, available=True),
        }

    def test_score_increases_with_benefit_attribution_and_causal(self):
        low = compute_row_score(
            row_id="row-1",
            grain="episode",
            episode_id=1,
            window_id=None,
            patient_key="1",
            unit_id=1,
            start_dt=None,
            end_dt=None,
            components=self._components(b=0.1, a=0.1, c=0.1, q=0.1, u=0.1),
            weights=self.spec["weights"],
            raw_uncertainty=0.1,
            lineage=self.lineage,
            legacy_icea={},
            aggregation={"severity_weight": 1.0},
            spec=self.spec,
        )
        high = compute_row_score(
            row_id="row-2",
            grain="episode",
            episode_id=2,
            window_id=None,
            patient_key="2",
            unit_id=1,
            start_dt=None,
            end_dt=None,
            components=self._components(b=0.9, a=0.7, c=0.8, q=0.1, u=0.1),
            weights=self.spec["weights"],
            raw_uncertainty=0.1,
            lineage=self.lineage,
            legacy_icea={},
            aggregation={"severity_weight": 1.0},
            spec=self.spec,
        )
        self.assertLess(float(low.score), float(high.score))

    def test_score_decreases_when_uncertainty_rises(self):
        low_u = compute_row_score(
            row_id="row-1",
            grain="episode",
            episode_id=1,
            window_id=None,
            patient_key="1",
            unit_id=1,
            start_dt=None,
            end_dt=None,
            components=self._components(b=0.5, a=0.5, c=0.5, q=0.5, u=0.1),
            weights=self.spec["weights"],
            raw_uncertainty=0.1,
            lineage=self.lineage,
            legacy_icea={},
            aggregation={"severity_weight": 1.0},
            spec=self.spec,
        )
        high_u = compute_row_score(
            row_id="row-2",
            grain="episode",
            episode_id=2,
            window_id=None,
            patient_key="2",
            unit_id=1,
            start_dt=None,
            end_dt=None,
            components=self._components(b=0.5, a=0.5, c=0.5, q=0.5, u=0.9),
            weights=self.spec["weights"],
            raw_uncertainty=0.9,
            lineage=self.lineage,
            legacy_icea={},
            aggregation={"severity_weight": 1.0},
            spec=self.spec,
        )
        self.assertGreater(float(low_u.score), float(high_u.score))
        self.assertGreaterEqual(float(high_u.score), 0.0)
        self.assertLessEqual(float(high_u.score), 100.0)

    def test_missing_causal_yields_provisional_state(self):
        row = compute_row_score(
            row_id="row-3",
            grain="episode",
            episode_id=3,
            window_id=None,
            patient_key="3",
            unit_id=1,
            start_dt=None,
            end_dt=None,
            components=self._components(b=0.5, a=0.4, c=0.0, q=0.5, u=0.2, causal_available=False),
            weights=self.spec["weights"],
            raw_uncertainty=0.2,
            lineage=self.lineage,
            legacy_icea={},
            aggregation={"severity_weight": 1.0},
            spec=self.spec,
        )
        self.assertEqual(row.status, "provisional")
        self.assertTrue(row.provisional)
        self.assertTrue(row.flags["missing_key_inputs"] is False)

    def test_missing_required_component_yields_insufficient_evidence(self):
        components = self._components()
        components["benefit"] = ICEAPlusComponentValue(raw=None, normalized=None, available=False)
        row = compute_row_score(
            row_id="row-4",
            grain="episode",
            episode_id=4,
            window_id=None,
            patient_key="4",
            unit_id=1,
            start_dt=None,
            end_dt=None,
            components=components,
            weights=self.spec["weights"],
            raw_uncertainty=0.2,
            lineage=self.lineage,
            legacy_icea={},
            aggregation={"severity_weight": 1.0},
            spec=self.spec,
        )
        self.assertEqual(row.status, "insufficient_evidence")
        self.assertIsNone(row.score)
