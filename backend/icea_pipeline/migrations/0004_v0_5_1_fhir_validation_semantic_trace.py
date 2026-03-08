from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("icea_pipeline", "0003_v0_5_windows_governance"),
    ]

    operations = [
        migrations.AddField(
            model_name="rawfhirresource",
            name="validation_ok",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="rawfhirresource",
            name="validation_issues",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="rawfhirresource",
            name="validation_profile",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="rawfhirresource",
            name="validation_version",
            field=models.CharField(blank=True, default="v0.5.1", max_length=32),
        ),
        migrations.AddField(
            model_name="normalizedobservation",
            name="source_code_system",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="normalizedobservation",
            name="source_code",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="normalizedobservation",
            name="source_display",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="normalizedcondition",
            name="source_code_system",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="normalizedcondition",
            name="source_code",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="normalizedcondition",
            name="source_display",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="normalizedprocedure",
            name="source_code_system",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="normalizedprocedure",
            name="source_code",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="normalizedprocedure",
            name="source_display",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
