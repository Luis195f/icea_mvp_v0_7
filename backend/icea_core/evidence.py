from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from icea_core.models import ModelArtifact
from icea_pipeline.temporal import CASE_MIX_REQUIRED_DOMAINS, validate_case_mix_spec


INTENDED_USE_SHADOW_AGGREGATE = "shadow_aggregate_research"
REQUIRED_MODEL_LIMITATIONS = frozenset(
    {
        "shadow_aggregate_research_only",
        "not_for_individual_decisioning",
        "not_mdr_production_ready",
    }
)
MINIMUM_LIMITATIONS = [
    "shadow_aggregate_research_only",
    "not_for_individual_decisioning",
    "not_clinically_validated",
    "not_mdr_production_ready",
]
DEFENSIBLE_TEMPORAL_GUARDRAIL_STATUSES = frozenset(
    {
        "passed",
        "temporal_spec_valid",
        "temporal_guardrails_passed",
    }
)
NOT_EVALUATED_TEMPORAL_GUARDRAIL_STATUSES = frozenset(
    {
        "",
        "unknown",
        "not_evaluated",
        "not_evaluated_external_payload",
        "external_payload_temporal_not_defensible",
    }
)
TEMPORAL_SPEC_REQUIRED_STATUSES = frozenset(
    {
        "",
        "unknown",
        "not_evaluated",
        "not_evaluated_external_payload",
        "external_payload_temporal_not_defensible",
        "insufficient_temporal_spec",
    }
)
CASE_MIX_DERIVATION_CANDIDATES = {
    "age": ["age", "patient_age", "age_years"],
    "severity": ["ri_initial", "baseline_severity", "severity", "rothman_index_initial"],
    "comorbidity": ["charlson", "charlson_index", "comorbidity_index", "comorbidity_count"],
    "fragility_or_dependency": ["frailty", "frailty_score", "fragility", "dependency", "dependency_score", "adl", "barthel"],
    "baseline_risk": ["baseline_risk", "predicted_baseline_risk", "ri_initial"],
    "baseline_load": ["baseline_load", "patient_census", "unit_census", "census", "nurse_hppd"],
}
OUTCOME_COMPARABILITY_WARNINGS = frozenset(
    {
        "mixed_outcome_horizons",
        "outcome_window_not_unique",
        "outcome_definition_not_comparable",
        "mixed_temporal_spec_versions",
    }
)


@dataclass(frozen=True)
class ModelEvidenceSummary:
    evidence_status: str
    defensible: bool
    missing_evidence: list[str]
    statuses: list[str]
    intended_use: str | None
    limitations: list[str]
    limitations_status: str
    temporal_spec_version: str | None
    temporal_guardrail_status: str | None
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
            "limitations_status": self.limitations_status,
            "temporal_spec_version": self.temporal_spec_version,
            "temporal_guardrail_status": self.temporal_guardrail_status,
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


def _normalized_feature_names(value: Any) -> list[str]:
    normalized = []
    seen = set()
    for name in _as_list(value):
        if name is None:
            continue
        text = str(name).strip()
        if text and text not in seen:
            normalized.append(text)
            seen.add(text)
    return normalized


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


def derive_case_mix_spec_from_columns(columns: list[str]) -> dict[str, Any] | None:
    normalized = {str(column).lower(): str(column) for column in columns if str(column)}
    domains: dict[str, list[str]] = {}
    variables: list[str] = []
    for domain in sorted(CASE_MIX_REQUIRED_DOMAINS):
        matched = []
        for candidate in CASE_MIX_DERIVATION_CANDIDATES.get(domain, []):
            candidate_lower = candidate.lower()
            for normalized_name, original_name in normalized.items():
                if normalized_name == candidate_lower or normalized_name.startswith(f"{candidate_lower}_"):
                    matched.append(original_name)
        matched = sorted(set(matched))
        if matched:
            domains[domain] = matched
            variables.extend(matched)
    if set(domains.keys()) != set(CASE_MIX_REQUIRED_DOMAINS):
        return None
    return {
        "source": "derived_from_training_data",
        "derivation_method": "column_domain_name_match",
        "domains": domains,
        "variables": sorted(set(variables)),
        "limitations": ["derived_case_mix_requires_clinical_review_before_any_claim"],
    }


