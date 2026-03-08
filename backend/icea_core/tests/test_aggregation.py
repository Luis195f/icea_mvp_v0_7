from django.test import SimpleTestCase

from icea_core.aggregation import aggregate_scored_rows


class ICEAPlusAggregationTests(SimpleTestCase):
    def _row(self, *, row_id, score, group, severity=1.0, share=1.0, status="complete"):
        return {
            "row_id": row_id,
            "patient_key": group,
            "episode_id": 1,
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
        aggregated = aggregate_scored_rows(rows=rows, group_by="unit", epsilon=1e-6)
        self.assertEqual(len(aggregated), 1)
        self.assertAlmostEqual(aggregated[0]["score"], (60.0 * 1.0 + 80.0 * 2.0) / 3.0, places=3)

    def test_aggregation_by_shift_is_stable_with_small_denominator(self):
        rows = [self._row(row_id="r1", score=70.0, group="p1", severity=0.0001, share=0.0001)]
        aggregated = aggregate_scored_rows(rows=rows, group_by="shift", epsilon=1e-6)
        self.assertEqual(len(aggregated), 1)
        self.assertIsNotNone(aggregated[0]["score"])

    def test_aggregation_by_professional_respects_exposure_share(self):
        rows = [
            self._row(row_id="r1", score=90.0, group="nurse-a", severity=1.0, share=0.75),
            self._row(row_id="r2", score=50.0, group="nurse-a", severity=1.0, share=0.25),
        ]
        aggregated = aggregate_scored_rows(rows=rows, group_by="patient", epsilon=1e-6)
        self.assertEqual(len(aggregated), 1)
        self.assertAlmostEqual(aggregated[0]["score"], 80.0, places=3)
