from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split


@dataclass
class TrainResult:
    model_path: str
    features: list[str]
    target: str
    metrics: dict[str, Any]


def train_xgb_regressor(
    df: pd.DataFrame,
    *,
    features: list[str],
    target: str,
    model_dir: str,
    params: dict[str, Any] | None = None,
    test_size: float = 0.2,
    random_state: int = 42,
) -> TrainResult:
    if target not in df.columns:
        raise ValueError(f"Target '{target}' not found in dataset columns")

    # Fill missing feature columns with 0.
    X = df.copy()
    for c in features:
        if c not in X.columns:
            X[c] = 0
    X = X[features]
    y = df[target]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state
    )

    default_params: dict[str, Any] = {
        "n_estimators": 600,
        "learning_rate": 0.03,
        "max_depth": 5,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "reg_lambda": 1.0,
        "random_state": random_state,
    }
    if params:
        default_params.update(params)

    model = xgb.XGBRegressor(**default_params)
    model.fit(X_train, y_train)

    pred = model.predict(X_test)
    rmse = float(np.sqrt(mean_squared_error(y_test, pred)))
    mae = float(mean_absolute_error(y_test, pred))

    # v0.5.5: Split Conformal Prediction (regression) for individual-risk intervals.
    # Store a single calibration quantile of absolute residuals in ModelArtifact.metrics.
    # This enables fast prediction intervals without additional dependencies.
    alpha = 0.05
    q_hat = None
    try:
        resid = np.abs(np.asarray(y_test) - np.asarray(pred))
        n_cal = int(len(resid))
        if n_cal > 5:
            # Conformal quantile: ceil((n+1)*(1-alpha))/n
            k = int(np.ceil((n_cal + 1) * (1.0 - alpha)))
            k = min(max(k, 1), n_cal)
            q_hat = float(np.partition(resid, k - 1)[k - 1])
    except Exception:
        q_hat = None

    Path(model_dir).mkdir(parents=True, exist_ok=True)
    filename = f"icea_xgb_{uuid.uuid4().hex}.json"
    out_path = str(Path(model_dir) / filename)
    model.save_model(out_path)

    metrics = {
        "rmse": rmse,
        "mae": mae,
        "n_rows": int(len(df)),
        "n_features": int(len(features)),
    }

    if q_hat is not None:
        metrics["conformal"] = {
            "method": "split_abs_residual",
            "alpha": alpha,
            "q_hat": q_hat,
            "calibration_size": int(len(y_test)),
        }

    return TrainResult(model_path=out_path, features=features, target=target, metrics=metrics)
