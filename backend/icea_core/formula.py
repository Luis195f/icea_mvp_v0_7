from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np


@dataclass
class ICEAPlusComponentValue:
    raw: float | None
    normalized: float | None
    available: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class ICEAPlusLineage:
    formula_version: str
    formula_protocol_hash: str
    model_id: str | None
    model_version: str | None
    baseline_model_id: str | None
    causal_spec_hash: str | None
    outcome: str
    outcome_goal: str
    treatment: str | None
    nurse_cols: list[str]
    transformations: dict[str, Any]
    source: dict[str, Any]


@dataclass
class ICEAPlusRowScore:
    row_id: str
    grain: str
    episode_id: int | None
    window_id: str | None
    patient_key: str | None
    unit_id: int | None
    start_dt: str | None
    end_dt: str | None
    status: str
    provisional: bool
    confidence: dict[str, Any]
    raw_score: float | None
    score: float | None
    flags: dict[str, Any]
    warnings: list[str]
    components: dict[str, ICEAPlusComponentValue]
    legacy_icea: dict[str, Any]
    lineage: ICEAPlusLineage
    aggregation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["components"] = {name: asdict(value) for name, value in self.components.items()}
        data["lineage"] = asdict(self.lineage)
        return data



def sigmoid100(value: float) -> float:
    clipped = float(np.clip(value, -20.0, 20.0))
    return float(100.0 / (1.0 + np.exp(-clipped)))



def confidence_label(value: float) -> str:
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"



def compute_row_score(
    *,
    row_id: str,
    grain: str,
    episode_id: int | None,
    window_id: str | None,
    patient_key: str | None,
    unit_id: int | None,
    start_dt: str | None,
    end_dt: str | None,
    components: dict[str, ICEAPlusComponentValue],
    weights: dict[str, float],
    raw_uncertainty: float | None,
    lineage: ICEAPlusLineage,
    legacy_icea: dict[str, Any],
    aggregation: dict[str, Any],
    spec: dict[str, Any],
) -> ICEAPlusRowScore:
    scoring_spec = spec.get("scoring") or {}
    required = list(scoring_spec.get("required_components") or [])
    optional = list(scoring_spec.get("optional_components") or [])
    warnings: list[str] = []

    missing_required = [name for name in required if not components.get(name) or not components[name].available]
    missing_optional = [name for name in optional if not components.get(name) or not components[name].available]

    flags = {
        "causal_available": "causal" not in missing_optional,
        "low_support": bool((raw_uncertainty or 0.0) >= 0.25 and components.get("uncertainty", ICEAPlusComponentValue(None, None, False)).available),
        "high_uncertainty": bool((raw_uncertainty or 0.0) >= float(((spec.get("uncertainty") or {}).get("high_uncertainty_threshold")) or 0.60)),
        "missing_key_inputs": bool(missing_required),
        "insufficient_evidence": bool(missing_required),
    }

    status = "complete"
    provisional = False
    raw_score = None
    score = None

    if missing_required:
        status = "insufficient_evidence"
        warnings.extend([f"missing_required_component:{name}" for name in missing_required])
    else:
        raw_score = float(weights.get("intercept", 0.0))
        raw_score += float(weights.get("benefit", 1.0)) * float(components["benefit"].normalized or 0.0)
        raw_score += float(weights.get("attribution", 1.0)) * float(components["attribution"].normalized or 0.0)
        raw_score += float(weights.get("quality", 1.0)) * float(components["quality"].normalized or 0.0)
        raw_score -= float(weights.get("uncertainty", 1.0)) * float(components["uncertainty"].normalized or 0.0)
        if "causal" not in missing_optional:
            raw_score += float(weights.get("causal", 1.0)) * float(components["causal"].normalized or 0.0)
        else:
            provisional = True
            status = "provisional"
            warnings.append("causal_unavailable_score_is_provisional")
        score = sigmoid100(raw_score)

    if flags["high_uncertainty"]:
        warnings.append("high_uncertainty")
    if flags["low_support"]:
        warnings.append("low_support")

    conf_base = 1.0 - float(max(raw_uncertainty or 0.0, 0.0))
    if provisional:
        conf_base *= 0.85
    if status == "insufficient_evidence":
        conf_base *= 0.25
    conf_value = float(np.clip(conf_base, float(scoring_spec.get("min_confidence") or 0.05), 1.0))

    return ICEAPlusRowScore(
        row_id=row_id,
        grain=grain,
        episode_id=episode_id,
        window_id=window_id,
        patient_key=patient_key,
        unit_id=unit_id,
        start_dt=start_dt,
        end_dt=end_dt,
        status=status,
        provisional=bool(provisional),
        confidence={"value": conf_value, "label": confidence_label(conf_value)},
        raw_score=raw_score,
        score=score,
        flags=flags,
        warnings=sorted(set(warnings)),
        components=components,
        legacy_icea=legacy_icea,
        lineage=lineage,
        aggregation=aggregation,
    )
