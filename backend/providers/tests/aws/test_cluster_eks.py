"""Tests for AWS EKS ClusterDriver (#29).

The driver's logic that's tested here:
- boto3 EKS auth flow (DescribeCluster → endpoint + CA)
- Manifest dispatch + result aggregation (apply / delete)
- Workload status projection
- Rollout polling (success / failure / timeout)
- Namespace lifecycle

Real apiserver interactions are stubbed via the
k8s_client_factory injection so tests run without a live EKS
cluster.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest

from _sdk.cluster import (
    ClusterContext,
    WorkloadStatus,
)
from aws._errors import NotFoundError
from aws.cluster_eks import EKSClusterDriver, EKSConfig, _NotFoundError


@pytest.fixture
def fake_k8s_client() -> Any:
    """Stub k8s client with controllable per-method behavior."""
    client = MagicMock()
    client.server_side_apply.return_value = "created"
    client.delete.return_value = None
    client.get.return_value = {
        "metadata": {"name": "x"},
        "spec": {"replicas": 3},
        "status": {"readyReplicas": 3, "conditions": []},
    }
    client.get_namespace.return_value = {
        "metadata": {"name": "ns", "labels": {}, "annotations": {}},
        "status": {"phase": "Active"},
    }
    return client


@pytest.fixture
def driver(fake_k8s_client) -> EKSClusterDriver:
    from moto import mock_aws

    with mock_aws():
        eks_client = boto3.client("eks", region_name="us-east-1")
        sts_client = boto3.client("sts", region_name="us-east-1")
        # moto needs an actual EKS cluster for DescribeCluster
        eks_client.create_cluster(
            name="test-cluster",
            version="1.30",
            roleArn=("arn:aws:iam::123456789012:role/eks-cluster-role"),
            resourcesVpcConfig={
                "subnetIds": ["subnet-12345678"],
            },
        )
        d = EKSClusterDriver(
            config=EKSConfig(
                region="us-east-1",
                cluster_name="test-cluster",
            ),
            eks_client=eks_client,
            sts_client=sts_client,
            k8s_client_factory=lambda **kw: fake_k8s_client,
        )
        yield d


def test_filesystem_preflight_inventory(driver: EKSClusterDriver, fake_k8s_client) -> None:
    fake_k8s_client.list.return_value = [
        {"metadata": {"name": "efs.csi.aws.com"}},
        {"metadata": {"name": "ebs.csi.aws.com"}},
    ]

    assert driver.list_csi_drivers("test-cluster") == ["ebs.csi.aws.com", "efs.csi.aws.com"]
    assert driver.persistent_volume_claim_exists("test-cluster", "acme-api", "shared") is True
    assert (
        driver.get_manifest(
            "test-cluster",
            "storage",
            "seaweed.seaweedfs.com/v1/Bucket",
            "uploads",
        )
        == fake_k8s_client.get.return_value
    )
    fake_k8s_client.get.assert_called_with(
        kind="seaweed.seaweedfs.com/v1/Bucket",
        namespace="storage",
        name="uploads",
    )


# ---- describe + auth ---------------------------------------------


def test_driver_caches_k8s_client_per_cluster(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    """Multiple operations on the same cluster don't re-call
    DescribeCluster + don't rebuild the k8s client."""
    driver.apply_manifests("aws-prod", "ns", [])
    driver.apply_manifests("aws-prod", "ns", [])
    # Implementation detail: the cache stores per-cluster
    assert "aws-prod" in driver._k8s_cache


# ---- apply_manifests ---------------------------------------------


