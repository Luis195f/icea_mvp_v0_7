from __future__ import annotations

import importlib
import os
import re
from datetime import datetime
from typing import Any

from dateutil import parser as dateparser
from pydantic import BaseModel, ConfigDict, Field, ValidationError


FHIR_ID_RE = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
REFERENCE_RE = re.compile(
    r"^(Patient|Encounter|Observation|Condition|Procedure|Practitioner|PractitionerRole|RiskAssessment)/[A-Za-z0-9\-.]{1,64}$"
)
SUPPORTED_LOCAL_RESOURCE_TYPES = {
    "Bundle",
    "Patient",
    "Encounter",
    "Observation",
    "Condition",
    "Procedure",
    "Practitioner",
    "PractitionerRole",
    "RiskAssessment",
}


def _bool_env(name: str, default: str = "false") -> bool:
    return str(os.environ.get(name, default)).lower() in {"1", "true", "yes"}


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return dateparser.parse(s)
    except Exception:
        return None


def _issue(loc: list[Any], msg: str, type_: str, *, severity: str = "error", layer: str = "basic") -> dict[str, Any]:
    return {"loc": loc, "msg": msg, "type": type_, "severity": severity, "layer": layer}


def _secure_references_required() -> bool:
    return _bool_env("ICEA_SECURE_MODE") or _bool_env("FHIR_REQUIRE_SECURE_REFERENCES")


def _iter_references(value: Any, path: list[Any] | None = None):
    path = path or []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = [*path, key]
            if key == "reference" and isinstance(child, str):
                yield child_path, child
            else:
                yield from _iter_references(child, child_path)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _iter_references(child, [*path, idx])


def _validate_fhir_id(resource_id: str | None, loc: list[Any]) -> list[dict[str, Any]]:
    if resource_id and not FHIR_ID_RE.match(str(resource_id)):
        return [_issue(loc, "FHIR id is malformed or too long", "value_error.fhir_id")]
    return []


def _validate_reference(reference: str, loc: list[Any], *, secure_mode: bool) -> list[dict[str, Any]]:
    ref = str(reference or "").strip()
    if not ref:
        return []
    if ref.startswith(("http://", "https://")) or ".." in ref:
        return [_issue(loc, "FHIR reference is not an allowed local relative reference", "value_error.fhir_reference")]
    if ref.startswith(("urn:uuid:", "urn:oid:", "#")):
        return []
    if REFERENCE_RE.match(ref):
        return []
    if secure_mode or "/" not in ref:
        return [_issue(loc, "FHIR reference is unsafe or uses a raw identifier", "value_error.fhir_reference")]
    return []


