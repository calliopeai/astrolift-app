"""Re-registering a cluster must not undo the operator's gate (#1729).

``startup.py`` runs ``register_tenant_cluster`` on every container start, so
this command re-runs on every deploy of the control plane. It resolved
``ingress_class`` to a plugin default and put it in ``defaults``, so a cluster
an operator had moved onto the central auth host silently went back to ``alb``
the next time the control plane restarted -- taking the gate off every app on
it, with no audit event, because a management command emits none.

Two fixes in this same function already establish the rule: ``oidc_auth_config``
(#1616) and ``ingress_mode`` (#1537) are both written only when the run actually
supplied one. ``ingress_class`` is the third field of that shape, and
``alb_auth_config`` (#1773) the fourth.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from astrolift_clusters.models import ProviderPlugin, TenantCluster

pytestmark = pytest.mark.django_db

SLUG = "gate-cluster"


@pytest.fixture
def aws_plugin(db):
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}},
    )
    return plugin


def _register(**env):
    call_command("register_tenant_cluster", slug=SLUG, plugin_slug="aws", **env)


def test_a_new_aws_cluster_still_gets_the_plugin_default(aws_plugin):
    """The default has to survive on create, or first registration produces a
    cluster with the wrong ingress class."""
    _register()
    assert TenantCluster.all_objects.get(slug=SLUG).ingress_class == "alb"


def test_rerunning_does_not_overwrite_an_operator_set_class(aws_plugin):
    """The bug: deploy the control plane and the gate comes off."""
    _register()
    cluster = TenantCluster.all_objects.get(slug=SLUG)
    cluster.ingress_class = "nginx"
    cluster.oidc_auth_config = {
        "discovery_url": "https://issuer.example.com",
        "client_id": "abc",
        "auth_proxy_host": "auth.example.com",
    }
    cluster.save(update_fields=["ingress_class", "oidc_auth_config"])

    _register()  # what every deploy does

    cluster.refresh_from_db()
    assert cluster.ingress_class == "nginx", "re-register reverted the operator's ingress class"
    assert cluster.oidc_auth_config["auth_proxy_host"] == "auth.example.com"


def test_an_explicit_class_still_wins_on_rerun(aws_plugin):
    """Leaving the row alone must not make the field unsettable -- an operator
    passing it explicitly is a deliberate act and still applies."""
    _register()
    cluster = TenantCluster.all_objects.get(slug=SLUG)
    cluster.ingress_class = "nginx"
    cluster.save(update_fields=["ingress_class"])

    _register(ingress_class="traefik")

    cluster.refresh_from_db()
    assert cluster.ingress_class == "traefik"


def test_rerunning_does_not_null_an_operator_set_cognito_config(aws_plugin):
    """#1773: the webservice re-registers on every boot; without the three
    ASTROLIFT_CLUSTER_ALB_AUTH_* vars it wrote alb_auth_config=None and every
    later deploy rendered an Ingress without the Cognito authenticate action."""
    _register()
    cluster = TenantCluster.all_objects.get(slug=SLUG)
    cluster.alb_auth_config = {
        "user_pool_arn": "arn:aws:cognito-idp:us-west-2:1:userpool/x",
        "user_pool_client_id": "client",
        "user_pool_domain": "acme",
    }
    cluster.save(update_fields=["alb_auth_config"])
    _register()  # what every deploy does
    cluster.refresh_from_db()
    assert cluster.alb_auth_config["user_pool_domain"] == "acme", "re-register nulled the Cognito gate"


def test_an_explicit_cognito_config_still_applies_on_rerun(aws_plugin, monkeypatch):
    _register()
    monkeypatch.setenv(
        "ASTROLIFT_CLUSTER_ALB_AUTH_USER_POOL_ARN", "arn:aws:cognito-idp:us-west-2:1:userpool/y"
    )
    monkeypatch.setenv("ASTROLIFT_CLUSTER_ALB_AUTH_CLIENT_ID", "client2")
    monkeypatch.setenv("ASTROLIFT_CLUSTER_ALB_AUTH_DOMAIN", "beta")
    _register()
    assert TenantCluster.all_objects.get(slug=SLUG).alb_auth_config["user_pool_domain"] == "beta"