def test_apply_aggregates_outcomes(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    """Per-manifest outcomes aggregate into ApplyResult."""
    fake_k8s_client.server_side_apply.side_effect = [
        "created",
        "updated",
        "unchanged",
    ]
    result = driver.apply_manifests(
        "aws-prod",
        "ns",
        [
            {"kind": "Deployment", "metadata": {"name": "a"}},
            {"kind": "Service", "metadata": {"name": "b"}},
            {"kind": "ConfigMap", "metadata": {"name": "c"}},
        ],
    )
    assert result.created == ["Deployment/a"]
    assert result.updated == ["Service/b"]
    assert result.unchanged == ["ConfigMap/c"]
    assert result.errors == []
    assert result.ok is True


def test_apply_collects_errors(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.server_side_apply.side_effect = [
        "created",
        Exception("webhook denied"),
    ]
    result = driver.apply_manifests(
        "aws-prod",
        "ns",
        [
            {"kind": "Deployment", "metadata": {"name": "ok"}},
            {"kind": "Service", "metadata": {"name": "bad"}},
        ],
    )
    assert result.created == ["Deployment/ok"]
    assert len(result.errors) == 1
    # Structured ApplyError (#603) -- str() preserves the legacy
    # ``Kind/name: message`` shape while the new fields expose the
    # exception type + retryability for the workflow's classifier.
    err = result.errors[0]
    assert err.kind == "Service"
    assert err.name == "bad"
    assert "webhook denied" in err.exception_message
    assert "webhook denied" in str(err)
    assert result.ok is False


def test_apply_propagates_dry_run(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    """dry_run flag must reach the k8s client wrapper."""
    driver.apply_manifests(
        "aws-prod",
        "ns",
        [{"kind": "Deployment", "metadata": {"name": "x"}}],
        dry_run=True,
    )
    fake_k8s_client.server_side_apply.assert_called_with(
        namespace="ns",
        manifest={"kind": "Deployment", "metadata": {"name": "x"}},
        dry_run=True,
    )


def test_apply_routes_each_manifest_to_its_own_namespace(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    """apply_manifests honors each manifest's own metadata.namespace
    (kubectl-style), falling back to the passed namespace when a manifest
    declares none. This lets one call carry a multi-namespace batch — the
    Knative post-install applies the vendored operator (knative-operator ns)
    alongside the KnativeServing CR (knative-serving ns) and a cluster-scoped
    Namespace. Without this, server-side apply would push every object through
    the single passed namespace and the apiserver would 422-reject the CR."""
    driver.apply_manifests(
        "aws-prod",
        "fallback-ns",
        [
            {"kind": "Deployment", "metadata": {"name": "op", "namespace": "knative-operator"}},
            {
                "apiVersion": "operator.knative.dev/v1beta1",
                "kind": "KnativeServing",
                "metadata": {"name": "knative-serving", "namespace": "knative-serving"},
            },
            # Cluster-scoped, declares no namespace → falls back to the passed one.
            {"kind": "Namespace", "metadata": {"name": "knative-serving"}},
        ],
    )
    passed = [call.kwargs["namespace"] for call in fake_k8s_client.server_side_apply.call_args_list]
    assert passed == ["knative-operator", "knative-serving", "fallback-ns"]


# ---- delete_manifests --------------------------------------------


def test_delete_classifies_not_found_separately(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.delete.side_effect = [None, _NotFoundError()]
    result = driver.delete_manifests(
        "aws-prod",
        "ns",
        [
            {"kind": "Deployment", "metadata": {"name": "exists"}},
            {"kind": "Deployment", "metadata": {"name": "absent"}},
        ],
    )
    assert result.deleted == ["Deployment/exists"]
    assert result.not_found == ["Deployment/absent"]
    assert result.errors == []


def test_delete_collects_errors(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.delete.side_effect = Exception("forbidden")
    result = driver.delete_manifests(
        "aws-prod",
        "ns",
        [{"kind": "Service", "metadata": {"name": "bad"}}],
    )
    assert result.errors == ["Service/bad: forbidden"]
    assert result.ok is False


# ---- namespaces ---------------------------------------------------


def test_get_namespace_returns_state(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.get_namespace.return_value = {
        "metadata": {
            "name": "acme-api",
            "labels": {"astrolift.io/managed-by": "platform"},
            "annotations": {},
        },
        "status": {"phase": "Active"},
    }
    state = driver.get_namespace("aws-prod", "acme-api")
    assert state is not None
    assert state.name == "acme-api"
    assert state.phase == "Active"
    assert state.labels["astrolift.io/managed-by"] == "platform"


def test_get_namespace_missing_returns_none(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.get_namespace.side_effect = _NotFoundError()
    assert driver.get_namespace("aws-prod", "missing") is None


def test_get_namespace_none_response_returns_none(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    """Some client backends return None (not raise NotFound) when the
    namespace is gone — e.g. finished Terminating mid-teardown. get_namespace
    must treat None as "gone", not crash on ns["metadata"] (#1015 teardown
    wedge: the crash failed delete_namespace's wait loop, gating the final
    soft-delete and leaving the app stuck at tearing_down)."""
    fake_k8s_client.get_namespace.return_value = None
    assert driver.get_namespace("aws-prod", "terminating") is None


def test_ensure_namespace_calls_apply(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    ns = driver.ensure_namespace(
        "aws-prod",
        "acme-api",
        labels={"astrolift.io/managed-by": "platform"},
        annotations={"k": "v"},
    )
    assert ns.name == "acme-api"
    fake_k8s_client.server_side_apply.assert_called()


def test_delete_namespace_no_wait(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    driver.delete_namespace("aws-prod", "old-ns", wait=False)
    fake_k8s_client.delete.assert_called_with(
        kind="Namespace",
        namespace=None,
        name="old-ns",
    )


def test_delete_namespace_idempotent_on_missing(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.delete.side_effect = _NotFoundError()
    # Should NOT raise
    driver.delete_namespace("aws-prod", "missing", wait=False)


# ---- workload status / rollout -----------------------------------


def test_get_workload_status_projection(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.get.return_value = {
        "metadata": {"name": "api"},
        "spec": {"replicas": 5},
        "status": {
            "readyReplicas": 3,
            "conditions": [
                {"type": "Progressing", "status": "True"},
            ],
        },
    }
    status = driver.get_workload_status(
        "aws-prod",
        "ns",
        "Deployment",
        "api",
    )
    assert status.ready_replicas == 3
    assert status.desired_replicas == 5
    assert len(status.conditions) == 1


def test_get_workload_status_not_found(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.get.side_effect = _NotFoundError()
    with pytest.raises(NotFoundError):
        driver.get_workload_status(
            "aws-prod",
            "ns",
            "Deployment",
            "missing",
        )


def test_poll_rollout_returns_success_when_ready(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.get.return_value = {
        "metadata": {"name": "api"},
        "spec": {"replicas": 3},
        "status": {"readyReplicas": 3, "conditions": []},
    }
    result = driver.poll_rollout(
        "aws-prod",
        "ns",
        "Deployment",
        "api",
        timeout=5,
    )
    assert result.success is True
    assert result.timed_out is False


def test_poll_rollout_detects_progressing_failure(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    """Progressing=False is k8s-speak for rollout stuck."""
    fake_k8s_client.get.return_value = {
        "metadata": {"name": "api"},
        "spec": {"replicas": 3},
        "status": {
            "readyReplicas": 0,
            "conditions": [
                {
                    "type": "Progressing",
                    "status": "False",
                    "message": "ProgressDeadlineExceeded",
                }
            ],
        },
    }
    result = driver.poll_rollout(
        "aws-prod",
        "ns",
        "Deployment",
        "api",
        timeout=5,
    )
    assert result.success is False
    assert result.timed_out is False
    assert "ProgressDeadlineExceeded" in result.message


def test_poll_rollout_invokes_on_tick(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    """on_tick callback fires per poll loop."""
    fake_k8s_client.get.return_value = {
        "metadata": {"name": "api"},
        "spec": {"replicas": 3},
        "status": {"readyReplicas": 3, "conditions": []},
    }
    seen: list[WorkloadStatus] = []
    driver.poll_rollout(
        "aws-prod",
        "ns",
        "Deployment",
        "api",
        timeout=5,
        on_tick=seen.append,
    )
    # Success on first tick → at least one callback
    assert len(seen) >= 1


def test_poll_rollout_workload_not_found(
    driver: EKSClusterDriver,
    fake_k8s_client,
) -> None:
    fake_k8s_client.get.side_effect = _NotFoundError()
    result = driver.poll_rollout(
        "aws-prod",
        "ns",
        "Deployment",
        "missing",
        timeout=5,
    )
    assert result.success is False
    assert "not found" in result.message


# ---- bootstrap_components: LB controller backendSecurityGroup -------


_CLUSTER = "astrolift-eks"


@contextmanager
def _bootstrap_driver(fake_k8s_client: Any, *, tag_node_sg: bool = True):
    """EKS driver backed by moto with a VPC, an EKS cluster, and the
    dual-SG layout that trips the LB controller's tag discovery: the
    ``eks-cluster-sg-*`` group and the ``*-node-*`` shared group, both
    tagged ``kubernetes.io/cluster/<name>: owned``.

    Yields ``(driver, node_sg_id)``. The moto context is torn down on
    block exit even when an assertion fails, so the in-memory AWS state
    can't leak into the next test.
    """
    from moto import mock_aws

    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-west-2")
        eks = boto3.client("eks", region_name="us-west-2")
        sts = boto3.client("sts", region_name="us-west-2")

        vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        subnet_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]
        cluster_sg = ec2.create_security_group(
            GroupName=f"eks-cluster-sg-{_CLUSTER}-1234567890",
            Description="EKS-created cluster SG",
            VpcId=vpc_id,
        )["GroupId"]
        node_sg = ec2.create_security_group(
            GroupName=f"{_CLUSTER}-node-20240101000000000000000001",
            Description="node shared SG",
            VpcId=vpc_id,
        )["GroupId"]
        owned = [{"Key": f"kubernetes.io/cluster/{_CLUSTER}", "Value": "owned"}]
        # The cluster SG is always tagged owned; the node SG tag is the knob
        # the no-match test flips off.
        ec2.create_tags(Resources=[cluster_sg], Tags=owned)
        if tag_node_sg:
            ec2.create_tags(Resources=[node_sg], Tags=owned)

        eks.create_cluster(
            name=_CLUSTER,
            version="1.30",
            roleArn="arn:aws:iam::123456789012:role/eks-cluster-role",
            resourcesVpcConfig={"subnetIds": [subnet_id]},
        )

        d = EKSClusterDriver(
            config=EKSConfig(region="us-west-2", cluster_name=_CLUSTER),
            eks_client=eks,
            sts_client=sts,
            ec2_client=ec2,
            k8s_client_factory=lambda **kw: fake_k8s_client,
        )
        yield d, node_sg


def _alb_values(components: list) -> dict:
    for c in components:
        if c.key == "aws-load-balancer-controller":
            return c.helm_values
    raise AssertionError("aws-load-balancer-controller component not found")


def _component_values(components: list, key: str) -> dict:
    for c in components:
        if c.key == key:
            return c.helm_values
    raise AssertionError(f"{key} component not found")


def _mock_bootstrap_driver(fake_k8s_client: Any) -> EKSClusterDriver:
    """EKS driver wired with recording boto3 doubles (moto-free) for the
    metadata calls bootstrap_components makes (STS account id, EKS
    DescribeCluster vpc id, EC2 SG discovery)."""
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    eks = MagicMock()
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-123"}},
    }
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    return EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name=_CLUSTER),
        eks_client=eks,
        sts_client=sts,
        ec2_client=ec2,
        k8s_client_factory=lambda **kw: fake_k8s_client,
    )


def test_bootstrap_includes_ebs_csi_with_irsa_sa(fake_k8s_client) -> None:
    """The EKS recipe ships aws-ebs-csi-driver (#1024) so StatefulSet PVCs
    bind, and its controller SA carries the convention IRSA role
    annotation (arn:...:role/<cluster>-aws-ebs-csi-driver)."""
    driver = _mock_bootstrap_driver(fake_k8s_client)
    ctx = ClusterContext(slug="aws-prod", auth_method="exec_plugin")

    component = _component(driver.bootstrap_components(ctx), "aws-ebs-csi-driver")

    assert component.chart_name == "aws-ebs-csi-driver"
    sa = component.helm_values["controller"]["serviceAccount"]
    assert sa["name"] == "ebs-csi-controller-sa"
    assert sa["annotations"]["eks.amazonaws.com/role-arn"] == (
        f"arn:aws:iam::123456789012:role/{_CLUSTER}-aws-ebs-csi-driver"
    )


def _component(components: list, key: str):
    for c in components:
        if c.key == key:
            return c
    raise AssertionError(f"{key} component not found")


def test_bootstrap_external_dns_sync_policy_scoped_to_cluster(fake_k8s_client) -> None:
    """external-dns must run with policy=sync (the chart defaults to
    upsert-only, which never deletes → orphaned Route53 records on every
    app teardown) and a per-cluster txtOwnerId so sync deletes are scoped
    to records this cluster owns when installs share a hosted zone."""
    with _bootstrap_driver(fake_k8s_client) as (driver, _):
        ctx = ClusterContext(slug="aws-prod", auth_method="exec_plugin")

        values = _component_values(driver.bootstrap_components(ctx), "external-dns")

        assert values["policy"] == "sync"
        assert values["txtOwnerId"] == _CLUSTER


def test_bootstrap_sets_backend_sg_to_node_shared_sg(fake_k8s_client) -> None:
    """The LB controller's backendSecurityGroup resolves to the
    ``*-node-*`` shared SG, not the ``eks-cluster-sg-*`` group — the
    fix for the dual-SG FailedNetworkReconcile error."""
    with _bootstrap_driver(fake_k8s_client) as (driver, node_sg):
        ctx = ClusterContext(slug="aws-prod", auth_method="exec_plugin")

        values = _alb_values(driver.bootstrap_components(ctx))

        assert values["backendSecurityGroup"] == node_sg
        assert not values["backendSecurityGroup"].startswith("eks-cluster-sg-")


def test_bootstrap_backend_sg_override_skips_discovery(fake_k8s_client) -> None:
    """auth_config["backend_security_group"] is taken verbatim and no
    EC2 discovery runs."""
    with _bootstrap_driver(fake_k8s_client) as (driver, _):
        driver._ec2 = MagicMock()  # discovery must not be called
        ctx = ClusterContext(
            slug="aws-prod",
            auth_method="exec_plugin",
            auth_config={"backend_security_group": "sg-override123"},
        )

        values = _alb_values(driver.bootstrap_components(ctx))

        assert values["backendSecurityGroup"] == "sg-override123"
        driver._ec2.describe_security_groups.assert_not_called()


def test_bootstrap_omits_backend_sg_when_no_node_sg(fake_k8s_client) -> None:
    """No ``*-node-*`` SG tagged owned → key omitted so the controller
    falls back to tag discovery rather than getting a bad value."""
    with _bootstrap_driver(fake_k8s_client, tag_node_sg=False) as (driver, _):
        ctx = ClusterContext(slug="aws-prod", auth_method="exec_plugin")

        values = _alb_values(driver.bootstrap_components(ctx))

        assert "backendSecurityGroup" not in values


def test_discover_backend_sg_returns_empty_on_describe_error(fake_k8s_client) -> None:
    """A failed describe_security_groups yields '' (caller omits the
    key) instead of raising."""
    with _bootstrap_driver(fake_k8s_client) as (driver, _):
        driver._ec2 = MagicMock()
        driver._ec2.describe_security_groups.side_effect = RuntimeError("boom")

        assert driver._discover_backend_sg(_CLUSTER) == ""


# ---- bootstrap_components: Knative Serving (kind=function) ------------
#
# kind=function workloads render as serving.knative.dev Services but
# nothing installed Knative. The knative-serving component is chart-free: the
# Knative Operator's OCI Helm chart isn't publicly pullable, so instead of a
# HelmRelease it carries the vendored official operator YAML plus the
# KnativeServing CR + its namespace as post_install_manifests. It is opt-in
# (Knative is heavyweight).


def _knative_component(fake_k8s_client: Any):
    driver = _mock_bootstrap_driver(fake_k8s_client)
    ctx = ClusterContext(slug="aws-prod", auth_method="exec_plugin")
    return _component(driver.bootstrap_components(ctx), "knative-serving")


def test_bootstrap_includes_knative_serving_opt_in(fake_k8s_client) -> None:
    """The EKS recipe ships a knative-serving component so kind=function
    workloads can run, and it is opt-in (default_enabled=False) because
    Knative is heavyweight. It is chart-free — the operator install ships as
    vendored post_install manifests, not a HelmRelease (its OCI chart 403s)."""
    component = _knative_component(fake_k8s_client)

    assert component.default_enabled is False
    # Chart-free: no HelmRelease is emitted for this component.
    assert component.chart_name == ""
    assert component.chart_repo_url == ""
    assert component.chart_version == ""
    # Gated behind the LB controller (Kourier's LoadBalancer needs it).
    assert component.depends_on == ["aws-load-balancer-controller"]


def test_bootstrap_knative_post_install_has_namespace_and_cr(fake_k8s_client) -> None:
    """post_install_manifests must carry the knative-serving Namespace AND a
    KnativeServing CR (operator.knative.dev/v1beta1) in the knative-serving
    namespace — the objects that stand Knative up after the operator installs."""
    component = _knative_component(fake_k8s_client)
    manifests = component.post_install_manifests

    ns = next(m for m in manifests if m["kind"] == "Namespace" and m["metadata"]["name"] == "knative-serving")
    assert ns["metadata"]["name"] == "knative-serving"

    cr = next(m for m in manifests if m["kind"] == "KnativeServing")
    assert cr["apiVersion"] == "operator.knative.dev/v1beta1"
    assert cr["metadata"]["name"] == "knative-serving"
    assert cr["metadata"]["namespace"] == "knative-serving"


def test_bootstrap_knative_post_install_bundles_vendored_operator(fake_k8s_client) -> None:
    """The chart-free component carries the vendored Knative Operator install:
    the knative-operator Namespace and the operator.knative.dev CRDs ship in
    post_install ahead of the KnativeServing CR, so applying the batch registers
    the CRD before the CR (the install path sorts foundational kinds first)."""
    component = _knative_component(fake_k8s_client)
    manifests = component.post_install_manifests

    # The vendored operator Namespace is present (distinct from knative-serving).
    assert any(m["kind"] == "Namespace" and m["metadata"]["name"] == "knative-operator" for m in manifests)
    # The CRD that defines the KnativeServing CR is bundled (from the vendored
    # YAML) so the CR has a registered type to bind to.
    crd_names = {m["metadata"]["name"] for m in manifests if m["kind"] == "CustomResourceDefinition"}
    assert "knativeservings.operator.knative.dev" in crd_names

    # And that CRD is authored before the KnativeServing CR in the list, so the
    # install path's foundational-first sort keeps it ahead of the CR.
    def _index(pred):
        return next(i for i, m in enumerate(manifests) if pred(m))

    crd_i = _index(
        lambda m: (
            m["kind"] == "CustomResourceDefinition" and m["metadata"]["name"] == "knativeservings.operator.knative.dev"
        )
    )
    cr_i = _index(lambda m: m["kind"] == "KnativeServing")
    assert crd_i < cr_i


def test_bootstrap_knative_cr_uses_kourier_ingress(fake_k8s_client) -> None:
    """Kourier is the ingress, and the default ingress-class is repointed to
    Kourier (otherwise Knative keeps expecting Istio)."""
    component = _knative_component(fake_k8s_client)
    cr = next(m for m in component.post_install_manifests if m["kind"] == "KnativeServing")

    assert cr["spec"]["ingress"]["kourier"]["enabled"] is True
    assert cr["spec"]["config"]["network"]["ingress-class"] == "kourier.ingress.networking.knative.dev"


def test_bootstrap_knative_cr_pre_enables_request_logging(fake_k8s_client) -> None:
    """Request logging is pre-enabled for the future invocation pipeline, and
    the template captures method/path/status/duration/revision."""
    component = _knative_component(fake_k8s_client)
    cr = next(m for m in component.post_install_manifests if m["kind"] == "KnativeServing")
    obs = cr["spec"]["config"]["observability"]

    assert obs["logging.enable-request-log"] == "true"
    template = obs["logging.request-log-template"]
    for field_var in (
        ".Request.Method",  # method
        ".Request.RequestURI",  # path
        ".Response.Code",  # status
        ".Response.Latency",  # duration
        ".Revision.Name",  # revision
    ):
        assert field_var in template


def test_bootstrap_existing_components_have_no_post_install(fake_k8s_client) -> None:
    """No regression: the pre-existing components install via their
    HelmRelease alone (empty post_install_manifests). Only knative-serving
    opts into the new post-install path."""
    driver = _mock_bootstrap_driver(fake_k8s_client)
    ctx = ClusterContext(slug="aws-prod", auth_method="exec_plugin")

    for component in driver.bootstrap_components(ctx):
        if component.key == "knative-serving":
            assert component.post_install_manifests  # the one opt-in
        else:
            assert component.post_install_manifests == []


# ---- list_certificates (#858) -------------------------------------


def _cert_driver(acm_client: Any) -> EKSClusterDriver:
    """An EKS driver wired only with an injected ACM client — enough to
    exercise list_certificates, which doesn't touch eks/sts/k8s."""
    return EKSClusterDriver(
        config=EKSConfig(region="us-east-1", cluster_name="test-cluster"),
        eks_client=MagicMock(),
        sts_client=MagicMock(),
        ec2_client=MagicMock(),
        acm_client=acm_client,
        k8s_client_factory=lambda **kw: MagicMock(),
    )


def _stub_acm(certs: list[dict[str, Any]], describe: dict[str, dict[str, Any]] | None = None) -> Any:
    """A stub ACM client returning canned ListCertificates +
    DescribeCertificate responses.

    Used instead of moto for the ISSUED-cert path because moto's ACM
    leaves ``request_certificate`` certs at PENDING_VALIDATION (correct
    real-AWS behavior — DNS-validated certs aren't ISSUED until
    validated), so a moto cert would never pass the driver's
    ISSUED-only filter. The stub tests the list/describe/map/label
    contract deterministically, independent of moto's issuance
    semantics. The moto-backed tests cover the empty + API-error paths.
    """
    describe = describe or {}

    class _Paginator:
        def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
            # The driver filters to ISSUED via CertificateStatuses; the
            # stub returns whatever it was seeded with (already ISSUED).
            assert kwargs.get("CertificateStatuses") == ["ISSUED"]
            return [{"CertificateSummaryList": certs}]

    acm = MagicMock()
    acm.get_paginator.return_value = _Paginator()
    acm.describe_certificate.side_effect = lambda CertificateArn: {  # noqa: N803 — mirrors the boto3 kwarg
        "Certificate": describe.get(CertificateArn, {}),
    }
    return acm


def test_list_certificates_returns_issued_acm_certs() -> None:
    """list_certificates surfaces ISSUED ACM certs (arn + domain +
    status), enriched via DescribeCertificate."""
    arn = "arn:aws:acm:us-east-1:123456789012:certificate/abc"
    acm = _stub_acm(
        certs=[{"CertificateArn": arn, "DomainName": "api.acme.example", "Status": "ISSUED"}],
        describe={
            arn: {"DomainName": "api.acme.example", "Status": "ISSUED", "SubjectAlternativeNames": ["api.acme.example"]}
        },
    )
    driver = _cert_driver(acm)

    certs = driver.list_certificates(ClusterContext(slug="aws-prod", auth_method="exec_plugin"))

    assert len(certs) == 1
    assert certs[0].arn == arn
    assert certs[0].domain_name == "api.acme.example"
    assert certs[0].status == "ISSUED"
    assert certs[0].name == "api.acme.example"


def test_list_certificates_multi_san_label() -> None:
    """A multi-SAN cert annotates the label with the extra-SAN count so
    the operator can tell wildcard / multi-domain certs apart."""
    arn = "arn:aws:acm:us-east-1:123456789012:certificate/def"
    acm = _stub_acm(
        certs=[{"CertificateArn": arn, "DomainName": "acme.example", "Status": "ISSUED"}],
        describe={
            arn: {
                "DomainName": "acme.example",
                "Status": "ISSUED",
                "SubjectAlternativeNames": ["acme.example", "*.acme.example"],
            }
        },
    )
    driver = _cert_driver(acm)

    certs = driver.list_certificates(ClusterContext(slug="aws-prod", auth_method="exec_plugin"))

    assert len(certs) == 1
    # 2 SANs → "(+1)" suffix on the label.
    assert certs[0].name == "acme.example (+1)"


def test_list_certificates_describe_failure_falls_back_to_list_data() -> None:
    """A per-cert DescribeCertificate failure keeps the cert in the list
    using the list-level domain/status rather than dropping it."""
    arn = "arn:aws:acm:us-east-1:123456789012:certificate/ghi"
    acm = _stub_acm(
        certs=[{"CertificateArn": arn, "DomainName": "fallback.example", "Status": "ISSUED"}],
    )
    acm.describe_certificate.side_effect = RuntimeError("describe boom")
    driver = _cert_driver(acm)

    certs = driver.list_certificates(ClusterContext(slug="aws-prod", auth_method="exec_plugin"))

    assert len(certs) == 1
    assert certs[0].arn == arn
    assert certs[0].domain_name == "fallback.example"
    assert certs[0].status == "ISSUED"


def test_list_certificates_empty_when_none(acm_client) -> None:
    """No certs → empty list (moto, no certs requested)."""
    driver = _cert_driver(acm_client)
    assert driver.list_certificates(ClusterContext(slug="aws-prod", auth_method="exec_plugin")) == []


def test_list_certificates_swallows_list_error() -> None:
    """A ListCertificates API failure degrades to an empty list rather
    than raising — the UI falls back to manual ARN entry."""
    bad_acm = MagicMock()
    bad_acm.get_paginator.side_effect = RuntimeError("boom")
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-east-1", cluster_name="test-cluster"),
        eks_client=MagicMock(),
        sts_client=MagicMock(),
        ec2_client=MagicMock(),
        acm_client=bad_acm,
        k8s_client_factory=lambda **kw: MagicMock(),
    )
    assert driver.list_certificates(ClusterContext(slug="aws-prod", auth_method="exec_plugin")) == []
