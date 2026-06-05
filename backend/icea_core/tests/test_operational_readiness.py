from __future__ import annotations

import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings

from icea_core.operational_readiness import (
    FAIL,
    PASS,
    WARN,
    _audit_events_have_safe_actors,
    build_readiness_report,
    build_smoke_report,
    canonical_api_path,
    smoke_get,
    smoke_post,
)
from icea_core.tests.helpers import ICEAPlusFixtureMixin


SAFE_ENV = {
    "SECRET_KEY": "readiness-test-secret-not-for-production",
    "JWT_SIGNING_KEY": "readiness-test-jwt-key-not-for-production",
    "AUDIT_LOG_SECRET": "readiness-test-audit-secret-not-for-production",
    "ICEA_DEV_ALLOW_INSECURE": "false",
    "ICEA_AUTH_REQUIRED": "true",
    "ICEA_RBAC_ENFORCE": "true",
    "ICEA_SECURE_MODE": "true",
}


SAFE_SETTINGS = {
    "SECRET_KEY": "readiness-test-secret-not-for-production",
    "DEBUG": False,
    "ICEA_SECURE_MODE": True,
    "ICEA_DEV_ALLOW_INSECURE": False,
    "ICEA_AUTH_REQUIRED": True,
    "ICEA_RBAC_ENFORCE": True,
    "AUDIT_LOG_SECRET": "readiness-test-audit-secret-not-for-production",
    "ALLOWED_HOSTS": ["testserver", "localhost"],
    "CORS_ALLOW_ALL_ORIGINS": False,
}


