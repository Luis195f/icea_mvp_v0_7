# Generated manually for the ICEA MVP (Django 5).

from __future__ import annotations

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Hospital",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
            ],
        ),
        migrations.CreateModel(
            name="Unit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                (
                    "hospital",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="units", to="icea_core.hospital"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ModelArtifact",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(default="icea-xgb", max_length=128)),
                ("version", models.CharField(default="v1", max_length=64)),
                ("target", models.CharField(default="delta_ri", max_length=128)),
                ("features", models.JSONField(default=list)),
                ("model_type", models.CharField(default="xgboost", max_length=64)),
                ("model_path", models.CharField(max_length=512)),
                ("metrics", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [models.Index(fields=["name", "version"], name="icea_core_m_name_vers_8a8c88_idx")],
            },
        ),
        migrations.CreateModel(
            name="PatientEpisode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_patient_id", models.CharField(blank=True, default="", max_length=128)),
                ("admission_date", models.DateTimeField()),
                ("discharge_date", models.DateTimeField(blank=True, null=True)),
                ("ri_initial", models.FloatField(help_text="Baseline clinical status at admission")),
                ("ri_final", models.FloatField(help_text="Clinical status at discharge/endpoint")),
                (
                    "unit",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="episodes", to="icea_core.unit"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ICEAComputation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("rows", models.IntegerField(default=0)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("request_hash", models.CharField(blank=True, default="", max_length=64)),
                (
                    "model",
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="computations", to="icea_core.modelartifact"),
                ),
            ],
        ),
    ]
