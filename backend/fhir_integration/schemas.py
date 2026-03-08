from __future__ import annotations

import importlib
import os
from datetime import datetime
from typing import Any

from dateutil import parser as dateparser
from pydantic import BaseModel, ConfigDict, Field, ValidationError


def _bool_env(name: str, default: str = "false") -> bool:
    return str(os.environ.get(name, default)).lower() in {"1", "true", "yes"}


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return dateparser.parse(s)
    except Exception:
        return None


class FHIRMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    lastUpdated: str | None = None

    def last_updated_dt(self) -> datetime | None:
        return _parse_dt(self.lastUpdated)


class FHIRResource(BaseModel):
    """Minimal FHIR resource validation.

    This intentionally validates only the invariants ICEA needs:
      - resourceType must exist
      - id should exist for persisted resources

    All other fields are allowed (extra="allow") to support vendor variability.
    """

    model_config = ConfigDict(extra="allow")

    resourceType: str
    id: str | None = None
    meta: FHIRMeta | None = None


class BundleEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    resource: dict[str, Any] = Field(default_factory=dict)


class Bundle(BaseModel):
    model_config = ConfigDict(extra="allow")

    resourceType: str
    entry: list[BundleEntry] = Field(default_factory=list)
    link: list[dict[str, Any]] = Field(default_factory=list)


def _strict_validate_fhir_resources(payload: dict[str, Any], *, required_profiles: list[str]) -> list[dict[str, Any]]:
    """Strict validator using `fhir.resources` (optional dependency).

    Notes:
      - Validates against FHIR R4 base schema (cardinality + types) for the given resourceType.
      - "IPS/HL7 IG" profile enforcement here is implemented as:
          meta.profile must contain each URL provided via `required_profiles`.
        Full IG constraint validation (beyond meta.profile) is intentionally out-of-scope for the MVP.
    """

    issues: list[dict[str, Any]] = []
    rt = str(payload.get("resourceType") or "").strip()
    if not rt:
        return [{"loc": ["resourceType"], "msg": "Missing resourceType", "type": "value_error", "severity": "error", "layer": "strict"}]

    # Dynamic import: fhir.resources.<lowercase>
    # Examples: Observation -> fhir.resources.observation.Observation
    #           PractitionerRole -> fhir.resources.practitionerrole.PractitionerRole
    mod_name = f"fhir.resources.{rt.lower()}"

    mod = importlib.import_module(mod_name)
    cls = getattr(mod, rt)

    # Pydantic v2 model validation
    if hasattr(cls, "model_validate"):
        cls.model_validate(payload)
    else:
        # Fallback for older releases
        cls.parse_obj(payload)  # type: ignore[attr-defined]

    if required_profiles:
        profiles = ((payload.get("meta") or {}).get("profile") or [])
        profiles = [str(p) for p in profiles if p]
        missing = [p for p in required_profiles if p not in profiles]
        if missing:
            issues.append(
                {
                    "loc": ["meta", "profile"],
                    "msg": f"Missing required profile(s): {', '.join(missing)}",
                    "type": "value_error.profile",
                    "severity": "error",
                    "layer": "strict",
                }
            )

    return issues


def validate_resource(
    payload: dict[str, Any],
    expected_type: str | None = None,
    *,
    strict: bool | None = None,
    required_profiles: list[str] | None = None,
    fail_closed: bool | None = None,
) -> tuple[bool, list[dict[str, Any]], datetime | None]:
    """Validate a resource dict; returns (ok, issues, meta_lastUpdated_dt).

    - `strict` enables optional `fhir.resources` validation (if installed).
    - ok is true if there are **no error-severity issues**.
    """

    issues: list[dict[str, Any]] = []

    strict_mode = _bool_env("FHIR_STRICT_VALIDATION") if strict is None else bool(strict)
    req_profiles = (
        [p.strip() for p in os.environ.get("FHIR_REQUIRED_PROFILES", "").split(",") if p.strip()]
        if required_profiles is None
        else list(required_profiles)
    )
    fail_closed = _bool_env("FHIR_STRICT_FAIL_CLOSED", "false") if fail_closed is None else bool(fail_closed)

    try:
        res = FHIRResource.model_validate(payload)
        if expected_type and res.resourceType != expected_type:
            issues.append(
                {
                    "loc": ["resourceType"],
                    "msg": f"Expected '{expected_type}' got '{res.resourceType}'",
                    "type": "value_error.resourceType",
                    "severity": "error",
                    "layer": "basic",
                }
            )
        if not res.id and res.resourceType not in {"Bundle"}:
            issues.append(
                {"loc": ["id"], "msg": "Missing 'id'", "type": "value_error.missing", "severity": "error", "layer": "basic"}
            )

        # Optional strict validation (enterprise)
        if strict_mode and res.resourceType not in {"Bundle"}:
            try:
                strict_issues = _strict_validate_fhir_resources(payload, required_profiles=req_profiles)
                issues.extend(strict_issues)
            except Exception as e:
                # Graceful degradation: if strict deps are missing, do NOT break MVP by default.
                issues.append(
                    {
                        "loc": ["_strict"],
                        "msg": f"Strict validation unavailable: {e.__class__.__name__}: {str(e)}",
                        "type": "warning.strict_unavailable",
                        "severity": "error" if fail_closed else "warning",
                        "layer": "strict",
                    }
                )

        last = res.meta.last_updated_dt() if res.meta else None
        ok = not any(i.get("severity") == "error" for i in issues)
        return ok, issues, last

    except ValidationError as e:
        for err in e.errors():
            issues.append(
                {
                    "loc": list(err.get("loc", [])),
                    "msg": err.get("msg", "validation error"),
                    "type": err.get("type", ""),
                    "severity": "error",
                    "layer": "basic",
                }
            )
        return False, issues, None


def validate_bundle(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate a bundle; returns (resources, bundle_issues)."""

    issues: list[dict[str, Any]] = []
    try:
        b = Bundle.model_validate(payload)
        if b.resourceType != "Bundle":
            issues.append({"loc": ["resourceType"], "msg": "Not a Bundle", "type": "value_error.bundle", "severity": "error", "layer": "basic"})
            return [], issues
        resources = [e.resource for e in b.entry if isinstance(e.resource, dict) and e.resource]
        return resources, issues
    except ValidationError as e:
        for err in e.errors():
            issues.append(
                {
                    "loc": list(err.get("loc", [])),
                    "msg": err.get("msg", "validation error"),
                    "type": err.get("type", ""),
                    "severity": "error",
                    "layer": "basic",
                }
            )
        return [], issues


def get_bundle_next_url(bundle: dict[str, Any]) -> str | None:
    links = bundle.get("link") or []
    for l in links:
        if (l or {}).get("relation") == "next" and (l or {}).get("url"):
            return str(l.get("url"))
    return None
