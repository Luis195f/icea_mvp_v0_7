from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


def _try_import_fairlearn():
    """Best-effort import for fairlearn (enterprise optional dependency)."""
    try:
        import fairlearn.metrics as flm  # type: ignore

        return flm
    except Exception:
        return None


def _fairlearn_binary_from_series(s: pd.Series, *, threshold: float | None = None) -> pd.Series:
    """Convert a label/prediction series to binary for fairlearn metrics."""
    if threshold is None:
        s_num = pd.to_numeric(s, errors="coerce")
        thr = float(np.nanmedian(s_num)) if np.isfinite(np.nanmedian(s_num)) else 0.0
        return (s_num >= thr).astype(int)
    s_num = pd.to_numeric(s, errors="coerce")
    return (s_num >= float(threshold)).astype(int)


@dataclass
class DisparateImpactResult:
    sensitive_feature: str
    decision_col: str
    positive_label: int
    groups: list[dict[str, Any]]
    disparate_impact_ratio: float | None
    notes: list[str]


def _bin_age(s: pd.Series) -> pd.Series:
    """Default binning for age-like continuous variables."""
    s2 = pd.to_numeric(s, errors="coerce")
    bins = [-np.inf, 17, 39, 64, 79, np.inf]
    labels = ["<=17", "18-39", "40-64", "65-79", "80+"]
    try:
        return pd.cut(s2, bins=bins, labels=labels)
    except Exception:
        return pd.Series(["unknown"] * len(s2), index=s2.index)


def _as_binary_decision(s: pd.Series, *, threshold: float | None = None) -> pd.Series:
    """Coerce a decision series to {0,1}."""
    if threshold is None:
        # Heuristic: if already boolean-ish, keep it; otherwise median split.
        try:
            u = set(pd.unique(s.dropna()))
            if u.issubset({0, 1, True, False}):
                return (s.astype(int) > 0).astype(int)
        except Exception:
            pass
        s_num = pd.to_numeric(s, errors="coerce")
        thr = float(np.nanmedian(s_num)) if np.isfinite(np.nanmedian(s_num)) else 0.0
        return (s_num >= thr).astype(int)
    s_num = pd.to_numeric(s, errors="coerce")
    return (s_num >= float(threshold)).astype(int)


def audit_disparate_impact(
    df: pd.DataFrame,
    *,
    decision_col: str,
    sensitive_feature: str,
    positive_label: int = 1,
    min_group_size: int = 25,
    decision_threshold: float | None = None,
) -> DisparateImpactResult:
    """Compute a simple Disparate Impact audit for a binary decision.

    This is intentionally lightweight (no external fairness libs), designed for
    audit/regulatory reporting. It does NOT attempt bias mitigation; it reports.
    """
    notes: list[str] = []

    if decision_col not in df.columns:
        return DisparateImpactResult(
            sensitive_feature=sensitive_feature,
            decision_col=decision_col,
            positive_label=positive_label,
            groups=[],
            disparate_impact_ratio=None,
            notes=[f"decision_col '{decision_col}' not found"],
        )
    if sensitive_feature not in df.columns:
        return DisparateImpactResult(
            sensitive_feature=sensitive_feature,
            decision_col=decision_col,
            positive_label=positive_label,
            groups=[],
            disparate_impact_ratio=None,
            notes=[f"sensitive_feature '{sensitive_feature}' not found"],
        )

    d = _as_binary_decision(df[decision_col], threshold=decision_threshold)

    g_raw = df[sensitive_feature]
    g_name = sensitive_feature.lower()
    if g_name in {"age", "edad", "patient_age"}:
        g = _bin_age(g_raw).astype(str)
    else:
        # Keep as categorical (string) to avoid leaking numeric precision.
        g = g_raw.astype(str).fillna("unknown")

    tmp = pd.DataFrame({"g": g, "d": d})
    groups_out: list[dict[str, Any]] = []
    rates: list[float] = []
    for gv, sub in tmp.groupby("g", dropna=False):
        n = int(len(sub))
        if n < min_group_size:
            continue
        rate = float((sub["d"] == int(positive_label)).mean())
        rates.append(rate)
        groups_out.append({"group": str(gv), "n": n, "selection_rate": rate})

    groups_out.sort(key=lambda x: (-x["n"], x["group"]))

    di = None
    if len(rates) >= 2:
        r_min = float(np.min(rates))
        r_max = float(np.max(rates))
        if r_max > 0:
            di = float(r_min / r_max)
        else:
            notes.append("max selection_rate is 0; cannot compute DI")
    else:
        notes.append("insufficient groups for DI (after min_group_size filter)")

    return DisparateImpactResult(
        sensitive_feature=sensitive_feature,
        decision_col=decision_col,
        positive_label=int(positive_label),
        groups=groups_out,
        disparate_impact_ratio=di,
        notes=notes,
    )


