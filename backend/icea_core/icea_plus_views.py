from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Model
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from icea_core.aggregation import (
    MIN_AGGREGATE_EPISODES,
    MIN_STAFF_FOR_STAFF_DIMENSION,
    aggregate_scored_rows,
    governance_export_metadata,
    redacted_low_support_summary,
)
from icea_core.followup import (
    ScoringBlockedError,
    build_patient_summary,
    build_summary_writeback,
    ensure_followup_record,
    get_followup_record,
    ingest_followup,
    persist_initial_followup_records,
    rescore_followup,
)
from icea_core.icea_plus_serializers import (
    ICEAPlusAggregateQuerySerializer,
    ICEAPlusCalibrateSerializer,
    ICEAPlusFollowupIngestSerializer,
    ICEAPlusFollowupRescoreSerializer,
    ICEAPlusFollowupStatusQuerySerializer,
    ICEAPlusScoreRequestSerializer,
    ICEAPlusWritebackPatientQuerySerializer,
    ICEAPlusWritebackSummaryQuerySerializer,
)
from icea_core.models import ICEAPlusComputation, ModelArtifact, PatientEpisode
from icea_core.permissions import (
    ICEAAdminPermission,
    ICEAAdminOrServicePermission,
    ICEAAggregateViewerPermission,
    ICEABackwardCompatiblePermission,
    ICEAResearcherPermission,
)
from icea_core.scoring import redact_shadow_score_response, score_icea_plus, select_formula, upsert_formula_version
from icea_pipeline.audit import append_audit_event


def _error_response(detail: str, *, status_code: int, errors: dict | None = None, **extra):
    payload = {"detail": detail}
    if errors:
        payload["errors"] = errors
    if extra:
        payload.update(extra)
    return Response(payload, status=status_code)


def _validate_serializer(ser, *, request_type: str):
    if ser.is_valid():
        return ser.validated_data
    return _error_response(
        "invalid_request",
        status_code=400,
        errors=ser.errors,
        request_type=request_type,
    )


def _scoring_blocked_response(exc: ScoringBlockedError) -> Response:
    payload = dict(exc.result)
    payload.setdefault("status", str(payload.get("detail") or "scoring_blocked"))
    payload.setdefault("non_individual_use", True)
    payload.setdefault("shadow_mode", True)
    payload.setdefault("intended_use", "shadow_aggregate_research")
    payload.setdefault("score_summary", None)
    payload.setdefault("score_summary_redacted", True)
    return Response(payload, status=400)


def _safe_aggregate_grouping(requested_group_by: str, *, grain: str) -> tuple[str, list[str], bool]:
    requested = str(requested_group_by or "unit")
    warnings: list[str] = []
    require_staff_count = False
    if requested in {"patient", "episode", "window", "nurse"}:
        warnings.append(f"{requested}_grouping_individualizable_falling_back_to_unit")
        return "unit", warnings, False
    if requested == "team":
        warnings.append("team_not_explicitly_modeled_falling_back_to_unit")
        return "unit", warnings, False
    if requested == "shift":
        if str(grain or "episode") != "window":
            warnings.append("shift_aggregation_requires_window_grain_falling_back_to_unit")
            return "unit", warnings, False
        require_staff_count = True
        warnings.append("shift_aggregation_deidentified_to_unit_date_bucket")
        return "shift", warnings, require_staff_count
    if requested not in {"unit", "date"}:
        warnings.append("unsupported_grouping_falling_back_to_unit")
        return "unit", warnings, False
    return requested, warnings, require_staff_count


def _get_object_or_typed_error(model_cls: type[Model], **lookup):
    try:
        return model_cls.objects.get(**lookup)
    except (model_cls.DoesNotExist, ValueError, TypeError, DjangoValidationError):
        detail = "resource_not_found"
        if model_cls is ModelArtifact:
            detail = "model_not_found"
        elif model_cls is PatientEpisode:
            detail = "episode_not_found"
        key, value = next(iter(lookup.items()))
        return _error_response(detail, status_code=404, **{key: str(value)})


