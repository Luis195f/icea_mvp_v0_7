# Generated for ICEA Platform MVP v0.4.0 (Django 5).

from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("icea_core", "0002_patientepisode_fhir_linkage"),
        ("icea_pipeline", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="normalizedprocedure",
            name="performer_actor_ref",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="normalizedprocedure",
            name="performer_actor_type",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="normalizedprocedure",
            name="is_nursing",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="normalizedprocedure",
            name="nursing_label_method",
            field=models.CharField(blank=True, default="", help_text="deterministic|heuristic|unknown", max_length=32),
        ),
        migrations.AddIndex(
            model_name="normalizedprocedure",
            index=models.Index(fields=["episode", "is_nursing"], name="icea_pipel_episode_is_nursing_idx"),
        ),
        migrations.AddField(
            model_name="episodefeaturerow",
            name="schema_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="episodefeaturerow",
            name="feature_version",
            field=models.CharField(blank=True, default="v0.4", max_length=32),
        ),
        migrations.CreateModel(
            name="RosterShift",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("start_dt", models.DateTimeField()),
                ("end_dt", models.DateTimeField()),
                ("rn_count", models.IntegerField(default=0)),
                ("na_count", models.IntegerField(default=0)),
                ("patient_census", models.IntegerField(blank=True, null=True)),
                ("source", models.CharField(blank=True, default="csv", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "unit",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="roster_shifts", to="icea_core.unit"),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="rostershift",
            index=models.Index(fields=["unit", "start_dt", "end_dt"], name="icea_pipel_roster_unit_dt_idx"),
        ),
        migrations.CreateModel(
            name="CausalSpec",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("name", models.CharField(default="default", max_length=128)),
                ("spec", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="CausalRun",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("outcome", models.CharField(default="delta_ri", max_length=128)),
                ("treatment", models.CharField(default="", max_length=128)),
                ("n_rows", models.IntegerField(default=0)),
                ("summary", models.JSONField(default=dict)),
                (
                    "spec",
                    models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, to="icea_pipeline.causalspec"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="DataQualitySnapshot",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("feature_version", models.CharField(blank=True, default="v0.4", max_length=32)),
                ("schema_hash", models.CharField(blank=True, default="", max_length=64)),
                ("report", models.JSONField(default=dict)),
            ],
        ),
        migrations.CreateModel(
            name="FHIRWritebackRecord",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("model_id", models.UUIDField()),
                ("resource_type", models.CharField(default="RiskAssessment", max_length=64)),
                ("payload", models.JSONField(default=dict)),
                ("attempted_writeback", models.BooleanField(default=False)),
                ("writeback_ok", models.BooleanField(default=False)),
                ("writeback_response", models.JSONField(blank=True, default=dict)),
                (
                    "episode",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="writebacks", to="icea_core.patientepisode"),
                ),
            ],
        ),
    ]
