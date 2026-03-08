"""Custom DRF throttles with secure client IP extraction.

Motivation
----------
DRF's default throttles use REMOTE_ADDR unless NUM_PROXIES is configured.
In hospital deployments behind a load balancer, REMOTE_ADDR is often the
balancer's IP, causing whole-site false positives.

This module provides drop-in throttles that use icea_core.net.get_client_ip()
when ICEA_TRUST_PROXY_HEADERS=true and the peer is a trusted proxy.

Graceful degradation
--------------------
If ICEA_TRUST_PROXY_HEADERS is false (default), behavior matches DRF.
"""

from __future__ import annotations

from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle

from icea_core.net import get_client_ip


class IceaAnonRateThrottle(AnonRateThrottle):
    """Anon throttle that can honor X-Forwarded-For securely."""

    def get_ident(self, request):  # type: ignore[override]
        return get_client_ip(request)


class IceaUserRateThrottle(UserRateThrottle):
    """User throttle that can honor X-Forwarded-For securely (for anon fallback)."""

    def get_ident(self, request):  # type: ignore[override]
        return get_client_ip(request)


class IceaScopedRateThrottle(ScopedRateThrottle):
    """Scoped throttle that can honor X-Forwarded-For securely."""

    def get_ident(self, request):  # type: ignore[override]
        return get_client_ip(request)