class ICEAPlusExplainView(APIView):
    def get(self, request):
        version = (request.query_params.get("formula_version") or "").strip() or None
        formula = select_formula(version)
        return Response(
            {
                "formula_version": formula.version,
                "formula_protocol_hash": formula.protocol_hash,
                "formula_source": formula.source,
                "status": formula.spec.get("status"),
                "weights": dict(formula.spec.get("weights") or {}),
                "equation": "ICEA+_i = 100 * sigmoid(beta0 + betaB*B_i + betaA*A_i + betaC*C_i + betaQ*Q_i - betaU*U_i)",
                "components": {
                    "benefit": "B_i = z(g(y_i) - g(y_hat_i_base))",
                    "attribution": "A_i = z(phi_i^N / (sum_j |phi_i,j| + epsilon))",
                    "causal": "C_i = z(sign_goal * tau_i^N)",
                    "quality": "Q_i = z(q_i)",
                    "uncertainty": "U_i = z(u_i)",
                },
                "baseline_mode": (formula.spec.get("baseline") or {}).get("mode"),
                "limitations": [
                    "Pilot-grade/calibration-ready formula: institutional calibration and external validation remain pending.",
                    "ICEA+ does not replace clinical judgment and must not be used as automatic labor sanctioning evidence.",
                    "Causal component is exploratory, aggregate-only, and degrades to provisional when support is not defensible.",
                    "Uncertainty and process-quality components only use signals that exist in the current repo state.",
                ],
                "flags": {
                    "causal_required_for_complete_score": True,
                    "provisional_without_causal": True,
                    "insufficient_when_required_components_missing": True,
                },
            }
        )


class ICEAPlusScoreView(APIView):
    permission_classes = [ICEAResearcherPermission]

    def post(self, request):
        ser = ICEAPlusScoreRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        artifact = ModelArtifact.objects.get(id=payload["model_id"])
        result = score_icea_plus(
            model_artifact=artifact,
            grain=payload.get("grain") or "episode",
            from_db=bool(payload.get("from_db", True)),
            rows=payload.get("rows"),
            reference_rows=payload.get("reference_rows"),
            formula_version=(payload.get("formula_version") or "").strip() or None,
            nurse_cols=payload.get("nurse_cols"),
            outcome_goal=payload.get("outcome_goal"),
            causal_run_id=str(payload.get("causal_run_id")) if payload.get("causal_run_id") else None,
            causal_spec_override=payload.get("causal_spec"),
            baseline_model_id=str(payload.get("baseline_model_id")) if payload.get("baseline_model_id") else None,
            episode_ids=payload.get("episode_ids"),
            unit_id=payload.get("unit_id"),
            date_from=payload.get("date_from"),
            date_to=payload.get("date_to"),
        )

        if result.get("detail"):
            return Response(result, status=400)

        summary = dict(result.get("summary") or {})
        request_hash = ""
        if result.get("results"):
            request_hash = str((((result["results"][0] or {}).get("lineage") or {}).get("source") or {}).get("request_hash") or "")
        computation = ICEAPlusComputation.objects.create(
            formula_version=result["formula_version"],
            model=artifact,
            grain=str(payload.get("grain") or "episode"),
            rows=int(summary.get("rows_requested") or 0),
            status="ok",
            summary=summary,
            request_hash=request_hash,
        )
        append_audit_event(
            event_type="icea_plus_score",
            payload={
                "model_id": str(artifact.id),
                "baseline_model_id": str(payload.get("baseline_model_id") or ""),
                "formula_version": result["formula_version"],
                "grain": str(payload.get("grain") or "episode"),
                "rows": int(summary.get("rows_requested") or 0),
            },
            context="icea-plus/score",
        )
        if bool(payload.get("from_db", True)) and str(payload.get("grain") or "episode") == "episode":
            persist_initial_followup_records(
                artifact=artifact,
                result=result,
                computation=computation,
                request_config=dict(payload),
            )
        return Response(redact_shadow_score_response(result))


