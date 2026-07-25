"""backfill_ci_workflow_stamps — baseline the versioned-sync record for apps
whose managed CI workflow file predates Phase 0/1 versioning (#1211, Phase 3).

Phase 0 (stamping) + Phase 1 (per-app ``ci_workflow_template_version`` /
``ci_workflow_state``) shipped AFTER some apps already had a managed CI workflow
file pushed into their repo by the old, unversioned push. Those apps carry a
file in-repo but a NULL ``ci_workflow_template_version``, so the drift machinery
reads them as "never synced" and the resync sweep can't tell a stale file from a
current one.

This command repairs that: for each candidate app (a pushable source repo + a
NULL ``ci_workflow_template_version``) it FETCHES the repo's managed workflow
file and, when present, persists a baseline stamp (``ci_workflow_template_version``
+ ``ci_workflow_state``) so drift computes correctly going forward. It reuses
Phase 2's fetch (``fetch_repo_ci_workflow``) + Phase 1's persist
(``adopt_repo_ci_workflow``, which re-points the sync record at the repo's
current file and flags ``in_sync``) — no new persistence logic.

Idempotent / re-runnable: a baselined app gets a non-null
``ci_workflow_template_version`` and so drops out of the candidate set; a clean
run leaves nothing to do. Apps with NO file in the repo are left untouched
(nothing to baseline). This is an operator backfill; it is NOT tenant-scoped,
but ``--org`` / ``--app`` narrow the sweep.

Usage:
    python manage.py backfill_ci_workflow_stamps [--dry-run] [--org SLUG] [--app SLUG]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

# Source hosts Phase 1 can render + push a managed workflow for — the only ones
# a baseline fetch makes sense for (git_url / direct_upload have no repo to read).
_HOSTS = ("github", "gitlab", "bitbucket", "gitea")


class Command(BaseCommand):
    help = "Baseline the CI-workflow sync record for managed apps whose file predates versioning."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report what would be baselined without writing to the DB.",
        )
        parser.add_argument(
            "--org",
            default=None,
            help="Limit the sweep to one organization (by slug).",
        )
        parser.add_argument(
            "--app",
            default=None,
            help="Limit the sweep to one app (by slug).",
        )

    def handle(self, *args, **options):
        from astrolift_registry.models import RegisteredApp
        from astrolift_scm.services.ci_workflow_drift import (
            CiWorkflowAdoptError,
            CiWorkflowFetchError,
            adopt_repo_ci_workflow,
            fetch_repo_ci_workflow,
        )

        dry_run: bool = options["dry_run"]
        org_slug: str | None = options["org"]
        app_slug: str | None = options["app"]
        prefix = "[DRY RUN] " if dry_run else ""

        # Candidates: a pushable repo, but NO baseline yet (unstamped). Once
        # baselined the version column is non-null, so a re-run finds none.
        candidates = (
            RegisteredApp.objects.filter(
                ci_workflow_template_version__isnull=True,
                source_kind__in=_HOSTS,
            )
            .exclude(source_repo="")
            .select_related("organization")
            .order_by("pk")
        )
        if org_slug:
            candidates = candidates.filter(organization__slug=org_slug)
        if app_slug:
            candidates = candidates.filter(slug=app_slug)

        considered = baselined = skipped_absent = skipped_error = 0

        for app in candidates.iterator():
            considered += 1
            org = app.organization.slug

            if dry_run:
                # Read-only: fetch to classify present / absent / error; never
                # persist. Mirrors the live persist decision below.
                try:
                    text = fetch_repo_ci_workflow(app)
                except CiWorkflowFetchError as exc:
                    skipped_error += 1
                    self.stdout.write(
                        f"  {prefix}SKIP [fetch {exc.code}] app={app.slug} org={org}: {exc.message}"
                    )
                    continue
                if text is None:
                    skipped_absent += 1
                    self.stdout.write(
                        f"  {prefix}SKIP [absent] app={app.slug} org={org} — no managed file in repo"
                    )
                    continue
                baselined += 1
                self.stdout.write(f"  {prefix}BASELINE app={app.slug} org={org} repo={app.source_repo}")
                continue

            # Live: adopt fetches the repo file and re-baselines the sync record
            # (Phase 1 persist) — the exact baseline we want. It raises
            # CiWorkflowAdoptError('ABSENT') when there is no file to adopt.
            try:
                result = adopt_repo_ci_workflow(app)
            except CiWorkflowAdoptError:
                skipped_absent += 1
                self.stdout.write(f"  SKIP [absent] app={app.slug} org={org} — no managed file in repo")
                continue
            except CiWorkflowFetchError as exc:
                skipped_error += 1
                self.stdout.write(f"  SKIP [fetch {exc.code}] app={app.slug} org={org}: {exc.message}")
                continue
            baselined += 1
            self.stdout.write(
                f"  BASELINE app={app.slug} org={org} version={result.template_version} repo={app.source_repo}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Done — {considered} candidate app(s) scanned, {baselined} baselined, "
                f"{skipped_absent} absent (no file), {skipped_error} skipped on fetch error."
            )
        )
