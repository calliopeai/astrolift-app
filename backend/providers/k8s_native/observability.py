"""Pod listing + log streaming for the k8s_native ClusterDriver (#299).

This module owns the kubernetes-client SDK calls that used to live
in the backend's ``core/k8s/{client,pods,logs}.py``. Moving them
here keeps every cloud-specific quirk (IRSA exec-plugin, GKE
Workload Identity, AKS federated creds) bottled up inside the
provider package that owns the cloud — the lifecycle resolvers
stay cloud-agnostic.

Two stages of indirection:

1. ``build_api_client(auth)`` turns a ``ClusterAuth`` payload (the
   shape persisted on ``TenantCluster.auth_method`` +
   ``auth_config``) into an authenticated ``kubernetes.ApiClient``.
   Supports ``kubeconfig`` blobs + ``service_account_token`` bearer
   auth. ``exec_plugin`` is the per-cloud subclass's job — see
   ``aws/cluster_eks.py`` for the EKS STS GetToken example.

2. ``LivePodBackend`` / ``LiveLogBackend`` wrap that ApiClient with
   the pod-list + log-stream operations the resolvers call. Both
   are pluggable on the driver: tests inject deterministic fakes
   without touching module globals.

The kubernetes SDK import is lazy in every public entry point so
unit tests in non-k8s plugins don't pay for the import.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from _sdk.cluster import (
    ClusterAuth,
    ContainerResources,
    ContainerStatusInfo,
    PodInfo,
    PodLogLine,
)

logger = logging.getLogger(__name__)


class ClusterAuthError(Exception):
    """A ``ClusterAuth`` payload can't be turned into a usable
    kubernetes ApiClient. Resolver layer maps this to an empty
    UI state — there's nothing actionable for the operator beyond
    fixing the row, and the platform-event log already carries
    the diagnostic."""


# ---- ApiClient factory --------------------------------------------


def build_api_client(auth: ClusterAuth) -> Any:
    """Turn a runtime auth payload into an authenticated
    ``kubernetes.client.ApiClient``.

    Raises ``ClusterAuthError`` on misconfigured rows. The exception
    type carries the cluster slug in its message so resolver-side
    logs identify the offending row.
    """
    try:
        from kubernetes import client, config
    except ImportError as exc:  # pragma: no cover — packaging guard
        raise ClusterAuthError(
            "kubernetes python client is not installed; install astrolift-providers[k8s] or [aws] / [gcp] / [azure]",
        ) from exc

    method = auth.auth_method
    cfg_blob = auth.auth_config or {}

    if method == "kubeconfig":
        kubeconfig_blob = (cfg_blob.get("kubeconfig") or "").strip()
        if not kubeconfig_blob:
            raise ClusterAuthError(
                f"cluster {auth.slug}: auth_method=kubeconfig but auth_config.kubeconfig is empty",
            )
        import yaml

        try:
            kubeconfig_dict = yaml.safe_load(kubeconfig_blob)
        except yaml.YAMLError as exc:
            raise ClusterAuthError(
                f"cluster {auth.slug}: kubeconfig is not valid YAML",
            ) from exc
        api_cfg = client.Configuration()
        context = cfg_blob.get("context") or None
        try:
            config.load_kube_config_from_dict(
                kubeconfig_dict,
                context=context,
                client_configuration=api_cfg,
            )
        except Exception as exc:
            raise ClusterAuthError(
                f"cluster {auth.slug}: kubeconfig load failed: {exc}",
            ) from exc
        return client.ApiClient(configuration=api_cfg)

    if method == "service_account_token":
        token = (cfg_blob.get("token") or "").strip()
        if not token:
            raise ClusterAuthError(
                f"cluster {auth.slug}: auth_method=service_account_token but auth_config.token is empty",
            )
        endpoint = (auth.endpoint or "").rstrip("/")
        if not endpoint:
            raise ClusterAuthError(
                f"cluster {auth.slug}: endpoint is required for service_account_token auth",
            )
        api_cfg = client.Configuration()
        api_cfg.host = endpoint
        api_cfg.api_key = {"authorization": f"Bearer {token}"}
        ca_cert = (cfg_blob.get("ca_cert") or auth.ca_cert or "").strip()
        if ca_cert:
            # Some operators paste base64'd CAs (kubeconfig format). Be
            # tolerant of both forms — try decoding, fall back to assuming
            # the row already holds PEM.
            if "BEGIN CERTIFICATE" not in ca_cert:
                with contextlib.suppress(Exception):
                    ca_cert = base64.b64decode(ca_cert).decode("utf-8")
            # tempfile must outlive this function — the kubernetes
            # client reads ``ssl_ca_cert`` lazily on first request, so
            # closing here would race the SSL handshake. We rely on
            # the kernel to GC the FD when the process exits.
            ca_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 — must outlive this scope; client reads lazily
                mode="w",
                suffix=".crt",
                delete=False,
            )
            ca_file.write(ca_cert)
            ca_file.flush()
            api_cfg.ssl_ca_cert = ca_file.name
            api_cfg.verify_ssl = True
        else:
            # No CA on the row — verify_ssl off keeps the surface alive.
            # Production rows should always carry the CA.
            api_cfg.verify_ssl = False
        return client.ApiClient(configuration=api_cfg)

    if method == "exec_plugin":
        # exec-plugin auth is the cloud-specific subclass's job — it
        # mints a kubeconfig with the right exec-plugin binary
        # (aws-iam-authenticator, gke-gcloud-auth-plugin, kubelogin)
        # and routes through the kubeconfig branch above. The base
        # k8s_native driver can't satisfy it.
        raise ClusterAuthError(
            f"cluster {auth.slug}: exec_plugin auth requires a "
            "cloud-specific driver (EKS/GKE/AKS); the k8s_native "
            "base driver does not mint tokens",
        )

    raise ClusterAuthError(
        f"cluster {auth.slug}: unknown auth_method {method!r}",
    )


# ---- Pod backend --------------------------------------------------


class PodBackend(Protocol):
    """Lists pods in a namespace for a single app.

    The driver swaps the backend at construction time; tests
    inject deterministic fakes via the driver's ``pod_backend=``
    kwarg without touching module globals.
    """

    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
        task_id: str = "",
        job_name: str = "",
    ) -> list[PodInfo]: ...


def _classify_status(
    pod_phase: str,
    containers: list[ContainerStatusInfo],
) -> str:
    """Roll up the worst-case status across container states.

    The k8s phase alone hides waiting / terminated reasons, which
    is where the actionable info lives (CrashLoopBackOff,
    ImagePullBackOff, ErrImagePull). Mirror what ``kubectl get
    pods`` does: prefer a waiting reason or a non-Completed
    terminal reason over the raw phase when present.
    """
    bad_waiting = {
        "CrashLoopBackOff",
        "ImagePullBackOff",
        "ErrImagePull",
        "CreateContainerConfigError",
        "InvalidImageName",
        "CreateContainerError",
    }
    for c in containers:
        if c.state == "waiting" and c.waiting_reason in bad_waiting:
            return c.waiting_reason
    for c in containers:
        if c.state == "terminated" and c.terminated_reason not in {"", "Completed"}:
            return c.terminated_reason
    return pod_phase or "Unknown"


def _workload_slug_for(metadata: Any, app_slug: str) -> str:
    """Pull the workload slug off the pod's labels, falling back to
    its owner-reference. Empty string when neither can be inferred."""
    labels = getattr(metadata, "labels", None) or {}
    for key in ("astrolift.dev/workload", "astrolift.io/workload", "app.kubernetes.io/component"):
        v = labels.get(key)
        if v:
            return str(v)
    name_label = labels.get("app.kubernetes.io/name")
    if name_label and name_label != app_slug:
        return str(name_label)
    refs = getattr(metadata, "owner_references", None) or []
    for ref in refs:
        if getattr(ref, "controller", False):
            return str(getattr(ref, "name", "") or "")
    return ""


def _resources_for_spec(spec_container: Any) -> ContainerResources:
    """Project a ``V1Container.resources`` payload onto the
    ``ContainerResources`` surface. Falls back to empty strings when
    a key isn't set — the UI renders empty as ``"—"``."""
    if spec_container is None:
        return ContainerResources()
    resources = getattr(spec_container, "resources", None)
    if resources is None:
        return ContainerResources()
    requests = getattr(resources, "requests", None) or {}
    limits = getattr(resources, "limits", None) or {}
    return ContainerResources(
        cpu_request=str(requests.get("cpu", "") or ""),
        cpu_limit=str(limits.get("cpu", "") or ""),
        memory_request=str(requests.get("memory", "") or ""),
        memory_limit=str(limits.get("memory", "") or ""),
    )


