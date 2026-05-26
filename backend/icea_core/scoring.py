from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from analytics.causal import ICEACausal
from icea_core.components import (
    ComponentBuildResult,
    combine_uncertainty_subcomponents,
    compute_conformal_burden,
    compute_low_support_burden,
    compute_missingness_burden,
    compute_ood_burden,
    compute_quality_raw,
    compute_relative_nursing_attribution,
    compute_severity_weight,
    infer_nurse_columns,
    infer_outcome_goal,
    outcome_sign,
    robust_z,
    utility_transform,
)
from icea_core.engine import ICEAEngine
from icea_core.formula import ICEAPlusComponentValue, ICEAPlusLineage, compute_row_score
from icea_core.models import ICEAPlusFormulaVersion, ModelArtifact
from icea_core.specs import build_default_icea_plus_spec, deep_merge_dict, formula_protocol_hash
from icea_pipeline.models import CausalRun, EpisodeFeatureRow, EpisodeWindowFeatureRow, NormalizedProcedure

FEATURE_CONTRACT_VERSION = "handover-icea-feature-v1"
FEATURE_SOURCE_REPO = "Luis195f/HANDOVER"
DEFAULT_MIN_FEATURE_COVERAGE = 0.95


@dataclass
class FormulaSelection:
    version: str
    spec: dict[str, Any]
    protocol_hash: str
    source: str
    record: ICEAPlusFormulaVersion | None = None


@dataclass
class LoadedDataset:
    selected_df: pd.DataFrame
    reference_df: pd.DataFrame
    meta_rows: list[dict[str, Any]]
    candidate_rows: int
    grain: str


@dataclass
class FeatureContractIssue:
    status: str
    row_id: str
    warnings: list[str]
    flags: dict[str, Any]
    coverage: float
    missing_features: list[str]
    missing_critical_features: list[str]
    expected_contract_version: str
    expected_source_repo: str
    expected_contract_versions: list[str] | None = None
    expected_source_repos: list[str] | None = None
    model_role: str = "primary"



def _safe_json_hash(obj: Any) -> str:
    dumped = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def _feature_contract_config(model_artifact: ModelArtifact) -> dict[str, Any]:
    raw = (model_artifact.metrics or {}).get("feature_contract")
    return dict(raw) if isinstance(raw, dict) else {}


def _contract_rows_present(rows: list[dict[str, Any]] | None) -> bool:
    return any(isinstance(row, dict) and "features" in row for row in rows or [])


def _external_feature_payload(row: dict[str, Any]) -> dict[str, Any]:
    features = row.get("features")
    return dict(features) if isinstance(features, dict) else dict(row)


def _normalize_external_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    normalized: list[dict[str, Any]] = []
    for idx, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        features = _external_feature_payload(row)
        missingness = row.get("missingness_flags") if isinstance(row.get("missingness_flags"), dict) else {}
        flat = dict(features)
        for key, value in missingness.items():
            flag_name = str(key) if str(key).startswith("missing_") else f"missing_{key}"
            flat[flag_name] = 1.0 if bool(value) else 0.0
        flat.update(
            {
                "row_id": str(row.get("row_id") or f"row:{idx}"),
                "episode_id": row.get("episode_id"),
                "window_id": row.get("window_id"),
                "patient_key": row.get("patient_key") or row.get("episode_id") or f"row:{idx}",
                "unit_id": row.get("unit_id"),
                "start_dt": row.get("start_dt") or row.get("clinical_timestamp"),
                "end_dt": row.get("end_dt") or row.get("clinical_timestamp"),
                "nurse_shares": dict(row.get("nurse_shares") or {}),
                "nurse_reliability": float(row.get("nurse_reliability") or 0.0),
            }
        )
        normalized.append(flat)
    return normalized


