"""Tests for the per-section settings-card lastModifiedAt rollup (#454).

Replaces the ``app.updatedAt`` proxy the FE was using as a fallback
(#437 scope E) so each Settings landing card reports its own freshness
instead of a single timestamp for the whole app.

``build_settings_last_modified`` fans out across eight sections:

- deploy strategy   → app.updated_at proxy (no separate resource)
- deploy tokens     → DeployToken
- secrets           → AppSecretBundleRef
- managed services  → ManagedServiceBinding
- domains           → CustomDomain
- webhooks          → WebhookSubscription
- members           → RoleBinding (scope=APP)
- observability     → AlertRule (target=app)

The detail resolver ``astroliftApp(slug)`` exposes the wrapper;
list-shape resolvers leave it None so the cheap list path stays cheap.

Real Postgres; soft-deleted rows are excluded from the aggregate.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.models.deploy_token import DeployToken
from astrolift_lifecycle.models.ingress import CustomDomain
from astrolift_operations.models.alert import AlertRule
from astrolift_operations.models.webhook_subscription import WebhookSubscription
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.schema.types import build_settings_last_modified
from astrolift_services.models.managed_service import ManagedService, ManagedServiceBinding
from astrolift_services.models.secret_bundle import AppSecretBundleRef, SecretBundle
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _user(username: str, **kw):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test", **kw)


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


def _provider_plugin(slug: str):
    plugin = ProviderPlugin(
        name=f"Plugin {slug}",
        slug=slug,
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return ProviderPlugin.objects.get(slug=slug)


def _scaffold(suffix: str):
    org = Organization.objects.create(name="Acme", slug=f"slm-acme{suffix}")
    team = Team.objects.create(organization=org, name="Plat", slug=f"slm-plat{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"slm-demo{suffix}")
    plugin = _provider_plugin(f"slm-plugin{suffix}")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="slm-cluster",
        slug=f"slm-cluster{suffix}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, team, project, cluster


def _app(org, team, project, *, slug: str) -> RegisteredApp:
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=slug,
        slug=slug,
        provisioning_status=RegisteredApp.ProvisioningStatus.READY,
        registry_repo_uri="111.dkr.ecr.us-east-1.amazonaws.com/acme/app",
    )


def _env(app, cluster, *, name: str = "prod") -> AppEnvironment:
    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name=name,
        url="https://x.example.com",
        required_approvals=0,
    )


def _set_updated_at(model_cls, pk, when):
    """Backdate a row's ``updated_at`` so the aggregate has something
    deterministic to assert against. Bypasses the auto_now save() hook
    via raw UPDATE."""
    model_cls.objects.filter(pk=pk).update(updated_at=when)


# ---------- empty app: every section is None except deploy_strategy ----


def test_empty_app_returns_null_for_every_resourced_section():
    """An app with no related rows reports None for each section that
    has its own resource — the FE hides the caption rather than
    showing a misleading default. ``deploy_strategy`` is always
    populated because it mirrors ``app.updated_at`` (no separate row)."""
    org, team, project, _ = _scaffold(suffix="-empty")
    app = _app(org, team, project, slug="empty-app")

    payload = build_settings_last_modified(app)

    assert payload.deploy_tokens is None
    assert payload.secrets is None
    assert payload.managed_services is None
    assert payload.domains is None
    assert payload.webhooks is None
    assert payload.members is None
    assert payload.observability is None
    # Deploy-strategy is the on-row proxy and is always populated.
    assert payload.deploy_strategy == app.updated_at


# ---------- populated paths: one per section --------------------------


def test_deploy_tokens_section_returns_max_updated_at_across_tokens():
    org, team, project, _ = _scaffold(suffix="-dt")
    app = _app(org, team, project, slug="dt-app")

    older = DeployToken.objects.create(
        registered_app=app, name="ci-older", token_hash="h1", token_last_4="aaaa"
    )
    newer = DeployToken.objects.create(
        registered_app=app, name="ci-newer", token_hash="h2", token_last_4="bbbb"
    )
    t_older = timezone.now() - timedelta(days=10)
    t_newer = timezone.now() - timedelta(minutes=5)
    _set_updated_at(DeployToken, older.pk, t_older)
    _set_updated_at(DeployToken, newer.pk, t_newer)

    payload = build_settings_last_modified(app)

    assert payload.deploy_tokens == t_newer


def test_secrets_section_returns_max_updated_at_across_refs():
    org, team, project, cluster = _scaffold(suffix="-sc")
    app = _app(org, team, project, slug="sc-app")
    env = _env(app, cluster)
    bundle_a = SecretBundle.objects.create(
        organization=org, name="api-keys", slug="api-keys", backend_ref="a"
    )
    bundle_b = SecretBundle.objects.create(organization=org, name="db-keys", slug="db-keys", backend_ref="b")
    ref_a = AppSecretBundleRef.objects.create(registered_app=app, app_environment=env, secret_bundle=bundle_a)
    ref_b = AppSecretBundleRef.objects.create(registered_app=app, app_environment=env, secret_bundle=bundle_b)
    t_a = timezone.now() - timedelta(days=2)
    t_b = timezone.now() - timedelta(hours=1)
    _set_updated_at(AppSecretBundleRef, ref_a.pk, t_a)
    _set_updated_at(AppSecretBundleRef, ref_b.pk, t_b)

    payload = build_settings_last_modified(app)

    assert payload.secrets == t_b


def test_managed_services_section_returns_max_updated_at_across_bindings():
    org, team, project, cluster = _scaffold(suffix="-ms")
    app = _app(org, team, project, slug="ms-app")
    env = _env(app, cluster)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary-db",
        variant="rds",
        status=ManagedService.Status.ACTIVE,
    )
    binding_a = ManagedServiceBinding.objects.create(
        managed_service=svc, env_key="DATABASE_URL", env_value_ref="ref-a"
    )
    binding_b = ManagedServiceBinding.objects.create(
        managed_service=svc, env_key="DATABASE_HOST", env_value_ref="ref-b"
    )
    t_a = timezone.now() - timedelta(hours=12)
    t_b = timezone.now() - timedelta(minutes=15)
    _set_updated_at(ManagedServiceBinding, binding_a.pk, t_a)
    _set_updated_at(ManagedServiceBinding, binding_b.pk, t_b)

    payload = build_settings_last_modified(app)

    assert payload.managed_services == t_b


def test_domains_section_returns_max_updated_at_across_custom_domains():
    org, team, project, _ = _scaffold(suffix="-dom")
    app = _app(org, team, project, slug="dom-app")
    d_a = CustomDomain.objects.create(registered_app=app, hostname="a.example.com")
    d_b = CustomDomain.objects.create(registered_app=app, hostname="b.example.com")
    t_a = timezone.now() - timedelta(days=5)
    t_b = timezone.now() - timedelta(minutes=30)
    _set_updated_at(CustomDomain, d_a.pk, t_a)
    _set_updated_at(CustomDomain, d_b.pk, t_b)

    payload = build_settings_last_modified(app)

    assert payload.domains == t_b


def test_webhooks_section_only_counts_app_scoped_subscriptions():
    """Org-wide subscriptions (registered_app=None) don't surface on
    the per-app card — staleness there belongs to the org settings
    landing, not the app settings landing."""
    org, team, project, _ = _scaffold(suffix="-wh")
    app = _app(org, team, project, slug="wh-app")
    sub_app = WebhookSubscription.objects.create(
        organization=org,
        registered_app=app,
        url="https://hooks.example.com/app",
        secret_hash="h",
        events=[],
    )
    sub_org = WebhookSubscription.objects.create(
        organization=org,
        registered_app=None,
        url="https://hooks.example.com/org",
        secret_hash="h",
        events=[],
    )
    t_app = timezone.now() - timedelta(hours=2)
    t_org = timezone.now() - timedelta(minutes=1)  # more recent but org-scoped
    _set_updated_at(WebhookSubscription, sub_app.pk, t_app)
    _set_updated_at(WebhookSubscription, sub_org.pk, t_org)

    payload = build_settings_last_modified(app)

    assert payload.webhooks == t_app


def test_members_section_returns_max_updated_at_across_app_scoped_bindings():
    """Only bindings whose scope is ``APP`` and whose ``scope_id`` is
    the app's pk count — team/project/org grants surface on those
    other surfaces, not the per-app members card."""
    org, team, project, _ = _scaffold(suffix="-mem")
    app = _app(org, team, project, slug="mem-app")
    role = Role.objects.create(
        organization=org, slug="app-viewer", name="App viewer", permissions=["app.read"]
    )
    viewer = _user("mem-viewer")
    deployer = _user("mem-deployer")

    rb_a = RoleBinding.objects.create(
        user=viewer, role=role, scope_kind=RoleBinding.ScopeKind.APP, scope_id=app.pk
    )
    rb_b = RoleBinding.objects.create(
        user=deployer, role=role, scope_kind=RoleBinding.ScopeKind.APP, scope_id=app.pk
    )
    # A team-scoped binding for the same user should NOT raise the
    # per-app card's freshness — its scope is the team, not the app.
    rb_team = RoleBinding.objects.create(
        user=viewer, role=role, scope_kind=RoleBinding.ScopeKind.TEAM, scope_id=team.pk
    )
    t_a = timezone.now() - timedelta(hours=6)
    t_b = timezone.now() - timedelta(minutes=10)
    t_team = timezone.now() - timedelta(seconds=5)  # most recent but team-scoped
    _set_updated_at(RoleBinding, rb_a.pk, t_a)
    _set_updated_at(RoleBinding, rb_b.pk, t_b)
    _set_updated_at(RoleBinding, rb_team.pk, t_team)

    payload = build_settings_last_modified(app)

    assert payload.members == t_b


def test_observability_section_only_counts_app_targeted_rules():
    """Org-wide / global / env / workload-scoped rules don't show up
    on the per-app observability card; only ``target=app`` with the
    app's slug."""
    org, team, project, _ = _scaffold(suffix="-obs")
    app = _app(org, team, project, slug="obs-app")
    rule_app = AlertRule.objects.create(
        organization=org,
        name="cpu-high",
        target=AlertRule.Target.APP,
        target_id=app.slug,
        predicate={},
        severity=AlertRule.Severity.WARN,
    )
    rule_global = AlertRule.objects.create(
        organization=org,
        name="cluster-load",
        target=AlertRule.Target.GLOBAL,
        target_id="",
        predicate={},
        severity=AlertRule.Severity.INFO,
    )
    t_app = timezone.now() - timedelta(days=3)
    t_global = timezone.now() - timedelta(minutes=1)
    _set_updated_at(AlertRule, rule_app.pk, t_app)
    _set_updated_at(AlertRule, rule_global.pk, t_global)

    payload = build_settings_last_modified(app)

    assert payload.observability == t_app