def _last_restart_reasons(cs: Any, restart_count: int) -> tuple[list[str], datetime | None]:
    """Pull the most recent restart reason + timestamp.

    The kubernetes API only carries ``lastState`` (the previous
    container instance) — there's no history beyond it. So
    "last 3 reasons" is best-effort: we return the latest reason at
    index 0, and pad with the *current* waiting reason if the
    container is currently in a bad waiting state (so the operator
    sees both ``CrashLoopBackOff`` and the underlying ``OOMKilled``
    that drove it). Ordering is
    ``[last_terminated_reason, current_waiting_reason]`` —
    most-actionable first."""
    reasons: list[str] = []
    last_at: datetime | None = None
    last_state = getattr(cs, "last_state", None)
    if last_state is not None:
        last_term = getattr(last_state, "terminated", None)
        if last_term is not None:
            reason = (getattr(last_term, "reason", "") or "").strip()
            if reason:
                reasons.append(reason)
            finished = getattr(last_term, "finished_at", None)
            if finished is not None:
                last_at = finished
    cur_state = getattr(cs, "state", None)
    if cur_state is not None:
        waiting = getattr(cur_state, "waiting", None)
        if waiting is not None:
            reason = (getattr(waiting, "reason", "") or "").strip()
            if reason and reason not in reasons:
                reasons.append(reason)
    if restart_count == 0 and not last_at:
        return [], None
    return reasons[:3], last_at


