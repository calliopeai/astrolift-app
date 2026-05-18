"""Tests for AKSClusterDriver (#42)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from azure._errors import NotFoundError
from azure.cluster_aks import AKSClusterDriver, AKSConfig, _NotFound


@dataclass
class FakeManagedCluster:
    fqdn: str = "acmeprod-1234.hcp.eastus.azmk8s.io"


@dataclass
class FakeManagedClusters:
    clusters: dict[str, FakeManagedCluster] = field(default_factory=dict)

    def get(
        self, *, resource_group_name: str, resource_name: str,
    ) -> FakeManagedCluster:
        return self.clusters.setdefault(
            resource_name, FakeManagedCluster(),
        )


@dataclass
class FakeAKS:
    managed_clusters: FakeManagedClusters = field(
        default_factory=FakeManagedClusters,
    )


@pytest.fixture
def fake_k8s_client() -> Any:
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
def driver(fake_k8s_client: Any) -> AKSClusterDriver:
    return AKSClusterDriver(
        config=AKSConfig(
            subscription_id="sub-1",
            resource_group="rg",
            cluster_name="prod",
            container_service_client=FakeAKS(),
        ),
        k8s_client_factory=lambda **kw: fake_k8s_client,
    )


def test_apply_aggregates(
    driver: AKSClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.server_side_apply.side_effect = [
        "created", "updated", "unchanged",
    ]
    result = driver.apply_manifests(
        "azure-prod", "ns",
        [
            {"kind": "Deployment", "metadata": {"name": "a"}},
            {"kind": "Service", "metadata": {"name": "b"}},
            {"kind": "ConfigMap", "metadata": {"name": "c"}},
        ],
    )
    assert result.created == ["Deployment/a"]
    assert result.updated == ["Service/b"]


def test_delete_classifies_not_found(
    driver: AKSClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.delete.side_effect = [None, _NotFound()]
    result = driver.delete_manifests(
        "azure-prod", "ns",
        [
            {"kind": "Deployment", "metadata": {"name": "exists"}},
            {"kind": "Deployment", "metadata": {"name": "absent"}},
        ],
    )
    assert result.deleted == ["Deployment/exists"]
    assert result.not_found == ["Deployment/absent"]


def test_get_namespace_returns_state(
    driver: AKSClusterDriver, fake_k8s_client,
) -> None:
    state = driver.get_namespace("azure-prod", "ns")
    assert state is not None
    assert state.phase == "Active"


def test_get_namespace_missing_returns_none(
    driver: AKSClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.get_namespace.side_effect = _NotFound()
    assert driver.get_namespace("azure-prod", "missing") is None


def test_workload_status_not_found(
    driver: AKSClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.get.side_effect = _NotFound()
    with pytest.raises(NotFoundError):
        driver.get_workload_status(
            "azure-prod", "ns", "Deployment", "missing",
        )


def test_poll_rollout_success(
    driver: AKSClusterDriver, fake_k8s_client,
) -> None:
    result = driver.poll_rollout(
        "azure-prod", "ns", "Deployment", "api", timeout=5,
    )
    assert result.success is True


def test_poll_rollout_progressing_false(
    driver: AKSClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.get.return_value = {
        "metadata": {"name": "api"},
        "spec": {"replicas": 3},
        "status": {
            "readyReplicas": 0,
            "conditions": [{
                "type": "Progressing",
                "status": "False",
                "message": "ProgressDeadlineExceeded",
            }],
        },
    }
    result = driver.poll_rollout(
        "azure-prod", "ns", "Deployment", "api", timeout=5,
    )
    assert result.success is False


def test_k8s_client_caches(
    driver: AKSClusterDriver, fake_k8s_client,
) -> None:
    driver.apply_manifests("azure-prod", "ns", [])
    driver.apply_manifests("azure-prod", "ns", [])
    assert "azure-prod" in driver._k8s_cache
