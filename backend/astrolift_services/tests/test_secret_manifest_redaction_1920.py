"""#1920 — the secret-write mutations echo the staged manifest back to
the caller. That response must mask every ``[env]`` value unless the
caller holds ``secret.read`` and is step-up elevated — the same gate
``revealAppSecret`` enforces — even though these mutations themselves
only require ``app.update``. Without the fix, an ``app.update``-only
caller writing one key could read every other secret already staged
on the app through the response payload.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from constance.test import override_config
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_identity.session_elevation import METHOD_PASSWORD, elevate
from astrolift_registry.models import RegisteredApp
from astrolift_services.schema.mutations import (
    BulkImportAppSecretsInput,
    DeleteAppSecretInput,
    RotateAppSecretInput,
    ServicesMutation,
    SetAppSecretInput,
)
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_SECRET_VALUE = "sk-live-super-secret-456"
_OTHER_KEY = "OTHER_SECRET"

_BASE_TOML = f"""\
astrolift_version = 1
name = "hello"

[env]
{_OTHER_KEY} = "{_SECRET_VALUE}"

[[workloads]]
name = "web"
kind = "deployment"
"""


class _FakeSession(dict):
    modified = False


def _info(user, *, session: dict | None = None):
    session = session if session is not None else _FakeSession()
    request = SimpleNamespace(user=user, session=session, META={})
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


def _make_user(username: str):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _grant(user, org, *permissions: str) -> None:
    """Real Role + org-scoped RoleBinding. ``can_reveal_app_secrets``'s
    single-app fallback reads ``RoleBinding`` rows directly via
    ``resolve_effective_permissions`` — the ``permission_resolver``
    stub fixture other tests in this module use is invisible to it."""
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


# ---- setAppSecret ---------------------------------------------------


def test_set_app_secret_masks_other_secrets_for_app_update_only():
    org, app = _scaffold()
    caller = _make_user("set-update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = ServicesMutation().set_app_secret(
            _info(caller),
            input=SetAppSecretInput(app_slug=app.slug, key="NEW_KEY", value="new-value"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE not in result.data.raw_manifest_staged
    assert _OTHER_KEY in result.data.raw_manifest_staged
    assert "[REDACTED]" in result.data.raw_manifest_staged


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_set_app_secret_reveals_for_secret_read_elevated():
    org, app = _scaffold()
    caller = _make_user("set-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = ServicesMutation().set_app_secret(
            _info(caller, session=session),
            input=SetAppSecretInput(app_slug=app.slug, key="NEW_KEY", value="new-value"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE in result.data.raw_manifest_staged


# ---- rotateAppSecret --------------------------------------------------


def test_rotate_app_secret_masks_other_secrets_for_app_update_only():
    org, app = _scaffold()
    caller = _make_user("rotate-update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = ServicesMutation().rotate_app_secret(
            _info(caller),
            input=RotateAppSecretInput(app_slug=app.slug, key=_OTHER_KEY, value="rotated-value"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE not in result.data.raw_manifest_staged
    assert "rotated-value" not in result.data.raw_manifest_staged
    assert _OTHER_KEY in result.data.raw_manifest_staged


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_rotate_app_secret_reveals_for_secret_read_elevated():
    org, app = _scaffold()
    caller = _make_user("rotate-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = ServicesMutation().rotate_app_secret(
            _info(caller, session=session),
            input=RotateAppSecretInput(app_slug=app.slug, key=_OTHER_KEY, value="rotated-value"),
        )

    assert result.ok is True, result.errors
    assert "rotated-value" in result.data.raw_manifest_staged


# ---- deleteAppSecret ----------------------------------------------


def test_delete_app_secret_masks_remaining_secrets_for_app_update_only():
    org, app = _scaffold()
    app.manifest_raw = _BASE_TOML.replace(
        "[[workloads]]",
        'KEEP_SECRET = "keep-me-secret"\n\n[[workloads]]',
    )
    app.save(update_fields=["manifest_raw"])
    caller = _make_user("delete-update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = ServicesMutation().delete_app_secret(
            _info(caller),
            input=DeleteAppSecretInput(app_slug=app.slug, key=_OTHER_KEY),
        )

    assert result.ok is True, result.errors
    assert "keep-me-secret" not in result.data.raw_manifest_staged
    assert "KEEP_SECRET" in result.data.raw_manifest_staged


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_delete_app_secret_reveals_for_secret_read_elevated():
    org, app = _scaffold()
    app.manifest_raw = _BASE_TOML.replace(
        "[[workloads]]",
        'KEEP_SECRET = "keep-me-secret"\n\n[[workloads]]',
    )
    app.save(update_fields=["manifest_raw"])
    caller = _make_user("delete-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = ServicesMutation().delete_app_secret(
            _info(caller, session=session),
            input=DeleteAppSecretInput(app_slug=app.slug, key=_OTHER_KEY),
        )

    assert result.ok is True, result.errors
    assert "keep-me-secret" in result.data.raw_manifest_staged


# ---- bulkImportAppSecrets ------------------------------------------


def test_bulk_import_app_secrets_masks_existing_secrets_for_app_update_only():
    org, app = _scaffold()
    caller = _make_user("bulk-update-only")
    _grant(caller, org, "app.update")

    with _ctx(org, caller):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(caller),
            input=BulkImportAppSecretsInput(app_slug=app.slug, dotenv_text="IMPORTED_KEY=imported-value\n"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE not in result.data.raw_manifest_staged
    assert "imported-value" not in result.data.raw_manifest_staged
    assert _OTHER_KEY in result.data.raw_manifest_staged
    assert "IMPORTED_KEY" in result.data.raw_manifest_staged


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_bulk_import_app_secrets_reveals_for_secret_read_elevated():
    org, app = _scaffold()
    caller = _make_user("bulk-secret-read-elevated")
    _grant(caller, org, "app.update", "app.read", "secret.read")
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)

    with _ctx(org, caller):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(caller, session=session),
            input=BulkImportAppSecretsInput(app_slug=app.slug, dotenv_text="IMPORTED_KEY=imported-value\n"),
        )

    assert result.ok is True, result.errors
    assert _SECRET_VALUE in result.data.raw_manifest_staged
    assert "imported-value" in result.data.raw_manifest_staged
