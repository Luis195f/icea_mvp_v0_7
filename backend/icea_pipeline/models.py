from __future__ import annotations

import hashlib
import json
import uuid

from django.db import models

from icea_pipeline.fields import EncryptedJSONField

from icea_core.models import PatientEpisode


class RawFHIRResource(models.Model):
    """Raw FHIR resources ingested from the hospital FHIR server.

    Pilot design choice:
      - Store the raw JSON for traceability and replay.
      - Keep patient identifiers pseudonymized at the episode level.

    NOTE: In production, consider encrypt-at-rest for payloads + field-level access controls.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    episode = models.ForeignKey(PatientEpisode, on_delete=models.CASCADE, related_name="fhir_raw")

    resource_type = models.CharField(max_length=64)
    resource_id = models.CharField(max_length=128)
    last_updated = models.DateTimeField(null=True, blank=True)

    # PHI hardening (v0.7.1): encrypt-at-rest transparently.
    # Backward compatible: legacy plaintext rows remain readable.
    payload = EncryptedJSONField()
    payload_sha256 = models.CharField(max_length=64, blank=True, default="")

    # v0.5.1: validation metadata (FHIR facade)
    validation_ok = models.BooleanField(default=True)
    validation_issues = models.JSONField(default=list, blank=True)
    validation_profile = models.CharField(max_length=128, blank=True, default="")
    validation_version = models.CharField(max_length=32, blank=True, default="v0.5.1")

    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("episode", "resource_type", "resource_id")
        indexes = [
            models.Index(fields=["episode", "resource_type"]),
            models.Index(fields=["resource_type", "resource_id"]),
        ]

    def save(self, *args, **kwargs):
        if not self.payload_sha256:
            dumped = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            self.payload_sha256 = hashlib.sha256(dumped.encode("utf-8")).hexdigest()
        super().save(*args, **kwargs)


class NormalizedObservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    episode = models.ForeignKey(PatientEpisode, on_delete=models.CASCADE, related_name="obs")

    code_system = models.CharField(max_length=128, blank=True, default="")  # e.g. LOINC
    code = models.CharField(max_length=128, blank=True, default="")
    display = models.CharField(max_length=255, blank=True, default="")

    # v0.5.1: semantic traceability (NNN -> SNOMED/LOINC mapping)
    source_code_system = models.CharField(max_length=128, blank=True, default="")
    source_code = models.CharField(max_length=128, blank=True, default="")
    source_display = models.CharField(max_length=255, blank=True, default="")

    value_num = models.FloatField(null=True, blank=True)
    value_text = models.CharField(max_length=255, blank=True, default="")
    unit = models.CharField(max_length=64, blank=True, default="")

    effective_dt = models.DateTimeField(null=True, blank=True)
    source_resource = models.ForeignKey(RawFHIRResource, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["episode", "code_system", "code"])]


class NormalizedCondition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    episode = models.ForeignKey(PatientEpisode, on_delete=models.CASCADE, related_name="conditions")

    code_system = models.CharField(max_length=128, blank=True, default="")  # e.g. SNOMED
    code = models.CharField(max_length=128, blank=True, default="")
    display = models.CharField(max_length=255, blank=True, default="")

    # v0.5.1: semantic traceability (NANDA -> SNOMED mapping)
    source_code_system = models.CharField(max_length=128, blank=True, default="")
    source_code = models.CharField(max_length=128, blank=True, default="")
    source_display = models.CharField(max_length=255, blank=True, default="")

    onset_dt = models.DateTimeField(null=True, blank=True)
    recorded_dt = models.DateTimeField(null=True, blank=True)
    clinical_status = models.CharField(max_length=64, blank=True, default="")

    source_resource = models.ForeignKey(RawFHIRResource, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["episode", "code_system", "code"])]


class NormalizedProcedure(models.Model):
    """Covers both clinical procedures and nursing interventions.

    In ICEA+, nursing interventions can be mapped from NIC -> SNOMED CT and represented as FHIR Procedure.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    episode = models.ForeignKey(PatientEpisode, on_delete=models.CASCADE, related_name="procedures")

    code_system = models.CharField(max_length=128, blank=True, default="")
    code = models.CharField(max_length=128, blank=True, default="")
    display = models.CharField(max_length=255, blank=True, default="")

    # v0.5.1: semantic traceability (NIC -> SNOMED mapping)
    source_code_system = models.CharField(max_length=128, blank=True, default="")
    source_code = models.CharField(max_length=128, blank=True, default="")
    source_display = models.CharField(max_length=255, blank=True, default="")

    performed_dt = models.DateTimeField(null=True, blank=True)

    performer_role = models.CharField(max_length=64, blank=True, default="")
    performer_actor_ref = models.CharField(max_length=128, blank=True, default="")
    performer_actor_type = models.CharField(max_length=64, blank=True, default="")
    is_nursing = models.BooleanField(default=False)
    nursing_label_method = models.CharField(max_length=32, blank=True, default="", help_text="deterministic|heuristic|unknown")
    source_resource = models.ForeignKey(RawFHIRResource, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [
            models.Index(fields=["episode", "performer_role"]),
            models.Index(fields=["episode", "is_nursing"]),
            models.Index(fields=["episode", "code_system", "code"]),
        ]


class EpisodeFeatureRow(models.Model):
    """Materialized analytic row for ML.

    This is the output of the dataset builder: one row per episode (or per episode-window).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    episode = models.OneToOneField(PatientEpisode, on_delete=models.CASCADE, related_name="feature_row")

    features = models.JSONField(default=dict)  # flattened feature dict
    target = models.JSONField(default=dict)  # supports multi-outcome

    schema_hash = models.CharField(max_length=64, blank=True, default="")
    feature_version = models.CharField(max_length=32, blank=True, default="v0.4")

    built_at = models.DateTimeField(auto_now_add=True)


class RosterShift(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unit = models.ForeignKey("icea_core.Unit", on_delete=models.CASCADE, related_name="roster_shifts")

    start_dt = models.DateTimeField()
    end_dt = models.DateTimeField()

    rn_count = models.IntegerField(default=0)
    na_count = models.IntegerField(default=0)
    patient_census = models.IntegerField(null=True, blank=True)

    source = models.CharField(max_length=64, blank=True, default="csv")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["unit", "start_dt", "end_dt"]) ]


class CausalSpec(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, default="default")
    spec = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class CausalRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    spec = models.ForeignKey(CausalSpec, null=True, blank=True, on_delete=models.SET_NULL)
    outcome = models.CharField(max_length=128, default="delta_ri")
    treatment = models.CharField(max_length=128, default="")
    n_rows = models.IntegerField(default=0)
    summary = models.JSONField(default=dict)


class DataQualitySnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    feature_version = models.CharField(max_length=32, blank=True, default="v0.4")
    schema_hash = models.CharField(max_length=64, blank=True, default="")
    report = models.JSONField(default=dict)


class FHIRWritebackRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    episode = models.ForeignKey(PatientEpisode, on_delete=models.CASCADE, related_name="writebacks")
    model_id = models.UUIDField()

    resource_type = models.CharField(max_length=64, default="RiskAssessment")
    payload = models.JSONField(default=dict)
    attempted_writeback = models.BooleanField(default=False)
    writeback_ok = models.BooleanField(default=False)
    writeback_response = models.JSONField(default=dict, blank=True)


class TrainingRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    dataset_rows = models.IntegerField(default=0)
    notes = models.CharField(max_length=255, blank=True, default="")

    model_artifact_id = models.UUIDField(null=True, blank=True)



# -------------------------
# v0.5 additions (trial-emulation + governance)
# -------------------------

class EpisodeWindow(models.Model):
    """Time window within an episode (e.g., nursing shift blocks).

    v0.5 enables episode-windows to emulate a target-trial with repeated exposure/outcome measurement.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    episode = models.ForeignKey(PatientEpisode, on_delete=models.CASCADE, related_name="windows")

    window_index = models.IntegerField(default=0)
    start_dt = models.DateTimeField()
    end_dt = models.DateTimeField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["episode", "start_dt", "end_dt"]),
            models.Index(fields=["episode", "window_index"]),
        ]
        unique_together = ("episode", "window_index")