class ICEAReadinessCommandTests(ICEAPlusFixtureMixin, TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp_dir = TemporaryDirectory()
        cls._settings_override = override_settings(
            BASE_DIR=Path(cls._temp_dir.name),
            ICEA_MODEL_DIR=str(Path(cls._temp_dir.name) / "models"),
        )
        cls._settings_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            cls._settings_override.disable()
            cls._temp_dir.cleanup()

    def _safe_settings(self, **extra):
        values = dict(SAFE_SETTINGS)
        values.update(extra)
        return override_settings(**values)

    def _codes(self, report):
        return {check["code"]: check for check in report["checks"]}

    def test_smoke_request_helpers_use_https_in_secure_mode_and_canonical_paths(self):
        class RecordingClient:
            def __init__(self):
                self.calls = []

            def get(self, path, payload=None, **kwargs):
                self.calls.append(("get", path, payload, kwargs))
                return object()

            def post(self, path, payload=None, **kwargs):
                self.calls.append(("post", path, payload, kwargs))
                return object()

        client = RecordingClient()
        with override_settings(ICEA_SECURE_MODE=True, DEBUG=False):
            smoke_get(client, "/api/v1/models")
            smoke_post(client, "/api/v1/models/train", {"x": 1})

        self.assertEqual(client.calls[0][1], "/api/v1/models/")
        self.assertTrue(client.calls[0][3]["secure"])
        self.assertEqual(client.calls[1][1], "/api/v1/models/train/")
        self.assertTrue(client.calls[1][3]["secure"])
        self.assertEqual(client.calls[1][3]["content_type"], "application/json")

    def test_readiness_check_passes_with_secure_minimal_configuration(self):
        with self._safe_settings(), mock.patch.dict(os.environ, SAFE_ENV, clear=False):
            report = build_readiness_report()

        self.assertEqual(report["status"], PASS)
        self.assertFalse(report["failures"])
        self.assertEqual(self._codes(report)["models.demo.shadow_aggregate_defensible"]["status"], PASS)

    def test_readiness_fails_if_jwt_key_missing_in_secure_auth_mode(self):
        env = dict(SAFE_ENV)
        env.pop("JWT_SIGNING_KEY")
        with self._safe_settings(), mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("JWT_SIGNING_KEY", None)
            os.environ.pop("JWT_VERIFYING_KEY", None)
            os.environ.pop("OIDC_JWKS_URL", None)
            report = build_readiness_report()

        check = self._codes(report)["config.jwt.dedicated_key_source"]
        self.assertEqual(report["status"], FAIL)
        self.assertEqual(check["status"], FAIL)

    def test_readiness_fails_if_throttling_is_misconfigured_in_secure_mode(self):
        rest_framework = dict(settings.REST_FRAMEWORK)
        rest_framework["DEFAULT_THROTTLE_CLASSES"] = []
        rest_framework["DEFAULT_THROTTLE_RATES"] = {}

        with (
            self._safe_settings(ICEA_ENABLE_THROTTLING=True, REST_FRAMEWORK=rest_framework),
            mock.patch.dict(os.environ, SAFE_ENV, clear=False),
        ):
            report = build_readiness_report()

        check = self._codes(report)["config.throttling.global_and_scoped"]
        self.assertEqual(report["status"], FAIL)
        self.assertEqual(check["status"], FAIL)

    def test_readiness_command_output_is_json_and_does_not_print_secrets(self):
        out = io.StringIO()
        with self._safe_settings(), mock.patch.dict(os.environ, SAFE_ENV, clear=False):
            call_command("icea_readiness_check", stdout=out)

        body = out.getvalue()
        parsed = json.loads(body)
        self.assertEqual(parsed["status"], PASS)
        for secret in SAFE_ENV.values():
            if secret.startswith("readiness-test-"):
                self.assertNotIn(secret, body)

    def test_readiness_command_reports_json_fail_for_empty_secret_key(self):
        out = io.StringIO()
        with override_settings(SECRET_KEY="", ICEA_SECURE_MODE=True, ALLOWED_HOSTS=[]):
            call_command("icea_readiness_check", stdout=out)

        parsed = json.loads(out.getvalue())
        self.assertEqual(parsed["status"], FAIL)
        self.assertEqual(self._codes(parsed)["config.secret_key.present"]["status"], FAIL)
        self.assertNotIn("readiness-test-secret-not-for-production", out.getvalue())

    def test_readiness_command_strict_exit_returns_exit_code_one_on_failure(self):
        out = io.StringIO()
        with override_settings(SECRET_KEY="", ICEA_SECURE_MODE=True, ALLOWED_HOSTS=[]):
            with self.assertRaises(SystemExit) as raised:
                call_command("icea_readiness_check", "--strict-exit", stdout=out)

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(json.loads(out.getvalue())["status"], FAIL)


class ICEASmokeCommandTests(ICEAPlusFixtureMixin, TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp_dir = TemporaryDirectory()
        cls._settings_override = override_settings(
            BASE_DIR=Path(cls._temp_dir.name),
            ICEA_MODEL_DIR=str(Path(cls._temp_dir.name) / "models"),
        )
        cls._settings_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            cls._settings_override.disable()
            cls._temp_dir.cleanup()

    def _codes(self, report):
        return {check["code"]: check for check in report["checks"]}

    def test_smoke_uses_canonical_routes_and_does_not_observe_redirects(self):
        with (
            override_settings(**SAFE_SETTINGS),
            mock.patch.dict(os.environ, SAFE_ENV, clear=False),
        ):
            report = build_smoke_report()

        details = " ".join(check["detail"] for check in report["checks"])
        self.assertEqual(canonical_api_path("models-list", "/api/v1/models/"), "/api/v1/models/")
        self.assertEqual(canonical_api_path("icea-plus-score", "/api/v1/icea-plus/score/"), "/api/v1/icea-plus/score/")
        self.assertNotIn("status_code=301", details)
        self.assertIn("status_code=", details)

    def test_smoke_test_no_individual_score_legacy_block_and_audit_guards(self):
        with (
            override_settings(**SAFE_SETTINGS),
            mock.patch.dict(
                os.environ,
                SAFE_ENV,
                clear=False,
            ),
        ):
            report = build_smoke_report()

        codes = self._codes(report)
        self.assertIn(report["status"], {PASS, WARN}, report)
        self.assertEqual(codes["smoke.score.no_individual_numeric_score"]["status"], PASS)
        self.assertEqual(codes["smoke.legacy_compute.censored"]["status"], PASS)
        self.assertEqual(codes["smoke.non_defensible_model.blocked"]["status"], PASS)
        self.assertEqual(codes["smoke.non_defensible_baseline.blocked"]["status"], PASS)
        self.assertEqual(codes["smoke.audit.actor_pseudonymous"]["status"], PASS)

    def test_smoke_command_output_is_parseable_and_exits_zero_without_blocking_failures(self):
        out = io.StringIO()
        with (
            override_settings(**SAFE_SETTINGS),
            mock.patch.dict(
                os.environ,
                SAFE_ENV,
                clear=False,
            ),
        ):
            call_command("icea_smoke_test", stdout=out)

        parsed = json.loads(out.getvalue())
        self.assertIn(parsed["status"], {PASS, WARN}, parsed)
        self.assertFalse(parsed["failures"], parsed)

    def test_smoke_missing_demo_is_warning_not_blocking_security_failure(self):
        with (
            override_settings(**SAFE_SETTINGS),
            mock.patch.dict(os.environ, SAFE_ENV, clear=False),
            mock.patch("icea_core.operational_readiness._governed_demo_artifacts", return_value=[]),
        ):
            report = build_smoke_report()

        codes = self._codes(report)
        self.assertEqual(report["status"], WARN, report)
        self.assertEqual(codes["smoke.model.governed_available"]["status"], WARN)
        self.assertFalse(report["failures"])

    def test_smoke_fails_if_individual_score_is_detected(self):
        with (
            override_settings(**SAFE_SETTINGS),
            mock.patch.dict(os.environ, SAFE_ENV, clear=False),
            mock.patch("icea_core.operational_readiness._redacted_score_payload", return_value=False),
        ):
            report = build_smoke_report()

        self.assertEqual(report["status"], FAIL)
        self.assertEqual(self._codes(report)["smoke.score.no_individual_numeric_score"]["status"], FAIL)
        self.assertIn("status_code=200", self._codes(report)["smoke.score.no_individual_numeric_score"]["detail"])

    def test_smoke_fails_if_protected_endpoint_is_open_without_auth(self):
        class FakeResponse:
            def __init__(self, status_code, body=None):
                self.status_code = status_code
                self._body = body or {}

            def json(self):
                return self._body

        class FakeClient:
            raise_request_exception = False

            def force_authenticate(self, user=None):
                return None

            def get(self, path, payload=None, **kwargs):
                if path == "/api/v1/health/":
                    return FakeResponse(200, {"status": "ok"})
                if path == "/api/v1/models/":
                    return FakeResponse(200, [])
                return FakeResponse(404, {})

            def post(self, path, payload=None, content_type=None, **kwargs):
                return FakeResponse(404, {})

        with (
            override_settings(**SAFE_SETTINGS),
            mock.patch.dict(os.environ, SAFE_ENV, clear=False),
            mock.patch("icea_core.operational_readiness._governed_demo_artifacts", return_value=[]),
            mock.patch("icea_core.operational_readiness._smoke_client", return_value=FakeClient()),
        ):
            report = build_smoke_report()

        check = self._codes(report)["smoke.models.unauth_blocked"]
        self.assertEqual(report["status"], FAIL)
        self.assertEqual(check["status"], FAIL)
        self.assertIn("status_code=200", check["detail"])

    def test_audit_actor_raw_identity_is_not_safe(self):
        self.assertFalse(_audit_events_have_safe_actors([{"actor": "clinician@example.test"}]))
        self.assertTrue(_audit_events_have_safe_actors([{"actor": "authenticated_user:" + "a" * 64}]))
