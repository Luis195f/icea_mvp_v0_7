from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from econml.dml import CausalForestDML
from sklearn.ensemble import RandomForestRegressor


@dataclass
class CausalResult:
    cate: np.ndarray
    shap_values: np.ndarray | None = None


class ICEACausal:
    """ICEA+ causal layer (MVP stub).

    This estimates CATE (conditional average treatment effects) for a nursing exposure T
    over features X and outcome Y.

    Typical usage (target-trial style):
      - X: baseline confounders (severity, comorbidity, unit, etc.)
      - T: nursing exposure (e.g., safe staffing coverage, HPPD, continuity)
      - Y: outcome (delta_ri, LOS, mortality risk)

    IMPORTANT: This is not a full causal identification strategy by itself.
    You still must justify:
      - temporal ordering,
      - no unmeasured confounding (or use IV / panel methods),
      - positivity, consistency.
    """

    def __init__(self, n_estimators: int = 400, random_state: int = 42):
        self.model = CausalForestDML(
            model_t=RandomForestRegressor(random_state=random_state),
            model_y=RandomForestRegressor(random_state=random_state),
            n_estimators=n_estimators,
            random_state=random_state,
        )

    def fit(self, *, X, W, T, Y) -> None:
        self.model.fit(Y, T, X=X, W=W)

    def effect(self, X, *, T0=None, T1=None) -> CausalResult:
        # EconML supports effect(X, T0, T1) for continuous treatments.
        if T0 is not None and T1 is not None:
            cate = self.model.effect(X, T0=T0, T1=T1)
        else:
            cate = self.model.effect(X)

        shap_vals = None
        # econml CATE estimators expose shap_values for effect explainability.
        try:
            shap_vals = self.model.shap_values(X)
        except Exception:
            shap_vals = None
        return CausalResult(cate=cate, shap_values=shap_vals)
