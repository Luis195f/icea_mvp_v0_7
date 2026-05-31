from __future__ import annotations

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand

from icea_core.ml import train_xgb_regressor
from icea_core.models import ModelArtifact
from icea_pipeline.models import EpisodeFeatureRow, TrainingRun
from icea_pipeline.temporal import validate_temporal_frame


class Command(BaseCommand):
    help = "Train an ICEA predictive model from EpisodeFeatureRow table and register as ModelArtifact."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="icea-xgb")
        parser.add_argument("--version", default="v0.3")
        parser.add_argument("--target", default="delta_ri")

    def handle(self, *args, **opts):
        name = str(opts["name"])
        version = str(opts["version"])
        target = str(opts["target"])

        rows = list(EpisodeFeatureRow.objects.select_related("episode").all())
        dataset = []
        for r in rows:
            row = dict(r.features)
            row.update(r.target)
            dataset.append(row)

        if not dataset:
            self.stdout.write(self.style.WARNING("No EpisodeFeatureRow data found. Run build_dataset first."))
            return

        df = pd.DataFrame(dataset)
        temporal_issues = validate_temporal_frame(df, feature_names=[c for c in df.columns if c != target], target=target)
        if temporal_issues:
            self.stdout.write(
                self.style.ERROR(
                    f"Dataset not temporally defensible: {temporal_issues[0][1].status} ({len(temporal_issues)} rows blocked)"
                )
            )
            return
        metadata_cols = {"temporal_spec", "outcome_status", "feature_timestamps"}
        features = [c for c in df.columns if c != target and c not in metadata_cols]
        df_model = df[features + [target]].copy()

        result = train_xgb_regressor(
            df_model,
            features=features,
            target=target,
            model_dir=settings.ICEA_MODEL_DIR,
        )

        artifact = ModelArtifact.objects.create(
            name=name,
            version=version,
            target=result.target,
            features=result.features,
            model_type="xgboost",
            model_path=result.model_path,
            metrics=result.metrics,
        )

        TrainingRun.objects.create(dataset_rows=len(df), model_artifact_id=artifact.id)

        self.stdout.write(self.style.SUCCESS(f"Trained and registered model: {artifact.id} ({name}:{version})"))
