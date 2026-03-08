"""RBAC permissions for ICEA+ (backward compatible).

Design goals:
1) Backward compatibility: existing clients keep working unless the operator
   explicitly enables enforcement flags.
2) Institutional hardening: when enabled, enforce authentication and minimum
   roles for high-risk operations (writeback/governance/federated).
3) OIDC/JWT compatibility: roles can come from JWT claims or Django groups.

Environment toggles:
  - ICEA_AUTH_REQUIRED=true|false
  - ICEA_RBAC_ENFORCE=true|false
  - ICEA_JWT_ROLE_CLAIM=roles (default)  (comma-separated list, or single)
  - ICEA_RBAC_RULES_JSON='{"/api/v1/fhir/writeback": ["command_center_admin"]}'

Note:
  This module is intentionally dependency-free beyond DRF.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from django.core.cache import cache

from rest_framework.permissions import BasePermission


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_token_payload(request) -> dict[str, Any]:
    """Best-effort extraction of JWT payload from DRF SimpleJWT."""
    auth = getattr(request, "auth", None)
    # SimpleJWT tokens expose .payload
    payload = getattr(auth, "payload", None)
    if isinstance(payload, dict):
        return payload
    # Some deployments may attach a dict-like auth
    if isinstance(auth, dict):
        return auth
    return {}


def _normalize_roles(val: Any) -> set[str]:
    if val is None:
        return set()
    if isinstance(val, str):
        # allow "a,b,c" or "a"
        parts = [p.strip() for p in val.replace(";", ",").split(",")]
        return {p for p in parts if p}
    if isinstance(val, (list, tuple, set)):
        return {str(x).strip() for x in val if str(x).strip()}
    return {str(val).strip()} if str(val).strip() else set()


def get_request_roles(request) -> set[str]:
    """Resolve roles from:
    1) JWT claim (ICEA_JWT_ROLE_CLAIM)
    2) Django groups (request.user.groups)
    3) Optional header X-ICEA-ROLES (service-to-service)
    """

    roles: set[str] = set()

    # 3) Service-to-service header (optional)
    hdr = (request.headers.get("X-ICEA-ROLES") or "").strip()
    roles |= _normalize_roles(hdr)

    # 1) JWT claim
    claim = os.environ.get("ICEA_JWT_ROLE_CLAIM", "roles").strip() or "roles"
    payload = _get_token_payload(request)
    if payload:
        roles |= _normalize_roles(payload.get(claim))
        # Common alternatives
        roles |= _normalize_roles(payload.get("role"))
        roles |= _normalize_roles(payload.get("groups"))

    # 2) Django groups
    user = getattr(request, "user", None)
    try:
        if user and getattr(user, "is_authenticated", False):
            roles |= {g.name for g in user.groups.all()}
    except Exception:
        pass

    return {r.lower() for r in roles}


def _load_rbac_rules() -> dict[str, list[str]]:
    """Load RBAC rules from env.

    Format: JSON mapping of path prefix -> list of required roles.
    Example:
        {
          "/api/v1/fhir/writeback": ["command_center_admin"],
          "/api/v1/governance": ["command_center_admin"],
          "/api/v1/causal/run": ["clinical_staff"]
        }
    """
    raw = os.environ.get("ICEA_RBAC_RULES_JSON", "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            out: dict[str, list[str]] = {}
            for k, v in obj.items():
                if not isinstance(k, str):
                    continue
                out[k] = [str(x).lower() for x in (v or [])]
            return out
    except Exception:
        return {}
    return {}


class ICEABackwardCompatiblePermission(BasePermission):
    """Default permission that preserves MVP compatibility.

    - If ICEA_AUTH_REQUIRED is false (default), allow all requests.
    - If ICEA_AUTH_REQUIRED is true, require authenticated.
    - If ICEA_RBAC_ENFORCE is true, apply path-prefix RBAC rules.

    This allows deployments to flip flags at runtime without changing URLs.
    """

    message = "Unauthorized"

    def has_permission(self, request, view) -> bool:
        auth_required = _truthy(os.environ.get("ICEA_AUTH_REQUIRED", "false"))
        rbac_enforce = _truthy(os.environ.get("ICEA_RBAC_ENFORCE", "false"))

        # MVP mode: open by default.
        if not auth_required and not rbac_enforce:
            return True

        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return False

        if not rbac_enforce:
            return True

        roles = get_request_roles(request)
        path = (getattr(request, "path", "") or "").rstrip("/")
        rules = _load_rbac_rules()

        # Built-in hardening defaults (only when rbac_enforce is true).
        # These are conservative and can be overridden by ICEA_RBAC_RULES_JSON.
        builtin = {
            "/api/v1/fhir/writeback": ["command_center_admin"],
            "/api/v1/governance": ["command_center_admin"],
            "/api/v1/federated": ["command_center_admin"],
        }
        for k, v in builtin.items():
            rules.setdefault(k, v)

        # Find the most specific matching prefix.
        matched_roles: list[str] = []
        matched_len = -1
        for prefix, req_roles in rules.items():
            p = prefix.rstrip("/")
            if p and path.startswith(p) and len(p) > matched_len:
                matched_len = len(p)
                matched_roles = req_roles

        if not matched_roles:
            # Authenticated is enough.
            return True

        return bool(roles.intersection({r.lower() for r in matched_roles}))


class IsClinicalStaff(BasePermission):
    """Allow users with role 'clinical_staff' (or superuser)."""

    message = "Clinical staff role required"

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user and getattr(user, "is_superuser", False):
            return True
        if not user or not getattr(user, "is_authenticated", False):
            return False
        return "clinical_staff" in get_request_roles(request)


class IsCommandCenterAdmin(BasePermission):
    """Allow users with role 'command_center_admin' (or superuser)."""

    message = "Command Center admin role required"

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user and getattr(user, "is_superuser", False):
            return True
        if not user or not getattr(user, "is_authenticated", False):
            return False
        return "command_center_admin" in get_request_roles(request)


class RequiresHMACSignature(BasePermission):
    """Require a valid HMAC-SHA256 signature for critical write endpoints.

    Graceful degradation (feature flag):
      - If ICEA_AUDIT_SIGNING_REQUIRED is false (default), allow.
      - If true, require header X-ICEA-Signature computed over the *raw request body*.

    Accepted formats:
      - hex digest: <hexdigest>
      - prefixed:   sha256=<hexdigest>
      - base64:     b64:<base64digest>

    Secret:
      - ICEA_AUDIT_SECRET (set in environment / secret manager)

    Notes:
      - This does not replace TLS/mTLS. It adds tamper-evidence at the app layer.
      - Ensure RequestSizeLimitMiddleware is enabled to avoid hashing huge bodies.
    """

    message = "Valid request signature required"

    def has_permission(self, request, view) -> bool:
        if not _truthy(os.environ.get("ICEA_AUDIT_SIGNING_REQUIRED", "false")):
            return True

        secret = (os.environ.get("ICEA_AUDIT_SECRET", "") or "").encode("utf-8")
        if not secret:
            # Fail-closed if signing is required but not configured.
            self.message = "Audit signing required but ICEA_AUDIT_SECRET is not set"
            return False

        provided = (request.headers.get("X-ICEA-Signature") or "").strip()
        if not provided:
            self.message = "Missing X-ICEA-Signature"
            return False

        provided_hex = provided
        provided_b64 = None

        if provided.lower().startswith("sha256="):
            provided_hex = provided.split("=", 1)[1].strip()

        if provided.lower().startswith("b64:"):
            provided_b64 = provided.split(":", 1)[1].strip()

        # Compute expected digest over raw body.
        try:
            body = request.body or b""
        except Exception:
            body = b""

        digest_bytes = hmac.new(secret, body, hashlib.sha256).digest()
        expected_hex = digest_bytes.hex()
        expected_b64 = base64.b64encode(digest_bytes).decode("ascii")

        # Constant-time compare.
        ok = False
        try:
            ok = hmac.compare_digest(provided_hex, expected_hex)
        except Exception:
            ok = False

        if not ok and provided_b64 is not None:
            try:
                ok = hmac.compare_digest(provided_b64, expected_b64)
            except Exception:
                ok = False

        if not ok and provided_b64 is None:
            # Best-effort: allow bare base64 without prefix.
            try:
                ok = hmac.compare_digest(provided, expected_b64)
            except Exception:
                ok = False

        if not ok:
            self.message = "Invalid X-ICEA-Signature"

        return ok


def _parse_timestamp_to_epoch_seconds(value: str) -> float | None:
    """Parse a timestamp header to epoch seconds.

    Accepts:
      - UNIX epoch seconds (int/float as string)
      - ISO-8601 (e.g., 2026-03-01T12:34:56Z or with offset)

    Returns None if parsing fails.
    """
    v = (value or "").strip()
    if not v:
        return None

    # UNIX epoch
    try:
        if all(c in "0123456789." for c in v) and any(c.isdigit() for c in v):
            return float(v)
    except Exception:
        pass

    # ISO-8601
    try:
        # Support trailing Z
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


class RequiresAntiReplayHMAC(BasePermission):
    """Anti-replay HMAC enforcement for critical endpoints.

    Backward compatible behavior:
      - ICEA_AUDIT_SIGNING_REQUIRED=false (default) => allow.
      - ICEA_AUDIT_SIGNING_REQUIRED=true and ICEA_ANTI_REPLAY_REQUIRED=false =>
            behave like RequiresHMACSignature (HMAC over raw body).

    Anti-replay mode (feature flag):
      - ICEA_ANTI_REPLAY_REQUIRED=true => require:
            X-ICEA-Timestamp  (UNIX epoch or ISO)
            X-ICEA-Nonce      (unique random string)
            X-ICEA-Signature  (HMAC-SHA256 over: timestamp + '.' + nonce + '.' + raw_body)

    Replay window:
      - ICEA_REPLAY_WINDOW_SECONDS (default 300)

    Nonce tracking:
      - Uses Django cache backend for nonce uniqueness within the replay window.
        The nonce is stored with TTL=window_seconds.
    """

    message = "Valid request signature required"

    def has_permission(self, request, view) -> bool:
        # Graceful degradation: open if signing is not required.
        if not _truthy(os.environ.get("ICEA_AUDIT_SIGNING_REQUIRED", "false")):
            return True

        secret = (os.environ.get("ICEA_AUDIT_SECRET", "") or "").encode("utf-8")
        if not secret:
            self.message = "Audit signing required but ICEA_AUDIT_SECRET is not set"
            return False

        anti_replay = _truthy(os.environ.get("ICEA_ANTI_REPLAY_REQUIRED", "false"))

        # Pull raw body once.
        try:
            body = request.body or b""
        except Exception:
            body = b""

        if not anti_replay:
            return self._verify_signature(request=request, secret=secret, body=body, prefix=b"")

        # Anti-replay required
        window_seconds = 300
        try:
            window_seconds = int(os.environ.get("ICEA_REPLAY_WINDOW_SECONDS", "300"))
        except Exception:
            window_seconds = 300
        if window_seconds <= 0:
            window_seconds = 300

        ts_raw = (request.headers.get("X-ICEA-Timestamp") or "").strip()
        nonce = (request.headers.get("X-ICEA-Nonce") or "").strip()
        if not ts_raw:
            self.message = "Missing X-ICEA-Timestamp"
            return False
        if not nonce:
            self.message = "Missing X-ICEA-Nonce"
            return False
        if len(nonce) > 512:
            self.message = "X-ICEA-Nonce too long"
            return False

        ts = _parse_timestamp_to_epoch_seconds(ts_raw)
        if ts is None:
            self.message = "Invalid X-ICEA-Timestamp"
            return False

        now = time.time()
        if abs(now - ts) > window_seconds:
            self.message = "Replay window expired"
            return False

        # Canonicalize timestamp to integer epoch seconds for signature stability.
        # This prevents integration breakage where clients send ISO vs epoch strings.
        ts_canon = str(int(ts))
        prefix = (ts_canon + "." + nonce + ".").encode("utf-8")

        # Fast replay check (best effort): reject if nonce already seen.
        nonce_key = "icea:nonce:" + hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        try:
            if cache.get(nonce_key) is not None:
                self.message = "Replay detected (nonce already used)"
                return False
        except Exception:
            self.message = "Anti-replay cache unavailable"
            return False

        # Verify signature first to avoid cache pollution by invalid requests.
        ok_sig = self._verify_signature(request=request, secret=secret, body=body, prefix=prefix)

        # Backward compatibility: accept legacy signatures that used the raw timestamp string.
        # This is enabled by default to avoid breaking existing clients; disable in ENS mode.
        if not ok_sig and _truthy(os.environ.get("ICEA_ACCEPT_LEGACY_TIMESTAMP_SIGNATURE", "true")):
            legacy_prefix = (ts_raw + "." + nonce + ".").encode("utf-8")
            ok_sig = self._verify_signature(request=request, secret=secret, body=body, prefix=legacy_prefix)

        if not ok_sig:
            return False

        # Record nonce (atomic where supported)
        try:
            added = cache.add(nonce_key, "1", timeout=window_seconds)
            if not added:
                self.message = "Replay detected (nonce already used)"
                return False
        except Exception:
            self.message = "Anti-replay cache unavailable"
            return False

        return True

    def _verify_signature(self, request, secret: bytes, body: bytes, prefix: bytes) -> bool:
        provided = (request.headers.get("X-ICEA-Signature") or "").strip()
        if not provided:
            self.message = "Missing X-ICEA-Signature"
            return False

        provided_hex = provided
        provided_b64 = None
        if provided.lower().startswith("sha256="):
            provided_hex = provided.split("=", 1)[1].strip()
        if provided.lower().startswith("b64:"):
            provided_b64 = provided.split(":", 1)[1].strip()

        msg = prefix + body
        digest_bytes = hmac.new(secret, msg, hashlib.sha256).digest()
        expected_hex = digest_bytes.hex()
        expected_b64 = base64.b64encode(digest_bytes).decode("ascii")

        ok = False
        try:
            ok = hmac.compare_digest(provided_hex, expected_hex)
        except Exception:
            ok = False

        if not ok and provided_b64 is not None:
            try:
                ok = hmac.compare_digest(provided_b64, expected_b64)
            except Exception:
                ok = False

        if not ok and provided_b64 is None:
            # Best-effort: allow bare base64 without prefix.
            try:
                ok = hmac.compare_digest(provided, expected_b64)
            except Exception:
                ok = False

        if not ok:
            self.message = "Invalid X-ICEA-Signature"

        return ok

