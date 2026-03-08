"""Encrypted JSON Field for PHI (application-layer encrypt-at-rest).

Why this approach?
- Keeps DB column type as JSON/JSONB (no DB schema migration required).
- Backward compatible: existing plaintext JSON rows remain readable.
- Encryption is transparent at the model layer.

Storage format (in DB):
  {
    "__enc__": "fernet",
    "v": 1,
    "kid": "0",
    "ct": "<fernet token base64>"
  }

If a row is plaintext (legacy), it is returned as-is.

Key management:
  - settings.PHI_ENCRYPTION_KEYS: comma-separated list of Fernet keys
    (urlsafe_b64encode(32 bytes)). First key is used for new writes.
  - In dev, if no keys are provided, a deterministic key is derived from
    SECRET_KEY to keep local bootstrap working. In production, always set
    PHI_ENCRYPTION_KEYS.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


ENC_MARK = "__enc__"


def _derive_dev_key() -> str:
    """Derive a stable Fernet key from SECRET_KEY for local/dev only."""
    # Fernet key is 32 urlsafe base64 bytes.
    sk = (getattr(settings, "SECRET_KEY", "") or "dev").encode("utf-8")
    digest = hashlib.sha256(sk).digest()  # 32 bytes
    return base64.urlsafe_b64encode(digest).decode("ascii")


def _get_keys() -> list[str]:
    keys = list(getattr(settings, "PHI_ENCRYPTION_KEYS", []) or [])
    keys = [k.strip() for k in keys if k and str(k).strip()]
    if not keys:
        # dev fallback
        keys = [_derive_dev_key()]
    return keys


def _fernet_for_kid(kid: str | None) -> Fernet | None:
    keys = _get_keys()
    if kid is not None:
        try:
            idx = int(kid)
            if 0 <= idx < len(keys):
                return Fernet(keys[idx].encode("ascii"))
        except Exception:
            pass
    # default to newest
    return Fernet(keys[0].encode("ascii")) if keys else None


def _encrypt_obj(obj: Any) -> dict[str, Any]:
    f = _fernet_for_kid("0")
    if f is None:
        # should never happen
        return obj
    dumped = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    token = f.encrypt(dumped).decode("utf-8")
    return {ENC_MARK: "fernet", "v": 1, "kid": "0", "ct": token}


def _decrypt_wrapper(wrapper: dict[str, Any]) -> Any:
    if not isinstance(wrapper, dict) or wrapper.get(ENC_MARK) != "fernet":
        return wrapper
    token = str(wrapper.get("ct") or "")
    kid = str(wrapper.get("kid") or "")
    keys = _get_keys()

    # Try indicated key first, then rotate through remaining keys.
    try_order: list[Fernet] = []
    f0 = _fernet_for_kid(kid)
    if f0 is not None:
        try_order.append(f0)
    for k in keys:
        try:
            f = Fernet(k.encode("ascii"))
            if f not in try_order:
                try_order.append(f)
        except Exception:
            continue

    for f in try_order:
        try:
            pt = f.decrypt(token.encode("utf-8"))
            return json.loads(pt.decode("utf-8"))
        except InvalidToken:
            continue
        except Exception:
            continue
    # If decryption fails, return wrapper for forensic inspection.
    return wrapper


class EncryptedJSONField(models.JSONField):
    """JSONField with transparent Fernet encryption at rest."""

    def from_db_value(self, value, expression, connection):
        value = super().from_db_value(value, expression, connection)
        if isinstance(value, dict) and value.get(ENC_MARK) == "fernet":
            return _decrypt_wrapper(value)
        return value

    def get_prep_value(self, value):
        # Preserve NULLs
        if value is None:
            return None

        # If already wrapped/encrypted, keep as-is.
        if isinstance(value, dict) and value.get(ENC_MARK) == "fernet":
            return value

        # Encrypt any JSON-serializable payload (dict/list/scalar)
        wrapped = _encrypt_obj(value)
        return super().get_prep_value(wrapped)
