from __future__ import annotations

from datetime import timezone as dt_tz
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from icea_core.aggregation import aggregate_scored_rows
from icea_core.models import Hospital, PatientEpisode, Unit
from icea_pipeline.models import EpisodeFeatureRow, EpisodeWindow, EpisodeWindowFeatureRow, NormalizedObservation, RosterShift
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

    def _valid_target_trial(self):
        return {
            "time_zero": "window_start",
            "follow_up": {"horizon_hours": 12, "anchor": "time_zero", "mode": "fixed"},
            "eligibility": [],
            "estimand": "ATE",
        }

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

    def test_none_outcome_target_is_insufficient_outcome_evidence(self):
        issue = validate_temporal_row(
            {"delta_ri": None, "temporal_spec": self._spec()},
            feature_names=["ri_initial"],
            target="delta_ri",
        )
        self.assertIsNotNone(issue)
        self.assertEqual(issue.status, "insufficient_outcome_evidence")
        self.assertIn("missing_outcome_target", issue.warnings)
        self.assertTrue(issue.flags["insufficient_outcome_evidence"])

    def test_float_nan_outcome_target_is_insufficient_outcome_evidence(self):
        issue = validate_temporal_row(
            {"delta_ri": float("nan"), "temporal_spec": self._spec()},
            feature_names=["ri_initial"],
            target="delta_ri",
        )
        self.assertIsNotNone(issue)
        self.assertEqual(issue.status, "insufficient_outcome_evidence")
        self.assertIn("missing_outcome_target", issue.warnings)
        self.assertFalse(issue.flags["temporal_spec_valid"])

    def test_numpy_nan_outcome_target_is_insufficient_outcome_evidence_when_available(self):
        try:
            import numpy as np
        except Exception:
            self.skipTest("numpy unavailable")

        issue = validate_temporal_row(
            {"delta_ri": np.nan, "temporal_spec": self._spec()},
            feature_names=["ri_initial"],
            target="delta_ri",
        )
        self.assertIsNotNone(issue)
        self.assertEqual(issue.status, "insufficient_outcome_evidence")
        self.assertIn("missing_outcome_target", issue.warnings)

    def test_naive_feature_timestamp_with_aware_temporal_spec_does_not_type_error_and_blocks_future_feature(self):
        temporal_spec = {
            "temporal_spec_version": "icea_temporal_v1",
            "index_time": "2026-03-01T08:00:00Z",
            "feature_window_start": "2026-03-01T08:00:00Z",
            "feature_window_end": "2026-03-01T12:00:00Z",
            "outcome_window_start": "2026-03-01T12:00:00Z",
            "outcome_window_end": "2026-03-02T12:00:00Z",
            "censoring_reason": "not_censored",
            "outcome_status": "defensible_fixed_horizon",
        }
        issue = validate_temporal_row(
            {
                "delta_ri": 1.0,
                "temporal_spec": temporal_spec,
                "feature_timestamps": {
                    "vs_hr_last": timezone.datetime(2026, 3, 1, 13, 0),
                },
            },
            feature_names=["vs_hr_last"],
            target="delta_ri",
        )
        self.assertIsNotNone(issue)
        self.assertEqual(issue.status, "temporal_leakage_blocked")
        self.assertIn("future_feature_timestamps_blocked:vs_hr_last", issue.warnings)

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

    def test_valid_target_trial_without_dag_edge_proves_treatment_outcome_temporal_order(self):
        issue = validate_causal_temporal_order(
            {
                "treatment": "nurse_hppd",
                "outcome": "delta_ri",
                "confounders": [],
                "target_trial": self._valid_target_trial(),
            }
        )
        self.assertIsNone(issue)

    def test_empty_target_trial_without_dag_edge_is_blocked(self):
        issue = validate_causal_temporal_order(
            {
                "treatment": "nurse_hppd",
                "outcome": "delta_ri",
                "confounders": [],
                "target_trial": {},
            }
        )
        self.assertIsNotNone(issue)
        self.assertIn("insufficient_temporal_spec", issue.warnings)
        self.assertIn("treatment_outcome_temporal_order_not_proven", issue.warnings)

    def test_missing_target_trial_temporal_spec_and_dag_edge_still_blocks(self):
        issue = validate_causal_temporal_order(
            {
                "treatment": "nurse_hppd",
                "outcome": "delta_ri",
                "confounders": [],
                "dag_edges": [],
            }
        )
        self.assertIsNotNone(issue)
        self.assertIn("insufficient_temporal_spec", issue.warnings)
        self.assertIn("treatment_outcome_temporal_order_not_proven", issue.warnings)

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

    def test_roster_shift_hours_are_clipped_to_feature_window(self):
        admission = timezone.datetime(2026, 3, 1, 8, 0, tzinfo=dt_tz.utc)
        feature_end = admission + timezone.timedelta(hours=24)
        episode = PatientEpisode.objects.create(
            unit=self.unit,
            admission_date=admission,
            discharge_date=admission + timezone.timedelta(days=3),
            ri_initial=50.0,
            ri_final=60.0,
        )
        RosterShift.objects.create(
            unit=self.unit,
            start_dt=admission - timezone.timedelta(hours=4),
            end_dt=admission + timezone.timedelta(hours=4),
            rn_count=1,
            na_count=0,
            patient_census=2,
        )
        RosterShift.objects.create(
            unit=self.unit,
            start_dt=admission + timezone.timedelta(hours=6),
            end_dt=admission + timezone.timedelta(hours=10),
            rn_count=5,
            na_count=5,
            patient_census=1,
        )
        RosterShift.objects.create(
            unit=self.unit,
            start_dt=feature_end - timezone.timedelta(hours=6),
            end_dt=feature_end + timezone.timedelta(hours=6),
            rn_count=3,
            na_count=1,
            patient_census=1,
        )
        RosterShift.objects.create(
            unit=self.unit,
            start_dt=feature_end + timezone.timedelta(hours=1),
            end_dt=feature_end + timezone.timedelta(hours=5),
            rn_count=20,
            na_count=20,
            patient_census=1,
        )

        response = self.client.post("/api/v1/pipeline/build-dataset/", {"episode_id": episode.id}, format="json")
        self.assertEqual(response.status_code, 200)

        row = EpisodeFeatureRow.objects.get(episode=episode)
        expected_nurse_hours = (1.0 * 4.0) + (10.0 * 4.0) + (4.0 * 6.0)
        expected_rn_hours = (1.0 * 4.0) + (5.0 * 4.0) + (3.0 * 6.0)
        expected_patient_hours = (2.0 * 4.0) + (1.0 * 4.0) + (1.0 * 6.0)
        expected_hppd = expected_nurse_hours / (expected_patient_hours / 24.0)
        expected_skillmix = expected_rn_hours / expected_nurse_hours
        self.assertAlmostEqual(row.features["nurse_hppd"], expected_hppd, places=6)
        self.assertAlmostEqual(row.features["nurse_skillmix"], expected_skillmix, places=6)


