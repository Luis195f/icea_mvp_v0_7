from __future__ import annotations

import hashlib
import json
import base64
import os
from datetime import datetime, timedelta, timezone as dt_tz
from typing import Any

import numpy as np
import pandas as pd
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from icea_core.permissions import (
    ICEAAdminOrServicePermission,
    ICEABackwardCompatiblePermission,
    ICEACausalDiscoverPermission,
    ICEAFederatedPermission,
    ICEAResearcherPermission,
    ICEASimulatePermission,
    RequiresAntiReplayHMAC,
)

from fhir_integration.facade import FHIRFacade
from fhir_integration.service import FHIRClient
from icea_core.engine import ICEAEngine
from icea_core.ml import train_xgb_regressor
from icea_core.models import ICEAComputation, ModelArtifact, PatientEpisode

from analytics.causal import ICEACausal
from icea_pipeline.audit import append_audit_event
from icea_pipeline.models import (
    CausalRun,
    CausalSpec,
    DataQualitySnapshot,
    EpisodeFeatureRow,
    EpisodeWindow,
    EpisodeWindowFeatureRow,
    AuditEvent,
    GovernanceDecision,
    FHIRWritebackRecord,
    NormalizedCondition,
    NormalizedObservation,
    NormalizedProcedure,
    RawFHIRResource,
    RosterShift,
    TrainingRun,
)
from icea_pipeline.normalize import normalize_condition, normalize_observation, normalize_procedure
from icea_pipeline.serializers import (
    BuildDatasetSerializer,
    BuildWindowsSerializer,
    CausalRunSerializer,
    IngestFHIRSerializer,
    NormalizeFHIRSerializer,
    RiskAssessmentWritebackSerializer,
    ConformalPredictSerializer,
    RosterUploadSerializer,
    GovernanceDecisionSerializer,
    TrainFromDBSerializer,
)
from icea_pipeline.target_trial import sha256_hex_of, validate_target_trial
from icea_pipeline.temporal import (
    LEGACY_OUTCOME_STATUS,
    episode_legacy_temporal_spec,
    validate_causal_temporal_order,
    validate_case_mix_spec,
    validate_temporal_frame,
    window_temporal_spec,
)
from icea_pipeline.trial_report import generate_trial_protocol_report


def _iter_bundle_entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    if not bundle:
        return []
    if bundle.get("resourceType") != "Bundle":
        return []
    return [e.get("resource") for e in (bundle.get("entry") or []) if e.get("resource")]


def _stable_schema_hash(features: dict[str, Any], target: dict[str, Any]) -> str:
    return hashlib.sha256(("|".join(sorted(features.keys())) + "#" + "|".join(sorted(target.keys()))).encode("utf-8")).hexdigest()


def _to_utc_aware(value: Any):
    if isinstance(value, datetime):
        dt = value
    elif value:
        dt = parse_datetime(str(value))
    else:
        dt = None
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return dt.replace(tzinfo=dt_tz.utc)
    return dt.astimezone(dt_tz.utc)


def _positive_overlap(start: Any, end: Any, window_start: Any, window_end: Any):
    start_dt = _to_utc_aware(start)
    end_dt = _to_utc_aware(end)
    window_start_dt = _to_utc_aware(window_start)
    window_end_dt = _to_utc_aware(window_end)
    if not all([start_dt, end_dt, window_start_dt, window_end_dt]):
        return None, None, 0.0
    overlap_start = max(start_dt, window_start_dt)
    overlap_end = min(end_dt, window_end_dt)
    overlap_hours = max((overlap_end - overlap_start).total_seconds() / 3600.0, 0.0)
    if overlap_hours <= 0:
        return overlap_start, overlap_end, 0.0
    return overlap_start, overlap_end, overlap_hours


class PipelineIngestView(APIView):
    """FHIR ingestion.

    Backwards compatible with v0.3 (patient-centered). v0.4 adds encounter-centered ingestion and best-effort
    ingestion of referenced PractitionerRole/Practitioner for deterministic nursing labels.
    """

    # Optional HMAC signing (v0.7.2) + optional anti-replay (v0.7.3).
    # Enforced only when ICEA_AUDIT_SIGNING_REQUIRED=true.
    permission_classes = [ICEABackwardCompatiblePermission, RequiresAntiReplayHMAC]

    # Scoped throttling (v0.7.3): applies only when ICEA_ENABLE_THROTTLING=true
    # and a matching ICEA_THROTTLE_SCOPE_INGEST rate is configured.
    throttle_scope = "ingest"

    def post(self, request):
        ser = IngestFHIRSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        episode = PatientEpisode.objects.get(id=payload["episode_id"])
        patient_id = (payload.get("patient_id") or "").strip()
        encounter_id = (payload.get("encounter_id") or "").strip()
        mode = payload.get("mode") or "patient"
        resources = payload.get("resources") or ["Observation", "Condition", "Procedure"]

        if mode == "encounter" and not encounter_id:
            return Response({"detail": "mode=encounter requires encounter_id"}, status=400)
        if mode == "patient" and not patient_id:
            return Response({"detail": "mode=patient requires patient_id"}, status=400)

        # v0.5.1: use FHIR Facade (validation + pagination + retry/backoff)
        # ENS Alto compliance: enforce strict validation + required profiles.
        ens_alto = os.environ.get("ICEA_ENS_ALTO_COMPLIANCE", "false").lower() in {"1", "true", "yes"}
        if ens_alto:
            req_profiles = [p.strip() for p in os.environ.get("FHIR_REQUIRED_PROFILES", "").split(",") if p.strip()]
            if not req_profiles:
                # Configuration error: ENS Alto mode requires profile enforcement.
                return Response(
                    {"detail": "ENS Alto compliance requires FHIR_REQUIRED_PROFILES"},
                    status=500,
                )
            facade = FHIRFacade(FHIRClient(), strict=True, required_profiles=req_profiles, fail_closed=True)
        else:
            facade = FHIRFacade(FHIRClient())

        # If encounter-centered, pull Encounter first to derive patient.
        if mode == "encounter":
            enc_v = facade.read("Encounter", encounter_id)
            if ens_alto and not enc_v.ok:
                return Response(
                    {"detail": "FHIR strict validation failed (ENS Alto)", "resource_type": "Encounter", "resource_id": encounter_id, "issues": enc_v.issues},
                    status=400,
                )
            enc = enc_v.payload
            enc_rid = enc.get("id")
            if enc_rid:
                RawFHIRResource.objects.update_or_create(
                    episode=episode,
                    resource_type="Encounter",
                    resource_id=enc_rid,
                    defaults={
                        "payload": enc,
                        "last_updated": enc_v.last_updated,
                        "validation_ok": bool(enc_v.ok),
                        "validation_issues": enc_v.issues,
                        "validation_profile": "Encounter:R4",
                    },
                )
            subj = (enc.get("subject") or {}).get("reference", "")
            if subj.startswith("Patient/"):
                patient_id = subj.split("/", 1)[1]

            changed = False
            if patient_id and episode.fhir_patient_id != patient_id:
                episode.fhir_patient_id = patient_id
                changed = True
            if encounter_id and episode.fhir_encounter_id != encounter_id:
                episode.fhir_encounter_id = encounter_id
                changed = True
            if changed:
                episode.save(update_fields=["fhir_patient_id", "fhir_encounter_id"])
        else:
            # patient mode
            if patient_id and episode.fhir_patient_id != patient_id:
                episode.fhir_patient_id = patient_id
                episode.save(update_fields=["fhir_patient_id"])

        # Pre-fetch + validate ALL resources first in ENS Alto mode to ensure all-or-nothing
        # ingestion (prevents partial commits and schema drift).
        validated_by_rtype: dict[str, list[Any]] = {}
        referenced: list[tuple[str, str]] = []
        ref_validated: list[Any] = []

        for rtype in resources:
            params: dict[str, Any]
            if mode == "encounter" and encounter_id:
                params = {"encounter": encounter_id}
            else:
                params = {"patient": patient_id}

            validated = facade.search_all(rtype, params=params)
            validated_by_rtype[rtype] = validated

            if ens_alto:
                bad = next((vr for vr in validated if vr.resource_type != "Bundle" and not vr.ok), None)
                if bad is not None:
                    return Response(
                        {
                            "detail": "FHIR strict validation failed (ENS Alto)",
                            "resource_type": bad.resource_type,
                            "resource_id": bad.resource_id,
                            "issues": bad.issues,
                        },
                        status=400,
                    )

            # Collect referenced actor resources for deterministic nursing labels.
            if rtype == "Procedure":
                for vr in validated:
                    if vr.resource_type == "Bundle" or not isinstance(vr.payload, dict):
                        continue
                    for perf in (vr.payload.get("performer") or []):
                        actor_ref = ((perf or {}).get("actor") or {}).get("reference", "")
                        if not actor_ref or "/" not in actor_ref:
                            continue
                        rr, rid = actor_ref.split("/", 1)
                        if rr in {"PractitionerRole", "Practitioner"}:
                            referenced.append((rr, rid))

        # Pre-fetch referenced actor resources too (ENS Alto: fail closed if invalid).
        for rr, rid in referenced:
            try:
                rr_v = facade.read(rr, rid)
            except Exception:
                continue
            if ens_alto and not rr_v.ok:
                return Response(
                    {"detail": "FHIR strict validation failed (ENS Alto)", "resource_type": rr, "resource_id": rid, "issues": rr_v.issues},
                    status=400,
                )
            ref_validated.append(rr_v)

        ingested = 0
        invalid = 0

        with transaction.atomic():
            for rtype, validated in validated_by_rtype.items():
                new: list[dict[str, Any]] = []
                for vr in validated:
                    if vr.resource_type == "Bundle":
                        continue
                    rid = (vr.resource_id or "").strip()
                    if not rid:
                        invalid += 1
                        continue
                    RawFHIRResource.objects.update_or_create(
                        episode=episode,
                        resource_type=vr.resource_type or rtype,
                        resource_id=rid,
                        defaults={
                            "payload": vr.payload,
                            "last_updated": vr.last_updated,
                            "validation_ok": bool(vr.ok),
                            "validation_issues": vr.issues,
                            "validation_profile": f"{vr.resource_type}:R4",
                        },
                    )
                    new.append(vr.payload)
                ingested += len(new)

            # Ingest referenced actor resources (best effort by default; ENS Alto validated above).
            for rr_v in ref_validated:
                res = rr_v.payload
                res_id = res.get("id") if isinstance(res, dict) else None
                if not res_id:
                    continue
                RawFHIRResource.objects.update_or_create(
                    episode=episode,
                    resource_type=rr_v.resource_type,
                    resource_id=str(res_id),
                    defaults={
                        "payload": res,
                        "last_updated": rr_v.last_updated,
                        "validation_ok": bool(rr_v.ok),
                        "validation_issues": rr_v.issues,
                        "validation_profile": f"{rr_v.resource_type}:R4",
                    },
                )
                ingested += 1

        append_audit_event(
            event_type="ingest_fhir",
            payload={
                "episode_id": int(episode.id),
                "mode": mode,
                "resources": resources,
                "ingested": ingested,
                "invalid_skipped": int(invalid),
            },
            context="pipeline/ingest",
        )

        return Response({"episode_id": episode.id, "ingested": ingested, "invalid_skipped": int(invalid)})


class PipelineNormalizeView(APIView):
    """Normalize raw FHIR JSON into canonical tables.

    v0.4: adds deterministic nursing label support by resolving PractitionerRole/Practitioner payloads
    previously ingested in the same episode.
    """

    def post(self, request):
        ser = NormalizeFHIRSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        episode = PatientEpisode.objects.get(id=payload["episode_id"])
        truncate = bool(payload.get("truncate"))

        raws = RawFHIRResource.objects.filter(episode=episode).order_by("resource_type")

        # Build resolver map for deterministic performer labeling.
        ref_qs = RawFHIRResource.objects.filter(
            episode=episode,
            resource_type__in=["PractitionerRole", "Practitioner"],
        )
        ref_map = {(r.resource_type, r.resource_id): r.payload for r in ref_qs}

        def lookup(rtype: str, rid: str):
            return ref_map.get((rtype, rid))

        with transaction.atomic():
            if truncate:
                NormalizedObservation.objects.filter(episode=episode).delete()
                NormalizedCondition.objects.filter(episode=episode).delete()
                NormalizedProcedure.objects.filter(episode=episode).delete()

            n_obs = n_cond = n_proc = 0
            for raw in raws:
                rt = raw.resource_type
                if rt == "Observation":
                    data = normalize_observation(raw.payload)
                    NormalizedObservation.objects.create(episode=episode, source_resource=raw, **data)
                    n_obs += 1
                elif rt == "Condition":
                    data = normalize_condition(raw.payload)
                    NormalizedCondition.objects.create(episode=episode, source_resource=raw, **data)
                    n_cond += 1
                elif rt == "Procedure":
                    payload2 = dict(raw.payload)
                    payload2["__resolver__"] = {"lookup": lookup}
                    data = normalize_procedure(payload2)
                    NormalizedProcedure.objects.create(episode=episode, source_resource=raw, **data)
                    n_proc += 1

        append_audit_event(event_type="normalize_fhir", payload={"episode_id": int(episode.id), "truncate": truncate, "observations": n_obs, "conditions": n_cond, "procedures": n_proc}, context="pipeline/normalize")

        return Response(
            {
                "episode_id": episode.id,
                "normalized": {"observations": n_obs, "conditions": n_cond, "procedures": n_proc},
            }
        )


