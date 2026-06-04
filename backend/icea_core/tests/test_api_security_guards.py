from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
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
