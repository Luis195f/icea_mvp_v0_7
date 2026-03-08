# Generated for ICEA Platform MVP v0.5.0 (Django 5).

from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("icea_core", "0002_patientepisode_fhir_linkage"),
        ("icea_pipeline", "0002_v0_4_roster_causal_writeback"),
    ]

    operations = [
        migrations.CreateModel(
            name="EpisodeWindow",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("window_index", models.IntegerField(default=0)),
                ("start_dt", models.DateTimeField()),
                ("end_dt", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "episode",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="windows",
                        to="icea_core.patientepisode",
                    ),
                ),
            ],
            options={"unique_together": {("episode", "window_index")}},
        ),
        migrations.AddIndex(
            model_name="episodewindow",
            index=models.Index(fields=["episode", "start_dt", "end_dt"], name="icea_pipel_window_ep_dt_idx"),
        ),
        migrations.AddIndex(
            model_name="episodewindow",
            index=models.Index(fields=["episode", "window_index"], name="icea_pipel_window_ep_ix_idx"),
        ),
        migrations.CreateModel(
            name="EpisodeWindowFeatureRow",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("features", models.JSONField(default=dict)),
                ("target", models.JSONField(default=dict)),
                ("schema_hash", models.CharField(blank=True, default="", max_length=64)),
                ("feature_version", models.CharField(blank=True, default="v0.5", max_length=32)),
                ("built_at", models.DateTimeField(auto_now_add=True)),
                (
                    "window",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="feature_row",
                        to="icea_pipeline.episodewindow",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event_type", models.CharField(max_length=64)),
                ("actor", models.CharField(blank=True, default="api", max_length=128)),
                ("context", models.CharField(blank=True, default="", max_length=255)),
                ("payload_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("prev_hash", models.CharField(blank=True, default="", max_length=64)),
                ("chain_hash", models.CharField(blank=True, default="", max_length=64)),
                ("hmac_sig", models.CharField(blank=True, default="", max_length=64)),
            ],
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(fields=["created_at", "event_type"], name="icea_pipel_audit_ts_type_idx"),
        ),
        migrations.CreateModel(
            name="GovernanceDecision",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decision_type", models.CharField(default="override", max_length=64)),
                ("actor", models.CharField(blank=True, default="", max_length=128)),
                ("rationale", models.TextField(blank=True, default="")),
                ("payload", models.JSONField(blank=True, default=dict)),
                (
                    "causal_run",
                    models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, to="icea_pipeline.causalrun"),
                ),
                (
                    "episode",
                    models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, to="icea_core.patientepisode"),
                ),
                (
                    "model",
                    models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, to="icea_core.modelartifact"),
                ),
                (
                    "writeback",
                    models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, to="icea_pipeline.fhirwritebackrecord"),
                ),
            ],
        ),
    ]
