"""``manage.py audit_agent_secret_namespace`` lists what #1921 stops resolving.

It is run against production before the release that enforces the org secret
namespace, so it must find every kind of stored location the release refuses
(typed agent refs, binding overrides, org secret bundles, managed-service config
and copied binding rows), stay quiet about what keeps resolving, and never
touch a secret store.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.models.agent_secret_binding import AgentSecretBindingOverride
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceBinding, SecretBundle

pytestmark = pytest.mark.django_db

_VICTIM = "managed/rds-orders/url"


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise AssertionError("the audit must not resolve a secrets backend")

    monkeypatch.setattr("core.app_deploy.driver_for_capability", _refuse)


def _audit(*args) -> list[list[str]]:
    out = StringIO()
    call_command("audit_agent_secret_namespace", *args, stdout=out)
    lines = out.getvalue().splitlines()
    assert lines[-1].endswith("location(s) outside the org secret namespace")
    return [line.split("\t") for line in lines[:-1]]


def _org(slug):
    return Organization.objects.create(name=slug.title(), slug=slug)


def _spec(org, refs):
    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Brief",
        slug="brief",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        secret_refs=refs,
    )


def _app_service(org, *, config):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{org.slug}")
    project = Project.objects.create(organization=org, team=team, name="P", slug=f"p-{org.slug}")
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="K8s audit", slug=f"k8s-{org.slug}", plugin_version="1.0.0")]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=ProviderPlugin.objects.get(slug=f"k8s-{org.slug}"),
        name="Prod",
        slug=f"prod-{org.slug}",
        endpoint="https://cluster.example.com",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug=f"app-{org.slug}",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="production")
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="event_stream",
        name="events",
        config=config,
        applied_config=config,
        backend_ref="event_stream/events",
        status="active",
    )


def test_lists_every_kind_of_location_the_release_refuses():
    org = _org("acme-audit")
    spec = _spec(
        org,
        [
            {"env_var": "GITHUB_TOKEN", "uri": "agents/acme/github-token"},
            {"env_var": "OWN_TOKEN", "uri": f"agents/{org.guid}/own"},
        ],
    )
    AgentSecretBindingOverride.objects.create(environment_spec=spec, env_var="DATABASE_URL", uri=_VICTIM)
    SecretBundle.objects.create(organization=org, name="Shared", slug="shared", backend_ref="shared-defaults")
    SecretBundle.objects.create(
        organization=org, name="Own", slug="own", backend_ref=f"agent-bundles/{org.guid}/own"
    )
    service = _app_service(org, config={"password_secret_ref": _VICTIM, "auth_mode": "scram"})
    clean = _app_service(_org("globex-audit"), config={"broker_password_location": _VICTIM})
    ManagedServiceBinding.objects.create(
        managed_service=clean, env_key="EVENT_STREAM_PASSWORD", env_value_ref=_VICTIM, is_secret=True
    )

    rows = _audit()

    found = {(row[0], row[2], row[3], row[4]) for row in rows}
    assert found == {
        ("acme-audit", "agent env spec", "brief", "GITHUB_TOKEN"),
        ("acme-audit", "agent env spec", "brief", "DATABASE_URL"),
        ("acme-audit", "secret bundle", "shared", "backendRef"),
        (
            "acme-audit",
            "managed service",
            f"event_stream/events ({service.guid})",
            "config.password_secret_ref",
        ),
        (
            "globex-audit",
            "managed service",
            f"event_stream/events ({clean.guid})",
            "binding EVENT_STREAM_PASSWORD",
        ),
    }
    assert all(row[1] for row in rows)
    by_field = {row[4]: row[5] for row in rows}
    assert f"agents/{org.guid}/" in by_field["GITHUB_TOKEN"]
    assert f"agent-bundles/{org.guid}/" in by_field["backendRef"]
    assert f"services/{org.guid}/" in by_field["config.password_secret_ref"]


def test_filters_to_one_organization_by_slug_or_guid():
    acme = _org("acme-filter")
    globex = _org("globex-filter")
    _spec(acme, [{"env_var": "A", "uri": "a-token"}])
    SecretBundle.objects.create(organization=globex, name="G", slug="g", backend_ref="g-bundle")

    assert {row[0] for row in _audit("--org", "acme-filter")} == {"acme-filter"}
    assert {row[0] for row in _audit("--org", str(globex.guid))} == {"globex-filter"}


def test_prints_nothing_but_the_count_when_everything_is_inside_the_namespace():
    org = _org("clean-audit")
    _spec(org, [{"env_var": "OWN_TOKEN", "uri": f"agents/{org.guid}/own"}])

    assert _audit() == []


def test_an_unknown_organization_is_an_error():
    with pytest.raises(CommandError, match="organization not found"):
        _audit("--org", "no-such-org")
