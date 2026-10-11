"""
report_shared_environment_namespaces — environments that overwrite each other (#2094).

Lists every app whose live environments on one cluster still render into the
same namespace, because they predate per-environment namespaces (#1922). Each
deploy of one of them overwrites the other's objects. The command only reads;
moving an environment into its own namespace is a separate, operator-run step.

Usage:
    python manage.py report_shared_environment_namespaces [--organization <slug>] [--json]

Exits 1 when any shared namespace is found, so a check can gate on it.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from django.core.management.base import BaseCommand

from astrolift_lifecycle.services.shared_namespaces import shared_environment_namespaces


class Command(BaseCommand):
    help = "List apps whose environments on one cluster still share a namespace (#2094)."

    def add_arguments(self, parser):
        parser.add_argument("--organization", default=None, help="Only this organization slug")
        parser.add_argument("--json", action="store_true", help="Print JSON instead of a table")

    def handle(self, *args, **options):
        rows = shared_environment_namespaces(organization_slug=options["organization"])
        if options["json"]:
            self.stdout.write(json.dumps([asdict(row) for row in rows], indent=2))
        elif not rows:
            self.stdout.write("No environments share a namespace.")
        else:
            for row in rows:
                self.stdout.write(
                    f"{row.organization}/{row.app} on {row.cluster}: namespace {row.namespace} "
                    f"is shared by {', '.join(row.environments)}"
                )
        if rows:
            raise SystemExit(1)
