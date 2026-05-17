"""Resolver-facing bridge between the WS exec dispatcher (#280) and
the cluster driver SDK (#423).

The WS dispatcher in :mod:`core.schema.exec_ws` calls into an
:class:`ExecBackend` to open an exec session — the backend's job is
to:

1. Resolve the app slug + workload slug to a :class:`TenantCluster`
   + namespace (mirrors what :mod:`core.cluster_observability` does
   for log streaming).
2. Build a :class:`ClusterAuth` payload for that cluster.
3. Open an :class:`InteractiveExecSession` on the driver.
4. Wire stdin/stdout/stderr/resize/exit between the WS frames and
   the driver session, with clean teardown on cancel.

Test backends swap the live cluster driver via
:func:`set_driver_exec_backend_for_tests` so a fake driver returns a
recording session — same pattern as
``cluster_observability.set_log_backend_for_tests``.

Wiring: :class:`K8sExecBackend` is registered as the WS dispatcher's
backend from ``core.apps.CoreConfig.ready`` so the production stack
ships a real exec path without the operator needing to opt in.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from asgiref.sync import sync_to_async

from core.schema.exec_ws import ExecBackend, ExecSession

logger = logging.getLogger(__name__)


# ---- Test-injectable driver hook ----------------------------------


_DRIVER_OVERRIDE: Any = None


def set_driver_exec_backend_for_tests(driver: Any) -> None:
    """Install a test-only driver that exposes ``interactive_exec(...)``.

    The driver may be any object with an ``interactive_exec`` method
    matching the SDK signature; the production cluster lookup is
    skipped entirely while an override is in place. Reset with
    :func:`reset_driver_exec_backend_for_tests`."""
    global _DRIVER_OVERRIDE
    _DRIVER_OVERRIDE = driver


def reset_driver_exec_backend_for_tests() -> None:
    global _DRIVER_OVERRIDE
    _DRIVER_OVERRIDE = None


# ---- App + cluster + namespace resolution -------------------------


@sync_to_async
def _resolve_target(*, app_slug: str, workload_slug: str | None) -> dict | None:
    """Return the cluster + namespace + workload selector for an exec
    target. Mirrors :func:`astrolift_lifecycle.schema.subscriptions.
    LifecycleSubscription.astrolift_on_app_log._resolve`.

    Returns ``None`` if the app has no cluster wired — the dispatcher
    sends an error frame + exit code 2 so the UI renders the right
    'no runtime' state.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from core.cluster_observability import namespace_for_app
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return None

    app = (
        RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
        .filter(
            slug=app_slug,
            organization_id=org_id,
            deleted_at__isnull=True,
        )
        .first()
    )
    if app is None:
        return None

    cluster = None
    if workload_slug:
        # workload_slug is informational — the pod name encodes the
        # workload — but we use it as a hint to pick the right
        # environment cluster when an app spans more than one.
        env = (
            AppEnvironment.objects.select_related("tenant_cluster")
            .filter(registered_app=app, deleted_at__isnull=True)
            .order_by("created_at")
            .first()
        )
        if env and env.tenant_cluster_id:
            cluster = env.tenant_cluster
    if cluster is None:
        cluster = app.default_tenant_cluster
    if cluster is None or not getattr(cluster, "is_active", True):
        return None

    return {
        "cluster": cluster,
        "namespace": namespace_for_app(app),
    }


def _driver_for(cluster: Any) -> Any:
    """Look up the cluster driver. Tests can swap this entirely via
    :func:`set_driver_exec_backend_for_tests`; the production path
    delegates to :func:`core.cluster_observability._driver_for_cluster`
    so the cluster construction (config dataclasses + plugin lookup)
    stays in one place."""
    if _DRIVER_OVERRIDE is not None:
        return _DRIVER_OVERRIDE
    from core.cluster_observability import _driver_for_cluster

    return _driver_for_cluster(cluster)


def _auth_for(cluster: Any) -> Any:
    from core.cluster_observability import _auth_for_cluster

    return _auth_for_cluster(cluster)


# ---- ExecBackend implementation -----------------------------------


