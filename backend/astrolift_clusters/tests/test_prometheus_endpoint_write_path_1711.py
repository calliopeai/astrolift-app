"""The Prometheus endpoint has a supported write path and a named failure (#1711).

Every metrics panel reads
``TenantCluster.provider_config["prometheus_endpoint"]``. Until now the
only two things that wrote it were the registration UI and the capability
probe, which discovers the Prometheus *pod IP* -- correct until the pod is
rescheduled. An install that put a stable internal load balancer in front
of Prometheus had to write the column by hand.

The second half is the constraint that costs the time: the control plane
queries Prometheus over HTTP, so an in-cluster Service address is not
routable and fails with a connection error that reads as "Prometheus is
down". It is not an error to store one -- a control plane inside the same
cluster reaches it -- so it is warned about on write and named on failure.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import _unreachable_reason
from astrolift_observability.prom_client import is_cluster_internal_endpoint

pytestmark = pytest.mark.django_db

SLUG = "prom-cluster"
LB = "http://internal-prom-1234.us-west-2.elb.amazonaws.com:9090"


@pytest.fixture
def aws_plugin(db):
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}},
    )
    return plugin


def _register(**kwargs):
    call_command("register_tenant_cluster", slug=SLUG, plugin_slug="aws", **kwargs)


def _cluster():
    return TenantCluster.all_objects.get(slug=SLUG)


# ----------------------------------------------------------------------
# write path
# ----------------------------------------------------------------------


def test_the_endpoint_can_be_declared_on_the_command(aws_plugin):
    _register(prometheus_endpoint=LB)

    assert _cluster().provider_config["prometheus_endpoint"] == LB


def test_the_endpoint_can_come_from_the_environment(monkeypatch, aws_plugin):
    monkeypatch.setenv("ASTROLIFT_CLUSTER_PROMETHEUS_ENDPOINT", LB)

    _register()

    assert _cluster().provider_config["prometheus_endpoint"] == LB


def test_a_re_register_without_the_flag_keeps_the_endpoint(aws_plugin):
    """This command runs on every container start. An endpoint that
    survives only until the next deploy is not a write path."""
    _register(prometheus_endpoint=LB)

    _register()

    assert _cluster().provider_config["prometheus_endpoint"] == LB


def test_the_flag_overwrites_a_stale_probe_discovered_endpoint(aws_plugin):
    """The probe writes a pod IP; moving to a load balancer has to win."""
    _register(prometheus_endpoint="http://10.0.4.17:9090")

    _register(prometheus_endpoint=LB)

    assert _cluster().provider_config["prometheus_endpoint"] == LB


def test_a_cluster_internal_endpoint_is_stored_with_a_warning(aws_plugin, capsys):
    """Stored, not refused: a control plane running inside the cluster
    reaches this. Warned about, because usually it does not."""
    internal = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"

    _register(prometheus_endpoint=internal)

    assert _cluster().provider_config["prometheus_endpoint"] == internal
    assert "cluster-internal" in capsys.readouterr().out


# ----------------------------------------------------------------------
# the reachability contract
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://prometheus.monitoring.svc.cluster.local:9090",
        "http://prometheus-operated.monitoring.svc:9090",
        "prometheus.monitoring.svc.cluster.local:9090",
        "http://localhost:9090",
        "http://127.0.0.1:9090",
        "http://PROMETHEUS.MONITORING.SVC.CLUSTER.LOCAL.:9090",
    ],
)
def test_in_cluster_addresses_are_recognised(endpoint):
    assert is_cluster_internal_endpoint(endpoint) is True


@pytest.mark.parametrize(
    "endpoint",
    [
        LB,
        # A pod IP on EKS is a real VPC ENI address and routes from the
        # control plane -- it is what the capability probe discovers, and
        # flagging it would call the working default broken.
        "http://10.0.4.17:9090",
        "https://prometheus.example.com",
        "",
    ],
)
def test_routable_addresses_are_not_flagged(endpoint):
    assert is_cluster_internal_endpoint(endpoint) is False


def test_a_failure_against_an_in_cluster_address_says_which_failure_it_is():
    assert (
        _unreachable_reason("http://prometheus.monitoring.svc.cluster.local:9090")
        == "cluster_internal_endpoint"
    )


def test_a_failure_against_a_routable_address_stays_unreachable():
    assert _unreachable_reason(LB) == "unreachable"
