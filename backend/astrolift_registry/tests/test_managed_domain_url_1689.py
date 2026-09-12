"""An environment must not advertise a URL that cannot resolve (#1689, #1690).

Environment bootstrap fell back to the *organization slug* when no
ManagedDomain resolved, so the env came up claiming
``https://<app>.<org-slug>`` -- not a DNS zone, and an address that can
never answer. The same ``None`` left ``managed_domain`` unbound, so the
render emitted no Ingress either. The app read as deployed and healthy,
was unreachable, and nothing on the path said why.

The backfill command that binds the FK afterwards had the mirror
problem: it fixed the Ingress and left the advertised URL alone, so an
operator saw the same broken address immediately after the command
reported success.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations.helpers import _bootstrap_app_environments

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def world():
    org = Organization.objects.create(name="Conflict", slug="conflict-1689")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1689")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="aws",
                slug="aws-1689",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="c",
        slug="c-1689",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        is_active=True,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="exo-dash",
        slug="exo-dash-1689",
        subdomain="conflict-exo-dash",
        provisioning_status="ready",
    )
    return org, cluster, app


def _bootstrap(app, cluster, monkeypatch=None):
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster"])
    _bootstrap_app_environments(app, ["production"])
    return AppEnvironment.objects.get(registered_app=app, name="production")


def test_no_managed_domain_means_no_url(world):
    """The bug: this produced ``https://conflict-exo-dash.conflict``."""

    _org, cluster, app = world
    env = _bootstrap(app, cluster)

    assert env.managed_domain is None
    assert env.url == ""


def test_a_resolved_domain_still_builds_the_url(world):
    org, cluster, app = world
    domain = ManagedDomain.objects.create(
        zone="astro.conflict.cloud",
        dns_driver="route53",
        default_for=ManagedDomain.DefaultFor.TENANT_APPS,
    )
    org.default_managed_domain = domain
    org.save(update_fields=["default_managed_domain"])

    env = _bootstrap(app, cluster)

    assert env.managed_domain == domain
    assert env.url == "https://conflict-exo-dash.astro.conflict.cloud"


def test_the_backfill_recomputes_the_url_it_made_correct(world):
    """Binding the FK fixed the Ingress and left the operator looking at
    the same unroutable address."""

    _org, cluster, app = world
    env = _bootstrap(app, cluster)
    # Stand in for a row created before the fix.
    AppEnvironment.objects.filter(pk=env.pk).update(url="https://conflict-exo-dash.conflict")
    domain = ManagedDomain.objects.create(
        zone="astro.conflict.cloud",
        dns_driver="route53",
        default_for=ManagedDomain.DefaultFor.TENANT_APPS,
    )

    call_command("backfill_managed_domain", stdout=StringIO())

    env.refresh_from_db()
    assert env.managed_domain == domain
    assert env.url == "https://conflict-exo-dash.astro.conflict.cloud"


def test_the_backfill_dry_run_writes_nothing(world):
    _org, cluster, app = world
    env = _bootstrap(app, cluster)
    AppEnvironment.objects.filter(pk=env.pk).update(url="https://conflict-exo-dash.conflict")
    ManagedDomain.objects.create(
        zone="astro.conflict.cloud",
        dns_driver="route53",
        default_for=ManagedDomain.DefaultFor.TENANT_APPS,
    )

    call_command("backfill_managed_domain", "--dry-run", stdout=StringIO())

    env.refresh_from_db()
    assert env.managed_domain is None
    assert env.url == "https://conflict-exo-dash.conflict"


def test_a_domain_created_through_the_api_is_usable_by_default():
    """``default_for="none"`` made a UI-registered domain inert: accepted,
    listed, and matched by nothing."""

    from astrolift_clusters.schema.mutations import CreateManagedDomainInput

    assert CreateManagedDomainInput.default_for == ManagedDomain.DefaultFor.TENANT_APPS.value
