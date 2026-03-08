from __future__ import annotations

"""PHI retention job for RawFHIRResource.

Usage:
  python manage.py cleanup_phi
  python manage.py cleanup_phi --days 14 --action anonymize
  python manage.py cleanup_phi --dry-run

Settings:
  PHI_RETENTION_DAYS (default 30)
  PHI_RETENTION_ACTION = delete|anonymize

Security intent:
  - Reduce PHI footprint in the operational DB.
  - Support institutional data minimization (GDPR/EHDS/ENS).
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from icea_pipeline.models import RawFHIRResource


class Command(BaseCommand):
    help = "Apply PHI retention policy to RawFHIRResource (delete/anonymize)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, help="Retention window in days")
        parser.add_argument(
            "--action",
            type=str,
            default=None,
            choices=["delete", "anonymize"],
            help="Retention action",
        )
        parser.add_argument("--dry-run", action="store_true", help="Do not modify data")
        parser.add_argument("--batch-size", type=int, default=500, help="Batch size")

    def handle(self, *args, **opts):
        days = int(opts.get("days") or getattr(settings, "PHI_RETENTION_DAYS", 30))
        action = (opts.get("action") or getattr(settings, "PHI_RETENTION_ACTION", "delete") or "delete").strip().lower()
        dry_run = bool(opts.get("dry_run"))
        batch_size = int(opts.get("batch_size") or 500)

        if days <= 0:
            self.stdout.write(self.style.ERROR("Retention days must be > 0"))
            return

        cutoff = timezone.now() - timedelta(days=days)
        qs = RawFHIRResource.objects.filter(ingested_at__lt=cutoff).order_by("ingested_at")
        total = qs.count()

        self.stdout.write(
            f"PHI retention: action={action} days={days} cutoff={cutoff.isoformat()} candidates={total} dry_run={dry_run}"
        )

        if total == 0:
            return

        processed = 0
        if action == "delete":
            if dry_run:
                return
            # Delete in chunks to avoid long transactions.
            while True:
                ids = list(qs.values_list("id", flat=True)[:batch_size])
                if not ids:
                    break
                with transaction.atomic():
                    RawFHIRResource.objects.filter(id__in=ids).delete()
                processed += len(ids)
                self.stdout.write(f"Deleted {processed}/{total}")
            return

        if action == "anonymize":
            # Anonymize payload in-place, preserving minimal forensic metadata.
            # NOTE: payload is encrypted-at-rest (v0.7.1). We set a minimal stub.
            while True:
                batch = list(qs[:batch_size])
                if not batch:
                    break
                if dry_run:
                    processed += len(batch)
                    qs = qs[batch_size:]
                    continue

                with transaction.atomic():
                    for r in batch:
                        r.payload = {
                            "resourceType": r.resource_type,
                            "id": r.resource_id,
                            "__redacted__": True,
                            "__redacted_reason__": "retention_policy",
                        }
                        # Keep validation metadata; clear issues for privacy.
                        r.validation_ok = False
                        r.validation_issues = []
                        r.save(update_fields=["payload", "payload_sha256", "validation_ok", "validation_issues"])
                processed += len(batch)
                self.stdout.write(f"Anonymized {processed}/{total}")
            return

        self.stdout.write(self.style.ERROR(f"Unknown action: {action}"))
