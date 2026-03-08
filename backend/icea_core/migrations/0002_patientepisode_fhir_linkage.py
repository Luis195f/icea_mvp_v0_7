# Generated for ICEA MVP v0.4 (Django 5).

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("icea_core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="patientepisode",
            name="fhir_patient_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="patientepisode",
            name="fhir_encounter_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
