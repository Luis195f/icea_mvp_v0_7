from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from icea_core.evidence import INTENDED_USE_SHADOW_AGGREGATE, summarize_model_evidence
from icea_core.models import ModelArtifact
from icea_core.tests.helpers import ICEAPlusFixtureMixin


def complete_evidence_pack(**overrides):
    pack = {
        "dataset_fingerprint": "sha256:test-dataset",
        "dataset_hash": "sha256:test-dataset",
        "training_row_count": 80,
        "validation_row_count": 20,
        "feature_names": ["ri_initial", "nurse_hppd"],
        "temporal_spec_version": "icea_temporal_v1",
        "temporal_guardrail_status": "passed",
        "outcome_definition": "delta_ri",
        "outcome_window": {"horizon_hours": 12, "source": "fixed_future_horizon"},
        "case_mix_spec": {"baseline_adjustment_domains": ["ri_initial"]},
        "intended_use": INTENDED_USE_SHADOW_AGGREGATE,
        "non_individual_use": True,
        "shadow_mode": True,
        "calibration_summary": {"method": "split_conformal", "calibration_size": 20},
        "validation_metrics": {"rmse": 1.2, "mae": 0.9},
        "limitations": ["shadow_aggregate_research_only", "not_clinically_validated", "not_for_individual_decisioning"],
        "source_commit_unavailable_reason": "not_captured_in_test",
    }
    pack.update(overrides)
    return pack


def create_artifact(*, evidence_pack=None, metrics=None):
    final_metrics = dict(metrics or {})
    if evidence_pack is not None:
        final_metrics["evidence_pack"] = evidence_pack
    return ModelArtifact.objects.create(
        name="evidence-test",
        version="v-test",
        target="delta_ri",
        features=["ri_initial", "nurse_hppd"],
        model_type="xgboost",
        model_path="missing-model.json",
        metrics=final_metrics,
    )


class ModelEvidenceUnitTests(TestCase):
    def test_model_without_dataset_hash_is_not_defensible(self):
        pack = complete_evidence_pack(dataset_fingerprint=None, dataset_hash=None)
        artifact = create_artifact(evidence_pack=pack)

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertEqual(evidence.evidence_status, "evidence_incomplete")
        self.assertIn("dataset_fingerprint", evidence.missing_evidence)
        self.assertIn("model_not_defensible", evidence.statuses)

    def test_model_without_temporal_spec_version_is_not_defensible(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack(temporal_spec_version=None))

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertIn("temporal_spec_version", evidence.missing_evidence)
        self.assertIn("model_not_defensible", evidence.statuses)

    def test_model_without_case_mix_spec_is_case_mix_insufficient(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(
                case_mix_spec=None,
                case_mix_unavailable_reason="case_mix_not_declared",
            )
        )

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertEqual(evidence.case_mix_status, "case_mix_insufficient")
        self.assertIn("case_mix_insufficient", evidence.statuses)

    def test_model_without_calibration_summary_returns_unavailable_status(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(
                calibration_summary=None,
                calibration_unavailable_reason="insufficient_calibration_sample",
            )
        )

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertEqual(evidence.calibration_status, "calibration_unavailable")
        self.assertIn("calibration_unavailable", evidence.statuses)


class ModelEvidenceAPITests(ICEAPlusFixtureMixin, TestCase):
    def setUp(self):
        self.dev_env = mock.patch.dict(os.environ, {"ICEA_DEV_ALLOW_INSECURE": "true"}, clear=False)
        self.dev_env.start()
        self.addCleanup(self.dev_env.stop)
        self.client = APIClient()

    def test_score_with_incomplete_evidence_does_not_return_defensible_score(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack(dataset_hash=None, dataset_fingerprint=None))

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {"model_id": str(artifact.id), "grain": "episode", "from_db": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "model_not_defensible")
        self.assertFalse(body["defensible"])
        self.assertNotIn("results", body)
        self.assertIn("dataset_fingerprint", body["missing_evidence"])

    def test_complete_evidence_model_passes_only_as_shadow_aggregate_research(self):
        response = self.client.get("/api/v1/models/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        fixture_model = next(item for item in body if item["id"] == str(self.episode_artifact.id))
        self.assertTrue(fixture_model["defensible"])
        self.assertEqual(fixture_model["evidence_status"], "evidence_complete")
        self.assertEqual(fixture_model["intended_use"], INTENDED_USE_SHADOW_AGGREGATE)
        self.assertNotEqual(fixture_model["intended_use"], "production")

    def test_models_endpoint_does_not_declare_incomplete_model_ready_or_validated(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack(temporal_spec_version=None))

        response = self.client.get("/api/v1/models/")

        self.assertEqual(response.status_code, 200)
        item = next(row for row in response.json() if row["id"] == str(artifact.id))
        self.assertFalse(item["defensible"])
        self.assertEqual(item["evidence_status"], "evidence_incomplete")
        self.assertNotIn("ready", item)
        self.assertNotIn("validated", item)

    def test_training_saves_evidence_metadata_or_unavailable_reasons(self):
        with mock.patch("icea_pipeline.views.train_xgb_regressor") as train_mock:
            train_mock.return_value = SimpleNamespace(
                model_path="mock-window-model.json",
                features=["ri_initial", "window_index"],
                target="delta_ri",
                metrics={"rmse": 0.0, "mae": 0.0, "n_rows": 12, "n_features": 2},
            )
            response = self.client.post("/api/v1/pipeline/train/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        evidence = response.json()["metrics"]["evidence_pack"]
        self.assertTrue(evidence["dataset_fingerprint"])
        self.assertEqual(evidence["temporal_guardrail_status"], "passed")
        self.assertEqual(evidence["intended_use"], INTENDED_USE_SHADOW_AGGREGATE)
        self.assertTrue(evidence["non_individual_use"])
        self.assertTrue(evidence["shadow_mode"])
        self.assertEqual(evidence["case_mix_unavailable_reason"], "case_mix_spec_not_declared_for_training_dataset")
        self.assertEqual(evidence["calibration_unavailable_reason"], "insufficient_calibration_sample_or_not_computed")

    def test_shadow_scoring_does_not_reintroduce_individual_score(self):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": True,
                "episode_ids": [int(self.episodes[0].id)],
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
        self.assertTrue(body["shadow_mode"])
        self.assertTrue(body["non_individual_use"])
        self.assertEqual(body["intended_use"], INTENDED_USE_SHADOW_AGGREGATE)
        row = body["results"][0]
        self.assertTrue(row["shadow_mode"])
        self.assertTrue(row["non_individual_use"])
        self.assertEqual(row["intended_use"], INTENDED_USE_SHADOW_AGGREGATE)
        self.assertTrue(row["flags"]["shadow_mode"])
        self.assertTrue(row["flags"]["non_individual_use"])
        self.assertEqual(row["status"], "shadow_only")
        self.assertIsNone(row["score"])
        self.assertIsNone(row["raw_score"])