class PipelineBuildDatasetView(APIView):
    """Build analytic dataset rows (EpisodeFeatureRow).

    v0.4 adds roster-derived exposures and data-quality snapshots.
    """

    def post(self, request):
        ser = BuildDatasetSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        episode_id = payload.get("episode_id")
        truncate = bool(payload.get("truncate"))

        qs = PatientEpisode.objects.all().order_by("id")
        if episode_id:
            qs = qs.filter(id=int(episode_id))

        if truncate:
            if episode_id:
                EpisodeFeatureRow.objects.filter(episode_id=int(episode_id)).delete()
            else:
                EpisodeFeatureRow.objects.all().delete()

        built = 0
        legacy_outcome_rows = 0
        for ep in qs:
            temporal_spec = episode_legacy_temporal_spec(ep)
            feature_window_end = parse_datetime(str(temporal_spec["feature_window_end"]))
            features: dict[str, Any] = {"ri_initial": float(ep.ri_initial), "temporal_spec": temporal_spec}
            target: dict[str, Any] = {
                "delta_ri": float(ep.delta_ri),
                "temporal_spec": temporal_spec,
                "outcome_status": LEGACY_OUTCOME_STATUS,
            }

            # Procedures
            procs = NormalizedProcedure.objects.filter(episode=ep, performed_dt__lte=feature_window_end)
            nurse_like = procs.filter(performer_role__iregex=r"(nurs|rn|enfermer|tcae|aux)")
            nurse_det = procs.filter(is_nursing=True)
            features["proc_count"] = procs.count()
            features["nurse_proc_count"] = nurse_like.count()
            features["nurse_proc_count_det"] = nurse_det.count()

            # --- Roster-derived exposures (if available)
            start = ep.admission_date
            end = feature_window_end
            shifts = RosterShift.objects.filter(unit=ep.unit, end_dt__gt=start, start_dt__lt=end)

            nurse_hours = 0.0
            rn_hours = 0.0
            patient_hours = 0.0

            if shifts.exists():
                for s in shifts:
                    overlap_start, overlap_end, dur_h = _positive_overlap(s.start_dt, s.end_dt, start, end)
                    if dur_h <= 0:
                        continue
                    n = float(max(s.rn_count + s.na_count, 0))
                    rn = float(max(s.rn_count, 0))
                    census = s.patient_census
                    if census is None:
                        census = PatientEpisode.objects.filter(unit=ep.unit, admission_date__lte=overlap_end).filter(
                            Q(discharge_date__isnull=True) | Q(discharge_date__gte=overlap_start)
                        ).count()
                    census = max(int(census or 0), 1)
                    nurse_hours += n * dur_h
                    rn_hours += rn * dur_h
                    patient_hours += float(census) * dur_h

                patient_days = patient_hours / 24.0
                features["nurse_hppd"] = float(nurse_hours / patient_days) if patient_days > 0 else 0.0
                features["nurse_skillmix"] = float(rn_hours / nurse_hours) if nurse_hours > 0 else 0.0
            else:
                features["nurse_hppd"] = 0.0
                features["nurse_skillmix"] = 0.0

            # Minimal vital signs (LOINC) as stable features
            vital_loinc = {
                "8867-4": "hr",
                "8480-6": "sbp",
                "8462-4": "dbp",
                "8310-5": "temp",
                "59408-5": "spo2",
                "9279-1": "resp_rate",
            }
            obs = NormalizedObservation.objects.filter(
                episode=ep,
                code_system__icontains="loinc",
                effective_dt__lte=feature_window_end,
            ).exclude(
                value_num__isnull=True
            )
            last_by_code: dict[str, float] = {}
            for o in obs.order_by("effective_dt"):
                if o.code:
                    last_by_code[o.code] = float(o.value_num)
            for loinc, short in vital_loinc.items():
                v = last_by_code.get(loinc)
                missing = v is None
                features[f"vs_{short}_last"] = float(v) if v is not None else 0.0
                # v0.5.4: preserve semantic missingness traceability without breaking numeric features
                features[f"missing_vs_{short}_last"] = 1 if missing else 0
                features[f"missing_loinc_{loinc.replace('-', '_')}"] = 1 if missing else 0

            EpisodeFeatureRow.objects.update_or_create(
                episode=ep,
                defaults={
                    "features": features,
                    "target": target,
                    "schema_hash": _stable_schema_hash(features, target),
                    "feature_version": "v0.7.0",
                },
            )
            built += 1
            legacy_outcome_rows += 1

        # Data quality snapshot (best effort; never break pipeline)
        try:
            rows = list(EpisodeFeatureRow.objects.all())
            if rows:
                data = []
                for r in rows:
                    row = dict(r.features)
                    row.update(r.target)
                    data.append(row)
                ddf = pd.DataFrame(data)
                missing = (ddf.isna().mean().sort_values(ascending=False)).to_dict()
                report = {
                    "n_rows": int(len(ddf)),
                    "n_cols": int(ddf.shape[1]),
                    "missing_rate": {k: float(v) for k, v in list(missing.items())[:50]},
                }
                DataQualitySnapshot.objects.create(schema_hash=rows[0].schema_hash, feature_version="v0.7.0", report=report)
        except Exception:
            pass

        append_audit_event(
            event_type="build_dataset",
            payload={
                "episode_id": episode_id,
                "built": built,
                "truncate": truncate,
                "legacy_outcome_not_defensible_rows": legacy_outcome_rows,
            },
            context="pipeline/build-dataset",
        )

        return Response(
            {
                "built": built,
                "episode_id": episode_id,
                "status": "legacy_outcome_not_defensible" if legacy_outcome_rows else "ok",
                "warnings": ["legacy_outcome_not_defensible"] if legacy_outcome_rows else [],
            }
        )


class PipelineBuildWindowsView(APIView):
    """Build window-grain analytic dataset rows (EpisodeWindowFeatureRow).

    v0.5: episode-windows support shift-level target-trial emulation (e.g., 12h windows).
    """

    def post(self, request):
        ser = BuildWindowsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        episode_id = payload.get("episode_id")
        truncate = bool(payload.get("truncate"))
        window_hours = int(payload.get("window_hours") or 12)
        follow_up_hours = payload.get("follow_up_hours")
        follow_up_hours = int(follow_up_hours) if follow_up_hours is not None else int(window_hours)
        align = payload.get("align") or "admission"
        ri_boundary = payload.get("ri_boundary") or "first_last"
        ri_tol_min = int(payload.get("ri_boundary_tol_minutes") or 60)

        qs = PatientEpisode.objects.all().order_by("id")
        if episode_id:
            qs = qs.filter(id=int(episode_id))

        if truncate:
            if episode_id:
                EpisodeWindowFeatureRow.objects.filter(window__episode_id=int(episode_id)).delete()
                EpisodeWindow.objects.filter(episode_id=int(episode_id)).delete()
            else:
                EpisodeWindowFeatureRow.objects.all().delete()
                EpisodeWindow.objects.all().delete()

        # Helper: detect RI observations within a queryset.
        # Default: include the canonical LOINC code for Rothman Index Calculated.
        ri_code_env = os.environ.get("ROTHMAN_OBS_CODES", "").strip()
        if ri_code_env:
            ri_code_set = set([c.strip() for c in ri_code_env.split(",") if c.strip()])
        else:
            ri_code_set = {"85556-9"}

        def _ri_obs(qs_obs: Any):
            out = []
            for o in qs_obs.exclude(value_num__isnull=True).exclude(effective_dt__isnull=True).order_by("effective_dt"):
                code = (o.code or "").strip()
                disp = (o.display or "").lower()
                if code in ri_code_set or "rothman" in disp or code.lower() in {"rothman", "ri"}:
                    out.append((o.effective_dt, float(o.value_num)))
            return out

        def _pick_nearest(series: list[tuple[Any, float]], anchor, tol_minutes: int) -> float | None:
            if not series:
                return None
            best = None
            best_dt = None
            for dt, v in series:
                d = abs((dt - anchor).total_seconds())
                if best is None or d < best:
                    best = d
                    best_dt = v
            if best is None:
                return None
            if tol_minutes <= 0:
                return best_dt
            return best_dt if best <= (tol_minutes * 60) else None

        built = 0
        for ep in qs:
            start = ep.admission_date
            end = ep.discharge_date or timezone.now()
            if end <= start:
                continue

            windows: list[tuple[Any, Any]] = []
            if align == "shift":
                shifts = RosterShift.objects.filter(unit=ep.unit, end_dt__gt=start, start_dt__lt=end).order_by("start_dt")
                for s in shifts:
                    ws = max(s.start_dt, start)
                    we = min(s.end_dt, end)
                    if we > ws:
                        windows.append((ws, we))
            if not windows:
                # fixed windows from admission
                cur = start
                delta = timedelta(hours=window_hours)
                while cur < end:
                    nxt = min(cur + delta, end)
                    if nxt <= cur:
                        break
                    windows.append((cur, nxt))
                    cur = nxt

            # Create windows + rows
            with transaction.atomic():
                for idx, (ws, we) in enumerate(windows):
                    w = EpisodeWindow.objects.create(episode=ep, window_index=idx, start_dt=ws, end_dt=we)

                    temporal_spec = window_temporal_spec(ep, ws=ws, we=we, follow_up_hours=int(follow_up_hours))
                    outcome_start = parse_datetime(str(temporal_spec["outcome_window_start"]))
                    outcome_end = parse_datetime(str(temporal_spec["outcome_window_end"])) if temporal_spec.get("outcome_window_end") else None
                    features: dict[str, Any] = {
                        "ri_initial": float(ep.ri_initial),
                        "window_index": int(idx),
                        "window_hours": float((we - ws).total_seconds() / 3600.0),
                        "follow_up_hours": float(follow_up_hours),
                        "temporal_spec": temporal_spec,
                    }

                    # Procedures within window
                    procs = NormalizedProcedure.objects.filter(episode=ep)
                    procs_w = procs.filter(performed_dt__gte=ws, performed_dt__lt=we)
                    nurse_like = procs_w.filter(performer_role__iregex=r"(nurs|rn|enfermer|tcae|aux)")
                    nurse_det = procs_w.filter(is_nursing=True)
                    features["proc_count"] = procs_w.count()
                    features["nurse_proc_count"] = nurse_like.count()
                    features["nurse_proc_count_det"] = nurse_det.count()

                    # Roster exposures in window
                    shifts = RosterShift.objects.filter(unit=ep.unit, end_dt__gt=ws, start_dt__lt=we)
                    nurse_hours = 0.0
                    rn_hours = 0.0
                    patient_hours = 0.0
                    if shifts.exists():
                        for s in shifts:
                            ss, se, dur_h = _positive_overlap(s.start_dt, s.end_dt, ws, we)
                            if dur_h <= 0:
                                continue
                            n = float(max(s.rn_count + s.na_count, 0))
                            rn = float(max(s.rn_count, 0))
                            census = s.patient_census
                            if census is None:
                                census = PatientEpisode.objects.filter(unit=ep.unit, admission_date__lte=se).filter(
                                    Q(discharge_date__isnull=True) | Q(discharge_date__gte=ss)
                                ).count()
                            census = max(int(census or 0), 1)
                            nurse_hours += n * dur_h
                            rn_hours += rn * dur_h
                            patient_hours += float(census) * dur_h
                        patient_days = patient_hours / 24.0
                        features["nurse_hppd"] = float(nurse_hours / patient_days) if patient_days > 0 else 0.0
                        features["nurse_skillmix"] = float(rn_hours / nurse_hours) if nurse_hours > 0 else 0.0
                    else:
                        features["nurse_hppd"] = 0.0
                        features["nurse_skillmix"] = 0.0

                    # Vitals last within window
                    vital_loinc = {
                        "8867-4": "hr",
                        "8480-6": "sbp",
                        "8462-4": "dbp",
                        "8310-5": "temp",
                        "59408-5": "spo2",
                        "9279-1": "resp_rate",
                    }
                    obs = NormalizedObservation.objects.filter(episode=ep, code_system__icontains="loinc").exclude(
                        value_num__isnull=True
                    )
                    obs_w = obs.filter(effective_dt__gte=ws, effective_dt__lt=we).order_by("effective_dt")
                    last_by_code: dict[str, float] = {}
                    for o in obs_w:
                        if o.code:
                            last_by_code[o.code] = float(o.value_num)
                    for loinc, short in vital_loinc.items():
                        v = last_by_code.get(loinc)
                        missing = v is None
                        features[f"vs_{short}_last"] = float(v) if v is not None else 0.0
                        # v0.5.4: preserve semantic missingness traceability without breaking numeric features
                        features[f"missing_vs_{short}_last"] = 1 if missing else 0
                        features[f"missing_loinc_{loinc.replace('-', '_')}"] = 1 if missing else 0

                    # Window target using RI observations if available.
                    # v0.5.3: target horizon can be decoupled from the window length via follow_up_hours.
                    horizon_end = outcome_end
                    obs_t = obs.none()
                    if outcome_start is not None and horizon_end is not None:
                        if ri_boundary == "nearest":
                            tolerance = timedelta(minutes=ri_tol_min)
                            obs_t = obs.filter(
                                effective_dt__gte=outcome_start - tolerance,
                                effective_dt__lte=horizon_end + tolerance,
                            ).order_by("effective_dt")
                        else:
                            obs_t = obs.filter(effective_dt__gte=outcome_start, effective_dt__lte=horizon_end).order_by("effective_dt")
                    ri_series = _ri_obs(obs_t)
                    ri_start = None
                    ri_end = None
                    if len(ri_series) >= 2:
                        if ri_boundary == "nearest":
                            ri_start = _pick_nearest(ri_series, outcome_start, ri_tol_min)
                            ri_end = _pick_nearest(ri_series, horizon_end, ri_tol_min)
                        elif ri_start is None or ri_end is None:
                            # Fallback to first/last (backwards compatible)
                            ri_start = float(ri_series[0][1])
                            ri_end = float(ri_series[-1][1])

                    # v0.5.4: semantic missingness flags (LOINC) for CONSORT-grade audit reporting
                    missing_t0 = 1 if ri_start is None else 0
                    missing_t1 = 1 if ri_end is None else 0
                    features["missing_loinc_85556_9_t0"] = int(missing_t0)
                    features["missing_loinc_85556_9_t1"] = int(missing_t1)
                    features["missing_delta_ri"] = int(1 if (missing_t0 or missing_t1) else 0)

                    # v0.6: Forensic missingness attribution for Rothman components (26-variable proxy).
                    # This does NOT claim to reconstruct the proprietary Rothman Index; it provides
                    # actionable data-quality signals (which LOINC-coded measurements are absent at t0/t1).
                    if missing_t0 or missing_t1:
                        component_env = (os.environ.get("ROTHMAN_COMPONENT_LOINC_CODES") or "").strip()
                        if component_env:
                            component_codes = [c.strip() for c in component_env.split(",") if c.strip()]
                        else:
                            component_codes = [
                                "8867-4", "8480-6", "8462-4", "8478-0", "9279-1", "8310-5", "59408-5",
                                "3150-0", "3151-8", "9192-6", "6690-2", "718-7", "4544-3", "2951-2",
                                "2823-3", "3094-0", "2160-0", "2345-7", "2075-0", "2028-9", "6768-6",
                                "1751-7", "9269-2", "38226-6", "41959-4", "72514-3",
                            ]

                        # Build a time-series per code from obs_t (already limited to ws..horizon_end)
                        series_by_code: dict[str, list[tuple[Any, float]]] = {}
                        for o in obs_t.exclude(value_num__isnull=True).exclude(effective_dt__isnull=True):
                            c = (o.code or "").strip()
                            if c in component_codes:
                                series_by_code.setdefault(c, []).append((o.effective_dt, float(o.value_num)))

                        for c in component_codes:
                            s_c = series_by_code.get(c) or []
                            if missing_t0:
                                v0 = _pick_nearest(s_c, outcome_start, ri_tol_min)
                                features[f"missing_loinc_{c.replace('-', '_')}_t0"] = 1 if v0 is None else 0
                            if missing_t1:
                                v1 = _pick_nearest(s_c, horizon_end, ri_tol_min)
                                features[f"missing_loinc_{c.replace('-', '_')}_t1"] = 1 if v1 is None else 0

                        features["rothman_component_map"] = "v0.7_open_proxy"

                    if ri_start is not None and ri_end is not None:
                        features["ri_window_start"] = float(ri_start)
                        target = {
                            "delta_ri": float(float(ri_end) - float(ri_start)),
                            "temporal_spec": temporal_spec,
                            "outcome_status": "defensible_fixed_horizon",
                        }
                    else:
                        features["ri_window_start"] = 0.0
                        target = {
                            "delta_ri": None,
                            "temporal_spec": temporal_spec,
                            "outcome_status": "insufficient_outcome_evidence",
                        }

                    EpisodeWindowFeatureRow.objects.create(
                        window=w,
                        features=features,
                        target=target,
                        schema_hash=_stable_schema_hash(features, target),
                        feature_version="v0.7.0",
                    )
                    built += 1

        append_audit_event(
            event_type="build_windows",
            payload={
                "episode_id": episode_id,
                "built": built,
                "window_hours": window_hours,
                "follow_up_hours": int(follow_up_hours),
                "align": align,
                "ri_boundary": ri_boundary,
                "ri_tol_minutes": int(ri_tol_min),
            },
            context="pipeline/build-windows",
        )

        # Window-grain data quality snapshot (best effort)
        try:
            rows = list(EpisodeWindowFeatureRow.objects.all())
            if rows:
                data = []
                for r in rows:
                    row = dict(r.features)
                    row.update(r.target)
                    data.append(row)
                ddf = pd.DataFrame(data)
                missing = (ddf.isna().mean().sort_values(ascending=False)).to_dict()
                report = {
                    "grain": "window",
                    "n_rows": int(len(ddf)),
                    "n_cols": int(ddf.shape[1]),
                    "missing_rate": {k: float(v) for k, v in list(missing.items())[:50]},
                }
                DataQualitySnapshot.objects.create(schema_hash=rows[0].schema_hash, feature_version="v0.7.0", report=report)
        except Exception:
            pass

        return Response({"built": built, "episode_id": episode_id, "grain": "window", "temporal_spec_version": "icea_temporal_v1"})


