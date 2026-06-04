from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from fhir_integration.service import FHIRClient
from icea_core.models import PatientEpisode

from icea_pipeline.audit import append_audit_event
from icea_pipeline.models import RawFHIRResource


def _iter_bundle_entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    if not bundle:
        return []
    if bundle.get("resourceType") != "Bundle":
        return []
    return [e.get("resource") for e in (bundle.get("entry") or []) if e.get("resource")]


class Command(BaseCommand):
    help = "Ingest minimal FHIR resources for a PatientEpisode and store raw JSON for traceability."

    def add_arguments(self, parser):
        parser.add_argument("--episode-id", required=True, help="PatientEpisode id")
        parser.add_argument("--patient-id", required=True, help="FHIR Patient id")
        parser.add_argument(
            "--resources",
            default="Observation,Condition,Procedure",
            help="Comma-separated resource types to ingest",
        )

    def handle(self, *args, **opts):
        episode_id = int(opts["episode_id"])
        patient_id = str(opts["patient_id"])
        resources = [r.strip() for r in str(opts["resources"]).split(",") if r.strip()]

        episode = PatientEpisode.objects.get(id=episode_id)
        client = FHIRClient()

        total = 0
        with transaction.atomic():
            for rtype in resources:
                if rtype == "Observation":
                    bundle = client.fetch_observations(patient_id)
                elif rtype == "Condition":
                    bundle = client.fetch_conditions(patient_id)
                else:
                    # Generic GET with patient param; works for many resources
                    bundle = client.get(rtype, params={"patient": patient_id})

                for res in _iter_bundle_entries(bundle):
                    rid = res.get("id")
                    if not rid:
                        continue
                    raw, created = RawFHIRResource.objects.update_or_create(
                        episode=episode,
                        resource_type=res.get("resourceType", rtype),
                        resource_id=rid,
                        defaults={"payload": res},
                    )
                    total += 1

        append_audit_event(
            event_type="ingest_fhir",
            payload={"action": "ingest", "row_count": int(total), "status": "completed"},
            context="management/ingest_fhir",
            actor="management_command",
        )
        self.stdout.write(self.style.SUCCESS(f"Ingested/updated {total} resources for episode={episode_id}"))
