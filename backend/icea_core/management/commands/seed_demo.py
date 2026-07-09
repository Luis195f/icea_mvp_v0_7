from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from icea_core.evidence import build_training_evidence_metadata, summarize_model_evidence
from icea_core.ml import train_xgb_regressor
from icea_core.models import Hospital, ModelArtifact, PatientEpisode, Unit
from icea_pipeline.models import EpisodeFeatureRow
from icea_pipeline.temporal import build_temporal_spec, validate_temporal_frame


MIN_DEMO_ROWS = 40
DEMO_INDEX_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


class Command(BaseCommand):
    help = "Seed demo data and train a baseline ICEA model."

    def add_arguments(self, parser):
        parser.add_argument("--rows", type=int, default=800)
        parser.add_argument("--name", type=str, default="icea-demo")
        parser.add_argument("--model-version", dest="model_version", type=str, default="v1")

    def handle(self, *args, **opts):
        rows = int(opts["rows"])
        name = str(opts["name"])
        version = str(opts["model_version"])
        if rows < MIN_DEMO_ROWS:
            raise CommandError(
                f"seed_demo requires at least {MIN_DEMO_ROWS} rows for positive validation "
                "support and aggregate demo cells."
            )

        hospital, _ = Hospital.objects.get_or_create(name="Demo Hospital")
        unit, _ = Unit.objects.get_or_create(hospital=hospital, name="Med-Surg")

        # Synthetic dataset: keep it simple but plausible.
        rng = np.random.default_rng(42)
        age = rng.integers(18, 95, size=rows)
        charlson = rng.integers(0, 8, size=rows)
        frailty = rng.uniform(0.0, 1.0, size=rows)
        ri_initial = rng.normal(50, 12, size=rows).clip(0, 100)

        # Nursing exposures (features we will attribute):
        nurse_hppd = rng.normal(4.0, 1.2, size=rows).clip(0.5, 9.0)  # hours per patient day
        nurse_skillmix = rng.normal(0.65, 0.12, size=rows).clip(0.2, 0.95)  # RN proportion
        nurse_continuity = rng.normal(0.55, 0.18, size=rows).clip(0.0, 1.0)
        nurse_proc_count = rng.integers(1, 8, size=rows)
        nurse_proc_count_det = np.minimum(nurse_proc_count, rng.integers(1, 6, size=rows))

        # Confounders / other team effects:
        medical_intensity = rng.normal(0.0, 1.0, size=rows)
        unit_occupancy = rng.normal(0.85, 0.08, size=rows).clip(0.5, 1.1)

        # Outcome proxy (delta_ri): we enforce a positive marginal effect of nursing,
        # stronger at higher complexity (charlson) and lower initial status.
        complexity = (charlson / 7.0)
        nursing_effect = (0.9 * nurse_hppd + 6.0 * nurse_skillmix + 2.5 * nurse_continuity) * (0.6 + 0.8 * complexity)
        baseline_recovery = (60 - ri_initial) * 0.05
        delta_ri = baseline_recovery + 0.12 * nursing_effect + 0.8 * medical_intensity - 3.0 * (unit_occupancy - 0.85)
        delta_ri += rng.normal(0, 2.5, size=rows)

        ri_final = (ri_initial + delta_ri).clip(0, 100)
        temporal_specs = []
        for i in range(rows):
            index_time = DEMO_INDEX_TIME + timedelta(days=i)
            feature_end = index_time + timedelta(hours=12)
            spec = build_temporal_spec(
                index_time=index_time,
                feature_window_start=index_time,
                feature_window_end=feature_end,
                outcome_window_start=feature_end,
                outcome_window_end=feature_end + timedelta(hours=24),
            )
            spec["outcome_definition"] = "delta_ri"
            temporal_specs.append(spec)

        df = pd.DataFrame(
            {
                "age": age,
                "charlson": charlson,
                "frailty": frailty,
                "ri_initial": ri_initial,
                "nurse_hppd": nurse_hppd,
                "nurse_skillmix": nurse_skillmix,
                "nurse_continuity": nurse_continuity,
                "nurse_proc_count": nurse_proc_count,
                "nurse_proc_count_det": nurse_proc_count_det,
                "medical_intensity": medical_intensity,
                "unit_occupancy": unit_occupancy,
                "delta_ri": (ri_final - ri_initial),
                "temporal_spec": temporal_specs,
            }
        )

        features = [
            "age",
            "charlson",
            "frailty",
            "ri_initial",
            "nurse_hppd",
            "nurse_skillmix",
            "nurse_continuity",
            "nurse_proc_count",
            "nurse_proc_count_det",
            "medical_intensity",
            "unit_occupancy",
        ]
        temporal_issues = validate_temporal_frame(df, feature_names=features, target="delta_ri")
        if temporal_issues:
            raise CommandError(
                f"Generated demo data failed temporal governance: {temporal_issues[0][1].status}"
            )
        model_df = df[features + ["delta_ri"]].copy()

        train_result = train_xgb_regressor(
            model_df,
            features=features,
            target="delta_ri",
            model_dir=settings.ICEA_MODEL_DIR,
        )
        train_result.metrics["feature_stats"] = {
            "mean": model_df[features].mean(numeric_only=True).to_dict(),
            "std": model_df[features].std(numeric_only=True, ddof=0).replace(0, 1.0).to_dict(),
        }
        train_result.metrics["evidence_pack"] = build_training_evidence_metadata(
            raw_df=df,
            model_df=model_df,
            features=train_result.features,
            target=train_result.target,
            dataset_grain="synthetic_demo_episode",
            metrics=train_result.metrics,
            temporal_guardrail_status="passed",
        )

        artifact = ModelArtifact.objects.create(
            name=name,
            version=version,
            target=train_result.target,
            features=train_result.features,
            model_type="xgboost",
            model_path=train_result.model_path,
            metrics=train_result.metrics,
        )
        evidence = summarize_model_evidence(artifact)
        if not evidence.defensible:
            artifact.delete()
            raise CommandError(
                "Generated demo model did not satisfy the model evidence gate: "
                f"{','.join(evidence.missing_evidence)}"
            )

        # Seed enough governed episode rows to exercise aggregate suppression.
        for i in range(MIN_DEMO_ROWS):
            admit = DEMO_INDEX_TIME + timedelta(days=i)
            disch = admit + timedelta(days=2)
            ri0 = float(df.loc[i, "ri_initial"])
            rif = float(ri_final[i])
            ep = PatientEpisode.objects.create(
                unit=unit,
                external_patient_id=f"demo-{i:03d}",
                admission_date=admit,
                discharge_date=disch,
                ri_initial=ri0,
                ri_final=rif,
            )

            EpisodeFeatureRow.objects.update_or_create(
                episode=ep,
                defaults={
                    "features": {feature: float(df.loc[i, feature]) for feature in features},
                    "target": {
                        "delta_ri": float(ep.delta_ri),
                        "temporal_spec": temporal_specs[i],
                        "outcome_status": "defensible_fixed_horizon",
                    },
                },
            )

        # Save a demo dataset file so the API can be tested quickly.
        data_dir = Path(settings.ICEA_DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)
        demo_path = data_dir / "demo_dataset.json"
        demo_path.write_text(df.head(200).to_json(orient="records"), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS("Demo data seeded."))
        self.stdout.write(self.style.SUCCESS(f"Trained model: {artifact.id} ({artifact.name}:{artifact.version})"))
        self.stdout.write(self.style.SUCCESS("Governance: shadow_aggregate_research only; not clinically validated."))
        self.stdout.write(self.style.SUCCESS(f"Demo dataset saved: {demo_path}"))