class ICEAPlusAggregateView(APIView):
    permission_classes = [ICEAAggregateViewerPermission]

    def get(self, request):
        ser = ICEAPlusAggregateQuerySerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        params = ser.validated_data

        artifact = ModelArtifact.objects.get(id=params["model_id"])
        score_result = score_icea_plus(
            model_artifact=artifact,
            grain=params.get("grain") or "episode",
            from_db=True,
            rows=None,
            reference_rows=None,
            formula_version=(params.get("formula_version") or "").strip() or None,
            nurse_cols=None,
            outcome_goal=params.get("outcome_goal"),
            causal_run_id=str(params.get("causal_run_id")) if params.get("causal_run_id") else None,
            baseline_model_id=str(params.get("baseline_model_id")) if params.get("baseline_model_id") else None,
            unit_id=params.get("unit_id"),
            date_from=params.get("date_from"),
            date_to=params.get("date_to"),
        )
        if score_result.get("detail"):
            return Response(score_result, status=400)

        requested_group_by = str(params.get("group_by") or "unit")
        effective_group_by = requested_group_by
        warnings: list[str] = []
        rows = list(score_result.get("_aggregate_rows") or score_result.get("results") or [])

        formula = select_formula((params.get("formula_version") or "").strip() or None)
        min_rel = float((((formula.spec).get("aggregation") or {}).get("min_nurse_reliability")) or 0.60)

        effective_group_by, grouping_warnings, require_staff_count = _safe_aggregate_grouping(
            requested_group_by,
            grain=str(params.get("grain") or "episode"),
        )
        warnings.extend(grouping_warnings)
        if requested_group_by == "nurse":
            warnings.append("nurse_level_attribution_not_exported_or_ranked")
        if requested_group_by in {"team", "shift", "nurse"} and min_rel > 0:
            warnings.append("staff_dimension_requires_minimum_support_and_non_punitive_use")

        artifact_metrics = artifact.metrics or {}
        artifact_evidence = artifact_metrics.get("evidence_pack") if isinstance(artifact_metrics.get("evidence_pack"), dict) else {}
        aggregated = aggregate_scored_rows(
            rows=rows,
            group_by=effective_group_by,
            epsilon=float((((formula.spec).get("aggregation") or {}).get("epsilon")) or 1e-6),
            enforce_suppression=True,
            min_cell_count=MIN_AGGREGATE_EPISODES,
            min_staff_count=MIN_STAFF_FOR_STAFF_DIMENSION,
            require_staff_count=require_staff_count,
            case_mix_spec=artifact_evidence.get("case_mix_spec")
            or artifact_metrics.get("case_mix_spec")
            or (formula.spec or {}).get("case_mix_spec"),
        )
        suppressed_cells = int(sum(1 for row in aggregated if row.get("suppressed")))
        response_summary = score_result.get("summary")
        if suppressed_cells > 0:
            response_summary = redacted_low_support_summary(
                suppressed_cells=suppressed_cells,
                aggregation_level=effective_group_by,
                min_cell_count=MIN_AGGREGATE_EPISODES,
                min_staff_count=MIN_STAFF_FOR_STAFF_DIMENSION,
            )
        export_metadata = governance_export_metadata(
            aggregation_level=effective_group_by,
            min_cell_count=MIN_AGGREGATE_EPISODES,
            suppressed_cells=suppressed_cells,
            formula_version=score_result["formula_version"],
            model_lineage={
                "model_id": str(artifact.id),
                "model_version": artifact.version,
                "formula_protocol_hash": score_result["formula_protocol_hash"],
            },
            generated_at=timezone.now().isoformat(),
        )

        append_audit_event(
            event_type="icea_plus_aggregate",
            payload={
                "model_id": str(artifact.id),
                "baseline_model_id": str(params.get("baseline_model_id") or ""),
                "requested_group_by": requested_group_by,
                "effective_group_by": effective_group_by,
                "grain": str(params.get("grain") or "episode"),
                "rows": int(len(rows)),
            },
            context="icea-plus/aggregate",
        )
        evidence_metadata = {
            key: score_result[key]
            for key in (
                "primary_model_evidence_status",
                "baseline_model_id",
                "baseline_model_evidence_status",
                "baseline_model_not_defensible",
                "baseline_model_missing_evidence",
                "baseline_model",
            )
            if key in score_result
        }
        return Response(
            {
                "formula_version": score_result["formula_version"],
                "formula_protocol_hash": score_result["formula_protocol_hash"],
                **evidence_metadata,
                "requested_group_by": requested_group_by,
                "effective_group_by": effective_group_by,
                "grain": str(params.get("grain") or "episode"),
                "warnings": warnings,
                "summary": response_summary,
                "governance": export_metadata,
                "non_individual_use": True,
                "shadow_mode": True,
                "suppressed_cells": suppressed_cells,
                "min_cell_count": MIN_AGGREGATE_EPISODES,
                "results": aggregated,
            }
        )


class ICEAPlusCalibrateView(APIView):
    permission_classes = [ICEAAdminPermission]

    def post(self, request):
        ser = ICEAPlusCalibrateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        formula = upsert_formula_version(
            version=str(payload.get("version") or "icea_plus_v1"),
            spec_override=payload.get("spec") or {},
            notes=str(payload.get("notes") or ""),
            activate=bool(payload.get("activate", True)),
        )

        append_audit_event(
            event_type="icea_plus_calibrate",
            payload={
                "formula_version": formula.version,
                "activate": bool(payload.get("activate", True)),
            },
            context="icea-plus/calibrate",
        )
        return Response(
            {
                "formula_version": formula.version,
                "formula_protocol_hash": formula.protocol_hash,
                "formula_source": formula.source,
                "active": bool(formula.record.is_active if formula.record else False),
                "weights": dict((formula.spec or {}).get("weights") or {}),
            },
            status=201,
        )


