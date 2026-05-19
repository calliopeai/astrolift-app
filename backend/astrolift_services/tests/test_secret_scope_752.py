"""Tests for AppSecretMetadata scope field (#752).

Covers:
- scope defaults to 'all' on set/rotate writes that don't supply it
- setAppSecret with explicit scope persists it on the metadata row
- rotateAppSecret with explicit scope persists it
- setAppSecretMetadata standalone persists scope
- re-set resets scope to the supplied value (always written)
- resolver projects scope onto AppSecretType
- _allowed_scopes_for_env: production env gets {all, production}
- _allowed_scopes_for_env: preview env gets {all, preview, preview:<branch>}
- scope filtering: production-scoped secret hidden in preview env
- scope filtering: preview-scoped secret hidden in production env
- scope filtering: preview:<branch> secret visible only in matching branch env
- scope filtering: all-scoped secret visible everywhere
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.models.preview_environment import PreviewEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import AppSecretMetadata
from astrolift_services.schema.mutations import (
    RotateAppSecretInput,
    ServicesMutation,
    SetAppSecretInput,
    SetAppSecretMetadataInput,
)
from astrolift_services.schema.queries import ServicesQuery, _allowed_scopes_for_env
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_BASE_TOML = """\
astrolift_version = 1
name = "scope-app"

[env]
SHARED_KEY = "value"
PROD_ONLY = "secret"
PREVIEW_ONLY = "draft"

[[workloads]]
name = "web"
kind = "deployment"
"""


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _scaffold():
    org = Organization.objects.create(name="ScopeOrg", slug="scope-org")
    team = Team.objects.create(organization=org, name="Eng", slug="scope-eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="ScopeProj",
        slug="scope-proj",
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s Native",
                slug="k8s-native",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="k8s-native")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="scope-local",
        name="Local",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="ScopeApp",
        slug="scope-app",
        provisioning_status="ready",
        manifest_raw=_BASE_TOML,
    )
    prod_env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return org, app, cluster, prod_env


_pr_counter = 0


def _add_preview_env(
    app, cluster, *, env_name: str, branch: str
) -> tuple[AppEnvironment, PreviewEnvironment]:
    global _pr_counter
    _pr_counter += 1
    env = AppEnvironment.objects.create(
        registered_app=app,
        name=env_name,
        tenant_cluster=cluster,
    )
    preview = PreviewEnvironment.objects.create(
        registered_app=app,
        pr_number=_pr_counter,
        branch=branch,
        hostname=f"pr-{_pr_counter}.preview.example.com",
        namespace=f"preview-{_pr_counter}",
        app_environment=env,
        status=PreviewEnvironment.Status.RUNNING,
    )
    return env, preview


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- _allowed_scopes_for_env unit tests ---------------------------


def test_allowed_scopes_production_env():
    scopes = _allowed_scopes_for_env("production", {})
    assert "all" in scopes
    assert "production" in scopes
    assert "preview" not in scopes


def test_allowed_scopes_preview_env():
    scopes = _allowed_scopes_for_env("preview-feat", {"preview-feat": "feat/login"})
    assert "all" in scopes
    assert "preview" in scopes
    assert "preview:feat/login" in scopes
    assert "production" not in scopes


def test_allowed_scopes_unknown_env_treated_as_production():
    # An env_name not in preview_env_branches → treated as non-preview.
    scopes = _allowed_scopes_for_env("staging", {})
    assert "all" in scopes
    assert "production" in scopes
    assert "preview" not in scopes


# ---- scope persists on writes -------------------------------------


def test_set_app_secret_default_scope_is_all(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="SHARED_KEY", value="v"),
        )
    assert result.ok, result.errors
    row = AppSecretMetadata.objects.get(registered_app=app, key="SHARED_KEY")
    assert row.scope == "all"


def test_set_app_secret_explicit_scope_production(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="PROD_ONLY",
                value="secret",
                scope="production",
            ),
        )
    assert result.ok, result.errors
    row = AppSecretMetadata.objects.get(registered_app=app, key="PROD_ONLY")
    assert row.scope == "production"


def test_set_app_secret_explicit_scope_preview(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="PREVIEW_ONLY",
                value="draft",
                scope="preview",
            ),
        )
    assert result.ok, result.errors
    row = AppSecretMetadata.objects.get(registered_app=app, key="PREVIEW_ONLY")
    assert row.scope == "preview"


def test_rotate_app_secret_persists_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="ROT_SCOPE", value="v1"),
        )
        result = ServicesMutation().rotate_app_secret(
            _info(),
            input=RotateAppSecretInput(app_slug=app.slug, key="ROT_SCOPE", value="v2", scope="production"),
        )
    assert result.ok, result.errors
    row = AppSecretMetadata.objects.get(registered_app=app, key="ROT_SCOPE")
    assert row.scope == "production"


def test_set_metadata_standalone_persists_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().set_app_secret_metadata(
            _info(),
            input=SetAppSecretMetadataInput(
                app_slug=app.slug,
                key="META_SCOPE",
                scope="preview:feat/new-ui",
            ),
        )
    assert result.ok, result.errors
    row = AppSecretMetadata.objects.get(registered_app=app, key="META_SCOPE")
    assert row.scope == "preview:feat/new-ui"


def test_reset_overwrites_existing_scope(permission_resolver):
    """Re-calling setAppSecret always writes the supplied scope, resetting
    any previously set value (FE pre-fills from metadata to preserve scope
    across edits)."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="RESET_K", value="v1", scope="production"),
        )
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="RESET_K", value="v2"),
        )
    row = AppSecretMetadata.objects.get(registered_app=app, key="RESET_K")
    # Second write had no explicit scope → default "all" was written.
    assert row.scope == "all"


