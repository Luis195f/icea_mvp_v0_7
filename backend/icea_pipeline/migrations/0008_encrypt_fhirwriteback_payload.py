from __future__ import annotations

from django.db import migrations

import icea_pipeline.fields


def _noop(apps, schema_editor):
    # DB type stays JSON/JSONB; encryption is application-layer and legacy
    # plaintext rows remain readable until resaved.
    return


class Migration(migrations.Migration):
    dependencies = [
        ("icea_pipeline", "0007_v0_7_1_encrypt_rawfhir_payload"),
    ]

    operations = [
        migrations.AlterField(
            model_name="fhirwritebackrecord",
            name="payload",
            field=icea_pipeline.fields.EncryptedJSONField(default=dict),
        ),
        migrations.RunPython(_noop, reverse_code=_noop),
    ]
