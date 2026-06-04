import os
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from icea_core.models import ICEAComputation
from icea_core.tests.helpers import ICEAPlusFixtureMixin


class ICEABackwardCompatibilityTests(ICEAPlusFixtureMixin, TestCase):
    def setUp(self):
        self.dev_env = mock.patch.dict(os.environ, {"ICEA_DEV_ALLOW_INSECURE": "true"}, clear=False)
        self.dev_env.start()
        self.addCleanup(self.dev_env.stop)
        self.client = APIClient()

    def assert_no_individual_compute_outputs(self, value):
        if isinstance(value, dict):
            forbidden = {"predictions", "icea", "contributions", "raw_score", "score"}
            self.assertTrue(forbidden.isdisjoint(value.keys()), f"individual output keys exposed: {forbidden & value.keys()}")
            for nested in value.values():
                self.assert_no_individual_compute_outputs(nested)
        elif isinstance(value, list):
            for nested in value:
                self.assert_no_individual_compute_outputs(nested)

    def test_legacy_icea_compute_endpoint_still_works(self):
        feature_row = self.episodes[0].feature_row
        response = self.client.post(
            "/api/v1/icea/compute/",
            {
                "model_id": str(self.episode_artifact.id),
                "data": [feature_row.features],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "shadow_only")
        self.assertEqual(body["detail"], "legacy_compute_redacted")
        self.assertTrue(body["shadow_mode"])
        self.assertTrue(body["non_individual_use"])
        self.assertTrue(body["score_summary_redacted"])
        self.assertIsNone(body["score_summary"])
        self.assertEqual(body["results"], {})
        self.assertNotIn("predictions", body["summary"])
        self.assertNotIn("icea", body["summary"])
        self.assertNotIn("contributions", body["summary"])
        self.assert_no_individual_compute_outputs(body)

        computation = ICEAComputation.objects.order_by("-created_at").first()
        self.assertIsNotNone(computation)
        self.assertTrue(computation.summary["score_summary_redacted"])
        self.assertNotIn("predictions", computation.summary)
        self.assertNotIn("icea", computation.summary)
        self.assertNotIn("contributions", computation.summary)
        self.assert_no_individual_compute_outputs(computation.summary)

    def test_existing_causal_run_endpoint_still_works(self):
        response = self.client.post(
            "/api/v1/causal/run/",
            {
                "spec": {
                    "treatment": "nurse_hppd",
                    "outcome": "delta_ri",
                    "confounders": ["ri_initial", "proc_count"],
                    "effect_modifiers": ["ri_initial"],
                    "dag_edges": [["ri_initial", "nurse_hppd"], ["ri_initial", "delta_ri"], ["nurse_hppd", "delta_ri"]],
                    "n_estimators": 20,
                    "bootstrap": {"n": 10, "alpha": 0.1},
                    "sensitivity": {"e_value": True},
                }
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("run_id", body)
        self.assertIn("summary", body)
        self.assertIn("ate", body["summary"])
