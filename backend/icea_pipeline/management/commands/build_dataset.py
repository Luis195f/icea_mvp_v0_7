from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_datetime

from icea_core.models import PatientEpisode
from icea_pipeline.audit import append_audit_event
from icea_pipeline.models import EpisodeFeatureRow, NormalizedObservation, NormalizedProcedure
from icea_pipeline.temporal import LEGACY_OUTCOME_STATUS, episode_legacy_temporal_spec


VITAL_LOINC = {
    "8867-4": "hr",  # Heart rate
    "8480-6": "sbp",
    "8462-4": "dbp",
    "8310-5": "temp",
    "59408-5": "spo2",
    "9279-1": "resp_rate",
}


def _safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


class Command(BaseCommand):
    help = "Build a tabular ML dataset (one row per PatientEpisode) from normalized tables."

    def add_arguments(self, parser):
        parser.add_argument("--episode-id", help="If provided, build only for this episode id")
        parser.add_argument(
            "--truncate",
            action="store_true",
            help="Delete existing EpisodeFeatureRow(s) before rebuilding",
        )

    def handle(self, *args, **opts):
        episode_id = opts.get("episode_id")
        truncate = bool(opts.get("truncate"))

        qs = PatientEpisode.objects.all().order_by("id")
        if episode_id:
            qs = qs.filter(id=int(episode_id))

        with transaction.atomic():
            if truncate:
                if episode_id:
                    EpisodeFeatureRow.objects.filter(episode_id=int(episode_id)).delete()
                else:
                    EpisodeFeatureRow.objects.all().delete()

            built = 0
            for ep in qs:
                temporal_spec = episode_legacy_temporal_spec(ep)
                feature_window_end = parse_datetime(str(temporal_spec["feature_window_end"]))
                features = {"temporal_spec": temporal_spec}

                # Baseline (from episode table) — minimal but stable
                features["ri_initial"] = float(ep.ri_initial)

                # Outcomes
                target = {
                    "delta_ri": float(ep.delta_ri),
                    "temporal_spec": temporal_spec,
                    "outcome_status": LEGACY_OUTCOME_STATUS,
                }

                # Nursing exposure proxy (from procedures)
                procs = NormalizedProcedure.objects.filter(episode=ep, performed_dt__lte=feature_window_end)
                nurse_like = procs.filter(performer_role__iregex=r"(nurs|rn)")

                features["proc_count"] = procs.count()
                features["nurse_proc_count"] = nurse_like.count()

                # Vital sign features (LOINC) — last value during episode
                obs = NormalizedObservation.objects.filter(
                    episode=ep,
                    code_system__icontains="loinc",
                    effective_dt__lte=feature_window_end,
                )
                last_by_code = {}
                for o in obs.exclude(value_num__isnull=True).order_by("effective_dt"):
                    if o.code:
                        last_by_code[o.code] = o.value_num

                for loinc, name in VITAL_LOINC.items():
                    v = last_by_code.get(loinc)
                    if v is not None:
                        features[f"vs_{name}_last"] = float(v)
                    else:
                        features[f"vs_{name}_last"] = 0.0

                EpisodeFeatureRow.objects.update_or_create(
                    episode=ep,
                    defaults={"features": features, "target": target},
                )
                built += 1

        append_audit_event(
            event_type="build_dataset",
            payload={"action": "build_dataset", "row_count": int(built), "status": "completed"},
            context="management/build_dataset",
            actor="management_command",
        )
        self.stdout.write(self.style.SUCCESS(f"Built dataset rows: {built} (legacy_outcome_not_defensible)"))
