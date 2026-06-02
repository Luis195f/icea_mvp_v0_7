from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from icea_core.models import ModelArtifact


INTENDED_USE_SHADOW_AGGREGATE = "shadow_aggregate_research"
MINIMUM_LIMITATIONS = [
    "shadow_aggregate_research_only",
    "not_for_individual_decisioning",
    "not_clinically_validated",
    "not_mdr_production_ready",
]


@dataclass(frozen=True)
class ModelEvidenceSummary:
    evidence_status: str
    defensible: bool
    missing_evidence: list[str]
    statuses: list[str]
    intended_use: str | None
    limitations: list[str]
    temporal_spec_version: str | None
    case_mix_status: str
    calibration_status: str
    validation_status: str
    evidence_pack: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_status": self.evidence_status,
            "defensible": self.defensible,
            "missing_evidence": self.missing_evidence,
            "statuses": self.statuses,
            "intended_use": self.intended_use,
            "limitations": self.limitations,
            "temporal_spec_version": self.temporal_spec_version,
            "case_mix_status": self.case_mix_status,
            "calibration_status": self.calibration_status,
            "validation_status": self.validation_status,
            "evidence_pack": self.evidence_pack,
        }


def stable_json_hash(obj: Any) -> str:
    dumped = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def dataset_fingerprint_for_records(records: list[dict[str, Any]], *, target: str, features: list[str], grain: str) -> str:
    return stable_json_hash(
        {
            "grain": grain,
            "target": target,
            "features": list(features),
            "records": records,
        }
    )


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _metrics_evidence(metrics: dict[str, Any]) -> dict[str, Any]:
    evidence = metrics.get("evidence_pack")
    return dict(evidence) if isinstance(evidence, dict) else {}


def _validation_metrics(metrics: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any] | None:
    explicit = evidence.get("validation_metrics") or metrics.get("validation_metrics")
    if isinstance(explicit, dict) and explicit:
        return dict(explicit)
    values = {key: metrics.get(key) for key in ("rmse", "mae", "r2", "auc") if metrics.get(key) is not None}
    return values or None


def _calibration_summary(metrics: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any] | None:
    explicit = evidence.get("calibration_summary") or metrics.get("calibration_summary")
    if isinstance(explicit, dict) and explicit:
        return dict(explicit)
    conformal = metrics.get("conformal")
    if isinstance(conformal, dict) and conformal:
        return {"method": "conformal_interval_residual_summary", "conformal": dict(conformal)}
    return None


def _case_mix_spec(metrics: dict[str, Any], evidence: dict[str, Any]) -> Any:
    return _first_non_empty(evidence.get("case_mix_spec"), metrics.get("case_mix_spec"))


