from __future__ import annotations

from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from icea_core.aggregation import aggregate_scored_rows
from icea_core.icea_plus_serializers import (
    ICEAPlusAggregateQuerySerializer,
    ICEAPlusCalibrateSerializer,
    ICEAPlusScoreRequestSerializer,
)
from icea_core.models import ICEAPlusComputation, ModelArtifact
from icea_core.permissions import ICEABackwardCompatiblePermission
from icea_core.scoring import score_icea_plus, select_formula, upsert_formula_version
from icea_pipeline.audit import append_audit_event


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
                    "Causal contribution degrades to provisional when the current repo cannot support a defensible effect estimate.",
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
        ICEAPlusComputation.objects.create(
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
                "formula_version": result["formula_version"],
                "grain": str(payload.get("grain") or "episode"),
                "rows": int(summary.get("rows_requested") or 0),
            },
            context="icea-plus/score",
        )
        return Response(result)


class ICEAPlusAggregateView(APIView):
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
            unit_id=params.get("unit_id"),
            date_from=params.get("date_from"),
            date_to=params.get("date_to"),
        )
        if score_result.get("detail"):
            return Response(score_result, status=400)

        requested_group_by = str(params.get("group_by") or "unit")
        effective_group_by = requested_group_by
        warnings: list[str] = []
        rows = list(score_result.get("results") or [])

        formula = select_formula((params.get("formula_version") or "").strip() or None)
        min_rel = float((((formula.spec).get("aggregation") or {}).get("min_nurse_reliability")) or 0.60)

        if requested_group_by == "team":
            effective_group_by = "unit"
            warnings.append("team_not_explicitly_modeled_falling_back_to_unit")

        if requested_group_by == "shift" and str(params.get("grain") or "episode") != "window":
            effective_group_by = "unit"
            warnings.append("shift_aggregation_requires_window_grain_falling_back_to_unit")

        if requested_group_by == "nurse":
            nurse_rows = []
            for row in rows:
                shares = dict((row.get("aggregation") or {}).get("nurse_shares") or {})
                reliability = float((row.get("aggregation") or {}).get("nurse_reliability") or 0.0)
                if reliability < min_rel or not shares:
                    continue
                for nurse_id, share in shares.items():
                    clone = dict(row)
                    clone["aggregation"] = dict(row.get("aggregation") or {})
                    clone["aggregation"]["effective_exposure_share"] = float(share)
                    clone["patient_key"] = str(nurse_id)
                    nurse_rows.append(clone)
            if not nurse_rows:
                effective_group_by = "unit"
                warnings.append("nurse_level_attribution_unreliable_falling_back_to_unit")
            else:
                rows = nurse_rows
                effective_group_by = "patient"

        aggregated = aggregate_scored_rows(
            rows=rows,
            group_by=effective_group_by,
            epsilon=float((((formula.spec).get("aggregation") or {}).get("epsilon")) or 1e-6),
        )

        append_audit_event(
            event_type="icea_plus_aggregate",
            payload={
                "model_id": str(artifact.id),
                "requested_group_by": requested_group_by,
                "effective_group_by": effective_group_by,
                "grain": str(params.get("grain") or "episode"),
                "rows": int(len(rows)),
            },
            context="icea-plus/aggregate",
        )
        return Response(
            {
                "formula_version": score_result["formula_version"],
                "formula_protocol_hash": score_result["formula_protocol_hash"],
                "requested_group_by": requested_group_by,
                "effective_group_by": effective_group_by,
                "grain": str(params.get("grain") or "episode"),
                "warnings": warnings,
                "summary": score_result.get("summary"),
                "results": aggregated,
            }
        )


class ICEAPlusCalibrateView(APIView):
    permission_classes = [ICEABackwardCompatiblePermission, IsAdminUser]

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
