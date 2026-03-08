# Generated manually for ICEA Platform MVP v0.3.0

from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("icea_core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RawFHIRResource",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("resource_type", models.CharField(max_length=64)),
                ("resource_id", models.CharField(max_length=128)),
                ("last_updated", models.DateTimeField(null=True, blank=True)),
                ("payload", models.JSONField()),
                ("payload_sha256", models.CharField(max_length=64, blank=True, default="")),
                ("ingested_at", models.DateTimeField(auto_now_add=True)),
                (
                    "episode",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fhir_raw",
                        to="icea_core.patientepisode",
                    ),
                ),
            ],
            options={
                "unique_together": {("episode", "resource_type", "resource_id")},
            },
        ),
        migrations.CreateModel(
            name="NormalizedObservation",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("code_system", models.CharField(max_length=128, blank=True, default="")),
                ("code", models.CharField(max_length=128, blank=True, default="")),
                ("display", models.CharField(max_length=255, blank=True, default="")),
                ("value_num", models.FloatField(null=True, blank=True)),
                ("value_text", models.CharField(max_length=255, blank=True, default="")),
                ("unit", models.CharField(max_length=64, blank=True, default="")),
                ("effective_dt", models.DateTimeField(null=True, blank=True)),
                (
                    "episode",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="obs",
                        to="icea_core.patientepisode",
                    ),
                ),
                (
                    "source_resource",
                    models.ForeignKey(
                        null=True,
                        blank=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="icea_pipeline.rawfhirresource",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="NormalizedCondition",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("code_system", models.CharField(max_length=128, blank=True, default="")),
                ("code", models.CharField(max_length=128, blank=True, default="")),
                ("display", models.CharField(max_length=255, blank=True, default="")),
                ("onset_dt", models.DateTimeField(null=True, blank=True)),
                ("recorded_dt", models.DateTimeField(null=True, blank=True)),
                ("clinical_status", models.CharField(max_length=64, blank=True, default="")),
                (
                    "episode",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conditions",
                        to="icea_core.patientepisode",
                    ),
                ),
                (
                    "source_resource",
                    models.ForeignKey(
                        null=True,
                        blank=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="icea_pipeline.rawfhirresource",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="NormalizedProcedure",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("code_system", models.CharField(max_length=128, blank=True, default="")),
                ("code", models.CharField(max_length=128, blank=True, default="")),
                ("display", models.CharField(max_length=255, blank=True, default="")),
                ("performed_dt", models.DateTimeField(null=True, blank=True)),
                ("performer_role", models.CharField(max_length=64, blank=True, default="")),
                (
                    "episode",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="procedures",
                        to="icea_core.patientepisode",
                    ),
                ),
                (
                    "source_resource",
                    models.ForeignKey(
                        null=True,
                        blank=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="icea_pipeline.rawfhirresource",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="EpisodeFeatureRow",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("features", models.JSONField(default=dict)),
                ("target", models.JSONField(default=dict)),
                ("built_at", models.DateTimeField(auto_now_add=True)),
                (
                    "episode",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="feature_row",
                        to="icea_core.patientepisode",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="TrainingRun",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("dataset_rows", models.IntegerField(default=0)),
                ("notes", models.CharField(max_length=255, blank=True, default="")),
                ("model_artifact_id", models.UUIDField(null=True, blank=True)),
            ],
        ),
        migrations.AddIndex(
            model_name="rawfhirresource",
            index=models.Index(fields=["episode", "resource_type"], name="icea_pipel_episode_9f8c3b_idx"),
        ),
        migrations.AddIndex(
            model_name="rawfhirresource",
            index=models.Index(fields=["resource_type", "resource_id"], name="icea_pipel_resourc_9c3c22_idx"),
        ),
        migrations.AddIndex(
            model_name="normalizedobservation",
            index=models.Index(fields=["episode", "code_system", "code"], name="icea_pipel_episode_7db05c_idx"),
        ),
        migrations.AddIndex(
            model_name="normalizedcondition",
            index=models.Index(fields=["episode", "code_system", "code"], name="icea_pipel_episode_0c2444_idx"),
        ),
        migrations.AddIndex(
            model_name="normalizedprocedure",
            index=models.Index(fields=["episode", "performer_role"], name="icea_pipel_episode_126b8c_idx"),
        ),
        migrations.AddIndex(
            model_name="normalizedprocedure",
            index=models.Index(fields=["episode", "code_system", "code"], name="icea_pipel_episode_6a0f13_idx"),
        ),
    ]
