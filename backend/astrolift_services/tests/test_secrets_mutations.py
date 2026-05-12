"""Tests for the app-secrets GraphQL mutations (#279)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import AppSecretBundleRef, SecretBundle
from astrolift_services.schema.mutations import (
    AttachSecretBundleInput,
    BulkImportAppSecretsInput,
    DeleteAppSecretInput,
    DetachSecretBundleInput,
    ServicesMutation,
    SetAppSecretInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


_BASE_TOML = """\
astrolift_version = 1
[app]
name = "hello"
slug = "hello-app"

[env]
KEEP_ME = "yes"

[[workloads]]
name = "web"
kind = "service"
"""


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo",
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="local",
        name="Local",
        provider_plugin_id="k8s_native",
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
        manifest_raw=_BASE_TOML,
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    bundle = SecretBundle.objects.create(
        organization=org,
        team=team,
        slug="prod-secrets",
        name="Prod Secrets",
        backend_ref="vault:/acme/prod",
    )
    return org, app, env, bundle


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- setAppSecret ------------------------------------------------


def test_set_app_secret_writes_to_staging(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="API_KEY",
                value="abc123",
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    staged = app.manifest_raw_staged
    assert staged != ""  # staging populated
    parsed = read_app_env(staged)
    assert parsed["API_KEY"] == "abc123"
    assert parsed["KEEP_ME"] == "yes"  # preserved


def test_set_app_secret_invalid_key_rejected(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="bad-key",
                value="x",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "key"


def test_set_app_secret_unknown_app_returns_not_found(permission_resolver):
    org, _, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug="missing",
                key="K",
                value="v",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_set_app_secret_requires_permission():
    org, app, _, _ = _scaffold()
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="K",
                value="v",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- deleteAppSecret --------------------------------------------


def test_delete_app_secret_removes_key(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().delete_app_secret(
            _info(),
            input=DeleteAppSecretInput(
                app_slug=app.slug,
                key="KEEP_ME",
            ),
        )

    assert result.ok
    app.refresh_from_db()
    parsed = read_app_env(app.manifest_raw_staged)
    assert "KEEP_ME" not in parsed


def test_delete_app_secret_missing_key_not_found(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().delete_app_secret(
            _info(),
            input=DeleteAppSecretInput(
                app_slug=app.slug,
                key="NEVER_EXISTED",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- bulkImportAppSecrets ---------------------------------------


def test_bulk_import_parses_and_stages(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    text = """\
# Production envs
DATABASE_URL=postgres://prod
LOG_LEVEL=warn
SENTRY_DSN="https://x@y.io/1"
"""
    with _ctx(org):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(),
            input=BulkImportAppSecretsInput(
                app_slug=app.slug,
                dotenv_text=text,
            ),
        )

    assert result.ok, result.errors
    assert set(result.data.keys_set) == {
        "DATABASE_URL",
        "LOG_LEVEL",
        "SENTRY_DSN",
    }
    app.refresh_from_db()
    parsed = read_app_env(app.manifest_raw_staged)
    assert parsed["DATABASE_URL"] == "postgres://prod"
    assert parsed["KEEP_ME"] == "yes"  # preserved


def test_bulk_import_empty_returns_validation(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(),
            input=BulkImportAppSecretsInput(
                app_slug=app.slug,
                dotenv_text="# only comments\n",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


# ---- attachSecretBundle ------------------------------------------


def test_attach_secret_bundle_creates_ref(permission_resolver):
    org, app, env, bundle = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().attach_secret_bundle(
            _info(),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
                prefix="STRIPE_",
            ),
        )

    assert result.ok, result.errors
    refs = AppSecretBundleRef.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
    )
    assert refs.count() == 1
    assert refs.first().prefix == "STRIPE_"


def test_attach_secret_bundle_idempotent_updates_prefix(
    permission_resolver,
):
    org, app, env, bundle = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        ServicesMutation().attach_secret_bundle(
            _info(),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
                prefix="A_",
            ),
        )
        result = ServicesMutation().attach_secret_bundle(
            _info(),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
                prefix="B_",
            ),
        )

    assert result.ok
    refs = AppSecretBundleRef.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
    )
    assert refs.count() == 1
    assert refs.first().prefix == "B_"


def test_attach_secret_bundle_unknown_env_not_found(permission_resolver):
    org, app, _, bundle = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().attach_secret_bundle(
            _info(),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name="never",
                bundle_slug=bundle.slug,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_detach_secret_bundle_soft_deletes(permission_resolver):
    org, app, env, bundle = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    ref = AppSecretBundleRef.objects.create(
        registered_app=app,
        app_environment=env,
        secret_bundle=bundle,
    )

    with _ctx(org):
        result = ServicesMutation().detach_secret_bundle(
            _info(),
            input=DetachSecretBundleInput(
                attachment_id=str(ref.guid),
            ),
        )

    assert result.ok
    ref.refresh_from_db()
    assert ref.deleted_at is not None
