"""Shared Kubernetes dynamic-client wrapper used by every cluster driver.

Originally extracted from ``aws/cluster_eks.py`` (the reference
implementation in #565). The audit (May 18) found four cloud cluster
drivers + the Vault secrets backend all shipping the same
``NotImplementedError`` shape: a "Real" wrapper whose constructor
builds the auth surface but whose every public method raises. The fix
is to land one cloud-neutral helper here and have each per-cloud
driver wire only the credential / endpoint dance into it.

What lives in this module:

  - ``KubernetesDynamicClient`` — the wrapper. Wraps
    ``kubernetes.dynamic.DynamicClient`` + ``kubernetes.client`` so
    callers get ``server_side_apply / get / delete / get_namespace /
    exec_in_pod / port_forward`` without importing the kubernetes
    Python client at module-level.

  - ``PortForwardHandle`` — the ``PortForwardSession``-shaped return
    of ``port_forward``. Wraps ``kubernetes.stream.ws_client.PortForward``
    so callers can ``close()`` without touching the kubernetes module
    shape.

  - ``NotFoundError`` — the domain exception cluster drivers catch on
    ``client.get(...)`` / ``client.delete(...)`` to classify resources
    that already vanished. Each per-cloud driver re-exports a local
    alias so existing call-site imports keep working after the refactor.

  - ``split_kind`` + ``DEFAULT_API_VERSION_FOR_KIND`` — the bare-kind →
    apiVersion fallback table that lets callers pass ``"Deployment"``
    instead of ``"apps/v1/Deployment"``.

Auth surface
------------
The per-cloud credential dance is intentionally NOT part of this
helper. Each driver constructs an instance via the same three
arguments:

  * ``endpoint`` — the apiserver URL (already https).
  * ``ca_data`` — the CA bundle in one of three forms:
        - base64-encoded PEM (EKS / GKE shape),
        - raw PEM string,
        - empty (skip CA pinning; for kubeconfig auth where the
          ``ApiClient`` is built externally).
  * ``token_provider`` — a zero-arg callable returning a fresh bearer
    token. Called once on construction AND before each top-level
    operation so short-lived cloud-minted tokens (EKS: 15min, AKS:
    ~5min, GKE: 60min) never expire mid-workflow.

For the k8s_native kubeconfig path the driver loads the kubeconfig
externally, hands us the already-built ``ApiClient`` via
``from_api_client``, and the token_provider is a no-op (the kubeconfig
contains the auth directly).
"""

from __future__ import annotations

import base64
import contextlib
from typing import TYPE_CHECKING, Any

from _sdk.cluster import ExecResult

if TYPE_CHECKING:
    from collections.abc import Callable


# ---- Bare-kind → apiVersion fallbacks ------------------------------
#
# Most call sites pass a bare ``kind`` string ("Deployment", "Pod") and
# expect the wrapper to know the right apiVersion. CRDs come through
# as ``group/version/Kind`` (e.g. ``helm.toolkit.fluxcd.io/v2/HelmRelease``)
# so we split on ``/`` and fall back to this table when it's a single
# token. Keep this aligned with the manifests emitted from k8s_native/
# and aws/ — adding a new built-in kind only needs a row here.

DEFAULT_API_VERSION_FOR_KIND: dict[str, str] = {
    "Pod": "v1",
    "Service": "v1",
    "ConfigMap": "v1",
    "Secret": "v1",
    "Namespace": "v1",
    "ServiceAccount": "v1",
    "PersistentVolumeClaim": "v1",
    "PersistentVolume": "v1",
    "Endpoints": "v1",
    "Node": "v1",
    "Deployment": "apps/v1",
    "StatefulSet": "apps/v1",
    "DaemonSet": "apps/v1",
    "ReplicaSet": "apps/v1",
    "Job": "batch/v1",
    "CronJob": "batch/v1",
    "Ingress": "networking.k8s.io/v1",
    "NetworkPolicy": "networking.k8s.io/v1",
    "Role": "rbac.authorization.k8s.io/v1",
    "RoleBinding": "rbac.authorization.k8s.io/v1",
    "ClusterRole": "rbac.authorization.k8s.io/v1",
    "ClusterRoleBinding": "rbac.authorization.k8s.io/v1",
    "HorizontalPodAutoscaler": "autoscaling/v2",
    "PodDisruptionBudget": "policy/v1",
}


