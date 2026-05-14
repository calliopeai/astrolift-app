"""Tests for ``transferApp`` — re-parent a RegisteredApp under a
different team / project within the same org.

Permission contract:
- ``app.transfer`` on the source app
- ``app.create`` on the destination team/project

Both grants required (AND). Cross-org transfers are refused.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegistryMutation, TransferAppInput
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _grant_transfer(resolver):
    resolver.grant(Permission.APP_TRANSFER)
    resolver.grant(Permission.APP_CREATE)


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team_a = Team.objects.create(organization=org, name="Eng", slug="eng")
    team_b = Team.objects.create(organization=org, name="Ops", slug="ops")
    project_a = Project.objects.create(organization=org, team=team_a, name="Demo", slug="demo")
    project_b = Project.objects.create(organization=org, team=team_b, name="Tools", slug="tools")
    app = RegisteredApp.objects.create(
        organization=org, team=team_a, project=project_a, name="App", slug="my-app"
    )
    return org, team_a, team_b, project_a, project_b, app


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_transfer_to_new_project_under_new_team(permission_resolver):
    org, _, team_b, _, project_b, app = _scaffold()
    _grant_transfer(permission_resolver)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(
                app_id=str(app.guid),
                target_team_id=str(team_b.guid),
                target_project_id=str(project_b.guid),
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.team_id == team_b.id
    assert app.project_id == project_b.id


def test_transfer_with_only_project_inherits_its_team(permission_resolver):
    """Callers who supply only target_project_id get the project's
    team for free — the resolver follows the tree."""
    org, _, team_b, _, project_b, app = _scaffold()
    _grant_transfer(permission_resolver)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(
                app_id=str(app.guid),
                target_project_id=str(project_b.guid),
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.team_id == team_b.id
    assert app.project_id == project_b.id


def test_transfer_with_only_team_keeping_compatible_project_is_noop(permission_resolver):
    """If the current project already belongs to the target team
    (rare but possible when the team is the same), the mutation is
    a no-op."""
    org, team_a, _, project_a, _, app = _scaffold()
    _grant_transfer(permission_resolver)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(app_id=str(app.guid), target_team_id=str(team_a.guid)),
        )
    assert result.ok
    app.refresh_from_db()
    assert app.team_id == team_a.id
    assert app.project_id == project_a.id


def test_transfer_with_only_team_orphan_project_refused(permission_resolver):
    """Moving the team without moving the project would leave the
    app's project under the OLD team — refuse with PRECONDITION."""
    org, _, team_b, _, _, app = _scaffold()
    _grant_transfer(permission_resolver)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(app_id=str(app.guid), target_team_id=str(team_b.guid)),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert result.errors[0].field == "targetProjectId"


def test_transfer_unknown_app_returns_not_found(permission_resolver):
    org, *_ = _scaffold()
    _grant_transfer(permission_resolver)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(app_id="00000000-0000-0000-0000-000000000000"),
        )
    assert not result.ok
    assert result.errors[0].code in {"VALIDATION", "NOT_FOUND"}


def test_transfer_missing_targets_validation_error(permission_resolver):
    org, _, _, _, _, app = _scaffold()
    _grant_transfer(permission_resolver)

    with _tenant(org):
        result = RegistryMutation().transfer_app(_info(), input=TransferAppInput(app_id=str(app.guid)))
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


def test_transfer_to_unknown_team_returns_not_found(permission_resolver):
    org, _, _, _, _, app = _scaffold()
    _grant_transfer(permission_resolver)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(
                app_id=str(app.guid),
                target_team_id="00000000-0000-0000-0000-000000000000",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "targetTeamId"


def test_transfer_cross_org_refused(permission_resolver):
    org, _, _, _, _, app = _scaffold()
    other_org = Organization.objects.create(name="Other", slug="other")
    other_team = Team.objects.create(organization=other_org, name="Core", slug="core")
    other_project = Project.objects.create(organization=other_org, team=other_team, name="X", slug="x")
    _grant_transfer(permission_resolver)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(
                app_id=str(app.guid),
                target_team_id=str(other_team.guid),
                target_project_id=str(other_project.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_transfer_no_permission_denied(permission_resolver):
    org, _, team_b, _, project_b, app = _scaffold()
    # Grant only APP_CREATE, not APP_TRANSFER.
    permission_resolver.grant(Permission.APP_CREATE)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(
                app_id=str(app.guid),
                target_team_id=str(team_b.guid),
                target_project_id=str(project_b.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_transfer_partial_permission_denied(permission_resolver):
    org, _, team_b, _, project_b, app = _scaffold()
    # Grant only APP_TRANSFER, not APP_CREATE.
    permission_resolver.grant(Permission.APP_TRANSFER)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(
                app_id=str(app.guid),
                target_team_id=str(team_b.guid),
                target_project_id=str(project_b.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_transfer_idempotent_on_same_target(permission_resolver):
    org, team_a, _, project_a, _, app = _scaffold()
    _grant_transfer(permission_resolver)

    with _tenant(org):
        result = RegistryMutation().transfer_app(
            _info(),
            input=TransferAppInput(
                app_id=str(app.guid),
                target_team_id=str(team_a.guid),
                target_project_id=str(project_a.guid),
            ),
        )
    assert result.ok
    app.refresh_from_db()
    assert app.team_id == team_a.id
    assert app.project_id == project_a.id
