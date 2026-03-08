from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


@lru_cache(maxsize=1)
def load_nnn_mappings() -> dict[str, dict[str, str]]:
    """Load optional NNN->(SNOMED/LOINC) mappings.

    Configuration:
      - NNN_MAPPING_PATH: path to a JSON file.
      - NNN_MAPPING_JSON: inline JSON string (useful in container envs).

    Expected JSON shape:
    {
      "nanda_to_snomed": {"<NANDA_CODE>": "<SNOMED_CODE>", ...},
      "nic_to_snomed": {"<NIC_CODE>": "<SNOMED_CODE>", ...},
      "noc_to_loinc": {"<NOC_CODE>": "<LOINC_CODE>", ...}
    }
    """

    inline = os.environ.get("NNN_MAPPING_JSON")
    path = os.environ.get("NNN_MAPPING_PATH")

    raw: Any = {}
    try:
        if inline:
            raw = json.loads(inline)
        elif path and os.path.exists(path):
            raw = json.loads(open(path, "r", encoding="utf-8").read())
    except Exception:
        raw = {}

    out: dict[str, dict[str, str]] = {
        "nanda_to_snomed": {},
        "nic_to_snomed": {},
        "noc_to_loinc": {},
    }

    if isinstance(raw, dict):
        for k in list(out.keys()):
            v = raw.get(k)
            if isinstance(v, dict):
                out[k] = {str(kk).strip(): str(vv).strip() for kk, vv in v.items() if kk and vv}

    return out


def map_condition_system_code(system: str, code: str) -> tuple[str, str] | None:
    """Map NANDA -> SNOMED CT if configured."""

    sys_l = _norm(system)
    if "nanda" not in sys_l:
        return None

    m = load_nnn_mappings().get("nanda_to_snomed", {})
    snomed = m.get(code.strip())
    if not snomed:
        return None

    return ("http://snomed.info/sct", snomed)


def map_procedure_system_code(system: str, code: str) -> tuple[str, str] | None:
    """Map NIC -> SNOMED CT if configured."""

    sys_l = _norm(system)
    if "nic" not in sys_l:
        return None

    m = load_nnn_mappings().get("nic_to_snomed", {})
    snomed = m.get(code.strip())
    if not snomed:
        return None

    return ("http://snomed.info/sct", snomed)


def map_observation_system_code(system: str, code: str) -> tuple[str, str] | None:
    """Map NOC -> LOINC if configured."""

    sys_l = _norm(system)
    if "noc" not in sys_l:
        return None

    m = load_nnn_mappings().get("noc_to_loinc", {})
    loinc = m.get(code.strip())
    if not loinc:
        return None

    return ("http://loinc.org", loinc)