def _validate_local_resource_invariants(payload: dict[str, Any], *, secure_mode: bool) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    rt = str(payload.get("resourceType") or "").strip()
    if not rt:
        return [_issue(["resourceType"], "Missing resourceType", "value_error.missing")]
    if rt not in SUPPORTED_LOCAL_RESOURCE_TYPES:
        issues.append(_issue(["resourceType"], "Unsupported resourceType for local demo validation", "value_error.resourceType"))

    issues.extend(_validate_fhir_id(payload.get("id"), ["id"]))
    for loc, reference in _iter_references(payload):
        issues.extend(_validate_reference(reference, loc, secure_mode=secure_mode))

    if rt == "Observation":
        code = payload.get("code") or {}
        if not isinstance(code, dict):
            issues.append(_issue(["code"], "Observation.code must be an object for local validation", "value_error.code"))
        else:
            coding = code.get("coding")
            if not isinstance(coding, list) or not coding:
                issues.append(_issue(["code", "coding"], "Observation requires code.coding for local validation", "value_error.missing"))
    elif rt in {"Condition", "Procedure"}:
        code = payload.get("code") or {}
        if not isinstance(code, dict) or not (code.get("coding") or code.get("text")):
            issues.append(_issue(["code"], f"{rt} requires a coded or textual code", "value_error.missing"))
    elif rt == "RiskAssessment":
        text_parts: list[str] = []
        for note in payload.get("note") or []:
            if isinstance(note, dict):
                text_parts.append(str(note.get("text") or ""))
        text_parts.append(str((payload.get("text") or {}).get("div") or ""))
        mentions_shadow_only = "shadow-only" in " ".join(text_parts).lower()
        prediction = payload.get("prediction") or []
        has_individual_probability = any(
            isinstance(item, dict) and any(k in item for k in ("probabilityDecimal", "probabilityRange", "qualitativeRisk"))
            for item in prediction
        )
        if not mentions_shadow_only or has_individual_probability:
            issues.append(
                _issue(
                    ["RiskAssessment"],
                    "RiskAssessment must remain shadow-only and must not expose individual risk probability",
                    "value_error.shadow_only",
                )
            )

    return issues


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
        return [_issue(["resourceType"], "Missing resourceType", "value_error", layer="strict")]

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
                _issue(
                    ["meta", "profile"],
                    f"Missing required profile(s): {', '.join(missing)}",
                    "value_error.profile",
                    layer="strict",
                )
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
    secure_mode = _secure_references_required()

    try:
        res = FHIRResource.model_validate(payload)
        if expected_type and res.resourceType != expected_type:
            issues.append(
                _issue(["resourceType"], f"Expected '{expected_type}' got '{res.resourceType}'", "value_error.resourceType")
            )
        if not res.id and res.resourceType not in {"Bundle"}:
            issues.append(_issue(["id"], "Missing 'id'", "value_error.missing"))
        issues.extend(_validate_local_resource_invariants(payload, secure_mode=secure_mode))

        # Optional strict validation (enterprise)
        if strict_mode and res.resourceType not in {"Bundle"}:
            try:
                strict_issues = _strict_validate_fhir_resources(payload, required_profiles=req_profiles)
                issues.extend(strict_issues)
            except Exception as e:
                # Graceful degradation: if strict deps are missing, do NOT break MVP by default.
                issues.append(
                    _issue(
                        ["_strict"],
                        f"Strict validation unavailable: {e.__class__.__name__}",
                        "warning.strict_unavailable",
                        severity="error" if fail_closed else "warning",
                        layer="strict",
                    )
                )

        last = res.meta.last_updated_dt() if res.meta else None
        ok = not any(i.get("severity") == "error" for i in issues)
        return ok, issues, last

    except ValidationError as e:
        for err in e.errors():
            issues.append(
                _issue(list(err.get("loc", [])), err.get("msg", "validation error"), err.get("type", ""))
            )
        return False, issues, None


def validate_bundle(
    payload: dict[str, Any],
    *,
    require_encounter_context: bool = False,
    secure_mode: bool | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate a bundle; returns (resources, bundle_issues)."""

    issues: list[dict[str, Any]] = []
    secure_refs = _secure_references_required() if secure_mode is None else bool(secure_mode)
    try:
        b = Bundle.model_validate(payload)
        if b.resourceType != "Bundle":
            issues.append(_issue(["resourceType"], "Not a Bundle", "value_error.bundle"))
            return [], issues
        resources: list[dict[str, Any]] = []
        has_encounter = False
        for idx, entry in enumerate(b.entry):
            if not isinstance(entry.resource, dict) or not entry.resource:
                issues.append(_issue(["entry", idx, "resource"], "Bundle.entry.resource is required", "value_error.missing"))
                continue
            resource = entry.resource
            rt = str(resource.get("resourceType") or "").strip()
            if rt == "Encounter":
                has_encounter = True
            local_issues = _validate_local_resource_invariants(resource, secure_mode=secure_refs)
            for local_issue in local_issues:
                issues.append({**local_issue, "loc": ["entry", idx, "resource", *local_issue.get("loc", [])]})
            if require_encounter_context and rt in {"Observation", "Condition", "Procedure", "RiskAssessment"}:
                encounter_ref = ((resource.get("encounter") or {}).get("reference") or "").strip()
                if not encounter_ref:
                    issues.append(_issue(["entry", idx, "resource", "encounter"], "Encounter-centered flow requires encounter.reference", "value_error.missing"))
            resources.append(resource)
        if require_encounter_context and not has_encounter:
            issues.append(_issue(["entry"], "Encounter-centered flow requires an Encounter resource", "value_error.missing"))
        return resources, issues
    except ValidationError as e:
        for err in e.errors():
            issues.append(
                _issue(list(err.get("loc", [])), err.get("msg", "validation error"), err.get("type", ""))
            )
        return [], issues


def get_bundle_next_url(bundle: dict[str, Any]) -> str | None:
    links = bundle.get("link") or []
    for l in links:
        if (l or {}).get("relation") == "next" and (l or {}).get("url"):
            return str(l.get("url"))
    return None