def _is_observed_training_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def _observed_training_columns(raw_df: pd.DataFrame, model_df: pd.DataFrame) -> list[str]:
    model_columns = {str(column) for column in model_df.columns}
    observed = []
    for column in raw_df.columns:
        name = str(column)
        if name not in model_columns:
            continue
        series = raw_df[column]
        has_observed_value = any(_is_observed_training_value(value) for value in series.tolist())
        if has_observed_value:
            observed.append(name)
    return sorted(set(observed))


def _case_mix_domain_columns(case_mix_spec: dict[str, Any] | None) -> tuple[dict[str, list[str]], list[str]]:
    if not isinstance(case_mix_spec, dict):
        return {}, []
    mappings: dict[str, list[str]] = {}
    conflicts: list[str] = []
    for container_name in ("domains", "variables"):
        container = case_mix_spec.get(container_name)
        if container_name == "variables" and isinstance(container, list):
            container = {str(variable): str(variable) for variable in container if str(variable)}
        if not isinstance(container, dict):
            continue
        for domain, raw_values in container.items():
            if isinstance(raw_values, str):
                values = [raw_values]
            elif isinstance(raw_values, (list, tuple, set)):
                values = [str(value) for value in raw_values if str(value)]
            else:
                values = []
            if values:
                domain_name = str(domain)
                normalized_values = sorted(set(values))
                if domain_name in mappings and mappings[domain_name] != normalized_values:
                    conflicts.append(f"case_mix_domain_variable_conflict:{domain_name}")
                mappings[domain_name] = normalized_values
    return mappings, sorted(set(conflicts))


def _case_mix_support_issue(case_mix_spec: dict[str, Any] | None, observed_columns: list[str]) -> list[str]:
    domain_columns, conflicts = _case_mix_domain_columns(case_mix_spec)
    observed = set(observed_columns)
    warnings: list[str] = list(conflicts)
    for domain in sorted(CASE_MIX_REQUIRED_DOMAINS):
        columns = domain_columns.get(domain) or []
        if not columns:
            warnings.append(f"case_mix_domain_without_observed_columns:{domain}")
            continue
        unsupported = sorted(set(columns) - observed)
        if unsupported:
            warnings.append(f"case_mix_columns_missing_or_empty:{domain}:{','.join(unsupported)}")
    return warnings


def _parse_temporal_datetime(value: Any) -> pd.Timestamp | None:
    try:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
    except (TypeError, ValueError):
        return None
    return None if pd.isna(parsed) else parsed


def _training_outcome_comparability(records: list[dict[str, Any]], *, target: str) -> dict[str, Any]:
    temporal_specs = [row.get("temporal_spec") for row in records if isinstance(row.get("temporal_spec"), dict)]
    temporal_versions = sorted(
        {str(spec.get("temporal_spec_version")) for spec in temporal_specs if spec.get("temporal_spec_version")}
    )
    horizon_seconds: list[float] = []
    declared_definitions: set[str] = set()
    invalid_windows = 0

    for row, spec in (
        (row, row.get("temporal_spec"))
        for row in records
        if isinstance(row.get("temporal_spec"), dict)
    ):
        outcome_start = _parse_temporal_datetime(spec.get("outcome_window_start"))
        outcome_end = _parse_temporal_datetime(spec.get("outcome_window_end"))
        if outcome_start is None or outcome_end is None or outcome_end <= outcome_start:
            invalid_windows += 1
        else:
            horizon_seconds.append(float((outcome_end - outcome_start).total_seconds()))

        declared_definition = (
            spec.get("outcome_definition")
            or spec.get("outcome_target")
            or row.get("outcome_definition")
            or row.get("outcome_target")
        )
        if declared_definition not in (None, ""):
            declared_definitions.add(str(declared_definition))

    unique_horizon_seconds = sorted(set(horizon_seconds))
    warnings: list[str] = []
    if len(temporal_specs) != len(records) or invalid_windows:
        warnings.append("outcome_window_not_unique")
    if len(unique_horizon_seconds) > 1:
        warnings.extend(["mixed_outcome_horizons", "outcome_window_not_unique"])
    if len(temporal_versions) > 1:
        warnings.extend(["mixed_temporal_spec_versions", "outcome_definition_not_comparable"])
    if declared_definitions and (len(declared_definitions) > 1 or declared_definitions != {str(target)}):
        warnings.append("outcome_definition_not_comparable")

    unique_horizon_hours = [float(seconds / 3600.0) for seconds in unique_horizon_seconds]
    return {
        "status": "comparable" if not warnings else "not_comparable",
        "warnings": sorted(set(warnings)),
        "unique_horizon_hours": unique_horizon_hours,
        "outcome_horizon_hours": unique_horizon_hours[0] if len(unique_horizon_hours) == 1 else None,
        "declared_outcome_definitions": sorted(declared_definitions),
        "unique_temporal_spec_versions": temporal_versions,
        "temporal_spec_row_count": int(len(temporal_specs)),
        "invalid_outcome_window_count": int(invalid_windows),
    }


