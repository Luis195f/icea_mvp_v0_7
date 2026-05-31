from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from icea_core.models import ICEAPlusFollowupRecord
from icea_core.tests.helpers import ICEAPlusFixtureMixin
from icea_pipeline.models import FHIRWritebackRecord, NormalizedObservation, NormalizedProcedure


CONTRACT_FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "icea" / "handover_icea_feature_contract_v1.json"


def load_contract_fixture():
    with CONTRACT_FIXTURE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class ICEAPlusAPITests(ICEAPlusFixtureMixin, TestCase):
    def setUp(self):
        self.dev_env = mock.patch.dict(os.environ, {"ICEA_DEV_ALLOW_INSECURE": "true"}, clear=False)
        self.dev_env.start()
        self.addCleanup(self.dev_env.stop)
        self.client = APIClient()

    def _score_initial_episode(self, episode):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": True,
                "episode_ids": [int(episode.id)],
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
        return response.json()

    def _add_followup_evidence(self, episode):
        feature_row = episode.feature_row
        features = dict(feature_row.features)
        target = dict(feature_row.target)
        features["nurse_hppd"] = float(features.get("nurse_hppd", 0.0)) + 0.8
        features["nurse_proc_count_det"] = float(features.get("nurse_proc_count_det", 0.0)) + 1.0
        target["delta_ri"] = float(target.get("delta_ri", 0.0)) + 1.5
        feature_row.features = features
        feature_row.target = target
        feature_row.save(update_fields=["features", "target"])

        followup_dt = timezone.now() + timezone.timedelta(minutes=5)
        NormalizedObservation.objects.create(
            episode=episode,
            code_system="LOINC",
            code="8867-4",
            display="Heart rate",
            value_num=88.0,
            effective_dt=followup_dt,
        )
        NormalizedProcedure.objects.create(
            episode=episode,
            code_system="SNOMED",
            code=f"FOLLOW-{episode.id}",
            display="Follow-up nursing procedure",
            performer_role="registered nurse",
            performer_actor_ref="Practitioner/nurse-followup",
            performer_actor_type="Practitioner",
            is_nursing=True,
            nursing_label_method="deterministic",
            performed_dt=followup_dt,
        )

    def _native_feature_row(self):
        row = self.episode_train_df.drop(columns=["delta_ri"]).iloc[0].to_dict()
        return {key: float(value) for key, value in row.items()}

    def _contract_row(
        self,
        *,
        features,
        missingness_flags=None,
        source_grain="episode",
        row_id="episode:handover-fixture-001",
        contract_version="handover-icea-feature-v1",
        source_repo="Luis195f/HANDOVER",
    ):
        return {
            "contract_version": contract_version,
            "source_repo": source_repo,
            "source_grain": source_grain,
            "row_id": row_id,
            "episode_id": "handover-fixture-001",
            "unit_id": "icu-a",
            "clinical_timestamp": "2026-03-08T15:00:00Z",
            "recorded_timestamp": "2026-03-08T15:03:00Z",
            "features": features,
            "missingness_flags": missingness_flags or {key: False for key in features},
            "warnings": [],
            "shadow_mode": True,
            "non_individual_use": True,
        }

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

    def test_score_endpoint_returns_contract_mismatch_for_handover_feature_space(self):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "window",
                "from_db": False,
                "rows": [load_contract_fixture()],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(body["results"][0]["status"], "contract_mismatch")
        self.assertIsNone(body["results"][0]["score"])
        self.assertIn("model_feature_space_mismatch", body["results"][0]["warnings"])

    def test_score_endpoint_does_not_zero_fill_missing_model_features(self):
        features = self._native_feature_row()
        features.pop("nurse_hppd")

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [{"row_id": "episode:missing-nurse-hppd", **features}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(body["results"][0]["status"], "contract_mismatch")
        self.assertIsNone(body["results"][0]["score"])

    def test_score_endpoint_rejects_flat_external_row_without_contract_metadata(self):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [{"row_id": "episode:flat-legacy", **self._native_feature_row()}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        result = body["results"][0]
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(result["row_id"], "episode:flat-legacy")
        self.assertEqual(result["status"], "contract_mismatch")
        self.assertIsNone(result["score"])
        self.assertIn("contract_version_mismatch", result["warnings"])
        self.assertIn("source_repo_mismatch", result["warnings"])
        self.assertIn("grain_mismatch", result["warnings"])
        self.assertIn("temporal_context_missing", result["warnings"])
        self.assertIn("governance_flags_missing", result["warnings"])

    def test_score_endpoint_accepts_flat_external_row_with_contract_metadata(self):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [
                    {
                        "contract_version": "handover-icea-feature-v1",
                        "source_repo": "Luis195f/HANDOVER",
                        "source_grain": "episode",
                        "row_id": "episode:flat-contract",
                        "episode_id": "flat-contract",
                        "unit_id": "icu-a",
                        "clinical_timestamp": "2026-03-08T15:00:00Z",
                        "recorded_timestamp": "2026-03-08T15:03:00Z",
                        "shadow_mode": True,
                        "non_individual_use": True,
                        **self._native_feature_row(),
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        result = body["results"][0]
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertNotEqual(result["status"], "contract_mismatch")
        self.assertNotIn("feature_contract", result)
        self.assertNotIn("contract_version_mismatch", result["warnings"])
        self.assertNotIn("temporal_context_missing", result["warnings"])
        self.assertNotIn("governance_flags_missing", result["warnings"])

    def test_score_endpoint_from_db_true_is_not_subject_to_external_contract_metadata(self):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreater(body["summary"]["rows_requested"], 0)
        self.assertTrue(body["results"])
        self.assertNotIn("feature_contract", body["results"][0])

    def test_score_endpoint_no_score_if_critical_feature_is_missing(self):
        features = self._native_feature_row()
        features["nurse_hppd"] = None
        missingness = {key: False for key in features}
        missingness["nurse_hppd"] = True

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features=features, missingness_flags=missingness)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(body["results"][0]["status"], "insufficient_evidence")
        self.assertIn("nurse_hppd", body["results"][0]["feature_contract"]["missing_critical_features"])
        self.assertIsNone(body["results"][0]["score"])

    def test_score_endpoint_missingness_string_false_and_zero_do_not_mark_feature_missing(self):
        features = self._native_feature_row()
        missingness = {key: False for key in features}
        missingness["nurse_hppd"] = "false"
        missingness["ri_initial"] = "0"

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features=features, missingness_flags=missingness)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertNotIn("feature_contract", body["results"][0])

    def test_score_endpoint_missingness_string_true_and_one_mark_feature_missing(self):
        features = self._native_feature_row()
        missingness = {key: False for key in features}
        missingness["nurse_hppd"] = "true"
        missingness["ri_initial"] = "1"

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features=features, missingness_flags=missingness)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        result = body["results"][0]
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIn("nurse_hppd", result["feature_contract"]["missing_critical_features"])
        self.assertIn("ri_initial", result["feature_contract"]["missing_critical_features"])
        self.assertIsNone(result["score"])

    def test_score_endpoint_blocks_low_feature_coverage(self):
        artifact = self.episode_artifact
        artifact.metrics = {
            **(artifact.metrics or {}),
            "feature_contract": {
                "contract_version": "handover-icea-feature-v1",
                "source_repo": "Luis195f/HANDOVER",
                "required_features": ["ri_initial", "proc_count"],
                "min_feature_coverage": 0.95,
            },
        }
        artifact.save(update_fields=["metrics"])

        features = {key: self._native_feature_row()[key] for key in ("ri_initial", "proc_count")}
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features=features)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(body["results"][0]["status"], "low_feature_coverage")
        self.assertTrue(body["results"][0]["flags"]["low_feature_coverage"])

    def test_score_endpoint_respects_explicit_zero_min_feature_coverage(self):
        artifact = self.episode_artifact
        artifact.metrics = {
            **(artifact.metrics or {}),
            "feature_contract": {
                "contract_version": "handover-icea-feature-v1",
                "source_repo": "Luis195f/HANDOVER",
                "required_features": ["nurse_hppd"],
                "min_feature_coverage": 0.0,
            },
        }
        artifact.save(update_fields=["metrics"])

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features={"ri_initial": self._native_feature_row()["ri_initial"]})],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        result = body["results"][0]
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(result["status"], "contract_mismatch")
        self.assertFalse(result["flags"]["low_feature_coverage"])
        self.assertTrue(result["flags"]["insufficient_evidence"])
        self.assertNotIn("low_feature_coverage", result["warnings"])
        self.assertIn("nurse_hppd", result["feature_contract"]["missing_critical_features"])
        self.assertIsNone(result["score"])

    def test_score_endpoint_invalid_min_feature_coverage_returns_contract_failure(self):
        artifact = self.episode_artifact
        artifact.metrics = {
            **(artifact.metrics or {}),
            "feature_contract": {
                "contract_version": "handover-icea-feature-v1",
                "source_repo": "Luis195f/HANDOVER",
                "min_feature_coverage": "not-a-number",
            },
        }
        artifact.save(update_fields=["metrics"])

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features=self._native_feature_row())],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        result = body["results"][0]
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(result["status"], "contract_mismatch")
        self.assertIn("invalid_min_feature_coverage", result["warnings"])
        self.assertIsNone(result["score"])

    def test_score_endpoint_deduplicates_primary_and_baseline_contract_failures_by_row(self):
        features = {key: self._native_feature_row()[key] for key in ("ri_initial", "proc_count")}
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "baseline_model_id": str(self.window_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features=features)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(body["summary"]["status_counts"]["contract_mismatch"], 1)
        self.assertEqual(body["summary"]["status_counts"]["low_feature_coverage"], 0)
        self.assertEqual(body["results"][0]["row_id"], "episode:handover-fixture-001")
        self.assertEqual(body["results"][0]["status"], "contract_mismatch")
        self.assertIsNone(body["results"][0]["score"])
        self.assertEqual(
            body["results"][0]["feature_contract"]["validated_model_roles"],
            ["primary", "baseline"],
        )

    def test_score_endpoint_baseline_without_model_path_does_not_block_external_contract(self):
        baseline = self.window_artifact
        baseline.model_path = ""
        baseline.save(update_fields=["model_path"])

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "baseline_model_id": str(baseline.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features=self._native_feature_row())],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["baseline_mode"], "counterfactual_nursing_reference")
        self.assertEqual(body["summary"]["status_counts"].get("contract_mismatch", 0), 0)
        self.assertNotEqual(body["results"][0]["status"], "contract_mismatch")
        self.assertNotIn("feature_contract", body["results"][0])

    def test_score_endpoint_baseline_with_model_path_validates_external_contract(self):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "baseline_model_id": str(self.window_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features=self._native_feature_row())],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(body["summary"]["status_counts"]["contract_mismatch"], 1)
        self.assertEqual(body["results"][0]["status"], "contract_mismatch")
        self.assertIsNone(body["results"][0]["score"])
        self.assertEqual(body["results"][0]["feature_contract"]["validated_model_roles"], ["baseline"])

    def test_score_endpoint_reports_model_specific_expected_contract_identifiers(self):
        artifact = self.episode_artifact
        artifact.metrics = {
            **(artifact.metrics or {}),
            "feature_contract": {
                "contract_version": "handover-icea-feature-v2-custom",
                "source_repo": "Luis195f/custom-handover",
            },
        }
        artifact.save(update_fields=["metrics"])

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(features=self._native_feature_row())],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        feature_contract = body["results"][0]["feature_contract"]
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(body["summary"]["status_counts"]["contract_mismatch"], 1)
        self.assertEqual(body["results"][0]["status"], "contract_mismatch")
        self.assertIsNone(body["results"][0]["score"])
        self.assertEqual(feature_contract["expected_contract_version"], "handover-icea-feature-v2-custom")
        self.assertEqual(feature_contract["expected_source_repo"], "Luis195f/custom-handover")
        self.assertEqual(feature_contract["contract_version"], "handover-icea-feature-v2-custom")
        self.assertEqual(feature_contract["source_repo"], "Luis195f/custom-handover")

    def test_score_endpoint_preserves_numeric_zero_row_id_in_contract_failure(self):
        features = self._native_feature_row()
        features.pop("nurse_hppd")

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [self._contract_row(row_id=0, features=features)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(body["results"][0]["row_id"], "0")
        self.assertEqual(body["results"][0]["status"], "contract_mismatch")
        self.assertIsNone(body["results"][0]["score"])

    def test_score_endpoint_numeric_zero_row_id_is_not_replaced_with_fallback(self):
        valid_row = self._contract_row(row_id=0, features=self._native_feature_row())
        invalid_row = self._contract_row(
            row_id="episode:invalid-row",
            features={"ri_initial": self._native_feature_row()["ri_initial"]},
        )

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [valid_row, invalid_row],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        by_row = {row["row_id"]: row for row in body["results"]}
        self.assertIn("0", by_row)
        self.assertNotIn("row:0", by_row)
        self.assertEqual(by_row["0"]["status"], "contract_mismatch")
        self.assertIn("scoring_blocked_by_batch_contract_failure", by_row["0"]["warnings"])

    def test_score_endpoint_mixed_batch_preserves_valid_row_trace_when_another_row_fails(self):
        valid_row = self._contract_row(
            row_id="episode:valid-row",
            features=self._native_feature_row(),
        )
        invalid_row = self._contract_row(
            row_id="episode:invalid-row",
            features={"ri_initial": self._native_feature_row()["ri_initial"]},
        )

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [valid_row, invalid_row],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_requested"], 2)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(len(body["results"]), 2)
        by_row = {row["row_id"]: row for row in body["results"]}
        self.assertEqual(set(by_row), {"episode:valid-row", "episode:invalid-row"})
        self.assertEqual(by_row["episode:invalid-row"]["status"], "contract_mismatch")
        self.assertEqual(by_row["episode:valid-row"]["status"], "contract_mismatch")
        self.assertIn("scoring_blocked_by_batch_contract_failure", by_row["episode:valid-row"]["warnings"])
        self.assertIsNone(by_row["episode:valid-row"]["score"])
        self.assertIsNone(by_row["episode:invalid-row"]["score"])
        self.assertFalse(by_row["episode:valid-row"]["flags"]["insufficient_evidence"])
        self.assertEqual(body["summary"]["status_counts"]["contract_mismatch"], 2)

    def test_score_endpoint_reference_rows_contract_mismatch_blocks_numeric_scoring(self):
        valid_row = self._contract_row(features=self._native_feature_row())
        bad_reference = self._contract_row(
            row_id="episode:bad-reference",
            features=self._native_feature_row(),
            contract_version="handover-icea-feature-v9-bad",
        )

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [valid_row],
                "reference_rows": [bad_reference],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(len(body["results"]), 1)
        result = body["results"][0]
        self.assertEqual(result["row_id"], "episode:handover-fixture-001")
        self.assertEqual(result["status"], "blocked_by_reference_contract")
        self.assertIsNone(result["score"])
        self.assertIsNone(result["raw_score"])
        self.assertTrue(result["flags"]["blocked_by_reference_contract"])
        self.assertTrue(result["flags"]["contract_mismatch"])
        self.assertFalse(result["flags"]["insufficient_evidence"])
        self.assertIn("scoring_blocked_by_reference_rows_contract_failure", result["warnings"])
        self.assertEqual(body["summary"]["status_counts"]["blocked_by_reference_contract"], 1)

    def test_score_endpoint_reference_rows_low_feature_coverage_blocks_numeric_scoring(self):
        artifact = self.episode_artifact
        artifact.metrics = {
            **(artifact.metrics or {}),
            "feature_contract": {
                "contract_version": "handover-icea-feature-v1",
                "source_repo": "Luis195f/HANDOVER",
                "required_features": ["ri_initial"],
                "min_feature_coverage": 0.95,
            },
        }
        artifact.save(update_fields=["metrics"])
        valid_row = self._contract_row(features=self._native_feature_row())
        low_coverage_reference = self._contract_row(
            row_id="episode:low-coverage-reference",
            features={"ri_initial": self._native_feature_row()["ri_initial"]},
        )

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(artifact.id),
                "grain": "episode",
                "from_db": False,
                "rows": [valid_row],
                "reference_rows": [low_coverage_reference],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["rows_requested"], 1)
        self.assertEqual(body["summary"]["rows_scored"], 0)
        self.assertEqual(len(body["results"]), 1)
        result = body["results"][0]
        self.assertEqual(result["status"], "blocked_by_reference_contract")
        self.assertIsNone(result["score"])
        self.assertIsNone(result["raw_score"])
        self.assertTrue(result["flags"]["blocked_by_reference_contract"])
        self.assertTrue(result["flags"]["low_feature_coverage"])
        self.assertFalse(result["flags"]["insufficient_evidence"])
        self.assertIn("low_feature_coverage", result["warnings"])
        self.assertEqual(body["summary"]["status_counts"]["blocked_by_reference_contract"], 1)

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
        unit_body = response_unit.json()
        self.assertTrue(unit_body["results"])
        self.assertTrue(unit_body["non_individual_use"])
        self.assertTrue(unit_body["shadow_mode"])
        self.assertIn("governance", unit_body)

        response_shift = self.client.get(
            "/api/v1/icea-plus/aggregate/",
            {"model_id": str(self.window_artifact.id), "group_by": "shift", "grain": "window"},
        )
        self.assertEqual(response_shift.status_code, 200)
        shift_body = response_shift.json()
        self.assertTrue(shift_body["results"])
        self.assertIn("shift_aggregation_deidentified_to_unit_date_bucket", shift_body["warnings"])

    def test_aggregate_endpoint_blocks_individualizable_grouping_and_suppresses_low_support(self):
        response = self.client.get(
            "/api/v1/icea-plus/aggregate/",
            {
                "model_id": str(self.episode_artifact.id),
                "group_by": "patient",
                "grain": "episode",
                "date_from": self.episodes[0].admission_date.isoformat(),
                "date_to": (self.episodes[0].admission_date + timezone.timedelta(seconds=1)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["effective_group_by"], "unit")
        self.assertIn("patient_grouping_individualizable_falling_back_to_unit", body["warnings"])
        self.assertEqual(body["results"][0]["status"], "suppressed_low_support")
        self.assertIsNone(body["results"][0]["score"])
        self.assertTrue(body["summary"]["summary_redacted"])
        self.assertEqual(body["summary"]["redaction_reason"], "suppressed_low_support")
        self.assertEqual(body["summary"]["suppressed_cells"], body["suppressed_cells"])
        self.assertNotIn("component_means", body["summary"])

    def test_aggregate_endpoint_redacts_numeric_summary_when_any_cell_is_suppressed(self):
        response = self.client.get(
            "/api/v1/icea-plus/aggregate/",
            {
                "model_id": str(self.episode_artifact.id),
                "group_by": "unit",
                "grain": "episode",
                "date_from": self.episodes[0].admission_date.isoformat(),
                "date_to": (self.episodes[0].admission_date + timezone.timedelta(seconds=1)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreater(body["suppressed_cells"], 0)
        self.assertEqual(body["results"][0]["status"], "suppressed_low_support")
        self.assertIsNone(body["results"][0]["score"])
        self.assertEqual(
            body["summary"],
            {
                "summary_redacted": True,
                "redaction_reason": "suppressed_low_support",
                "suppressed_cells": body["suppressed_cells"],
                "min_cell_count": 10,
                "min_staff_count": 5,
                "aggregation_level": "unit",
            },
        )
        forbidden_terms = {"component_means", "benefit", "attribution", "causal", "quality", "uncertainty"}
        self.assertTrue(forbidden_terms.isdisjoint(set(body["summary"].keys())))

    def test_aggregate_endpoint_preserves_numeric_summary_when_no_cells_are_suppressed(self):
        response = self.client.get(
            "/api/v1/icea-plus/aggregate/",
            {"model_id": str(self.episode_artifact.id), "group_by": "unit", "grain": "episode"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["suppressed_cells"], 0)
        self.assertFalse(body["summary"].get("summary_redacted", False))
        self.assertIn("component_means", body["summary"])
        self.assertIn("benefit", body["summary"]["component_means"])

    def test_calibrate_endpoint_is_admin_only(self):
        with mock.patch.dict(
            os.environ,
            {
                "ICEA_DEV_ALLOW_INSECURE": "false",
                "ICEA_AUTH_REQUIRED": "true",
                "ICEA_RBAC_ENFORCE": "true",
            },
            clear=False,
        ):
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

    def test_followup_without_new_evidence_does_not_create_enriched_score(self):
        episode = self.episodes[0]
        self._score_initial_episode(episode)

        response = self.client.post(
            "/api/v1/icea-plus/followup/rescore/",
            {
                "episode_id": int(episode.id),
                "model_id": str(self.episode_artifact.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn(body["score_states"]["followup"], {"insufficient_evidence", "pending_followup"})
        self.assertIsNone(body["enriched_score"])

    def test_followup_ingest_and_rescore_generate_traced_enriched_score(self):
        episode = self.episodes[1]
        initial = self._score_initial_episode(episode)
        initial_row = initial["results"][0]
        self._add_followup_evidence(episode)

        ingest = self.client.post(
            "/api/v1/icea-plus/followup/ingest/",
            {
                "episode_id": int(episode.id),
                "model_id": str(self.episode_artifact.id),
            },
            format="json",
        )
        self.assertEqual(ingest.status_code, 200)
        self.assertEqual(ingest.json()["score_states"]["followup"], "stale")

        rescore = self.client.post(
            "/api/v1/icea-plus/followup/rescore/",
            {
                "episode_id": int(episode.id),
                "model_id": str(self.episode_artifact.id),
            },
            format="json",
        )
        self.assertEqual(rescore.status_code, 200)
        body = rescore.json()
        self.assertEqual(body["score_states"]["current"], "enriched_followup")
        self.assertEqual(body["score_states"]["followup"], "enriched_followup")
        self.assertIsNotNone(body["enriched_score"])
        self.assertEqual(body["initial_score"]["lineage"]["formula_version"], body["enriched_score"]["lineage"]["formula_version"])
        self.assertEqual(
            body["initial_score"]["lineage"]["formula_protocol_hash"],
            body["enriched_score"]["lineage"]["formula_protocol_hash"],
        )
        self.assertNotEqual(body["comparison"]["initial_computation_id"], body["comparison"]["enriched_computation_id"])
        self.assertIsNone(body["initial_score"]["score"])
        self.assertEqual(body["initial_score"]["status"], "shadow_only")
        self.assertTrue(body["initial_score"]["score_suppressed"])
        self.assertEqual(body["initial_score"]["source_status"], initial_row["status"])

        record = ICEAPlusFollowupRecord.objects.get(episode=episode, model=self.episode_artifact)
        self.assertIsNotNone(record.initial_computation)
        self.assertIsNotNone(record.enriched_computation)
        self.assertEqual(record.current_state, "enriched_followup")

    def test_followup_status_and_patient_writeback_contract(self):
        episode = self.episodes[2]
        self._score_initial_episode(episode)
        self._add_followup_evidence(episode)
        self.client.post(
            "/api/v1/icea-plus/followup/rescore/",
            {
                "episode_id": int(episode.id),
                "model_id": str(self.episode_artifact.id),
            },
            format="json",
        )

        status_response = self.client.get(
            "/api/v1/icea-plus/followup/status/",
            {
                "episode_id": int(episode.id),
                "model_id": str(self.episode_artifact.id),
            },
        )
        self.assertEqual(status_response.status_code, 200)
        status_body = status_response.json()
        self.assertIn("support", status_body)
        self.assertIn("provenance", status_body)
        self.assertTrue(status_body["non_individual_use"])
        self.assertTrue(status_body["shadow_mode"])
        self.assertTrue(status_body["exploratory_only"])

        patient_response = self.client.get(
            "/api/v1/icea-plus/writeback/patient/",
            {
                "episode_id": int(episode.id),
                "model_id": str(self.episode_artifact.id),
            },
        )
        self.assertEqual(patient_response.status_code, 200)
        patient_body = patient_response.json()
        self.assertEqual(patient_body["score_states"]["current"], "enriched_followup")
        self.assertIn("timestamps", patient_body)
        self.assertIn("evidence", patient_body)
        self.assertIsNone(patient_body["current_score"]["score"])
        self.assertTrue(patient_body["current_score"]["score_suppressed"])
        self.assertFalse(patient_body["operational_score"])

    def test_writeback_summary_is_stable_and_degrades_team_to_unit(self):
        episode = self.episodes[3]
        self._score_initial_episode(episode)
        self._add_followup_evidence(episode)
        self.client.post(
            "/api/v1/icea-plus/followup/rescore/",
            {
                "episode_id": int(episode.id),
                "model_id": str(self.episode_artifact.id),
            },
            format="json",
        )

        response = self.client.get(
            "/api/v1/icea-plus/writeback/summary/",
            {
                "model_id": str(self.episode_artifact.id),
                "group_by": "team",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["effective_group_by"], "unit")
        self.assertIn("team_writeback_not_explicitly_modeled_falling_back_to_unit", body["warnings"])
        self.assertIn("status_counts", body)
        self.assertTrue(body["non_individual_use"])
        self.assertIn("governance", body)
        self.assertIn("results", body)
        for row in body["results"]:
            if row["status"] == "suppressed_low_support":
                self.assertIsNone(row["score"])

    def test_writeback_list_suppresses_episode_identifier_for_export_surface(self):
        FHIRWritebackRecord.objects.create(
            episode=self.episodes[0],
            model_id=self.episode_artifact.id,
            payload={"resourceType": "RiskAssessment"},
        )
        response = self.client.get("/api/v1/fhir/writeback/list/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body)
        self.assertIsNone(body[0]["episode_id"])
        self.assertTrue(body[0]["identifier_suppressed"])

    def test_followup_endpoint_requires_auth_when_flag_enabled(self):
        episode = self.episodes[4]
        with mock.patch.dict(os.environ, {"ICEA_AUTH_REQUIRED": "true"}, clear=False):
            response = self.client.post(
                "/api/v1/icea-plus/followup/ingest/",
                {
                    "episode_id": int(episode.id),
                    "model_id": str(self.episode_artifact.id),
                },
                format="json",
            )
        self.assertIn(response.status_code, {401, 403})

    def test_followup_ingest_returns_typed_error_for_missing_model(self):
        episode = self.episodes[0]
        response = self.client.post(
            "/api/v1/icea-plus/followup/ingest/",
            {
                "episode_id": int(episode.id),
                "model_id": str(uuid.uuid4()),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "model_not_found")

    def test_followup_ingest_returns_typed_error_for_missing_episode(self):
        response = self.client.post(
            "/api/v1/icea-plus/followup/ingest/",
            {
                "episode_id": 999999,
                "model_id": str(self.episode_artifact.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "episode_not_found")

    def test_followup_post_invalid_body_returns_stable_json(self):
        response = self.client.post(
            "/api/v1/icea-plus/followup/ingest/",
            {
                "model_id": str(self.episode_artifact.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "invalid_request")
        self.assertEqual(body["request_type"], "body")
        self.assertIn("errors", body)
        self.assertNotIn("traceback", body)

    def test_followup_get_invalid_query_returns_stable_json(self):
        response = self.client.get(
            "/api/v1/icea-plus/writeback/summary/",
            {
                "model_id": "not-a-uuid",
            },
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "invalid_request")
        self.assertEqual(body["request_type"], "query")
        self.assertIn("errors", body)
        self.assertNotIn("traceback", body)

    def test_followup_status_returns_typed_error_for_missing_record(self):
        episode = self.episodes[5]
        response = self.client.get(
            "/api/v1/icea-plus/followup/status/",
            {
                "episode_id": int(episode.id),
                "model_id": str(self.episode_artifact.id),
            },
        )
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["detail"], "followup_record_not_found")
        self.assertEqual(body["episode_id"], int(episode.id))
