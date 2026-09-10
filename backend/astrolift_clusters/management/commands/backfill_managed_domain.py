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

            app = env.registered_app
            host = (app.subdomain or app.slug or "").strip()
            new_url = f"https://{host}.{domain.zone}" if host else env.url
            changes: dict = {"managed_domain": domain}
            if new_url != env.url:
                changes["url"] = new_url
                self.stdout.write(
                    f"  {prefix}SET   {app.slug}/{env.name} → {domain.zone} "
                    f"(url {env.url or '(empty)'} → {new_url})"
                )
            else:
                self.stdout.write(f"  {prefix}SET   {app.slug}/{env.name} → {domain.zone}")
            if not dry_run:
                AppEnvironment.objects.filter(pk=env.pk).update(**changes)
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"{prefix}Done — {updated} updated, {skipped} skipped (no domain found)")
        )
