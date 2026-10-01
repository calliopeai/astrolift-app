"""Read-only metadata inventory; runtime enforcement remains a held future release."""

import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from astrolift_agents.services.secret_owner_cutover import cutover_readiness
from astrolift_identity.models import Organization


class Command(BaseCommand):
    help = "Inventory live typed refs and tombstones against recorded owners without opening a secret store."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True)
        parser.add_argument("--require-ready", action="store_true")

    def handle(self, *args, **options):
        organization = Organization.objects.filter(slug=options["org"], deleted_at__isnull=True).first()
        if organization is None:
            try:
                organization = Organization.objects.filter(
                    guid=options["org"], deleted_at__isnull=True
                ).first()
            except (ValidationError, ValueError):
                pass
        if organization is None:
            raise CommandError("organization not found")
        try:
            report = cutover_readiness(organization)
        except Exception:
            raise CommandError(
                "owner readiness metadata is unavailable; inspect organization and cluster"
            ) from None
        self.stdout.write(json.dumps(report, sort_keys=True))
        if options["require_ready"] and not report["metadata_ready"]:
            raise CommandError(
                "owner readiness refused; review metadata findings and migration prerequisites"
            )
