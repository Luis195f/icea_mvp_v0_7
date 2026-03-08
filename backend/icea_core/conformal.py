from __future__ import annotations

from typing import Any


def conformal_interval_from_metrics(pred: float, metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a symmetric conformal interval from stored model metrics.

    The training pipeline stores a calibration quantile q_hat of absolute residuals.
    This helper turns it into [pred-q_hat, pred+q_hat].
    """
    if not metrics:
        return None
    conf = metrics.get("conformal") if isinstance(metrics, dict) else None
    if not isinstance(conf, dict):
        return None
    q_hat = conf.get("q_hat")
    if q_hat is None:
        return None
    try:
        q = float(q_hat)
    except Exception:
        return None
    return {
        "method": str(conf.get("method") or "split_abs_residual"),
        "alpha": float(conf.get("alpha") or 0.05),
        "q_hat": q,
        "lower": float(pred - q),
        "upper": float(pred + q),
    }
