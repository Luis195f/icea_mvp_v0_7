from __future__ import annotations

from datetime import timezone as dt_tz
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from icea_core.aggregation import aggregate_scored_rows
from icea_core.models import Hospital, PatientEpisode, Unit
from icea_pipeline.models import EpisodeFeatureRow, NormalizedObservation
from icea_pipeline.temporal import (
    build_temporal_spec,
    validate_case_mix_spec,
    validate_causal_temporal_order,
    validate_temporal_row,
)


class TemporalGuardrailUnitTests(SimpleTestCase):
    def _spec(self, *, feature_end=None, outcome_start=None, outcome_status="defensible_fixed_horizon"):
        index = timezone.datetime(2026, 3, 1, 8, 0, tzinfo=dt_tz.utc)
        feature_start = index
        feature_end = feature_end or timezone.datetime(2026, 3, 1, 12, 0, tzinfo=dt_tz.utc)
        outcome_start = outcome_start or feature_end
        outcome_end = outcome_start + timezone.timedelta(hours=12)
        spec = build_temporal_spec(
            index_time=index,
            feature_window_start=feature_start,
            feature_window_end=feature_end,
            outcome_window_start=outcome_start,
            outcome_window_end=outcome_end,
        )
        spec["outcome_status"] = outcome_status
        return spec

    def test_missing_index_time_blocks_defensible_scoring(self):
        issue = validate_temporal_row({"delta_ri": 1.0}, feature_names=["ri_initial"], target="delta_ri")
        self.assertIsNotNone(issue)
        self.assertEqual(issue.status, "insufficient_temporal_spec")

    def test_feature_window_after_outcome_start_blocks_leakage(self):
        issue = validate_temporal_row(
            {
                "delta_ri": 1.0,
                "temporal_spec": self._spec(
                    feature_end=timezone.datetime(2026, 3, 1, 16, 0, tzinfo=dt_tz.utc),
                    outcome_start=timezone.datetime(2026, 3, 1, 12, 0, tzinfo=dt_tz.utc),
                ),
            },
            feature_names=["ri_initial"],
            target="delta_ri",
        )
        self.assertEqual(issue.status, "temporal_leakage_blocked")

    def test_legacy_last_measurement_outcome_is_not_defensible(self):
        issue = validate_temporal_row(
            {
                "delta_ri": 1.0,
                "temporal_spec": self._spec(outcome_status="legacy_outcome_not_defensible"),
            },
            feature_names=["ri_initial"],
            target="delta_ri",
        )
        self.assertEqual(issue.status, "legacy_outcome_not_defensible")

    def test_same_window_exposure_outcome_without_lag_is_blocked(self):
        issue = validate_temporal_row(
            {
                "delta_ri": 1.0,
                "temporal_spec": self._spec(
                    feature_end=timezone.datetime(2026, 3, 1, 20, 0, tzinfo=dt_tz.utc),
                    outcome_start=timezone.datetime(2026, 3, 1, 8, 0, tzinfo=dt_tz.utc),
                ),
            },
            feature_names=["nurse_hppd"],
            target="delta_ri",
        )
        self.assertEqual(issue.status, "temporal_leakage_blocked")

    def test_case_mix_spec_absent_is_marked_insufficient(self):
        issue = validate_case_mix_spec(None)
        self.assertEqual(issue.status, "case_mix_insufficient")

    def test_causal_post_treatment_confounder_is_unavailable(self):
        issue = validate_causal_temporal_order(
            {
                "treatment": "nurse_hppd",
                "outcome": "delta_ri",
                "confounders": ["proc_count"],
                "dag_edges": [["nurse_hppd", "proc_count"], ["nurse_hppd", "delta_ri"]],
                "target_trial": {"time_zero": "window_start"},
            }
        )
        self.assertIsNotNone(issue)
        self.assertFalse(issue.flags["causal_available"])
        self.assertIn("confounder_post_treatment:proc_count", issue.warnings)

    def test_unit_aggregation_without_case_mix_warns_not_comparable(self):
        rows = []
        for i in range(10):
            rows.append(
                {
                    "row_id": f"r{i}",
                    "episode_id": i,
                    "unit_id": 1,
                    "score": 70.0,
                    "status": "complete",
                    "warnings": [],
                    "components": {},
                    "aggregation": {"severity_weight": 1.0, "effective_exposure_share": 1.0},
                }
            )
        aggregated = aggregate_scored_rows(rows=rows, group_by="unit", epsilon=1e-6, enforce_suppression=False)
        self.assertIn("no_comparable_without_case_mix", aggregated[0]["warnings"])


class TemporalBuildDatasetTests(TestCase):
    def setUp(self):
        self.dev_env = mock.patch.dict("os.environ", {"ICEA_DEV_ALLOW_INSECURE": "true"}, clear=False)
        self.dev_env.start()
        self.addCleanup(self.dev_env.stop)
        self.client = APIClient()
        hospital = Hospital.objects.create(name="Hospital Temporal")
        self.unit = Unit.objects.create(hospital=hospital, name="UCI")

    def test_future_observation_is_excluded_and_legacy_outcome_is_flagged(self):
        admission = timezone.datetime(2026, 3, 1, 8, 0, tzinfo=dt_tz.utc)
        episode = PatientEpisode.objects.create(
            unit=self.unit,
            admission_date=admission,
            discharge_date=admission + timezone.timedelta(days=3),
            ri_initial=50.0,
            ri_final=60.0,
        )
        NormalizedObservation.objects.create(
            episode=episode,
            code_system="LOINC",
            code="8867-4",
            display="Heart rate",
            value_num=77.0,
            effective_dt=admission + timezone.timedelta(hours=2),
        )
        NormalizedObservation.objects.create(
            episode=episode,
            code_system="LOINC",
            code="8867-4",
            display="Heart rate",
            value_num=99.0,
            effective_dt=admission + timezone.timedelta(days=2),
        )

        response = self.client.post("/api/v1/pipeline/build-dataset/", {"episode_id": episode.id}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "legacy_outcome_not_defensible")

        row = EpisodeFeatureRow.objects.get(episode=episode)
        self.assertEqual(row.features["vs_hr_last"], 77.0)
        self.assertEqual(row.target["outcome_status"], "legacy_outcome_not_defensible")
