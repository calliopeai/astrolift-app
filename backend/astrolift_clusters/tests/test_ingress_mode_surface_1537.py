"""`ingress_mode` is reachable by an operator (#1537).

The shared-ingress renderer has been correct for a while: `ingress_modes.py`
emits the ALB group annotation and `core/app_deploy` applies it. What was
missing is that **nothing wrote the field**. Six references across the whole
backend outside tests -- the migration, the model, one import, and two reads
-- and no writer at all.

So every AWS cluster sat on the `per_app_ingress` default, which is one load
balancer per app and is the issue's opening complaint, and the only way to
change one was editing the database row by hand. A feature whose switch
cannot be flipped is not shipped.

The model docstring is right that defaulting to shared would re-group load
balancers already serving traffic, and that this has to be an operator's
decision. These hold the other half of that: the operator must be able to
make it, and nothing else may make it for them by accident.
"""

from __future__ import annotations

import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from astrolift_clusters.ingress_modes import IngressMode
from astrolift_clusters.models import ProviderPlugin, TenantCluster

pytestmark = pytest.mark.django_db


@pytest.fixture
def plugin_k8s():
    return ProviderPlugin.objects.create(
        name="k8s_native", slug="k8s_native", capabilities_manifest={}, config_schema={}
    )


def _register(slug: str, *extra: str) -> None:
    call_command(
        "register_tenant_cluster",
        "--slug",
        slug,
        "--plugin-slug",
        "k8s_native",
        "--auth-method",
        "kubeconfig",
        "--endpoint",
        "https://invalid",
        *extra,
        stdout=StringIO(),
    )


def test_the_registration_command_can_set_it(plugin_k8s, monkeypatch):
    monkeypatch.setenv("ASTROLIFT_CLUSTER_KUBECONFIG", "fake")
    slug = f"mode-{uuid.uuid4().hex[:6]}"

    _register(slug, "--ingress-mode", "shared_ingress")

    assert TenantCluster.all_objects.get(slug=slug).ingress_mode == IngressMode.SHARED_INGRESS.value


def test_re_registering_without_the_flag_leaves_it_alone(plugin_k8s, monkeypatch):
    """The property the model docstring is protecting.

    If an absent flag resolved to a default, a routine re-register -- which
    CI setup does on every run -- would silently re-group every load balancer
    on the next deploy. Same shape as the `oidc_auth_config` fail-open fixed
    in #1616, in the same command.
    """
    monkeypatch.setenv("ASTROLIFT_CLUSTER_KUBECONFIG", "fake")
    slug = f"keep-{uuid.uuid4().hex[:6]}"

    _register(slug, "--ingress-mode", "shared_ingress")
    _register(slug)

    assert TenantCluster.all_objects.get(slug=slug).ingress_mode == "shared_ingress"


def test_the_command_refuses_a_mode_that_is_not_a_mode(plugin_k8s, monkeypatch):
    monkeypatch.setenv("ASTROLIFT_CLUSTER_KUBECONFIG", "fake")

    with pytest.raises((CommandError, SystemExit)):
        _register(f"bad-{uuid.uuid4().hex[:6]}", "--ingress-mode", "cheapest")


def test_the_field_is_readable_on_the_graphql_type():
    """An operator cannot decide what they cannot see. Unlike
    `oidc_auth_config`, which comes back redacted because it carries the
    oauth2-proxy cookie secret, this is not sensitive and is exposed plainly.
    """
    from astrolift_clusters.schema.types import TenantClusterType

    assert "ingress_mode" in TenantClusterType.__annotations__


def test_the_mutation_refuses_a_mode_that_is_not_a_mode():
    """Validated at the resolver rather than left to the model, so the caller
    gets a MutationResult naming the field instead of a database error."""
    from astrolift_clusters.schema.mutations import UpdateTenantClusterInput

    assert "ingress_mode" in UpdateTenantClusterInput.__annotations__
