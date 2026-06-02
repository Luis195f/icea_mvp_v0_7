from __future__ import annotations

import hashlib

import pandas as pd
from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from .evidence import build_training_evidence_metadata, summarize_model_evidence
from .engine import ICEAEngine, compute_basic_summary, stable_json_dumps
from .ml import train_xgb_regressor
from .models import ICEAComputation, ModelArtifact
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

    def post(self, request):
        ser = TrainRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        df = pd.DataFrame(payload["dataset"])
        result = train_xgb_regressor(
            df,
            features=payload["features"],
            target=payload["target"],
            model_dir=settings.ICEA_MODEL_DIR,
            params=payload.get("params") or None,
        )
        evidence_pack = build_training_evidence_metadata(
            raw_df=df,
            model_df=df,
            features=result.features,
            target=result.target,
            dataset_grain="external_payload",
            metrics=result.metrics,
            temporal_guardrail_status="not_evaluated_external_payload",
            case_mix_spec=payload.get("case_mix_spec"),
        )
        result.metrics["evidence_pack"] = evidence_pack

        artifact = ModelArtifact.objects.create(
            name=payload.get("name", "icea-xgb"),
            version=payload.get("version", "v1"),
            target=result.target,
            features=result.features,
            model_type="xgboost",
            model_path=result.model_path,
            metrics=result.metrics,
        )
        return Response(ModelArtifactSerializer(artifact).data, status=201)


class ICEAComputeView(APIView):
    """Compute ICEA (and optional group contributions) for a batch of rows."""

    def post(self, request):
        ser = ComputeRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        artifact = ModelArtifact.objects.get(id=payload["model_id"])
        evidence = summarize_model_evidence(artifact)
        if not evidence.defensible:
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

        group_map = payload.get("group_map")

        # Background for SHAP: sample the batch (cheap and deterministic)
        background = df.head(min(len(df), 200)).copy()

        shap_mode = request.query_params.get("shap_mode", "interventional")
        engine = ICEAEngine(
            artifact.model_path,
            background=background,
            shap_mode=shap_mode,
        )

        result = engine.compute(
            df,
            features=features,
            nurse_cols=nurse_cols,
            group_map=group_map,
        )

        # Traceability / audit
        request_hash = hashlib.sha256(stable_json_dumps(payload).encode("utf-8")).hexdigest()
        summary = {
            "icea": compute_basic_summary(result.icea),
            "predictions": compute_basic_summary(result.predictions),
            "base_value": float(result.base_value),
        }
        # Add group summaries
        group_summaries = {}
        for k, v in result.contributions.items():
            group_summaries[k] = compute_basic_summary(v)
        summary["groups"] = group_summaries

        ICEAComputation.objects.create(
            model=artifact,
            rows=len(df),
            summary=summary,
            request_hash=request_hash,
        )

        return Response(
            {
                "model": ModelArtifactSerializer(artifact).data,
                "summary": summary,
                "rows": len(df),
                "results": {
                    "predictions": result.predictions,
                    "base_value": float(result.base_value),
                    "icea": result.icea,
                    "contributions": result.contributions,
                },
            }
        )
