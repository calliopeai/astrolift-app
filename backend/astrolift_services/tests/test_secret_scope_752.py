"""Tests for AppSecretMetadata scope field (#752).

Covers:
- scope defaults to 'all' on set/rotate writes that don't supply it
- setAppSecret with explicit scope persists it on the metadata row
- rotateAppSecret with explicit scope persists it
- setAppSecretMetadata standalone persists scope
- a write that names a scope overwrites it; set / rotate / bulk import /
  metadata writes that omit it keep the stored scope (#1758)
- resolver projects scope onto AppSecretType
- allowed_scopes_for_env: production env gets {all, production}
- allowed_scopes_for_env: preview env gets {all, preview, preview:<branch>}
- scope filtering: production-scoped secret hidden in preview env
- scope filtering: preview-scoped secret hidden in production env
- scope filtering: preview:<branch> secret visible only in matching branch env
- scope filtering: all-scoped secret visible everywhere
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from constance.test import override_config

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
from astrolift_services.schema.mutations.types import BulkImportAppSecretsInput, ProposeSecretChangeInput
from astrolift_services.schema.queries import ServicesQuery
from astrolift_services.secret_literals import allowed_scopes_for_env
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
                plugin_version="0.0.1",
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


# ---- allowed_scopes_for_env unit tests ---------------------------


def test_allowed_scopes_production_env():
    scopes = allowed_scopes_for_env("production", {})
    assert "all" in scopes
    assert "production" in scopes
    assert "preview" not in scopes


def test_allowed_scopes_preview_env():
    scopes = allowed_scopes_for_env("preview-feat", {"preview-feat": "feat/login"})
    assert "all" in scopes
    assert "preview" in scopes
    assert "preview:feat/login" in scopes
    assert "production" not in scopes


def test_allowed_scopes_unknown_env_treated_as_production():
    # An env_name not in preview_env_branches → treated as non-preview.
    scopes = allowed_scopes_for_env("staging", {})
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


def test_reset_with_an_explicit_scope_overwrites_it(permission_resolver):
    """An operator who names a scope on a re-set gets it written."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="RESET_K", value="v1", scope="production"),
        )
        ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="RESET_K", value="v2", scope="all"),
        )
    row = AppSecretMetadata.objects.get(registered_app=app, key="RESET_K")
    assert row.scope == "all"


def _restrict_to_production(org, app, key: str) -> None:
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key=key, value="v1", scope="production"),
        )
    assert result.ok, result.errors


def _scope_of(app, key: str) -> str:
    return AppSecretMetadata.objects.get(registered_app=app, key=key, deleted_at__isnull=True).scope


# A write that omits the scope used to write the "all" default over the
# stored one, so a routine rotate, re-set, bulk import or metadata edit
# widened a production-only key to every environment, previews included
# (#1758 review, H3).


def test_reset_without_a_scope_keeps_the_stored_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    _restrict_to_production(org, app, "RESET_K")
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="RESET_K", value="v2"),
        )
    assert result.ok, result.errors
    assert _scope_of(app, "RESET_K") == "production"


def test_rotate_without_a_scope_keeps_the_stored_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    _restrict_to_production(org, app, "ROT_K")
    with _ctx(org):
        result = ServicesMutation().rotate_app_secret(
            _info(),
            input=RotateAppSecretInput(app_slug=app.slug, key="ROT_K", value="v2"),
        )
    assert result.ok, result.errors
    assert _scope_of(app, "ROT_K") == "production"


def test_bulk_import_keeps_the_stored_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    _restrict_to_production(org, app, "BULK_K")
    with _ctx(org):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(),
            input=BulkImportAppSecretsInput(app_slug=app.slug, dotenv_text="BULK_K=v2\nNEW_K=v3\n"),
        )
    assert result.ok, result.errors
    assert _scope_of(app, "BULK_K") == "production"
    assert _scope_of(app, "NEW_K") == "all"