def _kind_for_container(
    name: str,
    *,
    is_init: bool,
    primary_name: str,
) -> str:
    if is_init:
        return "init"
    if name and name == primary_name:
        return "primary"
    return "sidecar"


def _resolve_primary_name(
    spec_containers: list[Any],
    *,
    workload_slug: str,
    app_slug: str,
) -> str:
    """Pick the primary container name.

    Preference order:
      1. A container whose name matches the workload slug (the
         manifest renderer names the workload's main container after
         the workload itself).
      2. A container whose name matches the app slug (older renders
         and one-container apps).
      3. The first container in the spec (typical kubectl convention
         puts the primary first).
    """
    names = [getattr(c, "name", "") or "" for c in spec_containers]
    if workload_slug and workload_slug in names:
        return workload_slug
    if app_slug and app_slug in names:
        return app_slug
    return names[0] if names else ""


def _to_container_statuses(
    raw_statuses: Any,
    *,
    is_init: bool = False,
    spec_lookup: dict[str, Any] | None = None,
    primary_name: str = "",
) -> list[ContainerStatusInfo]:
    """Project the k8s container-status payload onto our surface
    dataclasses.

    ``spec_lookup`` is a ``{name: V1Container}`` map built from the
    pod spec — joined on container name to pull per-container resource
    requests/limits + classify init/primary/sidecar without a second
    apiserver call."""
    out: list[ContainerStatusInfo] = []
    lookup = spec_lookup or {}
    for cs in raw_statuses or []:
        state = "unknown"
        waiting_reason = ""
        terminated_reason = ""
        terminated_exit_code: int | None = None
        s = getattr(cs, "state", None)
        if s is not None:
            if getattr(s, "running", None) is not None:
                state = "running"
            elif getattr(s, "waiting", None) is not None:
                state = "waiting"
                waiting_reason = getattr(s.waiting, "reason", "") or ""
            elif getattr(s, "terminated", None) is not None:
                state = "terminated"
                terminated_reason = (
                    getattr(
                        s.terminated,
                        "reason",
                        "",
                    )
                    or ""
                )
                raw_exit = getattr(s.terminated, "exit_code", None)
                if raw_exit is None:
                    raw_exit = getattr(s.terminated, "exitCode", None)
                # Exit 0 is meaningful (a clean Completed), so the guard is
                # against a missing field, not against a falsy one.
                if raw_exit is not None:
                    try:
                        terminated_exit_code = int(raw_exit)
                    except (TypeError, ValueError):
                        terminated_exit_code = None
        name = getattr(cs, "name", "") or ""
        restart_count = int(getattr(cs, "restart_count", 0) or 0)
        reasons, last_restart_at = _last_restart_reasons(cs, restart_count)
        out.append(
            ContainerStatusInfo(
                name=name,
                ready=bool(getattr(cs, "ready", False)),
                restart_count=restart_count,
                image=getattr(cs, "image", "") or "",
                state=state,
                waiting_reason=waiting_reason,
                terminated_reason=terminated_reason,
                kind=_kind_for_container(
                    name,
                    is_init=is_init,
                    primary_name=primary_name,
                ),
                last_restart_reasons=reasons,
                last_restart_at=last_restart_at,
                resources=_resources_for_spec(lookup.get(name)),
                terminated_exit_code=terminated_exit_code,
            )
        )
    return out


