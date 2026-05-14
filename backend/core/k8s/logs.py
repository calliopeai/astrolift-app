"""Log streaming adapter — yield container log lines for a pod.

The default backend uses the kubernetes-client lib's
``read_namespaced_pod_log`` with ``follow=True`` + ``_preload_content=False``,
then iterates the underlying urllib3 response line-by-line. Each
line is yielded as a :class:`LogLine`.

The streaming surface is an *async generator* — the Strawberry
subscription resolver expects ``async for line in stream_logs(...)``.
Bridging the blocking line iterator into asyncio happens in
``_StreamingPodLogBackend`` via ``loop.run_in_executor``.

Tests inject a fake :class:`LogBackend` via :func:`set_log_backend`
to assert backpressure + clean teardown without spinning up a real
cluster.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from core.k8s.client import ClusterClientError, client_for_cluster

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster


@dataclass(frozen=True, slots=True)
class LogLine:
    pod_name: str
    container: str
    timestamp: datetime
    message: str
    stream: str
    """``stdout`` or ``stderr``. The k8s API doesn't separate them on
    the wire — the lib lumps both into one bytestream — so the
    default backend reports ``stdout``. Tests can emit ``stderr``."""


# ---- Backend protocol --------------------------------------------


class LogBackend(Protocol):
    """Stream log lines for a single pod (and optional container).

    Implementations should respect ``asyncio.CancelledError`` for
    fast tear-down when a WS subscriber disconnects."""

    def stream(
        self,
        *,
        cluster: TenantCluster,
        namespace: str,
        pod_name: str,
        container: str | None,
        tail_lines: int,
        follow: bool,
    ) -> AsyncIterator[LogLine]: ...


# ---- Default backend (real kubernetes client) --------------------


class _StreamingPodLogBackend:
    """Bridges the kubernetes-client blocking line iterator into an
    async generator. Each blocking ``readline`` is pushed onto a
    ``loop.run_in_executor`` to keep the event loop responsive.

    Stops on CancelledError, on EOF, and on any urllib3 error
    (the connection dropping from the cluster side is an EOF here)."""

    async def stream(
        self,
        *,
        cluster: TenantCluster,
        namespace: str,
        pod_name: str,
        container: str | None,
        tail_lines: int,
        follow: bool,
    ) -> AsyncIterator[LogLine]:
        try:
            from kubernetes import client as k8s_client
        except ImportError as exc:  # pragma: no cover
            raise ClusterClientError(
                "kubernetes python client is not installed",
            ) from exc

        cc = client_for_cluster(cluster)
        core_v1 = k8s_client.CoreV1Api(cc.api_client)

        loop = asyncio.get_running_loop()

        def _open():
            return core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                follow=follow,
                tail_lines=max(0, min(int(tail_lines), 5000)),
                timestamps=True,
                _preload_content=False,
            )

        resp = await loop.run_in_executor(None, _open)

        try:
            while True:
                # ``read_line`` returns ``b""`` on EOF + a complete
                # line (including \n) otherwise. Run in the executor
                # so we don't pin the event loop.
                chunk = await loop.run_in_executor(None, _read_line_safe, resp)
                if chunk is None:
                    break
                if not chunk:
                    # No bytes yet but stream open — yield to the loop
                    # so other subscribers + the disconnect signal can
                    # progress.
                    await asyncio.sleep(0.05)
                    continue
                try:
                    text = chunk.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:  # noqa: BLE001
                    continue
                ts, message = _split_timestamp(text)
                yield LogLine(
                    pod_name=pod_name,
                    container=container or "",
                    timestamp=ts,
                    message=message,
                    stream="stdout",
                )
        finally:
            # Best-effort release of the urllib3 connection back to
            # the pool. ``release_conn`` is safe on already-closed
            # responses.
            try:
                resp.release_conn()
            except Exception:  # noqa: BLE001
                pass


def _read_line_safe(resp):
    """Return one line or ``None`` on EOF / IO error. Empty bytes
    means the connection is still open but had no data — caller
    will yield to the loop and try again."""
    try:
        line = resp.read_chunked(decode_content=False)
    except (AttributeError, TypeError):
        line = None
    if line is None:
        # ``read_chunked`` isn't the right API on all urllib3
        # versions. Fall back to readline.
        try:
            line = resp.readline()
        except Exception:  # noqa: BLE001
            return None
    if not line:
        return b""
    return line


def _split_timestamp(text: str) -> tuple[datetime, str]:
    """The kubernetes API prefixes each line with an RFC3339 timestamp
    when ``timestamps=True``. Best-effort split — fall back to
    ``utcnow`` when parsing fails."""
    head, sep, tail = text.partition(" ")
    if not sep:
        return datetime.now(UTC), text
    try:
        # Python 3.11+ accepts the trailing Z form via fromisoformat.
        ts = datetime.fromisoformat(head.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC), text
    return ts, tail


# ---- Stub backend (default when k8s lib unavailable / not wired) --


class _StubLogBackend:
    """Emits a single 'log backend not wired' line + ends.

    This mirrors ``exec_ws._StubExecBackend`` — the WS handshake
    still succeeds so the UI renders the right empty state instead
    of bouncing on connect."""

    async def stream(
        self,
        *,
        cluster: TenantCluster,
        namespace: str,
        pod_name: str,
        container: str | None,
        tail_lines: int,
        follow: bool,
    ) -> AsyncIterator[LogLine]:
        yield LogLine(
            pod_name=pod_name,
            container=container or "",
            timestamp=datetime.now(UTC),
            message=(
                "log backend not wired in this build — "
                "connect a real cluster log backend via "
                "core.k8s.logs.set_log_backend at deploy time"
            ),
            stream="stderr",
        )


# Default to the real backend when the k8s lib is importable,
# otherwise the stub. The check is at import time so callers don't
# pay for it on every subscription.
def _default_backend() -> LogBackend:
    try:
        import kubernetes  # noqa: F401
    except ImportError:
        return _StubLogBackend()
    return _StreamingPodLogBackend()


_BACKEND: LogBackend = _default_backend()


def set_log_backend(backend: LogBackend) -> None:
    global _BACKEND
    _BACKEND = backend


def get_log_backend() -> LogBackend:
    return _BACKEND


def reset_log_backend() -> None:
    global _BACKEND
    _BACKEND = _default_backend()


# ---- Public entry -------------------------------------------------


async def stream_app_logs(
    *,
    cluster: TenantCluster,
    namespace: str,
    pod_name: str,
    container: str | None,
    tail_lines: int = 100,
    follow: bool = True,
) -> AsyncIterator[LogLine]:
    """Resolver-facing entry point. Yields log lines until the
    backend signals EOF or the consumer cancels.

    Explicit iterate-and-close: ``async for`` doesn't call
    ``aclose()`` on the underlying iterator when this generator is
    closed via ``aclose()`` from the caller; the inner gen would
    stay alive until GC. We bind the iterator and close it in
    finally so the kubernetes-client urllib3 connection is
    released back to the pool on disconnect."""
    inner = _BACKEND.stream(
        cluster=cluster,
        namespace=namespace,
        pod_name=pod_name,
        container=container,
        tail_lines=tail_lines,
        follow=follow,
    )
    try:
        async for line in inner:
            yield line
    finally:
        aclose = getattr(inner, "aclose", None)
        if aclose is not None:
            await aclose()


def install_log_backend_function(
    fn: Callable[..., AsyncIterator[LogLine]],
) -> None:
    """Install ``fn`` (an async generator function) as the backend."""

    class _FnBackend:
        def stream(self, **kw):
            return fn(**kw)

    set_log_backend(_FnBackend())
