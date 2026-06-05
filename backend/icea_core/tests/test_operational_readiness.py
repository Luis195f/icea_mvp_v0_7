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

from icea_core.operational_readiness import FAIL, PASS, WARN, build_readiness_report, build_smoke_report
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
