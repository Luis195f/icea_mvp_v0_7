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

    def test_models_endpoint_exposes_incomplete_limitations_status(self):
        artifact = create_artifact(evidence_pack=complete_evidence_pack(limitations=["some_note"]))

        response = self.client.get("/api/v1/models/")

        self.assertEqual(response.status_code, 200)
        item = next(row for row in response.json() if row["id"] == str(artifact.id))
        self.assertFalse(item["defensible"])
        self.assertEqual(item["limitations_status"], "limitations_incomplete")
        self.assertIn("required_limitations", item["missing_evidence"])

    def _external_training_payload(self, *, temporal_spec=True, invalid_temporal_spec=False):
        rows = []
        for i in range(8):
            row = {
                "ri_initial": 50.0 + i,
                "nurse_hppd": 3.0 + (i * 0.1),
                "delta_ri": 2.0 + (i * 0.2),
            }
            if temporal_spec:
                feature_end = "2026-03-01T20:00:00Z"
                outcome_start = "2026-03-01T18:00:00Z" if invalid_temporal_spec else "2026-03-01T20:00:00Z"
                row["temporal_spec"] = {
                    "temporal_spec_version": "icea_temporal_v1",
                    "index_time": "2026-03-01T08:00:00Z",
                    "feature_window_start": "2026-03-01T08:00:00Z",
                    "feature_window_end": feature_end,
                    "outcome_window_start": outcome_start,
                    "outcome_window_end": "2026-03-02T08:00:00Z",
                    "censoring_reason": "not_censored",
                }
            rows.append(row)
        return {
            "name": "external-evidence-test",
            "version": "v-external",
            "target": "delta_ri",
            "features": ["ri_initial", "nurse_hppd"],
            "dataset": rows,
            "case_mix_spec": complete_evidence_pack()["case_mix_spec"],
        }

    def _mock_external_train_result(self):
        return SimpleNamespace(
            model_path="mock-external-model.json",
            features=["ri_initial", "nurse_hppd"],
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
        row = body["results"][0]
        self.assertTrue(row["shadow_mode"])
        self.assertTrue(row["non_individual_use"])
        self.assertEqual(row["intended_use"], INTENDED_USE_SHADOW_AGGREGATE)
        self.assertTrue(row["flags"]["shadow_mode"])
        self.assertTrue(row["flags"]["non_individual_use"])
        self.assertEqual(row["status"], "shadow_only")
        self.assertIsNone(row["score"])
        self.assertIsNone(row["raw_score"])

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
            "summary": {"rows_requested": 10, "rows_scored": 10, "status_counts": {}},
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