def _validate_external_feature_contract(
    *,
    rows: list[dict[str, Any]] | None,
    model_artifact: ModelArtifact,
    grain: str,
    model_role: str = "primary",
) -> list[FeatureContractIssue]:
    features = [str(feature) for feature in list(model_artifact.features or []) if str(feature)]
    if not rows:
        return []

    config = _feature_contract_config(model_artifact)
    required_features = [str(feature) for feature in config.get("required_features") or features]
    min_coverage = float(config.get("min_feature_coverage") or DEFAULT_MIN_FEATURE_COVERAGE)
    expected_contract_version = str(config.get("contract_version") or FEATURE_CONTRACT_VERSION)
    expected_source_repo = str(config.get("source_repo") or FEATURE_SOURCE_REPO)
    issues: list[FeatureContractIssue] = []

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(
                FeatureContractIssue(
                    status="contract_mismatch",
                    row_id=f"row:{idx}",
                    warnings=["row_not_object"],
                    flags={"contract_mismatch": True, "insufficient_evidence": True},
                    coverage=0.0,
                    missing_features=features,
                    missing_critical_features=required_features,
                    expected_contract_version=expected_contract_version,
                    expected_source_repo=expected_source_repo,
                    expected_contract_versions=[expected_contract_version],
                    expected_source_repos=[expected_source_repo],
                    model_role=model_role,
                )
            )
            continue

        row_id = str(row.get("row_id") or f"row:{idx}")
        features_payload = row.get("features")
        if not isinstance(features_payload, dict):
            features_payload = row
        provided = set(str(key) for key in features_payload.keys())
        expected = set(features)
        missing_features = sorted(expected - provided)
        present_count = len(expected & provided)
        coverage = float(present_count / len(expected)) if expected else 0.0
        missingness = row.get("missingness_flags") if isinstance(row.get("missingness_flags"), dict) else {}
        missing_critical = sorted(
            feature
            for feature in required_features
            if feature not in provided
            or features_payload.get(feature) is None
            or bool(missingness.get(feature))
            or bool(missingness.get(f"missing_{feature}"))
        )

        warnings: list[str] = []
        status = ""
        if _contract_rows_present(rows):
            if row.get("contract_version") != expected_contract_version:
                warnings.append("contract_version_mismatch")
                status = "contract_mismatch"
            if row.get("source_repo") != expected_source_repo:
                warnings.append("source_repo_mismatch")
                status = "contract_mismatch"
            if row.get("source_grain") != grain:
                warnings.append("grain_mismatch")
                status = "contract_mismatch"
            if not row.get("clinical_timestamp") or not row.get("recorded_timestamp"):
                warnings.append("temporal_context_missing")
                status = "contract_mismatch"
            if row.get("shadow_mode") is not True or row.get("non_individual_use") is not True:
                warnings.append("governance_flags_missing")
                status = "contract_mismatch"

        if missing_features and not status:
            if not missing_critical and coverage < min_coverage:
                warnings.append("low_feature_coverage")
                status = "low_feature_coverage"
            else:
                warnings.append("model_feature_space_mismatch")
                status = "contract_mismatch"
        if coverage < min_coverage and not status:
            warnings.append("low_feature_coverage")
            status = "low_feature_coverage"
        if missing_critical and not status:
            warnings.append("missing_critical_features")
            status = "insufficient_evidence"

        if status:
            issues.append(
                FeatureContractIssue(
                    status=status,
                    row_id=row_id,
                    warnings=sorted(set(warnings)),
                    flags={
                        "contract_mismatch": status == "contract_mismatch",
                        "low_feature_coverage": status == "low_feature_coverage",
                        "insufficient_evidence": True,
                        "missing_key_inputs": bool(missing_critical),
                    },
                    coverage=coverage,
                    missing_features=missing_features,
                    missing_critical_features=missing_critical,
                    expected_contract_version=expected_contract_version,
                    expected_source_repo=expected_source_repo,
                    expected_contract_versions=[expected_contract_version],
                    expected_source_repos=[expected_source_repo],
                    model_role=model_role,
                )
            )
    return issues


def _feature_contract_status_rank(status: str) -> int:
    return {
        "low_feature_coverage": 1,
        "insufficient_evidence": 2,
        "contract_mismatch": 3,
    }.get(status, 0)


def _merge_unique_preserving_order(values: list[str]) -> list[str]:
    merged: list[str] = []
    for value in values:
        if value and value not in merged:
            merged.append(value)
    return merged


def _merge_feature_contract_issues(issues: list[FeatureContractIssue]) -> list[FeatureContractIssue]:
    by_row: dict[str, FeatureContractIssue] = {}
    for issue in issues:
        current = by_row.get(issue.row_id)
        if current is None:
            by_row[issue.row_id] = issue
            continue

        status = current.status
        if _feature_contract_status_rank(issue.status) > _feature_contract_status_rank(current.status):
            status = issue.status
        warnings = sorted(set(current.warnings + issue.warnings))
        flags = {**current.flags}
        for key, value in issue.flags.items():
            flags[key] = bool(flags.get(key)) or bool(value)

        expected_contract_versions = _merge_unique_preserving_order(
            list(current.expected_contract_versions or [current.expected_contract_version])
            + list(issue.expected_contract_versions or [issue.expected_contract_version])
        )
        expected_source_repos = _merge_unique_preserving_order(
            list(current.expected_source_repos or [current.expected_source_repo])
            + list(issue.expected_source_repos or [issue.expected_source_repo])
        )
        model_roles = _merge_unique_preserving_order([current.model_role, issue.model_role])
        by_row[issue.row_id] = FeatureContractIssue(
            status=status,
            row_id=current.row_id,
            warnings=warnings,
            flags=flags,
            coverage=min(current.coverage, issue.coverage),
            missing_features=sorted(set(current.missing_features + issue.missing_features)),
            missing_critical_features=sorted(set(current.missing_critical_features + issue.missing_critical_features)),
            expected_contract_version=expected_contract_versions[0] if expected_contract_versions else FEATURE_CONTRACT_VERSION,
            expected_source_repo=expected_source_repos[0] if expected_source_repos else FEATURE_SOURCE_REPO,
            expected_contract_versions=expected_contract_versions,
            expected_source_repos=expected_source_repos,
            model_role=",".join(model_roles),
        )
    return list(by_row.values())


