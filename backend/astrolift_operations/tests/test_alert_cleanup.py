"""Tests for alert-rule cleanup on app deregister + orphan backfill (real Postgres).

Covers the leak fix: app-targeted alert rules (seeded on registration with
``target="app"`` / ``target_id=<slug>``, no FK to the app) were never
soft-deleted when the app was deregistered, so the /alerts page kept listing
alerts for a deleted app (reported for ``smd-fileportal``).

* ``soft_delete_app_alert_rules`` soft-deletes exactly one app's rules and is
  idempotent.
* The deregister teardown's final soft-delete pass
  (``_soft_delete_app_records_sync``) now soft-deletes the app's alert rules
  so they drop out of the /alerts query (default manager).
* The ``cleanup_orphaned_alert_rules`` command finds + soft-deletes rules
  whose owning app is gone/soft-deleted; dry-run (default) makes no changes;
  a live app's rules are never touched.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.alert_cleanup import (
    find_orphaned_app_alert_rules,
    soft_delete_app_alert_rules,
)
from astrolift_operations.alert_rules import DEFAULT_RULES
from astrolift_operations.alert_seed import seed_default_alert_rules
from astrolift_operations.models import AlertRule
from astrolift_registry.models import RegisteredApp

pytestmark = pytest.mark.django_db

_N = len(DEFAULT_RULES)


def _scaffold() -> Organization:
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    return org


def _make_app(org: Organization, *, slug: str) -> RegisteredApp:
    team = org.teams.first()
    project = org.projects.first()
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=slug.title(),
        slug=slug,
        k8s_namespace=f"{org.slug}-{slug}",
        provisioning_status="ready",
    )


# ---------------------------------------------------------------------------
# soft_delete_app_alert_rules — the ownership-key cleanup primitive
# ---------------------------------------------------------------------------


def test_soft_delete_app_alert_rules_removes_only_that_apps_rules():
    org = _scaffold()
    app_a = _make_app(org, slug="app-a")
    app_b = _make_app(org, slug="app-b")
    seed_default_alert_rules(app_a)
    seed_default_alert_rules(app_b)

    deleted = soft_delete_app_alert_rules(organization_id=org.id, app_slug="app-a")

    assert deleted == _N
    # app-a's rules are gone from the live set but retained (soft) in all_objects.
    assert AlertRule.objects.filter(target_id="app-a").count() == 0
    assert AlertRule.all_objects.filter(target_id="app-a", deleted_at__isnull=False).count() == _N
    # app-b's rules are untouched.
    assert AlertRule.objects.filter(target_id="app-b").count() == _N


def test_soft_delete_app_alert_rules_is_idempotent():
    org = _scaffold()
    app = _make_app(org, slug="idem")
    seed_default_alert_rules(app)

    first = soft_delete_app_alert_rules(organization_id=org.id, app_slug="idem")
    second = soft_delete_app_alert_rules(organization_id=org.id, app_slug="idem")

    assert first == _N
    assert second == 0  # nothing live left to soft-delete
    assert AlertRule.all_objects.filter(target_id="idem", deleted_at__isnull=False).count() == _N


# ---------------------------------------------------------------------------
# Deregister teardown — the app soft-delete pass also clears alert rules
# ---------------------------------------------------------------------------


def test_deregister_soft_delete_records_also_soft_deletes_alert_rules():
    """The reported leak: after deregister, the /alerts query still returned
    the app's seeded rules. The final soft-delete pass must now clear them."""
    from astrolift_workflows.activities.app_teardown import (
        _soft_delete_app_records_sync,
    )

    org = _scaffold()
    app = _make_app(org, slug="fileportal")
    seed_default_alert_rules(app)
    assert AlertRule.objects.filter(organization=org).count() == _N

    summary = _soft_delete_app_records_sync(app.pk)

    # The app is soft-deleted AND its alert rules go with it.
    app.refresh_from_db()
    assert app.deleted_at is not None
    assert summary.get("alert_rules") == _N
    # The /alerts query reads AlertRule.objects (default manager) — now empty.
    assert AlertRule.objects.filter(organization=org).count() == 0
    # Rows retained (soft) for audit, not hard-deleted.
    assert AlertRule.all_objects.filter(organization=org, deleted_at__isnull=False).count() == _N


# ---------------------------------------------------------------------------
# find_orphaned_app_alert_rules + the management command
# ---------------------------------------------------------------------------


def test_find_orphans_only_for_deleted_owner():
    org = _scaffold()
    live = _make_app(org, slug="live-app")
    gone = _make_app(org, slug="gone-app")
    seed_default_alert_rules(live)
    seed_default_alert_rules(gone)
    # Simulate the pre-fix deregister: the app row was soft-deleted but its
    # alert rules were left behind.
    gone.soft_delete()

    orphans = {slug: rules for _org_id, slug, rules in find_orphaned_app_alert_rules()}

    assert set(orphans) == {"gone-app"}
    assert len(orphans["gone-app"]) == _N


def test_command_dry_run_makes_no_changes():
    org = _scaffold()
    gone = _make_app(org, slug="gone-app")
    seed_default_alert_rules(gone)
    gone.soft_delete()
    assert AlertRule.objects.filter(target_id="gone-app").count() == _N

    out = StringIO()
    call_command("cleanup_orphaned_alert_rules", stdout=out)  # no --apply

    # Nothing soft-deleted; the rules are still live.
    assert AlertRule.objects.filter(target_id="gone-app").count() == _N
    text = out.getvalue()
    assert "[DRY RUN]" in text
    assert "gone-app" in text


def test_command_apply_soft_deletes_orphans():
    org = _scaffold()
    gone = _make_app(org, slug="gone-app")
    seed_default_alert_rules(gone)
    gone.soft_delete()

    out = StringIO()
    call_command("cleanup_orphaned_alert_rules", "--apply", stdout=out)

    assert AlertRule.objects.filter(target_id="gone-app").count() == 0
    assert AlertRule.all_objects.filter(target_id="gone-app", deleted_at__isnull=False).count() == _N
    assert "[DRY RUN]" not in out.getvalue()


def test_command_leaves_live_apps_rules_untouched():
    org = _scaffold()
    live = _make_app(org, slug="live-app")
    seed_default_alert_rules(live)

    out = StringIO()
    call_command("cleanup_orphaned_alert_rules", "--apply", stdout=out)

    # The owning app is live, so its rules are not orphans.
    assert AlertRule.objects.filter(target_id="live-app").count() == _N


def test_command_ignores_slug_reclaimed_by_new_app():
    """Slug reuse: a soft-deleted app's rules are shared by name with a NEW
    app that re-claimed the slug (seeder is idempotent by name). Because a
    live app now holds the slug, the rules are NOT orphaned."""
    org = _scaffold()
    first = _make_app(org, slug="recycled")
    seed_default_alert_rules(first)
    first.soft_delete()
    # A new app re-claims the slug (partial-unique index frees it on soft-delete).
    second = _make_app(org, slug="recycled")
    # Re-seed is a no-op (names already exist live) — the rules stay in effect.
    seed_default_alert_rules(second)

    out = StringIO()
    call_command("cleanup_orphaned_alert_rules", "--apply", stdout=out)

    # A live app holds the slug → its alert rules are preserved.
    assert AlertRule.objects.filter(target_id="recycled").count() == _N
