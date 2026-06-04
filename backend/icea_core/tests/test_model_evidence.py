from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest import mock

import pandas as pd
from django.test import TestCase
from rest_framework.test import APIClient

from icea_core.evidence import INTENDED_USE_SHADOW_AGGREGATE, build_training_evidence_metadata, summarize_model_evidence
from icea_core.followup import (
    INTERNAL_AGGREGATE_ROW_KEY,
    FollowupEvaluation,
    build_patient_summary,
    build_summary_writeback,
    persist_initial_followup_records,
)
from icea_core.models import ICEAPlusComputation, ICEAPlusFollowupRecord, ModelArtifact
from icea_core.scoring import redact_shadow_score_response
from icea_core.tests.helpers import ICEAPlusFixtureMixin
from icea_pipeline.models import EpisodeWindowFeatureRow
from icea_pipeline.temporal import CASE_MIX_REQUIRED_DOMAINS


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
        "case_mix_spec": {
            "source": "test_declared",
            "domains": {
                "age": ["age_years"],
                "severity": ["ri_initial"],
                "comorbidity": ["charlson_index"],
                "fragility_or_dependency": ["frailty_score"],
                "baseline_risk": ["ri_initial"],
                "baseline_load": ["nurse_hppd"],
            },
            "variables": ["age_years", "ri_initial", "charlson_index", "frailty_score", "nurse_hppd"],
        },
        "intended_use": INTENDED_USE_SHADOW_AGGREGATE,
        "non_individual_use": True,
        "shadow_mode": True,
        "calibration_summary": {"method": "split_conformal", "calibration_size": 20},
        "validation_metrics": {"rmse": 1.2, "mae": 0.9},
        "limitations": [
            "shadow_aggregate_research_only",
            "not_clinically_validated",
            "not_for_individual_decisioning",
            "not_mdr_production_ready",
        ],
        "source_commit_unavailable_reason": "not_captured_in_test",
    }
    pack.update(overrides)
    return pack


def create_artifact(*, evidence_pack=None, metrics=None, features=None):
    final_metrics = dict(metrics or {})
    if evidence_pack is not None:
        final_metrics["evidence_pack"] = evidence_pack
    return ModelArtifact.objects.create(
        name="evidence-test",
        version="v-test",
        target="delta_ri",
        features=features or ["ri_initial", "nurse_hppd"],
        model_type="xgboost",
        model_path="missing-model.json",
        metrics=final_metrics,
    )


