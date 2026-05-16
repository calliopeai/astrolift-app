"""Tests for the Settings page CI-setup wiring (#382).

Two surfaces under test:

* The app-side ``RegisteredAppType`` now exposes ``ecr_repo_uri`` and
  ``ecr_push_role_arn`` so the UI can render the GitHub Actions secret
  table without a second round trip.
* The platform-level helper ``astroliftPlatformApiUrl`` returns the
  value an operator pastes into the ``ASTROLIFT_API_URL`` secret. It
  gates on viewer authentication — anonymous callers must be denied so
  we don't leak the install's API origin.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.schema.types import app_to_type
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _user(username: str, **kw):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test", **kw)


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """The user-create signal indexes profiles into OpenSearch. The
    test environment doesn't run OpenSearch so we stub the indexer."""

    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


def _scaffold_app() -> RegisteredApp:
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Web",
        slug="web",
        source_kind=RegisteredApp.SourceKind.GITHUB,
        source_repo="acme/web",
        registry_repo_uri="123456789012.dkr.ecr.us-west-2.amazonaws.com/acme/web",
        push_role_ref="arn:aws:iam::123456789012:role/astrolift-acme-web-push",
    )


def _info(viewer=None):
    request = SimpleNamespace(user=viewer) if viewer is not None else None
    return SimpleNamespace(context=SimpleNamespace(request=request, user=viewer))


def test_app_type_exposes_ecr_repo_uri_and_push_role_arn():
    """``app_to_type`` mirrors the registry URI into ``ecr_repo_uri``
    and surfaces ``push_role_ref`` as ``ecr_push_role_arn`` so the
    settings page can render both alongside the other Actions
    secrets without a second query."""

    app = _scaffold_app()
    t = app_to_type(app)
    assert t.ecr_repo_uri == "123456789012.dkr.ecr.us-west-2.amazonaws.com/acme/web"
    assert t.ecr_push_role_arn == "arn:aws:iam::123456789012:role/astrolift-acme-web-push"
    # Compatibility: the legacy ``registry_repo_uri`` field stays
    # populated for callers that already key off it.
    assert t.registry_repo_uri == t.ecr_repo_uri


def test_app_type_blank_push_role_when_not_yet_bootstrapped():
    """Apps registered before the IRSA bootstrap (#309) has run will
    have ``push_role_ref=''`` and the GraphQL field must surface an
    empty string (not raise / not collapse to null) so the UI can
    render a "pending provisioning" affordance for that one row."""

    org = Organization.objects.create(name="Beta", slug="beta")
    team = Team.objects.create(organization=org, name="Plat", slug="plat")
    project = Project.objects.create(organization=org, team=team, name="App", slug="app")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Pending",
        slug="pending",
        source_kind=RegisteredApp.SourceKind.GITHUB,
        registry_repo_uri="",
        push_role_ref="",
    )
    t = app_to_type(app)
    assert t.ecr_repo_uri == ""
    assert t.ecr_push_role_arn == ""


def test_app_visible_to_app_read_viewer(seed_cluster):
    """``app.read`` viewers reach the app through ``astrolift_app``
    and see the new CI-setup fields populated. The role + binding
    scaffold here mirrors the production permission chain so we know
    the fields surface via the gated resolver, not just the helper.
    """

    app = _scaffold_app()
    seed_cluster(app.organization)
    role = Role.objects.create(name="app-reader", slug="app-reader", permissions=["app.read"])
    viewer = _user("reader")
    RoleBinding.objects.create(user=viewer, role=role, scope_kind="ORG", scope_id=app.organization_id)

    info = _info(viewer=viewer)
    with tenant_context(TenantContext(organization_id=app.organization_id, actor_user_id=viewer.id)):
        result = RegistryQuery().astrolift_app(info, slug="web")

    assert result is not None
    assert result.ecr_repo_uri.endswith("/acme/web")
    assert result.ecr_push_role_arn.startswith("arn:aws:iam::")


@override_settings(PLATFORM_API_URL="https://api.astrolift.example.com")
def test_platform_api_url_returns_configured_value():
    """The helper strips a single trailing slash so the UI can
    template ``${URL}/api/v1/deploys`` without a double slash."""

    viewer = _user("authed")
    info = _info(viewer=viewer)
    assert RegistryQuery().astrolift_platform_api_url(info) == "https://api.astrolift.example.com"


@override_settings(PLATFORM_API_URL="https://api.astrolift.example.com/")
def test_platform_api_url_strips_trailing_slash():
    viewer = _user("authed2")
    info = _info(viewer=viewer)
    assert RegistryQuery().astrolift_platform_api_url(info) == "https://api.astrolift.example.com"


def test_platform_api_url_denies_anonymous():
    """Anonymous callers must not receive the API base URL —
    otherwise we leak the install's origin to unauthenticated
    probes. We assert a hard ``PermissionError`` rather than a
    silent empty string so the GraphQL layer surfaces a real
    error."""

    info = _info(viewer=None)
    with pytest.raises(PermissionError):
        RegistryQuery().astrolift_platform_api_url(info)


def test_platform_api_url_denies_anonymous_user_object():
    """Django's AnonymousUser has ``is_authenticated == False``; the
    gate must treat that the same as ``None``."""

    anon = SimpleNamespace(is_authenticated=False)
    info = _info(viewer=anon)
    with pytest.raises(PermissionError):
        RegistryQuery().astrolift_platform_api_url(info)
