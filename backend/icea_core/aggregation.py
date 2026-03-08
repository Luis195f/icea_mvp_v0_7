from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np



def weighted_quantile(values: list[float], weights: list[float], quantile: float) -> float | None:
    if not values or not weights:
        return None
    pairs = sorted((float(v), float(w)) for v, w in zip(values, weights) if np.isfinite(v) and np.isfinite(w) and w > 0)
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    cutoff = float(np.clip(quantile, 0.0, 1.0)) * total
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if acc >= cutoff:
            return value
    return pairs[-1][0]



def _date_bucket(row: dict[str, Any]) -> str:
    for key in ("start_dt", "end_dt"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw)).date().isoformat()
        except Exception:
            continue
    return "unknown"



def _shift_bucket(row: dict[str, Any]) -> str:
    unit = row.get("unit_id")
    start = row.get("start_dt") or ""
    end = row.get("end_dt") or ""
    return f"unit:{unit}|{start}|{end}"



def aggregate_scored_rows(
    *,
    rows: list[dict[str, Any]],
    group_by: str,
    epsilon: float,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"rows": [], "weights": [], "component_vectors": defaultdict(list)}
    )

    for row in rows:
        if group_by in {"episode", "patient"}:
            key = str(row.get("patient_key") or row.get("episode_id") or row.get("row_id"))
        elif group_by == "window":
            key = str(row.get("window_id") or row.get("row_id"))
        elif group_by == "unit":
            key = str(row.get("unit_id"))
        elif group_by == "date":
            key = _date_bucket(row)
        elif group_by == "shift":
            key = _shift_bucket(row)
        else:
            key = str(row.get("row_id"))

        entry = grouped[key]
        entry["rows"].append(row)

        severity = float((row.get("aggregation") or {}).get("severity_weight") or 1.0)
        exposure_share = float((row.get("aggregation") or {}).get("effective_exposure_share") or 1.0)
        weight = max(severity * exposure_share, 0.0)
        entry["weights"].append(weight)

        if row.get("score") is not None:
            for name, comp in (row.get("components") or {}).items():
                if comp.get("normalized") is not None:
                    entry["component_vectors"][name].append((float(comp["normalized"]), weight))

    results: list[dict[str, Any]] = []
    for key, entry in grouped.items():
        all_rows = entry["rows"]
        score_values = [float(r["score"]) for r in all_rows if r.get("score") is not None]
        weights = [float(w) for r, w in zip(all_rows, entry["weights"]) if r.get("score") is not None]
        total_weight = float(sum(weights))
        agg_score = None
        if total_weight > 0 and score_values:
            agg_score = float(sum(v * w for v, w in zip(score_values, weights)) / (total_weight + float(epsilon)))

        component_means = {}
        for name, vec in entry["component_vectors"].items():
            num = sum(value * weight for value, weight in vec)
            den = sum(weight for _, weight in vec)
            component_means[name] = float(num / den) if den > 0 else None

        provisional = sum(1 for r in all_rows if r.get("status") == "provisional")
        complete = sum(1 for r in all_rows if r.get("status") == "complete")
        insufficient = sum(1 for r in all_rows if r.get("status") == "insufficient_evidence")
        warnings = sorted({warning for r in all_rows for warning in (r.get("warnings") or [])})

        weighted_std = None
        if score_values and total_weight > 0:
            arr = np.asarray(score_values, dtype=float)
            mean = float(np.average(arr, weights=weights))
            weighted_std = float(np.sqrt(np.average((arr - mean) ** 2, weights=weights)))

        results.append(
            {
                "group": key,
                "score": agg_score,
                "n_observations": int(len(all_rows)),
                "coverage": float(len(score_values) / len(all_rows)) if all_rows else 0.0,
                "provisional_fraction": float(provisional / len(all_rows)) if all_rows else 0.0,
                "complete_fraction": float(complete / len(all_rows)) if all_rows else 0.0,
                "insufficient_fraction": float(insufficient / len(all_rows)) if all_rows else 0.0,
                "confidence_band": {
                    "p10": weighted_quantile(score_values, weights, 0.10),
                    "p50": weighted_quantile(score_values, weights, 0.50),
                    "p90": weighted_quantile(score_values, weights, 0.90),
                    "weighted_std": weighted_std,
                },
                "component_means": component_means,
                "warnings": warnings,
            }
        )
    return sorted(results, key=lambda item: str(item.get("group")))
