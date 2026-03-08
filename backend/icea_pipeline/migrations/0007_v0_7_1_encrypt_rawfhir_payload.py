from __future__ import annotations

from django.db import migrations

import icea_pipeline.fields


def _noop(apps, schema_editor):
    # DB type stays JSON/JSONB; encryption is application-layer.
    return


class Migration(migrations.Migration):
    """v0.7.1: Encrypt RawFHIRResource.payload at-rest (transparent).

    DB column remains JSON/JSONB. This migration exists to document the
    semantic change and keep schema history explicit.
    """

    dependencies = [
        ("icea_pipeline", "0006_v0_7_0_quality_sim_federated"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rawfhirresource",
            name="payload",
            field=icea_pipeline.fields.EncryptedJSONField(),
        ),
        migrations.RunPython(_noop, reverse_code=_noop),
    ]
