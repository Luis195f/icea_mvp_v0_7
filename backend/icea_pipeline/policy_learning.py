from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class PolicyLearningOutput:
    available: bool
    method: str
    treatment_col: str
    treatment_low: float
    treatment_high: float
    decision_col: str
    rule_text: str
    recommended_rate: float
    estimated_mean_gain: float
    notes: list[str]


def _safe_export_sklearn_tree(tree, feature_names: list[str]) -> str:
    try:
        from sklearn.tree import export_text  # type: ignore

        return export_text(tree, feature_names=feature_names, max_depth=4)
    except Exception:
        return ""


def learn_policy_from_marginal_cate(
    df: pd.DataFrame,
    *,
    cate: np.ndarray,
    treatment_col: str,
    feature_cols: list[str],
    decision_col: str = "policy_recommend_high",
    low_q: float = 0.25,
    high_q: float = 0.75,
    threshold: float = 0.0,
    max_depth: int = 3,
    min_samples_leaf: int = 50,
) -> tuple[pd.DataFrame, PolicyLearningOutput]:
    """Learn an interpretable treatment policy from marginal CATE estimates.

    Context (ICEA+): For continuous exposures (e.g., nurse_hppd) the causal model
    estimates a marginal effect (per unit). We discretize the treatment into two
    actionable regimes (low vs high) and learn a rule that decides which regime
    is optimal given baseline features.

    The function is best-effort: it tries EconML's PolicyTree if available,
    otherwise falls back to a shallow sklearn decision tree.
    """
    notes: list[str] = []
    if len(df) == 0:
        return df, PolicyLearningOutput(
            available=False,
            method="none",
            treatment_col=treatment_col,
            treatment_low=0.0,
            treatment_high=0.0,
            decision_col=decision_col,
            rule_text="",
            recommended_rate=0.0,
            estimated_mean_gain=0.0,
            notes=["empty_dataset"],
        )

    if treatment_col not in df.columns:
        return df, PolicyLearningOutput(
            available=False,
            method="none",
            treatment_col=treatment_col,
            treatment_low=0.0,
            treatment_high=0.0,
            decision_col=decision_col,
            rule_text="",
            recommended_rate=0.0,
            estimated_mean_gain=0.0,
            notes=[f"treatment_col '{treatment_col}' not found"],
        )

    t = pd.to_numeric(df[treatment_col], errors="coerce")
    t_low = float(np.nanquantile(t, low_q)) if np.isfinite(np.nanquantile(t, low_q)) else float(np.nanmedian(t))
    t_high = float(np.nanquantile(t, high_q)) if np.isfinite(np.nanquantile(t, high_q)) else float(np.nanmedian(t))
    if not np.isfinite(t_low):
        t_low = 0.0
    if not np.isfinite(t_high):
        t_high = t_low
    delta = float(t_high - t_low)
    if delta == 0:
        notes.append("treatment_low == treatment_high; policy degenerates")

    # Approx benefit of moving from low->high for each row
    cate = np.asarray(cate).reshape(-1)
    if len(cate) != len(df):
        notes.append("cate_length_mismatch; truncating")
        n = min(len(cate), len(df))
        cate = cate[:n]
        df = df.iloc[:n].copy()

    benefit = cate * delta
    y = (benefit > float(threshold)).astype(int)

    # Feature matrix
    feat = [c for c in feature_cols if c in df.columns]
    if not feat:
        feat = [treatment_col]
        notes.append("no_feature_cols_available; using treatment_col as proxy")

    X = df[feat].copy()
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    rule_text = ""
    method = ""
    model = None

    # Try EconML PolicyTree first (preferred)
    try:
        from econml.policy import PolicyTree  # type: ignore

        pt = PolicyTree(max_depth=max_depth, min_samples_leaf=min_samples_leaf, random_state=42)
        pt.fit(X.values, y)
        pred = pt.predict(X.values)
        method = "econml.PolicyTree"
        model = pt
        try:
            rule_text = str(pt)
        except Exception:
            rule_text = ""
    except Exception as e:
        notes.append(f"econml_policy_unavailable: {e.__class__.__name__}")

    if model is None:
        try:
            from sklearn.tree import DecisionTreeClassifier  # type: ignore

            dt = DecisionTreeClassifier(
                max_depth=max_depth,
                min_samples_leaf=min_samples_leaf,
                random_state=42,
            )
            dt.fit(X.values, y)
            pred = dt.predict(X.values)
            method = "sklearn.DecisionTreeClassifier"
            model = dt
            rule_text = _safe_export_sklearn_tree(dt, feat)
        except Exception as e:
            notes.append(f"policy_tree_fit_failed: {e.__class__.__name__}: {str(e)}")
            pred = y
            method = "heuristic_threshold"

    df2 = df.copy()
    df2[decision_col] = pd.Series(pred, index=df2.index).astype(int)

    recommended_rate = float(np.mean(df2[decision_col].values)) if len(df2) else 0.0
    estimated_mean_gain = float(np.mean(benefit * df2[decision_col].values)) if len(df2) else 0.0

    out = PolicyLearningOutput(
        available=True,
        method=method or "unknown",
        treatment_col=treatment_col,
        treatment_low=float(t_low),
        treatment_high=float(t_high),
        decision_col=decision_col,
        rule_text=rule_text or "",
        recommended_rate=recommended_rate,
        estimated_mean_gain=estimated_mean_gain,
        notes=notes,
    )
    return df2, out
