from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db.models import Max
from django.utils import timezone

from icea_core.aggregation import (
    MIN_AGGREGATE_EPISODES,
    MIN_STAFF_FOR_STAFF_DIMENSION,
    aggregate_scored_rows,
    governance_export_metadata,
)
from icea_core.evidence import INTENDED_USE_SHADOW_AGGREGATE, ModelEvidenceSummary, summarize_model_evidence
from icea_core.models import (
    ICEAPlusComputation,
    ICEAPlusFollowupRecord,
    ModelArtifact,
    PatientEpisode,
)
from icea_core.scoring import redact_shadow_score_response, score_icea_plus, select_formula
from icea_pipeline.audit import append_audit_event
from icea_pipeline.models import EpisodeWindow, FHIRWritebackRecord, NormalizedObservation, NormalizedProcedure


INTERNAL_AGGREGATE_ROW_KEY = "_aggregate_row"
WRITEBACK_MODEL_EVIDENCE_WARNING = "writeback_summary_blocked_by_current_model_evidence"
WRITEBACK_BASELINE_EVIDENCE_WARNING = "writeback_summary_blocked_by_current_baseline_evidence"
WRITEBACK_MIXED_BASELINE_WARNING = "writeback_summary_mixed_baseline_models_not_aggregable"


class ScoringBlockedError(ValueError):
    def __init__(self, result: dict[str, Any]):
        self.result = dict(result)
        super().__init__(str(result.get("detail") or "scoring_blocked"))


@dataclass
class FollowupEvaluation:
    evidence_types: list[str]
    evidence_summary: dict[str, Any]
    support: dict[str, Any]
    warnings: list[str]
    sufficient_for_rescore: bool
    followup_status: str
    last_followup_at: datetime | None
    feature_snapshot_hash: str


def _stable_json_hash(obj: Any) -> str:
    dumped = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _jsonable_request(data: dict[str, Any]) -> dict[str, Any]:
    def _coerce(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, list):
            return [_coerce(item) for item in value]
        if isinstance(value, dict):
            return {str(key): _coerce(item) for key, item in value.items()}
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    cleaned: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if value in (None, "", [], {}):
            continue
        cleaned[key] = _coerce(value)
    return cleaned


def _patient_key(episode: PatientEpisode, row: dict[str, Any]) -> str:
    return str(episode.external_patient_id or row.get("patient_key") or episode.id)


def _initial_state(row_status: str | None) -> str:
    if row_status == "provisional":
        return "immediate_provisional"
    if row_status == "complete":
        return "complete"
    if row_status == "failed":
        return "failed"
    return "insufficient_evidence"


def _derive_current_state(record: ICEAPlusFollowupRecord) -> str:
    if record.followup_status == "enriched_followup" and record.enriched_result:
        return "enriched_followup"
    if record.followup_status in {"stale", "failed", "insufficient_evidence"}:
        return record.followup_status
    if record.initial_state:
        return record.initial_state
    return "pending_followup"


def _formula_flags(formula_version: str | None) -> tuple[bool, bool]:
    formula = select_formula(formula_version)
    status = str((formula.spec or {}).get("status") or getattr(formula.record, "status", "") or "pilot").lower()
    pilot_like = status not in {"production", "validated"}
    return pilot_like, pilot_like


def _get_feature_row(episode: PatientEpisode):
    try:
        return getattr(episode, "feature_row")
    except Exception:
        return None


def _feature_snapshot(episode: PatientEpisode) -> tuple[str, dict[str, Any]]:
    feature_row = _get_feature_row(episode)
    if feature_row is None:
        return "", {}
    payload = {
        "schema_hash": feature_row.schema_hash,
        "feature_version": feature_row.feature_version,
        "features": dict(feature_row.features or {}),
        "target": dict(feature_row.target or {}),
    }
    return _stable_json_hash(payload), payload


def _base_time(record: ICEAPlusFollowupRecord) -> datetime | None:
    if record.last_rescore_at is not None:
        return record.last_rescore_at
    if record.initial_computation is not None and record.initial_computation.created_at is not None:
        return record.initial_computation.created_at
    return record.created_at