def _pod_label_selector(*, app_slug: str, task_id: str = "", job_name: str = "") -> str:
    """Label selector for ``list_pods``.

    Agent task pods carry ``astrolift.dev/task-id=<guid>`` (set by the
    K8s Job spawner) but app workloads carry ``astrolift.dev/app=<slug>``.
    A non-empty ``task_id`` selects the agent pod exactly (#891); otherwise a
    non-empty ``job_name`` selects by ``job-name``, the label the Job
    controller itself stamps on every pod it creates -- the only selector
    that can resolve a Job's real (suffixed) pod name from just the frozen
    Job name, for when the task-id label lookup comes up empty (#1712);
    otherwise fall back to the app-slug selector the app-log surface relies
    on.
    """
    if task_id:
        return f"astrolift.dev/task-id={task_id}"
    if job_name:
        return f"job-name={job_name}"
    return f"astrolift.dev/app={app_slug}"


@dataclass(frozen=True)
class LivePodBackend:
    """Default backend — talks to the live kubernetes apiserver."""

    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
        task_id: str = "",
        job_name: str = "",
    ) -> list[PodInfo]:
        try:
            from kubernetes import client as k8s_client
        except ImportError as exc:  # pragma: no cover
            raise ClusterAuthError(
                "kubernetes python client is not installed",
            ) from exc

        api_client = build_api_client(auth)
        core_v1 = k8s_client.CoreV1Api(api_client)

        label_selector = _pod_label_selector(app_slug=app_slug, task_id=task_id, job_name=job_name)
        resp = core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
            timeout_seconds=10,
        )
        items = list(getattr(resp, "items", []) or [])
        out: list[PodInfo] = []
        for pod in items:
            metadata = getattr(pod, "metadata", None)
            status = getattr(pod, "status", None)
            spec = getattr(pod, "spec", None)
            workload_slug = _workload_slug_for(metadata, app_slug) if metadata else ""

            spec_main = list(getattr(spec, "containers", None) or []) if spec else []
            spec_init = list(getattr(spec, "init_containers", None) or []) if spec else []
            primary_name = _resolve_primary_name(
                spec_main,
                workload_slug=workload_slug,
                app_slug=app_slug,
            )
            spec_main_lookup = {(getattr(c, "name", "") or ""): c for c in spec_main}
            spec_init_lookup = {(getattr(c, "name", "") or ""): c for c in spec_init}

            main_statuses = _to_container_statuses(
                getattr(status, "container_statuses", None) if status else None,
                is_init=False,
                spec_lookup=spec_main_lookup,
                primary_name=primary_name,
            )
            init_statuses = _to_container_statuses(
                getattr(status, "init_container_statuses", None) if status else None,
                is_init=True,
                spec_lookup=spec_init_lookup,
                primary_name=primary_name,
            )
            # Init containers ship in the same status list so the
            # resolver / UI can iterate once. They sort to the front
            # because that's the order kubelet starts them in.
            statuses = init_statuses + main_statuses

            phase = (getattr(status, "phase", "") if status else "") or ""
            # Readiness only considers main containers (init pods are
            # by definition transient and ``ready=false`` once
            # complete). Empty ``main_statuses`` (pod still pending /
            # init-only) reports ``ready=false`` rather than the
            # vacuously-true ``all([]) == True``.
            ready = bool(main_statuses) and all(c.ready for c in main_statuses)
            restarts = sum(c.restart_count for c in main_statuses)
            out.append(
                PodInfo(
                    name=(getattr(metadata, "name", "") or "") if metadata else "",
                    workload=workload_slug,
                    status=_classify_status(phase, main_statuses),
                    phase=phase,
                    ready=ready,
                    restarts=restarts,
                    age=(getattr(metadata, "creation_timestamp", None) if metadata else None),
                    node=((getattr(spec, "node_name", "") or "") if spec else ""),
                    container_statuses=statuses,
                )
            )
        return out


