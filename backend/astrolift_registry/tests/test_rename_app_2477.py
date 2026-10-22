"""Renaming an app in place (#2477).

``updateApp`` with a ``name`` is the display-name rename. The slug stays, and
so does everything named after it: the namespace, the hostname, the registry
repository and the CI push role. The name is held to what registration already
guarantees (an app is never nameless) and to its column, at the API boundary.
"""

from __future__ import annotations

import pytest

from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

UPDATE_APP = """
mutation($input: UpdateAppInput!) {
  updateApp(input: $input) { ok errors { code field message } data { name slug } }
}
"""

SLUG_NAMED = ("slug", "k8s_namespace", "subdomain", "registry_repo_uri", "push_role_ref")


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def world():
    world = ScopeWorld("rename2477")
    world.user = make_user("rename2477")
    app = world.medops_app
    app.subdomain = app.slug
    app.registry_repo_uri = f"registry.example.test/{world.org.slug}/{app.slug}"
    app.push_role_ref = f"astrolift-{world.org.slug}-{app.slug}-push"
    app.save()
    return world


def _update(world, app, **fields):
    from config.schema import schema

    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=world.user.pk)
    ):
        result = schema.execute_sync(
            UPDATE_APP,
            variable_values={"input": {"id": str(app.guid), **fields}},
            context_value=make_info(world.user).context,
        )
    assert result.errors is None, result.errors
    return result.data["updateApp"]


def _grant_update(world):
    bind_role(
        world.user,
        permissions=[Permission.APP_UPDATE],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="app-updater",
    )


def test_rename_changes_the_name_and_nothing_named_after_the_slug(world):
    _grant_update(world)
    app = world.medops_app
    before = {field: getattr(app, field) for field in SLUG_NAMED}

    out = _update(world, app, name="  Fossil Repos Brain  ", ifMatchVersion=app.version)

    assert out == {"ok": True, "errors": [], "data": {"name": "Fossil Repos Brain", "slug": app.slug}}
    app.refresh_from_db()
    assert app.name == "Fossil Repos Brain"
    assert {field: getattr(app, field) for field in SLUG_NAMED} == before


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_a_blank_name_is_refused_and_nothing_is_written(world, name):
    _grant_update(world)
    app = world.medops_app
    version = app.version

    out = _update(world, app, name=name, description="written only with a valid name")

    assert out == {
        "ok": False,
        "errors": [{"code": "VALIDATION", "field": "name", "message": "name must not be empty"}],
        "data": None,
    }
    app.refresh_from_db()
    assert (app.name, app.description, app.version) == ("QsOps", "", version)


def test_a_name_longer_than_its_column_is_refused(world):
    _grant_update(world)
    app = world.medops_app
    version = app.version

    out = _update(world, app, name="n" * 201)

    assert out == {
        "ok": False,
        "errors": [{"code": "VALIDATION", "field": "name", "message": "name must be at most 200 characters"}],
        "data": None,
    }
    app.refresh_from_db()
    assert (app.name, app.version) == ("QsOps", version)


def test_the_length_is_measured_after_trimming(world):
    _grant_update(world)
    app = world.medops_app

    out = _update(world, app, name=f"  {'n' * 200}  ")

    assert out["ok"], out["errors"]
    app.refresh_from_db()
    assert app.name == "n" * 200


def test_an_update_without_a_name_keeps_the_name(world):
    _grant_update(world)
    app = world.medops_app

    out = _update(world, app, description="Reads the fossil repositories")

    assert out["ok"], out["errors"]
    app.refresh_from_db()
    assert (app.name, app.description) == ("QsOps", "Reads the fossil repositories")


@pytest.mark.parametrize(
    "permission,target",
    [
        (Permission.APP_READ, "medops_app"),
        (Permission.APP_UPDATE, "platform_app"),
    ],
    ids=["read-only-on-own-app", "update-on-sibling-team-app"],
)
def test_a_caller_without_app_update_on_the_app_is_refused_before_the_name_is_read(world, permission, target):
    bind_role(world.user, permissions=[permission], kind="TEAM", scope_id=world.medops.pk, slug="caller")
    app = getattr(world, target)
    name, version = app.name, app.version

    out = _update(world, app, name="")

    assert [error["code"] for error in out["errors"]] == ["PERMISSION_DENIED"]
    app.refresh_from_db()
    assert (app.name, app.version) == (name, version)


def test_another_organizations_app_is_not_found_even_with_an_invalid_name(world):
    foreign = ScopeWorld("rename2477foreign")
    bind_role(
        world.user,
        permissions=[Permission.APP_UPDATE],
        kind="ORG",
        scope_id=world.org.pk,
        slug="org-updater",
    )
    app = foreign.medops_app
    version = app.version

    for name in ("", "Taken Over"):
        out = _update(world, app, name=name)
        assert [error["code"] for error in out["errors"]] == ["NOT_FOUND"]

    app.refresh_from_db()
    assert (app.name, app.version) == ("QsOps", version)
