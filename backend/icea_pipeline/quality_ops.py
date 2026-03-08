from __future__ import annotations

"""Quality Ops Playbook (certification-ready).

This module turns *passive* audit signals (missingness, cohort contractions) into
*active* operational mitigations, aligned with quality-system expectations
(e.g., ISO 13485) and high-risk AI governance.

Design constraints:
  - Pure JSON output (rendering belongs to UI layer).
  - Best-effort and backward compatible: never blocks the pipeline.
  - No licensed content shipped (only minimal, open labels).
"""

from typing import Any


_VITALS = {
    "8867-4",  # HR
    "8480-6",  # SBP
    "8462-4",  # DBP
    "8478-0",  # MAP
    "8310-5",  # Temp
    "59408-5",  # SpO2
    "9279-1",  # RR
    "3150-0",  # FiO2
    "3151-8",  # O2 flow
    "9192-6",  # urine output
}

_ASSESSMENTS = {
    "9269-2",  # GCS
    "38226-6",  # Braden
    "41959-4",  # Morse
    "72514-3",  # Pain
}

_LABS = {
    "6690-2",
    "718-7",
    "4544-3",
    "2951-2",
    "2823-3",
    "3094-0",
    "2160-0",
    "2345-7",
    "2075-0",
    "2028-9",
    "6768-6",
    "1751-7",
}


def _severity_from_excluded(excluded: int, n_before: int) -> str:
    if n_before <= 0:
        return "low"
    frac = excluded / float(n_before)
    if excluded >= 50 or frac >= 0.25:
        return "critical"
    if excluded >= 10 or frac >= 0.10:
        return "high"
    if excluded >= 3 or frac >= 0.03:
        return "medium"
    return "low"


def _default_action_for_loinc(code: str) -> tuple[str, str]:
    """Return (action_recommended, owner_role) for a given LOINC code."""
    if code == "85556-9":
        return (
            "Revisar la cadena completa de cálculo/captura del índice de agudeza (RI) en t0/t1: "
            "integración EHR, mapeo LOINC, y consistencia temporal por turno.",
            "Clinical Informatics / Data Steward",
        )
    if code in _ASSESSMENTS:
        return (
            "Reforzar documentación enfermera del instrumento/escala en el workflow del turno "
            "(formularios, obligatoriedad, recordatorios y auditoría en unidad).",
            "Nurse Supervisor / Quality Lead",
        )
    if code in _VITALS:
        return (
            "Auditar integración IoMT/monitorización: conectividad, dispositivos, y mapeo de constantes "
            "vitales a LOINC (latencia y pérdida de datos).",
            "Biomedical Engineering / Clinical Informatics",
        )
    if code in _LABS:
        return (
            "Auditar integración LIS/laboratorio: feed de resultados, mapeo de analitos a LOINC y tiempos "
            "de disponibilidad (t0/t1).",
            "Lab IT / Clinical Informatics",
        )
    return (
        "Ejecutar revisión operativa del dato faltante: validar captura en origen, mapeo semántico y "
        "entrenamiento del equipo en el punto de cuidado.",
        "Quality Ops",
    )


def build_quality_ops_playbook(
    *,
    consort_flow: dict[str, Any],
    missingness_audit: dict[str, Any],
    semantic_missingness: list[dict[str, Any]],
    semantic_components: list[dict[str, Any]],
    unit_hint: str | None = None,
) -> dict[str, Any]:
    """Build an operational playbook from audit artifacts."""

    n_before = int((missingness_audit or {}).get("n_before") or consort_flow.get("eligible_after_filters") or 0)

    issues: list[dict[str, Any]] = []

    # Prefer component-level attribution when present (more actionable), then fall back.
    candidates = list(semantic_components or [])
    if not candidates:
        candidates = list(semantic_missingness or [])

    for item in candidates[:25]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        excluded = int(item.get("excluded") or 0)
        if excluded <= 0:
            continue
        where = str(item.get("where") or "").strip()
        display = str(item.get("display") or "").strip()
        action, owner = _default_action_for_loinc(code)
        sev = _severity_from_excluded(excluded, n_before)
        issues.append(
            {
                "stage": "missingness",
                "system": "LOINC",
                "code": code,
                "display": display,
                "where": where,
                "excluded": excluded,
                "severity": sev,
                "action_recommended": action,
                "owner_role": owner,
                "unit_hint": unit_hint or "",
                "evidence": {
                    "n_before": n_before,
                    "n_after": int((missingness_audit or {}).get("n_after") or 0),
                    "excluded_missingness_total": int((missingness_audit or {}).get("excluded") or 0),
                },
            }
        )

    # Eligibility stages: if any rule failed due to unsupported expression, report an action.
    stages = list((consort_flow or {}).get("eligibility_stages") or [])
    for st in stages:
        if not isinstance(st, dict):
            continue
        err = str(st.get("error") or "").strip()
        if not err:
            continue
        excluded = int(st.get("excluded") or 0)
        issues.append(
            {
                "stage": f"eligibility_stage_{int(st.get('stage') or 0)}",
                "system": "protocol",
                "code": "eligibility_expression",
                "display": str(st.get("description") or "").strip(),
                "where": "",
                "excluded": excluded,
                "severity": "medium",
                "action_recommended": "Convertir la regla a expresión estructurada (dict) para ejecución segura, "
                "o materializarla upstream en el ETL (FHIR Search/SQL) para garantizar reproducibilidad.",
                "owner_role": "Data Engineer / Epidemiology",
                "unit_hint": unit_hint or "",
                "evidence": {
                    "error": err,
                    "n_before": int(st.get("n_before") or 0),
                    "n_after": int(st.get("n_after") or 0),
                },
            }
        )

    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda x: (sev_rank.get(str(x.get("severity") or "low"), 9), -int(x.get("excluded") or 0)))

    return {
        "available": True,
        "unit_hint": unit_hint or "",
        "issues": issues,
        "summary": {
            "n_issues": int(len(issues)),
            "n_critical": int(sum(1 for i in issues if i.get("severity") == "critical")),
            "n_high": int(sum(1 for i in issues if i.get("severity") == "high")),
        },
        "notes": [
            "Pure-JSON playbook: renderización documental pertenece a la capa UI.",
            "Best-effort: no bloquea el pipeline; orientado a mitigación activa (closed-loop).",
        ],
    }