def summarize_model_evidence(artifact: ModelArtifact) -> ModelEvidenceSummary:
    metrics = dict(artifact.metrics or {})
    evidence = _metrics_evidence(metrics)
    validation_metrics = _validation_metrics(metrics, evidence)
    calibration_summary = _calibration_summary(metrics, evidence)
    case_mix_spec = _case_mix_spec(metrics, evidence)
    case_mix_issue = validate_case_mix_spec(case_mix_spec if isinstance(case_mix_spec, dict) else None)

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
    temporal_guardrail_status = str(
        _first_non_empty(evidence.get("temporal_guardrail_status"), metrics.get("temporal_guardrail_status")) or ""
    ).strip()
    outcome_comparability_status = str(
        _first_non_empty(evidence.get("outcome_comparability_status"), metrics.get("outcome_comparability_status")) or ""
    ).strip()
    outcome_comparability_warnings = sorted(
        {
            str(value)
            for value in _as_list(
                _first_non_empty(
                    evidence.get("outcome_comparability_warnings"),
                    metrics.get("outcome_comparability_warnings"),
                )
            )
            if str(value) in OUTCOME_COMPARABILITY_WARNINGS
        }
    )
    evidence_feature_names = _normalized_feature_names(evidence.get("feature_names"))
    artifact_feature_names = _normalized_feature_names(artifact.features)
    evidence_features_missing_from_artifact = sorted(set(evidence_feature_names) - set(artifact_feature_names))
    artifact_features_missing_from_evidence = sorted(set(artifact_feature_names) - set(evidence_feature_names))
    feature_names_warnings = []
    if evidence_feature_names and evidence_feature_names != artifact_feature_names:
        if not evidence_features_missing_from_artifact and not artifact_features_missing_from_evidence:
            feature_names_warnings.append("feature_names_order_mismatch")
        if evidence_features_missing_from_artifact:
            feature_names_warnings.append(
                f"evidence_features_missing_from_artifact:{','.join(evidence_features_missing_from_artifact)}"
            )
        if artifact_features_missing_from_evidence:
            feature_names_warnings.append(
                f"artifact_features_missing_from_evidence:{','.join(artifact_features_missing_from_evidence)}"
            )
    limitations = [str(value) for value in _as_list(_first_non_empty(evidence.get("limitations"), metrics.get("limitations"))) if str(value)]
    missing_required_limitations = sorted(REQUIRED_MODEL_LIMITATIONS - set(limitations))

    pack = {
        "model_id": str(artifact.id) if artifact.id else None,
        "artifact_created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "dataset_fingerprint": dataset_fingerprint,
        "training_row_count": _first_non_empty(evidence.get("training_row_count"), metrics.get("training_row_count")),
        "validation_row_count": validation_row_count,
        "validation_unavailable_reason": validation_unavailable_reason,
        "feature_names": evidence_feature_names,
        "artifact_feature_names": artifact_feature_names,
        "feature_names_match": bool(evidence_feature_names) and evidence_feature_names == artifact_feature_names,
        "feature_names_warnings": feature_names_warnings,
        "evidence_features_missing_from_artifact": evidence_features_missing_from_artifact,
        "artifact_features_missing_from_evidence": artifact_features_missing_from_evidence,
        "temporal_spec_version": temporal_spec_version,
        "temporal_guardrail_status": temporal_guardrail_status or None,
        "outcome_definition": _first_non_empty(evidence.get("outcome_definition"), metrics.get("outcome_definition"), artifact.target),
        "outcome_window": _first_non_empty(evidence.get("outcome_window"), metrics.get("outcome_window")),
        "outcome_comparability_status": outcome_comparability_status or None,
        "outcome_comparability_warnings": outcome_comparability_warnings,
        "case_mix_spec": case_mix_spec,
        "case_mix_unavailable_reason": _first_non_empty(evidence.get("case_mix_unavailable_reason"), metrics.get("case_mix_unavailable_reason")),
        "intended_use": _first_non_empty(evidence.get("intended_use"), metrics.get("intended_use")),
        "non_individual_use": _first_non_empty(evidence.get("non_individual_use"), metrics.get("non_individual_use")),
        "shadow_mode": _first_non_empty(evidence.get("shadow_mode"), metrics.get("shadow_mode")),
        "calibration_summary": calibration_summary,
        "calibration_unavailable_reason": calibration_unavailable_reason,
        "validation_metrics": validation_metrics,
        "limitations": limitations,
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
    if case_mix_issue:
        missing.append("case_mix_spec_sufficient")
    if evidence_feature_names and evidence_feature_names != artifact_feature_names:
        missing.extend(["feature_names_mismatch", *feature_names_warnings])
    if temporal_guardrail_status not in DEFENSIBLE_TEMPORAL_GUARDRAIL_STATUSES:
        if temporal_guardrail_status in NOT_EVALUATED_TEMPORAL_GUARDRAIL_STATUSES:
            missing.append("temporal_guardrail_not_evaluated")
        else:
            missing.append(f"temporal_guardrail_not_passed:{temporal_guardrail_status}")
        if temporal_guardrail_status in TEMPORAL_SPEC_REQUIRED_STATUSES:
            missing.append("temporal_spec_required")
    if outcome_comparability_status == "not_comparable" or outcome_comparability_warnings:
        missing.extend(outcome_comparability_warnings or ["outcome_definition_not_comparable"])
    if missing_required_limitations:
        missing.extend(
            [
                "required_limitations",
                f"missing_required_limitations:{','.join(missing_required_limitations)}",
            ]
        )
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
    if temporal_guardrail_status not in DEFENSIBLE_TEMPORAL_GUARDRAIL_STATUSES:
        statuses.append(
            "temporal_guardrail_not_evaluated"
            if temporal_guardrail_status in NOT_EVALUATED_TEMPORAL_GUARDRAIL_STATUSES
            else "temporal_guardrail_not_passed"
        )
    if outcome_comparability_status == "not_comparable" or outcome_comparability_warnings:
        statuses.extend(["outcome_definition_not_comparable", *outcome_comparability_warnings])
    if missing_required_limitations:
        statuses.append("limitations_incomplete")
    if case_mix_issue:
        statuses.append("case_mix_insufficient")
    if evidence_feature_names and evidence_feature_names != artifact_feature_names:
        statuses.extend(["feature_names_mismatch", *feature_names_warnings])
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

    case_mix_status = "case_mix_available" if not case_mix_issue else "case_mix_insufficient"
    calibration_status = "calibration_available" if pack.get("calibration_summary") else "calibration_unavailable"
    validation_status = "validation_available" if pack.get("validation_metrics") else "validation_unavailable"
    limitations_status = "limitations_complete" if not missing_required_limitations else "limitations_incomplete"

    return ModelEvidenceSummary(
        evidence_status=evidence_status,
        defensible=bool(defensible),
        missing_evidence=sorted(missing),
        statuses=sorted(set(statuses)),
        intended_use=pack.get("intended_use"),
        limitations=list(pack.get("limitations") or []),
        limitations_status=limitations_status,
        temporal_spec_version=temporal_spec_version,
        temporal_guardrail_status=temporal_guardrail_status or None,
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
    temporal_guardrail_warnings: list[str] | None = None,
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
    outcome_comparability = _training_outcome_comparability(records, target=target)
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
    feature_names = {str(feature) for feature in features}
    observed_columns = [column for column in _observed_training_columns(raw_df, model_df) if column in feature_names]
    declared_feature_missing = sorted(set(str(feature) for feature in features) - set(str(column) for column in raw_df.columns))
    effective_case_mix_spec = dict(case_mix_spec) if isinstance(case_mix_spec, dict) else None
    if effective_case_mix_spec is None:
        effective_case_mix_spec = derive_case_mix_spec_from_columns(observed_columns)
    case_mix_issue = validate_case_mix_spec(effective_case_mix_spec)
    case_mix_support_warnings = _case_mix_support_issue(effective_case_mix_spec, observed_columns)
    case_mix_unavailable_reason = None
    if case_mix_issue or case_mix_support_warnings:
        case_mix_unavailable_reason = ";".join(
            sorted(set(list(case_mix_issue.warnings if case_mix_issue else []) + case_mix_support_warnings))
        )
        effective_case_mix_spec = None

    evidence = {
        "dataset_fingerprint": dataset_fingerprint,
        "dataset_hash": dataset_fingerprint,
        "training_row_count": int(len(model_df) - int(validation_row_count or 0)) if validation_row_count is not None else int(len(model_df)),
        "validation_row_count": validation_row_count,
        "feature_names": list(features),
        "observed_feature_columns": observed_columns,
        "feature_warnings": ["declared_feature_missing_from_payload"] if declared_feature_missing else [],
        "declared_features_missing_from_payload": declared_feature_missing,
        "temporal_spec_version": temporal_versions[0] if len(temporal_versions) == 1 else None,
        "temporal_guardrail_status": temporal_guardrail_status,
        "temporal_guardrail_warnings": sorted(
            set(list(temporal_guardrail_warnings or []) + list(outcome_comparability["warnings"]))
        ),
        "outcome_definition": target,
        "outcome_comparability_status": outcome_comparability["status"],
        "outcome_comparability_warnings": outcome_comparability["warnings"],
        "outcome_window": {
            "source": "row_temporal_spec",
            "row_count": int(len(temporal_specs)),
            "unique_temporal_spec_versions": temporal_versions,
            "unique_horizon_hours": outcome_comparability["unique_horizon_hours"],
            "horizon_hours": outcome_comparability["outcome_horizon_hours"],
            "invalid_outcome_window_count": outcome_comparability["invalid_outcome_window_count"],
            "declared_outcome_definitions": outcome_comparability["declared_outcome_definitions"],
            "examples": outcome_examples,
        }
        if temporal_specs
        else None,
        "case_mix_spec": effective_case_mix_spec,
        "case_mix_unavailable_reason": case_mix_unavailable_reason,
        "intended_use": INTENDED_USE_SHADOW_AGGREGATE,
        "non_individual_use": True,
        "shadow_mode": True,
        "calibration_summary": {"method": "conformal_residual_quantile", "conformal": conformal} if conformal else None,
        "calibration_unavailable_reason": None if conformal else "insufficient_calibration_sample_or_not_computed",
        "validation_metrics": validation_metrics or None,
        "validation_unavailable_reason": None if validation_metrics else "validation_metrics_not_computed",
        "limitations": MINIMUM_LIMITATIONS
        + ([] if not case_mix_issue and not case_mix_support_warnings else ["case_mix_insufficient"])
        + ([] if conformal else ["calibration_unavailable"]),
        "source_commit_unavailable_reason": "source_commit_not_captured_by_training_runtime",
    }
    return evidence
