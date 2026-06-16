from __future__ import annotations

import json

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand

from icea_core.evidence import build_training_evidence_metadata, summarize_model_evidence
from icea_core.ml import train_xgb_regressor
from icea_core.models import ModelArtifact
from icea_pipeline.audit import append_audit_event
from icea_pipeline.models import EpisodeFeatureRow, TrainingRun
from icea_pipeline.temporal import validate_temporal_frame


class Command(BaseCommand):
    help = "Train an ICEA predictive model from EpisodeFeatureRow table and register as ModelArtifact."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="icea-xgb")
        parser.add_argument("--version", default="v0.3")
        parser.add_argument("--target", default="delta_ri")
        parser.add_argument(
            "--case-mix-spec-json",
            default="",
            help="Optional JSON case_mix_spec with required domains for model evidence governance.",
        )

    def handle(self, *args, **opts):
        name = str(opts["name"])
        version = str(opts["version"])
        target = str(opts["target"])
        case_mix_spec = None
        if str(opts.get("case_mix_spec_json") or "").strip():
            try:
                parsed_case_mix_spec = json.loads(str(opts["case_mix_spec_json"]))
            except json.JSONDecodeError as exc:
                self.stdout.write(self.style.ERROR(f"Invalid --case-mix-spec-json: {exc}"))
                return
            if not isinstance(parsed_case_mix_spec, dict):
                self.stdout.write(self.style.ERROR("--case-mix-spec-json must decode to an object"))
                return
            case_mix_spec = parsed_case_mix_spec

        rows = list(EpisodeFeatureRow.objects.select_related("episode").all())
        dataset = []
        for r in rows:
            row = dict(r.features)
            row.update(r.target)
            dataset.append(row)

        append_audit_event(
            event_type="model_train_requested",
            payload={"action": "train", "row_count": int(len(dataset)), "status": "requested"},
            context="management/train_from_db",
            actor="management_command",
        )
        if not dataset:
            append_audit_event(
                event_type="model_train_blocked",
                payload={"action": "train", "row_count": 0, "status": "blocked", "error_code": "dataset_empty"},
                context="management/train_from_db",
                actor="management_command",
            )
            self.stdout.write(self.style.WARNING("No EpisodeFeatureRow data found. Run build_dataset first."))
            return

        df = pd.DataFrame(dataset)
        temporal_issues = validate_temporal_frame(df, feature_names=[c for c in df.columns if c != target], target=target)
        if temporal_issues:
            append_audit_event(
                event_type="model_train_blocked",
                payload={
                    "action": "train",
                    "row_count": int(len(df)),
                    "status": "blocked",
                    "error_code": "dataset_not_temporally_defensible",
                },
                context="management/train_from_db",
                actor="management_command",
            )
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
        result.metrics["evidence_pack"] = build_training_evidence_metadata(
            raw_df=df,
            model_df=df_model,
            features=result.features,
            target=result.target,
            dataset_grain="episode",
            metrics=result.metrics,
            temporal_guardrail_status="passed",
            case_mix_spec=case_mix_spec,
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
        evidence = summarize_model_evidence(artifact)
        if not evidence.defensible:
            artifact.governance_status = "quarantine"
            artifact.save(update_fields=["governance_status"])
            evidence = summarize_model_evidence(artifact)

        TrainingRun.objects.create(dataset_rows=len(df), model_artifact_id=artifact.id)
        append_audit_event(
            event_type="model_train_completed",
            payload={
                "action": "train",
                "model_id": str(artifact.id),
                "row_count": int(len(df)),
                "status": "completed" if evidence.defensible else "quarantine",
            },
            context="management/train_from_db",
            actor="management_command",
        )

        self.stdout.write(self.style.SUCCESS(f"Trained and registered model: {artifact.id} ({name}:{version})"))
