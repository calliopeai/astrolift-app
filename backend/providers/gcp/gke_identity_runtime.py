"""Read-only current GKE workload observations; never token exchange or IAM effects."""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from gcp.gke_identity_observation import (
    _NAME as _POOL_NAME,
)
from gcp.gke_identity_observation import (
    MAX_POOLS,
    GKEIdentityObserver,
    GKEObservationContext,
    GKEObservationError,
    KubernetesReadAdapter,
    _checkpoint,
    _http,
)
from gcp.identity_owned import MAX_BYTES, _guid, _hash

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_ITEMS = 256
MAX_WORKLOADS = 16
MAX_READS = 512
DEADLINE_SECONDS = 60
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
_KEY = re.compile(r"(?:[a-z0-9.-]{1,253}/)?[A-Za-z0-9_.-]{1,63}\Z")
_VALUE = re.compile(r"[A-Za-z0-9_.-]{0,63}\Z")
_VERSION = re.compile(r"[!-~]{1,256}\Z")
_ROUTES = {
    "Deployment": ("apps/v1", "deployments"),
    "StatefulSet": ("apps/v1", "statefulsets"),
    "DaemonSet": ("apps/v1", "daemonsets"),
    "ReplicaSet": ("apps/v1", "replicasets"),
    "Job": ("batch/v1", "jobs"),
    "CronJob": ("batch/v1", "cronjobs"),
}


@dataclass(frozen=True)
class WorkloadTarget:
    namespace: str
    kind: str
    name: str
    uid: str
    service_account_name: str
    generation: int
    selector_json: str
    template_sha256: str
    placement_sha256: str
    desired_count: int
    allowed_node_pools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _ROUTES or any(
            not isinstance(v, str) or not _NAME.fullmatch(v)
            for v in (self.namespace, self.name, self.service_account_name)
        ):
            raise GKEObservationError("INVALID_WORKLOAD_TARGET")
        _guid(self.uid)
        if (
            type(self.generation) is not int
            or self.generation < 1
            or type(self.desired_count) is not int
            or not 1 <= self.desired_count <= MAX_ITEMS
        ):
            raise GKEObservationError("INVALID_WORKLOAD_TARGET")
        for value in (self.template_sha256, self.placement_sha256):
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise GKEObservationError("INVALID_WORKLOAD_TARGET")
        if (
            not isinstance(self.allowed_node_pools, tuple)
            or len(self.allowed_node_pools) > 64
            or len(set(self.allowed_node_pools)) != len(self.allowed_node_pools)
            or any(not isinstance(v, str) or not _NAME.fullmatch(v) for v in self.allowed_node_pools)
        ):
            raise GKEObservationError("INVALID_PLACEMENT_CEILING")
        _selector(self.selector_json)


@dataclass(frozen=True)
class WorkloadObservation:
    kind: str
    name: str
    uid: str
    generation: int
    configuration_observed: bool
    generation_observed: bool
    workload_ready: bool
    execution_observed: bool
    reason: str
    ready_pod_uids: tuple[str, ...] = ()
    node_uids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeObservation:
    configuration_observed: bool
    workload_ready: bool
    reason: str
    workloads: tuple[WorkloadObservation, ...] = ()
    impersonation_verified: bool = False
    inference_verified: bool = False


def placement_sha256(spec: dict[str, Any]) -> str:
    """Fingerprint existing constraints; never add a selector or remove an affinity."""
    keys = (
        "nodeSelector",
        "affinity",
        "tolerations",
        "topologySpreadConstraints",
        "schedulerName",
        "runtimeClassName",
        "hostNetwork",
        "nodeName",
    )
    return _hash({key: spec[key] for key in keys if key in spec})


def template_sha256(template: dict[str, Any]) -> str:
    return _hash(template)


def _labels(value: Any) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or len(value) > 64
        or any(
            not isinstance(k, str) or not _KEY.fullmatch(k) or not isinstance(v, str) or not _VALUE.fullmatch(v)
            for k, v in value.items()
        )
    ):
        raise GKEObservationError("LABELS_UNVERIFIED")
    return value


