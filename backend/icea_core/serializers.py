from __future__ import annotations

from rest_framework import serializers

from .evidence import summarize_model_evidence
from .models import ICEAComputation, ModelArtifact


class ModelArtifactSerializer(serializers.ModelSerializer):
    evidence_status = serializers.SerializerMethodField()
    defensible = serializers.SerializerMethodField()
    missing_evidence = serializers.SerializerMethodField()
    intended_use = serializers.SerializerMethodField()
    limitations = serializers.SerializerMethodField()
    temporal_spec_version = serializers.SerializerMethodField()
    case_mix_status = serializers.SerializerMethodField()
    calibration_status = serializers.SerializerMethodField()
    validation_status = serializers.SerializerMethodField()

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
            "evidence_status",
            "defensible",
            "missing_evidence",
            "intended_use",
            "limitations",
            "temporal_spec_version",
            "case_mix_status",
            "calibration_status",
            "validation_status",
        ]
        read_only_fields = ["id", "created_at"]

    def _evidence(self, obj):
        return summarize_model_evidence(obj)

    def get_evidence_status(self, obj):
        return self._evidence(obj).evidence_status

    def get_defensible(self, obj):
        return self._evidence(obj).defensible

    def get_missing_evidence(self, obj):
        return self._evidence(obj).missing_evidence

    def get_intended_use(self, obj):
        return self._evidence(obj).intended_use

    def get_limitations(self, obj):
        return self._evidence(obj).limitations

    def get_temporal_spec_version(self, obj):
        return self._evidence(obj).temporal_spec_version

    def get_case_mix_status(self, obj):
        return self._evidence(obj).case_mix_status

    def get_calibration_status(self, obj):
        return self._evidence(obj).calibration_status

    def get_validation_status(self, obj):
        return self._evidence(obj).validation_status


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
