"""Internal staged execution; no API admission, reader activation or IAM attachment.

The caller supplies durable fencing/checkpoints, a verified renderer runtime and
registered transports. HTTP fixtures do not prove live collector ingestion.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from _sdk.k8s_dynamic_client import PreconditionFailedError
from aws._cloudwatch_collector import NAMESPACE, PIN, SERVICE_ACCOUNT

if TYPE_CHECKING:
    from _sdk.log_stream import LogQueryDriver
    from aws.cloudwatch_collector import CollectorStage

OWNER = "astrolift.io/collector-operation"
CLUSTER = "astrolift.io/cluster"
PROFILE = "astrolift.io/collector-profile"
KINDS = {"Namespace", "ServiceAccount", "ClusterRole", "ClusterRoleBinding", "ConfigMap", "DaemonSet", "Service"}
CLUSTER_KINDS = {"Namespace", "ClusterRole", "ClusterRoleBinding"}


class ExecutionRefused(Exception):
    """Static refusal only; never carry transport bodies or workload messages."""

    def __init__(self, code: str, *, cleanup_pending: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.cleanup_pending = cleanup_pending


class ReaderGrantPending(Exception):
    """The registered reader adapter has positively identified missing authority."""


class Checkpoints(Protocol):
    def check(self, action: str) -> None:
        """Re-admit original authority/source/run/deadline before every call and idle poll."""

    def load(self) -> dict[str, Any]: ...

    def save(self, state: dict[str, Any]) -> None:
        """Commit atomically under original run fencing before returning."""


def bounded_registered_kubernetes(registered: Any, *, checkpoints: Checkpoints) -> Any:
    """Own a finite-timeout transport cloned from an admitted registered client.

    No global Kubernetes configuration is loaded or changed. Callers close the
    returned client's ApiClient after use. Token refresh still belongs to the
    registered provider; admission must cover that provider's credential calls.
    """
    from kubernetes import client

    from _sdk.k8s_dynamic_client import KubernetesDynamicClient

    if not isinstance(registered, KubernetesDynamicClient):
        raise ExecutionRefused("REGISTERED_KUBERNETES_REQUIRED")
    checkpoints.check("kubernetes.transport")
    config = copy.deepcopy(registered._api_client.configuration)
    if not isinstance(config.host, str) or not config.host.startswith(("https://", "http://")):
        raise ExecutionRefused("REGISTERED_KUBERNETES_REQUIRED")
    config.retries = 0

    class BoundedApiClient(client.ApiClient):
        def call_api(self, resource_path: str, method: str, *args: Any, **kwargs: Any) -> Any:
            checkpoints.check("kubernetes.http." + method)
            kwargs["_request_timeout"] = (5, 20)
            return super().call_api(resource_path, method, *args, **kwargs)

    def refresh() -> str:
        checkpoints.check("kubernetes.refresh")
        return registered._token_provider()

    owned = BoundedApiClient(configuration=config)
    return KubernetesDynamicClient.from_api_client(api_client=owned, token_provider=refresh)


class Renderer(Protocol):
    def __call__(
        self, component: Any, *, archive: bytes, namespace: str, release_name: str
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ExecutionRequest:
    operation_guid: str
    since: str
    until: str
    probe_image: str
    reader_source: str
    max_polls: int = 3
    max_pages: int = 3

    def validate(self) -> None:
        try:
            if str(uuid.UUID(self.operation_guid)) != self.operation_guid or uuid.UUID(self.operation_guid).int == 0:
                raise ValueError
            start = dt.datetime.fromisoformat(self.since.replace("Z", "+00:00"))
            end = dt.datetime.fromisoformat(self.until.replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None or not 0 < (end - start).total_seconds() <= 1800:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ExecutionRefused("INVALID_REQUEST") from None
        if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}", self.probe_image):
            raise ExecutionRefused("APPROVED_PINNED_PROBE_IMAGE_REQUIRED")
        if not self.reader_source or not 1 <= self.max_polls <= 10 or not 1 <= self.max_pages <= 10:
            raise ExecutionRefused("INVALID_REQUEST")


@dataclass(frozen=True)
class ReaderBinding:
    driver: LogQueryDriver
    cluster_guid: str
    log_group: str
    region: str
    source: str


@dataclass(frozen=True)
class ExecutionResult:
    state: str
    coverage: str = "UNKNOWN"
    event_hash: str | None = None
    cleanup_pending: bool = False
    reader_activation_performed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _contains(current: Any, desired: Any) -> bool:
    if isinstance(desired, dict):
        return isinstance(current, dict) and all(
            key in current and _contains(current[key], value) for key, value in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(current, list)
            and len(current) == len(desired)
            and all(_contains(a, b) for a, b in zip(current, desired, strict=True))
        )
    return current == desired


class CollectorExecutor:
    def __init__(
        self,
        *,
        stage: CollectorStage,
        request: ExecutionRequest,
        archive: bytes,
        renderer: Renderer | None,
        kubernetes: Any,
        reader_factory: Any,
        checkpoints: Checkpoints,
        idle: Any,
    ) -> None:
        request.validate()
        if not stage.infrastructure_prepared or stage.collector_installed or stage.ingestion_verified:
            raise ExecutionRefused("PREPARED_STAGE_REQUIRED")
        if hashlib.sha256(archive).hexdigest() != PIN["archiveSha256"]:
            raise ExecutionRefused("CHART_DIGEST_MISMATCH")
        if renderer is None:
            raise ExecutionRefused("VERIFIED_RENDERER_UNAVAILABLE")
        self.stage, self.request, self.reader_factory = stage, request, reader_factory
        self.port, self.idle = checkpoints, idle
        self.state = self.call("checkpoint.load", checkpoints.load)
        expected = _hash({"stage": asdict(stage), "request": asdict(request), "chart": PIN["archiveSha256"]})
        if self.state and self.state.get("source") != expected:
            raise ExecutionRefused("CHECKPOINT_SOURCE_CHANGED")
        if not self.state:
            self.state = {"source": expected, "resources": {}, "probe_deleted": False}
            self.save()
        self.manifests = self.call(
            "chart.render",
            renderer,
            stage.component(),
            archive=archive,
            namespace=NAMESPACE,
            release_name=SERVICE_ACCOUNT,
        )
        self._validate_manifests()
        self.kube = bounded_registered_kubernetes(kubernetes, checkpoints=checkpoints)

    def __enter__(self) -> CollectorExecutor:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.kube._api_client.close()

    def call(self, action: str, function: Any, *args: Any, **kwargs: Any) -> Any:
        self.port.check(action)
        return function(*args, **kwargs)

    def save(self) -> None:
        self.call("checkpoint.save", self.port.save, copy.deepcopy(self.state))

    def _validate_manifests(self) -> None:
        if not isinstance(self.manifests, list) or not 1 <= len(self.manifests) <= 16:
            raise ExecutionRefused("RENDERED_RESOURCES_INVALID")
        seen = set()
        for item in self.manifests:
            kind, meta = item.get("kind"), item.get("metadata", {})
            name = meta.get("name")
            if (
                kind not in KINDS
                or not isinstance(name, str)
                or not name
                or item.get("apiVersion") not in {"v1", "apps/v1", "rbac.authorization.k8s.io/v1"}
            ):
                raise ExecutionRefused("RENDERED_RESOURCES_INVALID")
            if kind == "Namespace" and name != NAMESPACE:
                raise ExecutionRefused("RENDERED_SCOPE_INVALID")
            if kind not in CLUSTER_KINDS and meta.get("namespace", NAMESPACE) != NAMESPACE:
                raise ExecutionRefused("RENDERED_SCOPE_INVALID")
            key = f"{item['apiVersion']}/{kind}/{name}"
            if key in seen:
                raise ExecutionRefused("RENDERED_RESOURCES_INVALID")
            seen.add(key)
        sas = [m for m in self.manifests if m["kind"] == "ServiceAccount"]
        ds = [m for m in self.manifests if m["kind"] == "DaemonSet"]
        if len(sas) != 1 or len(ds) != 1 or sas[0]["metadata"]["name"] != SERVICE_ACCOUNT:
            raise ExecutionRefused("RENDERED_PROFILE_INVALID")
        if (
            sas[0].get("metadata", {}).get("annotations", {}).get("eks.amazonaws.com/role-arn")
            != self.stage.irsa_role_arn
        ):
            raise ExecutionRefused("RENDERED_IRSA_INVALID")
        template = ds[0].get("spec", {}).get("template", {}).get("spec", {})
        if template.get("serviceAccountName") != SERVICE_ACCOUNT or template.get("nodeSelector") != {
            "kubernetes.io/os": "linux"
        }:
            raise ExecutionRefused("RENDERED_PROFILE_INVALID")
        images = [c.get("image") for c in template.get("containers", [])]
        if images != ["cr.fluentbit.io/fluent/fluent-bit:5.1.2"]:
            raise ExecutionRefused("RENDERED_IMAGE_INVALID")
        profile_hash = _hash(self.manifests)
        old_hash = self.state.get("rendered_hash")
        if old_hash and old_hash != profile_hash:
            raise ExecutionRefused("RENDERED_PROFILE_CHANGED")
        self.state["rendered_hash"] = profile_hash
        self.save()
        for manifest in self.manifests:
            manifest.setdefault("metadata", {}).setdefault("annotations", {}).update(
                {OWNER: self.request.operation_guid, CLUSTER: self.stage.cluster_guid, PROFILE: profile_hash}
            )
        self.profile_hash = profile_hash

    def _identity(self, obj: dict[str, Any]) -> dict[str, str]:
        meta = obj.get("metadata", {})
        if not all(isinstance(meta.get(k), str) and meta[k] for k in ("uid", "resourceVersion")):
            raise ExecutionRefused("RESOURCE_IDENTITY_UNAVAILABLE")
        return {k: meta[k] for k in ("uid", "resourceVersion")}

    def _get(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        return self.call(
            "kubernetes.get",
            self.kube.get,
            kind=f"{manifest['apiVersion']}/{manifest['kind']}",
            namespace=None if manifest["kind"] in CLUSTER_KINDS else NAMESPACE,
            name=manifest["metadata"]["name"],
        )

    def _install(self, manifest: dict[str, Any]) -> dict[str, Any]:
        key = f"{manifest['apiVersion']}/{manifest['kind']}/{manifest['metadata']['name']}"
        record = self.state["resources"].get(key)
        current = self._get(manifest)
        if current is not None and current.get("metadata", {}).get("deletionTimestamp"):
            raise ExecutionRefused("RESOURCE_TERMINATING")
        if current is not None and manifest["kind"] == "Namespace" and record is None:
            self.state["resources"][key] = {**self._identity(current), "observed_only": True}
            self.save()
            return current
        if (
            record
            and record.get("uid")
            and (current is None or current.get("metadata", {}).get("uid") != record["uid"])
        ):
            raise ExecutionRefused("RECORDED_RESOURCE_REPLACED")
        if current is not None:
            if record and record.get("observed_only"):
                return current
            annotations = current.get("metadata", {}).get("annotations", {})
            if (
                annotations.get(OWNER) != self.request.operation_guid
                or annotations.get(CLUSTER) != self.stage.cluster_guid
                or not _contains(current, manifest)
            ):
                raise ExecutionRefused("FOREIGN_OR_CHANGED_RESOURCE")
            if not record:
                raise ExecutionRefused("UNRECORDED_RESOURCE")
        else:
            self.state["resources"][key] = {"intent": True}
            self.save()
            self.call(
                "kubernetes.create",
                self.kube.create_manifest,
                namespace=None if manifest["kind"] in CLUSTER_KINDS else NAMESPACE,
                manifest=manifest,
            )
            current = self._get(manifest)
            if current is None or not _contains(current, manifest):
                raise ExecutionRefused("RESOURCE_CREATE_UNCONFIRMED")
        self.state["resources"][key] = self._identity(current)
        self.save()
        return current

    def _coverage(self) -> tuple[list[dict[str, Any]], str]:
        nodes = self.call("kubernetes.nodes", self.kube.list, kind="v1/Node", namespace=None)
        eligible, unsupported = [], False
        for node in nodes:
            labels = node.get("metadata", {}).get("labels", {})
            if labels.get("eks.amazonaws.com/compute-type") == "fargate" or labels.get("kubernetes.io/os") != "linux":
                unsupported = True
            elif labels.get("eks.amazonaws.com/compute-type") in {None, "ec2"} and re.fullmatch(
                rf"aws:///{re.escape(self.stage.region)}[-a-z0-9]*/i-(?:[0-9a-f]{{8}}|[0-9a-f]{{17}})",
                node.get("spec", {}).get("providerID", ""),
            ):
                eligible.append(node)
            else:
                unsupported = True
        if not eligible:
            raise ExecutionRefused("UNSUPPORTED_NODE_COVERAGE")
        return eligible, "EC2_ONLY_MIXED" if unsupported else "LINUX_EC2"

    def _ready(self, nodes: list[dict[str, Any]]) -> bool:
        for manifest in self.manifests:
            if manifest["kind"] in {"DaemonSet", "ServiceAccount", "ConfigMap"}:
                current = self._get(manifest)
                key = f"{manifest['apiVersion']}/{manifest['kind']}/{manifest['metadata']['name']}"
                record = self.state["resources"][key]
                if (
                    current is None
                    or current.get("metadata", {}).get("uid") != record["uid"]
                    or not _contains(current, manifest)
                ):
                    raise ExecutionRefused("COLLECTOR_CHANGED")
                if manifest["kind"] == "DaemonSet":
                    status, meta = current.get("status", {}), current.get("metadata", {})
                    generation = meta.get("generation")
                    if (
                        type(generation) is not int
                        or type(status.get("observedGeneration")) is not int
                        or status.get("observedGeneration") != generation
                    ):
                        return False
                    if (
                        any(
                            type(status.get(k)) is not int or status.get(k) != len(nodes)
                            for k in (
                                "desiredNumberScheduled",
                                "numberReady",
                                "updatedNumberScheduled",
                                "numberAvailable",
                            )
                        )
                        or status.get("numberUnavailable", 0) != 0
                    ):
                        return False
        return True

    def _probe(self, node: str) -> dict[str, Any]:
        slug = "collector-probe-" + self.request.operation_guid.replace("-", "")
        marker = "astrolift-collector-proof-" + hashlib.sha256(self.request.operation_guid.encode()).hexdigest()
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": slug,
                "namespace": NAMESPACE,
                "labels": {"astrolift.io/app": slug, "astrolift.io/workload": "collector-probe"},
                "annotations": {
                    OWNER: self.request.operation_guid,
                    CLUSTER: self.stage.cluster_guid,
                    PROFILE: self.profile_hash,
                },
            },
            "spec": {
                "nodeName": node,
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "activeDeadlineSeconds": 60,
                "terminationGracePeriodSeconds": 1,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 65534,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "probe",
                        "image": self.request.probe_image,
                        "command": ["/bin/sh", "-c", "printf '%s\\n' \"$PROBE_MARKER\""],
                        "env": [{"name": "PROBE_MARKER", "value": marker}],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "resources": {
                            "requests": {"cpu": "1m", "memory": "8Mi"},
                            "limits": {"cpu": "10m", "memory": "16Mi"},
                        },
                    }
                ],
            },
        }

    def _read(self, binding: ReaderBinding, probe: dict[str, Any]) -> str | None:
        slug = probe["metadata"]["name"]
        marker = probe["spec"]["containers"][0]["env"][0]["value"]
        cursor, seen = "", set()
        for _ in range(self.request.max_pages):
            page = self.call(
                "reader.query",
                binding.driver.query_logs,
                f'{{namespace="{NAMESPACE}",app="{slug}",workload="collector-probe"}}',
                self.request.since,
                self.request.until,
                limit=100,
                cursor=cursor,
                level=None,
                search=marker,
            )
            for line in page.items:
                labels = line.labels or {}
                if (
                    line.pod == slug
                    and line.container == "probe"
                    and line.namespace == NAMESPACE
                    and labels.get("astrolift.io/app") == slug
                    and labels.get("astrolift.io/workload") == "collector-probe"
                    and marker in line.message
                ):
                    digest = _hash([line.timestamp, line.pod, line.container, line.message])
                    if not self.state.get("event_hash") or digest == self.state["event_hash"]:
                        return digest
            if not page.next_cursor or page.next_cursor in seen:
                break
            seen.add(page.next_cursor)
            cursor = page.next_cursor
        return None

    def run(self) -> ExecutionResult:
        cleanup = any(key.startswith("v1/Pod/") for key in self.state["resources"]) and not self.state["probe_deleted"]
        coverage = "UNKNOWN"
        try:
            nodes, coverage = self._coverage()
            namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}}
            if self._get(namespace) is None and not any(m["kind"] == "Namespace" for m in self.manifests):
                raise ExecutionRefused("COLLECTOR_NAMESPACE_UNAVAILABLE")
            if self._get(namespace) is not None:
                self._install(namespace)
            for manifest in self.manifests:
                self._install(manifest)
            for attempt in range(self.request.max_polls):
                if self._ready(nodes):
                    break
                if attempt + 1 == self.request.max_polls:
                    return ExecutionResult("READINESS_PENDING", coverage, cleanup_pending=cleanup)
                self.call("idle.readiness", self.idle)
                nodes, coverage = self._coverage()
            binding = self.call("reader.construct", self.reader_factory, self.stage)
            if not isinstance(binding, ReaderBinding) or (
                binding.cluster_guid,
                binding.log_group,
                binding.region,
                binding.source,
            ) != (self.stage.cluster_guid, self.stage.log_group, self.stage.region, self.request.reader_source):
                raise ExecutionRefused("REGISTERED_READER_IDENTITY_MISMATCH")
            probe_node = self.state.get("probe_node")
            if not probe_node:
                ready = [
                    n
                    for n in nodes
                    if not n.get("spec", {}).get("unschedulable")
                    and any(
                        c.get("type") == "Ready" and c.get("status") == "True"
                        for c in n.get("status", {}).get("conditions", [])
                    )
                ]
                if not ready:
                    raise ExecutionRefused("PROBE_NODE_UNAVAILABLE")
                probe_node = ready[0]["metadata"]["name"]
                self.state["probe_node"] = probe_node
                self.state["probe_node_uid"] = self._identity(ready[0])["uid"]
                self.save()
            if not any(
                n.get("metadata", {}).get("name") == probe_node
                and n.get("metadata", {}).get("uid") == self.state.get("probe_node_uid")
                for n in nodes
            ):
                raise ExecutionRefused("PROBE_NODE_CHANGED")
            probe = self._probe(probe_node)
            if not self.state["probe_deleted"]:
                if not self.state.get("delete_intent"):
                    self._read(binding, probe)
                    cleanup = True
                    self._install(probe)
                for attempt in range(0 if self.state.get("delete_intent") else self.request.max_polls):
                    digest = self._read(binding, probe)
                    if digest:
                        self.state["event_hash"] = digest
                        self.save()
                        break
                    if attempt + 1 == self.request.max_polls:
                        return ExecutionResult("INGESTION_PENDING", coverage, cleanup_pending=True)
                    self.call("idle.ingestion", self.idle)
                if self.state.get("delete_refused"):
                    raise ExecutionRefused("PROBE_DELETE_PRECONDITION_FAILED")
                current = self._get(probe)
                key = f"v1/Pod/{probe['metadata']['name']}"
                record = self.state["resources"][key]
                if current is not None:
                    if current.get("metadata", {}).get("uid") != record["uid"] or not _contains(current, probe):
                        raise ExecutionRefused("PROBE_REPLACED")
                    identity = self.state.get("delete_intent") or self._identity(current)
                    self.state["delete_intent"] = identity
                    self.save()
                    self.call(
                        "kubernetes.delete_probe",
                        self.kube.delete,
                        kind="v1/Pod",
                        namespace=NAMESPACE,
                        name=probe["metadata"]["name"],
                        uid=identity["uid"],
                        resource_version=identity["resourceVersion"],
                    )
                elif not self.state.get("delete_intent"):
                    raise ExecutionRefused("PROBE_DISAPPEARED_BEFORE_DELETE")
                remaining = self._get(probe)
                if remaining is not None:
                    if remaining.get("metadata", {}).get("uid") != record["uid"]:
                        raise ExecutionRefused("PROBE_REPLACED")
                    return ExecutionResult("PROBE_DELETION_PENDING", coverage, cleanup_pending=True)
                self.state["probe_deleted"] = True
                self.save()
                cleanup = False
            for attempt in range(self.request.max_polls):
                digest = self._read(binding, probe)
                if digest and digest == self.state.get("event_hash"):
                    nodes, coverage = self._coverage()
                    if not self._ready(nodes):
                        return ExecutionResult("READINESS_PENDING", coverage, cleanup_pending=cleanup)
                    return ExecutionResult("POST_LOSS_READ_VERIFIED", coverage, event_hash=digest)
                if attempt + 1 < self.request.max_polls:
                    self.call("idle.post_loss", self.idle)
            return ExecutionResult("POST_LOSS_READ_PENDING", coverage)
        except PreconditionFailedError:
            self.state["delete_refused"] = True
            self.save()
            raise ExecutionRefused("PROBE_DELETE_PRECONDITION_FAILED", cleanup_pending=True) from None
        except ReaderGrantPending:
            return ExecutionResult("READER_GRANT_PENDING", coverage, cleanup_pending=cleanup)
        except ExecutionRefused as exc:
            exc.cleanup_pending = cleanup
            raise
        except Exception:
            return ExecutionResult("PROVIDER_OUTCOME_UNCERTAIN", coverage, cleanup_pending=cleanup)
