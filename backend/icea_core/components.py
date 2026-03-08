from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class ComponentBuildResult:
    raw: pd.Series | None
    normalized: pd.Series | None = None
    subcomponents: dict[str, pd.Series] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)



def numeric_series(values: Any, index: pd.Index | None = None) -> pd.Series:
    if isinstance(values, pd.Series):
        out = pd.to_numeric(values, errors="coerce")
        return out if index is None else out.reindex(index)
    if index is None:
        raise ValueError("index is required when values is not a pandas Series")
    return pd.Series(values, index=index, dtype=float)



def infer_outcome_goal(outcome: str, rules: list[dict[str, Any]] | None) -> tuple[str, str]:
    name = (outcome or "").strip().lower()
    for rule in rules or []:
        contains = [str(x).strip().lower() for x in (rule.get("contains") or []) if str(x).strip()]
        if any(token in name for token in contains):
            return str(rule.get("goal") or "higher_is_better"), "rule_based"
    return "higher_is_better", "default_higher_is_better"



def outcome_sign(goal: str) -> int:
    return 1 if str(goal or "").strip().lower() == "higher_is_better" else -1



def utility_transform(values: pd.Series, goal: str) -> pd.Series:
    s = numeric_series(values)
    if outcome_sign(goal) > 0:
        return s
    return -s



def infer_nurse_columns(
    *,
    features: list[str],
    df: pd.DataFrame,
    supplied: list[str] | None = None,
) -> list[str]:
    if supplied:
        return [c for c in supplied if c]

    candidates = [
        col
        for col in features
        if col.startswith("nurse_") or col.startswith("nic_") or col in {"nurse_proc_count", "nurse_proc_count_det"}
    ]
    for extra in ["nurse_proc_count_det", "nurse_proc_count", "nurse_hppd", "nurse_skillmix"]:
        if extra in df.columns and extra not in candidates:
            candidates.append(extra)
    return candidates



def robust_z(
    values: pd.Series | None,
    reference: pd.Series | None,
    *,
    spec: dict[str, Any],
) -> tuple[pd.Series | None, dict[str, Any]]:
    if values is None or reference is None:
        return None, {"available": False, "reason": "missing_values_or_reference"}

    val = numeric_series(values)
    ref = numeric_series(reference).dropna()
    if ref.empty:
        return None, {"available": False, "reason": "empty_reference"}

    norm_spec = spec.get("normalization") or {}
    eps = float(norm_spec.get("eps") or 1e-6)
    min_rows = int(norm_spec.get("min_reference_rows") or 5)
    clip = float(norm_spec.get("clip") or 4.0)
    mad_scale = float(norm_spec.get("mad_scale") or 1.4826)

    median = float(ref.median())
    mad = float(np.median(np.abs(ref - median))) if len(ref) else 0.0
    scale = mad * mad_scale
    method = "robust_z"

    if len(ref) < min_rows or not np.isfinite(scale) or scale <= eps:
        scale = float(ref.std(ddof=0))
        method = "std_z_fallback"

    if not np.isfinite(scale) or scale <= eps:
        scale = 1.0
        method = "identity_fallback"

    z = ((val - median) / scale).clip(lower=-clip, upper=clip)
    return z, {
        "available": True,
        "method": method,
        "median": median,
        "scale": float(scale),
        "reference_rows": int(len(ref)),
    }



def compute_missingness_burden(df: pd.DataFrame) -> pd.Series | None:
    cols = [c for c in df.columns if c.startswith("missing_")]
    if not cols:
        return None
    values = df[cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).clip(lower=0.0, upper=1.0)
    return values.mean(axis=1)



def compute_relative_nursing_attribution(
    shap_df: pd.DataFrame,
    *,
    nurse_cols: list[str],
    epsilon: float,
) -> pd.Series | None:
    if shap_df.empty or not nurse_cols:
        return None
    cols = [c for c in nurse_cols if c in shap_df.columns]
    if not cols:
        return None
    phi_n = shap_df[cols].sum(axis=1)
    total_abs = shap_df.abs().sum(axis=1)
    return phi_n / (total_abs + float(epsilon))



def compute_quality_raw(df: pd.DataFrame, spec: dict[str, Any]) -> ComponentBuildResult:
    index = df.index
    subcomponents: dict[str, pd.Series] = {}

    missingness = compute_missingness_burden(df)
    if missingness is not None:
        subcomponents["structured_completeness"] = (1.0 - missingness).clip(lower=0.0, upper=1.0)

    if "nurse_proc_count" in df.columns:
        numerator = pd.to_numeric(df.get("nurse_proc_count_det", 0.0), errors="coerce").clip(lower=0.0)
        denominator = pd.to_numeric(df["nurse_proc_count"], errors="coerce").clip(lower=0.0)
        documentation = pd.Series(np.nan, index=index, dtype=float)
        valid = denominator > 0
        documentation.loc[valid] = (numerator.loc[valid] / denominator.loc[valid]).clip(lower=0.0, upper=1.0)
        subcomponents["documentation_consistency"] = documentation

    time_cols = [c for c in df.columns if c.startswith("missing_loinc_") and (c.endswith("_t0") or c.endswith("_t1"))]
    if time_cols:
        time_flags = df[time_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).clip(lower=0.0, upper=1.0)
        subcomponents["timeliness"] = (1.0 - time_flags.mean(axis=1)).clip(lower=0.0, upper=1.0)

    if not subcomponents:
        return ComponentBuildResult(raw=None, warnings=["process_quality_unavailable"])

    quality_df = pd.DataFrame(subcomponents)
    min_parts = int(((spec.get("quality") or {}).get("min_available_subcomponents")) or 1)
    available_parts = quality_df.notna().sum(axis=1)
    raw = quality_df.mean(axis=1, skipna=True)
    raw.loc[available_parts < min_parts] = np.nan
    warnings = []
    if int((available_parts >= min_parts).sum()) < len(df):
        warnings.append("process_quality_partial_basis")
    return ComponentBuildResult(raw=raw, subcomponents=subcomponents, warnings=warnings)



