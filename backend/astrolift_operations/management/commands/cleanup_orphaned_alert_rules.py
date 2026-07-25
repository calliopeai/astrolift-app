"""
cleanup_orphaned_alert_rules — soft-delete alert rules stranded by a
deregistered app.

Default alert rules are seeded per app at registration
(``astrolift_operations.alert_seed``), tagged ``target="app"`` /
``target_id=<app.slug>``. Before the deregister teardown learned to clean
them up, an app's rules survived its deletion — so the /alerts page kept
listing alerts for a gone app (reported for ``smd-fileportal``). The
teardown now soft-deletes them going forward; this command clears the rows
that pre-fix deregisters already stranded.

An app-targeted rule is *orphaned* when no live ``RegisteredApp`` exists
with its ``(organization, slug=target_id)`` — i.e. the owning app is
hard-absent or soft-deleted. Slug reuse is safe: a rule whose slug a live
app has since re-claimed is left untouched.

Usage:
    python manage.py cleanup_orphaned_alert_rules            # dry-run
    python manage.py cleanup_orphaned_alert_rules --apply    # soft-delete

Dry-run by default: prints exactly what it *would* soft-delete and changes
nothing. ``--apply`` performs the soft-delete (deleted_at/deleted_by; never
a hard delete, per the Tracking soft-delete invariant). Idempotent +
re-runnable across all orgs — this is an operator backfill, not
tenant-scoped.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Soft-delete alert rules whose owning app is deleted (orphans), across all orgs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Soft-delete the orphaned rules. Omit for a dry-run that changes nothing.",
        )

    def handle(self, *args, **options):
        from astrolift_operations.alert_cleanup import (
            find_orphaned_app_alert_rules,
            soft_delete_app_alert_rules,
        )

        apply = options["apply"]
        prefix = "" if apply else "[DRY RUN] "

        total_groups = 0
        total_rules = 0
        for org_id, slug, rules in find_orphaned_app_alert_rules():
            total_groups += 1
            self.stdout.write(
                f"{prefix}orphaned app slug={slug!r} (org={org_id}) — {len(rules)} alert rule(s):"
            )
            for rule in rules:
                self.stdout.write(f"  {prefix}soft-delete rule guid={rule.guid} name={rule.name!r}")
            if apply:
                total_rules += soft_delete_app_alert_rules(
                    organization_id=org_id,
                    app_slug=slug,
                )
            else:
                total_rules += len(rules)

        verb = "soft-deleted" if apply else "would be soft-deleted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Done — {total_groups} orphaned app(s), {total_rules} alert rule(s) {verb}."
            )
        )
