from __future__ import annotations

import hashlib

import pandas as pd
from django.conf import settings
from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView
from icea_pipeline.temporal import validate_temporal_frame

from .api_security import append_icea_api_audit
from .evidence import build_training_evidence_metadata, summarize_model_evidence
from .engine import stable_json_dumps
from .ml import train_xgb_regressor
from .models import ICEAComputation, ModelArtifact
from .permissions import ICEAResearcherPermission, ICEATrainingPermission
from .serializers import (
    ComputeRequestSerializer,
    ModelArtifactSerializer,
    TrainRequestSerializer,
)


class HealthView(APIView):
    def get(self, request):
        return Response(
            {
                "status": "ok",
                "service": "ICEA Platform MVP",
                "version": settings.SPECTACULAR_SETTINGS.get("VERSION", "0.0.0"),
            }
        )


class ModelListView(APIView):
    permission_classes = [ICEAResearcherPermission]
    throttle_scope = "icea_read"

    def get(self, request):
        qs = ModelArtifact.objects.order_by("-created_at")
        return Response(ModelArtifactSerializer(qs, many=True).data)


class ModelTrainView(APIView):
    """Train and register a new XGBoost model.

    Payload fields:
      - dataset: list[dict]
      - target: str (default: delta_ri)
      - features: list[str]
      - name/version (optional)
      - params (optional): XGBRegressor kwargs
    """

    permission_classes = [ICEATrainingPermission]
    throttle_scope = "icea_train"

    def post(self, request):
        ser = TrainRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        df = pd.DataFrame(payload["dataset"])
        append_icea_api_audit(
            request=request,
            event_type="model_train_requested",
            context="models/train",
            action="train",
            row_count=int(len(df)),
            status="requested",
        )
        evidence_model_df = df.reindex(columns=list(payload["features"]) + [str(payload["target"])])
        temporal_issues = validate_temporal_frame(
            df,
            feature_names=list(payload["features"]),
            target=str(payload["target"]),
        )
        temporal_guardrail_status = "temporal_guardrails_passed"
        temporal_guardrail_warnings: list[str] = []
        if temporal_issues:
            temporal_guardrail_status = str(temporal_issues[0][1].status or "external_payload_temporal_not_defensible")
            temporal_guardrail_warnings = sorted(
                {warning for _, issue in temporal_issues for warning in issue.warnings}
            )

        result = train_xgb_regressor(
            df,
            features=payload["features"],
            target=payload["target"],
            model_dir=settings.ICEA_MODEL_DIR,
            params=payload.get("params") or None,
        )
        evidence_pack = build_training_evidence_metadata(
            raw_df=df,
            model_df=evidence_model_df,
            features=result.features,
            target=result.target,
            dataset_grain="external_payload",
            metrics=result.metrics,
            temporal_guardrail_status=temporal_guardrail_status,
            temporal_guardrail_warnings=temporal_guardrail_warnings,
            case_mix_spec=payload.get("case_mix_spec"),
        )
        result.metrics["evidence_pack"] = evidence_pack

        with transaction.atomic():
            artifact = ModelArtifact.objects.create(
                name=payload.get("name", "icea-xgb"),
                version=payload.get("version", "v1"),
                target=result.target,
                features=result.features,
                model_type="xgboost",
                model_path=result.model_path,
                metrics=result.metrics,
            )
            evidence = summarize_model_evidence(artifact)
            if not evidence.defensible:
                artifact.governance_status = "quarantine"
                artifact.save(update_fields=["governance_status"])
                evidence = summarize_model_evidence(artifact)
            append_icea_api_audit(
                request=request,
                event_type="model_train_completed",
                context="models/train",
                action="train",
                model_id=str(artifact.id),
                row_count=int(len(df)),
                evidence_status=evidence.evidence_status,
                status="completed" if evidence.defensible else "quarantine",
            )
        return Response(ModelArtifactSerializer(artifact).data, status=201)


class ICEAComputeView(APIView):
    """Retain the legacy compute contract without emitting individual outputs."""

    permission_classes = [ICEAResearcherPermission]
    throttle_scope = "icea_compute"

    def post(self, request):
        ser = ComputeRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        artifact = ModelArtifact.objects.get(id=payload["model_id"])
        evidence = summarize_model_evidence(artifact)
        append_icea_api_audit(
            request=request,
            event_type="legacy_compute_requested",
            context="icea/compute",
            action="compute",
            model_id=str(artifact.id),
            row_count=int(len(payload["data"])),
            evidence_status=evidence.evidence_status,
            status="requested",
        )
        if not evidence.defensible:
            append_icea_api_audit(
                request=request,
                event_type="legacy_compute_blocked_model_evidence",
                context="icea/compute",
                action="compute",
                model_id=str(artifact.id),
                evidence_status=evidence.evidence_status,
                error_code="model_not_defensible",
                status="blocked",
            )
            return Response(
                {
                    "detail": "model_not_defensible",
                    "model_id": str(artifact.id),
                    "non_individual_use": True,
                    "shadow_mode": True,
                    **evidence.to_dict(),
                },
                status=400,
            )
        df = pd.DataFrame(payload["data"])

        features = payload.get("features") or artifact.features

        # Determine nurse columns
        nurse_cols = payload.get("nurse_cols")
        if not nurse_cols:
            # MVP heuristic: any column prefixed with nurse_ or nic_ counts as nursing exposure
            nurse_cols = [c for c in features if c.startswith("nurse_") or c.startswith("nic_")]
            if not nurse_cols:
                return Response(
                    {
                        "detail": "nurse_cols not provided and none inferred from features (prefix nurse_ or nic_).",
                        "hint": "Provide nurse_cols explicitly in request.",
                    },
                    status=400,
                )

        # Legacy compute is retained only as a governed audit surface. Models
        # approved for shadow aggregate research cannot emit row-level outputs.
        request_hash = hashlib.sha256(stable_json_dumps(payload).encode("utf-8")).hexdigest()
        warnings = ["individual_outputs_suppressed", "legacy_compute_redacted"]
        summary = {
            "status": "shadow_only",
            "rows_requested": int(len(df)),
            "score_summary": None,
            "score_summary_redacted": True,
            "warnings": warnings,
        }

        ICEAComputation.objects.create(
            model=artifact,
            rows=len(df),
            summary=summary,
            request_hash=request_hash,
        )
        append_icea_api_audit(
            request=request,
            event_type="legacy_compute_redacted",
            context="icea/compute",
            action="compute",
            model_id=str(artifact.id),
            row_count=int(len(df)),
            request_hash=request_hash,
            status="shadow_only",
            suppressed=True,
        )

        return Response(
            {
                "model": ModelArtifactSerializer(artifact).data,
                "summary": summary,
                "rows": len(df),
                "results": {},
                "status": "shadow_only",
                "detail": "legacy_compute_redacted",
                "model_evidence_status": evidence.evidence_status,
                "defensible": evidence.defensible,
                "intended_use": evidence.intended_use,
                "shadow_mode": True,
                "non_individual_use": True,
                "score_summary": None,
                "score_summary_redacted": True,
                "warnings": warnings,
            }
        )