def compute_ood_burden(
    df: pd.DataFrame,
    *,
    features: list[str],
    feature_stats: dict[str, Any] | None,
    spec: dict[str, Any],
) -> pd.Series | None:
    if not feature_stats:
        return None
    means = feature_stats.get("mean") if isinstance(feature_stats, dict) else None
    stds = feature_stats.get("std") if isinstance(feature_stats, dict) else None
    if not isinstance(means, dict) or not isinstance(stds, dict):
        return None

    threshold = float(((spec.get("uncertainty") or {}).get("ood_z_threshold")) or 3.0)
    cols = [c for c in features if c in df.columns and c in means and c in stds]
    if not cols:
        return None

    signals: list[pd.Series] = []
    for col in cols:
        std = float(stds.get(col) or 0.0)
        if not np.isfinite(std) or std <= 1e-9:
            continue
        mean = float(means.get(col) or 0.0)
        z_abs = (pd.to_numeric(df[col], errors="coerce") - mean).abs() / std
        signals.append(((z_abs - threshold) / max(threshold, 1e-6)).clip(lower=0.0, upper=1.0))
    if not signals:
        return None
    return pd.concat(signals, axis=1).mean(axis=1)



def compute_conformal_burden(
    *,
    metrics: dict[str, Any] | None,
    outcome_scale: float,
    index: pd.Index,
) -> pd.Series | None:
    if not metrics:
        return None
    conf = metrics.get("conformal") if isinstance(metrics, dict) else None
    if not isinstance(conf, dict):
        return None
    q_hat = conf.get("q_hat")
    try:
        width = abs(float(q_hat)) * 2.0
    except Exception:
        return None
    denom = max(float(outcome_scale), 1e-6)
    penalty = min(width / denom, 1.0)
    return pd.Series([float(penalty)] * len(index), index=index, dtype=float)



def compute_low_support_burden(
    *,
    metrics: dict[str, Any] | None,
    causal_rows: int | None,
    spec: dict[str, Any],
    index: pd.Index,
) -> pd.Series:
    unc_spec = spec.get("uncertainty") or {}
    min_training_rows = float(unc_spec.get("min_training_rows") or 100)
    train_rows = float((metrics or {}).get("n_rows") or 0.0)
    train_penalty = 0.0
    if min_training_rows > 0:
        train_penalty = max(0.0, (min_training_rows - train_rows) / min_training_rows)

    causal_min = float(((spec.get("causal") or {}).get("min_rows")) or 30)
    causal_penalty = 0.0
    if causal_rows is not None and causal_min > 0:
        causal_penalty = max(0.0, (causal_min - float(causal_rows)) / causal_min)

    return pd.Series([float(max(train_penalty, causal_penalty))] * len(index), index=index, dtype=float)



def combine_uncertainty_subcomponents(
    *,
    index: pd.Index,
    subcomponents: dict[str, pd.Series | None],
    spec: dict[str, Any],
) -> ComponentBuildResult:
    weights = dict(((spec.get("uncertainty") or {}).get("weights")) or {})
    valid_parts: dict[str, pd.Series] = {}
    for name, series in subcomponents.items():
        if series is None:
            continue
        valid_parts[name] = numeric_series(series, index=index).clip(lower=0.0, upper=1.0)

    if not valid_parts:
        return ComponentBuildResult(raw=None, warnings=["uncertainty_unavailable"])

    total_weight = sum(float(weights.get(name, 0.0)) for name in valid_parts)
    if total_weight <= 0:
        total_weight = float(len(valid_parts))
        weights = {name: 1.0 for name in valid_parts}

    acc = pd.Series([0.0] * len(index), index=index, dtype=float)
    for name, series in valid_parts.items():
        acc = acc + (series * float(weights.get(name, 1.0)))
    raw = acc / total_weight
    warnings = []
    if "conformal_width" not in valid_parts:
        warnings.append("conformal_unavailable")
    if "ood" not in valid_parts:
        warnings.append("ood_unavailable")
    return ComponentBuildResult(raw=raw, subcomponents=valid_parts, warnings=warnings)



def compute_severity_weight(
    baseline_expected: pd.Series | None,
    baseline_reference: pd.Series | None,
    *,
    goal: str,
    spec: dict[str, Any],
) -> pd.Series | None:
    if baseline_expected is None or baseline_reference is None:
        return None
    direction = -1.0 if outcome_sign(goal) > 0 else 1.0
    sev_val = numeric_series(baseline_expected) * direction
    sev_ref = numeric_series(baseline_reference) * direction
    z, _ = robust_z(sev_val, sev_ref, spec=spec)
    if z is None:
        return None
    clip = float(((spec.get("aggregation") or {}).get("severity_clip")) or 2.0)
    return 1.0 + z.clip(lower=0.0, upper=clip)