class EpisodeWindowFeatureRow(models.Model):
    """Materialized analytic row for ML/causal at window grain."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    window = models.OneToOneField(EpisodeWindow, on_delete=models.CASCADE, related_name="feature_row")

    features = models.JSONField(default=dict)
    target = models.JSONField(default=dict)

    schema_hash = models.CharField(max_length=64, blank=True, default="")
    feature_version = models.CharField(max_length=32, blank=True, default="v0.5")

    built_at = models.DateTimeField(auto_now_add=True)


class AuditEvent(models.Model):
    """Cryptographic audit log with hash chaining (append-only by design).

    This is a *pragmatic* MVP-grade control aligned with the need for traceability and immutable audit logs.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    event_type = models.CharField(max_length=64)
    actor = models.CharField(max_length=128, blank=True, default="api")
    context = models.CharField(max_length=255, blank=True, default="")  # endpoint name/path

    payload_sha256 = models.CharField(max_length=64, blank=True, default="")

    prev_hash = models.CharField(max_length=64, blank=True, default="")
    chain_hash = models.CharField(max_length=64, blank=True, default="")
    hmac_sig = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["created_at", "event_type"])]


class GovernanceDecision(models.Model):
    """Human-in-the-loop decisions (override/approval/rejection) for high-risk outputs."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    decision_type = models.CharField(max_length=64, default="override")  # override|approve|reject|note
    actor = models.CharField(max_length=128, blank=True, default="")
    rationale = models.TextField(blank=True, default="")

    # Optional linkage
    model = models.ForeignKey("icea_core.ModelArtifact", null=True, blank=True, on_delete=models.SET_NULL)
    episode = models.ForeignKey(PatientEpisode, null=True, blank=True, on_delete=models.SET_NULL)
    causal_run = models.ForeignKey(CausalRun, null=True, blank=True, on_delete=models.SET_NULL)
    writeback = models.ForeignKey(FHIRWritebackRecord, null=True, blank=True, on_delete=models.SET_NULL)

    payload = models.JSONField(default=dict, blank=True)


class EntityChangeLog(models.Model):
    """Row-level change log for base entities / configuration.

    This provides an always-on lineage record even when optional history
    backends (e.g., django-simple-history) are not enabled.

    It is intentionally minimal and avoids storing clinical PHI.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    actor = models.CharField(max_length=128, blank=True, default="")
    model_label = models.CharField(max_length=128)  # e.g., icea_core.Hospital
    object_id = models.CharField(max_length=128)

    action = models.CharField(max_length=32, default="update")  # create|update|delete
    changes = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["created_at", "model_label"])]



