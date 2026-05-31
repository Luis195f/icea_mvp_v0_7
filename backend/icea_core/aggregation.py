from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np


MIN_AGGREGATE_EPISODES = 10
MIN_STAFF_FOR_STAFF_DIMENSION = 5
SCORING_ROW_STATUSES = {"complete", "scored_aggregate"}
NON_SCORING_STATUSES = {
    "provisional",
    "insufficient_evidence",
    "contract_mismatch",
    "low_feature_coverage",
    "blocked_by_reference_contract",
    "suppressed_low_support",
    "shadow_only",
}


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
    return f"unit:{unit}|date:{_date_bucket(row)}"


def _staff_keys(rows: list[dict[str, Any]]) -> set[str]:
    staff: set[str] = set()
    for row in rows:
        shares = dict((row.get("aggregation") or {}).get("nurse_shares") or {})
        staff.update(str(key) for key in shares if str(key))
    return staff


def governance_export_metadata(
    *,
    aggregation_level: str,
    min_cell_count: int = MIN_AGGREGATE_EPISODES,
    suppressed_cells: int = 0,
    formula_version: str | None = None,
    model_lineage: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "non_individual_use": True,
        "shadow_mode": True,
        "aggregation_level": aggregation_level,
        "min_cell_count": int(min_cell_count),
        "suppressed_cells": int(suppressed_cells),
        "formula_version": formula_version,
        "model_lineage": dict(model_lineage or {}),
        "generated_at": generated_at,
    }



def aggregate_scored_rows(
    *,
    rows: list[dict[str, Any]],
    group_by: str,
    epsilon: float,
    enforce_suppression: bool = True,
    min_cell_count: int = MIN_AGGREGATE_EPISODES,
    min_staff_count: int = MIN_STAFF_FOR_STAFF_DIMENSION,
    require_staff_count: bool = False,
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

        if row.get("score") is not None and str(row.get("status") or "") in SCORING_ROW_STATUSES:
            for name, comp in (row.get("components") or {}).items():
                if comp.get("normalized") is not None:
                    entry["component_vectors"][name].append((float(comp["normalized"]), weight))

    results: list[dict[str, Any]] = []
    for key, entry in grouped.items():
        all_rows = entry["rows"]
        score_values = [
            float(r["score"])
            for r in all_rows
            if r.get("score") is not None and str(r.get("status") or "") in SCORING_ROW_STATUSES
        ]
        weights = [
            float(w)
            for r, w in zip(all_rows, entry["weights"])
            if r.get("score") is not None and str(r.get("status") or "") in SCORING_ROW_STATUSES
        ]
        total_weight = float(sum(weights))
        agg_score = None
        if total_weight > 0 and score_values:
            agg_score = float(sum(v * w for v, w in zip(score_values, weights)) / (total_weight + float(epsilon)))

        component_means = {}
        for name, vec in entry["component_vectors"].items():
            num = sum(value * weight for value, weight in vec)
            den = sum(weight for _, weight in vec)
            component_means[name] = float(num / den) if den > 0 else None

        status_counts = {
            "scored_aggregate": int(sum(1 for r in all_rows if str(r.get("status") or "") in SCORING_ROW_STATUSES)),
            "provisional": int(sum(1 for r in all_rows if r.get("status") == "provisional")),
            "insufficient_evidence": int(sum(1 for r in all_rows if r.get("status") == "insufficient_evidence")),
            "contract_mismatch": int(sum(1 for r in all_rows if r.get("status") == "contract_mismatch")),
            "low_feature_coverage": int(sum(1 for r in all_rows if r.get("status") == "low_feature_coverage")),
            "suppressed_low_support": 0,
            "shadow_only": int(sum(1 for r in all_rows if r.get("status") == "shadow_only")),
        }
        provisional = status_counts["provisional"]
        complete = status_counts["scored_aggregate"]
        insufficient = status_counts["insufficient_evidence"]
        warnings = sorted({warning for r in all_rows for warning in (r.get("warnings") or [])})
        n_episodes = len({str(r.get("episode_id")) for r in all_rows if r.get("episode_id") not in (None, "")})
        n_staff = len(_staff_keys(all_rows))

        suppression_reasons: list[str] = []
        if enforce_suppression and len(all_rows) < int(min_cell_count):
            suppression_reasons.append("n_observations_below_min_cell_count")
        if enforce_suppression and n_episodes < int(min_cell_count):
            suppression_reasons.append("n_episodes_below_min_cell_count")
        if enforce_suppression and require_staff_count and n_staff < int(min_staff_count):
            suppression_reasons.append("n_staff_below_min_staff_count")
        suppressed = bool(suppression_reasons)
        if suppressed:
            agg_score = None
            component_means = {}
            status_counts["suppressed_low_support"] = int(len(all_rows))
            warnings = sorted(set(warnings + suppression_reasons + ["suppressed_low_support"]))

        weighted_std = None
        if score_values and total_weight > 0 and not suppressed:
            arr = np.asarray(score_values, dtype=float)
            mean = float(np.average(arr, weights=weights))
            weighted_std = float(np.sqrt(np.average((arr - mean) ** 2, weights=weights)))

        status = "suppressed_low_support" if suppressed else ("scored_aggregate" if agg_score is not None else "insufficient_evidence")
        results.append(
            {
                "group": key,
                "status": status,
                "score": agg_score,
                "n_observations": int(len(all_rows)),
                "support": {
                    "n_observations": int(len(all_rows)),
                    "n_episodes": int(n_episodes),
                    "n_staff": int(n_staff),
                    "min_cell_count": int(min_cell_count),
                    "min_staff_count": int(min_staff_count),
                    "suppressed": suppressed,
                    "suppression_reasons": suppression_reasons,
                },
                "suppressed": suppressed,
                "coverage": float(len(score_values) / len(all_rows)) if all_rows else 0.0,
                "provisional_fraction": float(provisional / len(all_rows)) if all_rows else 0.0,
                "complete_fraction": float(complete / len(all_rows)) if all_rows else 0.0,
                "insufficient_fraction": float(insufficient / len(all_rows)) if all_rows else 0.0,
                "confidence_band": {
                    "p10": None if suppressed else weighted_quantile(score_values, weights, 0.10),
                    "p50": None if suppressed else weighted_quantile(score_values, weights, 0.50),
                    "p90": None if suppressed else weighted_quantile(score_values, weights, 0.90),
                    "weighted_std": weighted_std,
                },
                "component_means": component_means,
                "status_counts": status_counts,
                "warnings": warnings,
            }
        )
    return sorted(results, key=lambda item: str(item.get("group")))
