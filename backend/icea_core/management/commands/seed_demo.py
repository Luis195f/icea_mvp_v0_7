from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand

from icea_core.ml import train_xgb_regressor
from icea_core.models import Hospital, ModelArtifact, PatientEpisode, Unit
from icea_pipeline.models import EpisodeFeatureRow


class Command(BaseCommand):
    help = "Seed demo data and train a baseline ICEA model."

    def add_arguments(self, parser):
        parser.add_argument("--rows", type=int, default=800)
        parser.add_argument("--name", type=str, default="icea-demo")
        parser.add_argument("--version", type=str, default="v1")

    def handle(self, *args, **opts):
        rows = int(opts["rows"])
        name = str(opts["name"])
        version = str(opts["version"])

        hospital, _ = Hospital.objects.get_or_create(name="Demo Hospital")
        unit, _ = Unit.objects.get_or_create(hospital=hospital, name="Med-Surg")

        # Synthetic dataset: keep it simple but plausible.
        rng = np.random.default_rng(42)
        age = rng.integers(18, 95, size=rows)
        charlson = rng.integers(0, 8, size=rows)
        ri_initial = rng.normal(50, 12, size=rows).clip(0, 100)

        # Nursing exposures (features we will attribute):
        nurse_hppd = rng.normal(4.0, 1.2, size=rows).clip(0.5, 9.0)  # hours per patient day
        nurse_skillmix = rng.normal(0.65, 0.12, size=rows).clip(0.2, 0.95)  # RN proportion
        nurse_continuity = rng.normal(0.55, 0.18, size=rows).clip(0.0, 1.0)

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

        df = pd.DataFrame(
            {
                "age": age,
                "charlson": charlson,
                "ri_initial": ri_initial,
                "nurse_hppd": nurse_hppd,
                "nurse_skillmix": nurse_skillmix,
                "nurse_continuity": nurse_continuity,
                "medical_intensity": medical_intensity,
                "unit_occupancy": unit_occupancy,
                "delta_ri": (ri_final - ri_initial),
            }
        )

        features = [
            "age",
            "charlson",
            "ri_initial",
            "nurse_hppd",
            "nurse_skillmix",
            "nurse_continuity",
            "medical_intensity",
            "unit_occupancy",
        ]

        train_result = train_xgb_regressor(
            df,
            features=features,
            target="delta_ri",
            model_dir=settings.ICEA_MODEL_DIR,
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

        # Also seed a handful of PatientEpisode rows to show DB linkage.
        now = datetime.now(timezone.utc)
        for i in range(10):
            admit = now - timedelta(days=random.randint(1, 40))
            disch = admit + timedelta(days=random.randint(2, 9))
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
                    "features": {"ri_initial": float(ep.ri_initial), "proc_count": 0, "nurse_proc_count": 0},
                    "target": {"delta_ri": float(ep.delta_ri)},
                },
            )

        # Save a demo dataset file so the API can be tested quickly.
        data_dir = settings.BASE_DIR / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        demo_path = data_dir / "demo_dataset.json"
        demo_path.write_text(df.head(200).to_json(orient="records"), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS("Demo data seeded."))
        self.stdout.write(self.style.SUCCESS(f"Trained model: {artifact.id} ({artifact.name}:{artifact.version})"))
        self.stdout.write(self.style.SUCCESS(f"Demo dataset saved: {demo_path}"))
