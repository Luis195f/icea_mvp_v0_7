from __future__ import annotations

import hashlib
from typing import Any, Tuple

import numpy as np
import pandas as pd

from icea_pipeline.target_trial import canonical_json
from icea_pipeline.quality_ops import build_quality_ops_playbook



# Minimal, open (non-licensed) semantic labels for audit reporting.
# NOTE: Do NOT ship licensed taxonomy content.
LOINC_DISPLAY = {
    "85556-9": "Rothman Index Calculated",
    "8867-4": "Heart rate",
    "8480-6": "Systolic blood pressure",
    "8462-4": "Diastolic blood pressure",
    "8478-0": "Mean blood pressure",
    "8310-5": "Body temperature",
    "59408-5": "Oxygen saturation in Arterial blood by Pulse oximetry",
    "9279-1": "Respiratory rate",
    "3150-0": "Inhaled oxygen concentration",
    "3151-8": "Oxygen flow rate",
    "9192-6": "Urine output",
    "6690-2": "Leukocytes [#/volume] in Blood",
    "718-7": "Hemoglobin [Mass/volume] in Blood",
    "4544-3": "Hematocrit [Volume Fraction] of Blood",
    "2951-2": "Sodium [Moles/volume] in Serum or Plasma",
    "2823-3": "Potassium [Moles/volume] in Serum or Plasma",
    "3094-0": "Urea nitrogen [Mass/volume] in Serum or Plasma",
    "2160-0": "Creatinine [Mass/volume] in Serum or Plasma",
    "2345-7": "Glucose [Mass/volume] in Serum or Plasma",
    "2075-0": "Chloride [Moles/volume] in Serum or Plasma",
    "2028-9": "Carbon dioxide, total [Moles/volume] in Serum or Plasma",
    "6768-6": "Platelets [#/volume] in Blood",
    "1751-7": "Albumin [Mass/volume] in Serum or Plasma",
    "9269-2": "Glasgow coma score total",
    "38226-6": "Braden scale total score",
    "41959-4": "Morse fall scale total score",
    "72514-3": "Pain severity - 0-10 verbal numeric rating",
}


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def stable_json(obj: Any) -> str:
    return canonical_json(obj)


def _as_bool_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    s = df[col]
    try:
        return s.astype(float) > 0.5
    except Exception:
        return s.astype(bool)


def _eval_expr(df: pd.DataFrame, expr: dict[str, Any]) -> Tuple[pd.Series, str | None]:
    """Evaluate a safe, structured eligibility expression over a dataframe.

    Supported forms (dict):
      - {field, op, value}
      - {field, op: "between", min, max}
      - {all: [expr, ...]} / {any: [...]} / {not: expr}
      - ops: ==, !=, >, >=, <, <=, in, not_in, between, exists, not_exists
    """
    if not isinstance(expr, dict):
        return pd.Series([True] * len(df), index=df.index), "expression_not_a_dict"

    if "all" in expr:
        masks = []
        for sub in expr.get("all") or []:
            m, err = _eval_expr(df, sub)
            if err:
                return pd.Series([True] * len(df), index=df.index), err
            masks.append(m)
        out = masks[0] if masks else pd.Series([True] * len(df), index=df.index)
        for m in masks[1:]:
            out = out & m
        return out, None

    if "any" in expr:
        masks = []
        for sub in expr.get("any") or []:
            m, err = _eval_expr(df, sub)
            if err:
                return pd.Series([True] * len(df), index=df.index), err
            masks.append(m)
        out = masks[0] if masks else pd.Series([False] * len(df), index=df.index)
        for m in masks[1:]:
            out = out | m
        return out, None

    if "not" in expr:
        m, err = _eval_expr(df, expr.get("not") or {})
        if err:
            return pd.Series([True] * len(df), index=df.index), err
        return ~m, None

    field = str(expr.get("field") or "").strip()
    if not field:
        return pd.Series([True] * len(df), index=df.index), "missing_field"

    if field not in df.columns:
        return pd.Series([True] * len(df), index=df.index), f"unknown_field:{field}"

    op = str(expr.get("op") or "==").strip().lower()
    s = df[field]

    def _num(x):
        return pd.to_numeric(x, errors="coerce")

    if op in {">", ">=", "<", "<="}:
        v = expr.get("value")
        sv = _num(s)
        try:
            fv = float(v)
        except Exception:
            return pd.Series([True] * len(df), index=df.index), f"bad_value:{field}"
        if op == ">":
            return sv > fv, None
        if op == ">=":
            return sv >= fv, None
        if op == "<":
            return sv < fv, None
        return sv <= fv, None

    if op in {"==", "!="}:
        v = expr.get("value")
        if op == "==":
            return s == v, None
        return s != v, None

    if op in {"in", "not_in"}:
        vals = expr.get("value")
        if not isinstance(vals, (list, tuple, set)):
            return pd.Series([True] * len(df), index=df.index), f"bad_value_list:{field}"
        mask = s.isin(list(vals))
        return (mask if op == "in" else ~mask), None

    if op == "between":
        try:
            mn = float(expr.get("min"))
            mx = float(expr.get("max"))
        except Exception:
            return pd.Series([True] * len(df), index=df.index), f"bad_between:{field}"
        sv = _num(s)
        return (sv >= mn) & (sv <= mx), None

    if op == "exists":
        return ~s.isna(), None
    if op == "not_exists":
        return s.isna(), None

    return pd.Series([True] * len(df), index=df.index), f"unsupported_op:{op}"