def split_kind(kind: str) -> tuple[str, str]:
    """Resolve caller-supplied ``kind`` into ``(api_version, kind)``.

    Accepts either bare ``"Deployment"`` (looked up in the built-in
    table) or qualified ``"group/version/Kind"`` for CRDs. Raising
    KeyError here is intentional — an unknown bare kind is a coding
    error, not a runtime condition the caller can recover from.
    """
    if "/" in kind:
        parts = kind.split("/")
        if len(parts) == 3:
            # group/version/Kind  → apiVersion = group/version
            return f"{parts[0]}/{parts[1]}", parts[2]
        if len(parts) == 2:
            # version/Kind (core group)
            return parts[0], parts[1]
        raise ValueError(f"unrecognized kind path: {kind}")
    api_version = DEFAULT_API_VERSION_FOR_KIND.get(kind)
    if api_version is None:
        raise KeyError(
            f"no default apiVersion for bare kind {kind!r}; pass 'group/version/Kind' for CRDs",
        )
    return api_version, kind


class PreconditionFailedError(Exception):
    """A conditional delete was refused because the object changed.

    The apiserver answers 409 when a delete carries a UID or
    resourceVersion precondition that no longer matches, which means the
    object under this name is not the one the caller validated ownership
    on. It is a concurrency/ownership conflict, never a transient error:
    retrying it unconditionally would delete the replacement, which is
    exactly the race the precondition exists to prevent.
    """


class AlreadyExistsError(Exception):
    """Atomic create refused an occupied name; never retry as an update."""


class NotFoundError(Exception):
    """Raised by the helper when a resource doesn't exist.

    Cluster drivers catch this on ``delete_manifests`` / ``get_namespace``
    paths to classify already-gone resources without raising to the
    caller. Each per-cloud driver re-exports a local alias so existing
    call sites keep working after the refactor.
    """


class PortForwardHandle:
    """Concrete ``PortForwardSession`` returned by ``port_forward``.

    Wraps a ``kubernetes.stream.ws_client.PortForward`` instance so
    callers can ``close()`` the websocket without depending on the
    kubernetes module shape. ``local_port`` and ``remote_port`` mirror
    the first port pair the caller asked to forward — multi-port
    sessions need to inspect ``_pf`` directly via the underlying
    ``socket(remote_port)`` API.
    """

    def __init__(self, *, pf: Any, local_port: int, remote_port: int) -> None:
        self._pf = pf
        self.local_port = local_port
        self.remote_port = remote_port

    def close(self) -> None:
        # The websocket is best-effort — once we're shutting down,
        # a failed close shouldn't mask the original work.
        with contextlib.suppress(Exception):
            self._pf.close()

    def socket(self, port: int) -> Any:
        """Expose the underlying per-port socket for advanced callers."""
        return self._pf.socket(port)


def _decode_ca_data(ca_data: str) -> bytes | None:
    """Normalize the caller-supplied CA blob to PEM bytes.

    EKS / GKE hand us a base64-encoded PEM (the wire shape from
    DescribeCluster); AKS leaves it empty (the admin kubeconfig carries
    the CA in its YAML body); k8s_native passes a raw PEM through.
    Returns ``None`` when no CA is supplied (the caller's ApiClient
    already carries verification from a kubeconfig load).
    """
    if not ca_data:
        return None
    try:
        return base64.b64decode(ca_data, validate=True)
    except Exception:
        # Not base64 — treat as raw PEM. ``encode("utf-8")`` is safe
        # because PEM is strictly ASCII.
        return ca_data.encode("utf-8")


