from django.contrib import admin

from icea_pipeline.models import (
    AuditEvent,
    FHIRWritebackRecord,
    RawFHIRResource,
    TrainingRun,
)

from .models import (
    Hospital,
    ICEAComputation,
    ICEAPlusComputation,
    ICEAPlusFormulaVersion,
    ModelArtifact,
    PatientEpisode,
    Unit,
)


@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("id", "hospital", "name")
    search_fields = ("name", "hospital__name")


@admin.register(PatientEpisode)
class PatientEpisodeAdmin(admin.ModelAdmin):
    list_display = ("id", "unit", "admission_date", "discharge_date")
    list_filter = ("unit",)
    readonly_fields = ("id", "admission_date", "discharge_date", "ri_initial", "ri_final")
    fields = ("id", "unit", "admission_date", "discharge_date", "ri_initial", "ri_final")


@admin.register(ModelArtifact)
class ModelArtifactAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "version", "model_type", "governance_status", "created_at")
    list_filter = ("model_type", "governance_status", "created_at")
    search_fields = ("id", "name", "version", "governance_status")
    readonly_fields = ("id", "created_at", "features", "metrics")
    fields = ("id", "name", "version", "target", "model_type", "governance_status", "created_at", "features", "metrics")


@admin.register(ICEAComputation)
class ICEAComputationAdmin(admin.ModelAdmin):
    list_display = ("id", "model", "rows", "created_at", "request_hash")
    list_filter = ("created_at",)
    search_fields = ("id", "model__id", "request_hash")
    readonly_fields = ("id", "model", "created_at", "rows", "summary", "request_hash")
    fields = ("id", "model", "created_at", "rows", "summary", "request_hash")


@admin.register(ICEAPlusFormulaVersion)
class ICEAPlusFormulaVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "version", "label", "status", "is_active", "updated_at")
    list_filter = ("status", "is_active")
    search_fields = ("version", "label", "status", "protocol_hash")
    readonly_fields = ("id", "protocol_hash", "created_at", "updated_at")


@admin.register(ICEAPlusComputation)
class ICEAPlusComputationAdmin(admin.ModelAdmin):
    list_display = ("id", "formula_version", "model", "grain", "rows", "status", "created_at", "request_hash")
    list_filter = ("formula_version", "grain", "status", "created_at")
    search_fields = ("id", "model__id", "formula_version", "request_hash", "status")
    readonly_fields = ("id", "created_at", "model", "rows", "summary", "request_hash")
    fields = ("id", "created_at", "formula_version", "model", "grain", "rows", "status", "summary", "request_hash")


@admin.register(RawFHIRResource)
class RawFHIRResourceAdmin(admin.ModelAdmin):
    list_display = ("id", "episode_id", "resource_type", "payload_sha256", "validation_ok", "validation_version", "ingested_at")
    list_filter = ("resource_type", "validation_ok", "validation_version", "ingested_at")
    search_fields = ("id", "payload_sha256", "resource_type")
    readonly_fields = (
        "id",
        "episode",
        "resource_type",
        "payload_sha256",
        "validation_ok",
        "validation_issues",
        "validation_profile",
        "validation_version",
        "ingested_at",
    )
    fields = readonly_fields


@admin.register(FHIRWritebackRecord)
class FHIRWritebackRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "episode_id", "model_id", "resource_type", "attempted_writeback", "writeback_ok")
    list_filter = ("resource_type", "attempted_writeback", "writeback_ok", "created_at")
    search_fields = ("id", "model_id", "resource_type")
    readonly_fields = ("id", "created_at", "episode", "model_id", "resource_type", "attempted_writeback", "writeback_ok")
    fields = readonly_fields


@admin.register(TrainingRun)
class TrainingRunAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "dataset_rows", "model_artifact_id")
    list_filter = ("created_at",)
    search_fields = ("id", "model_artifact_id")
    readonly_fields = ("id", "created_at", "dataset_rows", "model_artifact_id")
    fields = readonly_fields


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "event_type", "context", "payload_sha256", "chain_hash")
    list_filter = ("event_type", "created_at")
    search_fields = ("id", "event_type", "context", "payload_sha256", "chain_hash")
    readonly_fields = ("id", "created_at", "event_type", "context", "payload_sha256", "prev_hash", "chain_hash", "hmac_sig")
    fields = readonly_fields
