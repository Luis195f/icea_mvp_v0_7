from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("icea_core", "0004_seed_icea_plus_formula_v1"),
    ]

    operations = [
        migrations.CreateModel(
            name="ICEAPlusFollowupRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("formula_version", models.CharField(default="icea_plus_v1", max_length=64)),
                ("formula_protocol_hash", models.CharField(blank=True, default="", max_length=64)),
                ("grain", models.CharField(default="episode", max_length=32)),
                ("patient_key", models.CharField(blank=True, default="", max_length=128)),
                ("initial_state", models.CharField(default="insufficient_evidence", max_length=32)),
                ("followup_status", models.CharField(default="pending_followup", max_length=32)),
                ("current_state", models.CharField(default="insufficient_evidence", max_length=32)),
                ("evidence_types", models.JSONField(blank=True, default=list)),
                ("evidence_summary", models.JSONField(blank=True, default=dict)),
                ("support", models.JSONField(blank=True, default=dict)),
                ("warnings", models.JSONField(blank=True, default=list)),
                ("provenance", models.JSONField(blank=True, default=dict)),
                ("initial_request", models.JSONField(blank=True, default=dict)),
                ("initial_result", models.JSONField(blank=True, default=dict)),
                ("enriched_result", models.JSONField(blank=True, default=dict)),
                ("feature_snapshot_hash", models.CharField(blank=True, default="", max_length=64)),
                ("enriched_snapshot_hash", models.CharField(blank=True, default="", max_length=64)),
                ("last_followup_at", models.DateTimeField(blank=True, null=True)),
                ("last_rescore_at", models.DateTimeField(blank=True, null=True)),
                ("non_individual_use", models.BooleanField(default=True)),
                ("shadow_mode", models.BooleanField(default=True)),
                ("exploratory_only", models.BooleanField(default=True)),
                (
                    "enriched_computation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="followup_enriched_records",
                        to="icea_core.iceapluscomputation",
                    ),
                ),
                (
                    "episode",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="icea_plus_followups",
                        to="icea_core.patientepisode",
                    ),
                ),
                (
                    "initial_computation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="followup_initial_records",
                        to="icea_core.iceapluscomputation",
                    ),
                ),
                (
                    "model",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="icea_plus_followups",
                        to="icea_core.modelartifact",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["episode", "model", "updated_at"], name="icea_core_i_episode_f19450_idx"),
                    models.Index(fields=["formula_version", "current_state"], name="icea_core_i_formula_7bdd97_idx"),
                    models.Index(fields=["followup_status", "last_followup_at"], name="icea_core_i_followu_4cebc5_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="iceaplusfollowuprecord",
            constraint=models.UniqueConstraint(
                fields=("episode", "model", "formula_version", "formula_protocol_hash"),
                name="uniq_followup_episode_model_formula_protocol",
            ),
        ),
    ]
