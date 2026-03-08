from __future__ import annotations

"""Federated Causal Learning scaffolding (EHDS/GDPR-ready).

This is a *minimal* federated layer to support cross-hospital collaboration
without centralizing patient-level data.

Scope (v0.7):
- Define a federated round (protocol) and accept model updates (weights/artifacts).
- Aggregate updates into an *ensemble* of local causal models (weighted by sample size).

Why ensemble instead of true gradient merging?
- EconML CausalForestDML does not expose stable, portable gradients.
- An ensemble is still privacy-preserving and empirically robust in practice.
- The API + data model keeps the door open for later FedAvg/FedProx or secure aggregation.

Security:
- Optional HMAC signature (ICEA_FEDERATED_SECRET) for update submission.
"""

import base64
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("utf-8")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))


def sign_body(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, digestmod="sha256").hexdigest()


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    if not secret or not signature:
        return False
    expected = sign_body(secret, body)
    return hmac.compare_digest(expected, signature.strip())


def save_pickle_artifact(obj: Any, *, prefix: str = "fed") -> str:
    """Backward-compatible name, but *pickle is forbidden*.

    SECURITY (v0.7.1):
      - Never serialize with pickle.
      - Accept only safe types:
          * bytes/bytearray -> stored as .bin
          * JSON-serializable (dict/list/scalar) -> stored as .json
      - Any other type raises NotImplementedError.

    This prevents RCE vectors via malicious pickle payloads.
    """

    model_dir = Path(getattr(settings, "ICEA_MODEL_DIR", Path(settings.BASE_DIR) / "models"))
    model_dir.mkdir(parents=True, exist_ok=True)

    # Store opaque bytes safely.
    if isinstance(obj, (bytes, bytearray)):
        fname = f"{prefix}_{uuid.uuid4().hex}.bin"
        path = model_dir / fname
        with open(path, "wb") as f:
            f.write(bytes(obj))
        return str(path)

    # Store JSON safely.
    try:
        body = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except Exception as e:
        raise NotImplementedError(
            "Insecure deserialization blocked: only bytes or JSON-serializable objects are allowed"
        ) from e

    fname = f"{prefix}_{uuid.uuid4().hex}.json"
    path = model_dir / fname
    with open(path, "wb") as f:
        f.write(body)
    return str(path)


def load_pickle_artifact(path: str) -> Any:
    """Explicitly blocked.

    Any unpickling of external artifacts is a Remote Code Execution vector.
    If you need to load a federated artifact, use a safe parser depending on
    the file extension (.json or .bin) and validate signatures.
    """

    raise NotImplementedError("Insecure deserialization blocked: pickle loading is not permitted")


def build_ensemble_spec(updates: list[dict[str, Any]]) -> dict[str, Any]:
    """Create an aggregation spec for an ensemble."""
    total = sum(float(u.get("n_rows") or 0.0) for u in updates) or 1.0
    members = []
    for u in updates:
        w = float(u.get("n_rows") or 0.0) / total
        members.append(
            {
                "model_artifact_id": str(u.get("model_artifact_id") or ""),
                "weight": w,
                "n_rows": int(u.get("n_rows") or 0),
                "client_id": str(u.get("client_id") or ""),
            }
        )
    return {
        "method": "weighted_ensemble",
        "members": members,
        "total_rows": int(total),
    }
