from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from dateutil import parser as dateparser

from terminology.mappings import (
    map_condition_system_code,
    map_observation_system_code,
    map_procedure_system_code,
)


def _get_first_coding(codeable: dict[str, Any] | None) -> dict[str, Any] | None:
    if not codeable:
        return None
    coding = codeable.get("coding") or []
    if not coding:
        return None
    return coding[0] or None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return dateparser.parse(s)
    except Exception:
        return None


def normalize_observation(resource: dict[str, Any]) -> dict[str, Any]:
    coding = _get_first_coding(resource.get("code"))
    system = (coding or {}).get("system", "")
    code = (coding or {}).get("code", "")
    display = (coding or {}).get("display", "")

    src_system, src_code, src_display = system, code, display
    mapped = map_observation_system_code(system, code)
    if mapped:
        system, code = mapped

    value_num = None
    value_text = ""
    unit = ""

    if "valueQuantity" in resource:
        q = resource.get("valueQuantity") or {}
        value_num = q.get("value")
        unit = q.get("unit", "")
    elif "valueString" in resource:
        value_text = resource.get("valueString") or ""
    elif "valueCodeableConcept" in resource:
        c2 = _get_first_coding(resource.get("valueCodeableConcept"))
        value_text = (c2 or {}).get("code", "")

    effective = resource.get("effectiveDateTime") or resource.get("effectiveInstant")

    return {
        "code_system": system,
        "code": code,
        "display": display,
        "source_code_system": src_system,
        "source_code": src_code,
        "source_display": src_display,
        "value_num": value_num,
        "value_text": value_text,
        "unit": unit,
        "effective_dt": _parse_dt(effective),
    }


def normalize_condition(resource: dict[str, Any]) -> dict[str, Any]:
    coding = _get_first_coding(resource.get("code"))
    system = (coding or {}).get("system", "")
    code = (coding or {}).get("code", "")
    display = (coding or {}).get("display", "")

    src_system, src_code, src_display = system, code, display
    mapped = map_condition_system_code(system, code)
    if mapped:
        system, code = mapped

    onset = resource.get("onsetDateTime")
    recorded = resource.get("recordedDate")

    clinical_status = ""
    cs = resource.get("clinicalStatus")
    cs_coding = _get_first_coding(cs)
    if cs_coding:
        clinical_status = cs_coding.get("code", "")

    return {
        "code_system": system,
        "code": code,
        "display": display,
        "source_code_system": src_system,
        "source_code": src_code,
        "source_display": src_display,
        "onset_dt": _parse_dt(onset),
        "recorded_dt": _parse_dt(recorded),
        "clinical_status": clinical_status,
    }


def normalize_procedure(resource: dict[str, Any]) -> dict[str, Any]:
    coding = _get_first_coding(resource.get("code"))
    system = (coding or {}).get("system", "")
    code = (coding or {}).get("code", "")
    display = (coding or {}).get("display", "")

    src_system, src_code, src_display = system, code, display
    mapped = map_procedure_system_code(system, code)
    if mapped:
        system, code = mapped

    performed = resource.get("performedDateTime")
    if not performed and isinstance(resource.get("performedPeriod"), dict):
        performed = (resource.get("performedPeriod") or {}).get("start")

    performers = resource.get("performer") or []
    performer_role = ""
    performer_actor_ref = ""
    performer_actor_type = ""

    is_nursing = False
    method = "unknown"

    def _kw(s: str) -> bool:
        s2 = (s or "").lower()
        return any(k in s2 for k in ["nurs", "registered nurse", "rn", "enfermer", "matrona", "auxiliar", "tcae", "nurse"])

    if performers:
        p0 = performers[0] or {}
        func = p0.get("function")
        func_coding = _get_first_coding(func)
        performer_role = (
            (func_coding or {}).get("code", "")
            or (func_coding or {}).get("display", "")
            or (p0.get("function") or {}).get("text", "")
        )

        actor = p0.get("actor") or {}
        performer_actor_ref = actor.get("reference", "") or ""
        if "/" in performer_actor_ref:
            performer_actor_type = performer_actor_ref.split("/", 1)[0]

        if _kw(performer_role):
            is_nursing = True
            method = "heuristic"

    resolver: Callable[[str, str], dict[str, Any] | None] | None = None
    if isinstance(resource.get("__resolver__"), dict) and callable(resource["__resolver__"].get("lookup")):
        resolver = resource["__resolver__"]["lookup"]

    if resolver and performer_actor_ref and "/" in performer_actor_ref:
        rtype, rid = performer_actor_ref.split("/", 1)
        actor_res = None
        try:
            actor_res = resolver(rtype, rid)
        except Exception:
            actor_res = None

        if actor_res:
            text_fields: list[str] = []
            if rtype == "PractitionerRole":
                for cc in (actor_res.get("code") or []):
                    c = _get_first_coding(cc)
                    if c:
                        text_fields += [c.get("code", ""), c.get("display", ""), c.get("system", "")]
                for cc in (actor_res.get("specialty") or []):
                    c = _get_first_coding(cc)
                    if c:
                        text_fields += [c.get("code", ""), c.get("display", ""), c.get("system", "")]
                text_fields.append(actor_res.get("text", "") or "")
            elif rtype == "Practitioner":
                for q in (actor_res.get("qualification") or []):
                    c = _get_first_coding((q or {}).get("code"))
                    if c:
                        text_fields += [c.get("code", ""), c.get("display", ""), c.get("system", "")]

            if any(_kw(t) for t in text_fields if t):
                is_nursing = True
                method = "deterministic"

    return {
        "code_system": system,
        "code": code,
        "display": display,
        "source_code_system": src_system,
        "source_code": src_code,
        "source_display": src_display,
        "performed_dt": _parse_dt(performed),
        "performer_role": performer_role,
        "performer_actor_ref": performer_actor_ref,
        "performer_actor_type": performer_actor_type,
        "is_nursing": bool(is_nursing),
        "nursing_label_method": method,
    }
