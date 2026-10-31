"""
backfill_managed_domain — bind the platform ManagedDomain FK on
AppEnvironment rows that pre-date the resolve_managed_domain fix, and
recompute the URL each row advertises to match.

Usage:
    python manage.py backfill_managed_domain [--dry-run]

Idempotent: rows that already have a managed_domain FK are skipped.

Binding the FK alone was not enough (#1690). The render reads the FK, so
the Ingress came out right, while the UI kept showing whatever URL the
row was created with — for rows created before #1689 that was
``https://<app>.<org-slug>``, an address that never resolved. Immediately
after a command that reported success, the app still read as broken.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from astrolift_clusters.managed_domain_bindings import backfill_managed_domain_bindings


class Command(BaseCommand):
    help = "Backfill managed_domain FK on AppEnvironments created before the auto-bind fix."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Print what would be updated without writing to the DB.",
        )

    def handle(self, *args, **options):
        result = backfill_managed_domain_bindings(dry_run=options["dry_run"])
        prefix = "[DRY RUN] " if result.dry_run else ""
        self.stdout.write(
            f"{prefix}Found {len(result.bound) + len(result.skipped)} AppEnvironment(s) with managed_domain=NULL"
        )
        for row in result.skipped:
            self.stdout.write(f"  SKIP  {row.app_slug}/{row.environment_name}: no matching ManagedDomain")
        for row in result.bound:
            if row.url != row.previous_url:
                self.stdout.write(
                    f"  {prefix}SET   {row.app_slug}/{row.environment_name} → {row.zone} "
                    f"(url {row.previous_url or '(empty)'} → {row.url})"
                )
            else:
                self.stdout.write(f"  {prefix}SET   {row.app_slug}/{row.environment_name} → {row.zone}")
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Done: {len(result.bound)} updated, {len(result.skipped)} skipped (no domain found)"
            )
        )
