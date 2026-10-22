"""The Zentinelle cluster gateway autoscales (#2482).

Astrolift renders the gateway, so its autoscaler is Astrolift's too: an
``autoscaling/v2`` HorizontalPodAutoscaler beside the Deployment and Service,
two replicas at least and ``ZENTINELLE_GATEWAY_MAX_REPLICAS`` at most, on CPU
at 60% of requests, in only after five minutes. The Deployment carries no
``replicas``, so applying the gateway again never resets the autoscaler's count.

Same fakes as #1887: Zentinelle's HTTP API and the cluster driver are
recorded, never reached. What a real apiserver does with these manifests is
in ``test_zentinelle_gateway_hpa_integration_2482.py``.
"""

from __future__ import annotations

import pytest
from constance import config
from constance.test import override_config

from astrolift_operations import zentinelle_connect
from astrolift_operations.models import ZentinelleClusterGateway
from astrolift_operations.tests import test_zentinelle_connect_1887 as base
from astrolift_operations.tests.test_zentinelle_connect_1887 import (
    BASE,
    IMAGE,
    _code,
    _connect,
    _register,
    _set_enabled,
)
from core import platform_settings

pytestmark = pytest.mark.django_db

_no_opensearch_profile_index = base._no_opensearch_profile_index
admin = base.admin
cluster = base.cluster
driver = base.driver
org = base.org
plugin = base.plugin
zentinelle = base.zentinelle

HPA = "HorizontalPodAutoscaler"
MARKER = {"astrolift.dev/replica-owner": "hpa"}


def test_the_gateway_scales_on_cpu_from_two_to_the_ceiling():
    deployment, _service, autoscaler = zentinelle_connect.gateway_manifests(
        cluster_id="c-1", zentinelle_url=BASE, image=IMAGE, max_replicas=9
    )

    assert "replicas" not in deployment["spec"]
    assert deployment["metadata"]["annotations"] == MARKER
    assert (autoscaler["apiVersion"], autoscaler["kind"]) == ("autoscaling/v2", HPA)
    assert autoscaler["metadata"] == {
        "name": "zentinelle-gateway",
        "namespace": "astrolift-system",
        "labels": deployment["metadata"]["labels"],
    }
    assert autoscaler["spec"] == {
        "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "zentinelle-gateway"},
        "minReplicas": 2,
        "maxReplicas": 9,
        "metrics": [
            {
                "type": "Resource",
                "resource": {"name": "cpu", "target": {"type": "Utilization", "averageUtilization": 60}},
            }
        ],
        "behavior": {"scaleDown": {"stabilizationWindowSeconds": 300}},
    }
    # Utilization is a share of the request: a container without one leaves
    # the autoscaler nothing to measure.
    [container] = deployment["spec"]["template"]["spec"]["containers"]
    assert container["resources"]["requests"]["cpu"] == "100m"


@override_config(ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE)
def test_every_deploy_applies_the_autoscaler_with_the_installs_ceiling_and_no_replicas(
    org, cluster, admin, zentinelle, driver
):
    assert _connect(org).ok

    assert _register(org, cluster).ok

    assert driver.kinds()[-3:] == ["Deployment", "Service", HPA]
    assert driver.last(HPA)["spec"]["maxReplicas"] == 6

    # Turning a running gateway on again applies it again, with the ceiling
    # the install holds now.
    with override_config(ZENTINELLE_GATEWAY_MAX_REPLICAS=12):
        assert _set_enabled(org, cluster, True).ok

    assert driver.last(HPA)["spec"]["maxReplicas"] == 12
    deployments = [manifest for _ns, manifest in driver.applied if manifest["kind"] == "Deployment"]
    assert len(deployments) == 2
    for deployment in deployments:
        assert "replicas" not in deployment["spec"]
        assert deployment["metadata"]["annotations"] == MARKER


@override_config(
    ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE, ZENTINELLE_GATEWAY_MAX_REPLICAS=1
)
def test_a_ceiling_below_two_is_refused_before_the_gateway_is_applied(
    org, cluster, admin, zentinelle, driver
):
    assert _connect(org).ok

    result = _register(org, cluster)

    assert result.ok is False and _code(result) == "PRECONDITION"
    assert "ZENTINELLE_GATEWAY_MAX_REPLICAS" in result.errors[0].message
    assert driver.kinds() == ["Namespace", "Secret"]
    gateway = ZentinelleClusterGateway.objects.get(cluster=cluster)
    assert gateway.gateway_deployed is False
    assert "ZENTINELLE_GATEWAY_MAX_REPLICAS" in gateway.last_error


@override_config(ZENTINELLE_GATEWAY_MAX_REPLICAS=6)
def test_the_ceiling_is_a_platform_setting_that_refuses_less_than_two():
    row = platform_settings.get_setting("ZENTINELLE_GATEWAY_MAX_REPLICAS")
    assert (row.group, row.kind, row.editable, row.default) == ("Zentinelle", "int", True, "6")

    for raw in ("1", "0", "-2", "six"):
        with pytest.raises(platform_settings.PlatformSettingError) as refused:
            platform_settings.set_setting("ZENTINELLE_GATEWAY_MAX_REPLICAS", raw)
        assert refused.value.code == "VALIDATION", raw
    assert config.ZENTINELLE_GATEWAY_MAX_REPLICAS == 6

    platform_settings.set_setting("ZENTINELLE_GATEWAY_MAX_REPLICAS", "2")

    assert config.ZENTINELLE_GATEWAY_MAX_REPLICAS == 2
