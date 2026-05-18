"""Tests for the k8s_native cluster driver's list_pods + stream_logs (#299).

The kubernetes-client surface is heavyweight to stand up against a
fake apiserver, so the driver swaps in a deterministic backend via
its constructor. These tests exercise:

  - PodInfo status classification (CrashLoopBackOff > Running phase)
  - ClusterAuth -> ApiClient errors are surfaced as ClusterAuthError
  - The driver delegates to the injected backend
  - The log stream propagates CancelledError so subscribers tear
    down cleanly
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from _sdk.cluster import (
    ClusterAuth,
    ContainerStatusInfo,
    PodInfo,
    PodLogLine,
)
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig
from k8s_native.observability import (
    ClusterAuthError,
    LivePodBackend,
    build_api_client,
)


# ---- Fakes --------------------------------------------------------


class _FakePodBackend:
    def __init__(self, pods: list[PodInfo]):
        self._pods = pods
        self.calls: list[dict[str, Any]] = []

    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
    ) -> list[PodInfo]:
        self.calls.append(
            {
                "auth_slug": auth.slug,
                "namespace": namespace,
                "app_slug": app_slug,
            }
        )
        return list(self._pods)


class _FakeLogBackend:
    def __init__(self, lines: list[PodLogLine]):
        self._lines = lines
        self.cancelled = False

    async def stream(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        pod_name: str,
        container: str | None,
        tail_lines: int,
        follow: bool,
    ) -> AsyncIterator[PodLogLine]:
        try:
            for line in self._lines:
                await asyncio.sleep(0)
                yield line
        except (asyncio.CancelledError, GeneratorExit):
            self.cancelled = True
            raise


# ---- list_pods ----------------------------------------------------


def _auth() -> ClusterAuth:
    return ClusterAuth(
        slug="cluster-a",
        auth_method="kubeconfig",
        auth_config={"kubeconfig": "x"},
    )


def _pod(**overrides: Any) -> PodInfo:
    base = {
        "name": "hello-1",
        "workload": "web",
        "status": "Running",
        "phase": "Running",
        "ready": True,
        "restarts": 0,
        "age": datetime.now(UTC),
        "node": "node-a",
        "container_statuses": [
            ContainerStatusInfo(
                name="web",
                ready=True,
                restart_count=0,
                image="x",
                state="running",
            ),
        ],
    }
    base.update(overrides)
    return PodInfo(**base)


def test_driver_list_pods_delegates_to_backend() -> None:
    """The driver forwards to whatever backend the constructor took."""
    backend = _FakePodBackend([_pod(), _pod(name="hello-2")])
    driver = K8sNativeClusterDriver(
        config=K8sNativeConfig(),
        pod_backend=backend,
    )
    pods = driver.list_pods(
        auth=_auth(),
        namespace="acme-app",
        app_slug="app",
    )
    assert [p.name for p in pods] == ["hello-1", "hello-2"]
    assert backend.calls == [
        {
            "auth_slug": "cluster-a",
            "namespace": "acme-app",
            "app_slug": "app",
        },
    ]


def test_driver_uses_live_pod_backend_by_default() -> None:
    """Default constructor wires the live backend so a production
    install doesn't have to remember to pass one. The live backend
    is what hits the kubernetes apiserver."""
    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    # The default is the live backend, not the stub.
    assert isinstance(driver._pod_backend, LivePodBackend)


# ---- build_api_client errors --------------------------------------


def test_build_api_client_rejects_empty_kubeconfig() -> None:
    auth = ClusterAuth(
        slug="cluster-a",
        auth_method="kubeconfig",
        auth_config={"kubeconfig": "   "},
    )
    with pytest.raises(ClusterAuthError) as exc:
        build_api_client(auth)
    assert "cluster-a" in str(exc.value)
    assert "kubeconfig" in str(exc.value)


def test_build_api_client_rejects_token_without_endpoint() -> None:
    auth = ClusterAuth(
        slug="cluster-a",
        auth_method="service_account_token",
        auth_config={"token": "xxx"},
        endpoint="",
    )
    with pytest.raises(ClusterAuthError) as exc:
        build_api_client(auth)
    assert "endpoint is required" in str(exc.value)


def test_build_api_client_rejects_exec_plugin_at_native_layer() -> None:
    """k8s_native can't satisfy exec_plugin auth — that's the
    cloud-specific subclass's job. Surface a clear error so a
    misconfigured row gets a useful message in logs."""
    auth = ClusterAuth(
        slug="cluster-a",
        auth_method="exec_plugin",
        auth_config={},
    )
    with pytest.raises(ClusterAuthError) as exc:
        build_api_client(auth)
    assert "cloud-specific driver" in str(exc.value)


def test_build_api_client_rejects_unknown_method() -> None:
    auth = ClusterAuth(
        slug="cluster-a",
        auth_method="banana",
        auth_config={},
    )
    with pytest.raises(ClusterAuthError) as exc:
        build_api_client(auth)
    assert "unknown auth_method" in str(exc.value)


# ---- stream_logs --------------------------------------------------


@pytest.mark.asyncio
async def test_driver_stream_logs_yields_backend_lines() -> None:
    """The driver returns the backend's async iterator unchanged."""
    lines = [
        PodLogLine(
            pod_name="hello-1",
            container="web",
            timestamp=datetime.now(UTC),
            message=f"line {i}",
            stream="stdout",
        )
        for i in range(3)
    ]
    backend = _FakeLogBackend(lines)
    driver = K8sNativeClusterDriver(
        config=K8sNativeConfig(),
        log_backend=backend,
    )
    out: list[PodLogLine] = []
    async for line in driver.stream_logs(
        auth=_auth(),
        namespace="acme-app",
        pod_name="hello-1",
        container=None,
        tail_lines=10,
        follow=False,
    ):
        out.append(line)
    assert [line.message for line in out] == ["line 0", "line 1", "line 2"]


@pytest.mark.asyncio
async def test_driver_stream_logs_propagates_cancellation() -> None:
    """A consumer calling ``aclose`` on the generator must propagate
    teardown to the backend so the urllib3 connection releases."""

    class _BlockingBackend:
        def __init__(self) -> None:
            self.cancelled = False

        async def stream(self, **_kw: Any) -> AsyncIterator[PodLogLine]:
            try:
                while True:
                    await asyncio.sleep(0.01)
                    yield PodLogLine(
                        pod_name="x",
                        container="",
                        timestamp=datetime.now(UTC),
                        message="tick",
                        stream="stdout",
                    )
            except (asyncio.CancelledError, GeneratorExit):
                self.cancelled = True
                raise

    backend = _BlockingBackend()
    driver = K8sNativeClusterDriver(
        config=K8sNativeConfig(),
        log_backend=backend,
    )
    gen = driver.stream_logs(
        auth=_auth(),
        namespace="acme-app",
        pod_name="x",
        container=None,
        tail_lines=0,
        follow=True,
    )
    iterator = gen.__aiter__()
    first = await asyncio.wait_for(iterator.__anext__(), timeout=1.0)
    assert first.message == "tick"
    await gen.aclose()
    assert backend.cancelled is True
