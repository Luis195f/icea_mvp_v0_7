from __future__ import annotations

from rest_framework import serializers


class ICEAPlusScoreRequestSerializer(serializers.Serializer):
    model_id = serializers.UUIDField(required=True)
    grain = serializers.ChoiceField(choices=["episode", "window"], required=False, default="episode")
    from_db = serializers.BooleanField(required=False, default=True)
    rows = serializers.ListField(child=serializers.DictField(), required=False)
    reference_rows = serializers.ListField(child=serializers.DictField(), required=False)
    episode_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    unit_id = serializers.IntegerField(required=False)
    date_from = serializers.DateTimeField(required=False)
    date_to = serializers.DateTimeField(required=False)
    formula_version = serializers.CharField(required=False, allow_blank=True, default="")
    nurse_cols = serializers.ListField(child=serializers.CharField(), required=False)
    outcome_goal = serializers.ChoiceField(
        choices=["higher_is_better", "lower_is_better", "adverse_event"],
        required=False,
    )
    causal_run_id = serializers.UUIDField(required=False)
    causal_spec = serializers.DictField(required=False)
    baseline_model_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        from_db = bool(attrs.get("from_db", True))
        if not from_db and not attrs.get("rows"):
            raise serializers.ValidationError({"rows": "rows is required when from_db=false"})
        return attrs


class ICEAPlusAggregateQuerySerializer(serializers.Serializer):
    model_id = serializers.UUIDField(required=True)
    grain = serializers.ChoiceField(choices=["episode", "window"], required=False, default="episode")
    group_by = serializers.ChoiceField(
        choices=["patient", "episode", "window", "shift", "nurse", "team", "unit", "date"],
        required=False,
        default="unit",
    )
    unit_id = serializers.IntegerField(required=False)
    date_from = serializers.DateTimeField(required=False)
    date_to = serializers.DateTimeField(required=False)
    formula_version = serializers.CharField(required=False, allow_blank=True, default="")
    causal_run_id = serializers.UUIDField(required=False)
    baseline_model_id = serializers.UUIDField(required=False)
    outcome_goal = serializers.ChoiceField(
        choices=["higher_is_better", "lower_is_better", "adverse_event"],
        required=False,
    )


class ICEAPlusCalibrateSerializer(serializers.Serializer):
    version = serializers.CharField(required=False, default="icea_plus_v1")
    spec = serializers.DictField(required=False, default=dict)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    activate = serializers.BooleanField(required=False, default=True)


class ICEAPlusFollowupIngestSerializer(serializers.Serializer):
    episode_id = serializers.IntegerField(required=True)
    model_id = serializers.UUIDField(required=True)
    formula_version = serializers.CharField(required=False, allow_blank=True, default="")


class ICEAPlusFollowupRescoreSerializer(serializers.Serializer):
    episode_id = serializers.IntegerField(required=True)
    model_id = serializers.UUIDField(required=True)
    formula_version = serializers.CharField(required=False, allow_blank=True, default="")
    outcome_goal = serializers.ChoiceField(
        choices=["higher_is_better", "lower_is_better", "adverse_event"],
        required=False,
    )
    causal_run_id = serializers.UUIDField(required=False)
    causal_spec = serializers.DictField(required=False)
    baseline_model_id = serializers.UUIDField(required=False)
    nurse_cols = serializers.ListField(child=serializers.CharField(), required=False)


class ICEAPlusFollowupStatusQuerySerializer(serializers.Serializer):
    episode_id = serializers.IntegerField(required=True)
    model_id = serializers.UUIDField(required=True)
    formula_version = serializers.CharField(required=False, allow_blank=True, default="")


class ICEAPlusWritebackSummaryQuerySerializer(serializers.Serializer):
    model_id = serializers.UUIDField(required=True)
    formula_version = serializers.CharField(required=False, allow_blank=True, default="")
    group_by = serializers.ChoiceField(choices=["unit", "team", "shift"], required=False, default="unit")
    unit_id = serializers.IntegerField(required=False)
    date_from = serializers.DateTimeField(required=False)
    date_to = serializers.DateTimeField(required=False)


class ICEAPlusWritebackPatientQuerySerializer(serializers.Serializer):
    episode_id = serializers.IntegerField(required=True)
    model_id = serializers.UUIDField(required=True)
    formula_version = serializers.CharField(required=False, allow_blank=True, default="")
