from django.test import SimpleTestCase

from icea_core.aggregation import aggregate_scored_rows


class ICEAPlusAggregationTests(SimpleTestCase):
    def _row(self, *, row_id, score, group, severity=1.0, share=1.0, status="complete", episode_id=1):
        return {
            "row_id": row_id,
            "patient_key": group,
            "episode_id": episode_id,
            "window_id": None,
            "unit_id": 10,
            "start_dt": "2026-03-01T08:00:00+00:00",
            "end_dt": "2026-03-01T20:00:00+00:00",
            "score": score,
            "status": status,
            "warnings": [],
            "components": {
                "benefit": {"normalized": 0.2},
                "attribution": {"normalized": 0.3},
                "causal": {"normalized": 0.4},
                "quality": {"normalized": 0.5},
                "uncertainty": {"normalized": 0.1},
            },
            "aggregation": {
                "severity_weight": severity,
                "effective_exposure_share": share,
            },
        }

    def test_aggregation_by_unit_uses_weighted_mean(self):
        rows = [
            self._row(row_id="r1", score=60.0, group="p1", severity=1.0),
            self._row(row_id="r2", score=80.0, group="p2", severity=2.0),
        ]
        aggregated = aggregate_scored_rows(rows=rows, group_by="unit", epsilon=1e-6, enforce_suppression=False)
        self.assertEqual(len(aggregated), 1)
        self.assertAlmostEqual(aggregated[0]["score"], (60.0 * 1.0 + 80.0 * 2.0) / 3.0, places=3)

    def test_aggregation_by_shift_is_stable_with_small_denominator(self):
        rows = [self._row(row_id="r1", score=70.0, group="p1", severity=0.0001, share=0.0001)]
        aggregated = aggregate_scored_rows(rows=rows, group_by="shift", epsilon=1e-6, enforce_suppression=False)
        self.assertEqual(len(aggregated), 1)
        self.assertIsNotNone(aggregated[0]["score"])

    def test_aggregation_by_professional_respects_exposure_share(self):
        rows = [
            self._row(row_id="r1", score=90.0, group="nurse-a", severity=1.0, share=0.75),
            self._row(row_id="r2", score=50.0, group="nurse-a", severity=1.0, share=0.25),
        ]
        aggregated = aggregate_scored_rows(rows=rows, group_by="patient", epsilon=1e-6, enforce_suppression=False)
        self.assertEqual(len(aggregated), 1)
        self.assertAlmostEqual(aggregated[0]["score"], 80.0, places=3)

    def test_low_support_cell_suppresses_score_without_zero_fill(self):
        rows = [self._row(row_id=f"r{i}", score=70.0, group="p", episode_id=i) for i in range(9)]
        aggregated = aggregate_scored_rows(rows=rows, group_by="unit", epsilon=1e-6)
        self.assertEqual(len(aggregated), 1)
        self.assertEqual(aggregated[0]["status"], "suppressed_low_support")
        self.assertIsNone(aggregated[0]["score"])
        self.assertTrue(aggregated[0]["suppressed"])
        self.assertIn("n_observations_below_min_cell_count", aggregated[0]["warnings"])

    def test_provisional_rows_do_not_contribute_numeric_aggregate_score(self):
        rows = [self._row(row_id=f"r{i}", score=70.0, group="p", status="provisional", episode_id=i) for i in range(10)]
        aggregated = aggregate_scored_rows(rows=rows, group_by="unit", epsilon=1e-6)
        self.assertEqual(len(aggregated), 1)
        self.assertEqual(aggregated[0]["status"], "insufficient_evidence")
        self.assertIsNone(aggregated[0]["score"])
        self.assertEqual(aggregated[0]["status_counts"]["provisional"], 10)

    def test_staff_dimension_requires_minimum_staff_support(self):
        rows = []
        for i in range(10):
            row = self._row(row_id=f"r{i}", score=70.0, group="p", episode_id=i)
            row["aggregation"]["nurse_shares"] = {"nurse-a": 1.0, "nurse-b": 0.5}
            rows.append(row)
        aggregated = aggregate_scored_rows(rows=rows, group_by="shift", epsilon=1e-6, require_staff_count=True)
        self.assertEqual(len(aggregated), 1)
        self.assertEqual(aggregated[0]["status"], "suppressed_low_support")
        self.assertIsNone(aggregated[0]["score"])
        self.assertIn("n_staff_below_min_staff_count", aggregated[0]["warnings"])