class ICEAPlusFollowupIngestView(APIView):
    permission_classes = [ICEABackwardCompatiblePermission]

    def post(self, request):
        ser = ICEAPlusFollowupIngestSerializer(data=request.data)
        payload = _validate_serializer(ser, request_type="body")
        if isinstance(payload, Response):
            return payload

        artifact = _get_object_or_typed_error(ModelArtifact, id=payload["model_id"])
        if isinstance(artifact, Response):
            return artifact
        episode = _get_object_or_typed_error(PatientEpisode, id=int(payload["episode_id"]))
        if isinstance(episode, Response):
            return episode
        try:
            record = ingest_followup(
                episode=episode,
                artifact=artifact,
                request_config={"formula_version": str(payload.get("formula_version") or "")},
            )
        except ScoringBlockedError as exc:
            return _scoring_blocked_response(exc)
        return Response(build_patient_summary(record))


class ICEAPlusFollowupRescoreView(APIView):
    permission_classes = [ICEABackwardCompatiblePermission]

    def post(self, request):
        ser = ICEAPlusFollowupRescoreSerializer(data=request.data)
        payload = _validate_serializer(ser, request_type="body")
        if isinstance(payload, Response):
            return payload

        artifact = _get_object_or_typed_error(ModelArtifact, id=payload["model_id"])
        if isinstance(artifact, Response):
            return artifact
        episode = _get_object_or_typed_error(PatientEpisode, id=int(payload["episode_id"]))
        if isinstance(episode, Response):
            return episode
        try:
            record = rescore_followup(
                episode=episode,
                artifact=artifact,
                request_config={
                    "formula_version": str(payload.get("formula_version") or ""),
                    "outcome_goal": payload.get("outcome_goal"),
                    "causal_run_id": payload.get("causal_run_id"),
                    "causal_spec": payload.get("causal_spec"),
                    "baseline_model_id": payload.get("baseline_model_id"),
                    "nurse_cols": payload.get("nurse_cols"),
                },
            )
        except ScoringBlockedError as exc:
            return _scoring_blocked_response(exc)
        return Response(build_patient_summary(record))


class ICEAPlusFollowupStatusView(APIView):
    permission_classes = [ICEABackwardCompatiblePermission]

    def get(self, request):
        ser = ICEAPlusFollowupStatusQuerySerializer(data=request.query_params)
        params = _validate_serializer(ser, request_type="query")
        if isinstance(params, Response):
            return params

        artifact = _get_object_or_typed_error(ModelArtifact, id=params["model_id"])
        if isinstance(artifact, Response):
            return artifact
        episode = _get_object_or_typed_error(PatientEpisode, id=int(params["episode_id"]))
        if isinstance(episode, Response):
            return episode
        record = get_followup_record(
            episode_id=int(episode.id),
            model_id=str(artifact.id),
            formula_version=(str(params.get("formula_version") or "").strip() or None),
        )
        if record is None:
            return Response(
                {
                    "detail": "followup_record_not_found",
                    "episode_id": int(episode.id),
                    "model_id": str(artifact.id),
                },
                status=404,
            )
        return Response(build_patient_summary(record))


class ICEAPlusWritebackSummaryView(APIView):
    permission_classes = [ICEAAdminOrServicePermission]

    def get(self, request):
        ser = ICEAPlusWritebackSummaryQuerySerializer(data=request.query_params)
        params = _validate_serializer(ser, request_type="query")
        if isinstance(params, Response):
            return params

        artifact = _get_object_or_typed_error(ModelArtifact, id=params["model_id"])
        if isinstance(artifact, Response):
            return artifact
        payload = build_summary_writeback(
            artifact=artifact,
            group_by=str(params.get("group_by") or "unit"),
            unit_id=params.get("unit_id"),
            formula_version=(str(params.get("formula_version") or "").strip() or None),
            date_from=params.get("date_from"),
            date_to=params.get("date_to"),
        )
        return Response(payload, status=400 if payload.get("detail") else 200)


class ICEAPlusWritebackPatientView(APIView):
    permission_classes = [ICEAAdminOrServicePermission]

    def get(self, request):
        ser = ICEAPlusWritebackPatientQuerySerializer(data=request.query_params)
        params = _validate_serializer(ser, request_type="query")
        if isinstance(params, Response):
            return params

        artifact = _get_object_or_typed_error(ModelArtifact, id=params["model_id"])
        if isinstance(artifact, Response):
            return artifact
        episode = _get_object_or_typed_error(PatientEpisode, id=int(params["episode_id"]))
        if isinstance(episode, Response):
            return episode
        record = get_followup_record(
            episode_id=int(episode.id),
            model_id=str(artifact.id),
            formula_version=(str(params.get("formula_version") or "").strip() or None),
        )
        if record is None:
            try:
                record = ensure_followup_record(
                    episode=episode,
                    artifact=artifact,
                    request_config={"formula_version": str(params.get("formula_version") or "")},
                )
            except ScoringBlockedError as exc:
                return _scoring_blocked_response(exc)
        return Response(build_patient_summary(record))
