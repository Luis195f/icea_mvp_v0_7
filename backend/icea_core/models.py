from __future__ import annotations

import hashlib
import json
import uuid

from django.db import models


class Hospital(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self) -> str:
        return self.name


class Unit(models.Model):
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="units")
    name = models.CharField(max_length=255)

    def __str__(self) -> str:
        return f"{self.hospital} / {self.name}"


class PatientEpisode(models.Model):
    """Minimal representation of an inpatient episode.

    In a real hospital integration, this is typically derived from the EHR/ADT system.
    For the MVP we keep it small: just timestamps + outcome proxy (e.g., Rothman Index).
    """

    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="episodes")
    external_patient_id = models.CharField(max_length=128, blank=True, default="")

    # --- FHIR linkage (optional, required for Encounter-centered ingest/writeback)
    fhir_patient_id = models.CharField(max_length=128, blank=True, default="")
    fhir_encounter_id = models.CharField(max_length=128, blank=True, default="")
    admission_date = models.DateTimeField()
    discharge_date = models.DateTimeField(null=True, blank=True)

    ri_initial = models.FloatField(help_text="Baseline clinical status at admission")
    ri_final = models.FloatField(help_text="Clinical status at discharge/endpoint")

    @property
    def delta_ri(self) -> float:
        return float(self.ri_final - self.ri_initial)


class ModelArtifact(models.Model):
    """Stores metadata for trained ML models used to compute ICEA."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, default="icea-xgb")
    version = models.CharField(max_length=64, default="v1")

    target = models.CharField(max_length=128, default="delta_ri")
    features = models.JSONField(default=list)
    model_type = models.CharField(max_length=64, default="xgboost")
    model_path = models.CharField(max_length=512)

    metrics = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["name", "version"])]

    def __str__(self) -> str:
        return f"{self.name}:{self.version} ({self.id})"


class ICEAComputation(models.Model):
    """Audit log for ICEA computations (traceability)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model = models.ForeignKey(ModelArtifact, on_delete=models.PROTECT, related_name="computations")
    created_at = models.DateTimeField(auto_now_add=True)

    rows = models.IntegerField(default=0)
    summary = models.JSONField(default=dict, blank=True)
    request_hash = models.CharField(max_length=64, blank=True, default="")

    def __str__(self) -> str:
        return f"ICEAComputation({self.id}) model={self.model_id} rows={self.rows}"


class ICEAPlusFormulaVersion(models.Model):
    """Versioned governance record for the official ICEA+ formula."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.CharField(max_length=64, unique=True, default="icea_plus_v1")
    label = models.CharField(max_length=255, default="ICEA+ v1 pilot composite index")
    status = models.CharField(max_length=32, default="pilot")
    is_active = models.BooleanField(default=True)

    spec = models.JSONField(default=dict, blank=True)
    protocol_hash = models.CharField(max_length=64, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["version", "is_active"])]

    def save(self, *args, **kwargs):
        dumped = json.dumps(self.spec or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.protocol_hash = hashlib.sha256(dumped.encode("utf-8")).hexdigest()
        if self.is_active:
            self.__class__.objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.version} ({self.status})"


class ICEAPlusComputation(models.Model):
    """Audit log for ICEA+ scoring and aggregation requests."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    formula_version = models.CharField(max_length=64, default="icea_plus_v1")
    model = models.ForeignKey(ModelArtifact, null=True, blank=True, on_delete=models.PROTECT, related_name="icea_plus_computations")
    grain = models.CharField(max_length=32, default="episode")
    rows = models.IntegerField(default=0)
    status = models.CharField(max_length=32, default="ok")
    summary = models.JSONField(default=dict, blank=True)
    request_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["created_at", "formula_version"])]

    def __str__(self) -> str:
        return f"ICEAPlusComputation({self.id}) formula={self.formula_version} rows={self.rows}"
