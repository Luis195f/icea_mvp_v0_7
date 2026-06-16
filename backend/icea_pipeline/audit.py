from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from icea_core.audit_identity import safe_stored_audit_actor
from icea_pipeline.models import AuditEvent

logger = logging.getLogger(__name__)


def _stable_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _get_secret() -> str:
    """Return the HMAC secret for audit chaining.

    Security invariant:
    - If ICEA_SECURE_MODE=true -> fail-closed unless a strong, non-default secret is provided.
    - Else -> allow a dev fallback for local testing.
    """
    secure_mode = bool(getattr(settings, "ICEA_SECURE_MODE", False)) or os.environ.get(
        "ICEA_SECURE_MODE", "false"
    ).lower() == "true"

    # Prefer Django settings, but allow an alias env var for deployments that centralize secrets.
    secret = getattr(settings, "AUDIT_LOG_SECRET", "") or os.environ.get("ICEA_AUDIT_SECRET", "")
    secret = str(secret or "").strip()

    if secure_mode:
        # Fail-closed in secure mode (ENS Alto): reject empty, known-dev default, placeholders, and weak secrets.
        if (
            (not secret)
            or (secret == "dev-audit-secret")
            or ("change_me" in secret.lower())
            or (len(secret) < 32)
        ):
            raise ImproperlyConfigured(
                "ICEA_SECURE_MODE=true requires AUDIT_LOG_SECRET (or ICEA_AUDIT_SECRET) to be set "
                "to a strong, non-default value (>= 32 chars) and MUST NOT contain placeholder strings "
                "such as 'CHANGE_ME'. Refusing to start (fail-closed)."
            )
        return secret

    # Dev / local fallback (explicitly NOT for production).
    if not secret:
        secret = "dev-audit-secret"
    return secret


def _secure_mode_enabled() -> bool:
    return bool(getattr(settings, "ICEA_SECURE_MODE", False)) or os.environ.get(
        "ICEA_SECURE_MODE", "false"
    ).lower() == "true"


def append_audit_event(*, event_type: str, payload: Any, context: str = "", actor: str = "api") -> str | None:
    """Append an audit event with hash chaining.

    In secure mode audit append is mandatory and fails closed. Outside secure
    mode it remains best-effort for local development and tests.
    Returns created event id as string.
    """

    try:
        now = timezone.now()
        actor = safe_stored_audit_actor(actor)
        payload_sha = sha256_hex(_stable_dumps(payload))

        last = AuditEvent.objects.order_by("-created_at").only("chain_hash").first()
        prev_hash = last.chain_hash if last else ""

        chain_material = "|".join([
            prev_hash,
            now.isoformat(),
            event_type,
            actor,
            context,
            payload_sha,
        ])
        chain_hash = sha256_hex(chain_material)
        sig = hmac.new(_get_secret().encode("utf-8"), chain_hash.encode("utf-8"), hashlib.sha256).hexdigest()

        ev = AuditEvent.objects.create(
            created_at=now,
            event_type=event_type,
            actor=actor,
            context=context,
            payload_sha256=payload_sha,
            prev_hash=prev_hash,
            chain_hash=chain_hash,
            hmac_sig=sig,
        )
        return str(ev.id)
    except Exception as exc:
        logger.warning(
            "ICEA audit append failed event_type=%s context=%s error_class=%s",
            str(event_type)[:64],
            str(context)[:255],
            exc.__class__.__name__,
        )
        if _secure_mode_enabled():
            raise
        return None
