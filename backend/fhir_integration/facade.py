from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from fhir_integration.schemas import get_bundle_next_url, validate_bundle, validate_resource
from fhir_integration.service import FHIRClient


@dataclass
class ValidatedResource:
    resource_type: str
    resource_id: str
    payload: dict[str, Any]
    ok: bool
    issues: list[dict[str, Any]]
    last_updated: Any = None


class FHIRFacade:
    """FHIR Facade: resilient fetch + minimal schema validation.

    Goals (pilot-grade):
      - Be strict about invariants (resourceType, id, meta.lastUpdated)
      - Be tolerant about vendor extensions (extra keys)
      - Provide pagination + retry/backoff without changing endpoint contracts.
    """

    def __init__(
        self,
        client: FHIRClient | None = None,
        *,
        retries: int | None = None,
        backoff_s: float | None = None,
        strict: bool | None = None,
        required_profiles: list[str] | None = None,
        fail_closed: bool | None = None,
    ):
        self.client = client or FHIRClient()
        self.retries = int(retries or 2)
        self.backoff_s = float(backoff_s or 0.4)
        # Optional strict validation overrides (used for ENS Alto compliance).
        self._strict = strict
        self._required_profiles = required_profiles
        self._fail_closed = fail_closed

    def _retry(self, fn, *args, **kwargs):
        last_err: Exception | None = None
        for i in range(max(self.retries, 1)):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_err = e
                if i < self.retries - 1:
                    time.sleep(self.backoff_s * (2 ** i))
        if last_err:
            raise last_err
        raise RuntimeError("Unknown FHIR retry error")

    def read(self, resource_type: str, resource_id: str) -> ValidatedResource:
        payload = self._retry(self.client.read, resource_type, resource_id)
        ok, issues, last = validate_resource(
            payload,
            expected_type=resource_type,
            strict=self._strict,
            required_profiles=self._required_profiles,
            fail_closed=self._fail_closed,
        )
        rid = str(payload.get("id") or resource_id)
        return ValidatedResource(resource_type=resource_type, resource_id=rid, payload=payload, ok=ok, issues=issues, last_updated=last)

    def search_all(self, resource_type: str, params: dict[str, Any]) -> list[ValidatedResource]:
        """Fetch all pages of a FHIR search Bundle."""

        out: list[ValidatedResource] = []
        bundle = self._retry(self.client.search, resource_type, params)

        while True:
            resources, bundle_issues = validate_bundle(bundle)
            # Bundle-level issues are attached to a synthetic record
            if bundle_issues:
                out.append(
                    ValidatedResource(
                        resource_type="Bundle",
                        resource_id="",
                        payload=bundle,
                        ok=False,
                        issues=bundle_issues,
                        last_updated=None,
                    )
                )

            for res in resources:
                rt = str(res.get("resourceType") or resource_type)
                ok, issues, last = validate_resource(
                    res,
                    expected_type=rt,
                    strict=self._strict,
                    required_profiles=self._required_profiles,
                    fail_closed=self._fail_closed,
                )
                rid = str(res.get("id") or "")
                out.append(ValidatedResource(resource_type=rt, resource_id=rid, payload=res, ok=ok, issues=issues, last_updated=last))

            next_url = get_bundle_next_url(bundle)
            if not next_url:
                break
            # Follow next link (absolute) best-effort.
            try:
                r = self._retry(requests.get, next_url, headers=self.client._headers(), timeout=self.client.timeout_s)
                r.raise_for_status()
                bundle = r.json()
            except Exception:
                break

        return out