class PipelineTrainFromDBView(APIView):
    """Train model using DB materialized dataset."""

    def post(self, request):
        ser = TrainFromDBSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        name = payload.get("name", "icea-xgb")
        version = payload.get("version", "v0.5.1")
        target = payload.get("target", "delta_ri")
        grain = str(payload.get("grain") or "auto").strip().lower()

        window_rows = []
        if grain in {"auto", "window"}:
            window_rows = list(EpisodeWindowFeatureRow.objects.select_related("window", "window__episode").all())
        if grain == "window" or (grain == "auto" and window_rows):
            rows = window_rows
            dataset_grain = "window"
        else:
            rows = list(EpisodeFeatureRow.objects.select_related("episode").all())
            dataset_grain = "episode"
        dataset: list[dict[str, Any]] = []
        for r in rows:
            row = dict(r.features)
            row.update(r.target)
            dataset.append(row)

        if not dataset:
            if dataset_grain == "window":
                return Response({"detail": "No window dataset rows. Run build-windows first.", "dataset_grain": dataset_grain}, status=400)
            return Response({"detail": "No dataset rows. Run build-dataset first.", "dataset_grain": dataset_grain}, status=400)

        df = pd.DataFrame(dataset)
        temporal_issues = validate_temporal_frame(df, feature_names=[c for c in df.columns if c != target], target=target)
        if temporal_issues:
            return Response(
                {
                    "detail": "dataset_not_temporally_defensible",
                    "dataset_grain": dataset_grain,
                    "status": temporal_issues[0][1].status,
                    "warnings": sorted({warning for _, issue in temporal_issues for warning in issue.warnings}),
                    "blocked_rows": int(len(temporal_issues)),
                },
                status=400,
            )
        df = df.replace([np.inf, -np.inf], np.nan).fillna(0.0)

        metadata_cols = {"temporal_spec", "outcome_status", "feature_timestamps"}
        features = [c for c in df.columns if c != target and c not in metadata_cols]
        df_model = df[features + [target]].copy()

        # Feature distribution snapshot for drift monitoring (v0.5 governance)
        feature_stats = {"mean": df_model.drop(columns=[target]).mean(numeric_only=True).to_dict(),
                        "std": df_model.drop(columns=[target]).std(numeric_only=True, ddof=0).to_dict()}

        result = train_xgb_regressor(
            df_model,
            features=features,
            target=target,
            model_dir=settings.ICEA_MODEL_DIR,
        )

        # attach governance metrics
        result.metrics["feature_stats"] = feature_stats

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
        append_audit_event(
            event_type="train_model",
            payload={
                "model_id": str(artifact.id),
                "name": name,
                "version": version,
                "target": target,
                "rows": int(len(df)),
                "dataset_grain": dataset_grain,
            },
            context="pipeline/train",
        )
        return Response(
            {
                "model_id": str(artifact.id),
                "name": name,
                "version": version,
                "dataset_grain": dataset_grain,
                "metrics": result.metrics,
            }
        )


class DashboardSummaryView(APIView):
    def get(self, request):
        latest_model = ModelArtifact.objects.order_by("-created_at").first()
        latest_train = TrainingRun.objects.order_by("-created_at").first()
        latest_comp = ICEAComputation.objects.order_by("-created_at").first()
        latest_causal = CausalRun.objects.order_by("-created_at").first()
        latest_dq = DataQualitySnapshot.objects.order_by("-created_at").first()
        latest_gov = GovernanceDecision.objects.order_by("-created_at").first()

        return Response(
            {
                "episodes": PatientEpisode.objects.count(),
                "raw_fhir": RawFHIRResource.objects.count(),
                "roster_shifts": RosterShift.objects.count(),
                "normalized": {
                    "observations": NormalizedObservation.objects.count(),
                    "conditions": NormalizedCondition.objects.count(),
                    "procedures": NormalizedProcedure.objects.count(),
                },
                "dataset_rows": EpisodeFeatureRow.objects.count(),
                "windows": EpisodeWindow.objects.count(),
                "window_rows": EpisodeWindowFeatureRow.objects.count(),
                "audit_events": AuditEvent.objects.count(),
                "governance_decisions": GovernanceDecision.objects.count(),
                "writebacks": {"count": FHIRWritebackRecord.objects.count()},
                "latest_model": {
                    "id": str(latest_model.id) if latest_model else None,
                    "name": latest_model.name if latest_model else None,
                    "version": latest_model.version if latest_model else None,
                    "created_at": latest_model.created_at if latest_model else None,
                },
                "latest_training": {
                    "id": str(latest_train.id) if latest_train else None,
                    "created_at": latest_train.created_at if latest_train else None,
                    "dataset_rows": latest_train.dataset_rows if latest_train else None,
                },
                "latest_compute": {
                    "id": str(latest_comp.id) if latest_comp else None,
                    "created_at": latest_comp.created_at if latest_comp else None,
                    "summary": latest_comp.summary if latest_comp else None,
                },
                "latest_causal": {
                    "id": str(latest_causal.id) if latest_causal else None,
                    "created_at": latest_causal.created_at if latest_causal else None,
                    "summary": latest_causal.summary if latest_causal else None,
                },

                "latest_governance": {
                    "id": str(latest_gov.id) if latest_gov else None,
                    "created_at": latest_gov.created_at if latest_gov else None,
                    "decision_type": latest_gov.decision_type if latest_gov else None,
                    "actor": latest_gov.actor if latest_gov else None,
                },
                "latest_data_quality": {
                    "id": str(latest_dq.id) if latest_dq else None,
                    "created_at": latest_dq.created_at if latest_dq else None,
                    "report": latest_dq.report if latest_dq else None,
                },
            }
        )


class RosterUploadView(APIView):
    def post(self, request):
        ser = RosterUploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        from icea_core.models import Unit

        unit = Unit.objects.get(id=payload["unit_id"])
        import csv
        import io

        reader = csv.DictReader(io.StringIO(payload["csv"]))
        created = 0

        with transaction.atomic():
            for row in reader:
                start_dt = parse_datetime((row.get("start_dt") or "").strip())
                end_dt = parse_datetime((row.get("end_dt") or "").strip())
                if not start_dt or not end_dt:
                    continue
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=dt_tz.utc)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=dt_tz.utc)

                rn_count = int(float(row.get("rn_count") or 0))
                na_count = int(float(row.get("na_count") or 0))
                pc = row.get("patient_census")
                patient_census = int(float(pc)) if pc not in (None, "") else None

                RosterShift.objects.create(
                    unit=unit,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    rn_count=rn_count,
                    na_count=na_count,
                    patient_census=patient_census,
                    source=payload.get("source", "csv"),
                )
                created += 1

        return Response({"unit_id": unit.id, "created": created})


class RosterSummaryView(APIView):
    def get(self, request):
        return Response(
            {
                "roster_shifts": RosterShift.objects.count(),
                "units_with_roster": RosterShift.objects.values_list("unit_id", flat=True).distinct().count(),
            }
        )


