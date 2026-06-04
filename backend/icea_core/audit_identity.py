"""Pseudonymous identities for ICEA audit events."""

from __future__ import annotations

import hashlib
import hmac
import os
import re

from django.conf import settings

from icea_core.net import get_client_ip


_PSEUDONYMOUS_ACTOR_RE = re.compile(r"^[a-z][a-z0-9_]*:[0-9a-f]{64}$")
_SYSTEM_ACTOR_RE = re.compile(r"^system:[a-z][a-z0-9_]*$")
_SYSTEM_ACTORS = {"api", "management_command"}


def hash_audit_identity(value: str) -> str:
    """Return a stable, peppered HMAC without persisting the source identity."""

    secret = str(
        getattr(settings, "AUDIT_LOG_SECRET", "")
        or os.environ.get("ICEA_AUDIT_SECRET", "")
        or settings.SECRET_KEY
        or "icea-audit-identity"
    )
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def safe_caller_audit_identity(request) -> tuple[str, str]:
    """Return a non-reversible descriptive caller identity for audit events."""

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        user_pk = getattr(user, "pk", None)
        if user_pk in (None, ""):
            user_pk = getattr(user, "id", None)
        if user_pk not in (None, ""):
            return "authenticated_user", hash_audit_identity(f"user_pk:{user_pk}")

        fallback = str(getattr(user, "username", "") or getattr(user, "email", "") or "").strip().lower()
        if fallback:
            return "authenticated_user_fallback", hash_audit_identity(f"user_fallback:{fallback}")
        return "authenticated_unknown", hash_audit_identity("authenticated_unknown")

    meta = getattr(request, "META", {}) or {}
    try:
        client_ip = str(get_client_ip(request) or "").strip()
    except Exception:
        client_ip = ""
    if client_ip in {"0.0.0.0", "::"}:
        client_ip = ""
    user_agent = str(meta.get("HTTP_USER_AGENT") or "").strip()

    is_service = bool(getattr(request, "icea_api_key_authenticated", False))
    caller_kind = "service_client" if is_service else "anonymous_client"
    if client_ip or user_agent:
        return caller_kind, hash_audit_identity(f"ip:{client_ip}|ua:{user_agent}")

    caller_kind = "service_unknown" if is_service else "anonymous_unknown"
    return caller_kind, hash_audit_identity(caller_kind)


def safe_caller_audit_dedupe_identity(request) -> tuple[str, str]:
    """Return a bounded identity for permission-denial audit deduplication.

    Authenticated callers retain their stable user identity. Anonymous and
    service callers use only the securely resolved client IP; attacker-
    controlled User-Agent and other request headers never affect the key.
    """

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        return safe_caller_audit_identity(request)

    try:
        client_ip = str(get_client_ip(request) or "").strip()
    except Exception:
        client_ip = ""
    if client_ip in {"0.0.0.0", "::"}:
        client_ip = ""

    is_service = bool(getattr(request, "icea_api_key_authenticated", False))
    caller_kind = "service_client" if is_service else "anonymous_client"
    if client_ip:
        return caller_kind, hash_audit_identity(f"ip:{client_ip}")

    caller_kind = "service_unknown" if is_service else "anonymous_unknown"
    return caller_kind, hash_audit_identity(f"dedupe:{caller_kind}")


def safe_audit_actor(request) -> str:
    """Return the canonical pseudonymous actor for a request."""

    caller_kind, caller_hash = safe_caller_audit_identity(request)
    return f"{caller_kind}:{caller_hash}"


def safe_stored_audit_actor(actor: object) -> str:
    """Sanitize actors at persistence and presentation boundaries.

    Already-pseudonymous actors and explicitly non-human system actors remain
    stable. Every other value is treated as a potentially identifying legacy
    actor and converted to a peppered hash.
    """

    value = str(actor or "").strip()
    if _PSEUDONYMOUS_ACTOR_RE.fullmatch(value) or _SYSTEM_ACTOR_RE.fullmatch(value):
        return value
    if value in _SYSTEM_ACTORS:
        return f"system:{value}"
    if not value:
        return "system:unknown"
    return f"legacy_actor:{hash_audit_identity(f'legacy_actor:{value}')}"
