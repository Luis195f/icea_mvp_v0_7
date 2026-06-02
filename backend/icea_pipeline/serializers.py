from __future__ import annotations

from rest_framework import serializers


class IngestFHIRSerializer(serializers.Serializer):
    episode_id = serializers.IntegerField()
    patient_id = serializers.CharField(required=False, allow_blank=True, default="")
    encounter_id = serializers.CharField(required=False, allow_blank=True, default="")
    mode = serializers.ChoiceField(choices=["patient", "encounter"], required=False, default="patient")
    resources = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=["Observation", "Condition", "Procedure"],
    )


class NormalizeFHIRSerializer(serializers.Serializer):
    episode_id = serializers.IntegerField()
    truncate = serializers.BooleanField(required=False, default=False)


class BuildDatasetSerializer(serializers.Serializer):
    episode_id = serializers.IntegerField(required=False)
    truncate = serializers.BooleanField(required=False, default=False)


class TrainFromDBSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, default="icea-xgb")
    version = serializers.CharField(required=False, default="v0.7.0")
    target = serializers.CharField(required=False, default="delta_ri")
    grain = serializers.ChoiceField(choices=["auto", "episode", "window"], required=False, default="auto")
    case_mix_spec = serializers.DictField(required=False)


class RosterUploadSerializer(serializers.Serializer):
    unit_id = serializers.IntegerField()
    csv = serializers.CharField(help_text="CSV text with header: start_dt,end_dt,rn_count,na_count,patient_census")
    source = serializers.CharField(required=False, default="csv")


class CausalRunSerializer(serializers.Serializer):
    spec = serializers.DictField(required=True)


class RiskAssessmentWritebackSerializer(serializers.Serializer):
    episode_id = serializers.IntegerField(required=True)
    model_id = serializers.UUIDField(required=True)
    writeback = serializers.BooleanField(required=False, default=False)
    # Retained for compatibility; response suppresses individual prediction values in shadow mode.
    conformal = serializers.BooleanField(required=False, default=False)


class ConformalPredictSerializer(serializers.Serializer):
    episode_id = serializers.IntegerField(required=True)
    model_id = serializers.UUIDField(required=True)
    alpha = serializers.FloatField(required=False, default=0.05, min_value=0.001, max_value=0.5)


class BuildWindowsSerializer(serializers.Serializer):
    episode_id = serializers.IntegerField(required=False)
    truncate = serializers.BooleanField(required=False, default=False)
    window_hours = serializers.IntegerField(required=False, default=12, min_value=1, max_value=168)
    align = serializers.ChoiceField(choices=["admission", "shift"], required=False, default="admission")
    # v0.5.1: deterministic RI boundary selection within each window
    ri_boundary = serializers.ChoiceField(choices=["first_last", "nearest"], required=False, default="first_last")
    ri_boundary_tol_minutes = serializers.IntegerField(required=False, default=60, min_value=0, max_value=720)
    # v0.5.3: optional follow-up horizon (hours) for delta_ri computation (time-zero -> time-zero + follow-up)
    follow_up_hours = serializers.IntegerField(required=False, min_value=1, max_value=720)


class GovernanceDecisionSerializer(serializers.Serializer):
    decision_type = serializers.ChoiceField(choices=["override", "approve", "reject", "note"], required=False, default="override")
    actor = serializers.CharField(required=False, allow_blank=True, default="")
    rationale = serializers.CharField(required=False, allow_blank=True, default="")

    model_id = serializers.UUIDField(required=False)
    episode_id = serializers.IntegerField(required=False)
    causal_run_id = serializers.UUIDField(required=False)
    writeback_id = serializers.UUIDField(required=False)

    payload = serializers.DictField(required=False, default=dict)


# -------------------------
# v0.7 additions
# -------------------------


class CausalDiscoverSerializer(serializers.Serializer):
    """Request for DAG discovery suggestions."""

    grain = serializers.ChoiceField(choices=["episode", "window"], required=False, default="episode")
    variables = serializers.ListField(child=serializers.CharField(), required=True)
    outcome = serializers.CharField(required=False, allow_blank=True, default="")
    target = serializers.CharField(required=False, allow_blank=True, default="")
    treatment = serializers.CharField(required=False, allow_blank=True, default="")
    target_trial = serializers.DictField(required=False)
    alpha = serializers.FloatField(required=False, default=0.05, min_value=0.001, max_value=0.5)
    max_cond_set = serializers.IntegerField(required=False, default=2, min_value=0, max_value=10)
    forbid_edges = serializers.ListField(child=serializers.ListField(child=serializers.CharField()), required=False, default=list)

    # Optional: discover using DB dataset (default). If false, user can provide rows directly.
    from_db = serializers.BooleanField(required=False, default=True)
    rows = serializers.ListField(child=serializers.DictField(), required=False)

    unit_id = serializers.IntegerField(required=False)


class CausalSimulateSerializer(serializers.Serializer):
    """Counterfactual simulation request."""

    # Either provide a full spec, or reference a run_id
    spec = serializers.DictField(required=False)
    run_id = serializers.UUIDField(required=False)

    model_id = serializers.UUIDField(required=False)

    scenarios = serializers.ListField(
        child=serializers.DictField(),
        required=True,
        help_text="List of scenarios: {name, set:{}, delta:{}}",
    )


class FederatedRoundStartSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, default="federated-round")
    protocol_spec = serializers.DictField(required=True)


class FederatedSubmitUpdateSerializer(serializers.Serializer):
    client_id = serializers.CharField(required=True)
    n_rows = serializers.IntegerField(required=False, default=0)

    # Provide either an already-stored model artifact id, or a pickled artifact as base64.
    model_artifact_id = serializers.UUIDField(required=False)
    artifact_b64 = serializers.CharField(required=False, allow_blank=True, default="")
    artifact_format = serializers.ChoiceField(choices=["pickle"], required=False, default="pickle")

    meta = serializers.DictField(required=False, default=dict)


class FederatedAggregateSerializer(serializers.Serializer):
    # optional: override weighting
    method = serializers.ChoiceField(choices=["weighted_ensemble"], required=False, default="weighted_ensemble")
