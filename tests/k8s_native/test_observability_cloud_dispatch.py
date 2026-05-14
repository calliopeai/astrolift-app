"""Cross-cloud test for the runtime-observability dispatch (#299).

Confirms EKS / GKE / AKS drivers satisfy the ``ClusterDriver``
protocol's new ``list_pods`` + ``stream_logs`` methods and dispatch
through the same pluggable backend the k8s_native driver uses. The
cloud-specific auth-translation hook (IRSA token mint via STS,
GKE Workload Identity, AKS federated creds) is the only place
these drivers diverge — that lives in #309/#310/#311 follow-ups.

These tests don't require any cloud SDK to be importable beyond
the modules already vendored — boto3, google-cloud-container,
azure-mgmt-containerservice are all pulled in by the existing
cluster-driver test suites in the same tests/ tree.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from _sdk.cluster import ClusterAuth, ContainerStatusInfo, PodInfo, PodLogLine
from aws.cluster_eks import EKSClusterDriver, EKSConfig
from azure.cluster_aks import AKSClusterDriver, AKSConfig
from gcp.cluster_gke import GKEClusterDriver, GKEConfig


def _auth() -> ClusterAuth:
    return ClusterAuth(
        slug="cluster-a",
        auth_method="kubeconfig",
        auth_config={"kubeconfig": "x"},
    )


def _pod() -> PodInfo:
    return PodInfo(
        name="hello-1",
        workload="web",
        status="Running",
        phase="Running",
        ready=True,
        restarts=0,
        age=datetime.now(UTC),
        node="node-a",
        container_statuses=[
            ContainerStatusInfo(
                name="web",
                ready=True,
                restart_count=0,
                image="x",
                state="running",
            ),
        ],
    )


class _PodBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_pods(self, *, auth: ClusterAuth, namespace: str, app_slug: str) -> list[PodInfo]:
        self.calls.append(
            {"auth_slug": auth.slug, "namespace": namespace, "app_slug": app_slug}
        )
        return [_pod()]


class _LogBackend:
    async def stream(self, **_kw: Any) -> AsyncIterator[PodLogLine]:
        yield PodLogLine(
            pod_name="hello-1",
            container="web",
            timestamp=datetime.now(UTC),
            message="line 0",
            stream="stdout",
        )


def test_eks_driver_dispatches_list_pods() -> None:
    pod_be = _PodBackend()
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="x"),
        eks_client=MagicMock(),
        sts_client=MagicMock(),
        pod_backend=pod_be,
    )
    pods = driver.list_pods(auth=_auth(), namespace="ns", app_slug="app")
    assert [p.name for p in pods] == ["hello-1"]
    assert pod_be.calls == [{"auth_slug": "cluster-a", "namespace": "ns", "app_slug": "app"}]


def test_gke_driver_dispatches_list_pods() -> None:
    pod_be = _PodBackend()
    driver = GKEClusterDriver(
        config=GKEConfig(
            project_id="p",
            location="us-central1",
            cluster_name="x",
            container_client=MagicMock(),
        ),
        pod_backend=pod_be,
    )
    pods = driver.list_pods(auth=_auth(), namespace="ns", app_slug="app")
    assert [p.name for p in pods] == ["hello-1"]
    assert pod_be.calls[0]["app_slug"] == "app"


def test_aks_driver_dispatches_list_pods() -> None:
    pod_be = _PodBackend()
    driver = AKSClusterDriver(
        config=AKSConfig(
            subscription_id="s",
            resource_group="r",
            cluster_name="x",
            container_service_client=MagicMock(),
        ),
        pod_backend=pod_be,
    )
    pods = driver.list_pods(auth=_auth(), namespace="ns", app_slug="app")
    assert [p.name for p in pods] == ["hello-1"]
    assert pod_be.calls[0]["app_slug"] == "app"


@pytest.mark.asyncio
async def test_eks_driver_dispatches_stream_logs() -> None:
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="x"),
        eks_client=MagicMock(),
        sts_client=MagicMock(),
        log_backend=_LogBackend(),
    )
    out: list[PodLogLine] = []
    async for line in driver.stream_logs(
        auth=_auth(),
        namespace="ns",
        pod_name="hello-1",
        container=None,
        tail_lines=10,
        follow=False,
    ):
        out.append(line)
    assert [line.message for line in out] == ["line 0"]


@pytest.mark.asyncio
async def test_gke_driver_dispatches_stream_logs() -> None:
    driver = GKEClusterDriver(
        config=GKEConfig(
            project_id="p",
            location="us-central1",
            cluster_name="x",
            container_client=MagicMock(),
        ),
        log_backend=_LogBackend(),
    )
    out: list[PodLogLine] = []
    async for line in driver.stream_logs(
        auth=_auth(),
        namespace="ns",
        pod_name="hello-1",
        container=None,
        tail_lines=10,
        follow=False,
    ):
        out.append(line)
    assert [line.message for line in out] == ["line 0"]


@pytest.mark.asyncio
async def test_aks_driver_dispatches_stream_logs() -> None:
    driver = AKSClusterDriver(
        config=AKSConfig(
            subscription_id="s",
            resource_group="r",
            cluster_name="x",
            container_service_client=MagicMock(),
        ),
        log_backend=_LogBackend(),
    )
    out: list[PodLogLine] = []
    async for line in driver.stream_logs(
        auth=_auth(),
        namespace="ns",
        pod_name="hello-1",
        container=None,
        tail_lines=10,
        follow=False,
    ):
        out.append(line)
    assert [line.message for line in out] == ["line 0"]


# Confirm a sentinel from the protocol-extension lands — the EKS
# driver previously didn't satisfy ClusterDriver because the
# protocol added methods. asyncio.iscoroutinefunction is not the
# right check (these return async iterators); just call them and
# make sure they don't AttributeError.
def test_eks_driver_satisfies_new_protocol_methods() -> None:
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="x"),
        eks_client=MagicMock(),
        sts_client=MagicMock(),
        pod_backend=_PodBackend(),
        log_backend=_LogBackend(),
    )
    # Smoke: both methods exist and the protocol surface is satisfied.
    assert callable(driver.list_pods)
    assert callable(driver.stream_logs)


async def _drain(gen: AsyncIterator[PodLogLine]) -> list[PodLogLine]:
    out: list[PodLogLine] = []
    async for line in gen:
        out.append(line)
    return out


@pytest.mark.asyncio
async def test_drain_helper_is_correct() -> None:
    """Sanity check on the drain helper — covered by the cloud tests
    above but explicit here so a stray AsyncIterator typing slip is
    caught before the cloud tests scream."""
    backend = _LogBackend()
    gen = backend.stream()
    out = await asyncio.wait_for(_drain(gen), timeout=1.0)
    assert [line.message for line in out] == ["line 0"]
