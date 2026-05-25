from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from icea_core.tests.helpers import ICEAPlusFixtureMixin


class ICEAFailClosedSecurityTests(ICEAPlusFixtureMixin, TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {
                "ICEA_DEV_ALLOW_INSECURE": "false",
                "ICEA_AUTH_REQUIRED": "true",
                "ICEA_RBAC_ENFORCE": "true",
                "ICEA_CAUSAL_DISCOVER_ENABLED": "false",
                "ICEA_SIMULATE_ENABLED": "false",
                "ICEA_FEDERATED_ENABLED": "false",
                "ICEA_POLICY_LEARNING_ENABLED": "false",
                "ICEA_FAIRNESS_ENABLED": "false",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = APIClient()

    def _user_with_role(self, role: str):
        user_model = get_user_model()
        user = user_model.objects.create_user(username=f"user-{role}", password="test-pass")
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        return user

    def _causal_spec(self, *, extras: dict | None = None) -> dict:
        spec = {
            "treatment": "nurse_hppd",
            "outcome": "delta_ri",
            "confounders": ["ri_initial", "proc_count"],
            "effect_modifiers": ["ri_initial"],
            "dag_edges": [["ri_initial", "nurse_hppd"], ["ri_initial", "delta_ri"], ["nurse_hppd", "delta_ri"]],
            "n_estimators": 20,
        }
        if extras:
            spec.update(extras)
        return spec

    def _request_cases(self):
        episode = self.episodes[0]
        return [
            ("icea_plus_score", "post", "/api/v1/icea-plus/score/", {"model_id": str(self.episode_artifact.id), "grain": "episode", "from_db": True}),
            ("icea_plus_calibrate", "post", "/api/v1/icea-plus/calibrate/", {"version": "icea_plus_sec", "spec": {"weights": {"benefit": 1.2}}}),
            ("icea_plus_writeback_patient", "get", "/api/v1/icea-plus/writeback/patient/", {"episode_id": int(episode.id), "model_id": str(self.episode_artifact.id)}),
            ("icea_plus_writeback_summary", "get", "/api/v1/icea-plus/writeback/summary/", {"model_id": str(self.episode_artifact.id)}),
            ("fhir_writeback_riskassessment", "post", "/api/v1/fhir/writeback/riskassessment/", {"episode_id": int(episode.id), "model_id": str(self.episode_artifact.id), "writeback": False}),
            ("fhir_writeback_list", "get", "/api/v1/fhir/writeback/list/", {}),
            ("causal_run", "post", "/api/v1/causal/run/", {"spec": self._causal_spec()}),
            ("causal_report", "get", "/api/v1/causal/report/", {"run_id": "00000000-0000-0000-0000-000000000000"}),
            ("causal_discover", "post", "/api/v1/causal/discover/", {"variables": ["ri_initial", "nurse_hppd", "delta_ri"]}),
            ("causal_simulate", "post", "/api/v1/causal/simulate/", {"spec": self._causal_spec(), "scenarios": [{"name": "more_hppd", "delta": {"nurse_hppd": 0.5}}]}),
            ("federated_start", "post", "/api/v1/federated/round/start/", {"protocol_spec": {"outcome": "delta_ri", "features": ["ri_initial"]}}),
            ("predict_conformal", "post", "/api/v1/predict/conformal/", {"episode_id": int(episode.id), "model_id": str(self.episode_artifact.id)}),
        ]

    def _call(self, method: str, path: str, payload: dict):
        if method == "get":
            return self.client.get(path, payload)
        return self.client.post(path, payload, format="json")

    def test_sensitive_endpoints_reject_unauthenticated_requests(self):
        for name, method, path, payload in self._request_cases():
            with self.subTest(endpoint=name):
                response = self._call(method, path, payload)
                self.assertIn(response.status_code, {401, 403})

    def test_sensitive_endpoints_reject_insufficient_role(self):
        self.client.force_authenticate(user=self._user_with_role("viewer_aggregate"))
        for name, method, path, payload in self._request_cases():
            if name == "icea_plus_score":
                expected_roles = {403}
            elif name in {"causal_run", "causal_report", "predict_conformal"}:
                expected_roles = {403}
            elif name in {"causal_discover", "causal_simulate", "federated_start"}:
                expected_roles = {403}
            else:
                expected_roles = {403}
            with self.subTest(endpoint=name):
                response = self._call(method, path, payload)
                self.assertIn(response.status_code, expected_roles)

    def test_correct_roles_can_access_representative_sensitive_endpoints(self):
        episode = self.episodes[0]
        episode.fhir_patient_id = "patient-sec"
        episode.fhir_encounter_id = "enc-sec"
        episode.save(update_fields=["fhir_patient_id", "fhir_encounter_id"])

        self.client.force_authenticate(user=self._user_with_role("researcher"))
        score = self.client.post(
            "/api/v1/icea-plus/score/",
            {"model_id": str(self.episode_artifact.id), "grain": "episode", "from_db": True, "episode_ids": [int(episode.id)]},
            format="json",
        )
        self.assertEqual(score.status_code, 200)

        causal = self.client.post("/api/v1/causal/run/", {"spec": self._causal_spec()}, format="json")
        self.assertEqual(causal.status_code, 200)

        conformal = self.client.post(
            "/api/v1/predict/conformal/",
            {"episode_id": int(episode.id), "model_id": str(self.episode_artifact.id)},
            format="json",
        )
        self.assertEqual(conformal.status_code, 200)

        self.client.force_authenticate(user=self._user_with_role("viewer_aggregate"))
        aggregate = self.client.get("/api/v1/icea-plus/aggregate/", {"model_id": str(self.episode_artifact.id), "grain": "episode"})
        self.assertEqual(aggregate.status_code, 200)

        self.client.force_authenticate(user=self._user_with_role("admin"))
        summary = self.client.get("/api/v1/icea-plus/writeback/summary/", {"model_id": str(self.episode_artifact.id)})
        self.assertEqual(summary.status_code, 200)

        patient = self.client.get(
            "/api/v1/icea-plus/writeback/patient/",
            {"episode_id": int(episode.id), "model_id": str(self.episode_artifact.id)},
        )
        self.assertEqual(patient.status_code, 200)

        fhir = self.client.post(
            "/api/v1/fhir/writeback/riskassessment/",
            {"episode_id": int(episode.id), "model_id": str(self.episode_artifact.id), "writeback": False},
            format="json",
        )
        self.assertEqual(fhir.status_code, 200)

        calibrate = self.client.post(
            "/api/v1/icea-plus/calibrate/",
            {"version": "icea_plus_sec_ok", "spec": {"weights": {"benefit": 1.1}}},
            format="json",
        )
        self.assertEqual(calibrate.status_code, 201)

    def test_explicit_opt_in_features_are_closed_until_enabled(self):
        self.client.force_authenticate(user=self._user_with_role("admin"))
        blocked = self.client.post(
            "/api/v1/federated/round/start/",
            {"protocol_spec": {"outcome": "delta_ri", "features": ["ri_initial"]}},
            format="json",
        )
        self.assertEqual(blocked.status_code, 403)

        with mock.patch.dict(os.environ, {"ICEA_FEDERATED_ENABLED": "true"}, clear=False):
            allowed = self.client.post(
                "/api/v1/federated/round/start/",
                {"protocol_spec": {"outcome": "delta_ri", "features": ["ri_initial"]}},
                format="json",
            )
        self.assertEqual(allowed.status_code, 200)

    def test_secure_mode_without_token_keys_fails_explicitly(self):
        env = os.environ.copy()
        env.update(
            {
                "DJANGO_SETTINGS_MODULE": "config.settings",
                "DJANGO_DEBUG": "false",
                "ICEA_SECURE_MODE": "true",
                "ICEA_AUTH_REQUIRED": "true",
                "ICEA_RBAC_ENFORCE": "true",
                "ICEA_DEV_ALLOW_INSECURE": "false",
                "SECRET_KEY": "strong-test-secret-not-for-prod",
            }
        )
        for key in ("JWT_SIGNING_KEY", "JWT_VERIFYING_KEY", "OIDC_JWKS_URL"):
            env.pop(key, None)

        proc = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=str(Path(settings.BASE_DIR)),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ICEA_SECURE_MODE=true requires JWT_SIGNING_KEY", proc.stdout + proc.stderr)

    def test_explicit_dev_mode_keeps_local_compatibility(self):
        with mock.patch.dict(os.environ, {"ICEA_DEV_ALLOW_INSECURE": "true", "ICEA_AUTH_REQUIRED": "false", "ICEA_RBAC_ENFORCE": "false"}, clear=False):
            response = self.client.get("/api/v1/icea-plus/aggregate/", {"model_id": str(self.episode_artifact.id), "grain": "episode"})
        self.assertEqual(response.status_code, 200)