def _feature_contract_failure_result(
    *,
    formula: FormulaSelection,
    model_artifact: ModelArtifact,
    grain: str,
    issues: list[FeatureContractIssue],
) -> dict[str, Any]:
    rows = []
    for issue in issues:
        rows.append(
            {
                "row_id": issue.row_id,
                "grain": grain,
                "status": issue.status,
                "score": None,
                "raw_score": None,
                "components": {},
                "flags": issue.flags,
                "warnings": issue.warnings,
                "feature_contract": {
                    "contract_version": issue.expected_contract_version,
                    "source_repo": issue.expected_source_repo,
                    "expected_contract_version": issue.expected_contract_version,
                    "expected_source_repo": issue.expected_source_repo,
                    "expected_contract_versions": issue.expected_contract_versions or [issue.expected_contract_version],
                    "expected_source_repos": issue.expected_source_repos or [issue.expected_source_repo],
                    "feature_coverage": issue.coverage,
                    "missing_features": issue.missing_features,
                    "missing_critical_features": issue.missing_critical_features,
                    "validated_model_roles": issue.model_role.split(",") if issue.model_role else [],
                },
                "lineage": {
                    "formula_version": formula.version,
                    "formula_protocol_hash": formula.protocol_hash,
                    "model_id": str(model_artifact.id),
                    "model_version": str(model_artifact.version),
                    "source": {"grain": grain, "feature_contract_status": issue.status},
                },
            }
        )

    status_counts = {
        "complete": 0,
        "provisional": 0,
        "insufficient_evidence": int(sum(1 for issue in issues if issue.status == "insufficient_evidence")),
        "contract_mismatch": int(sum(1 for issue in issues if issue.status == "contract_mismatch")),
        "low_feature_coverage": int(sum(1 for issue in issues if issue.status == "low_feature_coverage")),
    }
    return {
        "formula_version": formula.version,
        "formula_protocol_hash": formula.protocol_hash,
        "formula_source": formula.source,
        "model": {
            "id": str(model_artifact.id),
            "name": model_artifact.name,
            "version": model_artifact.version,
            "target": model_artifact.target,
        },
        "summary": {
            "rows_requested": int(len(issues)),
            "rows_scored": 0,
            "status_counts": status_counts,
            "warnings": sorted({warning for issue in issues for warning in issue.warnings}),
        },
        "results": rows,
    }



def select_formula(version: str | None = None) -> FormulaSelection:
    qs = ICEAPlusFormulaVersion.objects.all()
    record = qs.filter(version=version).first() if version else qs.filter(is_active=True).order_by("-created_at").first()
    if record is None and version:
        record = qs.filter(version=version).first()
    if record is None:
        spec = build_default_icea_plus_spec()
        return FormulaSelection(
            version=str(spec.get("version") or "icea_plus_v1"),
            spec=spec,
            protocol_hash=formula_protocol_hash(spec),
            source="built_in_default",
            record=None,
        )
    spec = deep_merge_dict(build_default_icea_plus_spec(), dict(record.spec or {}))
    return FormulaSelection(
        version=record.version,
        spec=spec,
        protocol_hash=record.protocol_hash or formula_protocol_hash(spec),
        source="database",
        record=record,
    )



def upsert_formula_version(
    *,
    version: str,
    spec_override: dict[str, Any],
    notes: str,
    activate: bool,
) -> FormulaSelection:
    spec = deep_merge_dict(build_default_icea_plus_spec(), spec_override or {})
    spec["version"] = version
    protocol_hash = formula_protocol_hash(spec)

    obj, _ = ICEAPlusFormulaVersion.objects.update_or_create(
        version=version,
        defaults={
            "label": str(spec.get("label") or version),
            "status": str(spec.get("status") or "pilot"),
            "spec": spec,
            "notes": notes,
            "is_active": bool(activate),
            "protocol_hash": protocol_hash,
        },
    )
    if activate:
        ICEAPlusFormulaVersion.objects.exclude(pk=obj.pk).update(is_active=False)
    return FormulaSelection(version=obj.version, spec=spec, protocol_hash=protocol_hash, source="database", record=obj)



def _apply_dataset_filters(
    qs,
    *,
    grain: str,
    episode_ids: list[int] | None,
    unit_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    include_explicit_ids: bool,
):
    if include_explicit_ids and episode_ids:
        field = "episode_id" if grain == "episode" else "window__episode_id"
        qs = qs.filter(**{f"{field}__in": episode_ids})
    if unit_id is not None:
        field = "episode__unit_id" if grain == "episode" else "window__episode__unit_id"
        qs = qs.filter(**{field: unit_id})
    if date_from is not None:
        if grain == "episode":
            qs = qs.filter(episode__admission_date__gte=date_from)
        else:
            qs = qs.filter(window__start_dt__gte=date_from)
    if date_to is not None:
        if grain == "episode":
            qs = qs.filter(episode__admission_date__lte=date_to)
        else:
            qs = qs.filter(window__end_dt__lte=date_to)
    return qs



