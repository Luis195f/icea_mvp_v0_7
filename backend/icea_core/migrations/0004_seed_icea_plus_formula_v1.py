import hashlib
import json

from django.db import migrations


DEFAULT_SPEC = {
    "version": "icea_plus_v1",
    "label": "ICEA+ v1 pilot composite index",
    "status": "pilot",
    "governance": {
        "calibration_state": "pilot_default_weights",
        "requires_external_validation": True,
        "not_for_automated_sanctions": True,
        "clinical_judgement_required": True,
        "causal_claims_limited_to_available_identification_strategy": True,
    },
    "weights": {
        "intercept": 0.0,
        "benefit": 1.0,
        "attribution": 1.0,
        "causal": 1.0,
        "quality": 1.0,
        "uncertainty": 1.0,
    },
    "normalization": {
        "method": "robust_z",
        "mad_scale": 1.4826,
        "eps": 1e-6,
        "clip": 4.0,
        "min_reference_rows": 5,
    },
    "baseline": {
        "mode": "counterfactual_nursing_reference",
        "reference_strategy": "cohort_median",
    },
    "attribution": {
        "epsilon": 1e-6,
    },
    "causal": {
        "effect_mode": "marginal_per_unit",
        "n_estimators": 200,
        "min_rows": 30,
    },
    "quality": {
        "subcomponents": [
            "structured_completeness",
            "documentation_consistency",
            "timeliness",
        ],
        "min_available_subcomponents": 1,
    },
    "uncertainty": {
        "weights": {
            "conformal_width": 0.35,
            "missingness": 0.35,
            "ood": 0.20,
            "low_support": 0.10,
        },
        "ood_z_threshold": 3.0,
        "min_training_rows": 100,
        "high_uncertainty_threshold": 0.60,
    },
    "aggregation": {
        "epsilon": 1e-6,
        "min_nurse_reliability": 0.60,
        "severity_clip": 2.0,
    },
    "scoring": {
        "required_components": ["benefit", "attribution", "quality", "uncertainty"],
        "optional_components": ["causal"],
        "min_confidence": 0.05,
    },
    "outcome_goal_rules": [
        {"contains": ["delta_ri", "ri_final", "recovery", "utility"], "goal": "higher_is_better"},
        {
            "contains": [
                "mortality",
                "fall",
                "pressure_injury",
                "pressure_ulcer",
                "upp",
                "infection",
                "readmission",
                "deterioration",
                "length_of_stay",
                "los",
            ],
            "goal": "lower_is_better",
        },
    ],
}


def _seed_formula(apps, schema_editor):
    model = apps.get_model("icea_core", "ICEAPlusFormulaVersion")
    dumped = json.dumps(DEFAULT_SPEC, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    protocol_hash = hashlib.sha256(dumped.encode("utf-8")).hexdigest()
    model.objects.update(is_active=False)
    model.objects.update_or_create(
        version="icea_plus_v1",
        defaults={
            "label": DEFAULT_SPEC["label"],
            "status": DEFAULT_SPEC["status"],
            "is_active": True,
            "spec": DEFAULT_SPEC,
            "protocol_hash": protocol_hash,
            "notes": "Seeded pilot default weights for ICEA+ v1.",
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("icea_core", "0003_iceapluscomputation_iceaplusformulaversion_and_more"),
    ]

    operations = [
        migrations.RunPython(_seed_formula, migrations.RunPython.noop),
    ]