def _latest(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _nurse_reliability_warning(result_row: dict[str, Any]) -> list[str]:
    reliability = float(((result_row.get("aggregation") or {}).get("nurse_reliability")) or 0.0)
    shares = dict((result_row.get("aggregation") or {}).get("nurse_shares") or {})
    if reliability < 0.60 or not shares:
        return ["non_individual_use_team_or_unit_only", "low_support"]
    return ["non_individual_use_team_or_unit_only"]


def _compact_score_payload(result_row: dict[str, Any], *, scored_at: datetime | None) -> dict[str, Any]:
    if not result_row:
        return {}
    lineage = dict(result_row.get("lineage") or {})
    return {
        "status": "shadow_only",
        "source_status": result_row.get("status"),
        "score": None,
        "raw_score": None,
        "score_suppressed": True,
        "suppression_reason": "patient_episode_score_is_not_operational_or_exportable",
        "confidence": dict(result_row.get("confidence") or {}),
        "warnings": list(result_row.get("warnings") or []),
        "flags": {
            **dict(result_row.get("flags") or {}),
            "non_individual_use": True,
            "shadow_mode": True,
            "operational_score": False,
        },
        "lineage": {
            "formula_version": lineage.get("formula_version"),
            "formula_protocol_hash": lineage.get("formula_protocol_hash"),
            "model_id": lineage.get("model_id"),
            "model_version": lineage.get("model_version"),
            "causal_spec_hash": lineage.get("causal_spec_hash"),
            "source": dict(lineage.get("source") or {}),
        },
        "computed_at": _iso(scored_at),
    }


def _row_identity(row: dict[str, Any]) -> str:
    return str(row.get("row_id") or row.get("episode_id") or "")


def _aggregate_rows_by_identity(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _row_identity(row): dict(row)
        for row in list(result.get("_aggregate_rows") or [])
        if isinstance(row, dict) and _row_identity(row)
    }


def _with_internal_aggregate_row(public_row: dict[str, Any], aggregate_row: dict[str, Any] | None) -> dict[str, Any]:
    stored = dict(public_row)
    if isinstance(aggregate_row, dict):
        stored[INTERNAL_AGGREGATE_ROW_KEY] = dict(aggregate_row)
    return stored


def _public_stored_result(result_row: dict[str, Any]) -> dict[str, Any]:
    public = dict(result_row or {})
    public.pop(INTERNAL_AGGREGATE_ROW_KEY, None)
    return public


def _aggregate_stored_result(result_row: dict[str, Any]) -> dict[str, Any]:
    aggregate_row = (result_row or {}).get(INTERNAL_AGGREGATE_ROW_KEY)
    if isinstance(aggregate_row, dict):
        return dict(aggregate_row)
    return _public_stored_result(result_row)


def _artifact_case_mix_spec(artifact: ModelArtifact) -> dict[str, Any] | None:
    metrics = artifact.metrics or {}
    evidence = metrics.get("evidence_pack") if isinstance(metrics.get("evidence_pack"), dict) else {}
    value = evidence.get("case_mix_spec") or metrics.get("case_mix_spec")
    return dict(value) if isinstance(value, dict) else None


def _model_evidence_payload(evidence: ModelEvidenceSummary) -> dict[str, Any]:
    return {
        "evidence_status": evidence.evidence_status,
        "defensible": evidence.defensible,
        "missing_evidence": list(evidence.missing_evidence),
        "intended_use": evidence.intended_use or INTENDED_USE_SHADOW_AGGREGATE,
    }


def _writeback_summary_block_payload(
    *,
    artifact: ModelArtifact,
    formula_version: str,
    formula_protocol_hash: str,
    records: list[ICEAPlusFollowupRecord],
    detail: str,
    warnings: list[str],
    evidence: ModelEvidenceSummary,
    baseline_model_id: str | None = None,
    baseline_evidence: ModelEvidenceSummary | None = None,
    baseline_missing_evidence: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "detail": detail,
        "status": detail,
        "model_id": str(artifact.id),
        "formula_version": formula_version,
        "formula_protocol_hash": formula_protocol_hash,
        **_model_evidence_payload(evidence),
        "primary_model_evidence_status": evidence.evidence_status,
        "warnings": sorted(set(warnings)),
        "summary": {
            "records": int(len(records)),
            "group_count": 0,
            "coverage": 0.0,
            "suppressed_cells": 0,
            "min_cell_count": MIN_AGGREGATE_EPISODES,
            "scored_aggregate": None,
            "no_score_due_to_model_evidence": True,
        },
        "scored_aggregate": None,
        "suppressed": True,
        "results": [],
        "non_individual_use": True,
        "shadow_mode": True,
        "intended_use": INTENDED_USE_SHADOW_AGGREGATE,
    }
    if baseline_model_id:
        payload.update(
            {
                "baseline_model_id": baseline_model_id,
                "baseline_model_evidence_status": (
                    baseline_evidence.evidence_status if baseline_evidence is not None else "not_found"
                ),
                "baseline_model_not_defensible": baseline_evidence is None or not baseline_evidence.defensible,
                "baseline_model_missing_evidence": (
                    list(baseline_evidence.missing_evidence)
                    if baseline_evidence is not None
                    else list(baseline_missing_evidence or ["model_artifact_not_found"])
                ),
            }
        )
    return payload


def _result_baseline_model_id(result_row: dict[str, Any]) -> str | None:
    public_row = _public_stored_result(result_row or {})
    aggregate_row = _aggregate_stored_result(result_row or {})
    for row in (aggregate_row, public_row):
        value = (row.get("lineage") or {}).get("baseline_model_id")
        if value:
            return str(value)
    return None


def _record_baseline_model_id(record: ICEAPlusFollowupRecord) -> str | None:
    candidates = [
        (record.initial_request or {}).get("baseline_model_id"),
        ((record.provenance or {}).get("initial_request") or {}).get("baseline_model_id"),
        ((record.provenance or {}).get("enriched_request") or {}).get("baseline_model_id"),
        _result_baseline_model_id(record.initial_result or {}),
        _result_baseline_model_id(record.enriched_result or {}),
    ]
    values = {str(value) for value in candidates if value not in (None, "")}
    return sorted(values)[0] if len(values) == 1 else ("__mixed__" if values else None)


def _validated_uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def validate_model_evidence_for_writeback_summary(
    *,
    artifact: ModelArtifact,
    formula_version: str,
    formula_protocol_hash: str,
    records: list[ICEAPlusFollowupRecord],
) -> dict[str, Any] | None:
    evidence = summarize_model_evidence(artifact)
    record_model_ids = {str(record.model_id) for record in records}
    if record_model_ids - {str(artifact.id)}:
        return _writeback_summary_block_payload(
            artifact=artifact,
            formula_version=formula_version,
            formula_protocol_hash=formula_protocol_hash,
            records=records,
            detail="mixed_models_not_aggregable",
            warnings=["writeback_summary_mixed_models_not_aggregable"],
            evidence=evidence,
        )
    if not evidence.defensible:
        return _writeback_summary_block_payload(
            artifact=artifact,
            formula_version=formula_version,
            formula_protocol_hash=formula_protocol_hash,
            records=records,
            detail="model_not_defensible",
            warnings=[WRITEBACK_MODEL_EVIDENCE_WARNING, *evidence.statuses],
            evidence=evidence,
        )

    baseline_modes = {_record_baseline_model_id(record) for record in records}
    if "__mixed__" in baseline_modes or len(baseline_modes) > 1:
        return _writeback_summary_block_payload(
            artifact=artifact,
            formula_version=formula_version,
            formula_protocol_hash=formula_protocol_hash,
            records=records,
            detail="mixed_baseline_models_not_aggregable",
            warnings=[WRITEBACK_MIXED_BASELINE_WARNING],
            evidence=evidence,
        )

    baseline_model_id = next(iter(baseline_modes), None)
    if baseline_model_id is None:
        return None
    validated_baseline_id = _validated_uuid(baseline_model_id)
    baseline_model = ModelArtifact.objects.filter(id=validated_baseline_id).first() if validated_baseline_id else None
    if baseline_model is None:
        return _writeback_summary_block_payload(
            artifact=artifact,
            formula_version=formula_version,
            formula_protocol_hash=formula_protocol_hash,
            records=records,
            detail="baseline_model_not_found",
            warnings=[WRITEBACK_BASELINE_EVIDENCE_WARNING],
            evidence=evidence,
            baseline_model_id=baseline_model_id,
            baseline_missing_evidence=["model_artifact_not_found"],
        )
    baseline_evidence = summarize_model_evidence(baseline_model)
    if not baseline_evidence.defensible:
        return _writeback_summary_block_payload(
            artifact=artifact,
            formula_version=formula_version,
            formula_protocol_hash=formula_protocol_hash,
            records=records,
            detail="baseline_model_not_defensible",
            warnings=[WRITEBACK_BASELINE_EVIDENCE_WARNING, *baseline_evidence.statuses],
            evidence=evidence,
            baseline_model_id=str(baseline_model.id),
            baseline_evidence=baseline_evidence,
        )
    return None


def _computation_from_result(
    *,
    artifact: ModelArtifact,
    result: dict[str, Any],
    grain: str,
    status: str,
    stage: str,
    linked_initial_computation_id: str | None = None,
) -> ICEAPlusComputation:
    summary = dict(result.get("summary") or {})
    summary["longitudinal_stage"] = stage
    if linked_initial_computation_id:
        summary["linked_initial_computation_id"] = linked_initial_computation_id
    request_hash = ""
    rows = list(result.get("results") or [])
    if rows:
        request_hash = str((((rows[0] or {}).get("lineage") or {}).get("source") or {}).get("request_hash") or "")
    return ICEAPlusComputation.objects.create(
        formula_version=str(result.get("formula_version") or ""),
        model=artifact,
        grain=grain,
        rows=int(summary.get("rows_requested") or len(rows)),
        status=status,
        summary=summary,
        request_hash=request_hash,
    )


def _record_queryset(*, episode_id: int, model_id: str, formula_version: str | None = None):
    qs = ICEAPlusFollowupRecord.objects.select_related(
        "episode",
        "model",
        "initial_computation",
        "enriched_computation",
    ).filter(episode_id=episode_id, model_id=model_id)
    if formula_version:
        qs = qs.filter(formula_version=formula_version)
    return qs


def get_followup_record(
    *,
    episode_id: int,
    model_id: str,
    formula_version: str | None = None,
) -> ICEAPlusFollowupRecord | None:
    return _record_queryset(
        episode_id=episode_id,
        model_id=model_id,
        formula_version=formula_version,
    ).order_by("-updated_at", "-created_at").first()


def _upsert_initial_record(
    *,
    episode: PatientEpisode,
    artifact: ModelArtifact,
    computation: ICEAPlusComputation,
    row: dict[str, Any],
    request_config: dict[str, Any],
) -> ICEAPlusFollowupRecord:
    lineage = dict(row.get("lineage") or {})
    formula_version = str(lineage.get("formula_version") or computation.formula_version or "icea_plus_v1")
    protocol_hash = str(lineage.get("formula_protocol_hash") or "")
    snapshot_hash, _ = _feature_snapshot(episode)
    shadow_mode, exploratory_only = _formula_flags(formula_version)

    record, _ = ICEAPlusFollowupRecord.objects.get_or_create(
        episode=episode,
        model=artifact,
        formula_version=formula_version,
        formula_protocol_hash=protocol_hash,
        defaults={
            "grain": "episode",
            "patient_key": _patient_key(episode, row),
            "initial_computation": computation,
            "initial_state": _initial_state(str(row.get("status") or "")),
            "followup_status": "pending_followup",
            "current_state": _initial_state(str(row.get("status") or "")),
            "initial_request": _jsonable_request(request_config),
            "initial_result": row,
            "feature_snapshot_hash": snapshot_hash,
            "warnings": sorted(set(list(row.get("warnings") or []) + _nurse_reliability_warning(row))),
            "provenance": {
                "formulaVersion": formula_version,
                "protocolHash": protocol_hash,
                "modelId": str(artifact.id),
                "modelVersion": artifact.version,
                "initial_request": _jsonable_request(request_config),
                "initial_request_hash": str((lineage.get("source") or {}).get("request_hash") or ""),
            },
            "non_individual_use": True,
            "shadow_mode": shadow_mode,
            "exploratory_only": exploratory_only,
        },
    )
    updated_fields: list[str] = []
    if not record.initial_result:
        record.initial_result = row
        record.initial_computation = computation
        record.initial_state = _initial_state(str(row.get("status") or ""))
        record.feature_snapshot_hash = snapshot_hash
        record.initial_request = _jsonable_request(request_config)
        updated_fields.extend(
            ["initial_result", "initial_computation", "initial_state", "feature_snapshot_hash", "initial_request"]
        )
    if not record.patient_key:
        record.patient_key = _patient_key(episode, row)
        updated_fields.append("patient_key")
    merged_warnings = sorted(set(list(record.warnings or []) + list(row.get("warnings") or []) + _nurse_reliability_warning(row)))
    if merged_warnings != list(record.warnings or []):
        record.warnings = merged_warnings
        updated_fields.append("warnings")
    record.current_state = _derive_current_state(record)
    updated_fields.append("current_state")
    if updated_fields:
        record.save(update_fields=sorted(set(updated_fields + ["updated_at"])))
    return record


def persist_initial_followup_records(
    *,
    artifact: ModelArtifact,
    result: dict[str, Any],
    computation: ICEAPlusComputation,
    request_config: dict[str, Any],
) -> list[ICEAPlusFollowupRecord]:
    public_result = redact_shadow_score_response(result)
    aggregate_rows = _aggregate_rows_by_identity(result)
    rows = [row for row in (public_result.get("results") or []) if row.get("episode_id") is not None]
    if not rows:
        return []
    episode_ids = sorted({int(row["episode_id"]) for row in rows})
    episodes = {
        ep.id: ep
        for ep in PatientEpisode.objects.filter(id__in=episode_ids).select_related("unit")
    }
    records: list[ICEAPlusFollowupRecord] = []
    for row in rows:
        episode = episodes.get(int(row["episode_id"]))
        if episode is None:
            continue
        stored_row = _with_internal_aggregate_row(row, aggregate_rows.get(_row_identity(row)))
        records.append(
            _upsert_initial_record(
                episode=episode,
                artifact=artifact,
                computation=computation,
                row=stored_row,
                request_config=request_config,
            )
        )
    return records


def _score_episode_from_request(
    *,
    episode: PatientEpisode,
    artifact: ModelArtifact,
    request_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    score_kwargs = {
        "model_artifact": artifact,
        "grain": "episode",
        "from_db": True,
        "rows": None,
        "reference_rows": None,
        "formula_version": request_config.get("formula_version") or None,
        "nurse_cols": request_config.get("nurse_cols"),
        "outcome_goal": request_config.get("outcome_goal"),
        "causal_run_id": str(request_config.get("causal_run_id")) if request_config.get("causal_run_id") else None,
        "causal_spec_override": request_config.get("causal_spec"),
        "baseline_model_id": str(request_config.get("baseline_model_id")) if request_config.get("baseline_model_id") else None,
        "episode_ids": [int(episode.id)],
        "unit_id": None,
        "date_from": None,
        "date_to": None,
    }
    raw_result = score_icea_plus(**score_kwargs)
    if raw_result.get("detail"):
        raise ScoringBlockedError(raw_result)
    result = redact_shadow_score_response(raw_result)
    row = next((candidate for candidate in (result.get("results") or []) if int(candidate.get("episode_id") or 0) == int(episode.id)), None)
    if row is None:
        raise ValueError("episode_not_returned_by_score")
    aggregate_row = _aggregate_rows_by_identity(raw_result).get(_row_identity(row))
    return result, _with_internal_aggregate_row(row, aggregate_row)


def ensure_followup_record(
    *,
    episode: PatientEpisode,
    artifact: ModelArtifact,
    request_config: dict[str, Any] | None = None,
) -> ICEAPlusFollowupRecord:
    normalized_request = {
        "grain": "episode",
        "from_db": True,
        **_jsonable_request(request_config or {}),
    }
    formula_version = str(normalized_request.get("formula_version") or "")
    record = get_followup_record(
        episode_id=int(episode.id),
        model_id=str(artifact.id),
        formula_version=formula_version or None,
    )
    if record and record.initial_result:
        return record

    result, row = _score_episode_from_request(
        episode=episode,
        artifact=artifact,
        request_config=normalized_request,
    )
    computation = _computation_from_result(
        artifact=artifact,
        result=result,
        grain="episode",
        status="ok",
        stage="initial_followup_bootstrap",
    )
    append_audit_event(
        event_type="icea_plus_followup_bootstrap",
        payload={
            "episode_id": int(episode.id),
            "model_id": str(artifact.id),
            "computation_id": str(computation.id),
            "formula_version": str(result.get("formula_version") or ""),
        },
        context="icea-plus/followup/bootstrap",
    )
    return _upsert_initial_record(
        episode=episode,
        artifact=artifact,
        computation=computation,
        row=row,
        request_config=normalized_request,
    )


def evaluate_followup(record: ICEAPlusFollowupRecord) -> FollowupEvaluation:
    episode = record.episode
    feature_snapshot_hash, _ = _feature_snapshot(episode)
    base_time = _base_time(record)

    obs_qs = NormalizedObservation.objects.filter(episode=episode).exclude(effective_dt__isnull=True)
    proc_qs = NormalizedProcedure.objects.filter(episode=episode).exclude(performed_dt__isnull=True)
    window_qs = EpisodeWindow.objects.filter(episode=episode)
    wb_qs = FHIRWritebackRecord.objects.filter(episode=episode, model_id=record.model_id)

    total_obs = int(obs_qs.count())
    total_proc = int(proc_qs.count())
    total_windows = int(window_qs.count())
    total_writebacks = int(wb_qs.count())

    new_obs = int(obs_qs.filter(effective_dt__gt=base_time).count()) if base_time else total_obs
    new_proc = int(proc_qs.filter(performed_dt__gt=base_time).count()) if base_time else total_proc
    new_windows = int(window_qs.filter(end_dt__gt=base_time).count()) if base_time else total_windows

    latest_obs = obs_qs.aggregate(value=Max("effective_dt")).get("value")
    latest_proc = proc_qs.aggregate(value=Max("performed_dt")).get("value")
    latest_window = window_qs.aggregate(value=Max("end_dt")).get("value")
    latest_writeback = wb_qs.aggregate(value=Max("created_at")).get("value")

    feature_row = _get_feature_row(episode)
    target = dict((feature_row.target if feature_row is not None else {}) or {})
    outcome_available = bool(
        target.get(record.model.target) is not None
        or (record.model.target == "delta_ri" and episode.discharge_date is not None)
    )
    feature_snapshot_changed = bool(
        feature_snapshot_hash and feature_snapshot_hash != str(record.enriched_snapshot_hash or record.feature_snapshot_hash or "")
    )

    evidence_types: list[str] = []
    if outcome_available:
        evidence_types.append("episode_outcome")
    if total_obs > 0:
        evidence_types.append("normalized_observations")
    if total_proc > 0:
        evidence_types.append("normalized_procedures")
    if total_windows > 0:
        evidence_types.append("window_features")
    if total_writebacks > 0:
        evidence_types.append("fhir_writeback")

    new_evidence_types: list[str] = []
    if new_obs > 0:
        new_evidence_types.append("normalized_observations")
    if new_proc > 0:
        new_evidence_types.append("normalized_procedures")
    if new_windows > 0:
        new_evidence_types.append("window_features")
    if feature_snapshot_changed:
        new_evidence_types.append("feature_snapshot")

    last_followup_at = _latest(
        episode.discharge_date,
        latest_obs,
        latest_proc,
        latest_window,
        latest_writeback,
    )

    warnings = list(_nurse_reliability_warning(record.enriched_result or record.initial_result or {}))
    if not outcome_available:
        warnings.append("followup_outcome_unavailable")
    if not new_evidence_types:
        warnings.append("no_new_supported_followup_evidence")
    if not total_obs and not total_proc and not total_windows:
        warnings.append("followup_signals_missing")

    sufficient_for_rescore = bool(outcome_available and (new_obs > 0 or new_proc > 0 or new_windows > 0 or feature_snapshot_changed))

    if record.enriched_result and record.last_rescore_at and not sufficient_for_rescore:
        followup_status = "enriched_followup"
    elif sufficient_for_rescore:
        followup_status = "stale"
    elif evidence_types:
        followup_status = "insufficient_evidence"
    else:
        followup_status = "pending_followup"

    support = {
        "coverage": 1.0 if outcome_available else 0.0,
        "outcome_available": outcome_available,
        "observation_count": total_obs,
        "procedure_count": total_proc,
        "window_count": total_windows,
        "writeback_count": total_writebacks,
        "new_observation_count": new_obs,
        "new_procedure_count": new_proc,
        "new_window_count": new_windows,
        "new_evidence_types": new_evidence_types,
        "feature_snapshot_changed": feature_snapshot_changed,
        "baseline_time": _iso(base_time),
    }
    evidence_summary = {
        "base_time": _iso(base_time),
        "latest_observation_at": _iso(latest_obs),
        "latest_procedure_at": _iso(latest_proc),
        "latest_window_end_at": _iso(latest_window),
        "latest_writeback_at": _iso(latest_writeback),
        "feature_snapshot_changed": feature_snapshot_changed,
        "new_evidence_types": new_evidence_types,
    }

    return FollowupEvaluation(
        evidence_types=evidence_types,
        evidence_summary=evidence_summary,
        support=support,
        warnings=sorted(set(warnings)),
        sufficient_for_rescore=sufficient_for_rescore,
        followup_status=followup_status,
        last_followup_at=last_followup_at,
        feature_snapshot_hash=feature_snapshot_hash,
    )


def update_record_followup_state(
    *,
    record: ICEAPlusFollowupRecord,
    evaluation: FollowupEvaluation,
) -> ICEAPlusFollowupRecord:
    record.evidence_types = evaluation.evidence_types
    record.evidence_summary = evaluation.evidence_summary
    record.support = evaluation.support
    record.last_followup_at = evaluation.last_followup_at
    record.followup_status = evaluation.followup_status
    record.warnings = sorted(set(list(record.warnings or []) + list(evaluation.warnings or [])))
    record.current_state = _derive_current_state(record)
    record.save(
        update_fields=[
            "evidence_types",
            "evidence_summary",
            "support",
            "last_followup_at",
            "followup_status",
            "warnings",
            "current_state",
            "updated_at",
        ]
    )
    return record


def ingest_followup(
    *,
    episode: PatientEpisode,
    artifact: ModelArtifact,
    request_config: dict[str, Any] | None = None,
) -> ICEAPlusFollowupRecord:
    record = ensure_followup_record(episode=episode, artifact=artifact, request_config=request_config)
    evaluation = evaluate_followup(record)
    record = update_record_followup_state(record=record, evaluation=evaluation)
    append_audit_event(
        event_type="icea_plus_followup_ingest",
        payload={
            "record_id": str(record.id),
            "episode_id": int(record.episode_id),
            "model_id": str(record.model_id),
            "followup_status": record.followup_status,
            "evidence_types": list(record.evidence_types or []),
        },
        context="icea-plus/followup/ingest",
    )
    return record


def rescore_followup(
    *,
    episode: PatientEpisode,
    artifact: ModelArtifact,
    request_config: dict[str, Any] | None = None,
) -> ICEAPlusFollowupRecord:
    record = ensure_followup_record(episode=episode, artifact=artifact, request_config=request_config)
    evaluation = evaluate_followup(record)
    record = update_record_followup_state(record=record, evaluation=evaluation)

    if not evaluation.sufficient_for_rescore:
        return record

    merged_request = dict(record.initial_request or {})
    merged_request.update(_jsonable_request(request_config or {}))
    merged_request["formula_version"] = record.formula_version

    try:
        result, row = _score_episode_from_request(
            episode=episode,
            artifact=artifact,
            request_config=merged_request,
        )
    except Exception as exc:
        record.followup_status = "failed"
        record.current_state = _derive_current_state(record)
        record.warnings = sorted(set(list(record.warnings or []) + [f"rescore_failed:{exc.__class__.__name__}"]))
        record.last_followup_at = evaluation.last_followup_at
        record.save(update_fields=["followup_status", "current_state", "warnings", "last_followup_at", "updated_at"])
        if isinstance(exc, ScoringBlockedError):
            raise
        return record

    computation = _computation_from_result(
        artifact=artifact,
        result=result,
        grain="episode",
        status="enriched_followup",
        stage="enriched_followup",
        linked_initial_computation_id=str(record.initial_computation_id or ""),
    )
    now = timezone.now()
    record.enriched_computation = computation
    record.enriched_result = row
    record.enriched_snapshot_hash = evaluation.feature_snapshot_hash
    record.followup_status = "enriched_followup"
    record.last_rescore_at = now
    record.last_followup_at = evaluation.last_followup_at
    record.warnings = sorted(set(list(record.warnings or []) + list(row.get("warnings") or []) + list(evaluation.warnings or [])))
    record.provenance = {
        **dict(record.provenance or {}),
        "enriched_request": merged_request,
        "last_rescore_request_hash": str((((row.get("lineage") or {}).get("source") or {}).get("request_hash")) or ""),
        "linked_initial_computation_id": str(record.initial_computation_id or ""),
        "latest_enriched_computation_id": str(computation.id),
    }
    record.current_state = "enriched_followup"
    record.save(
        update_fields=[
            "enriched_computation",
            "enriched_result",
            "enriched_snapshot_hash",
            "followup_status",
            "last_rescore_at",
            "last_followup_at",
            "warnings",
            "provenance",
            "current_state",
            "updated_at",
        ]
    )
    append_audit_event(
        event_type="icea_plus_followup_rescore",
        payload={
            "record_id": str(record.id),
            "episode_id": int(record.episode_id),
            "model_id": str(record.model_id),
            "initial_computation_id": str(record.initial_computation_id or ""),
            "enriched_computation_id": str(computation.id),
            "formula_version": record.formula_version,
        },
        context="icea-plus/followup/rescore",
    )
    return record


def _effective_result(record: ICEAPlusFollowupRecord) -> dict[str, Any]:
    if record.followup_status == "enriched_followup" and record.enriched_result:
        return _public_stored_result(record.enriched_result or {})
    return _public_stored_result(record.initial_result or {})


def _effective_aggregate_result(record: ICEAPlusFollowupRecord) -> dict[str, Any]:
    if record.followup_status == "enriched_followup" and record.enriched_result:
        return _aggregate_stored_result(record.enriched_result or {})
    return _aggregate_stored_result(record.initial_result or {})


def build_patient_summary(record: ICEAPlusFollowupRecord) -> dict[str, Any]:
    effective_result = _effective_result(record)
    model_evidence = summarize_model_evidence(record.model)
    initial_computed_at = record.initial_computation.created_at if record.initial_computation else record.created_at
    enriched_computed_at = record.enriched_computation.created_at if record.enriched_computation else record.last_rescore_at

    initial_score = _compact_score_payload(record.initial_result or {}, scored_at=initial_computed_at)
    enriched_score = _compact_score_payload(record.enriched_result or {}, scored_at=enriched_computed_at) if record.enriched_result else None
    current_score = enriched_score if record.followup_status == "enriched_followup" and enriched_score else initial_score

    delta_score = None

    return {
        "record_id": str(record.id),
        "episode_id": int(record.episode_id),
        "patient_key": None,
        "unit_id": int(record.episode.unit_id),
        "score_states": {
            "initial": record.initial_state,
            "followup": record.followup_status,
            "current": record.current_state,
        },
        "initial_score": initial_score,
        "enriched_score": enriched_score,
        "current_score": current_score,
        "comparison": {
            "delta_score": delta_score,
            "score_suppressed": True,
            "suppression_reason": "patient_episode_score_is_not_operational_or_exportable",
            "initial_computation_id": str(record.initial_computation_id or ""),
            "enriched_computation_id": str(record.enriched_computation_id or ""),
        },
        "warnings": sorted(
            set(
                list(record.warnings or [])
                + list(effective_result.get("warnings") or [])
                + ([] if model_evidence.defensible else [WRITEBACK_MODEL_EVIDENCE_WARNING])
            )
        ),
        "support": dict(record.support or {}),
        "evidence": {
            "types": list(record.evidence_types or []),
            "summary": dict(record.evidence_summary or {}),
            "model": _model_evidence_payload(model_evidence),
        },
        "provenance": {
            "formulaVersion": record.formula_version,
            "protocolHash": record.formula_protocol_hash,
            "modelId": str(record.model_id),
            "modelVersion": str((record.provenance or {}).get("modelVersion") or getattr(record.model, "version", "")),
            "initial_request_hash": str((record.provenance or {}).get("initial_request_hash") or ""),
            "linked_initial_computation_id": str(record.initial_computation_id or ""),
            "latest_enriched_computation_id": str(record.enriched_computation_id or ""),
        },
        "timestamps": {
            "initial_scored_at": _iso(initial_computed_at),
            "last_followup_at": _iso(record.last_followup_at),
            "last_rescore_at": _iso(record.last_rescore_at),
            "updated_at": _iso(record.updated_at),
        },
        "non_individual_use": bool(record.non_individual_use),
        "shadow_mode": bool(record.shadow_mode),
        "exploratory_only": bool(record.exploratory_only),
        "operational_score": False,
    }


def build_summary_writeback(
    *,
    artifact: ModelArtifact,
    group_by: str,
    unit_id: int | None = None,
    formula_version: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    formula = select_formula(formula_version)
    qs = ICEAPlusFollowupRecord.objects.select_related("episode", "model").filter(
        model=artifact,
        formula_version=formula.version,
    )
    if unit_id is not None:
        qs = qs.filter(episode__unit_id=unit_id)
    if date_from is not None:
        qs = qs.filter(episode__admission_date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(episode__admission_date__lte=date_to)
    records = list(qs)

    requested_group_by = str(group_by or "unit")
    effective_group_by = requested_group_by
    warnings: list[str] = []
    if requested_group_by == "team":
        effective_group_by = "unit"
        warnings.append("team_writeback_not_explicitly_modeled_falling_back_to_unit")
    if requested_group_by == "shift":
        effective_group_by = "unit"
        warnings.append("shift_writeback_requires_window_followup_falling_back_to_unit")
    require_staff_count = requested_group_by in {"team", "shift"}

    evidence_block = validate_model_evidence_for_writeback_summary(
        artifact=artifact,
        formula_version=formula.version,
        formula_protocol_hash=formula.protocol_hash,
        records=records,
    )
    if evidence_block is not None:
        evidence_block["requested_group_by"] = requested_group_by
        evidence_block["effective_group_by"] = effective_group_by
        evidence_block["warnings"] = sorted(set(list(evidence_block.get("warnings") or []) + warnings))
        return evidence_block

    rows = []
    for record in records:
        row = _effective_aggregate_result(record)
        if row:
            rows.append(row)
    aggregated = aggregate_scored_rows(
        rows=rows,
        group_by=effective_group_by,
        epsilon=float((((formula.spec or {}).get("aggregation") or {}).get("epsilon")) or 1e-6),
        enforce_suppression=True,
        min_cell_count=MIN_AGGREGATE_EPISODES,
        min_staff_count=MIN_STAFF_FOR_STAFF_DIMENSION,
        require_staff_count=require_staff_count,
        case_mix_spec=_artifact_case_mix_spec(artifact) or (formula.spec or {}).get("case_mix_spec"),
    )
    suppressed_cells = int(sum(1 for row in aggregated if row.get("suppressed")))
    state_counts = {
        "immediate_provisional": int(qs.filter(initial_state="immediate_provisional").count()),
        "complete": int(qs.filter(current_state="complete").count()),
        "enriched_followup": int(qs.filter(current_state="enriched_followup").count()),
        "insufficient_evidence": int(qs.filter(current_state="insufficient_evidence").count()),
        "stale": int(qs.filter(current_state="stale").count()),
        "failed": int(qs.filter(current_state="failed").count()),
    }
    return {
        "model_id": str(artifact.id),
        "formula_version": formula.version,
        "formula_protocol_hash": formula.protocol_hash,
        "requested_group_by": requested_group_by,
        "effective_group_by": effective_group_by,
        "status_counts": state_counts,
        "summary": {
            "records": int(qs.count()),
            "group_count": int(len(aggregated)),
            "coverage": float(sum((row.get("coverage") or 0.0) for row in aggregated) / len(aggregated)) if aggregated else 0.0,
            "suppressed_cells": suppressed_cells,
            "min_cell_count": MIN_AGGREGATE_EPISODES,
        },
        "governance": governance_export_metadata(
            aggregation_level=effective_group_by,
            min_cell_count=MIN_AGGREGATE_EPISODES,
            suppressed_cells=suppressed_cells,
            formula_version=formula.version,
            model_lineage={
                "model_id": str(artifact.id),
                "model_version": artifact.version,
                "formula_protocol_hash": formula.protocol_hash,
            },
            generated_at=timezone.now().isoformat(),
        ),
        "warnings": sorted(set(warnings + [warning for record in qs for warning in (record.warnings or [])])),
        "results": aggregated,
        "non_individual_use": True,
        "shadow_mode": bool(_formula_flags(formula.version)[0]),
        "exploratory_only": bool(_formula_flags(formula.version)[1]),
    }
