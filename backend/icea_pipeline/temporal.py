from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

import pandas as pd
from django.utils import timezone


TEMPORAL_SPEC_VERSION = "icea_temporal_v1"
REQUIRED_TEMPORAL_FIELDS = {
    "index_time",
    "feature_window_start",
    "feature_window_end",
    "outcome_window_start",
    "outcome_window_end",
    "censoring_reason",
    "temporal_spec_version",
}
LEGACY_OUTCOME_STATUS = "legacy_outcome_not_defensible"
TEMPORAL_STATUSES = {
    "insufficient_temporal_spec",
    "temporal_leakage_blocked",
    "legacy_outcome_not_defensible",
    "insufficient_outcome_evidence",
    "case_mix_insufficient",
    "post_outcome_predictor_blocked",
    "blocked_by_reference_temporal_spec",
}
DANGEROUS_PREDICTOR_NAMES = {
    "length_of_stay",
    "los",
    "discharge_status",
    "discharge_disposition",
    "discharge_date",
    "ri_final",
}
CASE_MIX_REQUIRED_DOMAINS = {
    "age",
    "severity",
    "comorbidity",
    "fragility_or_dependency",
    "baseline_risk",
    "baseline_load",
}


@dataclass
class TemporalIssue:
    status: str
    warnings: list[str]
    flags: dict[str, Any]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt_timezone.utc)
        return value.astimezone(dt_timezone.utc)
    if _is_missing_value(value, empty_string=True):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt_timezone.utc)
    return parsed.astimezone(dt_timezone.utc)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _is_missing_value(value: Any, *, empty_string: bool = False) -> bool:
    if value is None:
        return True
    if empty_string and isinstance(value, str) and not value.strip():
        return True
    try:
        missing = pd.isna(value)
    except Exception:
        return False
    if isinstance(missing, bool):
        return missing
    try:
        return bool(missing)
    except Exception:
        return False


def _target_trial_has_temporal_order(spec: dict[str, Any]) -> bool:
    tt = spec.get("target_trial")
    if not isinstance(tt, dict) or not tt:
        return False
    time_zero = tt.get("time_zero")
    follow_up = tt.get("follow_up")
    if _is_missing_value(time_zero, empty_string=True) or not isinstance(follow_up, dict):
        return False
    horizon = follow_up.get("horizon_hours")
    try:
        horizon_hours = int(horizon)
    except Exception:
        return False
    if horizon_hours <= 0:
        return False
    anchor = str(follow_up.get("anchor") or "").strip()
    mode = str(follow_up.get("mode") or "").strip()
    if anchor not in {"time_zero", "window_start"}:
        return False
    if mode not in {"fixed", "shift"}:
        return False
    return True


def build_temporal_spec(
    *,
    index_time: datetime,
    feature_window_start: datetime,
    feature_window_end: datetime,
    outcome_window_start: datetime,
    outcome_window_end: datetime | None,
    censoring_reason: str = "not_censored",
    outcome_status: str = "defensible_fixed_horizon",
) -> dict[str, Any]:
    return {
        "temporal_spec_version": TEMPORAL_SPEC_VERSION,
        "index_time": _iso(index_time),
        "feature_window_start": _iso(feature_window_start),
        "feature_window_end": _iso(feature_window_end),
        "outcome_window_start": _iso(outcome_window_start),
        "outcome_window_end": _iso(outcome_window_end),
        "censoring_reason": censoring_reason,
        "outcome_status": outcome_status,
    }


def episode_legacy_temporal_spec(ep: Any, *, feature_window_hours: int = 24, outcome_horizon_hours: int = 24) -> dict[str, Any]:
    index_time = ep.admission_date
    feature_end = min(index_time + timedelta(hours=feature_window_hours), ep.discharge_date or timezone.now())
    outcome_start = feature_end
    outcome_end = outcome_start + timedelta(hours=outcome_horizon_hours)
    censoring_reason = "legacy_discharge_endpoint"
    return build_temporal_spec(
        index_time=index_time,
        feature_window_start=index_time,
        feature_window_end=feature_end,
        outcome_window_start=outcome_start,
        outcome_window_end=outcome_end,
        censoring_reason=censoring_reason,
        outcome_status=LEGACY_OUTCOME_STATUS,
    )


