"""Actual EKS apply dispatch stages direct 503 before any custom backend."""

import copy
from unittest.mock import MagicMock

import pytest

from aws.cluster_eks import EKSClusterDriver, EKSConfig
from k8s_native.edge_gateway import render_custom_domain_routes

CONFIG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc",
    "client_id": "client",
    "auth_proxy_host": "auth.apps.example.net",
}


class Api:
    def __init__(self):
        self.objects = {}
        self.applied = []
        self.fail_policy = False

    def get(self, *, kind, namespace, name):
        return copy.deepcopy(self.objects.get((kind.split("/")[-1], namespace, name)))

    def server_side_apply(self, *, namespace, manifest, dry_run):
        if dry_run:
            return "unchanged"
        if self.fail_policy and manifest["kind"] == "SecurityPolicy":
            raise RuntimeError("provider error echoed private material")
        item = copy.deepcopy(manifest)
        key = (item["kind"], namespace, item["metadata"]["name"])
        previous = self.objects.get(key) or {}
        generation = previous.get("metadata", {}).get("generation", 0)
        item["metadata"]["generation"] = generation + (previous.get("spec") != item.get("spec"))
        item["status"] = previous.get("status", {})
        self.objects[key] = item
        self.applied.append(item)
        return "created" if not previous else "updated"

    def accept_policy(self, name, *, observed=None, status="True"):
        policy = self.objects[("SecurityPolicy", "astrolift-edge", name)]
        generation = policy["metadata"]["generation"]
        policy["status"] = {
            "ancestors": [
                {
                    "ancestorRef": {
                        "group": "gateway.networking.k8s.io",
                        "kind": "Gateway",
                        "namespace": "astrolift-edge",
                        "name": "edge",
                    },
                    "controllerName": "gateway.envoyproxy.io/gatewayclass-controller",
                    "conditions": [
                        {
                            "type": "Accepted",
                            "status": status,
                            "observedGeneration": generation if observed is None else observed,
                        }
                    ],
                }
            ]
        }


@pytest.fixture
def setup():
    api = Api()
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="test"),
        eks_client=MagicMock(),
        sts_client=MagicMock(),
        ec2_client=MagicMock(),
    )
    driver._k8s = lambda cluster: api
    manifests = render_custom_domain_routes(
        app_slug="app",
        namespace="org-app",
        hostname="customer.example",
        service="web",
        port=8080,
        gated=True,
        paused=False,
        config=CONFIG,
        access={"groups": ["staff"], "users": []},
        certificate_arn="arn:cert",
        alb_group="edge",
        platform_namespace="astrolift-system",
    )
    manifests.append(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "web", "namespace": "org-app"},
            "spec": {"replicas": 1},
        }
    )
    return driver, api, manifests


def policy(manifests):
    return next(item for item in manifests if item["kind"] == "SecurityPolicy")


def guarded(api, name):
    route = api.objects[("HTTPRoute", "astrolift-edge", name)]
    ref = route["spec"]["rules"][0]["filters"][-1]["extensionRef"]
    filter_ = api.objects[("HTTPRouteFilter", "astrolift-edge", ref["name"])]
    assert filter_["spec"]["directResponse"]["statusCode"] == 503
    assert route["spec"]["rules"][0]["backendRefs"][0]["name"] == "web"


def test_first_apply_stages_503_then_retry_opens_only_after_current_policy_accepted(setup):
    driver, api, manifests = setup
    result = driver.apply_manifests("cluster", "org-app", manifests)
    assert not result.ok
    assert result.errors[0].is_retryable
    assert [item["kind"] for item in api.applied] == ["HTTPRouteFilter", "HTTPRoute", "SecurityPolicy"]
    name = policy(manifests)["metadata"]["name"]
    guarded(api, name)
    assert not any(item["kind"] == "Deployment" for item in api.applied)
    api.accept_policy(name)
    assert driver.apply_manifests("cluster", "org-app", manifests).ok
    route = api.objects[("HTTPRoute", "astrolift-edge", name)]
    assert all(filter_["type"] != "ExtensionRef" for filter_ in route["spec"]["rules"][0]["filters"])
    assert any(item["kind"] == "Deployment" for item in api.applied)