# -------------------------
# v0.7 additions (certification-ready + final frontier)
# -------------------------


class CausalDiscoveryRun(models.Model):
    """Audit record for causal discovery suggestions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    actor = models.CharField(max_length=128, blank=True, default="")
    method = models.CharField(max_length=64, default="pc")
    alpha = models.FloatField(default=0.05)
    max_cond_set = models.IntegerField(default=2)

    grain = models.CharField(max_length=32, default="episode")
    variables = models.JSONField(default=list)
    spec = models.JSONField(default=dict)

    result = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=["created_at", "method"])]


class CounterfactualSimulationRun(models.Model):
    """Audit record for counterfactual simulations (digital twin)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    actor = models.CharField(max_length=128, blank=True, default="")

    causal_run = models.ForeignKey(CausalRun, null=True, blank=True, on_delete=models.SET_NULL)
    predictive_model = models.ForeignKey("icea_core.ModelArtifact", null=True, blank=True, on_delete=models.SET_NULL)

    spec = models.JSONField(default=dict)
    result = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=["created_at"]) ]


class FederatedRound(models.Model):
    """Federated learning round (protocol + submissions + aggregation)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    name = models.CharField(max_length=128, default="federated-round")
    status = models.CharField(max_length=32, default="open")  # open|aggregated|closed

    protocol_spec = models.JSONField(default=dict)
    ensemble_spec = models.JSONField(default=dict, blank=True)

    aggregated_model = models.ForeignKey("icea_core.ModelArtifact", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["created_at", "status"]) ]


class FederatedClientUpdate(models.Model):
    """A privacy-preserving client update: model artifact + metadata."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    round = models.ForeignKey(FederatedRound, on_delete=models.CASCADE, related_name="updates")
    client_id = models.CharField(max_length=128, default="")
    n_rows = models.IntegerField(default=0)

    model_artifact = models.ForeignKey("icea_core.ModelArtifact", null=True, blank=True, on_delete=models.SET_NULL)

    signature_ok = models.BooleanField(default=False)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["created_at", "client_id"]) ]
