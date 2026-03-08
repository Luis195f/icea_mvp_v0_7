from __future__ import annotations

import threading


_thread = threading.local()


def set_actor(actor: str) -> None:
    _thread.actor = actor


def get_actor(default: str = "api") -> str:
    actor = getattr(_thread, "actor", "")
    return actor or default


class RequestActorMiddleware:
    """Capture an actor identity for audit/lineage records.

    Sources (best effort):
      1) X-ICEA-ACTOR header
      2) authenticated Django user (admin)
      3) default "api"

    This is non-breaking and safe for MVP deployments.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        actor = (request.headers.get("X-ICEA-ACTOR") or "").strip()
        if not actor:
            try:
                if getattr(request, "user", None) is not None and request.user.is_authenticated:
                    actor = request.user.get_username() or ""
            except Exception:
                actor = ""
        set_actor(actor or "api")
        try:
            return self.get_response(request)
        finally:
            # avoid leaking actor across requests
            set_actor("")
