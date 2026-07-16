"""
backfill_managed_domain — bind the platform ManagedDomain FK on
AppEnvironment rows that pre-date the resolve_managed_domain fix.

Usage:
    python manage.py backfill_managed_domain [--dry-run]

Idempotent: rows that already have a managed_domain FK are skipped.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from astrolift_clusters.models import resolve_managed_domain


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
        from astrolift_lifecycle.models import AppEnvironment

        dry_run = options["dry_run"]
        prefix = "[DRY RUN] " if dry_run else ""

        qs = AppEnvironment.objects.filter(
            managed_domain__isnull=True,
            deleted_at__isnull=True,
        ).select_related("registered_app__organization")

        total = qs.count()
        self.stdout.write(f"{prefix}Found {total} AppEnvironment(s) with managed_domain=NULL")

        updated = 0
        skipped = 0
        for env in qs:
            org = getattr(env.registered_app, "organization", None)
            domain = resolve_managed_domain(org, for_preview=False)
            if domain is None:
                self.stdout.write(f"  SKIP  {env.registered_app.slug}/{env.name} — no matching ManagedDomain")
                skipped += 1
                continue

            self.stdout.write(f"  {prefix}SET   {env.registered_app.slug}/{env.name} → {domain.zone}")
            if not dry_run:
                AppEnvironment.objects.filter(pk=env.pk).update(managed_domain=domain)
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"{prefix}Done — {updated} updated, {skipped} skipped (no domain found)")
        )
