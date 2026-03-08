"""Network helpers for Zero-Trust perimeters.

This module provides a *secure* client-IP extractor that can be used by DRF
throttling in proxy / load-balancer deployments.

Design goals
------------
- Graceful degradation: if proxy trust is disabled, fall back to REMOTE_ADDR.
- Secure-by-configuration: only trust X-Forwarded-For when the immediate peer
  (REMOTE_ADDR) is a trusted proxy.

Env vars
--------
- ICEA_TRUST_PROXY_HEADERS: false|true  (default false)
- ICEA_TRUSTED_PROXY_CIDRS: comma-separated CIDRs or IPs

Example:
  ICEA_TRUST_PROXY_HEADERS=true
  ICEA_TRUSTED_PROXY_CIDRS=10.0.0.0/8,192.168.0.0/16,127.0.0.1/32
"""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


@lru_cache(maxsize=1)
def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:  # type: ignore[name-defined]
    raw = (os.environ.get("ICEA_TRUSTED_PROXY_CIDRS") or "").strip()
    if not raw:
        return ()
    nets: list[ipaddress._BaseNetwork] = []  # type: ignore[name-defined]
    for item in [x.strip() for x in raw.split(",") if x.strip()]:
        try:
            if "/" in item:
                nets.append(ipaddress.ip_network(item, strict=False))
            else:
                # single IP => /32 or /128
                ip = ipaddress.ip_address(item)
                nets.append(ipaddress.ip_network(f"{ip}/{ip.max_prefixlen}", strict=False))
        except Exception:
            continue
    return tuple(nets)


def _is_trusted_proxy_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except Exception:
        return False
    for net in _trusted_proxy_networks():
        if addr in net:
            return True
    return False


def get_client_ip(request) -> str:
    """Return the best-effort client IP for security controls.

    Rules:
    - If ICEA_TRUST_PROXY_HEADERS is false => use REMOTE_ADDR.
    - If true => trust X-Forwarded-For only if REMOTE_ADDR is a trusted proxy.

    For a trusted proxy, we interpret X-Forwarded-For as a chain and return the
    rightmost non-proxy IP.
    """

    remote = (request.META.get("REMOTE_ADDR") or "").strip()
    if not remote:
        return "0.0.0.0"

    if not _truthy(os.environ.get("ICEA_TRUST_PROXY_HEADERS", "false")):
        return remote

    if not _is_trusted_proxy_ip(remote):
        # Do not trust forwarded headers from untrusted peers.
        return remote

    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if not xff:
        return remote

    chain = [ip.strip() for ip in xff.split(",") if ip.strip()]
    # Append the immediate peer so we can strip trusted proxies from the right.
    chain.append(remote)

    # Walk from right to left: drop trusted proxies, first non-trusted is client.
    for ip in reversed(chain):
        if _is_trusted_proxy_ip(ip):
            continue
        return ip

    # Worst case: all are trusted proxies => fall back to first element.
    return chain[0] if chain else remote
