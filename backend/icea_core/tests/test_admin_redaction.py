from __future__ import annotations

from django.contrib import admin
from django.test import SimpleTestCase

from icea_core.models import PatientEpisode
from icea_pipeline.models import AuditEvent, FHIRWritebackRecord, RawFHIRResource


class AdminRedactionTests(SimpleTestCase):
    def test_sensitive_admins_do_not_search_raw_clinical_identifiers(self):
        for model in (PatientEpisode, RawFHIRResource, FHIRWritebackRecord, AuditEvent):
            model_admin = admin.site._registry[model]
            search_fields = set(getattr(model_admin, "search_fields", ()))

            self.assertNotIn("external_patient_id", search_fields)
            self.assertNotIn("fhir_patient_id", search_fields)
            self.assertNotIn("fhir_encounter_id", search_fields)
            self.assertNotIn("resource_id", search_fields)
            self.assertNotIn("payload", search_fields)
            self.assertNotIn("actor", search_fields)

    def test_sensitive_payload_fields_are_not_on_redacted_admin_forms(self):
        raw_admin = admin.site._registry[RawFHIRResource]
        writeback_admin = admin.site._registry[FHIRWritebackRecord]
        audit_admin = admin.site._registry[AuditEvent]

        self.assertNotIn("payload", raw_admin.fields)
        self.assertNotIn("resource_id", raw_admin.fields)
        self.assertNotIn("payload", writeback_admin.fields)
        self.assertNotIn("writeback_response", writeback_admin.fields)
        self.assertNotIn("actor", audit_admin.fields)