def summarize_model_evidence(artifact: ModelArtifact) -> ModelEvidenceSummary:
    metrics = dict(artifact.metrics or {})
    evidence = _metrics_evidence(metrics)
    validation_metrics = _validation_metrics(metrics, evidence)
    calibration_summary = _calibration_summary(metrics, evidence)
    case_mix_spec = _case_mix_spec(metrics, evidence)

    dataset_fingerprint = _first_non_empty(
        evidence.get("dataset_fingerprint"),
        evidence.get("dataset_hash"),
        metrics.get("dataset_fingerprint"),
        metrics.get("dataset_hash"),
    )
    validation_row_count = _first_non_empty(
        evidence.get("validation_row_count"),
        metrics.get("validation_row_count"),
        (metrics.get("conformal") or {}).get("calibration_size") if isinstance(metrics.get("conformal"), dict) else None,
    )
    validation_unavailable_reason = _first_non_empty(
        evidence.get("validation_unavailable_reason"),
        metrics.get("validation_unavailable_reason"),
    )
    calibration_unavailable_reason = _first_non_empty(
        evidence.get("calibration_unavailable_reason"),
        metrics.get("calibration_unavailable_reason"),
    )
    temporal_spec_version = _first_non_empty(
        evidence.get("temporal_spec_version"),
        metrics.get("temporal_spec_version"),
    )

    pack = {
        "model_id": str(artifact.id) if artifact.id else None,
        "artifact_created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "dataset_fingerprint": dataset_fingerprint,
        "training_row_count": _first_non_empty(evidence.get("training_row_count"), metrics.get("training_row_count")),
        "validation_row_count": validation_row_count,
        "validation_unavailable_reason": validation_unavailable_reason,
        "feature_names": list(artifact.features or []),
        "temporal_spec_version": temporal_spec_version,
        "temporal_guardrail_status": _first_non_empty(evidence.get("temporal_guardrail_status"), metrics.get("temporal_guardrail_status")),
        "outcome_definition": _first_non_empty(evidence.get("outcome_definition"), metrics.get("outcome_definition"), artifact.target),
        "outcome_window": _first_non_empty(evidence.get("outcome_window"), metrics.get("outcome_window")),
        "case_mix_spec": case_mix_spec,
        "case_mix_unavailable_reason": _first_non_empty(evidence.get("case_mix_unavailable_reason"), metrics.get("case_mix_unavailable_reason")),
        "intended_use": _first_non_empty(evidence.get("intended_use"), metrics.get("intended_use")),
        "non_individual_use": _first_non_empty(evidence.get("non_individual_use"), metrics.get("non_individual_use")),
        "shadow_mode": _first_non_empty(evidence.get("shadow_mode"), metrics.get("shadow_mode")),
        "calibration_summary": calibration_summary,
        "calibration_unavailable_reason": calibration_unavailable_reason,
        "validation_metrics": validation_metrics,
        "limitations": _as_list(_first_non_empty(evidence.get("limitations"), metrics.get("limitations"))),
        "provenance": _first_non_empty(evidence.get("provenance"), metrics.get("provenance")),
        "source_commit": _first_non_empty(evidence.get("source_commit"), metrics.get("source_commit")),
        "source_commit_unavailable_reason": _first_non_empty(
            evidence.get("source_commit_unavailable_reason"),
            metrics.get("source_commit_unavailable_reason"),
        ),
    }

    missing: list[str] = []
    for field in (
        "model_id",
        "artifact_created_at",
        "dataset_fingerprint",
        "training_row_count",
        "feature_names",
        "temporal_spec_version",
        "temporal_guardrail_status",
        "outcome_definition",
        "outcome_window",
        "intended_use",
        "non_individual_use",
        "shadow_mode",
        "limitations",
    ):
        if pack.get(field) in (None, "", [], {}):
            missing.append(field)
    if pack.get("validation_row_count") in (None, "") and not pack.get("validation_unavailable_reason"):
        missing.append("validation_row_count_or_unavailable_reason")
    if not pack.get("validation_metrics") and not pack.get("validation_unavailable_reason"):
        missing.append("validation_metrics_or_unavailable_reason")
    if not pack.get("calibration_summary") and not pack.get("calibration_unavailable_reason"):
        missing.append("calibration_summary_or_unavailable_reason")
    if not pack.get("case_mix_spec") and not pack.get("case_mix_unavailable_reason"):
        missing.append("case_mix_spec_or_unavailable_reason")
    if not pack.get("provenance") and not pack.get("source_commit") and not pack.get("source_commit_unavailable_reason"):
        missing.append("provenance_or_source_commit_or_unavailable_reason")

    statuses: list[str] = []
    if missing:
        statuses.append("evidence_incomplete")
    if pack.get("non_individual_use") is not True:
        statuses.append("individual_use_not_blocked")
    if pack.get("shadow_mode") is not True:
        statuses.append("shadow_mode_missing")
    if pack.get("intended_use") != INTENDED_USE_SHADOW_AGGREGATE:
        statuses.append("intended_use_not_shadow_aggregate_research")
    if not pack.get("case_mix_spec"):
        statuses.append("case_mix_insufficient")
    if not pack.get("calibration_summary"):
        statuses.append("calibration_unavailable")
    if not pack.get("validation_metrics"):
        statuses.append("validation_unavailable")

    evidence_status = "evidence_complete" if not missing else "evidence_incomplete"
    blocking = set(statuses) - {"calibration_unavailable", "validation_unavailable"}
    if "calibration_unavailable" in statuses or "validation_unavailable" in statuses:
        blocking.update({"calibration_unavailable", "validation_unavailable"} & set(statuses))
    defensible = evidence_status == "evidence_complete" and not blocking
    if not defensible and "model_not_defensible" not in statuses:
        statuses.append("model_not_defensible")

    case_mix_status = "case_mix_available" if pack.get("case_mix_spec") else "case_mix_insufficient"
    calibration_status = "calibration_available" if pack.get("calibration_summary") else "calibration_unavailable"
    validation_status = "validation_available" if pack.get("validation_metrics") else "validation_unavailable"

    return ModelEvidenceSummary(
        evidence_status=evidence_status,
        defensible=bool(defensible),
        missing_evidence=sorted(missing),
        statuses=sorted(set(statuses)),
        intended_use=pack.get("intended_use"),
        limitations=list(pack.get("limitations") or []),
        temporal_spec_version=temporal_spec_version,
        case_mix_status=case_mix_status,
        calibration_status=calibration_status,
        validation_status=validation_status,
        evidence_pack=pack,
    )


