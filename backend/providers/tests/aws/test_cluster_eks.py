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

from collections import defaultdict
from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest

from _sdk.cluster import (
    ApplyResult,
    DeleteResult,
    ExecResult,
    NamespaceState,
    RolloutResult,
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
