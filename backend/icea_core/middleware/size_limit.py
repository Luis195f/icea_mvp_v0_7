"""Request size limiting middleware (Zero-Trust perimeter).

Purpose
-------
Block oversized HTTP request bodies *before* they reach DRF parsers, preventing
memory pressure / DoS via large JSON payloads.

Design
------
Graceful degradation via feature flags (OFF by default):

  - ICEA_ENABLE_REQUEST_LIMITS=true|false  (default: false)
  - ICEA_MAX_REQUEST_SIZE_MB=5            (default global cap)

Dynamic route caps (asymmetric limits):

  - ICEA_ROUTE_SIZE_LIMITS_JSON='{"/api/v1/federated/": 10, "/api/v1/pipeline/ingest/": 5, "/api/v1/fhir/writeback/": 1}'

The middleware matches request.path by prefix. If multiple prefixes match,
the *most specific* (longest) prefix wins.

Notes
-----
- This middleware relies on CONTENT_LENGTH. If a reverse proxy sends chunked
  requests without CONTENT_LENGTH, enforce size limits at the proxy layer
  (NGINX/Envoy) too.
- The middleware is intentionally lightweight and does not read request.body.
"""

from __future__ import annotations

import json
import os
from typing import Any

from django.http import JsonResponse


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


class RequestSizeLimitMiddleware:
    """Reject requests whose declared Content-Length exceeds a configured cap.

    Supports a global cap + optional per-route caps.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = _truthy(os.environ.get("ICEA_ENABLE_REQUEST_LIMITS", "false"))
        try:
            mb = float(os.environ.get("ICEA_MAX_REQUEST_SIZE_MB", "5"))
        except Exception:
            mb = 5.0
        # Cap must be positive.
        if mb <= 0:
            mb = 5.0

        self.default_max_bytes = int(mb * 1024 * 1024)
        self._route_caps: list[tuple[str, int]] = self._load_route_caps()

    def _load_route_caps(self) -> list[tuple[str, int]]:
        """Parse ICEA_ROUTE_SIZE_LIMITS_JSON once at startup.

        Returns a list of (prefix, max_bytes) sorted by prefix length desc.
        """
        raw = (os.environ.get("ICEA_ROUTE_SIZE_LIMITS_JSON", "") or "").strip()
        if not raw:
            return []

        try:
            obj: Any = json.loads(raw)
        except Exception:
            return []

        if not isinstance(obj, dict):
            return []

        out: list[tuple[str, int]] = []
        for prefix, mb_val in obj.items():
            if not isinstance(prefix, str):
                continue
            p = prefix.strip()
            if not p:
                continue
            try:
                mb_f = float(mb_val)
            except Exception:
                continue
            if mb_f <= 0:
                continue
            out.append((p, int(mb_f * 1024 * 1024)))

        # Most specific prefix wins.
        out.sort(key=lambda x: len(x[0]), reverse=True)
        return out

    def _cap_for_path(self, path: str) -> int:
        pth = path or ""
        for prefix, cap_bytes in self._route_caps:
            if pth.startswith(prefix):
                return cap_bytes
        return self.default_max_bytes

    def __call__(self, request):
        if self.enabled:
            # Optional hard block for chunked uploads.
            # Django/WSGI cannot enforce body size caps reliably for chunked transfer
            # without reading the stream; for ENS Alto we may fail-closed.
            if _truthy(os.environ.get("ICEA_FAIL_ON_CHUNKED", "false")):
                te = (
                    (request.META.get("HTTP_TRANSFER_ENCODING") or "")
                    or (request.META.get("TRANSFER_ENCODING") or "")
                ).lower()
                if "chunked" in te and request.method in {"POST", "PUT", "PATCH"}:
                    # 411 Length Required is semantically correct here.
                    return JsonResponse(
                        {"detail": "Chunked Transfer-Encoding is not allowed", "hint": "Send Content-Length"},
                        status=411,
                    )

            cl = request.META.get("CONTENT_LENGTH")
            if cl:
                try:
                    max_bytes = self._cap_for_path(getattr(request, "path", "") or "")
                    if int(cl) > max_bytes:
                        # 413 Payload Too Large
                        return JsonResponse(
                            {
                                "detail": "Payload Too Large",
                                "max_bytes": max_bytes,
                            },
                            status=413,
                        )
                except Exception:
                    # Malformed CONTENT_LENGTH -> treat as suspicious only in secure mode.
                    # For backward compatibility, we do not hard-fail.
                    pass
        return self.get_response(request)
