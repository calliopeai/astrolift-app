"""Tests for GKEClusterDriver (#36).

Mirrors the EKS test file in shape — k8s_client_factory injects a
fake k8s client; container_v1 stub serves DescribeCluster.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from _sdk.cluster import WorkloadStatus
from gcp._errors import NotFoundError
from gcp.cluster_gke import GKEClusterDriver, GKEConfig, _NotFound


@dataclass
class FakeMasterAuth:
    cluster_ca_certificate: str = "BASE64-CA"


@dataclass
class FakeCluster:
    endpoint: str = "1.2.3.4"
    master_auth: FakeMasterAuth = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.master_auth is None:
            self.master_auth = FakeMasterAuth()


class FakeContainerClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_cluster(self, *, name: str) -> FakeCluster:
        self.calls.append(name)
        return FakeCluster()


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
def driver(fake_k8s_client: Any) -> GKEClusterDriver:
    return GKEClusterDriver(
        config=GKEConfig(
            project_id="acme",
            location="us-central1",
            cluster_name="prod",
            container_client=FakeContainerClient(),
        ),
        k8s_client_factory=lambda **kw: fake_k8s_client,
    )


def test_apply_aggregates_outcomes(
    driver: GKEClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.server_side_apply.side_effect = [
        "created", "updated", "unchanged",
    ]
    result = driver.apply_manifests(
        "gcp-prod", "ns",
        [
            {"kind": "Deployment", "metadata": {"name": "a"}},
            {"kind": "Service", "metadata": {"name": "b"}},
            {"kind": "ConfigMap", "metadata": {"name": "c"}},
        ],
    )
    assert result.created == ["Deployment/a"]
    assert result.updated == ["Service/b"]
    assert result.unchanged == ["ConfigMap/c"]
    assert result.ok is True


def test_apply_collects_errors(
    driver: GKEClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.server_side_apply.side_effect = [
        "created", Exception("webhook denied"),
    ]
    result = driver.apply_manifests(
        "gcp-prod", "ns",
        [
            {"kind": "Deployment", "metadata": {"name": "ok"}},
            {"kind": "Service", "metadata": {"name": "bad"}},
        ],
    )
    assert result.ok is False
    assert "webhook denied" in result.errors[0]


def test_delete_classifies_not_found(
    driver: GKEClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.delete.side_effect = [None, _NotFound()]
    result = driver.delete_manifests(
        "gcp-prod", "ns",
        [
            {"kind": "Deployment", "metadata": {"name": "exists"}},
            {"kind": "Deployment", "metadata": {"name": "absent"}},
        ],
    )
    assert result.deleted == ["Deployment/exists"]
    assert result.not_found == ["Deployment/absent"]


def test_get_namespace_returns_state(
    driver: GKEClusterDriver, fake_k8s_client,
) -> None:
    state = driver.get_namespace("gcp-prod", "ns")
    assert state is not None
    assert state.name == "ns"
    assert state.phase == "Active"


def test_get_namespace_missing_returns_none(
    driver: GKEClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.get_namespace.side_effect = _NotFound()
    assert driver.get_namespace("gcp-prod", "missing") is None


def test_ensure_namespace_apply(
    driver: GKEClusterDriver, fake_k8s_client,
) -> None:
    ns = driver.ensure_namespace(
        "gcp-prod", "acme-api",
        labels={"astrolift.io/managed-by": "platform"},
        annotations={"k": "v"},
    )
    assert ns.name == "acme-api"
    fake_k8s_client.server_side_apply.assert_called()


def test_workload_status_not_found(
    driver: GKEClusterDriver, fake_k8s_client,
) -> None:
    fake_k8s_client.get.side_effect = _NotFound()
    with pytest.raises(NotFoundError):
        driver.get_workload_status(
            "gcp-prod", "ns", "Deployment", "missing",
        )


def test_poll_rollout_success(
    driver: GKEClusterDriver, fake_k8s_client,
) -> None:
    result = driver.poll_rollout(
        "gcp-prod", "ns", "Deployment", "api", timeout=5,
    )
    assert result.success is True


def test_poll_rollout_progressing_false_fails_fast(
    driver: GKEClusterDriver, fake_k8s_client,
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
        "gcp-prod", "ns", "Deployment", "api", timeout=5,
    )
    assert result.success is False
    assert result.timed_out is False


def test_k8s_client_caches_per_cluster(
    driver: GKEClusterDriver, fake_k8s_client,
) -> None:
    driver.apply_manifests("gcp-prod", "ns", [])
    driver.apply_manifests("gcp-prod", "ns", [])
    assert "gcp-prod" in driver._k8s_cache