def build_training_evidence_metadata(
    *,
    raw_df: pd.DataFrame,
    model_df: pd.DataFrame,
    features: list[str],
    target: str,
    dataset_grain: str,
    metrics: dict[str, Any],
    temporal_guardrail_status: str,
    case_mix_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = raw_df.where(pd.notnull(raw_df), None).to_dict(orient="records")
    dataset_fingerprint = dataset_fingerprint_for_records(
        records,
        target=target,
        features=features,
        grain=dataset_grain,
    )
    temporal_specs = [row.get("temporal_spec") for row in records if isinstance(row.get("temporal_spec"), dict)]
    temporal_versions = sorted({str(spec.get("temporal_spec_version")) for spec in temporal_specs if spec.get("temporal_spec_version")})
    outcome_examples = [
        {
            "outcome_window_start": spec.get("outcome_window_start"),
            "outcome_window_end": spec.get("outcome_window_end"),
            "outcome_status": spec.get("outcome_status"),
        }
        for spec in temporal_specs[:3]
    ]

    validation_metrics = {key: metrics.get(key) for key in ("rmse", "mae", "r2", "auc") if metrics.get(key) is not None}
    conformal = metrics.get("conformal") if isinstance(metrics.get("conformal"), dict) else None
    validation_row_count = conformal.get("calibration_size") if conformal else None

    evidence = {
        "dataset_fingerprint": dataset_fingerprint,
        "dataset_hash": dataset_fingerprint,
        "training_row_count": int(len(model_df) - int(validation_row_count or 0)) if validation_row_count is not None else int(len(model_df)),
        "validation_row_count": validation_row_count,
        "feature_names": list(features),
        "temporal_spec_version": temporal_versions[0] if len(temporal_versions) == 1 else None,
        "temporal_guardrail_status": temporal_guardrail_status,
        "outcome_definition": target,
        "outcome_window": {
            "source": "row_temporal_spec",
            "row_count": int(len(temporal_specs)),
            "unique_temporal_spec_versions": temporal_versions,
            "examples": outcome_examples,
        }
        if temporal_specs
        else None,
        "case_mix_spec": case_mix_spec,
        "case_mix_unavailable_reason": None if case_mix_spec else "case_mix_spec_not_declared_for_training_dataset",
        "intended_use": INTENDED_USE_SHADOW_AGGREGATE,
        "non_individual_use": True,
        "shadow_mode": True,
        "calibration_summary": {"method": "conformal_residual_quantile", "conformal": conformal} if conformal else None,
        "calibration_unavailable_reason": None if conformal else "insufficient_calibration_sample_or_not_computed",
        "validation_metrics": validation_metrics or None,
        "validation_unavailable_reason": None if validation_metrics else "validation_metrics_not_computed",
        "limitations": MINIMUM_LIMITATIONS
        + ([] if case_mix_spec else ["case_mix_spec_absent"])
        + ([] if conformal else ["calibration_unavailable"]),
        "source_commit_unavailable_reason": "source_commit_not_captured_by_training_runtime",
    }
    return evidence
