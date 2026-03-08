from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class FollowUpSchema(BaseModel):
    """Follow-up definition for target trial emulation.

    In the MVP, follow-up is operationalized primarily as an outcome horizon in hours
    (used to compute delta_ri between time-zero and time-zero + horizon).
    """

    horizon_hours: int = Field(default=12, ge=1, le=720)
    anchor: Literal["time_zero", "window_start"] = "time_zero"
    mode: Literal["fixed", "shift"] = "fixed"


class EligibilityCriterion(BaseModel):
    """Human-readable eligibility criteria.

    This is intentionally light-weight; a hospital can store a formal rule (SQL/FHIRPath)
    in `expression` if available.
    """

    description: str = Field(min_length=1, max_length=512)
    expression: dict[str, Any] | str | None = None


class TargetTrialSchema(BaseModel):
    """Target Trial Template.

    Used as schema-as-code validation for a flexible JSON spec.
    Optional: if the client doesn't provide it, the pipeline stays backward compatible.
    """

    time_zero: str | datetime = Field(default="window_start")
    eligibility: list[EligibilityCriterion] = Field(default_factory=list)
    follow_up: FollowUpSchema = Field(default_factory=FollowUpSchema)
    estimand: Literal["ATE", "CATE", "ATT"] = "ATE"

    @field_validator("time_zero")
    @classmethod
    def validate_time_zero(cls, v):
        if isinstance(v, datetime):
            return v
        allowed = {"admission", "shift_start", "window_start", "custom"}
        sv = str(v).strip()
        if sv in allowed:
            return sv
        try:
            s2 = sv.replace("Z", "+00:00")
            return datetime.fromisoformat(s2)
        except Exception as e:
            raise ValueError(
                "time_zero must be an ISO datetime or one of: admission, shift_start, window_start, custom"
            ) from e


def canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding suitable for hashing."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def validate_target_trial(spec: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Validate an optional Target Trial Template in a causal spec.

    Returns:
      (validated_target_trial_or_none, issues)

    issues follow a minimal structure suitable for API responses.
    """

    issues: list[dict[str, Any]] = []
    tt = spec.get("target_trial")

    if tt is None:
        keys = {"time_zero", "eligibility", "follow_up", "estimand"}
        if any(k in spec for k in keys):
            tt = {k: spec.get(k) for k in keys if k in spec}
        else:
            return None, issues

    try:
        model = TargetTrialSchema.model_validate(tt)
        return model.model_dump(mode="json"), issues
    except ValidationError as e:
        for err in e.errors():
            issues.append({"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")})
        return None, issues
