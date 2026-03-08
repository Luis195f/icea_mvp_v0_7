from __future__ import annotations

"""Counterfactual digital-twin simulator.

Implements POST /api/v1/causal/simulate/.

Concept:
- Use a DML causal estimator to estimate the *effect* of changing staffing exposure.
- Optionally combine with a predictive model (XGBoost) to produce counterfactual outcomes
  and conformal intervals when available.

This is a decision-support *simulation* endpoint.
- Pure JSON output.
- Best-effort: never breaks the main pipeline.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from django.conf import settings

from analytics.causal import ICEACausal
from icea_core.conformal import conformal_interval_from_metrics
from icea_core.engine import ICEAEngine
from icea_core.models import ModelArtifact


@dataclass
class SimulationScenario:
    name: str
    # absolute values override; deltas add to baseline
    set_values: dict[str, float]
    delta_values: dict[str, float]


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _load_xgb_model(model: ModelArtifact) -> ICEAEngine | None:
    try:
        if not model or model.model_type != "xgboost":
            return None
        if not model.model_path:
            return None
        p = Path(model.model_path)
        if not p.exists():
            return None
        return ICEAEngine(p)
    except Exception:
        return None


def _predict_xgb(engine: ICEAEngine, df: pd.DataFrame, features: list[str]) -> np.ndarray:
    x = engine._ensure_columns(df, features)  # type: ignore[attr-defined]
    preds = engine.model.predict(x)  # type: ignore[attr-defined]
    return np.asarray(preds, dtype=float)


def _ensure_cols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c and c not in out.columns:
            out[c] = 0.0
    return out


def _scenario_apply(df: pd.DataFrame, sc: SimulationScenario) -> pd.DataFrame:
    out = df.copy()
    for k, v in (sc.delta_values or {}).items():
        if k not in out.columns:
            out[k] = 0.0
        out[k] = out[k].astype(float) + float(v)
    for k, v in (sc.set_values or {}).items():
        if k not in out.columns:
            out[k] = 0.0
        out[k] = float(v)
    return out


def simulate_counterfactual(
    df: pd.DataFrame,
    *,
    spec: dict[str, Any],
    scenarios: list[SimulationScenario],
    model_id: str | None = None,
) -> dict[str, Any]:
    treatment = str(spec.get("treatment") or "").strip()
    outcome = str(spec.get("outcome") or "delta_ri").strip()
    confounders = list(spec.get("confounders") or [])
    effect_modifiers = list(spec.get("effect_modifiers") or [])

    if not treatment:
        return {"available": False, "error": "spec.treatment is required"}

    # Prepare data
    df = df.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    needed = [treatment, outcome] + confounders + effect_modifiers
    df = _ensure_cols(df, needed)

    T0 = df[treatment].astype(float).values
    Y = df[outcome].astype(float).values
    W = df[confounders].astype(float).values if confounders else None
    X = df[effect_modifiers].astype(float).values if effect_modifiers else (df[confounders].astype(float).values if confounders else None)

    # Fit DML causal estimator
    n_estimators = int(spec.get("n_estimators") or 400)
    causal = ICEACausal(n_estimators=n_estimators)
    causal.fit(X=X, W=W, T=T0, Y=Y)

    # Optional predictive model for counterfactual outcome + conformal intervals
    xgb_engine = None
    xgb_model = None
    xgb_metrics = None
    xgb_features: list[str] = []

    if model_id:
        try:
            xgb_model = ModelArtifact.objects.get(id=model_id)
        except Exception:
            xgb_model = None
    if xgb_model is not None:
        xgb_engine = _load_xgb_model(xgb_model)
        xgb_metrics = xgb_model.metrics
        xgb_features = list(xgb_model.features or [])

    y_base_pred = None
    y_base_int = None
    if xgb_engine and xgb_features:
        y_base_pred = _predict_xgb(xgb_engine, df, xgb_features)
        # conformal interval only for scalar; provide cohort-level calibration q_hat
        # For per-row intervals, return q_hat and let UI build intervals if needed.
        y_base_int = {"conformal": (xgb_metrics or {}).get("conformal") or {}}

    results = []

    for sc in scenarios:
        df_sc = _scenario_apply(df, sc)
        T1 = df_sc[treatment].astype(float).values

        # EconML effect supports T0/T1 for continuous treatment.
        try:
            cate = np.asarray(causal.model.effect(X, T0=T0, T1=T1), dtype=float)  # type: ignore[attr-defined]
        except Exception:
            cate = np.asarray(causal.model.effect(X), dtype=float)  # type: ignore[attr-defined]

        ate = float(np.mean(cate)) if cate.size else 0.0

        out_item: dict[str, Any] = {
            "scenario": sc.name,
            "treatment": treatment,
            "ate_delta_outcome": ate,
            "cate": cate[:100].tolist(),  # cap for payload size
            "n_rows": int(len(df_sc)),
        }

        # Predict counterfactual outcomes with intervals when XGB is available
        if xgb_engine and xgb_features:
            y_sc_pred = _predict_xgb(xgb_engine, df_sc, xgb_features)
            out_item["y_pred_base_mean"] = float(np.mean(y_base_pred)) if y_base_pred is not None else None
            out_item["y_pred_cf_mean"] = float(np.mean(y_sc_pred))
            # cohort-level conformal interval (symmetric)
            if y_base_pred is not None and xgb_metrics:
                interval = conformal_interval_from_metrics(float(np.mean(y_sc_pred)), xgb_metrics)
                if interval:
                    out_item["y_pred_cf_interval"] = interval

        results.append(out_item)

    return {
        "available": True,
        "method": "dml_effect + optional_xgb_counterfactual",
        "treatment": treatment,
        "outcome": outcome,
        "n_rows": int(len(df)),
        "scenarios": results,
        "notes": [
            "Efectos estimados vía DML (CausalForestDML).",
            "Intervalos conformes solo disponibles si se proporciona un ModelArtifact xgboost con calibración.",
        ],
    }