class TemporalCausalDiscoverTests(TestCase):
    def setUp(self):
        self.dev_env = mock.patch.dict(
            "os.environ",
            {"ICEA_DEV_ALLOW_INSECURE": "true", "ICEA_CAUSAL_DISCOVER_ENABLED": "true"},
            clear=False,
        )
        self.dev_env.start()
        self.addCleanup(self.dev_env.stop)
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="temporal-discover-admin", password="test-pass", is_superuser=True)
        self.client.force_authenticate(user=self.user)

    def _rows(self, *, include_delta_ri: bool = False, include_temporal_spec: bool = False):
        rows = []
        for i in range(12):
            row = {
                "row_id": f"row-{i}",
                "ri_initial": 40.0 + float(i),
                "nurse_hppd": 3.0 + float(i % 4),
                "proc_count": 1.0 + float(i % 3),
            }
            if include_delta_ri:
                row["delta_ri"] = 0.5 + float(i) * 0.1
            if include_temporal_spec:
                row["temporal_spec"] = build_temporal_spec(
                    index_time=timezone.datetime(2026, 3, 1, 8, 0, tzinfo=dt_tz.utc),
                    feature_window_start=timezone.datetime(2026, 3, 1, 8, 0, tzinfo=dt_tz.utc),
                    feature_window_end=timezone.datetime(2026, 3, 1, 12, 0, tzinfo=dt_tz.utc),
                    outcome_window_start=timezone.datetime(2026, 3, 1, 12, 0, tzinfo=dt_tz.utc),
                    outcome_window_end=timezone.datetime(2026, 3, 2, 12, 0, tzinfo=dt_tz.utc),
                )
            rows.append(row)
        return rows

    def test_causal_discover_from_external_rows_without_delta_ri_does_not_fail_missing_outcome_target(self):
        response = self.client.post(
            "/api/v1/causal/discover/",
            {
                "from_db": False,
                "variables": ["ri_initial", "nurse_hppd", "proc_count"],
                "rows": self._rows(),
                "max_cond_set": 1,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertNotEqual(result.get("available"), False)
        self.assertNotIn("missing_outcome_target", result.get("warnings", []))
        self.assertIn("n_rows", result)

    def test_causal_discover_with_declared_outcome_and_missing_target_blocks_controlled(self):
        response = self.client.post(
            "/api/v1/causal/discover/",
            {
                "from_db": False,
                "variables": ["ri_initial", "nurse_hppd", "proc_count"],
                "outcome": "delta_ri",
                "rows": self._rows(include_temporal_spec=True),
                "max_cond_set": 1,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["available"])
        self.assertIn("missing_outcome_target", result["warnings"])


class TemporalCausalRunWindowTests(TestCase):
    def setUp(self):
        self.dev_env = mock.patch.dict("os.environ", {"ICEA_DEV_ALLOW_INSECURE": "true"}, clear=False)
        self.dev_env.start()
        self.addCleanup(self.dev_env.stop)
        self.client = APIClient()
        hospital = Hospital.objects.create(name="Hospital Causal Window")
        self.unit = Unit.objects.create(hospital=hospital, name="UCI")

    def _target_trial_spec(self):
        return {
            "time_zero": "window_start",
            "follow_up": {"horizon_hours": 12, "anchor": "time_zero", "mode": "fixed"},
            "eligibility": [],
            "estimand": "ATE",
        }

    def _causal_spec(self):
        return {
            "grain": "window",
            "treatment": "nurse_hppd",
            "outcome": "delta_ri",
            "confounders": ["ri_initial", "proc_count"],
            "effect_modifiers": ["ri_initial"],
            "dag_edges": [
                ["ri_initial", "nurse_hppd"],
                ["ri_initial", "delta_ri"],
                ["proc_count", "nurse_hppd"],
                ["proc_count", "delta_ri"],
                ["nurse_hppd", "delta_ri"],
            ],
            "target_trial": self._target_trial_spec(),
            "n_estimators": 20,
        }

    def _create_window_rows(self, *, bad_temporal_spec: bool = False):
        base = timezone.datetime(2026, 3, 1, 8, 0, tzinfo=dt_tz.utc)
        for i in range(12):
            admission = base + timezone.timedelta(days=i)
            episode = PatientEpisode.objects.create(
                unit=self.unit,
                admission_date=admission,
                discharge_date=admission + timezone.timedelta(days=3),
                ri_initial=50.0 + float(i),
                ri_final=55.0 + float(i),
            )
            ws = admission
            we = ws + timezone.timedelta(hours=12)
            window = EpisodeWindow.objects.create(episode=episode, window_index=0, start_dt=ws, end_dt=we)
            target = {"delta_ri": 999.0}
            if bad_temporal_spec:
                target["temporal_spec"] = build_temporal_spec(
                    index_time=ws,
                    feature_window_start=ws,
                    feature_window_end=we,
                    outcome_window_start=ws,
                    outcome_window_end=we,
                )
            EpisodeWindowFeatureRow.objects.create(
                window=window,
                features={
                    "ri_initial": 50.0 + float(i),
                    "proc_count": 1.0 + float(i % 3),
                    "nurse_hppd": 3.0 + float(i % 4),
                },
                target=target,
                schema_hash=f"window-causal-{i}",
                feature_version="v-test-window",
            )
            NormalizedObservation.objects.create(
                episode=episode,
                code_system="LOINC",
                code="85556-9",
                display="Rothman Index Calculated",
                value_num=50.0 + float(i),
                effective_dt=ws,
            )
            NormalizedObservation.objects.create(
                episode=episode,
                code_system="LOINC",
                code="85556-9",
                display="Rothman Index Calculated",
                value_num=60.0 + float(i),
                effective_dt=we,
            )

    def test_causal_window_followup_does_not_use_feature_window_start_as_outcome_start(self):
        self._create_window_rows()
        response = self.client.post("/api/v1/causal/run/", {"spec": self._causal_spec()}, format="json")

        self.assertEqual(response.status_code, 200)
        summary = response.json()["summary"]
        self.assertFalse(summary["causal_available"])
        self.assertEqual(summary["status"], "insufficient_outcome_evidence")
        self.assertIn("missing_outcome_target", summary["warnings"])
        self.assertNotEqual(summary.get("ate"), 0.0)

    def test_causal_window_explicit_overlapping_outcome_window_is_blocked(self):
        self._create_window_rows(bad_temporal_spec=True)
        response = self.client.post("/api/v1/causal/run/", {"spec": self._causal_spec()}, format="json")

        self.assertEqual(response.status_code, 200)
        summary = response.json()["summary"]
        self.assertFalse(summary["causal_available"])
        self.assertEqual(summary["status"], "temporal_leakage_blocked")
        self.assertIn("feature_window_end_after_outcome_window_start", summary["warnings"])