# ---- setAppSecretMetadata return payload carries scope ------------


def test_set_metadata_return_payload_carries_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().set_app_secret_metadata(
            _info(),
            input=SetAppSecretMetadataInput(
                app_slug=app.slug,
                key="PAY_K",
                scope="preview",
            ),
        )
    assert result.ok, result.errors
    assert result.data.scope == "preview"


# ---- resolver projects scope onto AppSecretType -------------------


def test_query_projects_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="SHARED_KEY",
                value="v",
                scope="production",
            ),
        )
        secrets = ServicesQuery().astrolift_app_secrets(
            _info(), app_slug=app.slug, environment_name="production"
        )
    hits = [s for s in secrets if s.source == "literal" and s.key == "SHARED_KEY"]
    assert hits, "SHARED_KEY not returned"
    assert hits[0].scope == "production"


def test_query_default_scope_is_all_when_no_metadata(permission_resolver):
    """Keys without a metadata row should surface scope='all'."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        secrets = ServicesQuery().astrolift_app_secrets(
            _info(), app_slug=app.slug, environment_name="production"
        )
    # No metadata rows — all keys should carry the default scope.
    for s in secrets:
        if s.source == "literal":
            assert s.scope == "all"


# ---- scope filtering at resolution time ---------------------------


def test_production_scoped_secret_hidden_in_preview_env(permission_resolver):
    org, app, cluster, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    _add_preview_env(app, cluster, env_name="pr-1", branch="feat/login")
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="PROD_ONLY", value="secret", scope="production"),
        )
        secrets = ServicesQuery().astrolift_app_secrets(_info(), app_slug=app.slug, environment_name="pr-1")
    keys = [s.key for s in secrets if s.source == "literal"]
    assert "PROD_ONLY" not in keys


def test_production_scoped_secret_visible_in_production_env(permission_resolver):
    org, app, cluster, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="PROD_ONLY", value="secret", scope="production"),
        )
        secrets = ServicesQuery().astrolift_app_secrets(
            _info(), app_slug=app.slug, environment_name="production"
        )
    keys = [s.key for s in secrets if s.source == "literal"]
    assert "PROD_ONLY" in keys


def test_preview_scoped_secret_hidden_in_production_env(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="PREVIEW_ONLY", value="draft", scope="preview"),
        )
        secrets = ServicesQuery().astrolift_app_secrets(
            _info(), app_slug=app.slug, environment_name="production"
        )
    keys = [s.key for s in secrets if s.source == "literal"]
    assert "PREVIEW_ONLY" not in keys


def test_preview_scoped_secret_visible_in_preview_env(permission_resolver):
    org, app, cluster, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    _add_preview_env(app, cluster, env_name="pr-1", branch="feat/login")
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="PREVIEW_ONLY", value="draft", scope="preview"),
        )
        secrets = ServicesQuery().astrolift_app_secrets(_info(), app_slug=app.slug, environment_name="pr-1")
    keys = [s.key for s in secrets if s.source == "literal"]
    assert "PREVIEW_ONLY" in keys


def test_branch_scoped_secret_visible_only_in_matching_preview(permission_resolver):
    org, app, cluster, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    _add_preview_env(app, cluster, env_name="pr-1", branch="feat/login")
    _add_preview_env(app, cluster, env_name="pr-2", branch="feat/signup")
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="SHARED_KEY",
                value="v",
                scope="preview:feat/login",
            ),
        )
        secrets_pr1 = ServicesQuery().astrolift_app_secrets(
            _info(), app_slug=app.slug, environment_name="pr-1"
        )
        secrets_pr2 = ServicesQuery().astrolift_app_secrets(
            _info(), app_slug=app.slug, environment_name="pr-2"
        )
    keys_pr1 = [s.key for s in secrets_pr1 if s.source == "literal"]
    keys_pr2 = [s.key for s in secrets_pr2 if s.source == "literal"]
    assert "SHARED_KEY" in keys_pr1
    assert "SHARED_KEY" not in keys_pr2


def test_all_scoped_secret_visible_in_every_env(permission_resolver):
    org, app, cluster, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    _add_preview_env(app, cluster, env_name="pr-1", branch="feat/login")
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="SHARED_KEY", value="v", scope="all"),
        )
        secrets_prod = ServicesQuery().astrolift_app_secrets(
            _info(), app_slug=app.slug, environment_name="production"
        )
        secrets_pr = ServicesQuery().astrolift_app_secrets(
            _info(), app_slug=app.slug, environment_name="pr-1"
        )
    assert any(s.key == "SHARED_KEY" for s in secrets_prod if s.source == "literal")
    assert any(s.key == "SHARED_KEY" for s in secrets_pr if s.source == "literal")