def _requirements(value: Any, *, fields: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 32:
        raise GKEObservationError("PREDICATE_UNSUPPORTED")
    for requirement in value:
        if not isinstance(requirement, dict) or set(requirement) - {"key", "operator", "values"}:
            raise GKEObservationError("PREDICATE_UNSUPPORTED")
        key, op, vals = requirement.get("key"), requirement.get("operator"), requirement.get("values", [])
        if (
            not isinstance(key, str)
            or not _KEY.fullmatch(key)
            or (fields and key != "metadata.name")
            or not isinstance(vals, list)
            or len(vals) > 64
            or any(not isinstance(v, str) or not _VALUE.fullmatch(v) for v in vals)
        ):
            raise GKEObservationError("PREDICATE_UNSUPPORTED")
        if (
            op not in ("In", "NotIn", "Exists", "DoesNotExist", "Gt", "Lt")
            or (op in ("In", "NotIn") and not vals)
            or (op in ("Exists", "DoesNotExist") and vals)
            or (op in ("Gt", "Lt") and (len(vals) != 1 or not re.fullmatch(r"-?[0-9]{1,19}", vals[0])))
        ):
            raise GKEObservationError("PREDICATE_UNSUPPORTED")
    return value


def _matches(requirements: list[dict[str, Any]], labels: dict[str, str]) -> bool:
    for item in requirements:
        key, op, values = item["key"], item["operator"], item.get("values", [])
        found = labels.get(key)
        if (
            (op == "In" and found not in values)
            or (op == "NotIn" and found in values)
            or (op == "Exists" and found is None)
            or (op == "DoesNotExist" and found is not None)
        ):
            return False
        if op in ("Gt", "Lt"):
            if found is None or not re.fullmatch(r"-?[0-9]{1,19}", found):
                return False
            if not (int(found) > int(values[0]) if op == "Gt" else int(found) < int(values[0])):
                return False
    return True


def _selector(value: str) -> dict[str, Any]:
    if not isinstance(value, str) or len(value) > 16384:
        raise GKEObservationError("SELECTOR_UNSUPPORTED")
    try:
        parsed = json.loads(value)
    except Exception:
        raise GKEObservationError("SELECTOR_UNSUPPORTED") from None
    if not isinstance(parsed, dict) or set(parsed) - {"matchLabels", "matchExpressions"}:
        raise GKEObservationError("SELECTOR_UNSUPPORTED")
    _labels(parsed.get("matchLabels", {}))
    requirements = _requirements(parsed.get("matchExpressions", []))
    if (not parsed.get("matchLabels") and not requirements) or any(x["operator"] in ("Gt", "Lt") for x in requirements):
        raise GKEObservationError("SELECTOR_UNSUPPORTED")
    return parsed


def _select(selector: dict[str, Any], labels: dict[str, str]) -> bool:
    return all(labels.get(k) == v for k, v in selector.get("matchLabels", {}).items()) and _matches(
        selector.get("matchExpressions", []), labels
    )


def _metadata(
    value: dict[str, Any], kind: str, namespace: str | None, *, name: str | None = None, uid: str | None = None
) -> dict[str, Any]:
    metadata = value.get("metadata")
    if (
        value.get("kind") != kind
        or value.get("apiVersion") != (_ROUTES[kind][0] if kind in _ROUTES else "v1")
        or not isinstance(metadata, dict)
    ):
        raise GKEObservationError("OBJECT_UNVERIFIED")
    if (
        not isinstance(metadata.get("name"), str)
        or not _NAME.fullmatch(metadata["name"])
        or metadata.get("namespace") != namespace
        or metadata.get("deletionTimestamp")
        or (name is not None and metadata["name"] != name)
        or (uid is not None and metadata.get("uid") != uid)
    ):
        raise GKEObservationError("OBJECT_IDENTITY_CHANGED")
    try:
        uid_value = metadata.get("uid")
        if not isinstance(uid_value, str):
            raise ValueError
        _guid(uid_value)
    except ValueError:
        raise GKEObservationError("OBJECT_UNVERIFIED") from None
    rv = metadata.get("resourceVersion")
    if not isinstance(rv, str) or not _VERSION.fullmatch(rv):
        raise GKEObservationError("OBJECT_UNVERIFIED")
    _labels(metadata.get("labels", {}))
    return metadata


def _owner(metadata: dict[str, Any], kind: str, uid: str, name: str) -> bool:
    owners = metadata.get("ownerReferences", [])
    if not isinstance(owners, list) or len(owners) > 8:
        raise GKEObservationError("OWNER_UNVERIFIED")
    controllers = [x for x in owners if isinstance(x, dict) and x.get("controller") is True]
    return len(controllers) == 1 and all(
        controllers[0].get(k) == v
        for k, v in {"apiVersion": _ROUTES[kind][0], "kind": kind, "uid": uid, "name": name}.items()
    )


def _integer(value: Any, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > MAX_ITEMS:
        raise GKEObservationError("STATUS_UNVERIFIED")
    return value


def _completed_job(value: dict[str, Any]) -> bool:
    status, spec = value.get("status", {}), value.get("spec", {})
    conditions = status.get("conditions", [])
    if (
        not isinstance(conditions, list)
        or len(conditions) > 32
        or any(not isinstance(x, dict) for x in conditions)
        or len({x.get("type") for x in conditions}) != len(conditions)
    ):
        raise GKEObservationError("JOB_STATUS_UNVERIFIED")
    return (
        _integer(status.get("succeeded", 0)) >= _integer(spec.get("completions", 1), positive=True)
        and any(x.get("type") == "Complete" and x.get("status") == "True" for x in conditions)
        and not any(x.get("type") == "Failed" and x.get("status") == "True" for x in conditions)
    )


def _pod_template_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    # Only the API server's bounded automatic service-account volume is additional.
    actual = copy.deepcopy(actual)
    expected_volumes = expected.get("volumes", [])
    actual_volumes = actual.get("volumes", [])
    if not isinstance(expected_volumes, list) or not isinstance(actual_volumes, list) or len(actual_volumes) > 128:
        return False
    original_names = {item.get("name") for item in expected_volumes if isinstance(item, dict)}
    automatic: set[str] = set()
    retained = []
    for item in actual_volumes:
        if not isinstance(item, dict):
            return False
        name = item.get("name", "")
        if name not in original_names and isinstance(name, str) and re.fullmatch(r"kube-api-access-[a-z0-9]{5}", name):
            projected = item.get("projected", {})
            sources = projected.get("sources") if isinstance(projected, dict) else None
            if (
                not isinstance(sources, list)
                or len(sources) != 3
                or [set(x) for x in sources if isinstance(x, dict)]
                != [{"serviceAccountToken"}, {"configMap"}, {"downwardAPI"}]
            ):
                return False
            token, config, downward = (
                sources[0]["serviceAccountToken"],
                sources[1]["configMap"],
                sources[2]["downwardAPI"],
            )
            if (
                not all(isinstance(v, dict) for v in (token, config, downward))
                or set(item) != {"name", "projected"}
                or set(projected) - {"defaultMode", "sources"}
                or projected.get("defaultMode", 420) != 420
                or set(token) != {"path", "expirationSeconds"}
                or token.get("path") != "token"
                or token.get("expirationSeconds") not in (3600, 3607)
                or set(config) - {"name", "items", "optional"}
                or config.get("name") != "kube-root-ca.crt"
                or config.get("optional", False) is not False
                or config.get("items") != [{"key": "ca.crt", "path": "ca.crt"}]
                or set(downward) != {"items"}
                or downward.get("items")
                not in (
                    [{"path": "namespace", "fieldRef": {"fieldPath": "metadata.namespace"}}],
                    [{"path": "namespace", "fieldRef": {"apiVersion": "v1", "fieldPath": "metadata.namespace"}}],
                )
            ):
                return False
            if expected.get("automountServiceAccountToken") is False or len(automatic) != 0:
                return False
            automatic.add(name)
        else:
            retained.append(item)
    if retained != expected_volumes:
        return False
    excluded = {
        "nodeName",
        "nodeSelector",
        "affinity",
        "tolerations",
        "topologySpreadConstraints",
        "schedulerName",
        "runtimeClassName",
        "volumes",
    }
    pod_defaults: dict[str, Any] = {
        "serviceAccount": expected.get("serviceAccountName"),
        "hostNetwork": False,
        "hostPID": False,
        "hostIPC": False,
        "dnsPolicy": "ClusterFirst",
        "restartPolicy": "Always",
        "terminationGracePeriodSeconds": 30,
        "enableServiceLinks": True,
        "securityContext": {},
        "priority": 0,
        "preemptionPolicy": "PreemptLowerPriority",
        "initContainers": [],
        "ephemeralContainers": [],
        "imagePullSecrets": [],
    }
    if any(
        k not in expected
        and k not in excluded
        and (k not in pod_defaults or type(v) is not type(pod_defaults[k]) or v != pod_defaults[k])
        for k, v in actual.items()
    ):
        return False
    for key, value in expected.items():
        if key in excluded:
            continue
        if key not in ("containers", "initContainers"):
            if actual.get(key) != value:
                return False
            continue
        received = actual.get(key, [])
        if (
            not isinstance(value, list)
            or not isinstance(received, list)
            or len(received) > 128
            or len(value) != len(received)
        ):
            return False
        for original, current in zip(value, received, strict=True):
            if not isinstance(original, dict) or not isinstance(current, dict):
                return False
            mounts = current.get("volumeMounts", [])
            if not isinstance(mounts, list) or len(mounts) > 128:
                return False
            current["volumeMounts"] = [
                m
                for m in mounts
                if not (
                    isinstance(m, dict)
                    and m.get("name") in automatic
                    and m.get("mountPath") == "/var/run/secrets/kubernetes.io/serviceaccount"
                    and m.get("readOnly") is True
                    and set(m) == {"name", "mountPath", "readOnly"}
                )
            ]
            if current.get("volumeMounts", []) != original.get("volumeMounts", []):
                return False
            if any(current.get(k) != v for k, v in original.items()):
                return False
            image = original.get("image", "")
            image_tag = image.rsplit("/", 1)[-1]
            pull_policy = (
                "IfNotPresent"
                if "@sha256:" in image or (":" in image_tag and not image_tag.endswith(":latest"))
                else "Always"
            )
            container_defaults = {
                "command": [],
                "args": [],
                "env": [],
                "envFrom": [],
                "ports": [],
                "securityContext": {},
                "resources": {},
                "volumeMounts": [],
                "imagePullPolicy": pull_policy,
                "terminationMessagePath": "/dev/termination-log",
                "terminationMessagePolicy": "File",
                "stdin": False,
                "stdinOnce": False,
                "tty": False,
                "workingDir": "",
            }
            if any(
                k not in original
                and (
                    k not in container_defaults
                    or type(v) is not type(container_defaults[k])
                    or v != container_defaults[k]
                )
                for k, v in current.items()
            ):
                return False
    return True


def _observation_projection(value: dict[str, Any]) -> dict[str, Any]:
    """Relevant reread facts, excluding opaque RV and status-clock bookkeeping.

    This is sequential drift detection, never an atomic incarnation guarantee.
    """

    def status_facts(item: Any) -> Any:
        if isinstance(item, list):
            return [status_facts(v) for v in item]
        if isinstance(item, dict):
            return {
                k: status_facts(v)
                for k, v in item.items()
                if k
                not in {
                    "lastProbeTime",
                    "lastHeartbeatTime",
                    "lastTransitionTime",
                    "startedAt",
                    "finishedAt",
                    "lastScheduleTime",
                    "startTime",
                    "completionTime",
                    "message",
                    "reason",
                    "restartCount",
                }
            }
        return item

    meta = value.get("metadata", {})
    return {
        "kind": value.get("kind"),
        "apiVersion": value.get("apiVersion"),
        "metadata": {
            k: meta.get(k)
            for k in (
                "name",
                "namespace",
                "uid",
                "generation",
                "deletionTimestamp",
                "labels",
                "annotations",
                "ownerReferences",
            )
        },
        "spec": value.get("spec"),
        "status": status_facts(value.get("status", {})),
    }


def _inventory_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_observation_projection(row) for row in sorted(rows, key=lambda row: row["metadata"]["uid"])]


def _job_template(value: Any, original: dict[str, Any]) -> Any:
    value = copy.deepcopy(value)
    if isinstance(value, dict):
        labels = value.get("metadata", {}).get("labels", {})
        expected = original.get("metadata", {}).get("labels", {})
        if isinstance(labels, dict):
            for key in (
                "batch.kubernetes.io/controller-uid",
                "batch.kubernetes.io/job-name",
                "controller-uid",
                "job-name",
            ):
                if key not in expected:
                    labels.pop(key, None)
    return value


class KubernetesRuntimeAdapter(KubernetesReadAdapter):
    """Bounded GET-only facade on the existing verified native CA and admitted ADC."""

    def get(self, path: str) -> dict[str, Any]:
        if not re.fullmatch(
            r"/(?:api/v1/(?:nodes/[a-z0-9.-]{1,253}|namespaces/[a-z0-9.-]{1,253}/pods)|apis/(?:apps|batch)/v1/namespaces/[a-z0-9.-]{1,253}/(?:deployments|statefulsets|daemonsets|replicasets|jobs|cronjobs)(?:/[a-z0-9.-]{1,253})?)",
            path,
        ):
            raise GKEObservationError("RUNTIME_PATH_REFUSED")
        query = "?" + urlencode({"limit": MAX_ITEMS + 1}) if path.endswith(("/pods", "/replicasets", "/jobs")) else ""
        _checkpoint(self.checkpoint)
        try:
            if not self.credentials.valid:
                self.credentials.refresh(self.credential_request)
            _checkpoint(self.checkpoint)
            token = self.credentials.token
            if not isinstance(token, str) or not token or len(token) > 16384 or any(c in token for c in "\r\n"):
                raise GKEObservationError("CREDENTIAL_UNAVAILABLE")
            response = _http(
                self.base_url + path + query,
                method="GET",
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                data=None,
                context=self.tls,
            )
        except Exception:
            _checkpoint(self.checkpoint)
            raise GKEObservationError("KUBERNETES_READ_UNCONFIRMED") from None
        _checkpoint(self.checkpoint)
        if response.status != 200:
            raise GKEObservationError(
                "RUNTIME_OBJECT_UNOBSERVED" if response.status == 404 else "KUBERNETES_READ_REFUSED"
            )
        try:
            value = json.loads(response.data)
        except Exception:
            raise GKEObservationError("KUBERNETES_RESPONSE_INVALID") from None
        if not isinstance(value, dict):
            raise GKEObservationError("KUBERNETES_RESPONSE_INVALID")
        return value


class GKEIdentityRuntimeObserver:
    def __init__(
        self,
        context: GKEObservationContext,
        workloads: tuple[WorkloadTarget, ...],
        *,
        clients: tuple[Any, Any] | None = None,
        kubernetes_factory: Callable[..., Any] | None = None,
    ) -> None:
        if (
            not isinstance(workloads, tuple)
            or not 1 <= len(workloads) <= MAX_WORKLOADS
            or any(not isinstance(w, WorkloadTarget) for w in workloads)
            or len({(w.namespace, w.kind, w.name) for w in workloads}) != len(workloads)
            or len({w.uid for w in workloads}) != len(workloads)
        ):
            raise GKEObservationError("INVALID_WORKLOAD_TARGET")
        for workload in workloads:
            if not any(
                s.namespace == workload.namespace
                and s.name == workload.service_account_name
                and s.namespace_uid
                and s.service_account_uid
                for s in context.subjects
            ):
                raise GKEObservationError("WORKLOAD_SUBJECT_UNRECORDED")
        self.context = context
        self._kubernetes_factory = kubernetes_factory or KubernetesRuntimeAdapter
        self._source = GKEIdentityObserver(context, clients=clients, kubernetes_factory=self._kubernetes_factory)
        self.workloads = workloads
        self._reads = 0
        self._deadline = 0.0
        self._pool_locations: dict[str, tuple[str, ...]] = {}

    def close(self) -> None:
        self._source.close()

    def __enter__(self) -> GKEIdentityRuntimeObserver:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _cluster(self, checkpoint: Callable[[], None]) -> tuple[Any, tuple[str, ...]]:
        return self._source._cluster(checkpoint)

    def _subjects(self, adapter: Any, checkpoint: Callable[[], None]) -> Any:
        return self._source._subjects(adapter, checkpoint)

    def _pool_snapshot(
        self,
        cluster: Any,
        pools: tuple[str, ...],
        checkpoint: Callable[[], None],
        *,
        accepted_node_pools: tuple[str, ...] | None = None,
    ) -> tuple[Any, ...]:
        if cluster.autopilot.enabled:
            locations = tuple(cluster.locations)
            if (
                not 1 <= len(locations) <= 16
                or len(set(locations)) != len(locations)
                or any(not re.fullmatch(r"[a-z]+(?:-[a-z0-9]+)+[0-9]-[a-z]", zone) for zone in locations)
            ):
                raise GKEObservationError("NODE_LOCATIONS_UNOBSERVED")
            self._pool_locations = {"autopilot": locations}
            return (("autopilot", locations),)
        _, native = self._source._native(checkpoint)
        response = self._source._call(native.list_node_pools, {"parent": self.context.cluster_resource}, checkpoint)
        rows = tuple(response.node_pools)
        if accepted_node_pools is not None:
            if (
                not 1 <= len(rows) <= MAX_POOLS
                or len({row.name for row in rows}) != len(rows)
                or any(not _POOL_NAME.fullmatch(row.name) for row in rows)
            ):
                raise GKEObservationError("NODE_CONFIGURATION_UNVERIFIED")
            rows = tuple(row for row in rows if row.name in accepted_node_pools)
        if len(rows) != len(pools) or {row.name for row in rows} != set(pools):
            raise GKEObservationError("NODE_CONFIGURATION_CHANGED")
        snapshots = []
        for row in rows:
            locations = tuple(row.locations) or tuple(cluster.locations)
            if (
                not 1 <= len(locations) <= 16
                or len(set(locations)) != len(locations)
                or any(not re.fullmatch(r"[a-z]+(?:-[a-z0-9]+)+[0-9]-[a-z]", zone) for zone in locations)
                or row.status != 2
                or row.config.workload_metadata_config.mode != 2
            ):
                raise GKEObservationError("NODE_CONFIGURATION_UNVERIFIED")
            snapshots.append((row.name, locations))
        self._pool_locations = dict(snapshots)
        return tuple(sorted(snapshots))

    def _get(self, adapter: Any, path: str, checkpoint: Callable[[], None]) -> dict[str, Any]:
        _checkpoint(checkpoint)
        self._reads += 1
        if self._reads > MAX_READS or time.monotonic() > self._deadline:
            raise GKEObservationError("RUNTIME_READ_BOUND_EXCEEDED")
        result = adapter.get(path)
        _checkpoint(checkpoint)
        if not isinstance(result, dict) or len(json.dumps(result).encode()) > MAX_BYTES:
            raise GKEObservationError("KUBERNETES_RESPONSE_INVALID")
        return result

    def _list(
        self, adapter: Any, path: str, kind: str, namespace: str, checkpoint: Callable[[], None]
    ) -> list[dict[str, Any]]:
        value = self._get(adapter, path, checkpoint)
        metadata = value.get("metadata")
        items = value.get("items")
        if (
            value.get("apiVersion") != (_ROUTES[kind][0] if kind in _ROUTES else "v1")
            or value.get("kind") != kind + "List"
            or not isinstance(metadata, dict)
            or metadata.get("continue")
            or metadata.get("remainingItemCount", 0)
            or not isinstance(items, list)
            or len(items) > MAX_ITEMS
        ):
            raise GKEObservationError("INVENTORY_INCOMPLETE")
        ids = set()
        for row in items:
            if not isinstance(row, dict):
                raise GKEObservationError("INVENTORY_UNVERIFIED")
            meta = _metadata(row, kind, namespace)
            if meta["uid"] in ids:
                raise GKEObservationError("INVENTORY_UNVERIFIED")
            ids.add(meta["uid"])
        return items

    def _controller(self, adapter: Any, target: WorkloadTarget, checkpoint: Callable[[], None]) -> dict[str, Any]:
        api, plural = _ROUTES[target.kind]
        value = self._get(adapter, f"/apis/{api}/namespaces/{target.namespace}/{plural}/{target.name}", checkpoint)
        meta = _metadata(value, target.kind, target.namespace, name=target.name, uid=target.uid)
        if meta.get("generation") != target.generation or any(
            meta.get("labels", {}).get(k) != v for k, v in self.context.owner_labels.items()
        ):
            raise GKEObservationError("WORKLOAD_IDENTITY_CHANGED")
        return value

    def _node(
        self,
        adapter: Any,
        pod: dict[str, Any],
        template_spec: dict[str, Any],
        target: WorkloadTarget,
        pools: tuple[str, ...],
        autopilot: bool,
        checkpoint: Callable[[], None],
    ) -> dict[str, Any]:
        name = pod["spec"].get("nodeName")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise GKEObservationError("POD_UNSCHEDULED")
        node = self._get(adapter, "/api/v1/nodes/" + name, checkpoint)
        metadata = _metadata(node, "Node", None, name=name)
        labels = _labels(metadata.get("labels", {}))
        match = re.fullmatch(r"gce://([^/]+)/([^/]+)/([^/]+)", node.get("spec", {}).get("providerID", ""))
        if (
            not match
            or match[1] not in (self.context.identity.project_id, self.context.identity.project_number)
            or labels.get("topology.kubernetes.io/zone") != match[2]
            or (
                match[2] != self.context.location
                if re.fullmatch(r".+-[a-z]", self.context.location)
                else not match[2].startswith(self.context.location + "-")
            )
        ):
            raise GKEObservationError("NODE_NATIVE_IDENTITY_UNVERIFIED")
        if not autopilot and (
            not target.allowed_node_pools
            or not set(target.allowed_node_pools) <= set(pools)
            or labels.get("cloud.google.com/gke-nodepool") not in target.allowed_node_pools
            or labels.get("iam.gke.io/gke-metadata-server-enabled") != "true"
        ):
            raise GKEObservationError("NODE_PLACEMENT_UNVERIFIED")
        if autopilot and target.allowed_node_pools:
            raise GKEObservationError("AUTOPILOT_PLACEMENT_UNVERIFIED")
        native_pool = "autopilot" if autopilot else labels.get("cloud.google.com/gke-nodepool", "")
        if match[2] not in self._pool_locations.get(native_pool, ()):
            raise GKEObservationError("NODE_POOL_LOCATION_UNVERIFIED")
        conditions = node.get("status", {}).get("conditions", [])
        if (
            not isinstance(conditions, list)
            or len(conditions) > 32
            or any(not isinstance(item, dict) for item in conditions)
            or len({item.get("type") for item in conditions}) != len(conditions)
            or sum(x.get("type") == "Ready" and x.get("status") == "True" for x in conditions if isinstance(x, dict))
            != 1
        ):
            raise GKEObservationError("NODE_NOT_READY")
        selector = _labels(template_spec.get("nodeSelector", {}))
        if any(labels.get(k) != v for k, v in selector.items()) or pod["spec"].get("nodeSelector", {}) != selector:
            raise GKEObservationError("NODE_SELECTOR_CHANGED")
        affinity = template_spec.get("affinity", {})
        if not isinstance(affinity, dict) or set(affinity) - {"nodeAffinity", "podAffinity", "podAntiAffinity"}:
            raise GKEObservationError("PREDICATE_UNSUPPORTED")
        node_affinity = affinity.get("nodeAffinity", {})
        if not isinstance(node_affinity, dict) or set(node_affinity) - {
            "requiredDuringSchedulingIgnoredDuringExecution",
            "preferredDuringSchedulingIgnoredDuringExecution",
        }:
            raise GKEObservationError("PREDICATE_UNSUPPORTED")
        preferred = node_affinity.get("preferredDuringSchedulingIgnoredDuringExecution", [])
        if not isinstance(preferred, list) or len(preferred) > 32:
            raise GKEObservationError("PREDICATE_UNSUPPORTED")
        for term in preferred:
            if (
                not isinstance(term, dict)
                or set(term) != {"weight", "preference"}
                or type(term["weight"]) is not int
                or not 1 <= term["weight"] <= 100
                or not isinstance(term["preference"], dict)
                or set(term["preference"]) - {"matchExpressions", "matchFields"}
            ):
                raise GKEObservationError("PREDICATE_UNSUPPORTED")
            _requirements(term["preference"].get("matchExpressions", []))
            _requirements(term["preference"].get("matchFields", []), fields=True)
        required = node_affinity.get("requiredDuringSchedulingIgnoredDuringExecution")
        if required is not None:
            terms = (
                required.get("nodeSelectorTerms")
                if isinstance(required, dict) and set(required) == {"nodeSelectorTerms"}
                else None
            )
            if not isinstance(terms, list) or not 1 <= len(terms) <= 32:
                raise GKEObservationError("PREDICATE_UNSUPPORTED")
            matches = []
            for term in terms:
                if not isinstance(term, dict) or set(term) - {"matchExpressions", "matchFields"}:
                    raise GKEObservationError("PREDICATE_UNSUPPORTED")
                expressions = _requirements(term.get("matchExpressions", []))
                fields = _requirements(term.get("matchFields", []), fields=True)
                matches.append(
                    bool(expressions or fields)
                    and _matches(expressions, labels)
                    and _matches(fields, {"metadata.name": name})
                )
            if not any(matches):
                raise GKEObservationError("NODE_AFFINITY_UNSATISFIED")
        tolerations = template_spec.get("tolerations", [])
        observed_tolerations = pod["spec"].get("tolerations", [])
        if (
            not isinstance(tolerations, list)
            or not isinstance(observed_tolerations, list)
            or len(observed_tolerations) > 64
            or any(item not in observed_tolerations for item in tolerations)
        ):
            raise GKEObservationError("POD_PLACEMENT_CHANGED")
        defaults: list[dict[str, Any]] = [
            {"key": key, "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 300}
            for key in ("node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable")
        ]
        if target.kind == "DaemonSet":
            defaults = [
                {"key": key, "operator": "Exists", "effect": "NoExecute"}
                for key in ("node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable")
            ] + [
                {"key": "node.kubernetes.io/" + key, "operator": "Exists", "effect": "NoSchedule"}
                for key in ("disk-pressure", "memory-pressure", "pid-pressure", "unschedulable")
            ]
            # hostNetwork is refused, so its network-unavailable default cannot apply.
        if any(item not in tolerations and item not in defaults for item in observed_tolerations):
            raise GKEObservationError("POD_PLACEMENT_CHANGED")
        actual_affinity = pod["spec"].get("affinity", {})
        if (
            not isinstance(actual_affinity, dict)
            or set(actual_affinity) - {"nodeAffinity", "podAffinity", "podAntiAffinity"}
            or any(actual_affinity.get(k) != affinity.get(k) for k in ("podAffinity", "podAntiAffinity"))
        ):
            raise GKEObservationError("POD_PLACEMENT_CHANGED")
        actual_node = actual_affinity.get("nodeAffinity", {})
        if (
            not isinstance(actual_node, dict)
            or set(actual_node)
            - {"requiredDuringSchedulingIgnoredDuringExecution", "preferredDuringSchedulingIgnoredDuringExecution"}
        ) or actual_node.get("preferredDuringSchedulingIgnoredDuringExecution") != node_affinity.get(
            "preferredDuringSchedulingIgnoredDuringExecution"
        ):
            raise GKEObservationError("POD_PLACEMENT_CHANGED")
        actual_required = actual_node.get("requiredDuringSchedulingIgnoredDuringExecution")
        generated_daemon_affinity = {
            "nodeSelectorTerms": [{"matchFields": [{"key": "metadata.name", "operator": "In", "values": [name]}]}]
        }
        if actual_required != required and not (
            target.kind == "DaemonSet" and actual_required == generated_daemon_affinity
        ):
            raise GKEObservationError("POD_PLACEMENT_CHANGED")
        for key in ("topologySpreadConstraints", "runtimeClassName"):
            if pod["spec"].get(key) != template_spec.get(key):
                raise GKEObservationError("POD_PLACEMENT_CHANGED")
        if pod["spec"].get("schedulerName", "default-scheduler") != template_spec.get(
            "schedulerName", "default-scheduler"
        ) or (template_spec.get("nodeName") and template_spec["nodeName"] != name):
            raise GKEObservationError("POD_PLACEMENT_CHANGED")
        return node

    def _workload(
        self,
        adapter: Any,
        target: WorkloadTarget,
        pools: tuple[str, ...],
        autopilot: bool,
        checkpoint: Callable[[], None],
    ) -> WorkloadObservation:
        controller = self._controller(adapter, target, checkpoint)
        spec = controller.get("spec", {})
        status = controller.get("status", {})
        if not isinstance(spec, dict) or not isinstance(status, dict):
            raise GKEObservationError("WORKLOAD_RESPONSE_INVALID")
        template = (
            spec.get("jobTemplate", {}).get("spec", {}).get("template")
            if target.kind == "CronJob"
            else spec.get("template")
        )
        if (
            not isinstance(template, dict)
            or template_sha256(template) != target.template_sha256
            or not isinstance(template.get("spec"), dict)
        ):
            raise GKEObservationError("WORKLOAD_TEMPLATE_CHANGED")
        pod_spec = template["spec"]
        if (
            pod_spec.get("serviceAccountName") != target.service_account_name
            or pod_spec.get("hostNetwork", False) is not False
            or placement_sha256(pod_spec) != target.placement_sha256
            or not _select(_selector(target.selector_json), _labels(template.get("metadata", {}).get("labels", {})))
        ):
            raise GKEObservationError("WORKLOAD_CONFIGURATION_CHANGED")
        if target.kind != "CronJob" and spec.get("selector") != _selector(target.selector_json):
            raise GKEObservationError("WORKLOAD_SELECTOR_CHANGED")
        generation_observed = (
            target.kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet")
            and type(status.get("observedGeneration")) is int
            and status["observedGeneration"] >= target.generation
        )
        if target.kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet") and not generation_observed:
            return WorkloadObservation(
                target.kind,
                target.name,
                target.uid,
                target.generation,
                True,
                False,
                False,
                False,
                "CONTROLLER_GENERATION_PENDING",
            )
        expected = target.desired_count
        owners = [(target.kind, target.uid, target.name)]
        current_rs_hash: str | None = None
        child_inventory = None
        child_kind = ""
        child_path = ""
        if target.kind == "Deployment":
            rs = self._list(
                adapter,
                f"/apis/apps/v1/namespaces/{target.namespace}/replicasets",
                "ReplicaSet",
                target.namespace,
                checkpoint,
            )
            child_inventory, child_kind = rs, "ReplicaSet"
            child_path = f"/apis/apps/v1/namespaces/{target.namespace}/replicasets"
            selected = []
            for row in rs:
                meta = row["metadata"]
                if _owner(meta, target.kind, target.uid, target.name):
                    child_template = copy.deepcopy(row.get("spec", {}).get("template", {}))
                    child_template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)
                    if _integer(row.get("spec", {}).get("replicas", 0)) > 0:
                        if child_template != template:
                            raise GKEObservationError("DEPLOYMENT_REVISION_PENDING")
                        selected.append(row)
            if len(selected) != 1:
                return WorkloadObservation(
                    target.kind,
                    target.name,
                    target.uid,
                    target.generation,
                    True,
                    True,
                    False,
                    False,
                    "DEPLOYMENT_REVISION_PENDING",
                )
            current_rs = selected[0]
            current_rs_hash = current_rs["metadata"].get("labels", {}).get("pod-template-hash")
            if (
                not isinstance(current_rs_hash, str)
                or not current_rs_hash
                or not _VALUE.fullmatch(current_rs_hash)
                or current_rs["spec"]["template"]["metadata"].get("labels", {}).get("pod-template-hash")
                != current_rs_hash
                or type(current_rs["metadata"].get("generation")) is not int
                or current_rs["metadata"].get("generation", 0) < 1
                or type(current_rs.get("status", {}).get("observedGeneration")) is not int
                or current_rs.get("status", {}).get("observedGeneration", 0)
                < current_rs["metadata"].get("generation", 1)
            ):
                raise GKEObservationError("DEPLOYMENT_REVISION_UNVERIFIED")
            owners = [("ReplicaSet", current_rs["metadata"]["uid"], current_rs["metadata"]["name"])]
        if target.kind in ("Deployment", "ReplicaSet", "StatefulSet"):
            counts = ("replicas", "readyReplicas") + (
                ("updatedReplicas", "availableReplicas")
                if target.kind in ("Deployment", "StatefulSet")
                else ("fullyLabeledReplicas", "availableReplicas")
            )
            if (
                _integer(spec.get("replicas"), positive=True) != expected
                or any(_integer(status.get(key, 0)) != expected for key in counts)
                or status.get("unavailableReplicas", 0) != 0
            ):
                return WorkloadObservation(
                    target.kind,
                    target.name,
                    target.uid,
                    target.generation,
                    True,
                    True,
                    False,
                    False,
                    "REPLICA_ROLLOUT_PENDING",
                )
            if target.kind == "StatefulSet" and (
                not isinstance(status.get("updateRevision"), str)
                or not status["updateRevision"]
                or status.get("currentRevision") != status["updateRevision"]
            ):
                return WorkloadObservation(
                    target.kind,
                    target.name,
                    target.uid,
                    target.generation,
                    True,
                    True,
                    False,
                    False,
                    "STATEFUL_REVISION_PENDING",
                )
        if target.kind == "DaemonSet" and (
            any(
                _integer(status.get(key, 0)) != expected
                for key in (
                    "desiredNumberScheduled",
                    "currentNumberScheduled",
                    "updatedNumberScheduled",
                    "numberReady",
                    "numberAvailable",
                )
            )
            or status.get("numberMisscheduled", 0) != 0
            or status.get("numberUnavailable", 0) != 0
        ):
            return WorkloadObservation(
                target.kind,
                target.name,
                target.uid,
                target.generation,
                True,
                True,
                False,
                False,
                "DAEMON_ROLLOUT_PENDING",
            )
        execution = False
        if target.kind == "Job":
            execution = _completed_job(controller)
            if spec.get("suspend", False) or _integer(status.get("active", 0)) != expected:
                return WorkloadObservation(
                    target.kind,
                    target.name,
                    target.uid,
                    target.generation,
                    True,
                    False,
                    False,
                    execution,
                    "JOB_EXECUTION_PENDING",
                )
        if target.kind == "CronJob":
            jobs = self._list(
                adapter, f"/apis/batch/v1/namespaces/{target.namespace}/jobs", "Job", target.namespace, checkpoint
            )
            child_inventory, child_kind = jobs, "Job"
            child_path = f"/apis/batch/v1/namespaces/{target.namespace}/jobs"
            active = status.get("active", [])
            if not isinstance(active, list) or len(active) > MAX_ITEMS:
                raise GKEObservationError("JOB_INVENTORY_UNVERIFIED")
            if any(
                not isinstance(x, dict)
                or x.get("apiVersion") != "batch/v1"
                or x.get("kind") != "Job"
                or x.get("namespace") != target.namespace
                or not isinstance(x.get("name"), str)
                or not _NAME.fullmatch(x["name"])
                for x in active
            ):
                raise GKEObservationError("JOB_INVENTORY_UNVERIFIED")
            active_uids = {x.get("uid") for x in active}
            if len(active_uids) != len(active):
                raise GKEObservationError("JOB_INVENTORY_UNVERIFIED")
            for uid in active_uids:
                _guid(uid)
            active_names = {x["uid"]: x["name"] for x in active}
            owners = []
            for job in jobs:
                if (
                    _owner(job["metadata"], target.kind, target.uid, target.name)
                    and _job_template(job.get("spec", {}).get("template"), template) == template
                    and _completed_job(job)
                ):
                    execution = True
                if (
                    _owner(job["metadata"], target.kind, target.uid, target.name)
                    and job["metadata"]["uid"] in active_uids
                ):
                    if (
                        _job_template(job.get("spec", {}).get("template"), template) != template
                        or job["metadata"]["name"] != active_names[job["metadata"]["uid"]]
                        or _integer(job.get("status", {}).get("active", 0), positive=True) != expected
                        or job.get("spec", {}).get("suspend", False)
                    ):
                        raise GKEObservationError("JOB_SOURCE_CHANGED")
                    owners.append(("Job", job["metadata"]["uid"], job["metadata"]["name"]))
            if spec.get("suspend", False) or not owners or len(owners) != len(active_uids):
                return WorkloadObservation(
                    target.kind,
                    target.name,
                    target.uid,
                    target.generation,
                    True,
                    False,
                    False,
                    execution,
                    "CRON_EXECUTION_PENDING",
                )
        pods = self._list(adapter, f"/api/v1/namespaces/{target.namespace}/pods", "Pod", target.namespace, checkpoint)
        selected = [
            p for p in pods if _select(_selector(target.selector_json), _labels(p["metadata"].get("labels", {})))
        ]
        if len(selected) != expected:
            return WorkloadObservation(
                target.kind,
                target.name,
                target.uid,
                target.generation,
                True,
                generation_observed,
                False,
                execution,
                "POD_ROLLOUT_PENDING",
            )
        pod_uids = []
        node_uids = []
        node_snapshots = {}
        for pod in selected:
            if not any(_owner(pod["metadata"], *owner) for owner in owners) or any(
                pod["metadata"].get("labels", {}).get(k) != v for k, v in self.context.owner_labels.items()
            ):
                raise GKEObservationError("POD_OWNER_CHANGED")
            p_spec = pod.get("spec", {})
            p_status = pod.get("status", {})
            conditions = p_status.get("conditions", [])
            if (
                not isinstance(p_spec, dict)
                or not isinstance(p_status, dict)
                or p_spec.get("hostNetwork", False) is not False
                or p_spec.get("serviceAccountName") != target.service_account_name
            ):
                raise GKEObservationError("POD_IDENTITY_CHANGED")
            if (
                not isinstance(conditions, list)
                or len(conditions) > 32
                or any(not isinstance(item, dict) for item in conditions)
                or len({item.get("type") for item in conditions}) != len(conditions)
                or p_status.get("phase") != "Running"
                or sum(
                    x.get("type") == "Ready" and x.get("status") == "True" for x in conditions if isinstance(x, dict)
                )
                != 1
            ):
                return WorkloadObservation(
                    target.kind,
                    target.name,
                    target.uid,
                    target.generation,
                    True,
                    generation_observed,
                    False,
                    execution,
                    "POD_NOT_READY",
                )
            if (
                target.kind == "StatefulSet"
                and pod["metadata"]["labels"].get("controller-revision-hash") != status["updateRevision"]
            ):
                raise GKEObservationError("POD_REVISION_CHANGED")
            containers = p_spec.get("containers", [])
            container_status = p_status.get("containerStatuses", [])
            if (
                not isinstance(containers, list)
                or not 1 <= len(containers) <= 128
                or not isinstance(container_status, list)
                or len(container_status) != len(containers)
                or any(
                    not isinstance(item, dict)
                    or item.get("ready") is not True
                    or not isinstance(item.get("state", {}).get("running"), dict)
                    for item in container_status
                )
                or {item.get("name") for item in container_status}
                != {item.get("name") for item in containers if isinstance(item, dict)}
            ):
                return WorkloadObservation(
                    target.kind,
                    target.name,
                    target.uid,
                    target.generation,
                    True,
                    generation_observed,
                    False,
                    execution,
                    "CONTAINERS_NOT_READY",
                )
            if (
                current_rs_hash is not None
                and pod["metadata"].get("labels", {}).get("pod-template-hash") != current_rs_hash
            ):
                raise GKEObservationError("POD_REVISION_CHANGED")
            template_meta = template.get("metadata", {})
            if any(
                pod["metadata"].get("labels", {}).get(k) != v for k, v in template_meta.get("labels", {}).items()
            ) or pod["metadata"].get("annotations", {}) != template_meta.get("annotations", {}):
                raise GKEObservationError("POD_TEMPLATE_CHANGED")
            if not _pod_template_matches(pod_spec, p_spec):
                raise GKEObservationError("POD_TEMPLATE_CHANGED")
            node = self._node(adapter, pod, pod_spec, target, pools, autopilot, checkpoint)
            pod_uids.append(pod["metadata"]["uid"])
            node_uids.append(node["metadata"]["uid"])
            node_snapshots[node["metadata"]["name"]] = node
        final_pods = self._list(
            adapter, f"/api/v1/namespaces/{target.namespace}/pods", "Pod", target.namespace, checkpoint
        )
        if _inventory_projection(final_pods) != _inventory_projection(pods):
            raise GKEObservationError("POD_OBSERVATION_CHANGED")
        for name, node in node_snapshots.items():
            final_node = self._get(adapter, "/api/v1/nodes/" + name, checkpoint)
            _metadata(final_node, kind="Node", namespace=None, name=name, uid=node["metadata"]["uid"])
            if _observation_projection(final_node) != _observation_projection(node):
                raise GKEObservationError("NODE_OBSERVATION_CHANGED")
        if child_inventory is not None and _inventory_projection(
            self._list(adapter, child_path, child_kind, target.namespace, checkpoint)
        ) != _inventory_projection(child_inventory):
            raise GKEObservationError("CONTROLLER_CHAIN_CHANGED")
        final = self._controller(adapter, target, checkpoint)
        if _observation_projection(final) != _observation_projection(controller):
            raise GKEObservationError("WORKLOAD_OBSERVATION_CHANGED")
        return WorkloadObservation(
            target.kind,
            target.name,
            target.uid,
            target.generation,
            True,
            generation_observed,
            True,
            execution,
            "WORKLOAD_ROLLOUT_OBSERVED",
            tuple(sorted(pod_uids)),
            tuple(sorted(set(node_uids))),
        )

    def observe(self, *, checkpoint: Callable[[], None]) -> RuntimeObservation:
        self._reads = 0
        self._deadline = time.monotonic() + DEADLINE_SECONDS
        original_checkpoint = checkpoint

        def bounded_checkpoint() -> None:
            _checkpoint(original_checkpoint)
            if time.monotonic() > self._deadline:
                raise GKEObservationError("RUNTIME_READ_BOUND_EXCEEDED")

        checkpoint = bounded_checkpoint
        _checkpoint(checkpoint)
        try:
            cluster, pools = self._cluster(checkpoint)
            pool_snapshot = self._pool_snapshot(cluster, pools, checkpoint)
            adapter = self._kubernetes_factory(
                cluster.endpoint, cluster.master_auth.cluster_ca_certificate, self._source._credentials, checkpoint
            )
            subjects = self._subjects(adapter, checkpoint)
            if any(not row.annotation_matches for row in subjects):
                raise GKEObservationError("GSA_ANNOTATION_UNOBSERVED")
            results = tuple(
                self._workload(adapter, w, pools, cluster.autopilot.enabled, checkpoint) for w in self.workloads
            )
            final_cluster, final_pools = self._cluster(checkpoint)
            final_pool_snapshot = self._pool_snapshot(final_cluster, final_pools, checkpoint)
            if (
                type(final_cluster).serialize(final_cluster) != type(cluster).serialize(cluster)
                or final_pools != pools
                or final_pool_snapshot != pool_snapshot
                or self._subjects(adapter, checkpoint) != subjects
            ):
                raise GKEObservationError("RUNTIME_SOURCE_CHANGED")
            _checkpoint(checkpoint)
            return RuntimeObservation(
                True,
                all(row.workload_ready for row in results),
                "WORKLOAD_ROLLOUT_OBSERVED"
                if all(row.workload_ready for row in results)
                else "WORKLOAD_ROLLOUT_PENDING",
                results,
            )
        except GKEObservationError as error:
            _checkpoint(original_checkpoint)
            return RuntimeObservation(False, False, str(error))
        except (AttributeError, TypeError, KeyError, RecursionError):
            _checkpoint(original_checkpoint)
            return RuntimeObservation(False, False, "RUNTIME_RESPONSE_INVALID")