class AuditEventsListView(APIView):
    """List recent audit events (for governance / forensic traceability)."""

    def get(self, request):
        limit = int(request.query_params.get("limit", "100"))
        limit = max(1, min(limit, 500))
        qs = AuditEvent.objects.order_by("-created_at")[:limit]
        return Response(
            {
                "count": qs.count(),
                "events": [
                    {
                        "id": str(e.id),
                        "created_at": e.created_at,
                        "event_type": e.event_type,
                        "actor": e.actor,
                        "context": e.context,
                        "payload_sha256": e.payload_sha256,
                        "prev_hash": e.prev_hash,
                        "chain_hash": e.chain_hash,
                        "hmac_sig": e.hmac_sig,
                    }
                    for e in qs
                ],
            }
        )


class GovernanceDecisionView(APIView):
    """Record a human-in-the-loop decision (override/approval) for outputs."""

    def post(self, request):
        ser = GovernanceDecisionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        p = ser.validated_data

        model = None
        ep = None
        cr = None
        wb = None
        if p.get("model_id"):
            model = ModelArtifact.objects.filter(id=p["model_id"]).first()
        if p.get("episode_id"):
            ep = PatientEpisode.objects.filter(id=int(p["episode_id"])).first()
        if p.get("causal_run_id"):
            cr = CausalRun.objects.filter(id=p["causal_run_id"]).first()
        if p.get("writeback_id"):
            wb = FHIRWritebackRecord.objects.filter(id=p["writeback_id"]).first()

        gd = GovernanceDecision.objects.create(
            decision_type=p.get("decision_type") or "override",
            actor=p.get("actor") or "",
            rationale=p.get("rationale") or "",
            model=model,
            episode=ep,
            causal_run=cr,
            writeback=wb,
            payload=p.get("payload") or {},
        )

        append_audit_event(
            event_type="governance_decision",
            payload={"decision_id": str(gd.id), "decision_type": gd.decision_type, "actor": gd.actor},
            context="governance/decision",
            actor=gd.actor or "api",
        )

        return Response({"decision_id": str(gd.id)})


