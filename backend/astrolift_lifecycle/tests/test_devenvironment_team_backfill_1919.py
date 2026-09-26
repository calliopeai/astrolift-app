"""Migration 0038 backfills ``DevEnvironment.team`` from its promoted app (#1919).

``team`` has been a nullable column since the very first migration (0023),
but nothing ever set it, so every pre-#1919 row has ``team_id IS NULL``.
The backfill derives it only where unambiguous: a promoted row inherits its
``RegisteredApp``'s team. Everything else -- never promoted, or promoted
into a since-deleted or team-less app -- is left null, which the fix
treats as org-admin-only.

Runs the migration's own function against the historical model state, the
same pattern ``test_orgs_with_a_dev_environment_get_the_builder_module``
(#1859) uses, rather than a full ``migrate`` round trip.
"""

from __future__ import annotations

import importlib

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import DevEnvironment
from astrolift_registry.models import RegisteredApp

pytestmark = pytest.mark.django_db

NAME = "0038_backfill_devenvironment_team"


def _migration_fn():
    migration = importlib.import_module(f"astrolift_lifecycle.migrations.{NAME}")
    historical = MigrationExecutor(connection).loader.project_state(("astrolift_lifecycle", NAME)).apps
    return migration.backfill_team_from_promoted_app, historical


def _org(suffix: str) -> Organization:
    return Organization.objects.create(name=f"Backfill {suffix}", slug=f"backfill-1919-{suffix}")


def _user(suffix: str):
    User = get_user_model()
    return User.objects.create(username=f"backfill-{suffix}", email=f"backfill-{suffix}@astrolift.dev")


def _dev(org, *, creator, promoted_app=None) -> DevEnvironment:
    return DevEnvironment.objects.create(
        organization=org,
        creator=creator,
        promoted_app=promoted_app,
        runtime=DevEnvironment.Runtime.PYTHON,
        status=(DevEnvironment.Status.PROMOTING if promoted_app else DevEnvironment.Status.TORN_DOWN),
        namespace=f"backfill-dev-{org.slug}",
    )


def test_a_promoted_row_inherits_its_apps_team():
    org = _org("promoted")
    team = Team.objects.create(organization=org, name="Eng", slug="backfill-eng")
    app = RegisteredApp.objects.create(
        organization=org, team=team, name="Shipped", slug="backfill-shipped", provisioning_status="ready"
    )
    dev = _dev(org, creator=_user("promoted"), promoted_app=app)
    assert dev.team_id is None

    backfill, historical = _migration_fn()
    backfill(historical, None)

    dev.refresh_from_db()
    assert dev.team_id == team.id


def test_an_unpromoted_row_is_left_null():
    org = _org("unpromoted")
    dev = _dev(org, creator=_user("unpromoted"))

    backfill, historical = _migration_fn()
    backfill(historical, None)

    dev.refresh_from_db()
    assert dev.team_id is None


def test_a_row_promoted_into_a_since_deleted_app_still_backfills():
    """The app's own later soft-delete doesn't erase what its team was: the
    migration reads the historical model's plain manager, not
    ``SoftDeleteManager``, so a soft-deleted app still counts."""
    org = _org("deleted-app")
    team = Team.objects.create(organization=org, name="Eng", slug="backfill-deleted-eng")
    app = RegisteredApp.objects.create(
        organization=org, team=team, name="Gone", slug="backfill-gone", provisioning_status="ready"
    )
    dev = _dev(org, creator=_user("deleted-app"), promoted_app=app)
    app.soft_delete()

    backfill, historical = _migration_fn()
    backfill(historical, None)

    dev.refresh_from_db()
    assert dev.team_id == team.id


def test_a_second_run_is_a_no_op():
    org = _org("rerun")
    team = Team.objects.create(organization=org, name="Ops", slug="backfill-ops")
    app = RegisteredApp.objects.create(
        organization=org, team=team, name="Shipped2", slug="backfill-shipped2", provisioning_status="ready"
    )
    dev = _dev(org, creator=_user("rerun"), promoted_app=app)

    backfill, historical = _migration_fn()
    backfill(historical, None)
    backfill(historical, None)

    dev.refresh_from_db()
    assert dev.team_id == team.id


def test_a_row_that_already_has_a_team_is_left_alone():
    """Post-#1919 rows always have a team already; the backfill only
    targets ``team_id IS NULL`` and must not touch anything else."""
    org = _org("already-set")
    real_team = Team.objects.create(organization=org, name="Eng", slug="backfill-real")
    other_team = Team.objects.create(organization=org, name="Ops", slug="backfill-other")
    app = RegisteredApp.objects.create(
        organization=org,
        team=other_team,
        name="Shipped3",
        slug="backfill-shipped3",
        provisioning_status="ready",
    )
    dev = _dev(org, creator=_user("already-set"), promoted_app=app)
    DevEnvironment.objects.filter(pk=dev.pk).update(team_id=real_team.id)

    backfill, historical = _migration_fn()
    backfill(historical, None)

    dev.refresh_from_db()
    assert dev.team_id == real_team.id
