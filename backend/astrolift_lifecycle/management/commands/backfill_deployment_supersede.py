"""
backfill_deployment_supersede — repair historically-stuck RUNNING deploys.

``Deployment.Status.RUNNING`` means *successfully live*, not in-progress.
Only one deployment per ``(registered_app, app_environment)`` should read
as RUNNING at a time; a newer deploy reaching RUNNING is supposed to
supersede the prior one. Before the ``_mark_running`` fix, that supersede
never happened, so every historical deploy stayed RUNNING and flooded the
Active tab. Deployments belonging to a soft-deleted app also kept reading
as live.

This command repairs that existing data:

1. For each ``(registered_app, app_environment)`` with >1 RUNNING
   deployment, keep the most-recent-by-``created_at`` RUNNING and
   transition the older ones to SUPERSEDED.
2. For any RUNNING deployment whose ``registered_app`` is soft-deleted,
   transition it to SUPERSEDED — its app is gone, it is not live.

Usage:
    python manage.py backfill_deployment_supersede [--dry-run]

Idempotent / re-runnable: a clean state leaves nothing to do. Every
mutation goes through ``Deployment.transition_to`` (never a direct field
set) so lifecycle events and logs fire exactly as in normal operation.
This is an operator backfill over all orgs — nothing is tenant-scoped.
"""

from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Supersede stale RUNNING deployments (duplicates + soft-deleted apps) across all orgs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Print what would change without writing to the DB.",
        )
        parser.add_argument(
            "--stale-in-flight-hours",
            type=int,
            default=None,
            metavar="HOURS",
            help=(
                "Also fail out PENDING/DEPLOYING/REDEPLOYING deployments older "
                "than HOURS (#1536). Deploys now supersede in-flight rows on "
                "start, so this only clears rows stranded before that fix on "
                "apps that haven't deployed since. A live deploy legitimately "
                "runs ~30m; pick a margin well above that."
            ),
        )

    def handle(self, *args, **options):
        from astrolift_lifecycle.models import Deployment

        dry_run = options["dry_run"]
        prefix = "[DRY RUN] " if dry_run else ""

        running = (
            Deployment.objects.filter(status=Deployment.Status.RUNNING.value)
            .select_related("registered_app")
            .order_by("-created_at")
        )

        # Group by (app, env); each list is newest-first thanks to the ordering.
        groups: dict[tuple[int, int], list[Deployment]] = defaultdict(list)
        for dep in running:
            groups[(dep.registered_app_id, dep.app_environment_id)].append(dep)

        superseded_duplicates = 0
        superseded_dead_app = 0

        for (_app_id, _env_id), deploys in groups.items():
            app_deleted = deploys[0].registered_app.deleted_at is not None

            if app_deleted:
                # App is soft-deleted — none of its deploys are live.
                to_close = deploys
            else:
                # Keep the newest RUNNING; supersede the rest.
                to_close = deploys[1:]

            for dep in to_close:
                label = "DEAD-APP" if app_deleted else "DUP"
                self.stdout.write(
                    f"  {prefix}SUPERSEDE [{label}] deployment={dep.guid} "
                    f"app={dep.registered_app.slug} env_id={dep.app_environment_id} "
                    f"created_at={dep.created_at.isoformat()}"
                )
                if not dry_run:
                    dep.transition_to(Deployment.Status.SUPERSEDED)
                if app_deleted:
                    superseded_dead_app += 1
                else:
                    superseded_duplicates += 1

        stale_failed = 0
        stale_hours = options["stale_in_flight_hours"]
        if stale_hours is not None:
            from datetime import timedelta

            from django.utils import timezone

            cutoff = timezone.now() - timedelta(hours=stale_hours)
            stale = (
                Deployment.objects.filter(
                    status__in=(
                        Deployment.Status.PENDING.value,
                        Deployment.Status.DEPLOYING.value,
                        Deployment.Status.REDEPLOYING.value,
                    ),
                    created_at__lt=cutoff,
                )
                .select_related("registered_app")
                .order_by("created_at")
            )
            for dep in stale:
                self.stdout.write(
                    f"  {prefix}FAIL-OUT [STALE-{dep.status.upper()}] deployment={dep.guid} "
                    f"app={dep.registered_app.slug} env_id={dep.app_environment_id} "
                    f"created_at={dep.created_at.isoformat()}"
                )
                if not dry_run:
                    if not dep.aborted_reason:
                        dep.aborted_reason = (
                            f"stale in-flight deploy (older than {stale_hours}h), failed out by backfill"
                        )
                        dep.save(update_fields=["aborted_reason", "updated_at", "version"])
                    dep.transition_to(Deployment.Status.FAILED)
                stale_failed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Done — {len(groups)} app/env group(s) scanned, "
                f"{superseded_duplicates} duplicate deployment(s) superseded, "
                f"{superseded_dead_app} dead-app deployment(s) closed, "
                f"{stale_failed} stale in-flight deployment(s) failed out."
            )
        )
