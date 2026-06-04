"""Small security helpers shared by ICEA API views."""

from __future__ import annotations

from typing import Any

from icea_core.audit_identity import safe_audit_actor, safe_stored_audit_actor
from icea_pipeline.audit import append_audit_event


# Do not expand this with patient, episode, row, resource, or payload fields.
AUDIT_FIELD_ALLOWLIST = {
    "action",
    "baseline_model_id",
    "caller_hash",
    "caller_kind",
    "effective_group_by",
    "endpoint",
    "error_code",
    "evidence_status",
    "formula_version",
    "grain",
    "model_id",
    "method",
    "requested_group_by",
    "request_hash",
    "role",
    "row_count",
    "status",
    "suppressed",
    "suppressed_cells",
    "unit_id",
}


def request_actor(request) -> str:
    """Backward-compatible alias for the canonical pseudonymous actor."""

    return safe_audit_actor(request)


def append_icea_api_audit(
    *,
    request,
    event_type: str,
    context: str,
    actor_override: str | None = None,
    **fields: Any,
) -> str | None:
    """Append an audit event while dropping non-allowlisted clinical fields."""

    safe_payload = {
        key: value
        for key, value in fields.items()
        if key in AUDIT_FIELD_ALLOWLIST and value not in (None, "")
    }
    safe_payload.setdefault("endpoint", context)
    actor = safe_stored_audit_actor(actor_override) if actor_override is not None else safe_audit_actor(request)
    return append_audit_event(
        event_type=event_type,
        payload=safe_payload,
        context=context,
        actor=actor,
    )