class K8sExecBackend(ExecBackend):
    """Production backend — resolves the target cluster + namespace,
    opens a kubernetes-client exec session via the provider SDK, and
    pipes stdin/stdout/stderr/resize/exit through the WS dispatcher's
    send callbacks.

    The session-side reader tasks run in the background and survive
    until the underlying SDK session signals exit or the WS dispatcher
    calls :meth:`_BackendSession.close`. On close the readers are
    cancelled and the SDK session's ``close()`` runs in a finally
    block so the kubelet socket is released.
    """

    async def open(
        self,
        *,
        app_slug: str,
        workload_slug: str,
        container: str,
        command: list[str],
        send_stdout,
        send_stderr,
        send_exit,
        send_error,
    ) -> ExecSession:
        resolved = await _resolve_target(
            app_slug=app_slug,
            workload_slug=workload_slug,
        )
        if resolved is None:
            await send_stderr(
                "no runtime cluster wired for this app — register a "
                "TenantCluster against the app's environment to enable "
                "interactive exec\n",
            )
            await send_exit(2)
            return _ClosedSession()

        cluster = resolved["cluster"]
        namespace = resolved["namespace"]

        try:
            driver = _driver_for(cluster)
            auth = _auth_for(cluster)
        except Exception as exc:  # noqa: BLE001
            logger.exception("cluster_exec: driver resolution failed")
            await send_stderr(
                f"cluster driver unavailable: {exc}\n",
            )
            await send_exit(2)
            return _ClosedSession()

        loop = asyncio.get_running_loop()

        # Pod-name resolution: ``workload_slug`` from the URL is the
        # logical workload selector. We treat it as the pod-name hint
        # — operators pick a specific pod from the dropdown in the
        # UI, which encodes the pod name in the URL pair. If the
        # workload_slug doesn't resolve to a live pod the kubelet
        # will surface a 404 on the exec channel and the SDK session
        # will exit; we don't try to second-guess that here.
        try:
            sdk_session = await loop.run_in_executor(
                None,
                lambda: driver.interactive_exec(
                    auth=auth,
                    namespace=namespace,
                    pod_name=workload_slug,
                    container=container,
                    command=command or ["sh"],
                    tty=True,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("cluster_exec: open failed")
            await send_stderr(
                f"failed to open exec session: {exc}\n",
            )
            await send_exit(2)
            return _ClosedSession()

        session = _BackendSession(
            sdk_session=sdk_session,
            send_stdout=send_stdout,
            send_stderr=send_stderr,
            send_exit=send_exit,
        )
        session.start_readers()
        return session


class _ClosedSession(ExecSession):
    """Used when the open path bailed before a real session existed —
    keeps the WS dispatcher's stdin/close calls from raising."""

    async def stdin(self, data: str) -> None:
        return None

    async def resize(self, rows: int, cols: int) -> None:
        return None

    async def close(self) -> None:
        return None


class _BackendSession(ExecSession):
    """Bridges WS-side frames to the SDK :class:`InteractiveExecSession`.

    Stdin from the WS pushes straight through. Stdout/stderr from the
    SDK is drained by two background reader tasks that forward each
    chunk to the dispatcher's send callbacks. A third watcher task
    waits on the SDK session's ``wait_exit`` and emits the exit frame
    when the remote command finishes — that's also the signal to tear
    down the readers.
    """

    def __init__(
        self,
        *,
        sdk_session: Any,
        send_stdout,
        send_stderr,
        send_exit,
    ) -> None:
        self._sdk = sdk_session
        self._send_stdout = send_stdout
        self._send_stderr = send_stderr
        self._send_exit = send_exit
        self._tasks: list[asyncio.Task] = []
        self._closed = False

    def start_readers(self) -> None:
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._pump(self._sdk.read_stdout, self._send_stdout)),
            loop.create_task(self._pump(self._sdk.read_stderr, self._send_stderr)),
            loop.create_task(self._exit_watcher()),
        ]

    async def _pump(self, reader, sender) -> None:
        try:
            while not self._closed:
                try:
                    chunk = await reader()
                except StopAsyncIteration:
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("cluster_exec: reader failed")
                    return
                if chunk:
                    try:
                        await sender(chunk)
                    except Exception:  # noqa: BLE001
                        logger.exception("cluster_exec: sender failed")
                        return
        except asyncio.CancelledError:
            return

    async def _exit_watcher(self) -> None:
        try:
            code = await self._sdk.wait_exit()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            logger.exception("cluster_exec: wait_exit failed")
            code = 1
        if not self._closed:
            with contextlib.suppress(Exception):
                await self._send_exit(int(code))

    async def stdin(self, data: str) -> None:
        if self._closed:
            return
        try:
            await self._sdk.write_stdin(data)
        except Exception:  # noqa: BLE001
            logger.exception("cluster_exec: stdin write failed")

    async def resize(self, rows: int, cols: int) -> None:
        if self._closed:
            return
        try:
            await self._sdk.resize(rows=rows, cols=cols)
        except Exception:  # noqa: BLE001
            logger.exception("cluster_exec: resize failed")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self._tasks:
            task.cancel()
        # Drain cancellations so the readers stop before we close the
        # underlying socket. Wrap in suppress because a cancelled task
        # re-raises CancelledError on await.
        for task in self._tasks:
            with contextlib.suppress(BaseException):
                await task
        with contextlib.suppress(Exception):
            await self._sdk.close()