# ---- Log backend --------------------------------------------------


class LogBackend(Protocol):
    """Streams log lines for a single pod/container.

    Implementations MUST respect ``asyncio.CancelledError`` /
    ``GeneratorExit`` so a WS subscriber disconnect tears down
    the underlying urllib3 connection.
    """

    def stream(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        pod_name: str,
        container: str | None,
        tail_lines: int,
        follow: bool,
    ) -> AsyncIterator[PodLogLine]: ...


def _split_timestamp(text: str) -> tuple[datetime, str]:
    """``kubernetes`` prefixes lines with an RFC3339 timestamp when
    ``timestamps=True``. Best-effort split — fall back to ``now``
    when parsing fails so the UI always has a time to render."""
    head, sep, tail = text.partition(" ")
    if not sep:
        return datetime.now(UTC), text
    try:
        ts = datetime.fromisoformat(head.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC), text
    return ts, tail


def _read_line_safe(resp: Any) -> bytes | None:
    """Return one line or ``None`` on EOF / IO error. Empty bytes
    means the connection is still open but had no data — caller
    will yield to the loop and try again."""
    try:
        line = resp.read_chunked(decode_content=False)
    except (AttributeError, TypeError):
        line = None
    if line is None:
        # ``read_chunked`` isn't the right API on all urllib3
        # versions. Fall back to ``readline``.
        try:
            line = resp.readline()
        except Exception:
            return None
    if not line:
        return b""
    return line


