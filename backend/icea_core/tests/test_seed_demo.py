from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from icea_core.evidence import (
    INTENDED_USE_SHADOW_AGGREGATE,
    REQUIRED_MODEL_LIMITATIONS,
    summarize_model_evidence,
)
from icea_core.models import ModelArtifact
from icea_pipeline.models import EpisodeFeatureRow
from icea_pipeline.temporal import CASE_MIX_REQUIRED_DOMAINS


class SeedDemoGovernanceTests(TestCase):
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

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", rows=40, name="icea-demo-governed", model_version="v-test")
        cls.artifact = ModelArtifact.objects.get(name="icea-demo-governed", version="v-test")

    def setUp(self):
        self.dev_env = mock.patch.dict(os.environ, {"ICEA_DEV_ALLOW_INSECURE": "true"}, clear=False)
        self.dev_env.start()
        self.addCleanup(self.dev_env.stop)
        self.client = APIClient()

    def test_seed_demo_creates_defensible_shadow_aggregate_evidence_pack(self):
        evidence_pack = self.artifact.metrics["evidence_pack"]
        evidence = summarize_model_evidence(self.artifact)

        self.assertTrue(evidence.defensible)
        self.assertEqual(evidence.evidence_status, "evidence_complete")
        self.assertEqual(evidence.intended_use, INTENDED_USE_SHADOW_AGGREGATE)
        self.assertTrue(evidence_pack["shadow_mode"])
        self.assertTrue(evidence_pack["non_individual_use"])
        self.assertEqual(evidence_pack["feature_names"], self.artifact.features)
        self.assertEqual(evidence_pack["feature_support_status"], "supported")
        self.assertEqual(evidence_pack["declared_features_missing_from_payload"], [])
        self.assertEqual(evidence_pack["features_without_observed_training_values"], [])
        self.assertGreater(evidence_pack["training_row_count"], 0)
        self.assertGreater(evidence_pack["validation_row_count"], 0)
        self.assertEqual(evidence_pack["dataset_fingerprint"], evidence_pack["dataset_hash"])
        self.assertTrue(REQUIRED_MODEL_LIMITATIONS.issubset(set(evidence_pack["limitations"])))

    def test_seed_demo_case_mix_is_derived_only_from_real_observed_columns(self):
        evidence_pack = self.artifact.metrics["evidence_pack"]
        case_mix_spec = evidence_pack["case_mix_spec"]
        observed = set(evidence_pack["observed_feature_columns"])

        self.assertEqual(case_mix_spec["source"], "derived_from_training_data")
        self.assertEqual(set(case_mix_spec["domains"]), CASE_MIX_REQUIRED_DOMAINS)
        for columns in case_mix_spec["domains"].values():
            self.assertTrue(set(columns).issubset(observed))

    def test_seed_demo_dataset_fingerprint_is_stable(self):
        call_command("seed_demo", rows=40, name="icea-demo-governed-repeat", model_version="v-test")
        repeated = ModelArtifact.objects.get(name="icea-demo-governed-repeat", version="v-test")

        self.assertEqual(
            repeated.metrics["evidence_pack"]["dataset_fingerprint"],
            self.artifact.metrics["evidence_pack"]["dataset_fingerprint"],
        )

    def test_seed_demo_rejects_insufficient_demo_support(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", rows=39, name="icea-demo-too-small", model_version="v-test")

        self.assertFalse(ModelArtifact.objects.filter(name="icea-demo-too-small").exists())

    def test_seed_demo_score_is_publicly_redacted_and_aggregate_has_sufficient_support(self):
        score_response = self.client.post(
            "/api/v1/icea-plus/score/",
            {"model_id": str(self.artifact.id), "grain": "episode", "from_db": True},
            format="json",
        )

        self.assertEqual(score_response.status_code, 200)
        score_body = score_response.json()
        self.assertTrue(score_body["shadow_mode"])
        self.assertTrue(score_body["non_individual_use"])
        self.assertGreater(score_body["summary"]["rows_scored"], 0)
        self.assertTrue(score_body["results"])
        for row in score_body["results"]:
            self.assertTrue(row["shadow_mode"])
            self.assertTrue(row["non_individual_use"])
            self.assertIsNone(row["score"])
            self.assertIsNone(row["raw_score"])

        aggregate_response = self.client.get(
            "/api/v1/icea-plus/aggregate/",
            {"model_id": str(self.artifact.id), "grain": "episode", "group_by": "unit"},
        )

        self.assertEqual(aggregate_response.status_code, 200)
        aggregate_body = aggregate_response.json()
        self.assertTrue(aggregate_body["shadow_mode"])
        self.assertTrue(aggregate_body["non_individual_use"])
        self.assertTrue(aggregate_body["results"])
        self.assertFalse(aggregate_body["results"][0]["suppressed"])
        self.assertGreaterEqual(aggregate_body["results"][0]["support"]["n_episodes"], 10)

    def test_seed_demo_legacy_compute_remains_redacted(self):
        feature_row = EpisodeFeatureRow.objects.order_by("episode_id").first()
        response = self.client.post(
            "/api/v1/icea/compute/",
            {
                "model_id": str(self.artifact.id),
                "data": [feature_row.features],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "shadow_only")
        self.assertEqual(body["results"], {})
        self.assertIsNone(body["score_summary"])
        self.assertTrue(body["score_summary_redacted"])
        self.assertTrue(body["shadow_mode"])
        self.assertTrue(body["non_individual_use"])

    def test_demo_artifact_without_evidence_pack_remains_blocked(self):
        artifact = ModelArtifact.objects.create(
            name="icea-demo-ungoverned",
            version="v-test",
            target=self.artifact.target,
            features=self.artifact.features,
            model_type=self.artifact.model_type,
            model_path=self.artifact.model_path,
            metrics={},
        )

        evidence = summarize_model_evidence(artifact)
        self.assertFalse(evidence.defensible)

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {"model_id": str(artifact.id), "grain": "episode", "from_db": True},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "model_not_defensible")
        self.assertNotIn("results", response.json())
