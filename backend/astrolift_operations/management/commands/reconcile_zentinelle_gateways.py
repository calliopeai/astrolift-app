"""Bring every registered Zentinelle gateway in line with ZENTINELLE_GATEWAY_ENABLED (#1905)."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deploy or remove the Zentinelle gateway in every registered cluster to match the install switch."

    def handle(self, *args, **options):
        from astrolift_operations.zentinelle_connect import reconcile_gateways

        for row in reconcile_gateways():
            self.stdout.write(f"{row.cluster_slug}: {row.outcome}")
