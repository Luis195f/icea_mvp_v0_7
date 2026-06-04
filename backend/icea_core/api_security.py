"""Small security helpers shared by ICEA API views."""

from __future__ import annotations

from typing import Any

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
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        return str(getattr(user, "username", "") or getattr(user, "pk", "") or "authenticated")
    if getattr(request, "icea_api_key_authenticated", False):
        return "service"
    return "anonymous"


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
    return append_audit_event(
        event_type=event_type,
        payload=safe_payload,
        context=context,
        actor=actor_override or request_actor(request),
    )
