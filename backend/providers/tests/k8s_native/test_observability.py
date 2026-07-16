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
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

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
    LiveLogBackend,
    LivePodBackend,
    _resolve_primary_name,
    _to_container_statuses,
    build_api_client,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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


# ---- enriched container status (#429 / #1062) ---------------------
#
# astrolift-local ships no kubernetes client + no fake apiserver, so we
# can't drive ``LivePodBackend.list_pods`` end-to-end here. Instead we
# exercise the pure mapping leaf (``_to_container_statuses``) with
# recording-double k8s objects (SimpleNamespace mirrors the V1*
# attribute shape the client exposes). Live-cluster validation of the
# full read path is a follow-up. See #1034 for the double pattern.


def _spec_container(name: str, *, requests: dict | None = None, limits: dict | None = None) -> Any:
    return SimpleNamespace(
        name=name,
        resources=SimpleNamespace(requests=requests or {}, limits=limits or {}),
    )


def _running_status(name: str, *, ready: bool = True, image: str = "x", restart_count: int = 0) -> Any:
    return SimpleNamespace(
        name=name,
        ready=ready,
        restart_count=restart_count,
        image=image,
        state=SimpleNamespace(running=object(), waiting=None, terminated=None),
        last_state=None,
    )


def test_to_container_statuses_populates_kind_resources_and_restart_history() -> None:
    """A primary container with resource requests/limits + a restart
    history (lastState terminated + a current bad waiting reason) maps
    to ``kind`` / ``resources`` / ``last_restart_reasons`` /
    ``last_restart_at``. The sidecar's budget stays separate, and the
    init container is classified ``init``."""
    finished = datetime.now(UTC)

    # Primary: running now, but flapped — lastState.terminated=OOMKilled
    # and currently waiting CrashLoopBackOff between restarts.
    web_cs = SimpleNamespace(
        name="web",
        ready=False,
        restart_count=7,
        image="ghcr.io/acme/web:v1",
        state=SimpleNamespace(
            running=None,
            waiting=SimpleNamespace(reason="CrashLoopBackOff"),
            terminated=None,
        ),
        last_state=SimpleNamespace(
            terminated=SimpleNamespace(reason="OOMKilled", finished_at=finished),
        ),
    )
    sidecar_cs = _running_status("istio-proxy")
    init_cs = SimpleNamespace(
        name="migrate",
        ready=True,
        restart_count=0,
        image="ghcr.io/acme/migrate:v1",
        state=SimpleNamespace(
            running=None,
            waiting=None,
            terminated=SimpleNamespace(reason="Completed"),
        ),
        last_state=None,
    )

    spec_main = [
        _spec_container(
            "web",
            requests={"cpu": "200m", "memory": "256Mi"},
            limits={"cpu": "500m", "memory": "512Mi"},
        ),
        _spec_container("istio-proxy", requests={"cpu": "100m", "memory": "128Mi"}),
    ]
    spec_init = [_spec_container("migrate", requests={"cpu": "50m", "memory": "64Mi"})]
    primary_name = _resolve_primary_name(spec_main, workload_slug="web", app_slug="app")
    assert primary_name == "web"

    main = _to_container_statuses(
        [web_cs, sidecar_cs],
        is_init=False,
        spec_lookup={c.name: c for c in spec_main},
        primary_name=primary_name,
    )
    init = _to_container_statuses(
        [init_cs],
        is_init=True,
        spec_lookup={c.name: c for c in spec_init},
        primary_name=primary_name,
    )
    by_name = {c.name: c for c in (*init, *main)}

    assert by_name["migrate"].kind == "init"
    assert by_name["web"].kind == "primary"
    assert by_name["istio-proxy"].kind == "sidecar"

    web = by_name["web"]
    # Restart history: lastState reason first, then the current waiting
    # reason — most-actionable first.
    assert web.last_restart_reasons == ["OOMKilled", "CrashLoopBackOff"]
    assert web.last_restart_at == finished
    assert web.resources.cpu_request == "200m"
    assert web.resources.cpu_limit == "500m"
    assert web.resources.memory_request == "256Mi"
    assert web.resources.memory_limit == "512Mi"

    # Sidecar resources are reported separately so its budget doesn't
    # blur into the primary container's accounting.
    sidecar = by_name["istio-proxy"]
    assert sidecar.resources.cpu_request == "100m"
    assert sidecar.resources.memory_request == "128Mi"
    # A steady-state container (no restarts, no lastState) carries no
    # restart history.
    assert sidecar.last_restart_reasons == []
    assert sidecar.last_restart_at is None


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


