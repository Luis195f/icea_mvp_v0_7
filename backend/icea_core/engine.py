from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import shap
import xgboost as xgb


@dataclass
class ICEAResult:
    predictions: list[float]
    base_value: float
    icea: list[float]
    contributions: dict[str, list[float]]


class ICEAEngine:
    """ICEA computation engine.

    MVP interpretation:
      - Train a predictive model for an outcome proxy (e.g., delta_ri).
      - Use SHAP to attribute the model prediction to input feature groups.
      - Define ICEA as the (signed) summed contribution of nursing-related features.

    Notes:
      - This is a *predictive attribution* implementation.
      - For causal attribution (ICEA+), use the analytics.causal module as a second phase.
    """

    def __init__(
        self,
        model_path: str | Path,
        background: pd.DataFrame | None = None,
        shap_mode: str = "interventional",
    ) -> None:
        self.model_path = str(model_path)
        self.model = xgb.XGBRegressor()
        self.model.load_model(self.model_path)

        # SHAP: interventional better-behaved under feature correlation than naive splicing.
        # background can be a small representative sample of the training set.
        self.explainer = shap.TreeExplainer(
            self.model,
            data=background,
            feature_perturbation=shap_mode,
        )

    @staticmethod
    def _ensure_columns(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
        out = df.copy()
        for col in features:
            if col not in out.columns:
                out[col] = 0
        return out[features]

    def compute(
        self,
        df: pd.DataFrame,
        *,
        features: list[str],
        nurse_cols: list[str],
        group_map: dict[str, list[str]] | None = None,
    ) -> ICEAResult:
        x = self._ensure_columns(df, features)
        preds = self.model.predict(x)

        shap_values = self.explainer.shap_values(x)
        # SHAP expected value (baseline) for interpretability.
        base = self.explainer.expected_value
        if isinstance(base, (list, tuple, np.ndarray)):
            base_value = float(base[0])
        else:
            base_value = float(base)
        shap_df = pd.DataFrame(shap_values, columns=features)

        # Nursing contribution (ICEA)
        missing_nurse = [c for c in nurse_cols if c not in shap_df.columns]
        if missing_nurse:
            # Fail closed: treat missing as zero but report.
            for c in missing_nurse:
                shap_df[c] = 0

        icea = shap_df[nurse_cols].sum(axis=1).astype(float).tolist()

        contributions: dict[str, list[float]] = {}
        if group_map:
            for group, cols in group_map.items():
                cols_present = [c for c in cols if c in shap_df.columns]
                if not cols_present:
                    contributions[group] = [0.0] * len(shap_df)
                else:
                    contributions[group] = shap_df[cols_present].sum(axis=1).astype(float).tolist()
        else:
            contributions["nursing"] = icea

        return ICEAResult(
            predictions=[float(p) for p in preds.tolist()],
            base_value=base_value,
            icea=icea,
            contributions=contributions,
        )


def compute_basic_summary(values: list[float]) -> dict[str, Any]:
    arr = np.array(values, dtype=float)
    if arr.size == 0:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
    }


def stable_json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
