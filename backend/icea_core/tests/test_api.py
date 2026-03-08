from __future__ import annotations

import os
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from icea_core.tests.helpers import ICEAPlusFixtureMixin


class ICEAPlusAPITests(ICEAPlusFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_score_endpoint_returns_breakdown(self):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": True,
                "causal_spec": {
                    "treatment": "nurse_hppd",
                    "outcome": "delta_ri",
                    "confounders": ["ri_initial", "proc_count"],
                    "effect_modifiers": ["ri_initial"],
                    "n_estimators": 20,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("summary", body)
        self.assertIn("results", body)
        self.assertTrue(body["results"])
        self.assertIn("components", body["results"][0])
        self.assertIn("formula_version", body)

    def test_score_endpoint_invalid_payload(self):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "from_db": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_score_endpoint_requires_auth_when_flag_enabled(self):
        with mock.patch.dict(os.environ, {"ICEA_AUTH_REQUIRED": "true"}, clear=False):
            response = self.client.post(
                "/api/v1/icea-plus/score/",
                {"model_id": str(self.episode_artifact.id), "grain": "episode", "from_db": True},
                format="json",
            )
        self.assertIn(response.status_code, {401, 403})

    def test_explain_endpoint_returns_formula_contract(self):
        response = self.client.get("/api/v1/icea-plus/explain/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("equation", body)
        self.assertIn("weights", body)
        self.assertEqual(body["formula_version"], "icea_plus_v1")

    def test_aggregate_endpoint_supports_unit_and_shift(self):
        response_unit = self.client.get(
            "/api/v1/icea-plus/aggregate/",
            {"model_id": str(self.episode_artifact.id), "group_by": "unit", "grain": "episode"},
        )
        self.assertEqual(response_unit.status_code, 200)
        self.assertTrue(response_unit.json()["results"])

        response_shift = self.client.get(
            "/api/v1/icea-plus/aggregate/",
            {"model_id": str(self.window_artifact.id), "group_by": "shift", "grain": "window"},
        )
        self.assertEqual(response_shift.status_code, 200)
        self.assertTrue(response_shift.json()["results"])

    def test_calibrate_endpoint_is_admin_only(self):
        self.client.force_authenticate(user=self.regular_user)
        forbidden = self.client.post(
            "/api/v1/icea-plus/calibrate/",
            {"version": "icea_plus_v1_test", "spec": {"weights": {"benefit": 1.5}}},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_authenticate(user=self.admin_user)
        allowed = self.client.post(
            "/api/v1/icea-plus/calibrate/",
            {"version": "icea_plus_v1_test", "spec": {"weights": {"benefit": 1.5}}, "activate": True},
            format="json",
        )
        self.assertEqual(allowed.status_code, 201)
        self.assertEqual(allowed.json()["formula_version"], "icea_plus_v1_test")
