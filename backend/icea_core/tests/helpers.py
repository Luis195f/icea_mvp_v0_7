from __future__ import annotations

from pathlib import Path

import pandas as pd
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from icea_core.ml import train_xgb_regressor
from icea_core.models import Hospital, ModelArtifact, PatientEpisode, Unit
from icea_pipeline.models import (
    EpisodeFeatureRow,
    EpisodeWindow,
    EpisodeWindowFeatureRow,
    NormalizedProcedure,
)


class ICEAPlusFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        super_method = getattr(super(), "setUpTestData", None)
        if callable(super_method):
            super_method()

        cls.hospital = Hospital.objects.create(name="Hospital Test")
        cls.unit = Unit.objects.create(hospital=cls.hospital, name="UCI")
        cls.now = timezone.now()

        cls.episode_train_df = cls._build_episode_frame(40)
        cls.window_train_df = cls._build_window_frame(24)
        cls.episode_artifact = cls._create_artifact("icea-plus-episode", "v-test-episode", cls.episode_train_df)
        cls.window_artifact = cls._create_artifact("icea-plus-window", "v-test-window", cls.window_train_df)

        cls.episodes = []
        for i, row in cls.episode_train_df.head(12).iterrows():
            episode = PatientEpisode.objects.create(
                unit=cls.unit,
                external_patient_id=f"PAT-{i}",
                admission_date=cls.now - timezone.timedelta(days=20 - i),
                discharge_date=cls.now - timezone.timedelta(days=19 - i),
                ri_initial=float(row["ri_initial"]),
                ri_final=float(row["ri_initial"] + row["delta_ri"]),
            )
            EpisodeFeatureRow.objects.create(
                episode=episode,
                features={k: float(v) for k, v in row.drop(labels=["delta_ri"]).to_dict().items()},
                target={"delta_ri": float(row["delta_ri"])},
                schema_hash=f"episode-{i}",
                feature_version="v-test",
            )
            for j in range(int(row["nurse_proc_count"])):
                NormalizedProcedure.objects.create(
                    episode=episode,
                    code_system="SNOMED",
                    code=f"PROC-{i}-{j}",
                    display="Nursing procedure",
                    performer_role="registered nurse",
                    performer_actor_ref=f"Practitioner/nurse-{j % 2}",
                    performer_actor_type="Practitioner",
                    is_nursing=True,
                    nursing_label_method="deterministic" if j < int(row["nurse_proc_count_det"]) else "heuristic",
                    performed_dt=episode.admission_date + timezone.timedelta(hours=j + 1),
                )
            cls.episodes.append(episode)

        cls.windows = []
        for i, row in cls.window_train_df.head(12).iterrows():
            episode = cls.episodes[i % len(cls.episodes)]
            start_dt = episode.admission_date + timezone.timedelta(hours=(i % 2) * 12)
            end_dt = start_dt + timezone.timedelta(hours=12)
            window = EpisodeWindow.objects.create(
                episode=episode,
                window_index=i,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            EpisodeWindowFeatureRow.objects.create(
                window=window,
                features={k: float(v) for k, v in row.drop(labels=["delta_ri"]).to_dict().items()},
                target={"delta_ri": float(row["delta_ri"])},
                schema_hash=f"window-{i}",
                feature_version="v-test-window",
            )
            for j in range(int(max(row["nurse_proc_count"], 1))):
                NormalizedProcedure.objects.create(
                    episode=episode,
                    code_system="SNOMED",
                    code=f"WIN-{i}-{j}",
                    display="Nursing procedure window",
                    performer_role="registered nurse",
                    performer_actor_ref=f"Practitioner/nurse-{(i + j) % 3}",
                    performer_actor_type="Practitioner",
                    is_nursing=True,
                    nursing_label_method="deterministic",
                    performed_dt=start_dt + timezone.timedelta(hours=j + 1),
                )
            cls.windows.append(window)

        user_model = get_user_model()
        cls.regular_user = user_model.objects.create_user(username="regular", password="test-pass")
        cls.admin_user = user_model.objects.create_user(username="admin", password="test-pass", is_staff=True, is_superuser=True)

    @classmethod
    def _build_episode_frame(cls, n_rows: int) -> pd.DataFrame:
        rows = []
        for i in range(n_rows):
            ri_initial = 45.0 + float(i % 18)
            proc_count = 2.0 + float(i % 4)
            nurse_proc_count = 1.0 + float(i % 3)
            nurse_proc_count_det = 1.0 if i % 2 == 0 else 0.0
            nurse_hppd = 3.0 + float(i % 5) * 0.4
            nurse_skillmix = 0.55 + float(i % 4) * 0.05
            vs_hr_last = 70.0 + float(i % 10)
            vs_sbp_last = 110.0 + float(i % 7) * 2.0
            missing_hr = 1.0 if i % 9 == 0 else 0.0
            missing_sbp = 1.0 if i % 11 == 0 else 0.0
            delta_ri = (
                0.08 * ri_initial
                + 1.25 * nurse_hppd
                + 0.85 * nurse_skillmix
                + 0.60 * nurse_proc_count_det
                - 1.10 * missing_hr
                - 0.85 * missing_sbp
            )
            rows.append(
                {
                    "ri_initial": ri_initial,
                    "proc_count": proc_count,
                    "nurse_proc_count": nurse_proc_count,
                    "nurse_proc_count_det": nurse_proc_count_det,
                    "nurse_hppd": nurse_hppd,
                    "nurse_skillmix": nurse_skillmix,
                    "vs_hr_last": vs_hr_last,
                    "vs_sbp_last": vs_sbp_last,
                    "missing_vs_hr_last": missing_hr,
                    "missing_vs_sbp_last": missing_sbp,
                    "missing_loinc_8867_4": missing_hr,
                    "missing_loinc_8480_6": missing_sbp,
                    "delta_ri": delta_ri,
                }
            )
        return pd.DataFrame(rows)

    @classmethod
    def _build_window_frame(cls, n_rows: int) -> pd.DataFrame:
        rows = []
        for i in range(n_rows):
            base = cls._build_episode_frame(n_rows).iloc[i]
            missing_t0 = 1.0 if i % 8 == 0 else 0.0
            missing_t1 = 1.0 if i % 10 == 0 else 0.0
            delta_ri = float(base["delta_ri"] - 0.7 * missing_t0 - 0.5 * missing_t1)
            rows.append(
                {
                    "ri_initial": float(base["ri_initial"]),
                    "proc_count": float(base["proc_count"]),
                    "nurse_proc_count": float(base["nurse_proc_count"]),
                    "nurse_proc_count_det": float(base["nurse_proc_count_det"]),
                    "nurse_hppd": float(base["nurse_hppd"]),
                    "nurse_skillmix": float(base["nurse_skillmix"]),
                    "vs_hr_last": float(base["vs_hr_last"]),
                    "vs_sbp_last": float(base["vs_sbp_last"]),
                    "missing_vs_hr_last": float(base["missing_vs_hr_last"]),
                    "missing_vs_sbp_last": float(base["missing_vs_sbp_last"]),
                    "missing_loinc_8867_4": float(base["missing_loinc_8867_4"]),
                    "missing_loinc_8480_6": float(base["missing_loinc_8480_6"]),
                    "window_index": float(i),
                    "window_hours": 12.0,
                    "missing_loinc_85556_9_t0": missing_t0,
                    "missing_loinc_85556_9_t1": missing_t1,
                    "missing_delta_ri": 1.0 if (missing_t0 or missing_t1) else 0.0,
                    "delta_ri": delta_ri,
                }
            )
        return pd.DataFrame(rows)

    @classmethod
    def _create_artifact(cls, name: str, version: str, frame: pd.DataFrame) -> ModelArtifact:
        features = [c for c in frame.columns if c != "delta_ri"]
        Path(settings.ICEA_MODEL_DIR).mkdir(parents=True, exist_ok=True)
        result = train_xgb_regressor(
            frame,
            features=features,
            target="delta_ri",
            model_dir=settings.ICEA_MODEL_DIR,
            params={"n_estimators": 20, "max_depth": 3, "learning_rate": 0.1, "subsample": 1.0, "colsample_bytree": 1.0},
        )
        metrics = dict(result.metrics)
        metrics["feature_stats"] = {
            "mean": frame[features].mean(numeric_only=True).to_dict(),
            "std": frame[features].std(numeric_only=True, ddof=0).replace(0, 1.0).to_dict(),
        }
        return ModelArtifact.objects.create(
            name=name,
            version=version,
            target=result.target,
            features=result.features,
            model_type="xgboost",
            model_path=result.model_path,
            metrics=metrics,
        )
