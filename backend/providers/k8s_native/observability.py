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
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from _sdk.cluster import (
    ClusterAuth,
    ContainerStatusInfo,
    PodInfo,
    PodLogLine,
)


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
            ca_file = tempfile.NamedTemporaryFile(
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


def _to_container_statuses(raw_statuses: Any) -> list[ContainerStatusInfo]:
    out: list[ContainerStatusInfo] = []
    for cs in raw_statuses or []:
        state = "unknown"
        waiting_reason = ""
        terminated_reason = ""
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
        out.append(
            ContainerStatusInfo(
                name=getattr(cs, "name", "") or "",
                ready=bool(getattr(cs, "ready", False)),
                restart_count=int(getattr(cs, "restart_count", 0) or 0),
                image=getattr(cs, "image", "") or "",
                state=state,
                waiting_reason=waiting_reason,
                terminated_reason=terminated_reason,
            )
        )
    return out


@dataclass(frozen=True)
class LivePodBackend:
    """Default backend — talks to the live kubernetes apiserver."""

    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
    ) -> list[PodInfo]:
        try:
            from kubernetes import client as k8s_client
        except ImportError as exc:  # pragma: no cover
            raise ClusterAuthError(
                "kubernetes python client is not installed",
            ) from exc

        api_client = build_api_client(auth)
        core_v1 = k8s_client.CoreV1Api(api_client)

        label_selector = f"astrolift.dev/app={app_slug}"
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
            statuses = _to_container_statuses(
                getattr(status, "container_statuses", None) if status else None,
            )
            phase = (getattr(status, "phase", "") if status else "") or ""
            ready = bool(statuses) and all(c.ready for c in statuses)
            restarts = sum(c.restart_count for c in statuses)
            out.append(
                PodInfo(
                    name=(getattr(metadata, "name", "") or "") if metadata else "",
                    workload=(_workload_slug_for(metadata, app_slug) if metadata else ""),
                    status=_classify_status(phase, statuses),
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
                    if not follow:
                        # One-shot tail (follow=False): an empty read
                        # means the response body is fully drained
                        # (EOF). No more data is coming on a non-follow
                        # request, so stop — spinning here would hang
                        # the reader forever (see #1013).
                        break
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
