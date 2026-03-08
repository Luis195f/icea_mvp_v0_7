from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("icea_pipeline", "0005_v0_6_0_entity_change_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="CausalDiscoveryRun",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.CharField(max_length=128, blank=True, default="")),
                ("method", models.CharField(max_length=64, default="pc")),
                ("alpha", models.FloatField(default=0.05)),
                ("max_cond_set", models.IntegerField(default=2)),
                ("grain", models.CharField(max_length=32, default="episode")),
                ("variables", models.JSONField(default=list)),
                ("spec", models.JSONField(default=dict)),
                ("result", models.JSONField(default=dict)),
            ],
            options={
                "indexes": [models.Index(fields=["created_at", "method"], name="icea_cdisc_created_8d2dd2_idx")],
            },
        ),
        migrations.CreateModel(
            name="CounterfactualSimulationRun",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.CharField(max_length=128, blank=True, default="")),
                ("spec", models.JSONField(default=dict)),
                ("result", models.JSONField(default=dict)),
                (
                    "causal_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="icea_pipeline.causalrun",
                    ),
                ),
                (
                    "predictive_model",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="icea_core.modelartifact",
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["created_at"], name="icea_csim_created_3d9c0a_idx")],
            },
        ),
        migrations.CreateModel(
            name="FederatedRound",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("name", models.CharField(max_length=128, default="federated-round")),
                ("status", models.CharField(max_length=32, default="open")),
                ("protocol_spec", models.JSONField(default=dict)),
                ("ensemble_spec", models.JSONField(default=dict, blank=True)),
                (
                    "aggregated_model",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="icea_core.modelartifact",
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["created_at", "status"], name="icea_fedro_created_2a61ce_idx")],
            },
        ),
        migrations.CreateModel(
            name="FederatedClientUpdate",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("client_id", models.CharField(max_length=128, default="")),
                ("n_rows", models.IntegerField(default=0)),
                ("signature_ok", models.BooleanField(default=False)),
                ("meta", models.JSONField(default=dict, blank=True)),
                (
                    "model_artifact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="icea_core.modelartifact",
                    ),
                ),
                (
                    "round",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="updates",
                        to="icea_pipeline.federatedround",
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["created_at", "client_id"], name="icea_fedup_created_4f2b16_idx")],
            },
        ),
    ]
