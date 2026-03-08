from __future__ import annotations

from django.apps import AppConfig


class IceaPipelineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "icea_pipeline"

    def ready(self):
        # Register lineage signals (always-on, non-breaking).
        try:
            from . import lineage  # noqa: F401
        except Exception:
            pass

        # Optional: django-simple-history integration (enterprise).
        try:
            import os

            if os.environ.get("ICEA_ENABLE_SIMPLE_HISTORY", "false").lower() in {"1", "true", "yes"}:
                from simple_history import register  # type: ignore

                from icea_core.models import Hospital, Unit, ModelArtifact
                from icea_pipeline.models import CausalSpec

                # Register only non-PHI configuration/base entities.
                for m in (Hospital, Unit, ModelArtifact, CausalSpec):
                    try:
                        register(m)
                    except Exception:
                        continue
        except Exception:
            # If dependency is missing, degrade gracefully.
            pass
