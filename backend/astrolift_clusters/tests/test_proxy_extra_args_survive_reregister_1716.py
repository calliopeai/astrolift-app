"""Declaring the OIDC vars must not delete the operator's proxy flags (#1716).

Same trap the gateway secret fell into (#1726): ``register_tenant_cluster``
assigns a fresh ``oidc_auth_config`` on every container start, so the write
REPLACES the stored config and a key the environment does not carry is
deleted rather than left alone.

``proxy_extra_args`` is the operator's provider-specific flag set -- most
pointedly ``insecure-oidc-allow-unverified-email`` for a federation whose
upstream cannot assert the claim. Losing it on a container start puts every
federated login back to a 500 at the callback, with the proxy passing all
its health checks throughout.
"""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command

from astrolift_clusters.models import ProviderPlugin, TenantCluster

pytestmark = pytest.mark.django_db

SLUG = "pea-cluster"
OIDC_ENV = {
    "ASTROLIFT_CLUSTER_OIDC_DISCOVERY_URL": "https://issuer.example.com",
    "ASTROLIFT_CLUSTER_OIDC_CLIENT_ID": "client-abc",
    "ASTROLIFT_CLUSTER_OIDC_COOKIE_SECRET": "c" * 32,
    "ASTROLIFT_CLUSTER_OIDC_AUTH_PROXY_HOST": "auth.example.com",
}
FLAGS = {"insecure-oidc-allow-unverified-email": "true"}


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


def _seed_flags(flags):
    cluster = TenantCluster.all_objects.get(slug=SLUG)
    cfg = dict(cluster.oidc_auth_config)
    cfg["proxy_extra_args"] = flags
    cluster.oidc_auth_config = cfg
    cluster.save(update_fields=["oidc_auth_config"])
    return cluster


def test_existing_proxy_flags_survive_a_rerun(monkeypatch, aws_plugin):
    """The bug this prevents: the next container start silently reverts
    the login fix."""

    _register(monkeypatch)
    cluster = _seed_flags(FLAGS)

    _register(monkeypatch)  # what every container start does

    cluster.refresh_from_db()
    assert cluster.oidc_auth_config.get("proxy_extra_args") == FLAGS
    assert cluster.oidc_auth_config["client_id"] == "client-abc"


def test_the_environment_wins_over_the_stored_flags(monkeypatch, aws_plugin):
    """An install moving to the declarative path should be able to say
    what the flags are, not just inherit them."""

    _register(monkeypatch)
    _seed_flags(FLAGS)

    declared = {"email-domain": "steadymd.com"}
    _register(
        monkeypatch,
        ASTROLIFT_CLUSTER_OIDC_PROXY_EXTRA_ARGS=json.dumps(declared),
    )

    cluster = TenantCluster.all_objects.get(slug=SLUG)
    assert cluster.oidc_auth_config["proxy_extra_args"] == declared


def test_malformed_json_falls_back_rather_than_refusing_to_start(monkeypatch, aws_plugin):
    """This command runs on every container start; refusing over a stray
    comma would take the control plane down."""

    _register(monkeypatch)
    _seed_flags(FLAGS)

    _register(monkeypatch, ASTROLIFT_CLUSTER_OIDC_PROXY_EXTRA_ARGS="{not json")

    cluster = TenantCluster.all_objects.get(slug=SLUG)
    assert cluster.oidc_auth_config["proxy_extra_args"] == FLAGS


def test_a_json_scalar_is_ignored(monkeypatch, aws_plugin):
    _register(monkeypatch)
    _seed_flags(FLAGS)

    _register(monkeypatch, ASTROLIFT_CLUSTER_OIDC_PROXY_EXTRA_ARGS='"a string"')

    cluster = TenantCluster.all_objects.get(slug=SLUG)
    assert cluster.oidc_auth_config["proxy_extra_args"] == FLAGS


def test_no_flags_anywhere_leaves_the_key_absent(monkeypatch, aws_plugin):
    """An empty dict on every cluster would be noise in a config operators read."""

    _register(monkeypatch)

    cluster = TenantCluster.all_objects.get(slug=SLUG)
    assert "proxy_extra_args" not in cluster.oidc_auth_config
