from __future__ import annotations

import os
from typing import Any

import requests


class FHIRClient:
    """Minimal FHIR REST client.

    MVP goals:
      - Provide a predictable interface for pulling EHR context into the ICEA pipeline.
      - Keep auth and base URL configurable.

    Production notes:
      - Replace with SMART-on-FHIR OAuth2 in real deployments.
      - Add retry/backoff + caching.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None, timeout_s: float = 10.0):
        self.base_url = (base_url or os.environ.get("FHIR_BASE_URL") or "").rstrip("/")
        if not self.base_url:
            raise ValueError("FHIR_BASE_URL is not configured")
        self.token = token or os.environ.get("FHIR_BEARER_TOKEN")
        self.timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/fhir+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        r = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        r = requests.post(
            url,
            headers={**self._headers(), "Content-Type": "application/fhir+json"},
            json=payload,
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        return r.json()

    def read(self, resource_type: str, resource_id: str) -> dict[str, Any]:
        return self.get(f"{resource_type}/{resource_id}")

    def search(self, resource_type: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.get(resource_type, params=params)

    def fetch_observations(self, patient_id: str) -> dict[str, Any]:
        return self.search("Observation", params={"patient": patient_id})

    def fetch_conditions(self, patient_id: str) -> dict[str, Any]:
        return self.search("Condition", params={"patient": patient_id})

    def fetch_procedures(self, patient_id: str) -> dict[str, Any]:
        return self.search("Procedure", params={"patient": patient_id})

    def fetch_encounter(self, encounter_id: str) -> dict[str, Any]:
        return self.read("Encounter", encounter_id)

    def fetch_by_encounter(self, resource_type: str, encounter_id: str) -> dict[str, Any]:
        return self.search(resource_type, params={"encounter": encounter_id})