class CausalRunView(APIView):
    """Run ICEA+ causal analysis from DB dataset.

    v0.5 additions:
      - bootstrap CIs (non-parametric)
      - formal sensitivity (E-value)
      - optional window-grain dataset (episode-windows)
    """

    permission_classes = [ICEAResearcherPermission]

    def post(self, request):
        ser = CausalRunSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        spec = ser.validated_data["spec"] or {}

        # v0.5.3: Target Trial Template (optional) validated in-memory via Pydantic.
        # This keeps DB schema flexible while preventing malformed protocol injection.
        tt_valid, tt_issues = validate_target_trial(spec)
        if tt_issues:
            return Response({"detail": "Invalid target_trial template", "issues": tt_issues}, status=400)
        if tt_valid is not None:
            spec["target_trial"] = tt_valid

        treatment = str(spec.get("treatment") or "").strip()
        outcome = str(spec.get("outcome") or "delta_ri").strip()
        confounders = list(spec.get("confounders") or [])
        effect_modifiers = list(spec.get("effect_modifiers") or [])
        dag_edges = spec.get("dag_edges") or []
        grain = str(spec.get("grain") or spec.get("dataset_grain") or "episode").strip().lower()
        causal_temporal_issue = validate_causal_temporal_order(spec)
        case_mix_issue = validate_case_mix_spec(spec.get("case_mix_spec"))

        if not treatment:
            return Response({"detail": "spec.treatment is required"}, status=400)

        # v0.5.3: cryptographic sealing of the trial protocol to prevent retroactive manipulation.
        # If the client provides protocol_hash, verify it matches; otherwise compute + persist.
        client_hash = str(spec.get("protocol_hash") or "").strip()
        # Define the protocol payload as the full spec (including target_trial), minus runtime-only fields.
        # We keep this conservative: only drop known non-protocol noise.
        protocol_payload = dict(spec)
        protocol_payload.pop("protocol_hash", None)
        protocol_payload.pop("protocol_hash_alg", None)
        protocol_payload.pop("protocol_hash_input", None)
        computed_hash = sha256_hex_of(protocol_payload)
        if client_hash and client_hash != computed_hash:
            return Response(
                {
                    "detail": "protocol_hash mismatch",
                    "provided": client_hash,
                    "computed": computed_hash,
                },
                status=400,
            )
        spec["protocol_hash"] = computed_hash
        spec["protocol_hash_alg"] = "sha256"
        spec["protocol_hash_input"] = "canonical_json_sorted"

        causal_row_warnings: list[str] = []
        if grain == "window":
            rows = list(EpisodeWindowFeatureRow.objects.select_related("window", "window__episode").all())
            if not rows:
                return Response({"detail": "No window dataset rows. Run build-windows first."}, status=400)

            # v0.5.3: Follow-up horizon can be injected from the Target Trial Template.
            # If outcome is delta_ri and a follow-up horizon is provided, recompute Y on-the-fly
            # from the RI time-series (NormalizedObservation) using window.start as time-zero.
            follow_up_h = None
            ri_boundary = str(spec.get("ri_boundary") or "first_last")
            ri_tol_min = int(spec.get("ri_boundary_tol_minutes") or 60)
            if isinstance(spec.get("target_trial"), dict):
                fu = (spec.get("target_trial") or {}).get("follow_up") or {}
                try:
                    follow_up_h = int(fu.get("horizon_hours")) if fu.get("horizon_hours") is not None else None
                except Exception:
                    follow_up_h = None

            # Prepare RI series per episode (best effort)
            ri_code_env = os.environ.get("ROTHMAN_OBS_CODES", "").strip()
            if ri_code_env:
                ri_code_set = set([c.strip() for c in ri_code_env.split(",") if c.strip()])
            else:
                ri_code_set = {"85556-9"}

            def _pick_nearest(series, anchor, tol_minutes: int):
                if not series:
                    return None
                best = None
                best_v = None
                for dt, v in series:
                    d = abs((dt - anchor).total_seconds())
                    if best is None or d < best:
                        best = d
                        best_v = v
                if best is None:
                    return None
                if tol_minutes <= 0:
                    return best_v
                return best_v if best <= (tol_minutes * 60) else None

            def _parse_causal_dt(value):
                if isinstance(value, datetime):
                    dt = value
                elif value:
                    dt = parse_datetime(str(value))
                else:
                    dt = None
                if dt is None:
                    return None
                if timezone.is_naive(dt):
                    return dt.replace(tzinfo=dt_tz.utc)
                return dt.astimezone(dt_tz.utc)

            def _iso_causal_dt(value):
                return value.isoformat() if value else None

            def _causal_outcome_window(row: dict[str, Any], window, horizon_hours: int):
                existing = dict(row.get("temporal_spec") or {})
                feature_start = _parse_causal_dt(existing.get("feature_window_start")) or window.start_dt
                feature_end = _parse_causal_dt(existing.get("feature_window_end")) or window.end_dt
                index_time = _parse_causal_dt(existing.get("index_time")) or window.start_dt
                existing_outcome_start = _parse_causal_dt(existing.get("outcome_window_start"))
                existing_outcome_end = _parse_causal_dt(existing.get("outcome_window_end"))

                if timezone.is_naive(feature_start):
                    feature_start = feature_start.replace(tzinfo=dt_tz.utc)
                if timezone.is_naive(feature_end):
                    feature_end = feature_end.replace(tzinfo=dt_tz.utc)
                if timezone.is_naive(index_time):
                    index_time = index_time.replace(tzinfo=dt_tz.utc)

                outcome_start = feature_end
                outcome_end = outcome_start + timedelta(hours=int(horizon_hours))
                censoring_reason = "not_censored"
                ep_end = window.episode.discharge_date
                if ep_end is not None:
                    ep_end = _parse_causal_dt(ep_end)
                    if ep_end is not None and ep_end < outcome_end:
                        outcome_end = ep_end
                        censoring_reason = "discharged_before_outcome_window_end"
                temporal_spec = {
                    "temporal_spec_version": "icea_temporal_v1",
                    "index_time": _iso_causal_dt(index_time),
                    "feature_window_start": _iso_causal_dt(feature_start),
                    "feature_window_end": _iso_causal_dt(feature_end),
                    "outcome_window_start": _iso_causal_dt(outcome_start),
                    "outcome_window_end": _iso_causal_dt(outcome_end),
                    "censoring_reason": censoring_reason,
                    "outcome_status": "defensible_fixed_horizon",
                }

                if existing_outcome_start is not None or existing_outcome_end is not None:
                    if (
                        existing_outcome_start is not None
                        and existing_outcome_end is not None
                        and existing_outcome_start == outcome_start
                        and existing_outcome_end == outcome_end
                        and feature_end <= existing_outcome_start
                        and existing_outcome_start <= existing_outcome_end
                    ):
                        return existing_outcome_start, existing_outcome_end, existing, True, []
                    return outcome_start, outcome_end, temporal_spec, False, [
                        "stored_outcome_window_mismatch_target_trial",
                        "outcome_recomputed_for_target_trial_horizon",
                    ]
                return outcome_start, outcome_end, temporal_spec, False, []

            series_by_ep = {}
            if follow_up_h and outcome == "delta_ri":
                ep_ids = sorted({int(r.window.episode_id) for r in rows})
                # Pull candidate RI observations (code-based, plus a light fallback on display).
                obs_qs = NormalizedObservation.objects.filter(
                    episode_id__in=ep_ids,
                ).exclude(value_num__isnull=True).exclude(effective_dt__isnull=True)
                if ri_code_set:
                    obs_qs = obs_qs.filter(Q(code__in=list(ri_code_set)) | Q(display__icontains="rothman") | Q(code__iexact="ri"))
                obs_qs = obs_qs.order_by("episode_id", "effective_dt")
                for o in obs_qs:
                    series_by_ep.setdefault(int(o.episode_id), []).append((o.effective_dt, float(o.value_num)))
            data = []
            for r in rows:
                row = dict(r.features)
                row.update(r.target)
                # attach minimal window identifiers for debugging
                row["episode_id"] = int(r.window.episode_id)
                row["window_index"] = int(r.window.window_index)

                if follow_up_h and outcome == "delta_ri":
                    ep = r.window.episode
                    outcome_start, horizon_end, temporal_spec, reuse_stored_outcome, row_warnings = _causal_outcome_window(
                        row, r.window, int(follow_up_h)
                    )
                    causal_row_warnings.extend(row_warnings)
                    row["temporal_spec"] = temporal_spec
                    if reuse_stored_outcome:
                        row["follow_up_hours_used"] = float(follow_up_h)
                        row["outcome_status"] = str(row.get("outcome_status") or "defensible_fixed_horizon")
                    else:
                        series = series_by_ep.get(int(ep.id)) or []
                    if not reuse_stored_outcome and outcome_start is not None and horizon_end is not None and len(series) >= 2:
                        ri_start = None
                        ri_end = None
                        if ri_boundary == "nearest":
                            ri_start = _pick_nearest(series, outcome_start, ri_tol_min)
                            ri_end = _pick_nearest(series, horizon_end, ri_tol_min)
                        if ri_start is None or ri_end is None:
                            # fallback: first point >= outcome_start and last point <= horizon_end
                            within = [(dt, v) for dt, v in series if dt >= outcome_start and dt <= horizon_end]
                            if len(within) >= 2:
                                ri_start = float(within[0][1])
                                ri_end = float(within[-1][1])
                        missing_t0 = 1 if ri_start is None else 0
                        missing_t1 = 1 if ri_end is None else 0
                        row["missing_loinc_85556_9_t0"] = int(missing_t0)
                        row["missing_loinc_85556_9_t1"] = int(missing_t1)
                        row["missing_delta_ri"] = int(1 if (missing_t0 or missing_t1) else 0)
                        if ri_start is not None and ri_end is not None:
                            row["delta_ri"] = float(float(ri_end) - float(ri_start))
                            row["follow_up_hours_used"] = float(follow_up_h)
                            row["outcome_status"] = "defensible_fixed_horizon"
                        else:
                            row["delta_ri"] = None
                            row["outcome_status"] = "insufficient_outcome_evidence"
                    elif not reuse_stored_outcome:
                        row["missing_loinc_85556_9_t0"] = 1
                        row["missing_loinc_85556_9_t1"] = 1
                        row["missing_delta_ri"] = 1
                        row["delta_ri"] = None
                        row["outcome_status"] = "insufficient_outcome_evidence"
                data.append(row)
        else:
            rows = list(EpisodeFeatureRow.objects.all())
            if not rows:
                return Response({"detail": "No dataset rows. Run build-dataset first."}, status=400)
            data = []
            for r in rows:
                row = dict(r.features)
                row.update(r.target)
                data.append(row)

        df_raw = pd.DataFrame(data)
        temporal_spec_used = data[0].get("temporal_spec") if grain == "window" and data else None
        temporal_dataset_issues = validate_temporal_frame(df_raw, target=outcome)
        if causal_temporal_issue or temporal_dataset_issues:
            warnings = []
            if causal_temporal_issue:
                warnings.extend(causal_temporal_issue.warnings)
            warnings.extend(causal_row_warnings)
            warnings.extend([warning for _, issue in temporal_dataset_issues for warning in issue.warnings])
            if case_mix_issue:
                warnings.extend(case_mix_issue.warnings)
            dataset_status = temporal_dataset_issues[0][1].status if temporal_dataset_issues else "insufficient_temporal_spec"
            summary = {
                "spec": {
                    "treatment": treatment,
                    "outcome": outcome,
                    "confounders": confounders,
                    "effect_modifiers": effect_modifiers,
                    "dag_edges": dag_edges,
                    "grain": grain,
                    "target_trial": spec.get("target_trial"),
                    "protocol_hash": spec.get("protocol_hash"),
                },
                "n_rows": int(len(df_raw)),
                "ate": None,
                "causal_available": False,
                "status": "temporal_leakage_blocked" if causal_temporal_issue else dataset_status,
                "temporal_spec_used": temporal_spec_used,
                "warnings": sorted(set(warnings)),
            }
            spec_hash = hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()
            cs_name = f"spec-{spec_hash[:32]}"
            cs, _ = CausalSpec.objects.get_or_create(name=cs_name, defaults={"spec": spec})
            run = CausalRun.objects.create(spec=cs, outcome=outcome, treatment=treatment, n_rows=int(len(df_raw)), summary=summary)
            append_audit_event(
                event_type="causal_run_blocked",
                payload={"run_id": str(run.id), "treatment": treatment, "outcome": outcome, "grain": grain},
                context="causal/run",
            )
            return Response({"run_id": str(run.id), "summary": summary})

        df = df_raw.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        # v0.7: optional causal discovery (PC) to suggest dag_edges from observed unit data.
        # This never mutates the protocol unless explicitly requested (auto_update=true).
        dag_discovery_out = None
        try:
            dd = spec.get("dag_discovery") if isinstance(spec.get("dag_discovery"), dict) else None
            if dd and bool(dd.get("enabled", False)):
                if str(os.environ.get("ICEA_CAUSAL_DISCOVER_ENABLED", "false")).lower() not in {"1", "true", "yes", "on"}:
                    dag_discovery_out = {"available": False, "detail": "ICEA_CAUSAL_DISCOVER_ENABLED must be explicitly enabled"}
                    raise RuntimeError("causal_discovery_disabled")
                from icea_pipeline.causal_discovery import discover_dag_pc

                dd_vars = list(dd.get("variables") or []) or list({treatment, outcome, *confounders, *effect_modifiers})
                dd_alpha = float(dd.get("alpha") or 0.05)
                dd_max = int(dd.get("max_cond_set") or 2)
                forbid = dd.get("forbid_edges") or []
                df_dd = df.copy()
                for v in dd_vars:
                    if v not in df_dd.columns:
                        df_dd[v] = np.nan
                df_dd = df_dd[dd_vars].apply(pd.to_numeric, errors="coerce").dropna(axis=0, how="any")
                disc = discover_dag_pc(df_dd, variables=dd_vars, alpha=dd_alpha, max_cond_set=dd_max, forbid_edges=forbid)
                dag_discovery_out = {
                    "available": True,
                    "method": disc.method,
                    "alpha": disc.alpha,
                    "max_cond_set": disc.max_cond_set,
                    "variables": disc.variables,
                    "suggested_dag_edges": disc.dag_edges,
                    "undirected_edges": disc.undirected_edges,
                    "n_rows_used": int(len(df_dd)),
                    "notes": disc.notes,
                }
                if bool(dd.get("auto_update", False)):
                    spec["dag_edges"] = disc.dag_edges
                    dag_edges = disc.dag_edges
        except Exception as e:
            if dag_discovery_out is None:
                dag_discovery_out = {"available": False, "error": f"{e.__class__.__name__}: {str(e)}"}


        needed = [treatment, outcome] + confounders + effect_modifiers
        for c in needed:
            if c and c not in df.columns:
                df[c] = 0.0

        T = df[treatment].astype(float).values
        Y = df[outcome].astype(float).values
        W = df[confounders].astype(float).values if confounders else None
        X = df[effect_modifiers].astype(float).values if effect_modifiers else (df[confounders].astype(float).values if confounders else None)

        n_estimators = int(spec.get("n_estimators") or 400)
        causal = ICEACausal(n_estimators=n_estimators)
        causal.fit(X=X, W=W, T=T, Y=Y)
        res = causal.effect(X=X)

        cate = np.asarray(res.cate)
        ate = float(np.mean(cate)) if cate.size else 0.0

        # Balance check (simple SMD split at median treatment)
        balance = {}
        if confounders:
            t_med = float(np.median(T))
            treated = df[T > t_med]
            control = df[T <= t_med]
            for c in confounders:
                a = treated[c].astype(float)
                b = control[c].astype(float)
                denom = float(np.sqrt(0.5 * (a.var(ddof=0) + b.var(ddof=0)))) if (a.var(ddof=0) + b.var(ddof=0)) > 0 else 0.0
                balance[c] = float((a.mean() - b.mean()) / denom) if denom > 0 else 0.0

        # Placebo check on ri_initial
        placebo_ate = None
        if "ri_initial" in df.columns:
            try:
                Yp = df["ri_initial"].astype(float).values
                causal_p = ICEACausal(n_estimators=min(200, n_estimators))
                causal_p.fit(X=X, W=W, T=T, Y=Yp)
                placebo_ate = float(np.mean(np.asarray(causal_p.effect(X=X).cate)))
            except Exception:
                placebo_ate = None

        # Causal SHAP (effect explainability)
        shap_top = []
        if res.shap_values is not None and effect_modifiers:
            try:
                sv = np.asarray(res.shap_values)
                if sv.ndim == 3:
                    sv = sv[:, :, 0]
                mean_abs = np.mean(np.abs(sv), axis=0)
                pairs = list(zip(effect_modifiers, mean_abs.tolist()))
                pairs.sort(key=lambda x: x[1], reverse=True)
                shap_top = [{"feature": k, "mean_abs_shap": float(v)} for k, v in pairs[:10]]
            except Exception:
                shap_top = []

        # Bootstrap CI (optional)
        boot = spec.get("bootstrap") or {}
        bootstrap_n = int(spec.get("bootstrap_n") or boot.get("n") or 0)
        alpha = float(spec.get("ci_alpha") or boot.get("alpha") or 0.05)
        ate_ci = None
        if bootstrap_n:
            bootstrap_n = max(10, min(bootstrap_n, 500))
            rng = np.random.default_rng(int(boot.get("seed") or 42))
            ate_bs = []
            est_bs = int(boot.get("n_estimators") or min(200, n_estimators))
            n = int(len(df))
            for _ in range(bootstrap_n):
                idx = rng.integers(0, n, size=n)
                Tb = T[idx]
                Yb = Y[idx]
                Wb = W[idx] if W is not None else None
                Xb = X[idx] if X is not None else None
                try:
                    cb = ICEACausal(n_estimators=est_bs)
                    cb.fit(X=Xb, W=Wb, T=Tb, Y=Yb)
                    cate_b = np.asarray(cb.effect(X=Xb).cate)
                    ate_bs.append(float(np.mean(cate_b)) if cate_b.size else 0.0)
                except Exception:
                    continue
            if len(ate_bs) >= 10:
                lo = float(np.percentile(ate_bs, 100 * (alpha / 2.0)))
                hi = float(np.percentile(ate_bs, 100 * (1.0 - alpha / 2.0)))
                ate_ci = {"method": "bootstrap", "alpha": alpha, "n_boot": int(len(ate_bs)), "lower": lo, "upper": hi}

        # Sensitivity analysis for unmeasured confounding: E-value (approx for continuous outcomes)
        sens = spec.get("sensitivity") or {}
        sensitivity = {}
        if bool(sens.get("e_value", True)):
            sd_y = float(np.std(Y)) if np.std(Y) > 0 else 0.0
            if sd_y > 0:
                # approximate RR from standardized mean difference (VanderWeele-style approximation)
                d = float(ate / sd_y)
                rr = float(np.exp(0.91 * d))
                rr = rr if rr >= 1.0 else (1.0 / max(rr, 1e-9))
                evalue = float(rr + np.sqrt(rr * (rr - 1.0)))

                rr_ci = None
                evalue_ci = None
                if ate_ci:
                    # pick the CI bound closest to 0 (most conservative)
                    bound = ate_ci["lower"] if abs(ate_ci["lower"]) < abs(ate_ci["upper"]) else ate_ci["upper"]
                    d_b = float(bound / sd_y)
                    rr_b = float(np.exp(0.91 * d_b))
                    rr_b = rr_b if rr_b >= 1.0 else (1.0 / max(rr_b, 1e-9))
                    rr_ci = rr_b
                    evalue_ci = float(rr_b + np.sqrt(rr_b * (rr_b - 1.0)))

                sensitivity["e_value"] = {
                    "assumption": "approx_RR_from_SMD",
                    "sd_y": sd_y,
                    "rr_equivalent": rr,
                    "e_value": evalue,
                    "rr_equivalent_ci_bound": rr_ci,
                    "e_value_ci_bound": evalue_ci,
                }
            else:
                sensitivity["e_value"] = {"detail": "sd(Y)=0; cannot compute"}


        # v0.5.5+: Policy learning + fairness audit (best effort, non-breaking)
        # Goal: move from "what happened" (ATE/CATE) to "what to do" (optimal staffing policy)
        # while auditing disparate impact across subgroups (EU AI Act non-discrimination).
        policy_learning = {}
        fairness_audit = {}
        try:
            policy_cfg = spec.get("policy_learning")
            fairness_cfg = spec.get("fairness")
            policy_enabled = str(os.environ.get("ICEA_POLICY_LEARNING_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}
            fairness_enabled = str(os.environ.get("ICEA_FAIRNESS_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}

            if policy_cfg and policy_cfg is not False and not policy_enabled:
                policy_learning = {"available": False, "detail": "ICEA_POLICY_LEARNING_ENABLED must be explicitly enabled"}
                if fairness_cfg and fairness_cfg is not False:
                    fairness_audit = {"available": False, "detail": "ICEA_FAIRNESS_ENABLED requires explicit opt-in and policy_learning output"}

            elif fairness_cfg and fairness_cfg is not False and not policy_cfg:
                fairness_audit = {"available": False, "detail": "ICEA_FAIRNESS_ENABLED requires explicit policy_learning opt-in"}

            elif policy_cfg and policy_cfg is not False and policy_enabled:
                from icea_pipeline.policy_learning import learn_policy_from_marginal_cate

                feat_cols = []
                if isinstance(policy_cfg, dict):
                    feat_cols = list(policy_cfg.get("features") or [])
                if not feat_cols:
                    feat_cols = list(effect_modifiers or []) or list(confounders or [])

                df_pol, pol_out = learn_policy_from_marginal_cate(
                    df,
                    cate=cate,
                    treatment_col=treatment,
                    feature_cols=feat_cols,
                    decision_col=str((policy_cfg or {}).get("decision_col") or "policy_recommend_high"),
                    max_depth=int((policy_cfg or {}).get("max_depth") or 3),
                    min_samples_leaf=int((policy_cfg or {}).get("min_samples_leaf") or 50),
                    threshold=float((policy_cfg or {}).get("threshold") or 0.0),
                )
                # replace df with df_pol so the report can access the decision column
                df = df_pol
                policy_learning = {
                    "available": True,
                    "method": pol_out.method,
                    "treatment": pol_out.treatment_col,
                    "treatment_low": pol_out.treatment_low,
                    "treatment_high": pol_out.treatment_high,
                    "decision_col": pol_out.decision_col,
                    "rule_text": pol_out.rule_text,
                    "recommended_rate": pol_out.recommended_rate,
                    "estimated_mean_gain": pol_out.estimated_mean_gain,
                    "notes": pol_out.notes,
                }

                # v0.6: Policy robustness (E-value style sensitivity on policy value)
                try:
                    sd_y_pol = float(np.std(Y)) if np.std(Y) > 0 else 0.0
                    mg = float(pol_out.estimated_mean_gain)
                    if sd_y_pol > 0:
                        d_pol = float(mg / sd_y_pol)
                        rr_pol = float(np.exp(0.91 * d_pol))
                        rr_pol = rr_pol if rr_pol >= 1.0 else (1.0 / max(rr_pol, 1e-9))
                        e_pol = float(rr_pol + np.sqrt(rr_pol * (rr_pol - 1.0)))
                        policy_learning["robustness"] = {
                            "e_value": {
                                "assumption": "approx_RR_from_SMD_policy_value",
                                "sd_y": sd_y_pol,
                                "rr_equivalent": rr_pol,
                                "e_value": e_pol,
                            }
                        }
                    else:
                        policy_learning["robustness"] = {"e_value": {"detail": "sd(Y)=0; cannot compute"}}
                except Exception:
                    policy_learning["robustness"] = {"e_value": {"detail": "robustness_computation_failed"}}

                # Optional: DoWhy audit layer for the learned policy (best effort).
                # This treats the policy decision as a binary treatment and estimates its effect on Y.
                try:
                    audit_pol = bool((policy_cfg or {}).get("audit_dowhy")) if isinstance(policy_cfg, dict) else False
                    if audit_pol:
                        from dowhy import CausalModel  # type: ignore

                        cols_pol = sorted({pol_out.decision_col, outcome, *confounders, *effect_modifiers})
                        df_pol_a = df[cols_pol].copy()
                        # Minimal graph: confounders -> (T,Y), T -> Y
                        lines = ["digraph {", f'  "{pol_out.decision_col}" -> "{outcome}";']
                        for c in confounders:
                            lines.append(f'  "{c}" -> "{pol_out.decision_col}";')
                            lines.append(f'  "{c}" -> "{outcome}";')
                        lines.append("}")
                        graph_pol = "\n".join(lines)

                        m_pol = CausalModel(data=df_pol_a, treatment=pol_out.decision_col, outcome=outcome, graph=graph_pol)
                        ident = m_pol.identify_effect()
                        est = m_pol.estimate_effect(ident, method_name="backdoor.linear_regression")
                        policy_learning.setdefault("robustness", {})
                        policy_learning["robustness"]["dowhy_policy_ate"] = {
                            "estimate": float(getattr(est, "value", 0.0)),
                            "method": "backdoor.linear_regression",
                        }
                except Exception as e:
                    # do not fail; record error if robustness already exists
                    policy_learning.setdefault("robustness", {})
                    policy_learning["robustness"]["dowhy_policy_ate"] = {"error": f"{e.__class__.__name__}: {str(e)}"}

                if fairness_cfg and fairness_cfg is not False and not fairness_enabled:
                    fairness_audit = {"available": False, "detail": "ICEA_FAIRNESS_ENABLED must be explicitly enabled"}
                elif fairness_cfg and fairness_cfg is not False and fairness_enabled:
                    from icea_pipeline.fairness import audit_fairness_bundle

                    sensitive = []
                    if isinstance(fairness_cfg, dict):
                        sensitive = list(fairness_cfg.get("sensitive_features") or [])
                    if not sensitive:
                        sensitive = [
                            "age",
                            "edad",
                            "sex",
                            "gender",
                            "ethnicity",
                            "race",
                            "ses_index",
                            "socioeconomic_status",
                        ]
                    sensitive = [c for c in sensitive if c in df.columns]
                    if sensitive:
                        # v0.6: institutional fairness metrics via fairlearn (optional)
                        use_fairlearn = None
                        label_col = None
                        label_thr = None
                        if isinstance(fairness_cfg, dict):
                            use_fairlearn = fairness_cfg.get("use_fairlearn")
                            label_col = fairness_cfg.get("label_col")
                            label_thr = fairness_cfg.get("label_threshold")
                        # Env flag can force-enable standardized metrics when deps are installed.
                        env_use = (os.environ.get("FAIRNESS_USE_FAIRLEARN") or "").strip().lower()
                        if env_use in {"1", "true", "yes", "on"}:
                            use_fairlearn = True

                        # If not specified, use outcome as a weak label proxy for equalized-odds
                        # (thresholding continuous outcomes at median). This remains best-effort.
                        if not label_col and outcome in df.columns:
                            label_col = outcome

                        fairness_audit = audit_fairness_bundle(
                            df,
                            decision_col=pol_out.decision_col,
                            sensitive_features=sensitive,
                            min_group_size=int((fairness_cfg or {}).get("min_group_size") or 25),
                            use_fairlearn=use_fairlearn,
                            label_col=label_col,
                            label_threshold=float(label_thr) if label_thr is not None else None,
                        )
                    else:
                        fairness_audit = {
                            "decision_col": pol_out.decision_col,
                            "results": [],
                            "notes": ["no_sensitive_features_available_in_dataset"],
                        }
        except Exception as e:
            # Never abort the causal run for enterprise-only extras.
            policy_learning = {"available": False, "error": f"{e.__class__.__name__}: {str(e)}"}
            fairness_audit = {"available": False, "error": f"{e.__class__.__name__}: {str(e)}"}


        # Optional causal refuters (DoWhy) — enterprise audit layer
        refuters_req = list(spec.get("refuters") or [])
        refuters_strict = bool(spec.get("refuters_strict") or False)
        refuters = {"requested": refuters_req, "available": False, "results": [], "errors": []}

        def _dag_to_dot(edges: list) -> str:
            if not edges:
                # minimal backdoor graph: confounders -> (T,Y) and T -> Y
                lines = ["digraph {", f'  "{treatment}" -> "{outcome}";']
                for c in confounders:
                    lines.append(f'  "{c}" -> "{treatment}";')
                    lines.append(f'  "{c}" -> "{outcome}";')
                lines.append("}")
                return "\n".join(lines)
            lines = ["digraph {"]
            for a, b in edges:
                lines.append(f'  "{a}" -> "{b}";')
            lines.append("}")
            return "\n".join(lines)

        if refuters_req:
            try:
                from dowhy import CausalModel  # type: ignore

                refuters["available"] = True
                graph = _dag_to_dot(dag_edges)
                cols = sorted({treatment, outcome, *confounders, *effect_modifiers})
                df_d = df[cols].copy()

                model = CausalModel(data=df_d, treatment=treatment, outcome=outcome, graph=graph)
                identified = model.identify_effect()
                method_name = str(spec.get("dowhy_estimator") or "backdoor.linear_regression")
                estimate = model.estimate_effect(identified, method_name=method_name)

                # Store baseline DoWhy estimate (audit only; ICEA ATE remains EconML-based)
                try:
                    refuters["baseline_estimate"] = float(getattr(estimate, "value", np.nan))
                except Exception:
                    refuters["baseline_estimate"] = None
                refuters["baseline_method"] = method_name
                refuters["graph"] = graph

                for rname in refuters_req:
                    try:
                        ref = model.refute_estimate(identified, estimate, method_name=str(rname))
                        refuters["results"].append({"refuter": str(rname), "summary": str(ref)})
                    except Exception as e:
                        refuters["errors"].append({"refuter": str(rname), "error": f"{e.__class__.__name__}: {str(e)}"})

                if refuters_strict and refuters["errors"]:
                    return Response({"detail": "One or more refuters failed", "refuters": refuters}, status=400)

            except Exception as e:
                refuters["available"] = False
                refuters["errors"].append({"error": f"DoWhy unavailable: {e.__class__.__name__}: {str(e)}"})
                if refuters_strict:
                    return Response({"detail": "DoWhy is not installed/available", "refuters": refuters}, status=501)

        warnings = []
        warnings.extend(causal_row_warnings)
        if confounders and dag_edges:
            # minimal sanity: encourage explicit confounder paths to treatment/outcome
            for c in confounders:
                has_path = [c, treatment] in dag_edges or [c, outcome] in dag_edges
                if not has_path:
                    warnings.append(f"Confounder '{c}' not connected to treatment/outcome in dag_edges (check DAG).")
        if case_mix_issue:
            warnings.extend(case_mix_issue.warnings)

        summary = {
            "spec": {
                "treatment": treatment,
                "outcome": outcome,
                "confounders": confounders,
                "effect_modifiers": effect_modifiers,
                "dag_edges": dag_edges,
                "grain": grain,
                "target_trial": spec.get("target_trial"),
                "protocol_hash": spec.get("protocol_hash"),
            },
            "n_rows": int(len(df)),
            "ate": ate,
            "causal_available": True,
            "ate_ci": ate_ci,
            "cate": {
                "mean": float(np.mean(cate)) if cate.size else 0.0,
                "std": float(np.std(cate)) if cate.size else 0.0,
                "p50": float(np.percentile(cate, 50)) if cate.size else 0.0,
                "p75": float(np.percentile(cate, 75)) if cate.size else 0.0,
                "p90": float(np.percentile(cate, 90)) if cate.size else 0.0,
            },
            "balance_smd": balance,
            "placebo_ate_on_ri_initial": placebo_ate,
            "shap_top_effect_modifiers": shap_top,
            "sensitivity": sensitivity,
            "policy_learning": policy_learning,
            "fairness_audit": fairness_audit,
            "refuters": refuters,
            "temporal_spec_used": temporal_spec_used,
            "warnings": sorted(set(warnings)),
            "dag_discovery": dag_discovery_out,
        }


        # v0.5.4: CONSORT-emulated Trial Protocol Report (clinical hard audit)
        # Best-effort and non-breaking: failures must never abort the causal run.
        try:
            # Use the same dataframe used for causal estimation (plus optional policy column).
            df_report = df.copy().replace([np.inf, -np.inf], np.nan)
            summary["trial_protocol_report"] = generate_trial_protocol_report(
                df=df_report,
                spec=spec,
                treatment=treatment,
                outcome=outcome,
                confounders=confounders,
                effect_modifiers=effect_modifiers,
                causal_summary=summary,
            )
        except Exception as e:
            summary["trial_protocol_report"] = {
                "detail": "trial_protocol_report_generation_failed",
                "error": f"{e.__class__.__name__}: {str(e)}",
            }

        # Persist the protocol spec (immutable intent). Use a longer prefix to reduce collision risk.
        spec_hash = hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()
        cs_name = f"spec-{spec_hash[:32]}"
        cs, created = CausalSpec.objects.get_or_create(name=cs_name, defaults={"spec": spec})
        if not created:
            # Defensive: if an existing spec has a different protocol_hash, treat it as mismatch.
            try:
                prev = (cs.spec or {}).get("protocol_hash")
                if prev and prev != spec.get("protocol_hash"):
                    return Response(
                        {
                            "detail": "CausalSpec protocol hash mismatch (possible collision or mutation)",
                            "stored": prev,
                            "current": spec.get("protocol_hash"),
                        },
                        status=409,
                    )
            except Exception:
                pass
        run = CausalRun.objects.create(spec=cs, outcome=outcome, treatment=treatment, n_rows=int(len(df)), summary=summary)

        append_audit_event(event_type="causal_run", payload={"run_id": str(run.id), "treatment": treatment, "outcome": outcome, "grain": grain}, context="causal/run")


        return Response({"run_id": str(run.id), "summary": summary})


class CausalReportView(APIView):
    """Retrieve the CONSORT-emulated Trial Protocol Report for a given causal run.

    v0.5.5+: This is the 'auditoría clínica dura' artifact:
      - Cohort flow (CONSORT-emulated + staged eligibility)
      - Semantic missingness (LOINC-attributed)
      - E-value closure metric
      - Human-in-the-loop supervision status (linked from GovernanceDecision)
      - Policy learning + fairness audit (when available)
    """

    permission_classes = [ICEAResearcherPermission]

    def get(self, request):
        run_id = (request.query_params.get("run_id") or "").strip()
        if not run_id:
            return Response({"detail": "run_id query param is required"}, status=400)

        run = CausalRun.objects.filter(id=run_id).first()
        if not run:
            return Response({"detail": "causal run not found", "run_id": run_id}, status=404)

        report = {}
        try:
            report = dict((run.summary or {}).get("trial_protocol_report") or {})
        except Exception:
            report = {}

        # Attach human-in-loop decisions (runtime view; immutable record lives in GovernanceDecision + audit chain)
        qs = GovernanceDecision.objects.filter(causal_run=run).order_by("created_at")
        decisions = []
        for d in qs:
            decisions.append(
                {
                    "id": str(d.id),
                    "created_at": d.created_at,
                    "decision_type": d.decision_type,
                    "actor": d.actor,
                    "rationale": d.rationale,
                    "payload": d.payload,
                }
            )

        status = "unreviewed"
        human_override_flag = False
        if decisions:
            last = decisions[-1]
            dt = str(last.get("decision_type") or "")
            if dt == "approve":
                status = "accepted"
            elif dt == "override":
                status = "modified"
                human_override_flag = True
            elif dt == "reject":
                status = "overridden"
                human_override_flag = True
            elif dt == "note":
                status = "noted"

        human = {
            "status": status,
            "human_override_flag": bool(human_override_flag),
            "decisions": decisions,
            "source": "governance_decisions",
        }

        if not report:
            report = {"detail": "trial_protocol_report_not_available", "human_in_loop": human}
        else:
            report["human_in_loop"] = human

        return Response({"run_id": str(run.id), "trial_protocol_report": report})


class RiskAssessmentWritebackView(APIView):
    """Create (and optionally write back) a FHIR RiskAssessment for an episode/model."""

    # Optional HMAC signing (v0.7.2) + optional anti-replay (v0.7.3).
    permission_classes = [ICEAAdminOrServicePermission, RequiresAntiReplayHMAC]

    # Scoped throttling (v0.7.3)
    throttle_scope = "writeback"

    def post(self, request):
        ser = RiskAssessmentWritebackSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        model_id = payload["model_id"]
        episode_id = int(payload["episode_id"])
        writeback = bool(payload.get("writeback"))

        artifact = ModelArtifact.objects.get(id=model_id)
        ep = PatientEpisode.objects.get(id=episode_id)

        if not ep.fhir_patient_id or not ep.fhir_encounter_id:
            return Response(
                {
                    "detail": "Episode missing fhir_patient_id/fhir_encounter_id. Use ingest(mode=encounter) first.",
                    "episode_id": ep.id,
                },
                status=400,
            )

        fr = getattr(ep, "feature_row", None)
        if not fr:
            return Response({"detail": "No EpisodeFeatureRow. Run build-dataset first.", "episode_id": ep.id}, status=400)

        now = timezone.now().isoformat()
        note_txt = (
            "ICEA+ shadow-only aggregate analytics. Individual RiskAssessment score is suppressed; "
            "not for operational, punitive, staffing, or causal individual use."
        )

        risk_assessment = {
            "resourceType": "RiskAssessment",
            "status": "entered-in-error",
            "method": {"text": "ICEA+ shadow mode: individual score suppressed"},
            "subject": {"reference": f"Patient/{ep.fhir_patient_id}"},
            "encounter": {"reference": f"Encounter/{ep.fhir_encounter_id}"},
            "occurrenceDateTime": now,
            "prediction": [{"outcome": {"text": artifact.target}, "rationale": note_txt}],
            "note": [{"text": note_txt}],
        }

        rec = FHIRWritebackRecord.objects.create(
            episode=ep,
            model_id=artifact.id,
            payload=risk_assessment,
            attempted_writeback=False,
        )

        if writeback and (str(os.environ.get("FHIR_WRITEBACK_ENABLED", "false")).lower() in {"1", "true", "yes"}):
            rec.attempted_writeback = True
            rec.save(update_fields=["attempted_writeback"])
            rec.writeback_ok = False
            rec.writeback_response = {
                "detail": "individual_riskassessment_writeback_blocked_in_shadow_mode",
                "non_individual_use": True,
                "shadow_mode": True,
            }
            rec.save(update_fields=["writeback_ok", "writeback_response"])

        append_audit_event(event_type="fhir_writeback", payload={"record_id": str(rec.id), "episode_id": int(ep.id), "model_id": str(artifact.id), "attempted": bool(rec.attempted_writeback), "ok": bool(rec.writeback_ok)}, context="fhir/writeback/riskassessment")

        return Response(
            {
                "record_id": str(rec.id),
                "episode_id": ep.id,
                "writeback_ok": bool(rec.writeback_ok),
                "status": "shadow_only",
                "prediction": {
                    "target": artifact.target,
                    "pred": None,
                    "base": None,
                    "icea_nursing": None,
                    "score_suppressed": True,
                    "suppression_reason": "individual_riskassessment_writeback_blocked_in_shadow_mode",
                },
                "conformal": None,
                "non_individual_use": True,
                "shadow_mode": True,
                "operational_score": False,
            }
        )


class ConformalPredictView(APIView):
    """Return a model prediction with a conformal (marginal) prediction interval.

    v0.5.5+: This endpoint is the 'bedside-grade' guarantee layer described in the
    v0.5.5+ evaluation document: prediction is returned with an empirically calibrated
    coverage band.
    """

    permission_classes = [ICEAResearcherPermission]

    def post(self, request):
        ser = ConformalPredictSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        p = ser.validated_data

        artifact = ModelArtifact.objects.get(id=p["model_id"])
        ep = PatientEpisode.objects.get(id=int(p["episode_id"]))
        fr = getattr(ep, "feature_row", None)
        if not fr:
            return Response({"detail": "No EpisodeFeatureRow. Run build-dataset first.", "episode_id": ep.id}, status=400)

        interval = {
            "status": "shadow_only",
            "score_suppressed": True,
            "suppression_reason": "individual_prediction_not_operational_or_exportable",
        }

        append_audit_event(
            event_type="conformal_predict",
            payload={"episode_id": int(ep.id), "model_id": str(artifact.id), "target": artifact.target},
            context="predict/conformal",
        )

        return Response(
            {
                "episode_id": int(ep.id),
                "model_id": str(artifact.id),
                "target": artifact.target,
                "pred": None,
                "interval": interval,
                "status": "shadow_only",
                "score_suppressed": True,
                "suppression_reason": "individual_prediction_not_operational_or_exportable",
                "non_individual_use": True,
                "shadow_mode": True,
                "operational_score": False,
            }
        )


class WritebackListView(APIView):
    permission_classes = [ICEAAdminOrServicePermission]

    def get(self, request):
        qs = FHIRWritebackRecord.objects.order_by("-created_at")[:50]
        out = []
        for r in qs:
            out.append(
                {
                    "id": str(r.id),
                    "created_at": r.created_at,
                    "episode_id": None,
                    "model_id": str(r.model_id),
                    "attempted": bool(r.attempted_writeback),
                    "ok": bool(r.writeback_ok),
                    "non_individual_use": True,
                    "shadow_mode": True,
                    "identifier_suppressed": True,
                }
            )
        return Response(out)


class FHIROpisodeQualityView(APIView):
    """Quality/validation summary for ingested FHIR resources (per episode).

    v0.5.1: exposes validation statistics produced by the FHIR Facade.
    Does not modify any data.
    """

    def get(self, request):
        episode_id = request.query_params.get("episode_id")
        if not episode_id:
            return Response({"detail": "episode_id query param is required"}, status=400)
        ep = PatientEpisode.objects.filter(id=int(episode_id)).first()
        if not ep:
            return Response({"detail": "episode not found", "episode_id": episode_id}, status=404)

        qs = RawFHIRResource.objects.filter(episode=ep).order_by("resource_type")
        total = qs.count()
        ok = qs.filter(validation_ok=True).count()
        bad = qs.filter(validation_ok=False)
        by_type = {}
        for r in qs:
            by_type.setdefault(r.resource_type, {"total": 0, "ok": 0, "bad": 0})
            by_type[r.resource_type]["total"] += 1
            if r.validation_ok:
                by_type[r.resource_type]["ok"] += 1
            else:
                by_type[r.resource_type]["bad"] += 1

        top_issues = []
        for r in bad[:50]:
            top_issues.append(
                {
                    "resource_type": r.resource_type,
                    "resource_id": r.resource_id,
                    "issues": r.validation_issues,
                }
            )

        return Response(
            {
                "episode_id": int(ep.id),
                "total": int(total),
                "ok": int(ok),
                "bad": int(total - ok),
                "by_type": by_type,
                "examples": top_issues,
            }
        )


class EntityChangeLogListView(APIView):
    """Row-level lineage for base entities / configuration.

    This is a lightweight, always-on audit layer which complements the cryptographic
    AuditEvent chain and optional django-simple-history tables.

    GET params:
      - model_label (optional): e.g. icea_core.Hospital
      - object_id (optional)
      - limit (optional, default 100, max 500)
    """

    def get(self, request):
        from icea_pipeline.models import EntityChangeLog

        model_label = (request.query_params.get("model_label") or "").strip()
        object_id = (request.query_params.get("object_id") or "").strip()
        try:
            limit = int(request.query_params.get("limit") or 100)
        except Exception:
            limit = 100
        limit = max(1, min(limit, 500))

        qs = EntityChangeLog.objects.order_by("-created_at")
        if model_label:
            qs = qs.filter(model_label=model_label)
        if object_id:
            qs = qs.filter(object_id=object_id)

        out = []
        for r in qs[:limit]:
            out.append(
                {
                    "id": str(r.id),
                    "created_at": r.created_at,
                    "actor": r.actor,
                    "model_label": r.model_label,
                    "object_id": r.object_id,
                    "action": r.action,
                    "changes": r.changes,
                }
            )
        return Response(out)

# -------------------------
# v0.7 additions (Quality Ops + Causal Discovery + Digital Twin + Federated)
# -------------------------


class CausalDiscoverView(APIView):
    """Suggest DAG edges from observed data (best-effort PC algorithm).

    POST /api/v1/causal/discover/

    This endpoint NEVER mutates existing causal specs; it only suggests dag_edges.
    """

    permission_classes = [ICEACausalDiscoverPermission]

    def post(self, request):
        from icea_pipeline.serializers import CausalDiscoverSerializer
        from icea_pipeline.causal_discovery import discover_dag_pc
        from icea_pipeline.models import CausalDiscoveryRun, EpisodeFeatureRow, EpisodeWindowFeatureRow

        ser = CausalDiscoverSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        p = ser.validated_data

        grain = str(p.get("grain") or "episode").strip().lower()
        variables = list(p.get("variables") or [])
        declared_outcome = str(p.get("outcome") or p.get("target") or "").strip()
        alpha = float(p.get("alpha") or 0.05)
        max_cond_set = int(p.get("max_cond_set") or 2)
        forbid_edges = p.get("forbid_edges") or []

        actor = (request.headers.get("X-ICEA-ACTOR") or "").strip()

        if bool(p.get("from_db", True)):
            if grain == "window":
                rows = list(EpisodeWindowFeatureRow.objects.select_related("window", "window__episode").all())
                data = []
                for r in rows:
                    row = dict(r.features)
                    row.update(r.target)
                    row["episode_id"] = int(r.window.episode_id)
                    row["unit_id"] = int(r.window.episode.unit_id)
                    data.append(row)
            else:
                rows = list(EpisodeFeatureRow.objects.select_related("episode").all())
                data = []
                for r in rows:
                    row = dict(r.features)
                    row.update(r.target)
                    row["episode_id"] = int(r.episode_id)
                    row["unit_id"] = int(r.episode.unit_id)
                    data.append(row)
        else:
            data = list(p.get("rows") or [])

        df = pd.DataFrame(data).replace([np.inf, -np.inf], np.nan)
        discovery_frame = pd.DataFrame(data)
        has_row_temporal_spec = "temporal_spec" in discovery_frame.columns if not discovery_frame.empty else False
        has_temporal_context = bool(declared_outcome or has_row_temporal_spec)
        temporal_discovery_issues = []
        if has_temporal_context:
            temporal_discovery_issues = validate_temporal_frame(discovery_frame, target=declared_outcome)
        if temporal_discovery_issues:
            result = {
                "available": False,
                "causal_available": False,
                "dag_edges": [],
                "undirected_edges": [],
                "p_values": {},
                "notes": ["causal_discovery_blocked_by_temporal_guardrails"],
                "warnings": sorted({warning for _, issue in temporal_discovery_issues for warning in issue.warnings}),
                "n_rows": int(len(df)),
            }
            run = CausalDiscoveryRun.objects.create(
                actor=actor,
                method="pc",
                alpha=alpha,
                max_cond_set=max_cond_set,
                grain=grain,
                variables=variables,
                spec={"alpha": alpha, "max_cond_set": max_cond_set, "forbid_edges": forbid_edges},
                result=result,
            )
            append_audit_event(
                event_type="causal_discover_blocked",
                payload={"discovery_run_id": str(run.id), "method": "pc", "grain": grain, "n_rows": int(len(df))},
                context="causal/discover",
            )
            return Response({"discovery_run_id": str(run.id), "result": run.result})

        # Optional filter by unit
        unit_id = p.get("unit_id")
        if unit_id is not None and "unit_id" in df.columns:
            try:
                df = df[df["unit_id"].astype(float) == float(unit_id)]
            except Exception:
                pass

        # Keep only numeric columns for discovery
        for v in variables:
            if v not in df.columns:
                df[v] = np.nan
        df_num = df[variables].apply(pd.to_numeric, errors="coerce").dropna(axis=0, how="any")

        res = discover_dag_pc(df_num, variables=variables, alpha=alpha, max_cond_set=max_cond_set, forbid_edges=forbid_edges)

        run = CausalDiscoveryRun.objects.create(
            actor=actor,
            method=res.method,
            alpha=res.alpha,
            max_cond_set=res.max_cond_set,
            grain=grain,
            variables=res.variables,
            spec={"alpha": res.alpha, "max_cond_set": res.max_cond_set, "forbid_edges": forbid_edges},
            result={
                "dag_edges": res.dag_edges,
                "undirected_edges": res.undirected_edges,
                "p_values": res.p_values,
                "notes": res.notes,
                "n_rows": int(len(df_num)),
            },
        )

        append_audit_event(
            event_type="causal_discover",
            payload={"discovery_run_id": str(run.id), "method": res.method, "grain": grain, "n_rows": int(len(df_num))},
            context="causal/discover",
        )

        return Response({"discovery_run_id": str(run.id), "result": run.result})


class CausalSimulateView(APIView):
    """Counterfactual Digital Twin simulation endpoint.

    POST /api/v1/causal/simulate/

    Uses DML effects; optionally uses a predictive XGB model to attach conformal intervals.
    """

    permission_classes = [ICEASimulatePermission]

    def post(self, request):
        from icea_pipeline.serializers import CausalSimulateSerializer
        from icea_pipeline.models import CounterfactualSimulationRun, CausalRun, EpisodeFeatureRow, EpisodeWindowFeatureRow
        from icea_pipeline.simulate import SimulationScenario, simulate_counterfactual

        ser = CausalSimulateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        p = ser.validated_data

        actor = (request.headers.get("X-ICEA-ACTOR") or "").strip()

        spec = dict(p.get("spec") or {})
        run = None
        if not spec and p.get("run_id"):
            run = CausalRun.objects.filter(id=p["run_id"]).select_related("spec").first()
            if not run or not run.spec:
                return Response({"detail": "causal run not found or missing spec"}, status=404)
            spec = dict(run.spec.spec or {})

        grain = str(spec.get("grain") or spec.get("dataset_grain") or "episode").strip().lower()

        # Load dataset similarly to causal/run
        if grain == "window":
            rows = list(EpisodeWindowFeatureRow.objects.select_related("window", "window__episode").all())
            if not rows:
                return Response({"detail": "No window dataset rows. Run build-windows first."}, status=400)
            data = []
            for r in rows:
                row = dict(r.features)
                row.update(r.target)
                row["episode_id"] = int(r.window.episode_id)
                row["window_index"] = int(r.window.window_index)
                row["unit_id"] = int(r.window.episode.unit_id)
                data.append(row)
        else:
            rows = list(EpisodeFeatureRow.objects.select_related("episode").all())
            if not rows:
                return Response({"detail": "No dataset rows. Run build-dataset first."}, status=400)
            data = []
            for r in rows:
                row = dict(r.features)
                row.update(r.target)
                row["episode_id"] = int(r.episode_id)
                row["unit_id"] = int(r.episode.unit_id)
                data.append(row)

        df_raw = pd.DataFrame(data)
        treatment = str(spec.get("treatment") or "").strip()
        outcome = str(spec.get("outcome") or "delta_ri").strip()
        confounders = list(spec.get("confounders") or [])
        effect_modifiers = list(spec.get("effect_modifiers") or [])
        dag_edges = spec.get("dag_edges") or []
        causal_temporal_issue = validate_causal_temporal_order(spec)
        temporal_dataset_issues = validate_temporal_frame(df_raw, target=outcome)
        if causal_temporal_issue or temporal_dataset_issues:
            warnings = []
            if causal_temporal_issue:
                warnings.extend(causal_temporal_issue.warnings)
            warnings.extend([warning for _, issue in temporal_dataset_issues for warning in issue.warnings])
            result = {
                "available": False,
                "causal_available": False,
                "status": "temporal_leakage_blocked" if causal_temporal_issue else "insufficient_temporal_spec",
                "warnings": sorted(set(warnings)),
                "n_rows": int(len(df_raw)),
            }
            sim = CounterfactualSimulationRun.objects.create(
                actor=actor,
                causal_run=run,
                predictive_model_id=str(p.get("model_id")) if p.get("model_id") else None,
                spec={"grain": grain, "spec": spec, "scenarios": list(p.get("scenarios") or [])},
                result=result,
            )
            return Response({"simulation_run_id": str(sim.id), "result": result})
        df = df_raw.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        dag_discovery = None
        # v0.7: optional causal discovery (PC) to suggest dag_edges from observed unit data.
        # This never mutates the protocol unless explicitly requested (auto_update=true).
        try:
            dd = spec.get("dag_discovery") if isinstance(spec.get("dag_discovery"), dict) else None
            if dd and bool(dd.get("enabled", False)):
                from icea_pipeline.causal_discovery import discover_dag_pc

                dd_vars = list(dd.get("variables") or []) or list({treatment, outcome, *confounders, *effect_modifiers})
                dd_alpha = float(dd.get("alpha") or 0.05)
                dd_max = int(dd.get("max_cond_set") or 2)
                forbid = dd.get("forbid_edges") or []
                # numeric-only + drop rows with missing
                df_dd = df.copy()
                for v in dd_vars:
                    if v not in df_dd.columns:
                        df_dd[v] = np.nan
                df_dd = df_dd[dd_vars].apply(pd.to_numeric, errors="coerce").dropna(axis=0, how="any")
                disc = discover_dag_pc(df_dd, variables=dd_vars, alpha=dd_alpha, max_cond_set=dd_max, forbid_edges=forbid)
                dag_discovery = {
                    "available": True,
                    "method": disc.method,
                    "alpha": disc.alpha,
                    "max_cond_set": disc.max_cond_set,
                    "variables": disc.variables,
                    "suggested_dag_edges": disc.dag_edges,
                    "undirected_edges": disc.undirected_edges,
                    "n_rows_used": int(len(df_dd)),
                    "notes": disc.notes,
                }
                if bool(dd.get("auto_update", False)):
                    spec["dag_edges"] = disc.dag_edges
                    dag_edges = disc.dag_edges
        except Exception as e:
            dag_discovery = {"available": False, "error": f"{e.__class__.__name__}: {str(e)}"}

        scenarios_in = list(p.get("scenarios") or [])
        scenarios: list[SimulationScenario] = []
        for s in scenarios_in:
            if not isinstance(s, dict):
                continue
            name = str(s.get("name") or "scenario").strip()
            set_values = dict(s.get("set") or {})
            delta_values = dict(s.get("delta") or {})
            # coerce to float
            set_values = {k: float(v) for k, v in set_values.items() if k}
            delta_values = {k: float(v) for k, v in delta_values.items() if k}
            scenarios.append(SimulationScenario(name=name, set_values=set_values, delta_values=delta_values))

        model_id = str(p.get("model_id") or "") if p.get("model_id") else None

        result = simulate_counterfactual(df, spec=spec, scenarios=scenarios, model_id=model_id)
        if dag_discovery is not None:
            result["dag_discovery"] = dag_discovery

        sim = CounterfactualSimulationRun.objects.create(
            actor=actor,
            causal_run=run,
            predictive_model_id=model_id if model_id else None,
            spec={"grain": grain, "spec": spec, "scenarios": scenarios_in},
            result=result,
        )

        append_audit_event(
            event_type="causal_simulate",
            payload={"simulation_run_id": str(sim.id), "grain": grain, "n_rows": int(len(df))},
            context="causal/simulate",
        )

        return Response({"simulation_run_id": str(sim.id), "result": result})


class FederatedRoundStartView(APIView):

    # Optional HMAC signing (v0.7.2) + optional anti-replay (v0.7.3).
    permission_classes = [ICEAFederatedPermission, RequiresAntiReplayHMAC]

    # Scoped throttling (v0.7.3)
    throttle_scope = "federated"

    """Start a federated learning round (protocol definition)."""

    def post(self, request):
        from icea_pipeline.serializers import FederatedRoundStartSerializer
        from icea_pipeline.models import FederatedRound

        ser = FederatedRoundStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        p = ser.validated_data

        actor = (request.headers.get("X-ICEA-ACTOR") or "").strip()

        rnd = FederatedRound.objects.create(name=p.get("name") or "federated-round", protocol_spec=p["protocol_spec"], status="open")

        append_audit_event(
            event_type="federated_round_start",
            payload={"round_id": str(rnd.id), "name": rnd.name, "actor": actor},
            context="federated/round/start",
        )

        return Response({"round_id": str(rnd.id), "status": rnd.status, "protocol_spec": rnd.protocol_spec})


class FederatedSubmitUpdateView(APIView):

    # Optional HMAC signing (v0.7.2) + optional anti-replay (v0.7.3).
    permission_classes = [ICEAFederatedPermission, RequiresAntiReplayHMAC]

    # Scoped throttling (v0.7.3)
    throttle_scope = "federated"

    """Submit a client update (model artifact) into a federated round."""

    def post(self, request, round_id: str):
        from icea_pipeline.serializers import FederatedSubmitUpdateSerializer
        from icea_pipeline.models import FederatedRound, FederatedClientUpdate
        from icea_pipeline.federated import verify_signature, save_pickle_artifact
        from icea_core.models import ModelArtifact

        rnd = FederatedRound.objects.filter(id=round_id).first()
        if not rnd:
            return Response({"detail": "federated round not found", "round_id": round_id}, status=404)
        if rnd.status != "open":
            return Response({"detail": "round not open", "status": rnd.status}, status=409)

        ser = FederatedSubmitUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        p = ser.validated_data

        actor = (request.headers.get("X-ICEA-ACTOR") or "").strip()

        # Legacy federated signature (deprecated): keep only for backward compatibility.
        # ENS Alto / Zero-Trust: prefer the unified HMAC(+anti-replay) permission layer.
        sig_ok = True
        legacy_enabled = os.environ.get("ICEA_ENABLE_LEGACY_FED_SIGNATURE", "true").lower() in {"1", "true", "yes"}
        ens_alto = os.environ.get("ICEA_ENS_ALTO_COMPLIANCE", "false").lower() in {"1", "true", "yes"}
        secret = (os.environ.get("ICEA_FEDERATED_SECRET") or "").strip()
        if legacy_enabled and secret and not ens_alto:
            sig = (request.headers.get("X-ICEA-FED-SIG") or "").strip()
            body_bytes = json.dumps(request.data, sort_keys=True).encode("utf-8")
            sig_ok = verify_signature(secret, body_bytes, sig)
            if not sig_ok:
                return Response({"detail": "invalid federated signature"}, status=403)

        model_art = None
        if p.get("model_artifact_id"):
            model_art = ModelArtifact.objects.filter(id=p["model_artifact_id"]).first()
        else:
            b64 = (p.get("artifact_b64") or "").strip()
            if not b64:
                return Response({"detail": "Provide model_artifact_id or artifact_b64"}, status=400)
            try:
                raw = base64.b64decode(b64.encode("utf-8"))
            except Exception:
                return Response({"detail": "artifact_b64 is not valid base64"}, status=400)

            path = save_pickle_artifact(raw, prefix="fed_update")
            # Store as ModelArtifact pointing to raw bytes. We do NOT unpickle here.
            model_art = ModelArtifact.objects.create(
                name="icea-federated-update",
                version="v0.7.0",
                target=str((rnd.protocol_spec or {}).get("outcome") or "delta_ri"),
                features=list((rnd.protocol_spec or {}).get("features") or []),
                model_type="pickle-bytes",
                model_path=path,
                metrics={"meta": p.get("meta") or {}, "federated": True, "unsafe_pickle": True},
            )

        upd = FederatedClientUpdate.objects.create(
            round=rnd,
            client_id=str(p.get("client_id") or ""),
            n_rows=int(p.get("n_rows") or 0),
            model_artifact=model_art,
            signature_ok=bool(sig_ok),
            meta=p.get("meta") or {},
        )

        append_audit_event(
            event_type="federated_submit_update",
            payload={"round_id": str(rnd.id), "update_id": str(upd.id), "client_id": upd.client_id, "n_rows": upd.n_rows},
            context="federated/round/submit",
        )

        return Response({"update_id": str(upd.id), "round_id": str(rnd.id), "signature_ok": bool(sig_ok)})


class FederatedAggregateView(APIView):

    # Optional HMAC signing (v0.7.2) + optional anti-replay (v0.7.3).
    permission_classes = [ICEAFederatedPermission, RequiresAntiReplayHMAC]

    # Scoped throttling (v0.7.3)
    throttle_scope = "federated"

    """Aggregate client updates into a weighted ensemble."""

    def post(self, request, round_id: str):
        from icea_pipeline.serializers import FederatedAggregateSerializer
        from icea_pipeline.models import FederatedRound
        from icea_pipeline.federated import build_ensemble_spec, save_pickle_artifact
        from icea_core.models import ModelArtifact

        rnd = FederatedRound.objects.filter(id=round_id).prefetch_related("updates").first()
        if not rnd:
            return Response({"detail": "federated round not found", "round_id": round_id}, status=404)
        if rnd.status != "open":
            return Response({"detail": "round not open", "status": rnd.status}, status=409)

        ser = FederatedAggregateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        updates = []
        for u in rnd.updates.all():
            if not u.model_artifact_id:
                continue
            updates.append(
                {
                    "model_artifact_id": str(u.model_artifact_id),
                    "n_rows": int(u.n_rows or 0),
                    "client_id": str(u.client_id or ""),
                }
            )

        if not updates:
            return Response({"detail": "no updates to aggregate"}, status=400)

        ens = build_ensemble_spec(updates)
        path = save_pickle_artifact({"ensemble_spec": ens}, prefix="fed_ensemble")

        agg = ModelArtifact.objects.create(
            name="icea-federated-ensemble",
            version="v0.7.0",
            target=str((rnd.protocol_spec or {}).get("outcome") or "delta_ri"),
            features=list((rnd.protocol_spec or {}).get("features") or []),
            model_type="federated_ensemble",
            model_path=path,
            metrics={"federated": True, "ensemble": ens},
        )

        rnd.ensemble_spec = ens
        rnd.aggregated_model = agg
        rnd.status = "aggregated"
        rnd.save(update_fields=["ensemble_spec", "aggregated_model", "status"])

        append_audit_event(
            event_type="federated_aggregate",
            payload={"round_id": str(rnd.id), "aggregated_model_id": str(agg.id), "n_updates": int(len(updates))},
            context="federated/round/aggregate",
        )

        return Response({"round_id": str(rnd.id), "status": rnd.status, "aggregated_model_id": str(agg.id), "ensemble_spec": ens})
