from __future__ import annotations

import json
import sys

from django.core.management.base import BaseCommand

from icea_core.operational_readiness import FAIL, build_readiness_report


class Command(BaseCommand):
    help = "Run non-destructive ICEA operational readiness checks."

    def add_arguments(self, parser):
        parser.add_argument("--compact", action="store_true", help="Emit compact JSON instead of indented JSON.")
        parser.add_argument("--strict-exit", action="store_true", help="Exit with code 1 when status is fail.")

    def handle(self, *args, **options):
        report = build_readiness_report()
        self.stdout.write(json.dumps(report, indent=None if options["compact"] else 2, sort_keys=True))
        if options["strict_exit"] and report["status"] == FAIL:
            sys.exit(1)
