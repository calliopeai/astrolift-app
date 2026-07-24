"""Tests for default alert-rule seeding on app registration (real Postgres).

Covers:
* ``register_app`` seeds one AlertRule per ``DEFAULT_RULES`` template.
* Seeded rules carry the app-namespaced name, PromQL predicate, and the
  template severity mapped to the model's vocabulary (warning -> warn).
* Seeding is idempotent (a re-run creates nothing).
* Two apps in one org don't collide on the org-unique rule-name constraint.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.alert_rules import DEFAULT_RULES
from astrolift_operations.alert_seed import seed_default_alert_rules
from astrolift_operations.models import AlertRule
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_onboard_workflow(monkeypatch):
    """register_app fires OnboardAppWorkflow via `_bootstrap_app_environments`,
    which needs a live Temporal server. Seeding runs separately, so stub the
    Temporal-coupled bootstrap to a no-op and let the seeder run for real."""
    monkeypatch.setattr(
        "astrolift_registry.schema.mutations._bootstrap_app_environments",
        lambda *a, **k: None,
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="Local", slug="local", capabilities_manifest={}, config_schema={})]
    )
    TenantCluster.objects.create(
        organization=org,
        name="local",
        slug="local",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, project


def _make_app(org, *, slug: str) -> RegisteredApp:
    team = org.teams.first()
    project = org.projects.first()
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=slug.title(),
        slug=slug,
        k8s_namespace=f"{org.slug}-{slug}",
        provisioning_status="ready",
    )


def test_register_app_seeds_default_alert_rules(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(project_id=str(project.guid), slug="my-app", source_repo="acme/my-app"),
        )

    assert result.ok, result.errors
    rules = AlertRule.objects.filter(organization=org)
    # One rule per default template.
    assert rules.count() == len(DEFAULT_RULES)
    # Every rule is app-namespaced (unique per org) and targets the app.
    for rule in rules:
        assert rule.name.startswith("my-app: ")
        assert rule.target == AlertRule.Target.APP.value
        assert rule.target_id == "my-app"
        assert rule.is_active is True
        # PromQL predicate (no ``kind``) -> evaluated via the Prometheus path,
        # not the synchronous dispatcher.
        assert "kind" not in rule.predicate
        assert rule.predicate["expression"]
        assert "for_seconds" in rule.predicate


def test_seeded_predicate_interpolates_app_and_namespace(permission_resolver):
    org, _project = _scaffold()
    app = _make_app(org, slug="webthing")

    seed_default_alert_rules(app)

    # The 5xx-rate template carries ``app="$app"``; the render must have
    # substituted the app slug (proves render_for_app ran with app.slug).
    rule = AlertRule.objects.get(name="webthing: High error rate (>5% 5xx for 5 min)")
    assert 'app="webthing"' in rule.predicate["expression"]
    # A namespace-scoped template ($ns) must carry the app's namespace.
    restart = AlertRule.objects.get(name="webthing: Pod restart loop")
    assert "acme-webthing" in restart.predicate["expression"]


def test_seed_maps_warning_severity_to_warn(permission_resolver):
    org, _project = _scaffold()
    app = _make_app(org, slug="sev-app")

    seed_default_alert_rules(app)

    severities = set(AlertRule.objects.filter(organization=org).values_list("severity", flat=True))
    # Template vocab is info/warning/critical; the model vocab is
    # info/warn/critical. The seeder must translate warning -> warn.
    assert "warn" in severities
    assert "warning" not in severities
    assert severities <= {
        AlertRule.Severity.INFO.value,
        AlertRule.Severity.WARN.value,
        AlertRule.Severity.CRITICAL.value,
    }


def test_seed_is_idempotent(permission_resolver):
    org, _project = _scaffold()
    app = _make_app(org, slug="idem")

    first = seed_default_alert_rules(app)
    second = seed_default_alert_rules(app)

    assert first == len(DEFAULT_RULES)
    assert second == 0  # nothing new the second time
    assert AlertRule.objects.filter(organization=org).count() == len(DEFAULT_RULES)


def test_two_apps_same_org_do_not_collide(permission_resolver):
    org, _project = _scaffold()
    app_a = _make_app(org, slug="app-a")
    app_b = _make_app(org, slug="app-b")

    seed_default_alert_rules(app_a)
    seed_default_alert_rules(app_b)

    # App-namespaced names keep both apps' full sets alive under the
    # org-unique constraint.
    assert AlertRule.objects.filter(organization=org).count() == 2 * len(DEFAULT_RULES)
    assert AlertRule.objects.filter(target_id="app-a").count() == len(DEFAULT_RULES)
    assert AlertRule.objects.filter(target_id="app-b").count() == len(DEFAULT_RULES)
