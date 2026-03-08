from __future__ import annotations

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("icea_pipeline", "0004_v0_5_1_fhir_validation_semantic_trace"),
    ]

    operations = [
        migrations.CreateModel(
            name="EntityChangeLog",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.CharField(max_length=128, blank=True, default="")),
                ("model_label", models.CharField(max_length=128)),
                ("object_id", models.CharField(max_length=128)),
                ("action", models.CharField(max_length=32, default="update")),
                ("changes", models.JSONField(default=dict, blank=True)),
            ],
            options={
                "indexes": [models.Index(fields=["created_at", "model_label"], name="icea_entit_created_5d5d9d_idx")],
            },
        ),
    ]
