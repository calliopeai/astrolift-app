"""resync_workflow_schedules — rewrite configured Workflows' cron schedules (#2053).

A schedule written before #2053 carries a run input built once, when its
Workflow was saved, and every fire after the first fails with "Cannot open a
stage on a closed workflow". Rewriting it replaces the action with one that
starts ``ConfiguredWorkflowScheduleWorkflow``, which creates a run per fire.
A deploy alone changes no schedule, so run this once on each install after
deploying #2053. Re-running it rewrites the same schedules to the same thing.

Every live, enabled, schedule-triggered Workflow gets its schedule rewritten.
Every other configured Workflow, soft-deleted ones included, gets the delete
its own save would issue, which is a no-op when it has no schedule: a schedule
that outlived a failed delete would otherwise keep firing the old action.
Install-wide operator command; not tenant-scoped.

Usage:
    python manage.py resync_workflow_schedules            # report only
    python manage.py resync_workflow_schedules --apply    # rewrite
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Rewrite every scheduled configured Workflow's Temporal schedule so each fire creates its own run."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", default=False, help="Rewrite the schedules.")
        parser.add_argument(
            "--workflow-id", type=str, help="Reconcile only this exact configured Workflow GUID."
        )

    def handle(self, *args, **options):
        from astrolift_workflows.client import _temporal_enabled
        from workflows.models import Workflow
        from workflows.schedule_sync import (
            delete_workflow_schedule,
            schedule_id_for,
            schedule_inactive_reason,
            write_workflow_schedule,
        )

        apply = options["apply"]
        if apply and not _temporal_enabled():
            raise CommandError("Temporal is disabled on this install; there is no schedule to rewrite.")
        prefix = "" if apply else "[REPORT] "
        rewritten = failed = inactive = 0
        rows = Workflow.objects.select_related("organization", "definition__project__team").order_by("pk")
        if options.get("workflow_id"):
            from uuid import UUID

            try:
                guid = UUID(options["workflow_id"])
            except ValueError as exc:
                raise CommandError("--workflow-id must be a configured Workflow GUID.") from exc
            rows = rows.filter(guid=guid)
            if not rows.exists():
                raise CommandError("No configured Workflow has this GUID.")
        for wf in rows:
            if schedule_inactive_reason(wf) is not None:
                if apply:
                    result = delete_workflow_schedule(wf)
                    if not result.confirmed:
                        self.stdout.write(
                            f"  FAILED  {schedule_id_for(wf)}: {result.error_code or 'schedule_not_confirmed'}"
                        )
                        failed += 1
                        continue
                inactive += 1
                continue
            label = f"{schedule_id_for(wf)} org={wf.organization.slug} workflow={wf.slug} cron={wf.schedule_cron!r}"
            if not apply:
                self.stdout.write(f"{prefix}REWRITE {label}")
                rewritten += 1
                continue
            try:
                write_workflow_schedule(wf)
            except Exception:  # noqa: BLE001 - report the Workflow, keep going without leaking engine errors
                self.stdout.write(f"  FAILED  {label}: schedule_not_confirmed")
                failed += 1
                continue
            self.stdout.write(f"  REWROTE {label}")
            rewritten += 1

        if not apply:
            self.stdout.write(
                f"{prefix}{rewritten} schedule(s) to rewrite; {inactive} workflow(s) that should have no "
                "schedule would get a delete. Re-run with --apply to write."
            )
            return
        self.stdout.write(
            f"{rewritten} rewritten, {failed} failed; absence confirmed for {inactive} workflow(s) "
            "that should have no schedule."
        )
        if failed:
            raise CommandError(f"{failed} schedule(s) failed to rewrite; re-run once Temporal accepts them.")