# ---- LiveLogBackend: follow=False EOF termination (#1013) ---------


class _FakeLogResp:
    """Stands in for the urllib3 HTTPResponse returned by
    ``read_namespaced_pod_log(_preload_content=False)``.

    ``read()`` returns the whole (finite) body at once — the path the
    follow=False one-shot tail takes. ``readline()`` drains line-by-line
    then returns ``b""`` forever — the follow=True idle signal. Has no
    ``read_chunked`` so ``_read_line_safe`` falls back to ``readline``
    (the AttributeError path)."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)
        self.released = False

    def read(self) -> bytes:
        return b"".join(self._lines)

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""  # EOF — and every subsequent call.

    def release_conn(self) -> None:
        self.released = True


def _patch_live_backend(monkeypatch: Any, resp: _FakeLogResp) -> None:
    """Wire LiveLogBackend.stream onto a fake k8s client whose
    ``read_namespaced_pod_log`` returns ``resp``."""
    pytest.importorskip("kubernetes")
    import k8s_native.observability as obs

    monkeypatch.setattr(obs, "build_api_client", lambda auth: object())

    class _FakeCoreV1:
        def __init__(self, _api_client: Any) -> None:
            pass

        def read_namespaced_pod_log(self, **_kw: Any) -> _FakeLogResp:
            return resp

    from kubernetes import client as k8s_client

    monkeypatch.setattr(k8s_client, "CoreV1Api", _FakeCoreV1)


@pytest.mark.asyncio
async def test_live_backend_follow_false_terminates_on_eof(
    monkeypatch: Any,
) -> None:
    """#1013: a one-shot tail must stop once the body drains. Before the
    fix the empty-read branch slept-and-retried forever, hanging the
    GraphQL worker; now ``follow=False`` breaks on the first empty read."""
    resp = _FakeLogResp(
        [
            b"2024-01-01T00:00:00Z line 0\n",
            b"2024-01-01T00:00:01Z line 1\n",
        ]
    )
    _patch_live_backend(monkeypatch, resp)

    out: list[str] = []

    async def _drain() -> None:
        async for line in LiveLogBackend().stream(
            auth=_auth(),
            namespace="astrolift-agents-acme",
            pod_name="agent-task-1",
            container=None,
            tail_lines=100,
            follow=False,
        ):
            out.append(line.message)

    # Generous bound: pre-fix this never completes and trips the timeout.
    await asyncio.wait_for(_drain(), timeout=2.0)
    assert out == ["line 0", "line 1"]
    assert resp.released is True


@pytest.mark.asyncio
async def test_live_backend_follow_true_keeps_waiting_on_idle(
    monkeypatch: Any,
) -> None:
    """The follow=True (subscription) path must NOT treat an idle empty
    read as EOF — it keeps the stream open for new lines. Verifies the
    fix is scoped to the one-shot path and doesn't regress live tail."""
    resp = _FakeLogResp([])  # always empty: open but idle
    _patch_live_backend(monkeypatch, resp)

    async def _drain_first() -> str:
        async for line in LiveLogBackend().stream(
            auth=_auth(),
            namespace="astrolift-agents-acme",
            pod_name="web-1",
            container=None,
            tail_lines=100,
            follow=True,
        ):
            return line.message
        return "ENDED"

    # follow=True over an idle stream should block, not end — so the
    # wait_for must time out rather than return "ENDED".
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain_first(), timeout=0.4)