def nested_keys(value):
    keys = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(key)
            keys.update(nested_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(nested_keys(nested))
    return keys


PUBLIC_INDIVIDUAL_DERIVED_KEYS = {
    "aggregation",
    "attribution",
    "attributions",
    "baseline",
    "baseline_expected",
    "baseline_prediction",
    "benefit",
    "breakdown",
    "breakdowns",
    "component_means",
    "components",
    "confidence",
    "contributions",
    "default_pilot_weights",
    "explanation",
    "explanations",
    "feature_coverage",
    "legacy_icea",
    "prediction",
    "predictions",
    "shap",
    "transformations",
    "uncertainty",
}


class ModelEvidenceUnitTests(TestCase):
    def test_shadow_response_redaction_removes_individual_numeric_derivatives(self):
        raw = {
            "summary": {
                "rows_requested": 1,
                "rows_scored": 1,
                "status_counts": {"complete": 1},
                "component_means": {"benefit": 0.4},
                "default_pilot_weights": {"benefit": 1.0},
            },
            "results": [
                {
                    "row_id": "episode:1",
                    "status": "complete",
                    "score": 72.0,
                    "raw_score": 0.9,
                    "confidence": {"value": 0.8},
                    "components": {"benefit": {"raw": 1.0, "normalized": 0.4}},
                    "prediction": 9.0,
                    "predictions": [9.0],
                    "baseline": 7.0,
                    "baseline_prediction": 7.0,
                    "benefit": 2.0,
                    "legacy_icea": {"prediction": 9.0},
                    "uncertainty": 0.2,
                    "shap": {"nurse_hppd": 0.2},
                    "contributions": {"nurse_hppd": 0.2},
                    "attributions": {"nurse_hppd": 0.2},
                    "explanation": {"numeric_payload": 0.2},
                    "breakdown": {"benefit": 0.4},
                    "aggregation": {"nurse_reliability": 0.9},
                    "lineage": {
                        "formula_version": "icea_plus_v1",
                        "transformations": {"baseline_reference_values": {"nurse_hppd": 2.0}},
                        "source": {"request_hash": "sha256:test", "reference_rows": 100},
                    },
                }
            ],
            "_aggregate_rows": [{"score": 72.0, "components": {"benefit": {"normalized": 0.4}}}],
        }

        public = redact_shadow_score_response(raw)

        self.assertNotIn("_aggregate_rows", public)
        self.assertTrue(PUBLIC_INDIVIDUAL_DERIVED_KEYS.isdisjoint(nested_keys(public["results"][0])))
        self.assertTrue(PUBLIC_INDIVIDUAL_DERIVED_KEYS.isdisjoint(nested_keys(public["summary"])))
        self.assertEqual(public["results"][0]["status"], "shadow_only")
        self.assertIsNone(public["results"][0]["score"])
        self.assertIsNone(public["results"][0]["raw_score"])
        self.assertEqual(public["summary"]["rows_scored"], 1)
        self.assertEqual(public["summary"]["status_counts"]["complete"], 1)
        self.assertIsNone(public["score_summary"])
        self.assertTrue(public["score_summary_redacted"])

    def test_matching_evidence_and_artifact_features_can_be_defensible(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(
                feature_names=[" ri_initial ", None, "", "nurse_hppd"],
            )
        )

        evidence = summarize_model_evidence(artifact)

        self.assertTrue(evidence.defensible)
        self.assertEqual(evidence.evidence_pack["feature_names"], ["ri_initial", "nurse_hppd"])
        self.assertTrue(evidence.evidence_pack["feature_names_match"])
        self.assertEqual(evidence.evidence_pack["feature_names_warnings"], [])

    def test_invalid_training_row_counts_are_not_defensible(self):
        for invalid_count in (0, -1, "0", "-1", "abc", None):
            with self.subTest(invalid_count=invalid_count):
                artifact = create_artifact(
                    evidence_pack=complete_evidence_pack(training_row_count=invalid_count)
                )

                evidence = summarize_model_evidence(artifact)

                self.assertFalse(evidence.defensible)
                self.assertEqual(evidence.evidence_status, "evidence_incomplete")
                self.assertIn("invalid_training_row_count", evidence.missing_evidence)
                self.assertIn("model_not_defensible", evidence.statuses)

    def test_invalid_validation_row_counts_are_unavailable_and_not_defensible(self):
        for invalid_count in (0, -1, "0", "-1", "abc", None):
            with self.subTest(invalid_count=invalid_count):
                artifact = create_artifact(
                    evidence_pack=complete_evidence_pack(
                        validation_row_count=invalid_count,
                        validation_unavailable_reason="validation_count_not_available",
                    )
                )

                evidence = summarize_model_evidence(artifact)

                self.assertFalse(evidence.defensible)
                self.assertEqual(evidence.validation_status, "validation_unavailable")
                self.assertIn("invalid_validation_row_count", evidence.missing_evidence)
                self.assertIn("validation_unavailable", evidence.statuses)

    def test_positive_training_and_validation_row_counts_can_be_defensible(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(training_row_count=1, validation_row_count=1)
        )

        evidence = summarize_model_evidence(artifact)

        self.assertTrue(evidence.defensible)
        self.assertEqual(evidence.validation_status, "validation_available")

    def test_training_evidence_metadata_never_emits_negative_training_count(self):
        frame = pd.DataFrame({"ri_initial": [1.0, 2.0], "nurse_hppd": [3.0, 4.0], "delta_ri": [0.1, 0.2]})

        evidence = build_training_evidence_metadata(
            raw_df=frame,
            model_df=frame,
            features=["ri_initial", "nurse_hppd"],
            target="delta_ri",
            dataset_grain="test",
            metrics={
                "rmse": 0.1,
                "conformal": {"calibration_size": 3},
            },
            temporal_guardrail_status="passed",
        )

        self.assertIsNone(evidence["training_row_count"])
        self.assertEqual(evidence["validation_row_count"], 3)

    def test_training_evidence_metadata_marks_invalid_validation_count_unavailable(self):
        frame = pd.DataFrame({"ri_initial": [1.0, 2.0], "nurse_hppd": [3.0, 4.0], "delta_ri": [0.1, 0.2]})

        evidence = build_training_evidence_metadata(
            raw_df=frame,
            model_df=frame,
            features=["ri_initial", "nurse_hppd"],
            target="delta_ri",
            dataset_grain="test",
            metrics={
                "rmse": 0.1,
                "conformal": {"calibration_size": "abc"},
            },
            temporal_guardrail_status="passed",
        )

        self.assertEqual(evidence["training_row_count"], 2)
        self.assertIsNone(evidence["validation_row_count"])
        self.assertEqual(evidence["validation_unavailable_reason"], "validation_row_count_invalid")

    def test_training_evidence_metadata_marks_missing_validation_count_unavailable(self):
        frame = pd.DataFrame({"ri_initial": [1.0, 2.0], "nurse_hppd": [3.0, 4.0], "delta_ri": [0.1, 0.2]})

        evidence = build_training_evidence_metadata(
            raw_df=frame,
            model_df=frame,
            features=["ri_initial", "nurse_hppd"],
            target="delta_ri",
            dataset_grain="test",
            metrics={"rmse": 0.1},
            temporal_guardrail_status="passed",
        )

        self.assertEqual(evidence["training_row_count"], 2)
        self.assertIsNone(evidence["validation_row_count"])
        self.assertEqual(evidence["validation_unavailable_reason"], "validation_row_count_not_computed")

    def test_reordered_evidence_features_are_not_defensible(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(feature_names=["nurse_hppd", "ri_initial"])
        )

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertIn("feature_names_mismatch", evidence.missing_evidence)
        self.assertIn("feature_names_order_mismatch", evidence.missing_evidence)

    def test_artifact_feature_not_covered_by_evidence_is_not_defensible(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(),
            features=["ri_initial", "nurse_hppd", "new_feature"],
        )

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertIn("feature_names_mismatch", evidence.missing_evidence)
        self.assertIn("artifact_features_missing_from_evidence:new_feature", evidence.missing_evidence)

    def test_evidence_feature_not_used_by_artifact_is_not_defensible(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(feature_names=["ri_initial", "nurse_hppd", "stale_feature"])
        )

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertEqual(
            evidence.evidence_pack["feature_names"],
            ["ri_initial", "nurse_hppd", "stale_feature"],
        )
        self.assertIn("feature_names_mismatch", evidence.missing_evidence)
        self.assertIn("evidence_features_missing_from_artifact:stale_feature", evidence.missing_evidence)

    def test_missing_evidence_feature_names_is_not_replaced_by_artifact_features(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack(feature_names=None))

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertEqual(evidence.evidence_pack["feature_names"], [])
        self.assertIn("feature_names", evidence.missing_evidence)

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

    def test_not_evaluated_external_payload_is_not_defensible(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(temporal_guardrail_status="not_evaluated_external_payload")
        )

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertIn("temporal_guardrail_not_evaluated", evidence.missing_evidence)
        self.assertIn("temporal_spec_required", evidence.missing_evidence)
        self.assertIn("model_not_defensible", evidence.statuses)

    def test_arbitrary_limitation_note_is_not_sufficient(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack(limitations=["some_note"]))

        evidence = summarize_model_evidence(artifact)

        self.assertFalse(evidence.defensible)
        self.assertEqual(evidence.limitations_status, "limitations_incomplete")
        self.assertIn("required_limitations", evidence.missing_evidence)

    def test_each_required_limitation_is_blocking_when_missing(self):
        required = [
            "not_for_individual_decisioning",
            "not_mdr_production_ready",
            "shadow_aggregate_research_only",
        ]
        for missing_limitation in required:
            with self.subTest(missing_limitation=missing_limitation):
                limitations = [value for value in complete_evidence_pack()["limitations"] if value != missing_limitation]
                artifact = create_artifact(evidence_pack=complete_evidence_pack(limitations=limitations))

                evidence = summarize_model_evidence(artifact)

                self.assertFalse(evidence.defensible)
                self.assertEqual(evidence.limitations_status, "limitations_incomplete")
                self.assertIn(f"missing_required_limitations:{missing_limitation}", evidence.missing_evidence)

    def test_complete_required_limitations_allow_evidence_to_advance(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack())

        evidence = summarize_model_evidence(artifact)

        self.assertTrue(evidence.defensible)
        self.assertEqual(evidence.limitations_status, "limitations_complete")


class ModelEvidenceAPITests(ICEAPlusFixtureMixin, TestCase):
    def setUp(self):
        self.dev_env = mock.patch.dict(os.environ, {"ICEA_DEV_ALLOW_INSECURE": "true"}, clear=False)
        self.dev_env.start()
        self.addCleanup(self.dev_env.stop)
        self.client = APIClient()

    def _create_followup_records(self, *, artifact, count, score=70.0, baseline_model_id=None, start=0):
        for i, episode in enumerate(self.episodes[start : start + count], start=start):
            aggregate_row = {
                "row_id": f"episode:{episode.id}",
                "grain": "episode",
                "episode_id": int(episode.id),
                "unit_id": int(episode.unit_id),
                "status": "complete",
                "score": float(score + i),
                "raw_score": 0.1,
                "warnings": [],
                "components": {
                    "benefit": {"normalized": 0.2},
                    "attribution": {"normalized": 0.3},
                    "causal": {"normalized": 0.4},
                    "quality": {"normalized": 0.5},
                    "uncertainty": {"normalized": 0.1},
                },
                "aggregation": {"severity_weight": 1.0, "effective_exposure_share": 1.0},
            }
            public_row = {
                **aggregate_row,
                "status": "shadow_only",
                "score": None,
                "raw_score": None,
                INTERNAL_AGGREGATE_ROW_KEY: aggregate_row,
            }
            ICEAPlusFollowupRecord.objects.create(
                episode=episode,
                model=artifact,
                formula_version="icea_plus_v1",
                formula_protocol_hash="",
                initial_state="complete",
                current_state="complete",
                initial_request={"baseline_model_id": str(baseline_model_id)} if baseline_model_id else {},
                initial_result=public_row,
                non_individual_use=True,
                shadow_mode=True,
                exploratory_only=True,
            )

    def _sufficient_followup_evaluation(self):
        return FollowupEvaluation(
            evidence_types=["test_followup"],
            evidence_summary={"test_followup": True},
            support={"sufficient_for_rescore": True},
            warnings=[],
            sufficient_for_rescore=True,
            followup_status="stale",
            last_followup_at=self.now,
            feature_snapshot_hash="followup-snapshot",
        )

    def _persist_initial_result(self, *, row_status, score):
        episode = self.episodes[0]
        aggregate_row = {
            "row_id": f"episode:{episode.id}",
            "episode_id": int(episode.id),
            "unit_id": int(episode.unit_id),
            "status": row_status,
            "score": score,
            "raw_score": 0.1 if score is not None else None,
            "warnings": [],
            "aggregation": {"severity_weight": 1.0, "effective_exposure_share": 1.0},
        }
        public_row = {
            **aggregate_row,
            "status": "shadow_only" if score is not None else row_status,
            "score": None,
            "raw_score": None,
            "shadow_mode": True,
            "non_individual_use": True,
        }
        result = {
            "formula_version": "icea_plus_v1",
            "formula_protocol_hash": "",
            "results": [public_row],
            "_aggregate_rows": [aggregate_row],
        }
        computation = ICEAPlusComputation.objects.create(
            formula_version="icea_plus_v1",
            model=self.episode_artifact,
            grain="episode",
            rows=1,
            status="ok",
            summary={},
        )
        record = persist_initial_followup_records(
            artifact=self.episode_artifact,
            result=result,
            computation=computation,
            request_config={"grain": "episode", "from_db": True},
        )[0]
        return record, public_row

    def _case_mix_variables_list_payload(self):
        payload = self._external_training_payload()
        variables = sorted(CASE_MIX_REQUIRED_DOMAINS)
        for index, row in enumerate(payload["dataset"]):
            for offset, variable in enumerate(variables):
                row[variable] = float(index + offset + 1)
        payload["features"] = ["ri_initial", "nurse_hppd", *variables]
        payload["case_mix_spec"] = {
            "source": "test_declared_variables_list",
            "variables": variables,
        }
        return payload

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
        self.assertEqual(body["primary_model_evidence_status"], "evidence_incomplete")
        self.assertNotIn("results", body)
        self.assertIn("dataset_fingerprint", body["missing_evidence"])

    def test_invalid_training_row_count_is_exposed_by_models_and_blocks_score(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack(training_row_count=0))

        models_response = self.client.get("/api/v1/models/")

        self.assertEqual(models_response.status_code, 200)
        model = next(item for item in models_response.json() if item["id"] == str(artifact.id))
        self.assertFalse(model["defensible"])
        self.assertEqual(model["evidence_status"], "evidence_incomplete")
        self.assertIn("invalid_training_row_count", model["missing_evidence"])

        score_response = self.client.post(
            "/api/v1/icea-plus/score/",
            {"model_id": str(artifact.id), "grain": "episode", "from_db": True},
            format="json",
        )

        self.assertEqual(score_response.status_code, 400)
        self.assertEqual(score_response.json()["detail"], "model_not_defensible")
        self.assertIn("invalid_training_row_count", score_response.json()["missing_evidence"])
        self.assertNotIn("results", score_response.json())

    def test_invalid_validation_row_count_is_exposed_as_unavailable_by_models(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(
                validation_row_count="abc",
                validation_unavailable_reason="validation_count_not_available",
            )
        )

        response = self.client.get("/api/v1/models/")

        self.assertEqual(response.status_code, 200)
        model = next(item for item in response.json() if item["id"] == str(artifact.id))
        self.assertFalse(model["defensible"])
        self.assertEqual(model["validation_status"], "validation_unavailable")
        self.assertIn("invalid_validation_row_count", model["missing_evidence"])

    def test_feature_names_mismatch_is_exposed_and_blocks_score(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(),
            features=["ri_initial", "nurse_hppd", "post_training_feature"],
        )

        models_response = self.client.get("/api/v1/models/")

        self.assertEqual(models_response.status_code, 200)
        model = next(item for item in models_response.json() if item["id"] == str(artifact.id))
        self.assertFalse(model["defensible"])
        self.assertEqual(model["evidence_status"], "evidence_incomplete")
        self.assertIn("feature_names_mismatch", model["missing_evidence"])
        self.assertIn(
            "artifact_features_missing_from_evidence:post_training_feature",
            model["missing_evidence"],
        )

        score_response = self.client.post(
            "/api/v1/icea-plus/score/",
            {"model_id": str(artifact.id), "grain": "episode", "from_db": True},
            format="json",
        )
        self.assertEqual(score_response.status_code, 400)
        self.assertEqual(score_response.json()["detail"], "model_not_defensible")
        self.assertIn("feature_names_mismatch", score_response.json()["missing_evidence"])

    def test_legacy_compute_with_non_defensible_model_remains_blocked(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(),
            features=["ri_initial", "nurse_hppd", "post_training_feature"],
        )

        response = self.client.post(
            "/api/v1/icea/compute/",
            {
                "model_id": str(artifact.id),
                "data": [{"ri_initial": 50.0, "nurse_hppd": 4.0, "post_training_feature": 1.0}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "model_not_defensible")
        self.assertIn("feature_names_mismatch", body["missing_evidence"])
        self.assertNotIn("results", body)

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

    def test_models_endpoint_exposes_incomplete_limitations_status(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack(limitations=["some_note"]))

        response = self.client.get("/api/v1/models/")

        self.assertEqual(response.status_code, 200)
        item = next(row for row in response.json() if row["id"] == str(artifact.id))
        self.assertFalse(item["defensible"])
        self.assertEqual(item["limitations_status"], "limitations_incomplete")
        self.assertIn("required_limitations", item["missing_evidence"])

    def _external_training_payload(
        self,
        *,
        temporal_spec=True,
        invalid_temporal_spec=False,
        mixed_outcome_horizons=False,
        include_real_case_mix=True,
        provide_case_mix_spec=True,
    ):
        rows = []
        for i in range(8):
            row = {
                "ri_initial": 50.0 + i,
                "nurse_hppd": 3.0 + (i * 0.1),
                "delta_ri": 2.0 + (i * 0.2),
            }
            if include_real_case_mix:
                row.update(
                    {
                        "age_years": 60.0 + i,
                        "charlson_index": float(i % 3),
                        "frailty_score": 1.0 + float(i % 2),
                    }
                )
            if temporal_spec:
                feature_end = "2026-03-01T20:00:00Z"
                outcome_start = "2026-03-01T18:00:00Z" if invalid_temporal_spec else "2026-03-01T20:00:00Z"
                row["temporal_spec"] = {
                    "temporal_spec_version": "icea_temporal_v1",
                    "index_time": "2026-03-01T08:00:00Z",
                    "feature_window_start": "2026-03-01T08:00:00Z",
                    "feature_window_end": feature_end,
                    "outcome_window_start": outcome_start,
                    "outcome_window_end": (
                        "2026-03-02T20:00:00Z"
                        if mixed_outcome_horizons and i % 2 == 0
                        else "2026-03-02T02:00:00Z"
                        if mixed_outcome_horizons
                        else "2026-03-02T08:00:00Z"
                    ),
                    "censoring_reason": "not_censored",
                }
            rows.append(row)
        payload = {
            "name": "external-evidence-test",
            "version": "v-external",
            "target": "delta_ri",
            "features": ["ri_initial", "nurse_hppd", "age_years", "charlson_index", "frailty_score"],
            "dataset": rows,
        }
        if provide_case_mix_spec:
            payload["case_mix_spec"] = complete_evidence_pack()["case_mix_spec"]
        return payload

    def _mock_external_train_result(self, *, features=None):
        return SimpleNamespace(
            model_path="mock-external-model.json",
            features=features or ["ri_initial", "nurse_hppd", "age_years", "charlson_index", "frailty_score"],
            target="delta_ri",
            metrics={
                "rmse": 0.1,
                "mae": 0.1,
                "n_rows": 8,
                "n_features": 2,
                "conformal": {"method": "split_abs_residual", "alpha": 0.05, "q_hat": 0.1, "calibration_size": 2},
            },
        )

    def test_models_train_without_temporal_spec_is_not_defensible(self):
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post(
                "/api/v1/models/train/",
                self._external_training_payload(temporal_spec=False),
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        self.assertEqual(body["evidence_status"], "evidence_incomplete")
        self.assertEqual(body["temporal_guardrail_status"], "insufficient_temporal_spec")
        self.assertIn("temporal_spec_required", body["missing_evidence"])
        self.assertIn("model_not_defensible", summarize_model_evidence(ModelArtifact.objects.get(id=body["id"])).statuses)

    def test_models_train_with_invalid_temporal_spec_is_not_defensible_not_500(self):
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post(
                "/api/v1/models/train/",
                self._external_training_payload(invalid_temporal_spec=True),
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        self.assertEqual(body["temporal_guardrail_status"], "temporal_leakage_blocked")
        self.assertIn("temporal_guardrail_not_passed:temporal_leakage_blocked", body["missing_evidence"])

    def test_models_train_with_valid_temporal_spec_can_be_defensible_shadow_research(self):
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post(
                "/api/v1/models/train/",
                self._external_training_payload(),
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["defensible"])
        self.assertEqual(body["evidence_status"], "evidence_complete")
        self.assertEqual(body["temporal_guardrail_status"], "temporal_guardrails_passed")
        self.assertEqual(body["limitations_status"], "limitations_complete")
        self.assertEqual(body["intended_use"], INTENDED_USE_SHADOW_AGGREGATE)
        evidence = body["metrics"]["evidence_pack"]
        self.assertEqual(evidence["outcome_comparability_status"], "comparable")
        self.assertEqual(evidence["outcome_window"]["horizon_hours"], 12.0)
        self.assertEqual(evidence["outcome_comparability_warnings"], [])

    def test_models_train_with_mixed_outcome_horizons_is_not_defensible(self):
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post(
                "/api/v1/models/train/",
                self._external_training_payload(mixed_outcome_horizons=True),
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        self.assertEqual(body["evidence_status"], "evidence_incomplete")
        self.assertIn("mixed_outcome_horizons", body["missing_evidence"])
        evidence = body["metrics"]["evidence_pack"]
        self.assertEqual(evidence["outcome_comparability_status"], "not_comparable")
        self.assertIn("mixed_outcome_horizons", evidence["outcome_comparability_warnings"])
        self.assertIn("outcome_window_not_unique", evidence["outcome_comparability_warnings"])
        self.assertEqual(evidence["outcome_window"]["unique_horizon_hours"], [6.0, 24.0])
        self.assertIsNone(evidence["outcome_window"]["horizon_hours"])

        models_response = self.client.get("/api/v1/models/")
        model = next(item for item in models_response.json() if item["id"] == body["id"])
        self.assertIn("mixed_outcome_horizons", model["missing_evidence"])

        score_response = self.client.post(
            "/api/v1/icea-plus/score/",
            {"model_id": body["id"], "grain": "episode", "from_db": True},
            format="json",
        )
        self.assertEqual(score_response.status_code, 400)
        self.assertEqual(score_response.json()["detail"], "model_not_defensible")

        aggregate_response = self.client.get(
            "/api/v1/icea-plus/aggregate/",
            {"model_id": body["id"], "grain": "episode", "group_by": "unit"},
        )
        self.assertEqual(aggregate_response.status_code, 400)
        self.assertEqual(aggregate_response.json()["detail"], "model_not_defensible")
        self.assertNotIn("results", aggregate_response.json())

    def test_models_train_with_inconsistent_outcome_definition_is_not_defensible(self):
        payload = self._external_training_payload()
        payload["dataset"][0]["temporal_spec"]["outcome_definition"] = "mortality_24h"
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        self.assertIn("outcome_definition_not_comparable", body["missing_evidence"])

    def test_models_train_with_mixed_temporal_versions_is_not_defensible(self):
        payload = self._external_training_payload()
        payload["dataset"][0]["temporal_spec"]["temporal_spec_version"] = "icea_temporal_v2"
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        evidence = body["metrics"]["evidence_pack"]
        self.assertIn("mixed_temporal_spec_versions", evidence["outcome_comparability_warnings"])
        self.assertIn("outcome_definition_not_comparable", body["missing_evidence"])

    def test_declared_case_mix_features_missing_from_payload_do_not_support_case_mix(self):
        payload = self._external_training_payload(include_real_case_mix=False)
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        evidence = body["metrics"]["evidence_pack"]
        self.assertIn("declared_feature_missing_from_payload", evidence["feature_warnings"])
        self.assertEqual(
            evidence["declared_features_missing_from_payload"],
            ["age_years", "charlson_index", "frailty_score"],
        )
        self.assertIsNone(evidence["case_mix_spec"])
        self.assertEqual(body["case_mix_status"], "case_mix_insufficient")
        score_response = self.client.post(
            "/api/v1/icea-plus/score/",
            {"model_id": body["id"], "grain": "episode", "from_db": True},
            format="json",
        )
        self.assertEqual(score_response.status_code, 400)
        self.assertEqual(score_response.json()["detail"], "model_not_defensible")

    def test_real_non_null_case_mix_columns_can_be_derived(self):
        payload = self._external_training_payload(provide_case_mix_spec=False)
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["defensible"])
        evidence = body["metrics"]["evidence_pack"]
        self.assertEqual(evidence["case_mix_spec"]["source"], "derived_from_training_data")
        self.assertEqual(body["case_mix_status"], "case_mix_available")

    def test_empty_case_mix_columns_do_not_count_as_real_support(self):
        payload = self._external_training_payload()
        for row in payload["dataset"]:
            row["age_years"] = None
            row["charlson_index"] = None
            row["frailty_score"] = None
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        self.assertEqual(body["case_mix_status"], "case_mix_insufficient")
        self.assertIn("case_mix_columns_missing_or_empty", body["metrics"]["evidence_pack"]["case_mix_unavailable_reason"])

    def test_provided_case_mix_spec_with_missing_columns_is_not_defensible(self):
        payload = self._external_training_payload(include_real_case_mix=False)
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        self.assertEqual(body["case_mix_status"], "case_mix_insufficient")
        self.assertIn("case_mix_columns_missing_or_empty", body["metrics"]["evidence_pack"]["case_mix_unavailable_reason"])

    def test_provided_case_mix_spec_with_real_support_can_be_defensible(self):
        payload = self._external_training_payload()
        with mock.patch("icea_core.views.train_xgb_regressor", return_value=self._mock_external_train_result()):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["defensible"])
        self.assertEqual(body["case_mix_status"], "case_mix_available")

    def test_case_mix_variables_list_with_observed_columns_is_supported(self):
        payload = self._case_mix_variables_list_payload()
        with mock.patch(
            "icea_core.views.train_xgb_regressor",
            return_value=self._mock_external_train_result(features=payload["features"]),
        ):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["defensible"])
        self.assertEqual(body["case_mix_status"], "case_mix_available")
        model = next(item for item in self.client.get("/api/v1/models/").json() if item["id"] == body["id"])
        self.assertEqual(model["case_mix_status"], "case_mix_available")

    def test_case_mix_variables_list_with_missing_column_is_not_supported(self):
        payload = self._case_mix_variables_list_payload()
        for row in payload["dataset"]:
            row.pop("baseline_load")
        with mock.patch(
            "icea_core.views.train_xgb_regressor",
            return_value=self._mock_external_train_result(features=payload["features"]),
        ):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        self.assertEqual(body["case_mix_status"], "case_mix_insufficient")
        self.assertIn(
            "case_mix_columns_missing_or_empty:baseline_load:baseline_load",
            body["metrics"]["evidence_pack"]["case_mix_unavailable_reason"],
        )
        model = next(item for item in self.client.get("/api/v1/models/").json() if item["id"] == body["id"])
        self.assertEqual(model["case_mix_status"], "case_mix_insufficient")
        score_response = self.client.post(
            "/api/v1/icea-plus/score/",
            {"model_id": body["id"], "grain": "episode", "from_db": True},
            format="json",
        )
        self.assertEqual(score_response.status_code, 400)
        self.assertEqual(score_response.json()["case_mix_status"], "case_mix_insufficient")

    def test_case_mix_variables_list_with_nan_only_column_is_not_supported(self):
        payload = self._case_mix_variables_list_payload()
        for row in payload["dataset"]:
            row["baseline_load"] = None
        with mock.patch(
            "icea_core.views.train_xgb_regressor",
            return_value=self._mock_external_train_result(features=payload["features"]),
        ):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        self.assertEqual(body["case_mix_status"], "case_mix_insufficient")
        self.assertIn(
            "case_mix_columns_missing_or_empty:baseline_load:baseline_load",
            body["metrics"]["evidence_pack"]["case_mix_unavailable_reason"],
        )

    def test_case_mix_domains_and_variables_conflict_is_not_supported(self):
        payload = self._case_mix_variables_list_payload()
        payload["case_mix_spec"]["domains"] = {
            **{domain: [domain] for domain in CASE_MIX_REQUIRED_DOMAINS},
            "age": ["age_alias"],
        }
        for row in payload["dataset"]:
            row["age_alias"] = row["age"]
        payload["features"].append("age_alias")
        with mock.patch(
            "icea_core.views.train_xgb_regressor",
            return_value=self._mock_external_train_result(features=payload["features"]),
        ):
            response = self.client.post("/api/v1/models/train/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["defensible"])
        self.assertIn("case_mix_domain_variable_conflict:age", body["metrics"]["evidence_pack"]["case_mix_unavailable_reason"])

    def test_initial_followup_state_uses_internal_complete_status_not_public_shadow_only(self):
        record, public_row = self._persist_initial_result(row_status="complete", score=72.0)

        self.assertEqual(public_row["status"], "shadow_only")
        self.assertIsNone(public_row["score"])
        self.assertEqual(record.initial_state, "complete")
        self.assertEqual(record.current_state, "complete")
        self.assertEqual(record.followup_status, "pending_followup")
        self.assertEqual(record.initial_result[INTERNAL_AGGREGATE_ROW_KEY]["status"], "complete")
        patient_summary = build_patient_summary(record)
        self.assertIsNone(patient_summary["initial_score"]["score"])
        self.assertNotIn(INTERNAL_AGGREGATE_ROW_KEY, patient_summary["initial_score"])
        summary = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")
        self.assertEqual(summary["status_counts"]["complete"], 1)

    def test_initial_followup_state_uses_internal_provisional_status_not_public_shadow_only(self):
        record, public_row = self._persist_initial_result(row_status="provisional", score=51.0)

        self.assertEqual(public_row["status"], "shadow_only")
        self.assertIsNone(public_row["score"])
        self.assertEqual(record.initial_state, "immediate_provisional")
        self.assertEqual(record.current_state, "immediate_provisional")
        self.assertEqual(record.followup_status, "pending_followup")

    def test_initial_followup_state_keeps_real_insufficient_evidence(self):
        record, public_row = self._persist_initial_result(row_status="insufficient_evidence", score=None)

        self.assertEqual(public_row["status"], "insufficient_evidence")
        self.assertIsNone(public_row["score"])
        self.assertEqual(record.initial_state, "insufficient_evidence")
        self.assertEqual(record.current_state, "insufficient_evidence")
        self.assertEqual(record.followup_status, "insufficient_evidence")

    def test_score_blocks_model_with_temporal_guardrails_not_evaluated(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(temporal_guardrail_status="not_evaluated_external_payload")
        )

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
        self.assertIn("case_mix_insufficient", evidence["case_mix_unavailable_reason"])
        self.assertEqual(evidence["calibration_unavailable_reason"], "insufficient_calibration_sample_or_not_computed")

    def test_training_with_sufficient_case_mix_spec_is_defensible_shadow_research(self):
        case_mix_spec = complete_evidence_pack()["case_mix_spec"]
        for index, feature_row in enumerate(EpisodeWindowFeatureRow.objects.all()):
            feature_row.features = {
                **dict(feature_row.features or {}),
                "age_years": 60.0 + index,
                "charlson_index": float(index % 3),
                "frailty_score": 1.0 + float(index % 2),
            }
            feature_row.save(update_fields=["features"])
        with mock.patch("icea_pipeline.views.train_xgb_regressor") as train_mock:
            train_mock.return_value = SimpleNamespace(
                model_path="mock-window-model.json",
                features=[
                    "ri_initial",
                    "window_index",
                    "nurse_hppd",
                    "age_years",
                    "charlson_index",
                    "frailty_score",
                ],
                target="delta_ri",
                metrics={
                    "rmse": 0.0,
                    "mae": 0.0,
                    "n_rows": 12,
                    "n_features": 6,
                    "conformal": {"method": "split_abs_residual", "alpha": 0.05, "q_hat": 0.1, "calibration_size": 6},
                },
            )
            response = self.client.post("/api/v1/pipeline/train/", {"case_mix_spec": case_mix_spec}, format="json")

        self.assertEqual(response.status_code, 200)
        model_id = response.json()["model_id"]
        models_response = self.client.get("/api/v1/models/")
        self.assertEqual(models_response.status_code, 200)
        model = next(item for item in models_response.json() if item["id"] == model_id)
        self.assertEqual(model["evidence_status"], "evidence_complete")
        self.assertTrue(model["defensible"])
        self.assertEqual(model["case_mix_status"], "case_mix_available")
        self.assertEqual(model["intended_use"], INTENDED_USE_SHADOW_AGGREGATE)

    def test_training_without_case_mix_spec_and_without_derivable_domains_is_not_defensible(self):
        with mock.patch("icea_pipeline.views.train_xgb_regressor") as train_mock:
            train_mock.return_value = SimpleNamespace(
                model_path="mock-window-model.json",
                features=["ri_initial", "window_index"],
                target="delta_ri",
                metrics={
                    "rmse": 0.0,
                    "mae": 0.0,
                    "n_rows": 12,
                    "n_features": 2,
                    "conformal": {"method": "split_abs_residual", "alpha": 0.05, "q_hat": 0.1, "calibration_size": 6},
                },
            )
            response = self.client.post("/api/v1/pipeline/train/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        model_id = response.json()["model_id"]
        models_response = self.client.get("/api/v1/models/")
        model = next(item for item in models_response.json() if item["id"] == model_id)
        self.assertFalse(model["defensible"])
        self.assertEqual(model["case_mix_status"], "case_mix_insufficient")
        self.assertIn("case_mix_spec_sufficient", model["missing_evidence"])

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
        self.assertNotIn("_aggregate_rows", body)
        row = body["results"][0]
        self.assertTrue(row["shadow_mode"])
        self.assertTrue(row["non_individual_use"])
        self.assertEqual(row["intended_use"], INTENDED_USE_SHADOW_AGGREGATE)
        self.assertTrue(row["flags"]["shadow_mode"])
        self.assertTrue(row["flags"]["non_individual_use"])
        self.assertEqual(row["status"], "shadow_only")
        self.assertIsNone(row["score"])
        self.assertIsNone(row["raw_score"])
        self.assertTrue(PUBLIC_INDIVIDUAL_DERIVED_KEYS.isdisjoint(nested_keys(row)))
        summary = body["summary"]
        self.assertGreater(summary["rows_scored"], 0)
        self.assertEqual(
            summary["rows_scored"],
            summary["status_counts"]["complete"] + summary["status_counts"]["provisional"],
        )
        self.assertGreater(
            summary["status_counts"]["complete"] + summary["status_counts"]["provisional"],
            0,
        )
        self.assertTrue(PUBLIC_INDIVIDUAL_DERIVED_KEYS.isdisjoint(nested_keys(summary)))
        self.assertTrue(summary["summary_redacted"])
        self.assertIsNone(summary["score_summary"])
        self.assertTrue(body["score_summary_redacted"])
        self.assertIsNone(body["score_summary"])
        computation = ICEAPlusComputation.objects.filter(model=self.episode_artifact).order_by("-created_at").first()
        self.assertIsNotNone(computation)
        self.assertEqual(computation.summary, summary)
        self.assertNotIn(INTERNAL_AGGREGATE_ROW_KEY, row)
        record = ICEAPlusFollowupRecord.objects.get(episode=self.episodes[0], model=self.episode_artifact)
        self.assertIn(INTERNAL_AGGREGATE_ROW_KEY, record.initial_result)
        self.assertIsNotNone(record.initial_result[INTERNAL_AGGREGATE_ROW_KEY]["score"])
        patient_summary = build_patient_summary(record)
        self.assertIsNone(patient_summary["initial_score"]["score"])
        self.assertNotIn(INTERNAL_AGGREGATE_ROW_KEY, patient_summary["initial_score"])
        self.assertTrue(PUBLIC_INDIVIDUAL_DERIVED_KEYS.isdisjoint(nested_keys(patient_summary["initial_score"])))

    def test_score_blocks_non_defensible_baseline_model_before_numeric_benefit(self):
        baseline = create_artifact(
            evidence_pack=complete_evidence_pack(dataset_fingerprint=None, dataset_hash=None)
        )

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "baseline_model_id": str(baseline.id),
                "grain": "episode",
                "from_db": True,
                "episode_ids": [int(self.episodes[0].id)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "baseline_model_not_defensible")
        self.assertEqual(body["primary_model_evidence_status"], "evidence_complete")
        self.assertEqual(body["baseline_model_evidence_status"], "evidence_incomplete")
        self.assertTrue(body["baseline_model_not_defensible"])
        self.assertIn("dataset_fingerprint", body["baseline_model_missing_evidence"])
        self.assertEqual(body["baseline_model"]["id"], str(baseline.id))
        self.assertNotIn("results", body)

    def test_score_returns_controlled_error_for_missing_baseline_model(self):
        missing_baseline_id = uuid.uuid4()

        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "baseline_model_id": str(missing_baseline_id),
                "grain": "episode",
                "from_db": True,
                "episode_ids": [int(self.episodes[0].id)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "baseline_model_not_found")
        self.assertEqual(body["primary_model_evidence_status"], "evidence_complete")
        self.assertEqual(body["baseline_model_evidence_status"], "not_found")
        self.assertTrue(body["baseline_model_not_defensible"])
        self.assertEqual(body["baseline_model_missing_evidence"], ["model_artifact_not_found"])
        self.assertNotIn("results", body)

    def test_score_allows_defensible_baseline_only_as_publicly_redacted_shadow(self):
        response = self.client.post(
            "/api/v1/icea-plus/score/",
            {
                "model_id": str(self.episode_artifact.id),
                "baseline_model_id": str(self.window_artifact.id),
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
        self.assertEqual(body["primary_model_evidence_status"], "evidence_complete")
        self.assertEqual(body["baseline_model_evidence_status"], "evidence_complete")
        self.assertFalse(body["baseline_model_not_defensible"])
        self.assertEqual(body["summary"]["baseline_mode"], "dedicated_baseline_model")
        self.assertTrue(body["shadow_mode"])
        self.assertTrue(body["non_individual_use"])
        self.assertIsNone(body["results"][0]["score"])
        self.assertEqual(body["results"][0]["status"], "shadow_only")

    def test_aggregate_blocks_non_defensible_baseline_without_agg_score(self):
        baseline = create_artifact(
            evidence_pack=complete_evidence_pack(dataset_fingerprint=None, dataset_hash=None)
        )

        response = self.client.get(
            "/api/v1/icea-plus/aggregate/",
            {
                "model_id": str(self.episode_artifact.id),
                "baseline_model_id": str(baseline.id),
                "grain": "episode",
                "group_by": "unit",
            },
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "baseline_model_not_defensible")
        self.assertEqual(body["baseline_model_evidence_status"], "evidence_incomplete")
        self.assertTrue(body["baseline_model_not_defensible"])
        self.assertNotIn("results", body)

    def test_followup_rescore_returns_controlled_baseline_evidence_block(self):
        baseline = create_artifact(
            evidence_pack=complete_evidence_pack(dataset_fingerprint=None, dataset_hash=None)
        )

        response = self.client.post(
            "/api/v1/icea-plus/followup/rescore/",
            {
                "episode_id": int(self.episodes[0].id),
                "model_id": str(self.episode_artifact.id),
                "baseline_model_id": str(baseline.id),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "baseline_model_not_defensible")
        self.assertEqual(body["primary_model_evidence_status"], "evidence_complete")
        self.assertEqual(body["baseline_model_evidence_status"], "evidence_incomplete")
        self.assertNotIn("results", body)

    def test_followup_governance_block_preserves_prior_enriched_result_without_failed_state(self):
        self._create_followup_records(artifact=self.episode_artifact, count=1)
        record = ICEAPlusFollowupRecord.objects.get(episode=self.episodes[0], model=self.episode_artifact)
        prior_enriched_result = dict(record.initial_result)
        prior_enriched_result["row_id"] = "prior-enriched"
        record.enriched_result = prior_enriched_result
        record.followup_status = "enriched_followup"
        record.current_state = "enriched_followup"
        record.save(update_fields=["enriched_result", "followup_status", "current_state"])

        invalidated_pack = complete_evidence_pack(dataset_fingerprint=None, dataset_hash=None)
        self.episode_artifact.metrics = {
            **dict(self.episode_artifact.metrics or {}),
            "evidence_pack": invalidated_pack,
        }
        self.episode_artifact.save(update_fields=["metrics"])

        with mock.patch(
            "icea_core.followup.evaluate_followup",
            return_value=self._sufficient_followup_evaluation(),
        ):
            response = self.client.post(
                "/api/v1/icea-plus/followup/rescore/",
                {
                    "episode_id": int(self.episodes[0].id),
                    "model_id": str(self.episode_artifact.id),
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "model_not_defensible")
        record.refresh_from_db()
        self.assertEqual(record.followup_status, "governance_blocked")
        self.assertEqual(record.current_state, "governance_blocked")
        self.assertNotEqual(record.followup_status, "failed")
        self.assertEqual(record.enriched_result, prior_enriched_result)
        self.assertEqual(record.provenance["governance_block"]["detail"], "model_not_defensible")
        self.assertIn("dataset_fingerprint", record.provenance["governance_block"]["missing_evidence"])
        self.assertIn("model_not_defensible", record.provenance["governance_block"]["warnings"])

        patient_summary = build_patient_summary(record)
        self.assertEqual(patient_summary["current_score"]["source_status"], prior_enriched_result["status"])
        self.assertIsNone(patient_summary["current_score"]["score"])
        aggregate_summary = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")
        self.assertEqual(aggregate_summary["detail"], "model_not_defensible")
        self.assertEqual(aggregate_summary["results"], [])

    def test_followup_technical_rescore_error_still_marks_failed(self):
        self._create_followup_records(artifact=self.episode_artifact, count=1)

        with (
            mock.patch(
                "icea_core.followup.evaluate_followup",
                return_value=self._sufficient_followup_evaluation(),
            ),
            mock.patch("icea_core.followup._score_episode_from_request", side_effect=RuntimeError("technical_failure")),
        ):
            response = self.client.post(
                "/api/v1/icea-plus/followup/rescore/",
                {
                    "episode_id": int(self.episodes[0].id),
                    "model_id": str(self.episode_artifact.id),
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        record = ICEAPlusFollowupRecord.objects.get(episode=self.episodes[0], model=self.episode_artifact)
        self.assertEqual(record.followup_status, "failed")
        self.assertEqual(record.current_state, "failed")
        self.assertIn("rescore_failed:RuntimeError", record.warnings)

    def test_writeback_patient_returns_controlled_model_evidence_block_without_record(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(dataset_fingerprint=None, dataset_hash=None)
        )

        response = self.client.get(
            "/api/v1/icea-plus/writeback/patient/",
            {
                "episode_id": int(self.episodes[0].id),
                "model_id": str(artifact.id),
            },
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "model_not_defensible")
        self.assertEqual(body["status"], "model_not_defensible")
        self.assertEqual(body["evidence_status"], "evidence_incomplete")
        self.assertIn("dataset_fingerprint", body["missing_evidence"])
        self.assertTrue(body["shadow_mode"])
        self.assertTrue(body["non_individual_use"])
        self.assertIsNone(body["score_summary"])
        self.assertTrue(body["score_summary_redacted"])

    def test_writeback_patient_existing_legacy_record_stays_redacted(self):
        artifact = create_artifact(metrics={})
        self._create_followup_records(artifact=artifact, count=1)

        response = self.client.get(
            "/api/v1/icea-plus/writeback/patient/",
            {
                "episode_id": int(self.episodes[0].id),
                "model_id": str(artifact.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["evidence"]["model"]["defensible"])
        self.assertEqual(body["evidence"]["model"]["evidence_status"], "evidence_incomplete")
        self.assertIn("writeback_summary_blocked_by_current_model_evidence", body["warnings"])
        self.assertIsNone(body["current_score"]["score"])
        self.assertTrue(body["current_score"]["score_suppressed"])
        self.assertNotIn(INTERNAL_AGGREGATE_ROW_KEY, body["current_score"])

    def test_writeback_summary_endpoint_returns_controlled_model_evidence_block(self):
        artifact = create_artifact(metrics={})
        self._create_followup_records(artifact=artifact, count=10)

        response = self.client.get(
            "/api/v1/icea-plus/writeback/summary/",
            {
                "model_id": str(artifact.id),
                "group_by": "unit",
            },
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"], "model_not_defensible")
        self.assertEqual(body["evidence_status"], "evidence_incomplete")
        self.assertTrue(body["suppressed"])
        self.assertEqual(body["results"], [])

    def test_followup_aggregate_uses_internal_rows_with_sufficient_support(self):
        self._create_followup_records(artifact=self.episode_artifact, count=10)

        payload = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")

        self.assertEqual(len(payload["results"]), 1)
        result = payload["results"][0]
        self.assertEqual(result["status"], "scored_aggregate")
        self.assertIsNotNone(result["score"])
        self.assertFalse(result["suppressed"])
        self.assertNotIn("rows", result)

    def test_followup_aggregate_blocks_legacy_model_without_evidence_pack(self):
        artifact = create_artifact(metrics={})
        self._create_followup_records(artifact=artifact, count=10)

        payload = build_summary_writeback(artifact=artifact, group_by="unit")

        self.assertEqual(payload["detail"], "model_not_defensible")
        self.assertEqual(payload["evidence_status"], "evidence_incomplete")
        self.assertTrue(payload["suppressed"])
        self.assertIsNone(payload["scored_aggregate"])
        self.assertIsNone(payload["summary"]["scored_aggregate"])
        self.assertIn("dataset_fingerprint", payload["missing_evidence"])
        self.assertIn("writeback_summary_blocked_by_current_model_evidence", payload["warnings"])
        self.assertEqual(payload["results"], [])

    def test_followup_aggregate_blocks_model_whose_evidence_was_invalidated(self):
        self._create_followup_records(artifact=self.episode_artifact, count=10)
        invalidated_pack = complete_evidence_pack(dataset_fingerprint=None, dataset_hash=None)
        self.episode_artifact.metrics = {**dict(self.episode_artifact.metrics or {}), "evidence_pack": invalidated_pack}
        self.episode_artifact.save(update_fields=["metrics"])

        payload = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")

        self.assertEqual(payload["detail"], "model_not_defensible")
        self.assertEqual(payload["evidence_status"], "evidence_incomplete")
        self.assertEqual(payload["summary"]["records"], 10)
        self.assertEqual(payload["results"], [])

    def test_followup_aggregate_separates_records_for_other_models(self):
        other_artifact = create_artifact(evidence_pack=complete_evidence_pack())
        self._create_followup_records(artifact=self.episode_artifact, count=10, score=70.0)
        self._create_followup_records(artifact=other_artifact, count=10, score=10.0)

        payload = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")

        self.assertEqual(payload["summary"]["records"], 10)
        self.assertEqual(len(payload["results"]), 1)
        self.assertGreater(payload["results"][0]["score"], 60.0)

    def test_followup_aggregate_blocks_non_defensible_stored_baseline(self):
        baseline = create_artifact(
            evidence_pack=complete_evidence_pack(dataset_fingerprint=None, dataset_hash=None)
        )
        self._create_followup_records(
            artifact=self.episode_artifact,
            count=10,
            baseline_model_id=baseline.id,
        )

        payload = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")

        self.assertEqual(payload["detail"], "baseline_model_not_defensible")
        self.assertEqual(payload["baseline_model_evidence_status"], "evidence_incomplete")
        self.assertTrue(payload["baseline_model_not_defensible"])
        self.assertEqual(payload["results"], [])

    def test_followup_aggregate_allows_defensible_stored_baseline(self):
        self._create_followup_records(
            artifact=self.episode_artifact,
            count=10,
            baseline_model_id=self.window_artifact.id,
        )

        payload = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")

        self.assertNotIn("detail", payload)
        self.assertEqual(payload["results"][0]["status"], "scored_aggregate")
        self.assertIsNotNone(payload["results"][0]["score"])

    def test_followup_aggregate_blocks_mixed_baseline_modes(self):
        self._create_followup_records(artifact=self.episode_artifact, count=5, start=0)
        self._create_followup_records(
            artifact=self.episode_artifact,
            count=5,
            start=5,
            baseline_model_id=self.window_artifact.id,
        )

        payload = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")

        self.assertEqual(payload["detail"], "mixed_baseline_models_not_aggregable")
        self.assertIn("writeback_summary_mixed_baseline_models_not_aggregable", payload["warnings"])
        self.assertEqual(payload["results"], [])

    def test_followup_aggregate_suppresses_internal_rows_with_low_support(self):
        self._create_followup_records(artifact=self.episode_artifact, count=9)

        payload = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")

        result = payload["results"][0]
        self.assertEqual(result["status"], "suppressed_low_support")
        self.assertIsNone(result["score"])
        self.assertTrue(result["suppressed"])

    def test_followup_aggregate_reads_case_mix_from_evidence_pack(self):
        self._create_followup_records(artifact=self.episode_artifact, count=10)

        payload = build_summary_writeback(artifact=self.episode_artifact, group_by="unit")

        self.assertNotIn("no_comparable_without_case_mix", payload["results"][0]["warnings"])

    def test_followup_aggregate_blocks_without_case_mix(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack(case_mix_spec=None, case_mix_unavailable_reason="missing"))
        self._create_followup_records(artifact=artifact, count=10)

        payload = build_summary_writeback(artifact=artifact, group_by="unit")

        self.assertEqual(payload["detail"], "model_not_defensible")
        self.assertEqual(payload["evidence_status"], "evidence_incomplete")
        self.assertEqual(payload["results"], [])

    def test_followup_aggregate_keeps_legacy_case_mix_fallback(self):
        artifact = create_artifact(
            evidence_pack=complete_evidence_pack(case_mix_spec=None, case_mix_unavailable_reason="missing"),
            metrics={"case_mix_spec": complete_evidence_pack()["case_mix_spec"]},
        )
        self._create_followup_records(artifact=artifact, count=10)

        payload = build_summary_writeback(artifact=artifact, group_by="unit")

        self.assertNotIn("no_comparable_without_case_mix", payload["results"][0]["warnings"])

    def test_aggregate_uses_internal_scores_while_public_rows_stay_redacted(self):
        aggregate_rows = []
        public_rows = []
        for i in range(10):
            aggregate_rows.append(
                {
                    "row_id": f"row-{i}",
                    "grain": "episode",
                    "episode_id": i + 1,
                    "unit_id": int(self.unit.id),
                    "start_dt": "2026-03-01T08:00:00+00:00",
                    "end_dt": "2026-03-01T20:00:00+00:00",
                    "status": "complete",
                    "score": 60.0 + i,
                    "raw_score": 0.1,
                    "shadow_mode": True,
                    "non_individual_use": True,
                    "flags": {"shadow_mode": True, "non_individual_use": True},
                    "warnings": [],
                    "components": {
                        "benefit": {"normalized": 0.2},
                        "attribution": {"normalized": 0.3},
                        "causal": {"normalized": 0.4},
                        "quality": {"normalized": 0.5},
                        "uncertainty": {"normalized": 0.1},
                    },
                    "aggregation": {"severity_weight": 1.0, "effective_exposure_share": 1.0},
                }
            )
            public_rows.append({**aggregate_rows[-1], "status": "shadow_only", "score": None, "raw_score": None})

        score_result = {
            "formula_version": "icea_plus_v1",
            "formula_protocol_hash": "hash",
            "formula_source": "test",
            "shadow_mode": True,
            "non_individual_use": True,
            "intended_use": INTENDED_USE_SHADOW_AGGREGATE,
            "model": {"id": str(self.episode_artifact.id), "version": self.episode_artifact.version},
            "summary": {
                "rows_requested": 10,
                "rows_scored": 10,
                "status_counts": {"complete": 10, "provisional": 0, "insufficient_evidence": 0},
            },
            "results": public_rows,
            "_aggregate_rows": aggregate_rows,
        }
        with mock.patch("icea_core.icea_plus_views.score_icea_plus", return_value=score_result):
            response = self.client.get(
                "/api/v1/icea-plus/aggregate/",
                {"model_id": str(self.episode_artifact.id), "grain": "episode", "group_by": "unit"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["status"], "scored_aggregate")
        self.assertIsNotNone(body["results"][0]["score"])
        self.assertEqual(body["summary"]["rows_scored"], 10)
        self.assertEqual(body["summary"]["status_counts"]["complete"], 10)
        self.assertNotIn("_aggregate_rows", body)
        self.assertNotIn("rows", body["results"][0])

    def test_aggregate_still_suppresses_low_support_internal_scores(self):
        aggregate_rows = [
            {
                "row_id": f"row-{i}",
                "grain": "episode",
                "episode_id": i + 1,
                "unit_id": int(self.unit.id),
                "status": "complete",
                "score": 70.0,
                "raw_score": 0.1,
                "shadow_mode": True,
                "non_individual_use": True,
                "flags": {"shadow_mode": True, "non_individual_use": True},
                "warnings": [],
                "components": {},
                "aggregation": {"severity_weight": 1.0, "effective_exposure_share": 1.0},
            }
            for i in range(9)
        ]
        score_result = {
            "formula_version": "icea_plus_v1",
            "formula_protocol_hash": "hash",
            "formula_source": "test",
            "summary": {"rows_requested": 9, "rows_scored": 9, "status_counts": {}},
            "results": [{**row, "status": "shadow_only", "score": None, "raw_score": None} for row in aggregate_rows],
            "_aggregate_rows": aggregate_rows,
        }
        with mock.patch("icea_core.icea_plus_views.score_icea_plus", return_value=score_result):
            response = self.client.get(
                "/api/v1/icea-plus/aggregate/",
                {"model_id": str(self.episode_artifact.id), "grain": "episode", "group_by": "unit"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()["results"][0]
        self.assertEqual(result["status"], "suppressed_low_support")
        self.assertIsNone(result["score"])
        self.assertTrue(result["suppressed"])
