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
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from icea_core.api_security import append_icea_api_audit
from icea_core.audit_identity import safe_caller_audit_dedupe_identity
from icea_core.icea_plus_views import (
    ICEAPlusAggregateView,
    ICEAPlusScoreView,
    ICEAPlusWritebackPatientView,
    ICEAPlusWritebackSummaryView,
)
from icea_core.models import ICEAComputation, ModelArtifact
from icea_core.permissions import _audit_permission_denial, _safe_caller_audit_identity
from icea_core.tests.helpers import ICEAPlusFixtureMixin
from icea_core.throttling import IceaAnonRateThrottle, IceaScopedRateThrottle, IceaUserRateThrottle
from icea_core.views import ICEAComputeView, ModelTrainView
from icea_pipeline.audit import append_audit_event
from icea_pipeline.models import AuditEvent, FHIRWritebackRecord
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

    def _canonical_path(self, path: str) -> str:
        # Risk regressions must reach permissions/views, not middleware redirects.
        return path if path.endswith("/") else f"{path}/"

    def _get(self, path: str, payload: dict | None = None, **extra):
        return self.client.get(self._canonical_path(path), payload or {}, secure=True, **extra)

    def _post(self, path: str, payload: dict, **extra):
        return self.client.post(self._canonical_path(path), payload, format="json", secure=True, **extra)

    def test_security_request_helpers_use_canonical_https_paths(self):
        with mock.patch.object(self.client, "get") as get:
            self._get("/api/v1/health")
        get.assert_called_once_with("/api/v1/health/", {}, secure=True)

        with mock.patch.object(self.client, "post") as post:
            self._post("/api/v1/models/train", {})
        post.assert_called_once_with("/api/v1/models/train/", {}, format="json", secure=True)

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

    def test_global_throttles_cover_unscoped_jwt_auth_and_scoped_views(self):
        configured = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]
        self.assertEqual(
            configured,
            [
                "icea_core.throttling.IceaAnonRateThrottle",
                "icea_core.throttling.IceaUserRateThrottle",
                "icea_core.throttling.IceaScopedRateThrottle",
            ],
        )
        self.assertFalse(hasattr(settings, "ICEA_ENABLE_GLOBAL_THROTTLING"))

        for view_class in (TokenObtainPairView, TokenRefreshView):
            with self.subTest(view=view_class.__name__):
                self.assertFalse(hasattr(view_class, "throttle_scope"))
                throttles = view_class().get_throttles()
                self.assertTrue(any(isinstance(throttle, IceaAnonRateThrottle) for throttle in throttles))
                self.assertTrue(any(isinstance(throttle, IceaUserRateThrottle) for throttle in throttles))
                self.assertTrue(any(isinstance(throttle, IceaScopedRateThrottle) for throttle in throttles))

        scoped_throttles = ICEAPlusScoreView().get_throttles()
        self.assertTrue(any(isinstance(throttle, IceaAnonRateThrottle) for throttle in scoped_throttles))
        self.assertTrue(any(isinstance(throttle, IceaUserRateThrottle) for throttle in scoped_throttles))
        self.assertTrue(any(isinstance(throttle, IceaScopedRateThrottle) for throttle in scoped_throttles))

    def test_training_rejects_service_role(self):
        self.client.force_authenticate(user=self._user_with_role("service"))

        core = self._post("/api/v1/models/train/", {})
        pipeline = self._post("/api/v1/pipeline/train/", {})

        self.assertEqual(core.status_code, 403)
        self.assertEqual(pipeline.status_code, 403)

    def test_legacy_compute_rejects_aggregate_viewer(self):
        self.client.force_authenticate(user=self._user_with_role("viewer_aggregate"))
        response = self._post(
            "/api/v1/icea/compute/",
            {
                "model_id": str(self.episode_artifact.id),
                "data": [self.episodes[0].feature_row.features],
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_conformal_response_suppresses_episode_identifier(self):
        self.client.force_authenticate(user=self._user_with_role("researcher"))
        response = self._post(
            "/api/v1/predict/conformal/",
            {"episode_id": int(self.episodes[0].id), "model_id": str(self.episode_artifact.id)},
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
        predict = self._post(
            "/api/v1/predict/conformal/",
            {"episode_id": int(self.episodes[0].id), "model_id": str(artifact.id)},
        )
        self.assertEqual(predict.status_code, 400)
        self.assertEqual(predict.json()["detail"], "model_not_defensible")

        self.client.force_authenticate(user=self._user_with_role("admin"))
        writeback = self._post(
            "/api/v1/fhir/writeback/riskassessment/",
            {"episode_id": int(self.episodes[0].id), "model_id": str(artifact.id), "writeback": False},
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

        response = self._get("/api/v1/fhir/writeback/list/")

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

        response = self._get("/api/v1/dashboard/summary/")

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
        self.assertRegex(append.call_args.kwargs["actor"], r"^anonymous_unknown:[0-9a-f]{64}$")

    def test_authenticated_audit_actor_is_stable_pseudonymous_and_distinct(self):
        user_a = get_user_model().objects.create_user(
            username="successful-audit-user-a",
            email="successful-audit-a@example.test",
            password="test-pass",
        )
        user_b = get_user_model().objects.create_user(
            username="successful-audit-user-b",
            email="successful-audit-b@example.test",
            password="test-pass",
        )
        sensitive_token = "Bearer successful-audit-secret-token"
        sensitive_cookie = "sessionid=successful-audit-secret-cookie"

        def request_for(user):
            return SimpleNamespace(
                user=user,
                icea_api_key_authenticated=False,
                META={
                    "REMOTE_ADDR": "198.51.100.30",
                    "HTTP_USER_AGENT": "successful-audit-client",
                    "HTTP_AUTHORIZATION": sensitive_token,
                    "HTTP_COOKIE": sensitive_cookie,
                },
                headers={"Authorization": sensitive_token, "Cookie": sensitive_cookie},
            )

        append_icea_api_audit(request=request_for(user_a), event_type="actor_safety_a", context="security/test")
        append_icea_api_audit(request=request_for(user_a), event_type="actor_safety_a", context="security/test")
        append_icea_api_audit(request=request_for(user_b), event_type="actor_safety_b", context="security/test")

        actors_a = list(
            AuditEvent.objects.filter(event_type="actor_safety_a").order_by("created_at").values_list("actor", flat=True)
        )
        actor_b = AuditEvent.objects.get(event_type="actor_safety_b").actor

        self.assertEqual(len(actors_a), 2)
        self.assertEqual(actors_a[0], actors_a[1])
        self.assertNotEqual(actors_a[0], actor_b)
        self.assertRegex(actors_a[0], r"^authenticated_user:[0-9a-f]{64}$")
        self.assertRegex(actor_b, r"^authenticated_user:[0-9a-f]{64}$")
        self.assertNotEqual(actors_a[0], str(user_a.pk))
        serialized = str(list(AuditEvent.objects.filter(event_type__startswith="actor_safety_").values()))
        for sensitive in (
            user_a.username,
            user_a.email,
            user_b.username,
            user_b.email,
            sensitive_token,
            sensitive_cookie,
        ):
            self.assertNotIn(sensitive, serialized)

    def test_audit_persistence_and_list_pseudonymize_raw_legacy_actor(self):
        raw_actor = "legacy.clinician@example.test"
        sensitive_token = "Bearer legacy-secret-token"
        event_id = append_audit_event(
            event_type="legacy_actor_persistence_guard",
            payload={"Authorization": sensitive_token, "Cookie": "legacy-secret-cookie"},
            context="security/test",
            actor=raw_actor,
        )

        persisted = AuditEvent.objects.get(id=event_id)
        self.assertRegex(persisted.actor, r"^legacy_actor:[0-9a-f]{64}$")
        self.assertNotIn(raw_actor, str(persisted.__dict__))
        self.assertNotIn(sensitive_token, str(persisted.__dict__))

        direct_orm = AuditEvent.objects.create(
            event_type="direct_orm_actor_guard",
            actor=raw_actor,
            context="security/test",
        )
        self.assertRegex(direct_orm.actor, r"^legacy_actor:[0-9a-f]{64}$")

        historical_raw = AuditEvent.objects.create(
            event_type="historical_raw_actor",
            actor="system:api",
            context="security/historical",
        )
        AuditEvent.objects.filter(id=historical_raw.id).update(actor=raw_actor)
        self.client.force_authenticate(user=self._user_with_role("admin"))
        response = self._get("/api/v1/governance/audit/events/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        historical = next(event for event in body["events"] if event["id"] == str(historical_raw.id))
        self.assertRegex(historical["actor"], r"^legacy_actor:[0-9a-f]{64}$")
        self.assertNotIn(raw_actor, str(body))
        self.assertNotIn(sensitive_token, str(body))

    def test_legacy_compute_emits_requested_and_redacted_audit_events(self):
        self.client.force_authenticate(user=self._user_with_role("researcher"))
        with mock.patch("icea_core.views.append_icea_api_audit") as audit:
            response = self._post(
                "/api/v1/icea/compute/",
                {
                    "model_id": str(self.episode_artifact.id),
                    "data": [self.episodes[0].feature_row.features],
                    "nurse_cols": ["nurse_hppd"],
                },
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
        self.assertRegex(audit_call["actor"], r"^authenticated_user_fallback:[0-9a-f]{64}$")

    def test_anonymous_permission_denial_uses_safe_hash_and_unknown_fallback(self):
        anonymous = SimpleNamespace(is_authenticated=False)
        view = ICEAPlusScoreView()
        request_a = SimpleNamespace(
            user=anonymous,
            path="/api/v1/icea-plus/score/",
            method="POST",
            META={"REMOTE_ADDR": "198.51.100.20", "HTTP_USER_AGENT": "client-a"},
        )
        request_a_rotated_user_agent = SimpleNamespace(
            user=anonymous,
            path="/api/v1/icea-plus/score/",
            method="POST",
            META={"REMOTE_ADDR": "198.51.100.20", "HTTP_USER_AGENT": "attacker-rotated-user-agent"},
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
        dedupe_kind_a, dedupe_hash_a = safe_caller_audit_dedupe_identity(request_a)
        rotated_kind, rotated_hash = safe_caller_audit_dedupe_identity(request_a_rotated_user_agent)
        dedupe_kind_b, dedupe_hash_b = safe_caller_audit_dedupe_identity(request_b)
        unknown_kind, unknown_hash = _safe_caller_audit_identity(unknown)

        self.assertEqual(kind_a, "anonymous_client")
        self.assertEqual(kind_b, "anonymous_client")
        self.assertNotEqual(hash_a, hash_b)
        self.assertEqual((dedupe_kind_a, dedupe_hash_a), (rotated_kind, rotated_hash))
        self.assertEqual(dedupe_kind_b, "anonymous_client")
        self.assertNotEqual(dedupe_hash_a, dedupe_hash_b)
        self.assertNotIn("client-a", dedupe_hash_a)
        self.assertNotIn("attacker-rotated-user-agent", dedupe_hash_a)
        self.assertEqual(unknown_kind, "anonymous_unknown")
        self.assertEqual(len(unknown_hash), 64)
        self.assertNotIn("198.51.100.20", hash_a)

        with (
            mock.patch("icea_core.permissions.cache.add", wraps=cache.add) as cache_add,
            mock.patch("icea_core.api_security.append_icea_api_audit") as audit,
        ):
            _audit_permission_denial(request_a, view, error_code="auth_required")
            _audit_permission_denial(request_a_rotated_user_agent, view, error_code="auth_required")
            _audit_permission_denial(request_b, view, error_code="auth_required")
        self.assertEqual(audit.call_count, 2)
        self.assertEqual(cache_add.call_count, 3)
        self.assertEqual(cache_add.call_args_list[0].args[0], cache_add.call_args_list[1].args[0])
        self.assertNotEqual(cache_add.call_args_list[0].args[0], cache_add.call_args_list[2].args[0])
        self.assertNotIn("attacker-rotated-user-agent", str(cache_add.call_args_list))

    def test_permission_denial_remains_fail_closed(self):
        self.client.force_authenticate(user=self.regular_user)

        response = self._post(
            "/api/v1/icea-plus/calibrate/",
            {"version": "must-remain-blocked", "spec": {"weights": {"benefit": 1.0}}},
        )

        self.assertEqual(response.status_code, 403)
