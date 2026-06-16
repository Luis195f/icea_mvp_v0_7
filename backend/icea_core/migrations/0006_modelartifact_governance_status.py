from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("icea_core", "0005_iceaplusfollowuprecord"),
    ]

    operations = [
        migrations.AddField(
            model_name="modelartifact",
            name="governance_status",
            field=models.CharField(default="candidate", max_length=32),
        ),
        migrations.AddIndex(
            model_name="modelartifact",
            index=models.Index(fields=["governance_status"], name="icea_core_m_governa_b12b68_idx"),
        ),
    ]