def test_set_metadata_without_a_scope_keeps_the_stored_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    _restrict_to_production(org, app, "META_K")
    with _ctx(org):
        result = ServicesMutation().set_app_secret_metadata(
            _info(),
            input=SetAppSecretMetadataInput(app_slug=app.slug, key="META_K", set_via="cli"),
        )
    assert result.ok, result.errors
    assert result.data.scope == "production"
    assert _scope_of(app, "META_K") == "production"


def test_a_new_per_environment_row_without_a_scope_takes_the_scope_in_force(permission_resolver):
    """The deploy and the secrets list prefer a key's per-environment row
    to its app-wide one. Created as "all", a row that only recorded how a
    key was set for one preview widened a production-only key into it."""
    org, app, cluster, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    _add_preview_env(app, cluster, env_name="pr-env-row", branch="feat/env-row")
    _restrict_to_production(org, app, "ENV_ROW_K")
    with _ctx(org):
        result = ServicesMutation().set_app_secret_metadata(
            _info(),
            input=SetAppSecretMetadataInput(
                app_slug=app.slug, key="ENV_ROW_K", environment_name="pr-env-row", set_via="cli"
            ),
        )
        secrets = ServicesQuery().astrolift_app_secrets(
            _info(), app_slug=app.slug, environment_name="pr-env-row"
        )
    assert result.ok, result.errors
    assert result.data.scope == "production"
    assert "ENV_ROW_K" not in [s.key for s in secrets if s.source == "literal"]


# An explicit "" matches no environment's allowed scopes, so storing it
# would stop the key deploying anywhere without an error.


def _refused_as_empty_scope(result) -> bool:
    return result.ok is False and result.errors[0].code == "VALIDATION" and result.errors[0].field == "scope"


def test_set_refuses_an_empty_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    _restrict_to_production(org, app, "EMPTY_K")
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="EMPTY_K", value="v2", scope=""),
        )
    assert _refused_as_empty_scope(result)
    assert _scope_of(app, "EMPTY_K") == "production"


def test_rotate_refuses_an_empty_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    _restrict_to_production(org, app, "EMPTY_K")
    with _ctx(org):
        result = ServicesMutation().rotate_app_secret(
            _info(),
            input=RotateAppSecretInput(app_slug=app.slug, key="EMPTY_K", value="v2", scope="  "),
        )
    assert _refused_as_empty_scope(result)
    assert _scope_of(app, "EMPTY_K") == "production"


def test_set_metadata_refuses_an_empty_scope(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    _restrict_to_production(org, app, "EMPTY_K")
    with _ctx(org):
        result = ServicesMutation().set_app_secret_metadata(
            _info(),
            input=SetAppSecretMetadataInput(app_slug=app.slug, key="EMPTY_K", scope=""),
        )
    assert _refused_as_empty_scope(result)
    assert _scope_of(app, "EMPTY_K") == "production"


# Scope decides which environments receive a value, so changing it is a
# secret write: it needs the same fresh elevation as set/rotate (#1946).


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_set_metadata_requires_a_fresh_elevation(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    _restrict_to_production(org, app, "ELEV_K")
    unelevated = SimpleNamespace(
        context=SimpleNamespace(user=None, request=SimpleNamespace(user=None, session={}, META={}))
    )
    with _ctx(org):
        result = ServicesMutation().set_app_secret_metadata(
            unelevated,
            input=SetAppSecretMetadataInput(app_slug=app.slug, key="ELEV_K", scope="all"),
        )
    assert result.ok is False
    assert result.errors[0].code == "STEP_UP_REQUIRED"
    assert _scope_of(app, "ELEV_K") == "production"


def test_propose_secret_change_does_not_take_set_metadata(permission_resolver):
    """set_metadata proposals come only from setAppSecretMetadata, which
    checks that the scope really changes."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().propose_secret_change(
            _info(),
            input=ProposeSecretChangeInput(app_slug=app.slug, op="set_metadata", key="ANY_K"),
        )
    assert result.ok is False
    assert (result.errors[0].code, result.errors[0].field) == ("VALIDATION", "op")


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
