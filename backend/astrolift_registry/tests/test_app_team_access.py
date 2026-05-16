"""Tests for AppTeamAccess — multi-team access to a RegisteredApp.

Covers the three mutations:
- ``moveAppToTeam``: re-points the home team FK + materializes an
  OWNER row for the target + downgrades the previous home to DEPLOYER.
- ``grantTeamAccessToApp``: upserts a join row. Idempotent re-grant.
- ``revokeTeamAccessFromApp``: soft-deletes the row, refuses last
  OWNER removal.

Plus the backfill behavior: any existing ``(app, team)`` FK pair
must produce an active ``AppTeamAccess(OWNER)`` row after the
migration applies.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import AppTeamAccess, RegisteredApp
from astrolift_registry.schema.mutations import (
    GrantTeamAccessInput,
    MoveAppToTeamInput,
    RegistryMutation,
    RevokeTeamAccessInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _scaffold():
    """Org with two teams (each with one project) + an app pinned to
    team_a / project_a. Tests grant permissions explicitly per case."""

    org = Organization.objects.create(name="Acme", slug="acme")
    team_a = Team.objects.create(organization=org, name="Engineering", slug="eng")
    team_b = Team.objects.create(organization=org, name="Platform", slug="platform")
    project_a = Project.objects.create(organization=org, team=team_a, name="Demo", slug="demo")
    project_b = Project.objects.create(organization=org, team=team_b, name="Tools", slug="tools")
    app = RegisteredApp.objects.create(
        organization=org, team=team_a, project=project_a, name="App", slug="my-app"
    )
    # Home-team row, mirroring what the registration flow would do.
    AppTeamAccess.objects.create(
        registered_app=app, team=team_a, access_level=AppTeamAccess.AccessLevel.OWNER.value
    )
    return org, team_a, team_b, project_a, project_b, app


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- moveAppToTeam ----------------------------------------------------


def test_move_app_to_team_happy_path(permission_resolver):
    org, team_a, team_b, _, project_b, app = _scaffold()
    # The project under team_a doesn't belong to team_b, so before the
    # move we re-anchor the app to project_b (under team_b). In
    # production the operator would use ``transferApp`` to move both
    # at once; ``moveAppToTeam`` only handles the team flip.
    app.project = project_b
    app.save()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(org):
        result = RegistryMutation().move_app_to_team(
            _info(),
            input=MoveAppToTeamInput(app_id=str(app.guid), target_team_id=str(team_b.guid)),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.team_id == team_b.id
    # Target gains OWNER.
    target_grant = AppTeamAccess.objects.get(registered_app=app, team=team_b, deleted_at__isnull=True)
    assert target_grant.access_level == AppTeamAccess.AccessLevel.OWNER.value
    # Previous home team is downgraded (not revoked).
    prev_grant = AppTeamAccess.objects.get(registered_app=app, team=team_a, deleted_at__isnull=True)
    assert prev_grant.access_level == AppTeamAccess.AccessLevel.DEPLOYER.value


def test_move_app_to_team_refuses_cross_org(permission_resolver):
    org, _, _, _, _, app = _scaffold()
    other_org = Organization.objects.create(name="Other", slug="other")
    other_team = Team.objects.create(organization=other_org, name="Ops", slug="ops")
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(org):
        result = RegistryMutation().move_app_to_team(
            _info(),
            input=MoveAppToTeamInput(app_id=str(app.guid), target_team_id=str(other_team.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert result.errors[0].field == "targetTeamId"


def test_move_app_to_team_refuses_when_project_dangles(permission_resolver):
    """Project still under the OLD team — refuse the move outright."""
    org, _, team_b, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(org):
        result = RegistryMutation().move_app_to_team(
            _info(),
            input=MoveAppToTeamInput(app_id=str(app.guid), target_team_id=str(team_b.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_move_app_to_team_no_permission_denied():
    org, _, team_b, _, project_b, app = _scaffold()
    app.project = project_b
    app.save()

    with _tenant(org):
        result = RegistryMutation().move_app_to_team(
            _info(),
            input=MoveAppToTeamInput(app_id=str(app.guid), target_team_id=str(team_b.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_move_app_to_team_idempotent_on_same_team(permission_resolver):
    org, team_a, _, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(org):
        result = RegistryMutation().move_app_to_team(
            _info(),
            input=MoveAppToTeamInput(app_id=str(app.guid), target_team_id=str(team_a.guid)),
        )

    assert result.ok
    app.refresh_from_db()
    assert app.team_id == team_a.id


# ---- grantTeamAccessToApp ---------------------------------------------


def test_grant_team_access_creates_row(permission_resolver):
    org, _, team_b, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(org):
        result = RegistryMutation().grant_team_access_to_app(
            _info(),
            input=GrantTeamAccessInput(app_id=str(app.guid), team_id=str(team_b.guid), access_level="viewer"),
        )

    assert result.ok, result.errors
    grant = AppTeamAccess.objects.get(registered_app=app, team=team_b, deleted_at__isnull=True)
    assert grant.access_level == "viewer"


def test_grant_team_access_is_idempotent(permission_resolver):
    org, _, team_b, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(org):
        for _ in range(3):
            result = RegistryMutation().grant_team_access_to_app(
                _info(),
                input=GrantTeamAccessInput(
                    app_id=str(app.guid),
                    team_id=str(team_b.guid),
                    access_level="deployer",
                ),
            )
            assert result.ok

    # Only one active row exists.
    active = AppTeamAccess.objects.filter(registered_app=app, team=team_b, deleted_at__isnull=True)
    assert active.count() == 1
    assert active.first().access_level == "deployer"


def test_grant_team_access_upgrades_level(permission_resolver):
    org, _, team_b, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    AppTeamAccess.objects.create(registered_app=app, team=team_b, access_level="viewer")

    with _tenant(org):
        result = RegistryMutation().grant_team_access_to_app(
            _info(),
            input=GrantTeamAccessInput(app_id=str(app.guid), team_id=str(team_b.guid), access_level="owner"),
        )

    assert result.ok
    grant = AppTeamAccess.objects.get(registered_app=app, team=team_b, deleted_at__isnull=True)
    assert grant.access_level == "owner"


def test_grant_team_access_rejects_bad_level(permission_resolver):
    org, _, team_b, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(org):
        result = RegistryMutation().grant_team_access_to_app(
            _info(),
            input=GrantTeamAccessInput(app_id=str(app.guid), team_id=str(team_b.guid), access_level="admin"),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "accessLevel"


def test_grant_team_access_refuses_cross_org(permission_resolver):
    org, _, _, _, _, app = _scaffold()
    other_org = Organization.objects.create(name="Other", slug="other")
    other_team = Team.objects.create(organization=other_org, name="Core", slug="core")
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(org):
        result = RegistryMutation().grant_team_access_to_app(
            _info(),
            input=GrantTeamAccessInput(
                app_id=str(app.guid),
                team_id=str(other_team.guid),
                access_level="viewer",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


# ---- revokeTeamAccessFromApp ------------------------------------------


def test_revoke_team_access_soft_deletes_row(permission_resolver):
    org, _, team_b, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    AppTeamAccess.objects.create(registered_app=app, team=team_b, access_level="deployer")

    with _tenant(org):
        result = RegistryMutation().revoke_team_access_from_app(
            _info(),
            input=RevokeTeamAccessInput(app_id=str(app.guid), team_id=str(team_b.guid)),
        )

    assert result.ok
    assert not AppTeamAccess.objects.filter(registered_app=app, team=team_b, deleted_at__isnull=True).exists()
    # The row itself still exists (audit trail), just soft-deleted.
    assert AppTeamAccess.all_objects.filter(registered_app=app, team=team_b).exists()


def test_revoke_team_access_refuses_last_owner(permission_resolver):
    """Only one OWNER row exists (the home team's). Refusing the revoke
    keeps the app from being orphaned with no team that can manage
    it."""
    org, team_a, _, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _tenant(org):
        result = RegistryMutation().revoke_team_access_from_app(
            _info(),
            input=RevokeTeamAccessInput(app_id=str(app.guid), team_id=str(team_a.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    # Row still active.
    assert AppTeamAccess.objects.filter(registered_app=app, team=team_a, deleted_at__isnull=True).exists()


def test_revoke_team_access_allowed_when_another_owner_remains(permission_resolver):
    org, _, team_b, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    # Second OWNER row so revoking the home team's grant is safe.
    AppTeamAccess.objects.create(registered_app=app, team=team_b, access_level="owner")

    with _tenant(org):
        result = RegistryMutation().revoke_team_access_from_app(
            _info(),
            input=RevokeTeamAccessInput(app_id=str(app.guid), team_id=str(team_b.guid)),
        )

    assert result.ok


def test_revoke_team_access_unknown_grant_returns_not_found(permission_resolver):
    org, _, team_b, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    # No row exists for team_b at all.

    with _tenant(org):
        result = RegistryMutation().revoke_team_access_from_app(
            _info(),
            input=RevokeTeamAccessInput(app_id=str(app.guid), team_id=str(team_b.guid)),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- backfill ---------------------------------------------------------


def test_backfill_produced_owner_row_for_existing_fk_pair():
    """The migration's RunPython op materializes an OWNER row for
    every active ``(app, team)`` FK pair. Verify by clearing the join
    table and re-running the same backfill logic in process.

    Done in-process (not via ``call_command("migrate")``) so the test
    doesn't depend on the test runner's migration replay flag, and so
    the assertion is on the exact code path that ships in the
    migration file.
    """

    # Migration modules start with a digit, so dotted-import won't
    # work; load via importlib.
    import importlib

    migration_module = importlib.import_module("astrolift_registry.migrations.0006_appteamaccess")

    org = Organization.objects.create(name="Backfill", slug="backfill-org")
    team = Team.objects.create(organization=org, name="Core", slug="core")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p")
    app = RegisteredApp.objects.create(
        organization=org, team=team, project=project, name="Old App", slug="old-app"
    )
    # Simulate the pre-migration state: an existing FK pair with no
    # AppTeamAccess row.
    AppTeamAccess.all_objects.filter(registered_app=app).delete()
    assert not AppTeamAccess.objects.filter(registered_app=app).exists()

    # The migration uses ``apps.get_model``; pass the real Django
    # apps registry — the model API is the same.
    from django.apps import apps as django_apps

    migration_module._backfill_owner_rows(django_apps, None)

    grant = AppTeamAccess.objects.get(registered_app=app, team=team, deleted_at__isnull=True)
    assert grant.access_level == AppTeamAccess.AccessLevel.OWNER.value
