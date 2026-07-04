from __future__ import annotations

import json
from unittest import mock

from django.test import SimpleTestCase

from fhir_integration.schemas import validate_bundle, validate_resource
from icea_pipeline.normalize import normalize_condition, normalize_observation, normalize_procedure
from terminology.mappings import load_nnn_mappings


class FHIRLocalValidationTests(SimpleTestCase):
    def _synthetic_bundle(self):
        return {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "demo-patient",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Encounter",
                        "id": "demo-encounter",
                        "subject": {"reference": "Patient/demo-patient"},
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "demo-observation",
                        "subject": {"reference": "Patient/demo-patient"},
                        "encounter": {"reference": "Encounter/demo-encounter"},
                        "code": {
                            "coding": [
                                {"system": "http://loinc.org", "code": "85354-9", "display": "Blood pressure panel"}
                            ]
                        },
                    }
                },
            ],
        }

    def test_valid_synthetic_bundle_passes_basic_local_validation(self):
        resources, issues = validate_bundle(self._synthetic_bundle(), require_encounter_context=True, secure_mode=True)

        self.assertEqual(len(resources), 3)
        self.assertEqual(issues, [])

    def test_bundle_rejects_entry_without_resource_type(self):
        bundle = self._synthetic_bundle()
        bundle["entry"][2]["resource"].pop("resourceType")

        _, issues = validate_bundle(bundle, require_encounter_context=True, secure_mode=True)

        self.assertTrue(any(issue["loc"][-1] == "resourceType" for issue in issues))

    def test_bundle_rejects_raw_identifier_reference_in_secure_mode(self):
        bundle = self._synthetic_bundle()
        bundle["entry"][2]["resource"]["subject"]["reference"] = "demo-patient"

        _, issues = validate_bundle(bundle, require_encounter_context=True, secure_mode=True)

        self.assertTrue(any(issue["type"] == "value_error.fhir_reference" for issue in issues))
        self.assertNotIn("demo-patient", json.dumps(issues))

    def test_observation_without_coding_is_rejected(self):
        observation = {
            "resourceType": "Observation",
            "id": "demo-observation",
            "subject": {"reference": "Patient/demo-patient"},
            "encounter": {"reference": "Encounter/demo-encounter"},
            "code": {},
        }

        ok, issues, _ = validate_resource(observation, expected_type="Observation")

        self.assertFalse(ok)
        self.assertTrue(any(issue["loc"] == ["code", "coding"] for issue in issues))

    def test_encounter_centered_bundle_requires_encounter_resource(self):
        bundle = self._synthetic_bundle()
        bundle["entry"] = [entry for entry in bundle["entry"] if entry["resource"]["resourceType"] != "Encounter"]

        _, issues = validate_bundle(bundle, require_encounter_context=True, secure_mode=True)

        self.assertTrue(any(issue["msg"] == "Encounter-centered flow requires an Encounter resource" for issue in issues))

    def test_risk_assessment_must_remain_shadow_only(self):
        risk_assessment = {
            "resourceType": "RiskAssessment",
            "id": "demo-risk",
            "subject": {"reference": "Patient/demo-patient"},
            "encounter": {"reference": "Encounter/demo-encounter"},
            "prediction": [{"probabilityDecimal": 0.91}],
        }

        ok, issues, _ = validate_resource(risk_assessment, expected_type="RiskAssessment")

        self.assertFalse(ok)
        self.assertTrue(any(issue["type"] == "value_error.shadow_only" for issue in issues))


class FHIRMulticodingNormalizationTests(SimpleTestCase):
    def tearDown(self):
        load_nnn_mappings.cache_clear()
        super().tearDown()

    def test_observation_prefers_loinc_over_local_coding(self):
        normalized = normalize_observation(
            {
                "resourceType": "Observation",
                "code": {
                    "coding": [
                        {"system": "http://hospital.example/local", "code": "LOCAL-BP"},
                        {"system": "http://loinc.org", "code": "85354-9", "display": "Blood pressure panel"},
                    ]
                },
            }
        )

        self.assertEqual(normalized["code_system"], "http://loinc.org")
        self.assertEqual(normalized["code"], "85354-9")

    def test_condition_prefers_snomed_over_local_coding(self):
        normalized = normalize_condition(
            {
                "resourceType": "Condition",
                "code": {
                    "coding": [
                        {"system": "http://hospital.example/local", "code": "LOCAL-FALL-RISK"},
                        {"system": "http://snomed.info/sct", "code": "129839007", "display": "At risk for falls"},
                    ]
                },
            }
        )

        self.assertEqual(normalized["code_system"], "http://snomed.info/sct")
        self.assertEqual(normalized["code"], "129839007")

    def test_explicit_nanda_mapping_is_used_without_free_text_inference(self):
        mapping = {"nanda_to_snomed": {"00155": "129839007"}}
        with mock.patch.dict("os.environ", {"NNN_MAPPING_JSON": json.dumps(mapping)}, clear=False):
            load_nnn_mappings.cache_clear()
            normalized = normalize_condition(
                {
                    "resourceType": "Condition",
                    "code": {
                        "coding": [
                            {"system": "https://nanda.org", "code": "00155", "display": "Risk for falls"},
                        ]
                    },
                }
            )

        self.assertEqual(normalized["code_system"], "http://snomed.info/sct")
        self.assertEqual(normalized["code"], "129839007")
        self.assertEqual(normalized["source_code_system"], "https://nanda.org")
        self.assertEqual(normalized["source_code"], "00155")

    def test_unmapped_nic_is_marked_unmapped(self):
        with mock.patch.dict("os.environ", {"NNN_MAPPING_JSON": "{}"}, clear=False):
            load_nnn_mappings.cache_clear()
            normalized = normalize_procedure(
                {
                    "resourceType": "Procedure",
                    "code": {
                        "coding": [
                            {"system": "https://nic.example", "code": "NIC-DEMO"},
                        ]
                    },
                }
            )

        self.assertEqual(normalized["code_system"], "unmapped")
        self.assertEqual(normalized["code"], "NIC-DEMO")
        self.assertEqual(normalized["source_code_system"], "https://nic.example")

    def test_missing_coding_is_marked_unknown(self):
        normalized = normalize_observation({"resourceType": "Observation", "code": {}})

        self.assertEqual(normalized["code_system"], "unknown")
        self.assertEqual(normalized["code"], "unknown")
