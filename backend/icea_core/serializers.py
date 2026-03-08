from __future__ import annotations

from rest_framework import serializers

from .models import ICEAComputation, ModelArtifact


class ModelArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelArtifact
        fields = [
            "id",
            "name",
            "version",
            "target",
            "features",
            "model_type",
            "model_path",
            "metrics",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class TrainRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, default="icea-xgb")
    version = serializers.CharField(required=False, default="v1")
    target = serializers.CharField(required=False, default="delta_ri")
    features = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    dataset = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    params = serializers.DictField(required=False, default=dict)


class ComputeRequestSerializer(serializers.Serializer):
    model_id = serializers.UUIDField(required=True)
    data = serializers.ListField(child=serializers.DictField(), allow_empty=False)

    features = serializers.ListField(child=serializers.CharField(), required=False)
    nurse_cols = serializers.ListField(child=serializers.CharField(), required=False)
    group_map = serializers.DictField(required=False)


class ICEAComputationSerializer(serializers.ModelSerializer):
    model = ModelArtifactSerializer(read_only=True)

    class Meta:
        model = ICEAComputation
        fields = ["id", "model", "created_at", "rows", "summary", "request_hash"]
        read_only_fields = fields