class LiveLogBackend:
    """Default backend — bridges the kubernetes-client blocking line
    iterator into an async generator. Each blocking ``readline`` is
    pushed onto ``loop.run_in_executor`` to keep the event loop
    responsive.

    Stops on ``CancelledError``, on EOF, and on any urllib3 error
    (the connection dropping from the cluster side is an EOF here).
    """

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
            from kubernetes import client as k8s_client
        except ImportError as exc:  # pragma: no cover
            raise ClusterAuthError(
                "kubernetes python client is not installed",
            ) from exc

        api_client = build_api_client(auth)
        core_v1 = k8s_client.CoreV1Api(api_client)

        loop = asyncio.get_running_loop()

        def _open() -> Any:
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

        # One-shot tail (follow=False): the response body is finite, so
        # read it whole in a single bounded call and yield split lines.
        # The line-at-a-time loop below is only correct for a live
        # (follow=True) stream — driving a finite response through it
        # spins forever, because urllib3's ``read_chunked`` returns a
        # generator (never the empty-bytes EOF the loop waits for). See
        # #1013.
        if not follow:
            try:
                raw = await loop.run_in_executor(None, resp.read)
            finally:
                with contextlib.suppress(Exception):
                    resp.release_conn()
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
            for line in text.splitlines():
                if not line:
                    continue
                ts, message = _split_timestamp(line)
                yield PodLogLine(
                    pod_name=pod_name,
                    container=container or "",
                    timestamp=ts,
                    message=message,
                    stream="stdout",
                )
            return

        try:
            while True:
                chunk = await loop.run_in_executor(
                    None,
                    _read_line_safe,
                    resp,
                )
                if chunk is None:
                    break
                if not chunk:
                    # follow=True: stream still open but idle — yield to
                    # the loop so other subscribers + the disconnect
                    # signal can progress, then retry.
                    await asyncio.sleep(0.05)
                    continue
                try:
                    text = chunk.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:
                    continue
                ts, message = _split_timestamp(text)
                yield PodLogLine(
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
            with contextlib.suppress(Exception):
                resp.release_conn()


class StubLogBackend:
    """Emits a single 'log backend not wired' line and ends.

    Used when the ``kubernetes`` SDK isn't installed at build time
    (e.g. a slimmed-down image that doesn't ship the [k8s] extra).
    The WS handshake still succeeds so the UI renders the right
    empty state instead of bouncing on connect.
    """

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
        yield PodLogLine(
            pod_name=pod_name,
            container=container or "",
            timestamp=datetime.now(UTC),
            message=(
                "log backend not wired in this build — install "
                "astrolift-providers[k8s] or wire a real LogBackend "
                "via the driver constructor"
            ),
            stream="stderr",
        )


def default_log_backend() -> LogBackend:
    """Return the live backend when the kubernetes SDK is importable,
    otherwise the stub. Check happens once per driver-construction so
    the import cost isn't paid on every subscription."""
    try:
        import kubernetes  # noqa: F401
    except ImportError:
        return StubLogBackend()
    return LiveLogBackend()


# ---- interactive exec (#1040) ---------------------------------------
#
# `kubectl exec -it` equivalent: a bidirectional streaming session over
# the apiserver exec channel. The kubernetes WSClient is blocking, so a
# daemon drain thread pumps stdout/stderr into thread-safe queues and the
# async readers wait on event-loop notifications without occupying workers. This
# is the leaf the exec WS relay drives (core.schema.exec_ws →
# core.cluster_exec.K8sExecBackend); see that module for the frame protocol.

# kubernetes exec channels: 0=stdin 1=stdout 2=stderr 3=error(status) 4=resize.
_EXEC_ERROR_CHANNEL = 3
_EXEC_RESIZE_CHANNEL = 4
_EXEC_STDIN_CHANNEL = 0
# v5 adds one message to v4: a binary frame [255, channel] that half-closes
# that channel, which is how a piped read-to-EOF command gets stdin EOF.
# Data framing is otherwise identical, so offering v5 first is safe.
_V5_PROTOCOL = "v5.channel.k8s.io"
_EXEC_PROTOCOLS = f"{_V5_PROTOCOL},v4.channel.k8s.io"
_V5_CLOSE = 255


class _ExecQueue:
    """Thread producer, async consumers; cancellation never consumes a chunk.

    Sessions are constructed in an executor before an event loop is available,
    so data can arrive before the first reader. Keep the data under a lock and
    use loop events only to wake readers. Idle streams need no executor thread.
    """

    def __init__(self) -> None:
        self._items: deque[Any] = deque()
        self._lock = threading.Lock()
        self._waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    def put(self, value: Any) -> None:
        with self._lock:
            self._items.append(value)
            waiters = tuple(self._waiters)
        for loop, event in waiters:
            with contextlib.suppress(RuntimeError):  # Reader's loop may have closed.
                loop.call_soon_threadsafe(event.set)

    async def get(self) -> Any:
        event = asyncio.Event()
        waiter = (asyncio.get_running_loop(), event)
        with self._lock:
            self._waiters.add(waiter)
        try:
            while True:
                with self._lock:
                    if self._items:
                        return self._items.popleft()
                    event.clear()
                await event.wait()
        finally:
            with self._lock:
                self._waiters.discard(waiter)


class InteractiveExecSession:
    """Async-native handle over a blocking kubernetes ``WSClient`` exec
    stream. Mirrors what ``core.cluster_exec._BackendSession`` consumes:
    ``read_stdout`` / ``read_stderr`` raise ``StopAsyncIteration`` at EOF,
    ``wait_exit`` resolves to the remote exit code, and ``write_stdin`` /
    ``resize`` / ``close`` push control upstream."""

    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self._stdout_q = _ExecQueue()
        self._stderr_q = _ExecQueue()
        self._exit_q = _ExecQueue()
        self._closed = threading.Event()
        self._drain = threading.Thread(target=self._drain_loop, daemon=True)
        self._drain.start()

    def _drain_loop(self) -> None:
        try:
            while self._resp.is_open() and not self._closed.is_set():
                self._resp.update(timeout=1)
                if self._resp.peek_stdout():
                    self._stdout_q.put(self._resp.read_stdout())
                if self._resp.peek_stderr():
                    self._stderr_q.put(self._resp.read_stderr())
        except Exception:
            logger.exception("interactive_exec: drain loop failed")
        finally:
            self._exit_q.put(self._exit_code())
            # Sentinels unblock any reader parked on an empty queue.
            self._stdout_q.put(None)
            self._stderr_q.put(None)

    def _exit_code(self) -> int:
        try:
            payload = self._resp.read_channel(_EXEC_ERROR_CHANNEL)
        except Exception:
            return 0
        if not payload:
            return 0
        try:
            parsed = json.loads(payload)
        except (ValueError, TypeError):
            return 0
        if parsed.get("status") == "Success":
            return 0
        if parsed.get("status") == "Failure":
            for cause in parsed.get("details", {}).get("causes", []) or []:
                if cause.get("reason") == "ExitCode":
                    try:
                        return int(cause.get("message", "1"))
                    except (ValueError, TypeError):
                        return 1
            return 1
        return 0

    async def read_stdout(self) -> str:
        data = await self._stdout_q.get()
        if data is None:
            raise StopAsyncIteration
        return data

    async def read_stderr(self) -> str:
        data = await self._stderr_q.get()
        if data is None:
            raise StopAsyncIteration
        return data

    async def write_stdin(self, data: str) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self._resp.write_stdin, data)

    async def resize(self, *, rows: int, cols: int) -> None:
        payload = json.dumps({"Height": int(rows), "Width": int(cols)})
        await asyncio.get_running_loop().run_in_executor(None, self._resp.write_channel, _EXEC_RESIZE_CHANNEL, payload)

    async def close_stdin(self) -> None:
        """Half-close the remote stdin so a piped read-to-EOF command
        (``cat``, ``psql < script``) finishes (astrolift#145).

        Needs the v5 subprotocol, negotiated in :func:`open_interactive_exec`.
        The close is a raw binary frame: the pinned client (<36, see the EKS
        auth note in providers/pyproject.toml) has no ``close_channel``, and
        its ``write_channel`` would send it as text. Against an API server
        that only speaks v4 there is no half-close, so this stays a no-op.
        """
        if _negotiated_protocol(self._resp) != _V5_PROTOCOL:
            return None

        def _send() -> None:
            from websocket import ABNF

            self._resp.sock.send(bytes([_V5_CLOSE, _EXEC_STDIN_CHANNEL]), opcode=ABNF.OPCODE_BINARY)

        await asyncio.get_running_loop().run_in_executor(None, _send)

    async def wait_exit(self) -> int:
        return int(await self._exit_q.get())

    async def close(self) -> None:
        self._closed.set()
        with contextlib.suppress(Exception):
            await asyncio.get_running_loop().run_in_executor(None, self._resp.close)