def test_observability_returns_none_when_no_app_targeted_rules():
    """Apps with zero rules surface None so the FE hides the caption."""
    org, team, project, _ = _scaffold(suffix="-obs-empty")
    app = _app(org, team, project, slug="obs-empty-app")

    payload = build_settings_last_modified(app)

    assert payload.observability is None


# ---------- soft-deletion ----------------------------------------------


def test_soft_deleted_rows_are_excluded_from_max_aggregate():
    """A soft-deleted (most-recent) token doesn't poison the
    freshness signal — the operator deleted it; its updated_at no
    longer reflects the section's live state."""
    org, team, project, _ = _scaffold(suffix="-sd")
    app = _app(org, team, project, slug="sd-app")

    alive = DeployToken.objects.create(registered_app=app, name="alive", token_hash="h1", token_last_4="aaaa")
    dead = DeployToken.objects.create(registered_app=app, name="dead", token_hash="h2", token_last_4="bbbb")
    t_alive = timezone.now() - timedelta(hours=1)
    t_dead = timezone.now() - timedelta(seconds=5)  # more recent
    _set_updated_at(DeployToken, alive.pk, t_alive)
    _set_updated_at(DeployToken, dead.pk, t_dead)
    dead.deleted_at = timezone.now()
    dead.save(update_fields=["deleted_at"])

    payload = build_settings_last_modified(app)

    # The live token's timestamp wins; the soft-deleted row is excluded.
    assert payload.deploy_tokens == t_alive


# ---------- resolver wiring --------------------------------------------


def test_astrolift_app_detail_resolver_includes_settings_last_modified():
    """The single-app detail resolver populates the wrapper so the
    Settings card grid renders per-section staleness; list resolvers
    leave it None (covered separately)."""
    org, team, project, _ = _scaffold(suffix="-detail")
    app = _app(org, team, project, slug="detail-app")
    DeployToken.objects.create(registered_app=app, name="ci", token_hash="h", token_last_4="cafe")
    user = _user("detail-viewer", is_superuser=True, is_staff=True)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        row = RegistryQuery().astrolift_app(_info(), slug=app.slug)

    assert row is not None
    assert row.settings_last_modified is not None
    assert row.settings_last_modified.deploy_strategy is not None
    assert row.settings_last_modified.deploy_tokens is not None
    # Sections without rows stay None so the FE hides the caption.
    assert row.settings_last_modified.secrets is None
    assert row.settings_last_modified.observability is None
