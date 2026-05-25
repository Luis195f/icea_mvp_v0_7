from __future__ import annotations

import os

from django.http import JsonResponse


class OptionalAPIKeyMiddleware:
    """Optional API key gate.

    If ICEA_API_KEY is set, all /api/ requests require a matching key.
    Backwards compatible: if not set, it is a no-op.

    Accepted headers:
      - X-ICEA-API-KEY: <key>
      - Authorization: ApiKey <key>
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.api_key = (os.environ.get("ICEA_API_KEY") or "").strip()

    def __call__(self, request):
        if self.api_key and request.path.startswith("/api/"):
            key = (request.headers.get("X-ICEA-API-KEY") or "").strip()
            auth = (request.headers.get("Authorization") or "").strip()
            if not key and auth.lower().startswith("apikey "):
                key = auth.split(" ", 1)[1].strip()
            if key != self.api_key:
                return JsonResponse({"detail": "Unauthorized"}, status=401)
            request.icea_api_key_authenticated = True
        return self.get_response(request)