class KubernetesDynamicClient:
    """Cloud-neutral wrapper around kubernetes.dynamic + kubernetes.client.

    Each per-cloud driver constructs one of these per cluster slug and
    holds it for the lifetime of the driver instance. The helper
    builds the underlying ``ApiClient`` lazily — every public method
    re-mints the bearer via ``token_provider`` first so a single
    long-lived workflow can hold this client across activities without
    hitting a mid-flight 401.

    For the kubeconfig path (k8s_native) callers go through
    ``from_api_client``, which skips the bearer plumbing entirely and
    holds a pre-built ``ApiClient`` carrying its own auth.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        ca_data: str,
        token_provider: Callable[[], str],
    ) -> None:
        from kubernetes import client

        cfg = client.Configuration()
        cfg.host = endpoint
        cfg.api_key = {"authorization": f"Bearer {token_provider()}"}
        # Decode the caller-supplied CA into a PEM file the client
        # can read. Kept on disk per kubernetes-client convention.
        ca_bytes = _decode_ca_data(ca_data)
        if ca_bytes is not None:
            import tempfile

            ca_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 — must outlive this scope; client reads lazily
                suffix=".crt",
                delete=False,
            )
            ca_file.write(ca_bytes)
            ca_file.close()
            cfg.ssl_ca_cert = ca_file.name
        else:
            # Honor any caller-side CA pinning via a kubeconfig load —
            # the caller would in that case use ``from_api_client``,
            # but ``ca_data=""`` here means "trust the system bundle"
            # which is what kubernetes.client.Configuration does by
            # default.
            pass
        self._api_client = client.ApiClient(configuration=cfg)
        self._token_provider = token_provider
        self._config = cfg
        self._client_module = client
        self._dynamic: Any = None

    # ---- alternate constructor for kubeconfig-loaded clients ------

    @classmethod
    def from_api_client(
        cls,
        *,
        api_client: Any,
        token_provider: Callable[[], str] | None = None,
    ) -> KubernetesDynamicClient:
        """Build a helper around a pre-loaded ``ApiClient``.

        Used by the k8s_native driver after ``config.load_kube_config``
        / ``config.load_incluster_config`` has populated the ApiClient
        with cert-based or projected-token auth from the kubeconfig.
        We skip the bearer plumbing entirely — the kubeconfig already
        carries the auth — and use a no-op token provider so the
        ``_refresh_token`` hook is well-defined but inert.
        """
        instance = cls.__new__(cls)
        instance._api_client = api_client
        instance._token_provider = token_provider or (lambda: "")
        instance._config = getattr(api_client, "configuration", None)
        from kubernetes import client as _client_module

        instance._client_module = _client_module
        instance._dynamic = None
        return instance

    # ---- internal helpers ----------------------------------------

    def _refresh_token(self) -> None:
        """Re-mint the bearer before each top-level op.

        Cloud-minted tokens are short-lived (EKS: 15min, AKS: ~5min,
        GKE: 60min); long-running workflows that hold a single client
        instance across multiple activities would otherwise 401
        mid-flight. Mutating ``cfg.api_key`` updates the live
        ``ApiClient`` because the kubernetes client reads from the
        shared configuration object on each call.

        Skipped silently when the helper was built via
        ``from_api_client`` with no token provider — the kubeconfig
        load already wired auth.
        """
        if self._config is None:
            return
        token = self._token_provider()
        if not token:
            return
        self._config.api_key = {
            "authorization": f"Bearer {token}",
        }

    def _dyn(self) -> Any:
        """Lazy-build the DynamicClient once and reuse.

        DynamicClient hits ``/apis`` on construction to discover
        resources; we pay that cost once per helper instance instead
        of per call.
        """
        if self._dynamic is None:
            from kubernetes.dynamic import DynamicClient

            self._dynamic = DynamicClient(self._api_client)
        return self._dynamic

    def _resource_for(self, api_version: str, kind: str) -> Any:
        """Resolve a (apiVersion, kind) pair to a dynamic Resource."""
        return self._dyn().resources.get(
            api_version=api_version,
            kind=kind,
        )

    @staticmethod
    def _to_dict(obj: Any) -> dict[str, Any]:
        """Coerce a ``ResourceInstance`` (or already-a-dict) to dict.

        The dynamic client returns ``ResourceInstance`` objects whose
        ``.to_dict()`` walks the attribute tree. Tests inject plain
        dicts; we accept either to keep call-site contracts identical.
        """
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return obj

    # ---- public API ----------------------------------------------

    def create_manifest(
        self,
        *,
        namespace: str | None,
        manifest: dict[str, Any],
        dry_run: bool = False,
    ) -> str:
        """POST exactly once: a concurrent creator must win without being adopted."""
        self._refresh_token()
        from kubernetes.dynamic.exceptions import ConflictError as DynConflict

        api_version = manifest.get("apiVersion", "v1")
        kind = manifest.get("kind")
        name = (manifest.get("metadata") or {}).get("name")
        if not kind or not name:
            raise ValueError("Create-only manifest requires kind and metadata.name")
        resource = self._resource_for(api_version, kind)
        request_namespace = namespace if bool(getattr(resource, "namespaced", namespace is not None)) else None
        kwargs: dict[str, Any] = {"body": manifest, "namespace": request_namespace}
        if dry_run:
            kwargs["dry_run"] = "All"
        try:
            resource.create(**kwargs)
        except DynConflict as exc:
            raise AlreadyExistsError(f"{kind}/{name} already exists; create-only refused adoption") from exc
        return "created"

    def _prepare_hpa_replica_handover(
        self,
        *,
        resource: Any,
        namespace: str | None,
        manifest: dict[str, Any],
        current: Any,
        dry_run: bool,
    ) -> dict[str, Any]:
        observed = self._to_dict(current)
        metadata = observed.get("metadata") or {}
        uid, version = metadata.get("uid"), metadata.get("resourceVersion")
        requested_metadata = manifest.get("metadata") or {}
        for field, observed_value in (("uid", uid), ("resourceVersion", version)):
            if field in requested_metadata and requested_metadata[field] != observed_value:
                raise PreconditionFailedError("HPA deployment caller identity precondition no longer matches")
        replicas = (observed.get("spec") or {}).get("replicas")
        if (
            not isinstance(uid, str)
            or not uid
            or not isinstance(version, str)
            or not version
            or isinstance(replicas, bool)
            or not isinstance(replicas, int)
            or replicas < 0
        ):
            raise ValueError("cannot safely transfer HPA replicas without observed UID, resourceVersion and count")
        fields = metadata.get("managedFields")
        if not isinstance(fields, list):
            raise ValueError("cannot safely transfer HPA replicas without managed field ownership")
        owners = {
            entry.get("manager")
            for entry in fields
            if "f:replicas" in ((entry.get("fieldsV1") or {}).get("f:spec") or {})
        }
        if not owners or any(not isinstance(owner, str) or not owner for owner in owners):
            raise ValueError("cannot safely transfer HPA replicas with unknown field ownership")

        if owners == {"astrolift"}:
            # A second owner prevents omission from defaulting replicas to one before HPA ever writes /scale.
            handover = {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": manifest["metadata"]["name"], "uid": uid, "resourceVersion": version},
                "spec": {"replicas": replicas},
            }
            kwargs: dict[str, Any] = {
                "body": handover,
                "namespace": namespace,
                "field_manager": "astrolift-hpa-handover",
                "force_conflicts": False,
            }
            if dry_run:
                kwargs["dry_run"] = "All"
            transferred = self._to_dict(resource.server_side_apply(**kwargs))
            transferred_meta = transferred.get("metadata") or {}
            transferred_replicas = (transferred.get("spec") or {}).get("replicas")
            transferred_version = transferred_meta.get("resourceVersion")
            transferred_owners = {
                entry.get("manager")
                for entry in transferred_meta.get("managedFields") or []
                if "f:replicas" in ((entry.get("fieldsV1") or {}).get("f:spec") or {})
            }
            if (
                transferred_meta.get("uid") != uid
                or isinstance(transferred_replicas, bool)
                or not isinstance(transferred_replicas, int)
                or transferred_replicas != replicas
                or not isinstance(transferred_version, str)
                or not transferred_version
                or "astrolift-hpa-handover" not in transferred_owners
            ):
                raise ValueError("HPA replica ownership handover was not confirmed; deployment apply refused")
            version = transferred_meta["resourceVersion"]

        # The second apply must not target a replacement or race a later /scale write.
        return {**manifest, "metadata": {**manifest["metadata"], "uid": uid, "resourceVersion": version}}

    def server_side_apply(
        self,
        *,
        namespace: str | None,
        manifest: dict[str, Any],
        dry_run: bool,
        force_conflicts: bool = True,
    ) -> str:
        """Server-side apply via the dynamic client.

        Returns one of ``"created"`` / ``"updated"`` / ``"unchanged"``.
        Strategy:
          1. GET the target object first — a 404 means we're creating.
          2. Apply with ``field_manager="astrolift"`` and
             ``force_conflicts=True`` (the default).
          3. If the pre-apply GET found nothing → ``"created"``.
             Otherwise compare ``metadata.generation`` against the
             pre-apply snapshot — same generation means SSA accepted
             our intent without changing the resource spec
             (``"unchanged"``); a bump means ``"updated"``.

        ``force_conflicts`` defaults to ``True`` because the only
        non-test callers of this helper are the cluster drivers'
        ``apply_manifests``, i.e. the platform reconciling resources it
        owns. The platform is the authoritative field manager for those
        resources: a conflict means another manager (a legacy
        ``OpenAPI-Generator`` apply, or the bootstrap's
        ``astrolift-control-plane`` manager touching a shared label like
        ``astrolift.io/managed-by`` on the ``astrolift-system``
        namespace) has claimed a platform-owned field, and the platform
        must win rather than abort with a 409 ``FieldManagerConflict``.
        This mirrors the bootstrap path in
        :mod:`providers.k8s_native.management`, which already applies its
        platform-owned manifests with ``force_conflicts=True``. Callers
        that genuinely want to defer to an existing owner can pass
        ``force_conflicts=False``.

        HPA-marked Deployments that omit replicas conditionally share a
        solely Astrolift-owned count before relinquishing it. That handover
        never forces ownership, and both writes require the observed UID
        and resourceVersion so newer scale/replacement state wins.

        ``dry_run`` is the bool the SDK callers pass; the kubernetes
        wire takes the literal string ``"All"`` for dry-run.
        """
        self._refresh_token()
        from kubernetes.dynamic.exceptions import NotFoundError as DynNotFound

        api_version = manifest.get("apiVersion", "v1")
        kind = manifest.get("kind")
        if not kind:
            raise ValueError("manifest is missing 'kind'")
        meta = manifest.get("metadata") or {}
        name = meta.get("name")
        if not name:
            raise ValueError(f"manifest for {kind} is missing metadata.name")

        resource = self._resource_for(api_version, kind)
        request_namespace = namespace if bool(getattr(resource, "namespaced", namespace is not None)) else None

        # Snapshot pre-state so we can classify the outcome.
        pre_existed = False
        pre_generation: int | None = None
        current: Any = None
        try:
            current = resource.get(name=name, namespace=request_namespace)
            pre_existed = True
            pre_generation = self._to_dict(current).get("metadata", {}).get("generation")
        except DynNotFound:
            pre_existed = False

        if (
            pre_existed
            and api_version == "apps/v1"
            and kind == "Deployment"
            and (meta.get("annotations") or {}).get("astrolift.dev/replica-owner") == "hpa"
            and "replicas" not in (manifest.get("spec") or {})
        ):
            manifest = self._prepare_hpa_replica_handover(
                resource=resource,
                namespace=request_namespace,
                manifest=manifest,
                current=current,
                dry_run=dry_run,
            )

        apply_kwargs: dict[str, Any] = {
            "body": manifest,
            "namespace": request_namespace,
            "field_manager": "astrolift",
            "force_conflicts": force_conflicts,
        }
        if dry_run:
            apply_kwargs["dry_run"] = "All"

        try:
            applied = resource.server_side_apply(**apply_kwargs)
        except DynNotFound as exc:
            # Cluster-scoped resource the dynamic client refuses to
            # create through SSA — bubble up as our domain NotFound so
            # callers handle it uniformly.
            raise NotFoundError(str(exc)) from exc

        if not pre_existed:
            return "created"

        post_generation = self._to_dict(applied).get("metadata", {}).get("generation")
        # Resources that don't carry generation (ConfigMap, Secret,
        # ServiceAccount) can't distinguish updated vs unchanged via
        # generation. Fall back to resourceVersion comparison.
        if post_generation is not None and pre_generation is not None:
            if post_generation == pre_generation:
                return "unchanged"
            return "updated"
        pre_rv = self._to_dict(current).get("metadata", {}).get("resourceVersion")
        post_rv = self._to_dict(applied).get("metadata", {}).get("resourceVersion")
        if pre_rv is not None and pre_rv == post_rv:
            return "unchanged"
        return "updated"

    def get(
        self,
        *,
        kind: str,
        namespace: str | None,
        name: str,
    ) -> dict[str, Any] | None:
        """Fetch a resource by ``kind``/``namespace``/``name``.

        Returns the resource as a dict. Returns ``None`` if the
        resource doesn't exist (a 404 from the apiserver is the only
        non-error path that yields None — every other failure raises).
        """
        self._refresh_token()
        from kubernetes.dynamic.exceptions import NotFoundError as DynNotFound

        api_version, resolved_kind = split_kind(kind)
        resource = self._resource_for(api_version, resolved_kind)
        request_namespace = namespace if bool(getattr(resource, "namespaced", namespace is not None)) else None
        try:
            obj = resource.get(name=name, namespace=request_namespace)
        except DynNotFound:
            return None
        return self._to_dict(obj)

    def list(
        self,
        *,
        kind: str,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        """List resources of ``kind`` (cluster- or namespace-scoped).

        Returns the ``items`` as dicts (empty list when none). Raises on
        auth / reachability failure like the other reads."""
        self._refresh_token()
        api_version, resolved_kind = split_kind(kind)
        resource = self._resource_for(api_version, resolved_kind)
        request_namespace = namespace if bool(getattr(resource, "namespaced", namespace is not None)) else None
        obj = resource.get(namespace=request_namespace)
        return self._to_dict(obj).get("items", []) or []

    def delete(
        self,
        *,
        kind: str,
        namespace: str | None,
        name: str,
        propagation_policy: str | None = None,
        uid: str | None = None,
        resource_version: str | None = None,
    ) -> bool:
        """Delete a resource by ``kind``/``namespace``/``name``.

        Returns ``True`` if the apiserver accepted the delete,
        ``False`` if the resource was already gone (404 is swallowed
        so callers can drive idempotent teardown loops).

        ``uid`` and ``resource_version`` become ``DeleteOptions.preconditions``
        and close a read/delete race. A caller that validated ownership with a
        GET and then deletes by name alone will delete whatever holds that name
        at delete time, which need not be the object it inspected: names are
        reused, and a reconciler recreating a resource between the two calls is
        an ordinary event rather than an exotic one.

        Passing the UID from the validated GET makes the apiserver refuse in
        that case with 409, raised here as :class:`PreconditionFailedError`.
        ``resource_version`` narrows it further, to "unchanged since I looked",
        which is stricter than most teardowns want but right for a reconciler
        deleting something it just measured.
        """
        self._refresh_token()
        from kubernetes.dynamic.exceptions import ConflictError as DynConflict
        from kubernetes.dynamic.exceptions import NotFoundError as DynNotFound

        api_version, resolved_kind = split_kind(kind)
        resource = self._resource_for(api_version, resolved_kind)
        request_namespace = namespace if bool(getattr(resource, "namespaced", namespace is not None)) else None
        preconditions: dict[str, str] = {}
        if uid:
            preconditions["uid"] = uid
        if resource_version:
            preconditions["resourceVersion"] = resource_version
        try:
            kwargs: dict[str, Any] = {
                "name": name,
                "namespace": request_namespace,
            }
            if propagation_policy or preconditions:
                body: dict[str, Any] = {"apiVersion": "v1", "kind": "DeleteOptions"}
                if propagation_policy:
                    body["propagationPolicy"] = propagation_policy
                if preconditions:
                    body["preconditions"] = preconditions
                kwargs["body"] = body
            resource.delete(**kwargs)
        except DynNotFound:
            return False
        except DynConflict as exc:
            if not preconditions:
                raise
            raise PreconditionFailedError(
                f"{kind}/{name} changed between the ownership read and the delete "
                f"(preconditions {preconditions}); refusing to delete whatever holds "
                f"that name now"
            ) from exc
        return True

    def get_namespace(self, *, name: str) -> dict[str, Any] | None:
        """Fetch a Namespace by name. Returns dict, or ``None`` if 404."""
        self._refresh_token()
        from kubernetes.dynamic.exceptions import NotFoundError as DynNotFound

        resource = self._resource_for("v1", "Namespace")
        try:
            ns = resource.get(name=name)
        except DynNotFound:
            return None
        return self._to_dict(ns)

    def exec_in_pod(
        self,
        *,
        namespace: str,
        pod: str,
        container: str,
        command: list[str],
    ) -> ExecResult:
        """Exec ``command`` inside ``container`` of ``pod``.

        Returns ``ExecResult`` with the captured stdout, stderr, and
        exit code reported by the apiserver's exec channel. Uses
        ``kubernetes.stream.stream`` with ``_preload_content=False``
        so we can read stdout + stderr separately and inspect the
        exit status frame.
        """
        self._refresh_token()
        from kubernetes.stream import stream

        core_v1 = self._client_module.CoreV1Api(self._api_client)
        resp = stream(
            core_v1.connect_get_namespaced_pod_exec,
            pod,
            namespace,
            command=list(command),
            container=container,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        while resp.is_open():
            resp.update(timeout=1)
            if resp.peek_stdout():
                stdout_chunks.append(resp.read_stdout())
            if resp.peek_stderr():
                stderr_chunks.append(resp.read_stderr())

        exit_code = 0
        try:
            err_payload = resp.read_channel(3)
            if err_payload:
                # The error channel carries a v1.Status JSON. Non-zero
                # exit shows up as ``status: "Failure"`` with the exit
                # code in ``details.causes[].message``.
                import json

                parsed = json.loads(err_payload)
                if parsed.get("status") == "Failure":
                    for cause in parsed.get("details", {}).get("causes", []):
                        if cause.get("reason") == "ExitCode":
                            try:
                                exit_code = int(cause.get("message", "1"))
                            except (TypeError, ValueError):
                                exit_code = 1
                            break
                    else:
                        exit_code = 1
        except Exception:
            # The error channel is best-effort; the streamed payload
            # is the authoritative result and we don't want a parse
            # bug to mask an otherwise-successful exec.
            pass
        finally:
            resp.close()

        return ExecResult(
            exit_code=exit_code,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
        )

    def port_forward(
        self,
        *,
        namespace: str,
        pod: str,
        ports: list[tuple[int, int]],
    ) -> PortForwardHandle:
        """Open a port-forward session against ``pod``.

        ``ports`` is a list of ``(local, remote)`` tuples; the
        kubernetes API multiplexes them all on a single websocket.
        Returns a ``PortForwardHandle`` carrying the first pair on
        ``local_port`` / ``remote_port`` plus a ``close()`` for the
        websocket and a ``socket(port)`` accessor for callers that
        need the raw per-port socket.
        """
        self._refresh_token()
        from kubernetes.stream import portforward

        core_v1 = self._client_module.CoreV1Api(self._api_client)
        remote_ports = [remote for (_local, remote) in ports]
        pf = portforward(
            core_v1.connect_get_namespaced_pod_portforward,
            pod,
            namespace,
            ports=",".join(str(p) for p in remote_ports),
        )
        first_local, first_remote = ports[0] if ports else (0, 0)
        return PortForwardHandle(
            pf=pf,
            local_port=first_local,
            remote_port=first_remote,
        )

    # ---- health-surface proxies ------------------------------------
    #
    # _kube_health helpers call these methods on any driver client so
    # the cloud-specific auth bootstrap is irrelevant to the listing
    # logic. CoreV1Api / AppsV1Api are built per-call (cheap — they
    # share the already-open ApiClient) so no extra state is needed.

    def list_namespaced_pod(self, *, namespace: str, **kwargs: Any) -> Any:
        """Proxy to ``CoreV1Api.list_namespaced_pod`` for health queries.

        Accepts the same ``label_selector`` / ``field_selector`` kwargs
        the official client supports and passes them through unchanged.
        Token is refreshed before the call so short-lived cloud tokens
        (EKS: 15 min) don't expire mid-query.
        """
        self._refresh_token()
        core_v1 = self._client_module.CoreV1Api(self._api_client)
        return core_v1.list_namespaced_pod(namespace, **kwargs)

    def read_namespaced_pod_log(self, *, namespace: str, name: str, **kwargs: Any) -> str:
        """Proxy to ``CoreV1Api.read_namespaced_pod_log``.

        Accepts the same ``container`` / ``tail_lines`` / ``previous``
        kwargs the official client supports. Returns the log text; the
        caller decides what to do with a pod that has none yet.
        """
        self._refresh_token()
        core_v1 = self._client_module.CoreV1Api(self._api_client)
        return core_v1.read_namespaced_pod_log(name, namespace, **kwargs)

    def list_namespaced_event(self, *, namespace: str, **kwargs: Any) -> Any:
        """Proxy to ``CoreV1Api.list_namespaced_event`` for event queries."""
        self._refresh_token()
        core_v1 = self._client_module.CoreV1Api(self._api_client)
        return core_v1.list_namespaced_event(namespace, **kwargs)

    def list_namespaced_deployment(self, *, namespace: str, **kwargs: Any) -> Any:
        """Proxy to ``AppsV1Api.list_namespaced_deployment`` for workload health."""
        self._refresh_token()
        apps_v1 = self._client_module.AppsV1Api(self._api_client)
        return apps_v1.list_namespaced_deployment(namespace, **kwargs)

    def list_namespaced_ingress(self, namespace: str, **kwargs: Any) -> Any:
        """Proxy to ``NetworkingV1Api.list_namespaced_ingress``.

        Accepts the same ``label_selector`` / ``field_selector`` kwargs
        the official client supports and passes them through unchanged
        (the ALB-metrics fallback in ``aws/timeseries_cloudwatch.py`` and
        the cluster ingress reconcile in ``core/ingress_reconcile.py``
        both call this). ``namespace`` is positional to match the
        kubernetes-client signature the existing CloudWatch caller uses.
        Token is refreshed first so short-lived cloud tokens (EKS: 15
        min) don't expire mid-query."""
        self._refresh_token()
        net_v1 = self._client_module.NetworkingV1Api(self._api_client)
        return net_v1.list_namespaced_ingress(namespace, **kwargs)

    def merge_patch_ingress(
        self,
        *,
        namespace: str,
        name: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a strategic-merge patch to a namespaced Ingress.

        Used by the cluster ingress reconcile (#851) to add or remove the
        ``alb.ingress.kubernetes.io/auth-*`` annotation keys on the
        managed-subdomain Ingresses when a cluster's ``alb_auth_config``
        changes. A ``null`` annotation value deletes that key (a bare
        ``map[string]string`` has no merge-key directive, so strategic
        merge degrades to JSON-merge semantics for the annotations map).
        Returns the patched object as a dict. Uses the dynamic client's
        ``resource.patch()`` which handles the merge content type
        correctly — same shape as :meth:`merge_patch_deployment`."""
        self._refresh_token()
        resource = self._resource_for("networking.k8s.io/v1", "Ingress")
        result = resource.patch(
            name=name,
            namespace=namespace,
            body=patch,
            content_type="application/strategic-merge-patch+json",
        )
        return self._to_dict(result)

    def merge_patch_deployment(
        self,
        *,
        namespace: str,
        name: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a JSON merge patch to a namespaced Deployment.

        Used for live ops: rolling restart (restart annotation) and
        scale (spec.replicas). Returns the patched object as a dict.
        Uses the dynamic client's resource.patch() which handles the
        merge-patch content type correctly.
        """
        self._refresh_token()
        resource = self._resource_for("apps/v1", "Deployment")
        result = resource.patch(
            name=name,
            namespace=namespace,
            body=patch,
            content_type="application/merge-patch+json",
        )
        return self._to_dict(result)
