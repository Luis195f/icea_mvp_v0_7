from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from icea_core.models import PatientEpisode
from icea_pipeline.audit import append_audit_event
from icea_pipeline.models import (
    NormalizedCondition,
    NormalizedObservation,
    NormalizedProcedure,
    RawFHIRResource,
)
from icea_pipeline.normalize import normalize_condition, normalize_observation, normalize_procedure


class Command(BaseCommand):
    help = "Normalize ingested raw FHIR resources into canonical tables (Observation/Condition/Procedure)."

    def add_arguments(self, parser):
        parser.add_argument("--episode-id", required=True, help="PatientEpisode id")
        parser.add_argument(
            "--truncate",
            action="store_true",
            help="Delete existing normalized rows for the episode before rebuilding",
        )

    def handle(self, *args, **opts):
        episode_id = int(opts["episode_id"])
        truncate = bool(opts["truncate"])
        episode = PatientEpisode.objects.get(id=episode_id)

        raws = RawFHIRResource.objects.filter(episode=episode).order_by("resource_type")

        with transaction.atomic():
            if truncate:
                NormalizedObservation.objects.filter(episode=episode).delete()
                NormalizedCondition.objects.filter(episode=episode).delete()
                NormalizedProcedure.objects.filter(episode=episode).delete()

            n_obs = n_cond = n_proc = 0
            for raw in raws:
                rt = raw.resource_type
                payload = raw.payload

                if rt == "Observation":
                    data = normalize_observation(payload)
                    NormalizedObservation.objects.create(episode=episode, source_resource=raw, **data)
                    n_obs += 1
                elif rt == "Condition":
                    data = normalize_condition(payload)
                    NormalizedCondition.objects.create(episode=episode, source_resource=raw, **data)
                    n_cond += 1
                elif rt == "Procedure":
                    data = normalize_procedure(payload)
                    # simple role mapping: if SNOMED code maps from NIC, mark nursing externally later
                    NormalizedProcedure.objects.create(episode=episode, source_resource=raw, **data)
                    n_proc += 1

        append_audit_event(
            event_type="normalize_fhir",
            payload={
                "action": "normalize",
                "row_count": int(n_obs + n_cond + n_proc),
                "status": "completed",
            },
            context="management/normalize_fhir",
            actor="management_command",
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Normalized episode={episode_id}: observations={n_obs}, conditions={n_cond}, procedures={n_proc}"
            )
        )