def audit_fairness_bundle(
    df: pd.DataFrame,
    *,
    decision_col: str,
    sensitive_features: list[str],
    min_group_size: int = 25,
    decision_threshold: float | None = None,
    use_fairlearn: bool | None = None,
    label_col: str | None = None,
    label_threshold: float | None = None,
) -> dict[str, Any]:
    """Audit disparate impact across multiple sensitive features."""
    out: dict[str, Any] = {
        "decision_col": decision_col,
        "results": [],
        "notes": [],
        # v0.6: standardized fairness metrics (optional) using fairlearn when installed.
        "fairlearn": {"requested": bool(use_fairlearn), "available": False, "metrics": {}, "notes": []},
    }
    for sf in sensitive_features:
        r = audit_disparate_impact(
            df,
            decision_col=decision_col,
            sensitive_feature=sf,
            min_group_size=min_group_size,
            decision_threshold=decision_threshold,
        )
        out["results"].append(
            {
                "sensitive_feature": r.sensitive_feature,
                "disparate_impact_ratio": r.disparate_impact_ratio,
                "groups": r.groups,
                "notes": r.notes,
            }
        )

    # Optional: fairlearn metrics. Never break; degrade gracefully.
    flm = _try_import_fairlearn()
    if flm is None:
        out["fairlearn"]["notes"].append("fairlearn_not_installed")
        return out
    if use_fairlearn is False:
        out["fairlearn"]["notes"].append("fairlearn_disabled")
        return out

    try:
        if decision_col not in df.columns:
            out["fairlearn"]["notes"].append(f"decision_col_missing:{decision_col}")
            return out

        y_pred = _fairlearn_binary_from_series(df[decision_col], threshold=decision_threshold)
        y_true = None
        if label_col and label_col in df.columns:
            y_true = _fairlearn_binary_from_series(df[label_col], threshold=label_threshold)

        metrics: dict[str, Any] = {}
        for sf in sensitive_features:
            if sf not in df.columns:
                continue
            # Demographic parity does not require true labels.
            try:
                dpr = float(flm.demographic_parity_ratio(np.zeros(len(y_pred)), y_pred, sensitive_features=df[sf]))
            except Exception:
                dpr = float("nan")
            try:
                dpd = float(flm.demographic_parity_difference(np.zeros(len(y_pred)), y_pred, sensitive_features=df[sf]))
            except Exception:
                dpd = float("nan")

            entry: dict[str, Any] = {
                "demographic_parity_ratio": dpr,
                "demographic_parity_difference": dpd,
            }
            if y_true is not None:
                try:
                    entry["equalized_odds_difference"] = float(
                        flm.equalized_odds_difference(y_true, y_pred, sensitive_features=df[sf])
                    )
                except Exception:
                    entry["equalized_odds_difference"] = float("nan")

            metrics[sf] = entry

        out["fairlearn"]["available"] = True
        out["fairlearn"]["metrics"] = metrics
        if label_col and label_col not in df.columns:
            out["fairlearn"]["notes"].append(f"label_col_missing:{label_col}")
        if not metrics:
            out["fairlearn"]["notes"].append("no_sensitive_features_available")
    except Exception as e:
        out["fairlearn"] = {"requested": bool(use_fairlearn), "available": False, "error": f"{e.__class__.__name__}: {str(e)}"}

    return out