@pytest.mark.parametrize(
    "state",
    [
        "stale-generation",
        "missing-generation",
        "missing-observed",
        "rejected",
        "different-spec",
        "wrong-gateway",
        "wrong-controller",
        "overridden",
    ],
)
def test_stale_accepted_or_changed_policy_cannot_open_backend(setup, state):
    driver, api, manifests = setup
    driver.apply_manifests("cluster", "org-app", manifests)
    name = policy(manifests)["metadata"]["name"]
    api.accept_policy(name)
    current = api.objects[("SecurityPolicy", "astrolift-edge", name)]
    if state == "stale-generation":
        current["status"]["ancestors"][0]["conditions"][0]["observedGeneration"] = 0
    if state == "missing-generation":
        current["metadata"].pop("generation")
    if state == "missing-observed":
        current["status"]["ancestors"][0]["conditions"][0].pop("observedGeneration")
    if state == "rejected":
        current["status"]["ancestors"][0]["conditions"][0]["status"] = "False"
    if state == "different-spec":
        current["spec"]["oidc"]["redirectURL"] = "https://sibling.example/oauth2/callback"
    if state == "wrong-gateway":
        current["status"]["ancestors"][0]["ancestorRef"]["name"] = "sibling"
    if state == "wrong-controller":
        current["status"]["ancestors"][0]["controllerName"] = "another-controller"
    if state == "overridden":
        current["status"]["ancestors"][0]["conditions"].append(
            {"type": "Overridden", "status": "True", "observedGeneration": current["metadata"]["generation"]}
        )
    api.applied.clear()
    assert not driver.apply_manifests("cluster", "org-app", manifests).ok
    guarded(api, name)
    assert not any(item["kind"] == "Deployment" for item in api.applied)


def test_policy_only_refresh_restores_existing_route_after_acceptance(setup):
    driver, api, manifests = setup
    driver.apply_manifests("cluster", "org-app", manifests)
    name = policy(manifests)["metadata"]["name"]
    api.accept_policy(name)
    driver.apply_manifests("cluster", "org-app", manifests)
    update = copy.deepcopy(policy(manifests))
    update["spec"]["authorization"]["rules"][0]["name"] = "new-grant"
    api.applied.clear()
    assert not driver.apply_manifests("cluster", "astrolift-edge", [update]).ok
    guarded(api, name)
    api.accept_policy(name)
    assert driver.apply_manifests("cluster", "astrolift-edge", [update]).ok
    route = api.objects[("HTTPRoute", "astrolift-edge", name)]
    assert all(filter_["type"] != "ExtensionRef" for filter_ in route["spec"]["rules"][0]["filters"])


def test_policy_apply_failure_leaves_existing_backend_guarded_and_hides_provider_text(setup, caplog):
    driver, api, manifests = setup
    driver.apply_manifests("cluster", "org-app", manifests)
    name = policy(manifests)["metadata"]["name"]
    api.accept_policy(name)
    driver.apply_manifests("cluster", "org-app", manifests)
    update = copy.deepcopy(policy(manifests))
    update["spec"]["oidc"]["clientID"] = "new-client"
    api.fail_policy = True
    result = driver.apply_manifests("cluster", "astrolift-edge", [update])
    assert not result.ok
    guarded(api, name)
    assert "private material" not in str(result.errors) + caplog.text


def test_dry_run_has_no_staging_writes_and_no_reads(setup):
    driver, api, manifests = setup
    assert driver.apply_manifests("cluster", "org-app", manifests, dry_run=True).ok
    assert api.objects == {}
    assert api.applied == []


def test_access_refresh_does_not_recreate_a_deleted_route(setup):
    driver, api, manifests = setup
    result = driver.apply_manifests("cluster", "astrolift-edge", [policy(manifests)])
    assert not result.ok
    assert api.objects == {}