def window_temporal_spec(ep: Any, *, ws: datetime, we: datetime, follow_up_hours: int) -> dict[str, Any]:
    outcome_start = we
    requested_end = outcome_start + timedelta(hours=follow_up_hours)
    episode_end = ep.discharge_date
    censoring_reason = "not_censored"
    outcome_end = requested_end
    if episode_end is None:
        censoring_reason = "open_episode_outcome_window_unobserved"
        outcome_end = None
    elif episode_end < requested_end:
        censoring_reason = "discharged_before_outcome_window_end"
        outcome_end = episode_end
    return build_temporal_spec(
        index_time=ws,
        feature_window_start=ws,
        feature_window_end=we,
        outcome_window_start=outcome_start,
        outcome_window_end=outcome_end,
        censoring_reason=censoring_reason,
    )


def validate_temporal_row(row: dict[str, Any], *, feature_names: list[str] | None = None, target: str = "delta_ri") -> TemporalIssue | None:
    spec = _as_dict(row.get("temporal_spec"))
    warnings: list[str] = []
    flags: dict[str, Any] = {
        "temporal_spec_valid": False,
        "leakage_blocked": False,
        "legacy_outcome_not_defensible": False,
        "insufficient_temporal_spec": False,
        "insufficient_outcome_evidence": False,
    }
    missing = sorted(REQUIRED_TEMPORAL_FIELDS - set(spec.keys()))
    if missing or not spec:
        warnings.append("insufficient_temporal_spec")
        if missing:
            warnings.append(f"missing_temporal_fields:{','.join(missing)}")
        flags["insufficient_temporal_spec"] = True
        return TemporalIssue("insufficient_temporal_spec", sorted(set(warnings)), flags)

    feature_end = _parse_dt(spec.get("feature_window_end"))
    outcome_start = _parse_dt(spec.get("outcome_window_start"))
    outcome_end = _parse_dt(spec.get("outcome_window_end"))
    if feature_end is None or outcome_start is None or outcome_end is None:
        warnings.append("insufficient_temporal_spec")
        flags["insufficient_temporal_spec"] = True
        return TemporalIssue("insufficient_temporal_spec", sorted(set(warnings)), flags)

    if feature_end > outcome_start:
        warnings.append("feature_window_end_after_outcome_window_start")
        flags["leakage_blocked"] = True
        return TemporalIssue("temporal_leakage_blocked", sorted(set(warnings)), flags)

    if str(spec.get("outcome_status") or "") == LEGACY_OUTCOME_STATUS:
        warnings.append(LEGACY_OUTCOME_STATUS)
        flags["legacy_outcome_not_defensible"] = True
        return TemporalIssue("legacy_outcome_not_defensible", sorted(set(warnings)), flags)

    censoring_reason = str(spec.get("censoring_reason") or "")
    if censoring_reason and censoring_reason != "not_censored":
        warnings.append(censoring_reason)
        flags["insufficient_outcome_evidence"] = True
        return TemporalIssue("insufficient_outcome_evidence", sorted(set(warnings)), flags)

    if target and (target not in row or _is_missing_value(row.get(target))):
        warnings.append("missing_outcome_target")
        flags["insufficient_outcome_evidence"] = True
        return TemporalIssue("insufficient_outcome_evidence", sorted(set(warnings)), flags)

    feature_timestamps = _as_dict(row.get("feature_timestamps"))
    leaked_features = []
    invalid_feature_timestamps = []
    for name, raw_dt in feature_timestamps.items():
        dt = _parse_dt(raw_dt)
        if dt is None and not _is_missing_value(raw_dt, empty_string=True):
            invalid_feature_timestamps.append(str(name))
        elif dt is not None and dt > feature_end:
            leaked_features.append(str(name))
    if invalid_feature_timestamps:
        warnings.append(f"invalid_feature_timestamp:{','.join(sorted(invalid_feature_timestamps))}")
        flags["leakage_blocked"] = True
        return TemporalIssue("temporal_leakage_blocked", sorted(set(warnings)), flags)
    if leaked_features:
        warnings.append(f"future_feature_timestamps_blocked:{','.join(sorted(leaked_features))}")
        flags["leakage_blocked"] = True
        return TemporalIssue("temporal_leakage_blocked", sorted(set(warnings)), flags)

    names = {str(name) for name in (feature_names or row.keys())}
    dangerous = sorted(DANGEROUS_PREDICTOR_NAMES & names)
    if dangerous:
        warnings.append(f"post_outcome_predictor_blocked:{','.join(dangerous)}")
        flags["leakage_blocked"] = True
        return TemporalIssue("post_outcome_predictor_blocked", sorted(set(warnings)), flags)

    flags["temporal_spec_valid"] = True
    return None