def _procedure_shares_for_episode(episode_id: int) -> tuple[dict[str, float], float]:
    qs = NormalizedProcedure.objects.filter(episode_id=episode_id, is_nursing=True)
    total = int(qs.count())
    if total <= 0:
        return {}, 0.0
    counts: dict[str, int] = {}
    identified = 0
    for proc in qs:
        actor = str(proc.performer_actor_ref or "").strip()
        if not actor:
            continue
        counts[actor] = counts.get(actor, 0) + 1
        identified += 1
    shares = {actor: count / total for actor, count in counts.items()}
    reliability = identified / total if total > 0 else 0.0
    return shares, float(reliability)



def _procedure_shares_for_window(window_row) -> tuple[dict[str, float], float]:
    qs = NormalizedProcedure.objects.filter(
        episode_id=window_row.window.episode_id,
        is_nursing=True,
        performed_dt__gte=window_row.window.start_dt,
        performed_dt__lt=window_row.window.end_dt,
    )
    total = int(qs.count())
    if total <= 0:
        return {}, 0.0
    counts: dict[str, int] = {}
    identified = 0
    for proc in qs:
        actor = str(proc.performer_actor_ref or "").strip()
        if not actor:
            continue
        counts[actor] = counts.get(actor, 0) + 1
        identified += 1
    shares = {actor: count / total for actor, count in counts.items()}
    reliability = identified / total if total > 0 else 0.0
    return shares, float(reliability)



