from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

ICEA_PLUS_FORMULA_VERSION = "icea_plus_v1"


DEFAULT_ICEA_PLUS_SPEC: dict[str, Any] = {
    "version": ICEA_PLUS_FORMULA_VERSION,
    "label": "ICEA+ v1 pilot composite index",
    "status": "pilot",
    "governance": {
        "calibration_state": "pilot_default_weights",
        "requires_external_validation": True,
        "not_for_automated_sanctions": True,
        "clinical_judgement_required": True,
        "causal_claims_limited_to_available_identification_strategy": True,
    },
    "weights": {
        "intercept": 0.0,
        "benefit": 1.0,
        "attribution": 1.0,
        "causal": 1.0,
        "quality": 1.0,
        "uncertainty": 1.0,
    },
    "normalization": {
        "method": "robust_z",
        "mad_scale": 1.4826,
        "eps": 1e-6,
        "clip": 4.0,
        "min_reference_rows": 5,
    },
    "baseline": {
        "mode": "counterfactual_nursing_reference",
        "reference_strategy": "cohort_median",
    },
    "attribution": {
        "epsilon": 1e-6,
    },
    "causal": {
        "effect_mode": "marginal_per_unit",
        "n_estimators": 200,
        "min_rows": 30,
    },
    "quality": {
        "subcomponents": [
            "structured_completeness",
            "documentation_consistency",
            "timeliness",
        ],
        "min_available_subcomponents": 1,
    },
    "uncertainty": {
        "weights": {
            "conformal_width": 0.35,
            "missingness": 0.35,
            "ood": 0.20,
            "low_support": 0.10,
        },
        "ood_z_threshold": 3.0,
        "min_training_rows": 100,
        "high_uncertainty_threshold": 0.60,
    },
    "aggregation": {
        "epsilon": 1e-6,
        "min_nurse_reliability": 0.60,
        "severity_clip": 2.0,
    },
    "scoring": {
        "required_components": ["benefit", "attribution", "quality", "uncertainty"],
        "optional_components": ["causal"],
        "min_confidence": 0.05,
    },
    "outcome_goal_rules": [
        {"contains": ["delta_ri", "ri_final", "recovery", "utility"], "goal": "higher_is_better"},
        {
            "contains": [
                "mortality",
                "fall",
                "pressure_injury",
                "pressure_ulcer",
                "upp",
                "infection",
                "readmission",
                "deterioration",
                "length_of_stay",
                "los",
            ],
            "goal": "lower_is_better",
        },
    ],
}


def build_default_icea_plus_spec() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_ICEA_PLUS_SPEC)



def deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged



def formula_protocol_hash(spec: dict[str, Any]) -> str:
    dumped = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()