def _negotiated_protocol(resp: Any) -> str:
    """The exec subprotocol the API server chose.

    websocket-client records a subprotocol only when it was passed as
    ``subprotocols=``; the kubernetes client sends it as a raw header, so
    the server's choice is read from the handshake response.
    """
    try:
        response = getattr(getattr(resp, "sock", None), "handshake_response", None)
        headers = getattr(response, "headers", None) or {}
        return str(headers.get("sec-websocket-protocol") or "")
    except Exception:  # an unknown socket means no half-close
        return ""


def open_interactive_exec(
    *,
    auth: ClusterAuth,
    namespace: str,
    pod_name: str,
    container: str,
    command: list[str],
    tty: bool = True,
) -> InteractiveExecSession:
    """Open a streaming exec session into ``pod_name`` and return an
    :class:`InteractiveExecSession`. Cloud-neutral — ``build_api_client``
    resolves the per-provider auth (EKS/AKS/GKE/native). Synchronous; the
    caller invokes it via ``run_in_executor`` off the event loop."""
    try:
        from kubernetes import client as k8s_client
        from kubernetes.stream import stream
    except ImportError as exc:  # pragma: no cover
        raise ClusterAuthError("kubernetes python client is not installed") from exc

    api_client = build_api_client(auth)
    # The websocket client takes the subprotocol from the request headers;
    # this client is built for this one exec, so the default header is local.
    api_client.set_default_header("sec-websocket-protocol", _EXEC_PROTOCOLS)
    core_v1 = k8s_client.CoreV1Api(api_client)
    resp = stream(
        core_v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=list(command) or ["sh"],
        container=container or None,
        stderr=True,
        stdin=True,
        stdout=True,
        tty=tty,
        _preload_content=False,
    )
    return InteractiveExecSession(resp)