def load_dataset(
    *,
    grain: str,
    from_db: bool,
    rows: list[dict[str, Any]] | None,
    reference_rows: list[dict[str, Any]] | None,
    episode_ids: list[int] | None = None,
    unit_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> LoadedDataset:
    if not from_db:
        selected = list(_normalize_external_rows(rows) or [])
        reference = list(_normalize_external_rows(reference_rows) or selected)
        meta_rows = []
        for idx, row in enumerate(selected):
            meta_rows.append(
                {
                    "row_id": str(row.get("row_id") or f"row:{idx}"),
                    "episode_id": row.get("episode_id"),
                    "window_id": row.get("window_id"),
                    "patient_key": str(row.get("patient_key") or row.get("episode_id") or f"row:{idx}"),
                    "unit_id": row.get("unit_id"),
                    "start_dt": row.get("start_dt"),
                    "end_dt": row.get("end_dt"),
                    "nurse_shares": dict(row.get("nurse_shares") or {}),
                    "nurse_reliability": float(row.get("nurse_reliability") or 0.0),
                }
            )
        return LoadedDataset(
            selected_df=pd.DataFrame(selected),
            reference_df=pd.DataFrame(reference),
            meta_rows=meta_rows,
            candidate_rows=len(selected),
            grain=grain,
        )

    if grain == "window":
        base_qs = EpisodeWindowFeatureRow.objects.select_related("window", "window__episode")
    else:
        base_qs = EpisodeFeatureRow.objects.select_related("episode")

    selected_qs = _apply_dataset_filters(
        base_qs,
        grain=grain,
        episode_ids=episode_ids,
        unit_id=unit_id,
        date_from=date_from,
        date_to=date_to,
        include_explicit_ids=True,
    )
    reference_qs = _apply_dataset_filters(
        base_qs,
        grain=grain,
        episode_ids=episode_ids,
        unit_id=unit_id,
        date_from=date_from,
        date_to=date_to,
        include_explicit_ids=False,
    )

    meta_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    if grain == "window":
        for row in selected_qs.order_by("window__episode_id", "window__window_index"):
            values = dict(row.features)
            values.update(row.target)
            values["episode_id"] = int(row.window.episode_id)
            values["window_id"] = str(row.window_id)
            values["window_index"] = int(row.window.window_index)
            values["unit_id"] = int(row.window.episode.unit_id)
            selected_rows.append(values)
            shares, reliability = _procedure_shares_for_window(row)
            meta_rows.append(
                {
                    "row_id": f"window:{row.window_id}",
                    "episode_id": int(row.window.episode_id),
                    "window_id": str(row.window_id),
                    "patient_key": str(row.window.episode_id),
                    "unit_id": int(row.window.episode.unit_id),
                    "start_dt": row.window.start_dt.isoformat() if row.window.start_dt else None,
                    "end_dt": row.window.end_dt.isoformat() if row.window.end_dt else None,
                    "nurse_shares": shares,
                    "nurse_reliability": reliability,
                }
            )
    else:
        for row in selected_qs.order_by("episode_id"):
            values = dict(row.features)
            values.update(row.target)
            values["episode_id"] = int(row.episode_id)
            values["unit_id"] = int(row.episode.unit_id)
            selected_rows.append(values)
            shares, reliability = _procedure_shares_for_episode(int(row.episode_id))
            meta_rows.append(
                {
                    "row_id": f"episode:{row.episode_id}",
                    "episode_id": int(row.episode_id),
                    "window_id": None,
                    "patient_key": str(row.episode_id),
                    "unit_id": int(row.episode.unit_id),
                    "start_dt": row.episode.admission_date.isoformat() if row.episode.admission_date else None,
                    "end_dt": row.episode.discharge_date.isoformat() if row.episode.discharge_date else None,
                    "nurse_shares": shares,
                    "nurse_reliability": reliability,
                }
            )

    reference_values = []
    for row in reference_qs:
        values = dict(row.features)
        values.update(row.target)
        if grain == "window":
            values["episode_id"] = int(row.window.episode_id)
            values["window_id"] = str(row.window_id)
            values["window_index"] = int(row.window.window_index)
            values["unit_id"] = int(row.window.episode.unit_id)
        else:
            values["episode_id"] = int(row.episode_id)
            values["unit_id"] = int(row.episode.unit_id)
        reference_values.append(values)

    return LoadedDataset(
        selected_df=pd.DataFrame(selected_rows),
        reference_df=pd.DataFrame(reference_values),
        meta_rows=meta_rows,
        candidate_rows=int(len(selected_rows)),
        grain=grain,
    )



def _predict_with_reference_nursing(
    *,
    engine: ICEAEngine,
    df: pd.DataFrame,
    features: list[str],
    nurse_cols: list[str],
    reference_df: pd.DataFrame,
) -> tuple[pd.Series, dict[str, float]]:
    x = engine._ensure_columns(df, features)
    x_ref = engine._ensure_columns(reference_df, features)
    replacements: dict[str, float] = {}
    for col in nurse_cols:
        if col not in x.columns:
            continue
        replacements[col] = float(pd.to_numeric(x_ref[col], errors="coerce").median()) if col in x_ref.columns else 0.0
        x[col] = replacements[col]
    preds = engine.model.predict(x)
    return pd.Series(preds, index=df.index, dtype=float), replacements



def _compute_causal_effects(
    *,
    selected_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    causal_spec: dict[str, Any] | None,
    spec: dict[str, Any],
) -> tuple[ComponentBuildResult, pd.Series | None, str | None, str | None, int | None]:
    if not causal_spec:
        return ComponentBuildResult(raw=None, warnings=["causal_spec_missing"]), None, None, None, None

    treatment = str(causal_spec.get("treatment") or "").strip()
    outcome = str(causal_spec.get("outcome") or "").strip()
    confounders = list(causal_spec.get("confounders") or [])
    effect_modifiers = list(causal_spec.get("effect_modifiers") or [])
    if not treatment or not outcome:
        return ComponentBuildResult(raw=None, warnings=["causal_spec_incomplete"]), None, treatment or None, outcome or None, None

    need = [treatment, outcome, *confounders, *effect_modifiers]
    for col in need:
        if col and col not in reference_df.columns:
            return ComponentBuildResult(raw=None, warnings=[f"causal_missing_column:{col}"]), None, treatment, outcome, None

    ref = reference_df[need].replace([np.inf, -np.inf], np.nan).dropna(axis=0, how="any")
    if len(ref) < int(((spec.get("causal") or {}).get("min_rows")) or 30):
        return ComponentBuildResult(raw=None, warnings=["causal_low_support"]), None, treatment, outcome, int(len(ref))

    x_cols = effect_modifiers or confounders
    X_ref = ref[x_cols].astype(float).values if x_cols else np.zeros((len(ref), 1))
    W_ref = ref[confounders].astype(float).values if confounders else None
    T_ref = ref[treatment].astype(float).values
    Y_ref = ref[outcome].astype(float).values

    try:
        model = ICEACausal(
            n_estimators=int(causal_spec.get("n_estimators") or ((spec.get("causal") or {}).get("n_estimators")) or 200)
        )
        model.fit(X=X_ref, W=W_ref, T=T_ref, Y=Y_ref)
    except Exception as exc:
        return ComponentBuildResult(raw=None, warnings=[f"causal_fit_failed:{exc.__class__.__name__}"]), None, treatment, outcome, int(len(ref))

    def _build_matrix(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
        if not cols:
            return np.zeros((len(df), 1))
        out = df.copy()
        for col in cols:
            if col not in out.columns:
                out[col] = 0.0
        return out[cols].astype(float).values

    try:
        X_selected = _build_matrix(selected_df, x_cols)
        X_reference = _build_matrix(reference_df, x_cols)
        tau_selected = pd.Series(np.asarray(model.effect(X_selected).cate, dtype=float), index=selected_df.index)
        tau_reference = pd.Series(np.asarray(model.effect(X_reference).cate, dtype=float), index=reference_df.index)
        return ComponentBuildResult(raw=tau_selected), tau_reference, treatment, outcome, int(len(ref))
    except Exception as exc:
        return ComponentBuildResult(raw=None, warnings=[f"causal_effect_failed:{exc.__class__.__name__}"]), None, treatment, outcome, int(len(ref))



def score_icea_plus(
    *,
    model_artifact: ModelArtifact,
    grain: str,
    from_db: bool,
    rows: list[dict[str, Any]] | None,
    reference_rows: list[dict[str, Any]] | None,
    formula_version: str | None,
    nurse_cols: list[str] | None,
    outcome_goal: str | None,
    causal_run_id: str | None = None,
    causal_spec_override: dict[str, Any] | None = None,
    baseline_model_id: str | None = None,
    episode_ids: list[int] | None = None,
    unit_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    formula = select_formula(formula_version)
    dataset = load_dataset(
        grain=grain,
        from_db=from_db,
        rows=rows,
        reference_rows=reference_rows,
        episode_ids=episode_ids,
        unit_id=unit_id,
        date_from=date_from,
        date_to=date_to,
    )

    if dataset.selected_df.empty:
        return {
            "detail": "no_rows_available_for_scoring",
            "formula_version": formula.version,
            "formula_protocol_hash": formula.protocol_hash,
        }

    features = list(model_artifact.features or [])
    if not features:
        return {
            "detail": "model_has_no_features",
            "model_id": str(model_artifact.id),
            "formula_version": formula.version,
            "formula_protocol_hash": formula.protocol_hash,
        }

    if not from_db:
        contract_issues = _validate_external_feature_contract(
            rows=rows,
                model_artifact=model_artifact,
                grain=grain,
                model_role="primary",
            )
        if baseline_model_id:
            baseline_model_for_contract = ModelArtifact.objects.filter(id=baseline_model_id).first()
            if baseline_model_for_contract is not None:
                contract_issues.extend(
                    _validate_external_feature_contract(
                        rows=rows,
                        model_artifact=baseline_model_for_contract,
                        grain=grain,
                        model_role="baseline",
                    )
                )
        if contract_issues:
            contract_issues = _merge_feature_contract_issues(contract_issues)
            return _feature_contract_failure_result(
                formula=formula,
                model_artifact=model_artifact,
                grain=grain,
                issues=contract_issues,
            )

    selected_df = dataset.selected_df.copy()
    reference_df = dataset.reference_df.copy()
    if from_db:
        for frame in (selected_df, reference_df):
            for feature in features:
                if feature not in frame.columns:
                    frame[feature] = 0.0

    inferred_nurse_cols = infer_nurse_columns(features=features, df=selected_df, supplied=nurse_cols)
    goal, goal_source = (outcome_goal, "request") if outcome_goal else infer_outcome_goal(model_artifact.target, formula.spec.get("outcome_goal_rules"))
    goal = str(goal or "higher_is_better")

    background = reference_df.reindex(columns=features, fill_value=0.0).head(min(len(reference_df), 200)).copy()
    background = background.apply(pd.to_numeric, errors="coerce").fillna(0.0)
    engine = ICEAEngine(model_artifact.model_path, background=background, shap_mode="interventional")
    explained_selected = engine.explain(selected_df, features=features)
    explained_reference = engine.explain(reference_df, features=features)

    baseline_model = None
    baseline_mode = "counterfactual_nursing_reference"
    baseline_replacements: dict[str, float] = {}
    if baseline_model_id:
        baseline_model = ModelArtifact.objects.filter(id=baseline_model_id).first()

    if baseline_model and baseline_model.model_path:
        baseline_mode = "dedicated_baseline_model"
        baseline_background = reference_df.reindex(columns=baseline_model.features, fill_value=0.0).head(min(len(reference_df), 200)).copy()
        baseline_background = baseline_background.apply(pd.to_numeric, errors="coerce").fillna(0.0)
        baseline_engine = ICEAEngine(baseline_model.model_path, background=baseline_background, shap_mode="interventional")
        baseline_selected = pd.Series(
            baseline_engine.model.predict(baseline_engine._ensure_columns(selected_df, baseline_model.features)),
            index=selected_df.index,
            dtype=float,
        )
        baseline_reference = pd.Series(
            baseline_engine.model.predict(baseline_engine._ensure_columns(reference_df, baseline_model.features)),
            index=reference_df.index,
            dtype=float,
        )
    else:
        baseline_selected, baseline_replacements = _predict_with_reference_nursing(
            engine=engine,
            df=selected_df,
            features=features,
            nurse_cols=inferred_nurse_cols,
            reference_df=reference_df,
        )
        baseline_reference, _ = _predict_with_reference_nursing(
            engine=engine,
            df=reference_df,
            features=features,
            nurse_cols=inferred_nurse_cols,
            reference_df=reference_df,
        )

    outcome_col = model_artifact.target
    y_selected = pd.to_numeric(selected_df.get(outcome_col), errors="coerce") if outcome_col in selected_df.columns else None
    y_reference = pd.to_numeric(reference_df.get(outcome_col), errors="coerce") if outcome_col in reference_df.columns else None

    if y_selected is None or y_reference is None:
        benefit_component = ComponentBuildResult(raw=None, warnings=["outcome_missing_for_benefit"])
        benefit_reference = None
    else:
        benefit_raw = utility_transform(y_selected, goal) - utility_transform(baseline_selected, goal)
        benefit_reference = utility_transform(y_reference, goal) - utility_transform(baseline_reference, goal)
        benefit_component = ComponentBuildResult(raw=benefit_raw)

    attribution_component = ComponentBuildResult(
        raw=compute_relative_nursing_attribution(
            explained_selected.shap_values,
            nurse_cols=inferred_nurse_cols,
            epsilon=float(((formula.spec.get("attribution") or {}).get("epsilon")) or 1e-6),
        ),
    )
    attribution_reference = compute_relative_nursing_attribution(
        explained_reference.shap_values,
        nurse_cols=inferred_nurse_cols,
        epsilon=float(((formula.spec.get("attribution") or {}).get("epsilon")) or 1e-6),
    )

    quality_component = compute_quality_raw(selected_df, formula.spec)
    quality_reference_component = compute_quality_raw(reference_df, formula.spec)

    causal_spec = dict(causal_spec_override or {})
    if not causal_spec and causal_run_id:
        run = CausalRun.objects.filter(id=causal_run_id).select_related("spec").first()
        if run and run.spec:
            causal_spec = dict(run.spec.spec or {})

    causal_component, causal_reference_raw, causal_treatment, causal_outcome, causal_fit_rows = _compute_causal_effects(
        selected_df=selected_df,
        reference_df=reference_df,
        causal_spec=causal_spec or None,
        spec=formula.spec,
    )
    if causal_component.raw is not None:
        sign_goal = float(outcome_sign(goal))
        causal_component.raw = causal_component.raw * sign_goal
        causal_reference_raw = causal_reference_raw * sign_goal if causal_reference_raw is not None else None

    outcome_scale = float(np.nanstd(pd.to_numeric(reference_df.get(outcome_col, pd.Series([0.0])), errors="coerce"))) if outcome_col in reference_df.columns else 1.0
    if not np.isfinite(outcome_scale) or outcome_scale <= 1e-6:
        outcome_scale = 1.0

    missingness_selected = compute_missingness_burden(selected_df)
    missingness_reference = compute_missingness_burden(reference_df)
    ood_selected = compute_ood_burden(
        selected_df,
        features=features,
        feature_stats=(model_artifact.metrics or {}).get("feature_stats"),
        spec=formula.spec,
    )
    ood_reference = compute_ood_burden(
        reference_df,
        features=features,
        feature_stats=(model_artifact.metrics or {}).get("feature_stats"),
        spec=formula.spec,
    )
    low_support_selected = compute_low_support_burden(
        metrics=model_artifact.metrics,
        causal_rows=causal_fit_rows,
        spec=formula.spec,
        index=selected_df.index,
    )
    low_support_reference = compute_low_support_burden(
        metrics=model_artifact.metrics,
        causal_rows=causal_fit_rows,
        spec=formula.spec,
        index=reference_df.index,
    )
    conformal_selected = compute_conformal_burden(metrics=model_artifact.metrics, outcome_scale=outcome_scale, index=selected_df.index)
    conformal_reference = compute_conformal_burden(metrics=model_artifact.metrics, outcome_scale=outcome_scale, index=reference_df.index)

    uncertainty_component = combine_uncertainty_subcomponents(
        index=selected_df.index,
        subcomponents={
            "conformal_width": conformal_selected,
            "missingness": missingness_selected,
            "ood": ood_selected,
            "low_support": low_support_selected,
        },
        spec=formula.spec,
    )
    uncertainty_reference_component = combine_uncertainty_subcomponents(
        index=reference_df.index,
        subcomponents={
            "conformal_width": conformal_reference,
            "missingness": missingness_reference,
            "ood": ood_reference,
            "low_support": low_support_reference,
        },
        spec=formula.spec,
    )

    for component, ref_values in (
        (benefit_component, benefit_reference),
        (attribution_component, attribution_reference),
        (quality_component, quality_reference_component.raw),
        (causal_component, causal_reference_raw),
        (uncertainty_component, uncertainty_reference_component.raw),
    ):
        normalized, _ = robust_z(component.raw, ref_values, spec=formula.spec)
        component.normalized = normalized

    severity_weights = compute_severity_weight(
        baseline_selected,
        baseline_reference,
        goal=goal,
        spec=formula.spec,
    )
    if severity_weights is None:
        severity_weights = pd.Series([1.0] * len(selected_df), index=selected_df.index, dtype=float)

    request_hash = _safe_json_hash(
        {
            "model_id": str(model_artifact.id),
            "grain": grain,
            "formula_version": formula.version,
            "episode_ids": episode_ids,
            "unit_id": unit_id,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "causal_run_id": causal_run_id,
        }
    )

    results = []
    for pos, meta in enumerate(dataset.meta_rows):
        idx = selected_df.index[pos]

        def _value(series: pd.Series | None) -> float | None:
            if series is None:
                return None
            value = series.loc[idx]
            return None if pd.isna(value) else float(value)

        row_components = {
            "benefit": ICEAPlusComponentValue(
                raw=_value(benefit_component.raw),
                normalized=_value(benefit_component.normalized),
                available=_value(benefit_component.raw) is not None and _value(benefit_component.normalized) is not None,
                warnings=list(benefit_component.warnings),
            ),
            "attribution": ICEAPlusComponentValue(
                raw=_value(attribution_component.raw),
                normalized=_value(attribution_component.normalized),
                available=_value(attribution_component.raw) is not None and _value(attribution_component.normalized) is not None,
                warnings=list(attribution_component.warnings),
            ),
            "causal": ICEAPlusComponentValue(
                raw=_value(causal_component.raw),
                normalized=_value(causal_component.normalized),
                available=_value(causal_component.raw) is not None and _value(causal_component.normalized) is not None,
                warnings=list(causal_component.warnings),
            ),
            "quality": ICEAPlusComponentValue(
                raw=_value(quality_component.raw),
                normalized=_value(quality_component.normalized),
                available=_value(quality_component.raw) is not None and _value(quality_component.normalized) is not None,
                warnings=list(quality_component.warnings),
            ),
            "uncertainty": ICEAPlusComponentValue(
                raw=_value(uncertainty_component.raw),
                normalized=_value(uncertainty_component.normalized),
                available=_value(uncertainty_component.raw) is not None and _value(uncertainty_component.normalized) is not None,
                warnings=list(uncertainty_component.warnings),
            ),
        }

        legacy_icea_value = 0.0
        present_nurse_cols = [col for col in inferred_nurse_cols if col in explained_selected.shap_values.columns]
        if present_nurse_cols:
            legacy_icea_value = float(explained_selected.shap_values[present_nurse_cols].sum(axis=1).loc[idx])

        lineage = ICEAPlusLineage(
            formula_version=formula.version,
            formula_protocol_hash=formula.protocol_hash,
            model_id=str(model_artifact.id),
            model_version=str(model_artifact.version),
            baseline_model_id=str(baseline_model.id) if baseline_model else None,
            causal_spec_hash=_safe_json_hash(causal_spec) if causal_spec else None,
            outcome=outcome_col,
            outcome_goal=goal,
            treatment=causal_treatment,
            nurse_cols=inferred_nurse_cols,
            transformations={
                "utility": "identity" if goal == "higher_is_better" else "sign_flipped",
                "goal_source": goal_source,
                "normalization": (formula.spec.get("normalization") or {}).get("method"),
                "baseline_mode": baseline_mode,
                "baseline_reference_values": baseline_replacements,
                "causal_outcome": causal_outcome,
            },
            source={
                "grain": grain,
                "request_hash": request_hash,
                "reference_rows": int(len(reference_df)),
                "formula_source": formula.source,
            },
        )

        aggregation = {
            "severity_weight": float(severity_weights.loc[idx]) if idx in severity_weights.index else 1.0,
            "nurse_shares": dict(meta.get("nurse_shares") or {}),
            "nurse_reliability": float(meta.get("nurse_reliability") or 0.0),
            "effective_exposure_share": 1.0,
        }

        score_row = compute_row_score(
            row_id=str(meta["row_id"]),
            grain=grain,
            episode_id=meta.get("episode_id"),
            window_id=meta.get("window_id"),
            patient_key=meta.get("patient_key"),
            unit_id=meta.get("unit_id"),
            start_dt=meta.get("start_dt"),
            end_dt=meta.get("end_dt"),
            components=row_components,
            weights=dict(formula.spec.get("weights") or {}),
            raw_uncertainty=_value(uncertainty_component.raw),
            lineage=lineage,
            legacy_icea={
                "nursing_shap_sum": legacy_icea_value,
                "prediction": float(explained_selected.predictions[pos]),
                "baseline_expected": float(baseline_selected.loc[idx]),
            },
            aggregation=aggregation,
            spec=formula.spec,
        )
        score_dict = score_row.to_dict()
        score_dict["flags"]["low_support"] = bool((_value(low_support_selected) or 0.0) > 0.0)
        score_dict["flags"]["ood_detected"] = bool((_value(ood_selected) or 0.0) > 0.0)
        if score_dict["flags"]["low_support"] and "low_support" not in score_dict["warnings"]:
            score_dict["warnings"].append("low_support")
        if score_dict["flags"]["ood_detected"] and "ood_detected" not in score_dict["warnings"]:
            score_dict["warnings"].append("ood_detected")
        score_dict["warnings"] = sorted(set(score_dict["warnings"]))
        results.append(score_dict)

    scored_rows = [row for row in results if row.get("score") is not None]
    component_means = {}
    for name in ("benefit", "attribution", "causal", "quality", "uncertainty"):
        vals = [row["components"][name]["normalized"] for row in scored_rows if row["components"][name]["normalized"] is not None]
        component_means[name] = float(np.mean(vals)) if vals else None

    summary = {
        "rows_requested": int(dataset.candidate_rows),
        "rows_scored": int(len(scored_rows)),
        "formula_version": formula.version,
        "formula_protocol_hash": formula.protocol_hash,
        "default_pilot_weights": dict(formula.spec.get("weights") or {}),
        "baseline_mode": baseline_mode,
        "causal_available": bool(any(row["flags"].get("causal_available") for row in results)),
        "status_counts": {
            "complete": int(sum(1 for row in results if row.get("status") == "complete")),
            "provisional": int(sum(1 for row in results if row.get("status") == "provisional")),
            "insufficient_evidence": int(sum(1 for row in results if row.get("status") == "insufficient_evidence")),
        },
        "component_means": component_means,
        "warnings": sorted({warning for row in results for warning in (row.get("warnings") or [])}),
    }

    return {
        "formula_version": formula.version,
        "formula_protocol_hash": formula.protocol_hash,
        "formula_source": formula.source,
        "model": {
            "id": str(model_artifact.id),
            "name": model_artifact.name,
            "version": model_artifact.version,
            "target": model_artifact.target,
        },
        "summary": summary,
        "results": results,
    }
