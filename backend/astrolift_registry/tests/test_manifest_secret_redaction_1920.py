"""#1920 — raw manifest text must mask ``[env]`` values unless the
caller holds ``secret.read`` and is step-up elevated, the same gate
``revealAppSecret`` enforces.

Covers the ``RegisteredApp`` GraphQL type (single-app + list queries,
which take two different code paths inside ``app_to_type`` — a fresh
per-app RBAC lookup vs. a pre-resolved bulk ``viewerPermissions`` map)
and the manifest-stage mutation that echoes raw text directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from constance.test import override_config
from django.contrib.auth import get_user_model

from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_identity.session_elevation import METHOD_PASSWORD, elevate
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegistryMutation
from astrolift_registry.schema.mutations.types import UpdateManifestInput
from astrolift_registry.schema.queries import RegistryQuery
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_SECRET_VALUE = "sk-live-super-secret-123"

_BASE_TOML = f"""\
astrolift_version = 1
name = "hello"

[env]
API_KEY = "{_SECRET_VALUE}"
LOG_LEVEL = "info"

[[workloads]]
name = "web"
kind = "deployment"
"""

_STAGED_TOML = f"""\
astrolift_version = 1
name = "hello"

[env]
API_KEY = "{_SECRET_VALUE}"
LOG_LEVEL = "debug"

[[workloads]]
name = "web"
kind = "deployment"
"""


class _FakeSession(dict):
    """Dict-like stand-in for ``HttpRequest.session`` (mirrors the
    step-up test harness's shim)."""

    modified = False


def _info(user, *, session: dict | None = None):
    session = session if session is not None else _FakeSession()
    request = SimpleNamespace(user=user, session=session, META={})
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


def _make_user(username: str) -> object:
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _grant(user, org, *permissions: str) -> None:
    """Real Role + org-scoped RoleBinding — ``resolve_effective_permissions``
    reads RoleBinding rows directly, so a stubbed permission resolver
    (as ``test_secrets_mutations.py`` uses) would be invisible to it."""
    role = Role.objects.create(
        name=f"role-{'-'.join(permissions)}-{user.pk}",
        slug=f"role-{'-'.join(permissions)}-{user.pk}",
        permissions=list(permissions),
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        manifest_raw=_BASE_TOML,
    )
    return org, app


def _ctx(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


# ---- astroliftApp (single-app detail query) -----------------------


def test_astrolift_app_masks_env_values_for_app_read_only(seed_cluster):
    org, app = _scaffold()
    seed_cluster(org)
    viewer = _make_user("app-read-only")
    _grant(viewer, org, "app.read")

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_app(_info(viewer), slug=app.slug)

    assert result is not None
    assert _SECRET_VALUE not in result.raw_manifest
    assert "API_KEY" in result.raw_manifest
    assert "[REDACTED]" in result.raw_manifest


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_astrolift_app_reveals_env_values_for_secret_read_elevated(seed_cluster):
    org, app = _scaffold()
    seed_cluster(org)
    viewer = _make_user("secret-read-elevated")
    _grant(viewer, org, "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_app(_info(viewer, session=session), slug=app.slug)

    assert result is not None
    assert _SECRET_VALUE in result.raw_manifest


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_astrolift_app_masks_for_secret_read_without_elevation(seed_cluster):
    """Holding secret.read is not enough on its own — the step-up half
    of the gate must also be satisfied, same as ``revealAppSecret``."""
    org, app = _scaffold()
    seed_cluster(org)
    viewer = _make_user("secret-read-unelevated")
    _grant(viewer, org, "app.read", "secret.read")

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_app(_info(viewer, session=_FakeSession()), slug=app.slug)

    assert result is not None
    assert _SECRET_VALUE not in result.raw_manifest


# ---- astroliftMyApps (list query — bulk viewerPermissions path) ---


def test_astrolift_my_apps_masks_env_values_for_app_read_only():
    org, app = _scaffold()
    viewer = _make_user("list-app-read-only")
    _grant(viewer, org, "app.read")

    with _ctx(org, viewer):
        results = RegistryQuery().astrolift_my_apps(_info(viewer))

    assert len(results) == 1
    assert _SECRET_VALUE not in results[0].raw_manifest
    assert "API_KEY" in results[0].raw_manifest


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_astrolift_my_apps_reveals_env_values_for_secret_read_elevated():
    org, app = _scaffold()
    viewer = _make_user("list-secret-read-elevated")
    _grant(viewer, org, "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, viewer):
        results = RegistryQuery().astrolift_my_apps(_info(viewer, session=session))

    assert len(results) == 1
    assert _SECRET_VALUE in results[0].raw_manifest


# ---- updateManifest mutation ---------------------------------------


def test_update_manifest_masks_env_values_for_app_update_only():
    """``app.update`` (not app.read/secret.read) is all this mutation
    requires to run — but its response must not become a side-door
    into every other secret already staged on the app (#1920)."""
    org, app = _scaffold()
    caller = _make_user("update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = RegistryMutation().update_manifest(
            _info(caller),
            input=UpdateManifestInput(id=GUID(str(app.guid)), raw_manifest=_STAGED_TOML),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE not in result.data.raw_manifest
    assert _SECRET_VALUE not in result.data.raw_manifest_staged
    assert "API_KEY" in result.data.raw_manifest_staged
    app.refresh_from_db()
    # The fix is response-shaping only — the stored staged text is untouched.
    assert app.manifest_raw_staged == _STAGED_TOML


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_update_manifest_reveals_env_values_for_secret_read_elevated():
    org, app = _scaffold()
    caller = _make_user("update-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = RegistryMutation().update_manifest(
            _info(caller, session=session),
            input=UpdateManifestInput(id=GUID(str(app.guid)), raw_manifest=_STAGED_TOML),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE in result.data.raw_manifest
    assert _SECRET_VALUE in result.data.raw_manifest_staged
