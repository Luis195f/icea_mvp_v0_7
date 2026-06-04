from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from icea_core.api_security import append_icea_api_audit
from icea_core.icea_plus_views import (
    ICEAPlusAggregateView,
    ICEAPlusScoreView,
    ICEAPlusWritebackPatientView,
    ICEAPlusWritebackSummaryView,
)
from icea_core.models import ICEAComputation, ModelArtifact
from icea_core.permissions import _audit_permission_denial, _safe_caller_audit_identity
from icea_core.tests.helpers import ICEAPlusFixtureMixin
from icea_core.views import ICEAComputeView, ModelTrainView
from icea_pipeline.models import FHIRWritebackRecord
from icea_pipeline.views import (
    CausalRunView,
    ConformalPredictView,
    DashboardSummaryView,
    PipelineBuildDatasetView,
    PipelineBuildWindowsView,
    PipelineTrainFromDBView,
    RiskAssessmentWritebackView,
    WritebackListView,
)


class ICEAApiSecurityGuardTests(ICEAPlusFixtureMixin, TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {
                "ICEA_DEV_ALLOW_INSECURE": "false",
                "ICEA_AUTH_REQUIRED": "true",
                "ICEA_RBAC_ENFORCE": "true",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()

    def _user_with_role(self, role: str):
        user = get_user_model().objects.create_user(username=f"guard-{role}", password="test-pass")
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        return user

    def test_critical_views_have_explicit_throttle_scopes(self):
        expected = {
            ModelTrainView: "icea_train",
            ICEAComputeView: "icea_compute",
            ICEAPlusScoreView: "icea_compute",
            ICEAPlusAggregateView: "icea_compute",
            ICEAPlusWritebackPatientView: "icea_writeback",
            ICEAPlusWritebackSummaryView: "icea_export",
            PipelineBuildDatasetView: "icea_compute",
            PipelineBuildWindowsView: "icea_compute",
            PipelineTrainFromDBView: "icea_train",
            CausalRunView: "icea_compute",
            ConformalPredictView: "icea_compute",
            RiskAssessmentWritebackView: "icea_writeback",
            WritebackListView: "icea_export",
        }
        for view, scope in expected.items():
            with self.subTest(view=view.__name__):
                self.assertEqual(view.throttle_scope, scope)
                self.assertTrue(view.permission_classes)

        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        for scope in {"icea_read", "icea_compute", "icea_train", "icea_export", "icea_writeback"}:
            self.assertIn(scope, rates)

    def test_training_rejects_service_role(self):
        self.client.force_authenticate(user=self._user_with_role("service"))

        core = self.client.post("/api/v1/models/train/", {}, format="json")
        pipeline = self.client.post("/api/v1/pipeline/train/", {}, format="json")

        self.assertEqual(core.status_code, 403)
        self.assertEqual(pipeline.status_code, 403)

    def test_legacy_compute_rejects_aggregate_viewer(self):
        self.client.force_authenticate(user=self._user_with_role("viewer_aggregate"))
        response = self.client.post(
            "/api/v1/icea/compute/",
            {
                "model_id": str(self.episode_artifact.id),
                "data": [self.episodes[0].feature_row.features],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_conformal_response_suppresses_episode_identifier(self):
        self.client.force_authenticate(user=self._user_with_role("researcher"))
        response = self.client.post(
            "/api/v1/predict/conformal/",
            {"episode_id": int(self.episodes[0].id), "model_id": str(self.episode_artifact.id)},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["episode_id"])
        self.assertTrue(response.json()["identifier_suppressed"])
        self.assertNotIn("predictions", response.json())

    def test_predict_and_fhir_writeback_block_non_defensible_model(self):
        artifact = ModelArtifact.objects.create(
            name="legacy-no-evidence",
            version="v0",
            target="delta_ri",
            features=["ri_initial"],
            model_type="xgboost",
            model_path="missing.json",
            metrics={},
        )
        self.client.force_authenticate(user=self._user_with_role("researcher"))
        predict = self.client.post(
            "/api/v1/predict/conformal/",
            {"episode_id": int(self.episodes[0].id), "model_id": str(artifact.id)},
            format="json",
        )
        self.assertEqual(predict.status_code, 400)
        self.assertEqual(predict.json()["detail"], "model_not_defensible")

        self.client.force_authenticate(user=self._user_with_role("admin"))
        writeback = self.client.post(
            "/api/v1/fhir/writeback/riskassessment/",
            {"episode_id": int(self.episodes[0].id), "model_id": str(artifact.id), "writeback": False},
            format="json",
        )
        self.assertEqual(writeback.status_code, 400)
        self.assertEqual(writeback.json()["detail"], "model_not_defensible")

    def test_writeback_list_is_aggregate_only_and_suppresses_low_support(self):
        FHIRWritebackRecord.objects.create(
            episode=self.episodes[0],
            model_id=self.episode_artifact.id,
            payload={"resourceType": "RiskAssessment"},
        )
        self.client.force_authenticate(user=self._user_with_role("admin"))

        response = self.client.get("/api/v1/fhir/writeback/list/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "aggregate_only")
        self.assertTrue(body["results"][0]["suppressed"])
        self.assertIsNone(body["results"][0]["count"])
        self.assertNotIn("episode_id", str(body))
        self.assertNotIn("patient_id", str(body))
        self.assertNotIn('"score"', str(body))

    def test_dashboard_redacts_detailed_summaries(self):
        ICEAComputation.objects.create(
            model=self.episode_artifact,
            rows=1,
            summary={"prediction": 99.0, "patient_id": "must-not-leak"},
        )
        self.client.force_authenticate(user=self._user_with_role("viewer_aggregate"))

        response = self.client.get("/api/v1/dashboard/summary/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["non_individual_use"])
        self.assertIsNone(body["latest_compute"]["summary"])
        self.assertTrue(body["latest_compute"]["summary_redacted"])
        self.assertNotIn("patient_id", str(body))

    def test_audit_helper_drops_clinical_payload_and_identifiers(self):
        request = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=False),
            icea_api_key_authenticated=False,
        )
        with mock.patch("icea_core.api_security.append_audit_event") as append:
            append_icea_api_audit(
                request=request,
                event_type="score_requested",
                context="icea-plus/score",
                model_id=str(self.episode_artifact.id),
                row_count=2,
                episode_id=123,
                patient_id="patient-secret",
                rows=[{"clinical": "payload"}],
                payload={"resourceType": "Patient"},
            )

        audit_payload = append.call_args.kwargs["payload"]
        self.assertEqual(audit_payload["row_count"], 2)
        self.assertNotIn("episode_id", audit_payload)
        self.assertNotIn("patient_id", audit_payload)
        self.assertNotIn("rows", audit_payload)
        self.assertNotIn("payload", audit_payload)

    def test_legacy_compute_emits_requested_and_redacted_audit_events(self):
        self.client.force_authenticate(user=self._user_with_role("researcher"))
        with mock.patch("icea_core.views.append_icea_api_audit") as audit:
            response = self.client.post(
                "/api/v1/icea/compute/",
                {
                    "model_id": str(self.episode_artifact.id),
                    "data": [self.episodes[0].feature_row.features],
                    "nurse_cols": ["nurse_hppd"],
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        events = [call.kwargs["event_type"] for call in audit.call_args_list]
        self.assertIn("legacy_compute_requested", events)
        self.assertIn("legacy_compute_redacted", events)

    def test_permission_denial_audits_distinct_authenticated_users_independently(self):
        user_a = get_user_model().objects.create_user(username="denied-user-a", password="test-pass")
        user_b = get_user_model().objects.create_user(username="denied-user-b", password="test-pass")
        view = ICEAPlusScoreView()
        request_a = SimpleNamespace(user=user_a, path="/api/v1/icea-plus/score/", method="POST", META={})
        request_b = SimpleNamespace(user=user_b, path="/api/v1/icea-plus/score/", method="POST", META={})

        with mock.patch("icea_core.api_security.append_icea_api_audit") as audit:
            _audit_permission_denial(request_a, view, error_code="insufficient_role")
            _audit_permission_denial(request_b, view, error_code="insufficient_role")

        self.assertEqual(audit.call_count, 2)
        caller_hashes = [call.kwargs["caller_hash"] for call in audit.call_args_list]
        self.assertEqual(len(set(caller_hashes)), 2)

    def test_permission_denial_deduplicates_repeated_authenticated_user(self):
        user = get_user_model().objects.create_user(username="denied-repeat-user", password="test-pass")
        view = ICEAPlusScoreView()
        request = SimpleNamespace(user=user, path="/api/v1/icea-plus/score/", method="POST", META={})

        with mock.patch("icea_core.api_security.append_icea_api_audit") as audit:
            _audit_permission_denial(request, view, error_code="insufficient_role")
            _audit_permission_denial(request, view, error_code="insufficient_role")

        audit.assert_called_once()

    def test_permission_denial_cache_key_and_event_exclude_sensitive_identity_and_headers(self):
        sensitive_email = "clinical.user@example.test"
        sensitive_token = "Bearer raw-secret-token"
        sensitive_cookie = "sessionid=raw-secret-cookie"
        sensitive_path_identifier = "patient-record-123"
        fallback_user = SimpleNamespace(
            is_authenticated=True,
            pk=None,
            id=None,
            username="",
            email=sensitive_email,
        )
        request = SimpleNamespace(
            user=fallback_user,
            path=f"/api/v1/icea-plus/patients/{sensitive_path_identifier}/score/?ignored=true",
            method="post",
            resolver_match=SimpleNamespace(route="api/v1/icea-plus/patients/<uuid:patient_id>/score/"),
            META={
                "REMOTE_ADDR": "198.51.100.10",
                "HTTP_USER_AGENT": "Sensitive User Agent",
                "HTTP_AUTHORIZATION": sensitive_token,
                "HTTP_COOKIE": sensitive_cookie,
            },
            headers={"Authorization": sensitive_token, "Cookie": sensitive_cookie},
        )

        with (
            mock.patch("icea_core.permissions.cache.add", return_value=True) as cache_add,
            mock.patch("icea_core.api_security.append_audit_event") as audit,
        ):
            _audit_permission_denial(request, ICEAPlusScoreView(), error_code="insufficient_role")

        cache_key = cache_add.call_args.args[0]
        audit_call = audit.call_args.kwargs
        serialized = str({"cache_key": cache_key, "audit": audit_call})
        for sensitive in (
            sensitive_email,
            sensitive_token,
            sensitive_cookie,
            sensitive_path_identifier,
            "198.51.100.10",
            "Sensitive User Agent",
        ):
            self.assertNotIn(sensitive, serialized)
        self.assertEqual(audit_call["payload"]["caller_kind"], "authenticated_user_fallback")
        self.assertEqual(len(audit_call["payload"]["caller_hash"]), 64)
        self.assertEqual(audit_call["payload"]["method"], "POST")
        self.assertEqual(audit_call["context"], "/api/v1/icea-plus/patients/<uuid:patient_id>/score")

    def test_anonymous_permission_denial_uses_safe_hash_and_unknown_fallback(self):
        anonymous = SimpleNamespace(is_authenticated=False)
        view = ICEAPlusScoreView()
        request_a = SimpleNamespace(
            user=anonymous,
            path="/api/v1/icea-plus/score/",
            method="POST",
            META={"REMOTE_ADDR": "198.51.100.20", "HTTP_USER_AGENT": "client-a"},
        )
        request_b = SimpleNamespace(
            user=anonymous,
            path="/api/v1/icea-plus/score/",
            method="POST",
            META={"REMOTE_ADDR": "198.51.100.21", "HTTP_USER_AGENT": "client-b"},
        )
        unknown = SimpleNamespace(user=anonymous, path="/api/v1/icea-plus/score/", method="POST", META={})

        kind_a, hash_a = _safe_caller_audit_identity(request_a)
        kind_b, hash_b = _safe_caller_audit_identity(request_b)
        unknown_kind, unknown_hash = _safe_caller_audit_identity(unknown)

        self.assertEqual(kind_a, "anonymous_client")
        self.assertEqual(kind_b, "anonymous_client")
        self.assertNotEqual(hash_a, hash_b)
        self.assertEqual(unknown_kind, "anonymous_unknown")
        self.assertEqual(len(unknown_hash), 64)
        self.assertNotIn("198.51.100.20", hash_a)

        with mock.patch("icea_core.api_security.append_icea_api_audit") as audit:
            _audit_permission_denial(request_a, view, error_code="auth_required")
            _audit_permission_denial(request_b, view, error_code="auth_required")
        self.assertEqual(audit.call_count, 2)

    def test_permission_denial_remains_fail_closed(self):
        self.client.force_authenticate(user=self.regular_user)

        response = self.client.post(
            "/api/v1/icea-plus/calibrate/",
            {"version": "must-remain-blocked", "spec": {"weights": {"benefit": 1.0}}},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
