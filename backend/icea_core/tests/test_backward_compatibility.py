from django.test import TestCase
from rest_framework.test import APIClient

from icea_core.tests.helpers import ICEAPlusFixtureMixin


class ICEABackwardCompatibilityTests(ICEAPlusFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()

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
        self.assertIn("results", body)
        self.assertIn("icea", body["results"])

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