def validate_temporal_frame(
    df: pd.DataFrame,
    *,
    feature_names: list[str] | None = None,
    target: str = "delta_ri",
) -> list[tuple[int, TemporalIssue]]:
    issues: list[tuple[int, TemporalIssue]] = []
    if df.empty:
        return issues
    for pos, (_, series) in enumerate(df.iterrows()):
        issue = validate_temporal_row(series.to_dict(), feature_names=feature_names, target=target)
        if issue is not None:
            issues.append((pos, issue))
    return issues


def validate_case_mix_spec(case_mix_spec: dict[str, Any] | None) -> TemporalIssue | None:
    spec = _as_dict(case_mix_spec)
    supplied = set()
    domains = spec.get("domains")
    if isinstance(domains, dict):
        supplied.update(str(k) for k, v in domains.items() if v)
    variables = spec.get("variables")
    if isinstance(variables, dict):
        supplied.update(str(k) for k, v in variables.items() if v)
    elif isinstance(variables, list):
        supplied.update(str(v) for v in variables if v)
    missing = sorted(CASE_MIX_REQUIRED_DOMAINS - supplied)
    if not spec or missing:
        return TemporalIssue(
            "case_mix_insufficient",
            ["case_mix_insufficient", f"missing_case_mix_domains:{','.join(missing)}" if missing else "missing_case_mix_spec"],
            {"case_mix_insufficient": True},
        )
    return None


def validate_causal_temporal_order(spec: dict[str, Any]) -> TemporalIssue | None:
    treatment = str(spec.get("treatment") or "").strip()
    outcome = str(spec.get("outcome") or "delta_ri").strip()
    confounders = [str(c) for c in (spec.get("confounders") or []) if str(c)]
    dag_edges = [list(edge) for edge in (spec.get("dag_edges") or []) if isinstance(edge, (list, tuple)) and len(edge) == 2]
    post_treatment = {str(v) for v in (spec.get("post_treatment_variables") or []) if str(v)}
    has_temporal_spec = bool(spec.get("temporal_spec"))
    target_trial_has_order = _target_trial_has_temporal_order(spec)

    warnings: list[str] = []
    if not treatment:
        warnings.append("treatment_missing")
    if spec.get("target_trial") and not target_trial_has_order:
        warnings.append("target_trial_temporal_order_not_demonstrated")
    if not target_trial_has_order and not has_temporal_spec:
        warnings.append("insufficient_temporal_spec")
    if [treatment, outcome] not in dag_edges and not has_temporal_spec and not target_trial_has_order:
        warnings.append("treatment_outcome_temporal_order_not_proven")
    for c in confounders:
        if c in post_treatment or [treatment, c] in dag_edges:
            warnings.append(f"confounder_post_treatment:{c}")
        if dag_edges and [c, treatment] not in dag_edges:
            warnings.append(f"confounder_not_pre_treatment_in_dag:{c}")

    if warnings:
        return TemporalIssue(
            "temporal_leakage_blocked",
            sorted(set(warnings)),
            {"causal_available": False, "leakage_blocked": True},
        )
    return None
