"""Declaring the OIDC vars must not delete the gateway secret (#1726).

``register_tenant_cluster`` builds ``oidc_auth_config`` as a fresh dict and
assigns it, so the write REPLACES the stored config rather than merging into it.
A key the environment does not carry is therefore deleted, not left alone.

That matters because the gateway secret is what lets a gated app tell traffic
that came through the auth host from anything else able to reach its Service
port. Losing it on a container start does not degrade the gate -- it makes every
gated app start refusing requests it can no longer prove came through it.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from astrolift_clusters.models import ProviderPlugin, TenantCluster

pytestmark = pytest.mark.django_db

SLUG = "gw-cluster"
OIDC_ENV = {
    "ASTROLIFT_CLUSTER_OIDC_DISCOVERY_URL": "https://issuer.example.com",
    "ASTROLIFT_CLUSTER_OIDC_CLIENT_ID": "client-abc",
    "ASTROLIFT_CLUSTER_OIDC_COOKIE_SECRET": "c" * 32,
    "ASTROLIFT_CLUSTER_OIDC_AUTH_PROXY_HOST": "auth.example.com",
}


@pytest.fixture
def aws_plugin(db):
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}},
    )
    return plugin


def _register(monkeypatch, **extra):
    for k, v in {**OIDC_ENV, **extra}.items():
        monkeypatch.setenv(k, v)
    call_command("register_tenant_cluster", slug=SLUG, plugin_slug="aws")


def test_an_existing_gateway_secret_survives_a_rerun(monkeypatch, aws_plugin):
    """Adopting the declarative path must not force re-issuing a secret that is
    already deployed and working."""
    _register(monkeypatch)
    cluster = TenantCluster.all_objects.get(slug=SLUG)
    cfg = dict(cluster.oidc_auth_config)
    cfg["gateway_secret"] = "s" * 64
    cluster.oidc_auth_config = cfg
    cluster.save(update_fields=["oidc_auth_config"])

    _register(monkeypatch)  # what every container start does

    cluster.refresh_from_db()
    assert cluster.oidc_auth_config.get("gateway_secret") == "s" * 64
    assert cluster.oidc_auth_config["client_id"] == "client-abc"


def test_the_environment_can_declare_it(monkeypatch, aws_plugin):
    _register(monkeypatch, ASTROLIFT_CLUSTER_OIDC_GATEWAY_SECRET="d" * 64)
    cluster = TenantCluster.all_objects.get(slug=SLUG)
    assert cluster.oidc_auth_config["gateway_secret"] == "d" * 64


def test_the_environment_wins_over_the_stored_value(monkeypatch, aws_plugin):
    """Rotation has to be possible: an explicitly declared secret replaces the
    one on the row rather than being ignored in favour of it."""
    _register(monkeypatch, ASTROLIFT_CLUSTER_OIDC_GATEWAY_SECRET="old" + "o" * 61)
    _register(monkeypatch, ASTROLIFT_CLUSTER_OIDC_GATEWAY_SECRET="new" + "n" * 61)
    cluster = TenantCluster.all_objects.get(slug=SLUG)
    assert cluster.oidc_auth_config["gateway_secret"].startswith("new")


def test_no_secret_anywhere_leaves_the_key_absent(monkeypatch, aws_plugin):
    """Absent is correct -- an empty string would read as a configured gate that
    stamps a blank header."""
    _register(monkeypatch)
    cluster = TenantCluster.all_objects.get(slug=SLUG)
    assert "gateway_secret" not in cluster.oidc_auth_config