def apply_target_trial_eligibility(
    df: pd.DataFrame,
    *,
    target_trial: dict[str, Any] | None,
    apply: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply structured eligibility filters (best effort) and produce an audit trail."""
    audit: dict[str, Any] = {
        "applied": False,
        "criteria": [],
        # v0.5.5: cohort-flow by stages (EMA/RWD audit)
        # Each stage records retention before/after applying the rule.
        "stages": [],
        "excluded": 0,
        "n_before": int(len(df)),
        "n_after": int(len(df)),
    }
    if not apply or not target_trial:
        return df, audit

    criteria = list((target_trial.get("eligibility") or []))
    if not criteria:
        return df, audit

    mask = pd.Series([True] * len(df), index=df.index)
    any_applied = False
    excluded_total = 0

    for i, crit in enumerate(criteria, start=1):
        desc = str((crit or {}).get("description") or "").strip()
        expr = (crit or {}).get("expression")
        entry = {"description": desc, "supported": False, "error": None, "excluded": 0}
        stage = {
            "stage": int(i),
            "description": desc,
            "supported": False,
            "error": None,
            "n_before": int(mask.sum()),
            "n_after": int(mask.sum()),
            "excluded": 0,
        }
        if isinstance(expr, dict):
            m, err = _eval_expr(df, expr)
            if err is None:
                any_applied = True
                entry["supported"] = True
                before = int(mask.sum())
                mask = mask & m
                after = int(mask.sum())
                entry["excluded"] = int(before - after)
                excluded_total += entry["excluded"]
                stage["supported"] = True
                stage["n_before"] = int(before)
                stage["n_after"] = int(after)
                stage["excluded"] = int(before - after)
            else:
                entry["error"] = err
                stage["error"] = err
        elif expr is None:
            entry["error"] = "no_expression"
            stage["error"] = "no_expression"
        else:
            # strings (FHIRPath/SQL) are recorded but not executed for safety.
            entry["error"] = "expression_string_not_executed"
            stage["error"] = "expression_string_not_executed"
        audit["criteria"].append(entry)
        audit["stages"].append(stage)

    if any_applied:
        df2 = df.loc[mask].copy()
        audit["applied"] = True
        audit["excluded"] = int(excluded_total)
        audit["n_after"] = int(len(df2))
        return df2, audit

    return df, audit


def compute_missingness_exclusions(
    df: pd.DataFrame,
    *,
    required_vars: list[str],
    outcome: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compute complete-case exclusions with semantic traceability via missing_loinc_* flags."""
    audit: dict[str, Any] = {
        "n_before": int(len(df)),
        "n_after": int(len(df)),
        "excluded": 0,
        "reasons": [],
        "semantic_reasons": [],
        # v0.6: granular Rothman-component forensic missingness (non-gating unless outcome missing)
        "component_semantic_reasons": [],
    }

    if len(df) == 0:
        return df, audit

    missing_masks: list[pd.Series] = []
    reasons: list[tuple[str, pd.Series]] = []

    # Generic missing flags for required vars
    for v in required_vars:
        col = f"missing_{v}"
        if col in df.columns:
            m = _as_bool_series(df, col)
            missing_masks.append(m)
            reasons.append((col, m))

    # Semantic missing flags via LOINC
    # IMPORTANT (v0.6): do NOT treat every missing_loinc_* feature as a complete-case exclusion.
    # We only gate on time-anchored missing flags (suffix _t0/_t1) which represent protocol-critical
    # measurements (e.g., RI at time-zero / follow-up). Non-anchored missing_loinc_* can be used as
    # covariates without forcing exclusion.
    loinc_cols = [c for c in df.columns if c.startswith("missing_loinc_") and (c.endswith("_t0") or c.endswith("_t1"))]
    for col in loinc_cols:
        m = _as_bool_series(df, col)
        missing_masks.append(m)
        reasons.append((col, m))

    # Special-case: delta_ri invalid if RI missing at t0 or t1 (encoded as missing_loinc_85556_9_t0/_t1)
    if outcome == "delta_ri":
        for col in ("missing_loinc_85556_9_t0", "missing_loinc_85556_9_t1"):
            if col in df.columns:
                m = _as_bool_series(df, col)
                missing_masks.append(m)
                reasons.append((col, m))

    if not missing_masks:
        return df, audit

    overall = missing_masks[0].copy()
    for m in missing_masks[1:]:
        overall = overall | m

    excluded_df = df.loc[overall].copy()
    kept_df = df.loc[~overall].copy()

    audit["excluded"] = int(len(excluded_df))
    audit["n_after"] = int(len(kept_df))

    reason_counts = []
    for name, m in reasons:
        cnt = int(m.loc[overall].sum())
        if cnt:
            reason_counts.append({"flag": name, "excluded": cnt})
    reason_counts.sort(key=lambda x: x["excluded"], reverse=True)
    audit["reasons"] = reason_counts[:50]

    sem = []
    for rc in reason_counts:
        flag = rc["flag"]
        if not flag.startswith("missing_loinc_"):
            continue
        rest = flag[len("missing_loinc_"):]
        parts = rest.split("_")
        where = ""
        if len(parts) >= 2 and parts[-1] in {"t0", "t1"}:
            where = parts[-1]
            code = "-".join(parts[:-1]).replace("_", "-")
        else:
            code = "-".join(parts).replace("_", "-")
        sem.append(
            {
                "system": "LOINC",
                "code": code,
                "display": LOINC_DISPLAY.get(code, ""),
                "where": where,
                "excluded": int(rc["excluded"]),
            }
        )
    audit["semantic_reasons"] = sem

    # v0.6: If outcome is missing due to RI gaps, report which component measurements are missing.
    # This helps operations teams separate "AI issue" from "documentation / device / workflow issue".
    if outcome == "delta_ri" and len(excluded_df) and "missing_delta_ri" in excluded_df.columns:
        try:
            excl_outcome = excluded_df[_as_bool_series(excluded_df, "missing_delta_ri")]
            comp_cols = [
                c
                for c in excl_outcome.columns
                if c.startswith("missing_loinc_")
                and (c.endswith("_t0") or c.endswith("_t1"))
                and ("85556_9" not in c)
            ]
            comp_counts = []
            for col in comp_cols:
                cnt = int(_as_bool_series(excl_outcome, col).sum())
                if cnt:
                    comp_counts.append({"flag": col, "excluded": cnt})
            comp_counts.sort(key=lambda x: x["excluded"], reverse=True)
            comp_sem = []
            for rc in comp_counts[:60]:
                flag = rc["flag"]
                rest = flag[len("missing_loinc_"):]
                parts = rest.split("_")
                where = ""
                if len(parts) >= 2 and parts[-1] in {"t0", "t1"}:
                    where = parts[-1]
                    code = "-".join(parts[:-1]).replace("_", "-")
                else:
                    code = "-".join(parts).replace("_", "-")
                comp_sem.append(
                    {
                        "system": "LOINC",
                        "code": code,
                        "display": LOINC_DISPLAY.get(code, ""),
                        "where": where,
                        "excluded": int(rc["excluded"]),
                    }
                )
            audit["component_semantic_reasons"] = comp_sem
        except Exception:
            pass

    return kept_df, audit


def generate_trial_protocol_report(
    *,
    df: pd.DataFrame,
    spec: dict[str, Any],
    treatment: str,
    outcome: str,
    confounders: list[str],
    effect_modifiers: list[str],
    causal_summary: dict[str, Any],
) -> dict[str, Any]:
    """Generate a CONSORT-emulated Trial Protocol Report (paper-grade audit artifact)."""
    tt = spec.get("target_trial") if isinstance(spec.get("target_trial"), dict) else None

    apply_elig = spec.get("apply_eligibility")
    if apply_elig is None:
        has_expr = False
        if tt:
            for c in (tt.get("eligibility") or []):
                if isinstance((c or {}).get("expression"), dict):
                    has_expr = True
                    break
        apply_elig = bool(has_expr)

    df0 = df.copy()
    n_assessed = int(len(df0))

    df1, elig_audit = apply_target_trial_eligibility(df0, target_trial=tt, apply=bool(apply_elig))

    required = [treatment, outcome] + list(confounders or []) + list(effect_modifiers or [])
    df2, miss_audit = compute_missingness_exclusions(df1, required_vars=required, outcome=outcome)

    flow = {
        "assessed_for_eligibility": n_assessed,
        "eligible_after_filters": int(len(df1)),
        "excluded_eligibility": int(n_assessed - len(df1)),
        "excluded_missingness": int(len(df1) - len(df2)),
        "included_in_analysis": int(len(df2)),
        # v0.5.5: explicit staged cohort-flow for EMA/RWD audits.
        "eligibility_stages": list((elig_audit or {}).get("stages") or []),
        "eligibility_audit": elig_audit,
        "missingness_audit": miss_audit,
    }

    closing = {
        "ate": causal_summary.get("ate"),
        "ate_ci": causal_summary.get("ate_ci"),
        "e_value": ((causal_summary.get("sensitivity") or {}).get("e_value") or {}),
        "placebo_ate_on_ri_initial": causal_summary.get("placebo_ate_on_ri_initial"),
    }

    # v0.7: Quality Ops Playbook (certification-ready)
    unit_hint = None
    try:
        if "unit_id" in df.columns and len(df["unit_id"].dropna()):
            unit_hint = str(df["unit_id"].dropna().mode().iloc[0])
        elif "unit" in df.columns and len(df["unit"].dropna()):
            unit_hint = str(df["unit"].dropna().mode().iloc[0])
    except Exception:
        unit_hint = None

    quality_ops = build_quality_ops_playbook(
        consort_flow=flow,
        missingness_audit=miss_audit,
        semantic_missingness=miss_audit.get("semantic_reasons") or [],
        semantic_components=miss_audit.get("component_semantic_reasons") or [],
        unit_hint=unit_hint,
    )

    core: dict[str, Any] = {

        "version": "v0.7.0",
        "protocol_hash": (spec.get("protocol_hash") or ""),
        "dataset_grain": (spec.get("grain") or spec.get("dataset_grain") or "episode"),
        "target_trial": tt,
        "treatment": treatment,
        "outcome": outcome,
        "confounders": confounders,
        "effect_modifiers": effect_modifiers,
        "consort_flow": flow,
        "semantic_missingness": miss_audit.get("semantic_reasons") or [],
        # v0.6: component-level missingness attribution (forensic, not proprietary RI reconstruction)
        "semantic_missingness_components": miss_audit.get("component_semantic_reasons") or [],
        "quality_ops_playbook": quality_ops,
        "closing_metrics": closing,
        # v0.5.5: optional sections attached from the causal layer (best effort)
        # These are pure-JSON artifacts; rendering to PDF/Word belongs to the UI layer.
        "policy_learning": (causal_summary.get("policy_learning") or {}),
        "fairness_audit": (causal_summary.get("fairness_audit") or {}),
        # Human-in-loop decisions are stored in GovernanceDecision and attached at read-time.
        "human_in_loop": {
            "status": "unreviewed",
            "human_override_flag": False,
            "decisions": [],
        },
    }

    core["report_hash"] = _sha256_hex(stable_json(core))
    core["report_hash_alg"] = "sha256"
    core["report_hash_input"] = "canonical_json_sorted"
    return core
