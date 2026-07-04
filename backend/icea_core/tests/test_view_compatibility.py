from __future__ import annotations

from django.test import SimpleTestCase

from icea_pipeline.views import FHIREpisodeQualityView, FHIROpisodeQualityView


class ViewCompatibilityTests(SimpleTestCase):
    def test_fhir_episode_quality_typo_alias_remains_compatible(self):
        self.assertIs(FHIROpisodeQualityView, FHIREpisodeQualityView)
