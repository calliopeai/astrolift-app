"""Private receipt-bound app resources; configuration evidence is not rollout."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from gcp.gke_identity_observation import (
    GKEIdentityObserver,
    GKEObservationContext,
    GKEObservationError,
    KubernetesReadAdapter,
    _checkpoint,
    _http,
)
from gcp.gke_identity_runtime import _select, _selector, placement_sha256, template_sha256
from gcp.identity_owned import MAX_BYTES

if TYPE_CHECKING:
    from collections.abc import Callable

_ROUTES = {
    "Deployment": ("apps/v1", "deployments"),
    "StatefulSet": ("apps/v1", "statefulsets"),
    "DaemonSet": ("apps/v1", "daemonsets"),
    "Service": ("v1", "services"),
    "Secret": ("v1", "secrets"),
    "ConfigMap": ("v1", "configmaps"),
    "PersistentVolumeClaim": ("v1", "persistentvolumeclaims"),
    "Ingress": ("networking.k8s.io/v1", "ingresses"),
    "NetworkPolicy": ("networking.k8s.io/v1", "networkpolicies"),
    "PodMonitor": ("monitoring.coreos.com/v1", "podmonitors"),
    "HTTPRoute": ("gateway.networking.k8s.io/v1", "httproutes"),
    "Gateway": ("gateway.networking.k8s.io/v1", "gateways"),
}
_CONTROLLERS = {"Deployment", "StatefulSet", "DaemonSet"}
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_VERSION = re.compile(r"[!-~]{1,256}\Z")
_MARKER = "astrolift.io/app-apply-operation"


class ApplyError(ValueError):
    def __init__(self, reason: str, ledger: ApplyLedger | None = None) -> None:
        super().__init__(reason)
        self.ledger = ledger


def _guid(value: Any) -> None:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or not UUID(value).int:
            raise ValueError
    except Exception:
        raise ApplyError("INVALID_APPLY_GUID") from None


def _sha(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise ApplyError("INVALID_APPLY_DIGEST")


def _hash(value: Any) -> str:
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except Exception:
        raise ApplyError("INVALID_APPLY_METADATA") from None
    if len(data) > MAX_BYTES:
        raise ApplyError("APPLY_BOUND_EXCEEDED")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ExecutionBinding:
    deployment_id: str
    operation_id: str
    workflow_id: str
    execution_id: str

    def __post_init__(self) -> None:
        _guid(self.deployment_id)
        _guid(self.operation_id)
        if any(
            not isinstance(v, str) or not 1 <= len(v) <= 200 or any(ord(c) < 32 for c in v)
            for v in (self.workflow_id, self.execution_id)
        ):
            raise ApplyError("INVALID_APPLY_EXECUTION")


@dataclass(frozen=True)
class ResourcePlan:
    api_version: str
    kind: str
    namespace: str
    name: str
    request_sha256: str
    projection_sha256: str
    fields: tuple[str, ...]
    label_keys: tuple[str, ...]
    annotation_keys: tuple[str, ...]
    template_sha256: str = ""
    placement_sha256: str = ""
    replicas: int | None = None

    def __post_init__(self) -> None:
        if _ROUTES.get(self.kind, (None,))[0] != self.api_version:
            raise ApplyError("APPLY_RESOURCE_UNSUPPORTED")
        if any(not isinstance(v, str) or not _NAME.fullmatch(v) for v in (self.namespace, self.name)):
            raise ApplyError("INVALID_APPLY_RESOURCE")
        for digest in (self.request_sha256, self.projection_sha256):
            _sha(digest)
        for keys in (self.fields, self.label_keys, self.annotation_keys):
            if (
                type(keys) is not tuple
                or len(keys) > 64
                or tuple(sorted(set(keys))) != keys
                or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9_.\-/]{1,253}", k) for k in keys)
            ):
                raise ApplyError("INVALID_APPLY_RESOURCE_KEYS")
        allowed = {"spec"} if self.kind not in ("Secret", "ConfigMap") else {"data", "binaryData", "type", "immutable"}
        if set(self.fields) - allowed:
            raise ApplyError("APPLY_RESOURCE_FIELDS_UNSUPPORTED")
        if self.kind in _CONTROLLERS:
            _sha(self.template_sha256)
            _sha(self.placement_sha256)
            if self.kind == "DaemonSet":
                if self.replicas is not None:
                    raise ApplyError("APPLY_REPLICAS_UNSUPPORTED")
            elif type(self.replicas) is not int or not 1 <= self.replicas <= 256:
                raise ApplyError("APPLY_REPLICAS_UNSUPPORTED")
        elif self.template_sha256 or self.placement_sha256 or self.replicas is not None:
            raise ApplyError("INVALID_APPLY_RESOURCE")

    @property
    def collection(self) -> str:
        route = _ROUTES.get(self.kind)
        if route is None or route[0] != self.api_version:
            raise ApplyError("APPLY_RESOURCE_UNSUPPORTED")
        prefix = "/api/v1" if self.api_version == "v1" else "/apis/" + self.api_version
        return prefix + "/namespaces/" + self.namespace + "/" + route[1]

    @property
    def path(self) -> str:
        return self.collection + "/" + self.name


@dataclass(frozen=True)
class PlacementAcceptance:
    cluster_resource: str
    native_cluster_id: str
    autopilot: bool
    allowed_node_pools: tuple[str, ...]
    controller_binding_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.cluster_resource, str)
            or not re.fullmatch(
                r"projects/[a-z0-9-]{6,63}/locations/[a-z0-9-]{1,63}/clusters/[a-z0-9-]{1,63}", self.cluster_resource
            )
            or not isinstance(self.native_cluster_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.native_cluster_id)
            or type(self.autopilot) is not bool
            or type(self.allowed_node_pools) is not tuple
            or any(not isinstance(p, str) or not _NAME.fullmatch(p) for p in self.allowed_node_pools)
            or tuple(sorted(set(self.allowed_node_pools))) != self.allowed_node_pools
            or len(self.allowed_node_pools) > 64
            or (self.autopilot and self.allowed_node_pools)
            or (not self.autopilot and not self.allowed_node_pools)
        ):
            raise ApplyError("INVALID_PLACEMENT_ACCEPTANCE")
        _sha(self.controller_binding_sha256)


def controller_binding_sha256(execution: ExecutionBinding, identity: str, resources: tuple[ResourcePlan, ...]) -> str:
    return _hash(
        {
            "schema": "astrolift.gcp.controller-placement.v1",
            "execution": asdict(execution),
            "identity": identity,
            "controllers": [asdict(r) for r in resources if r.kind in _CONTROLLERS],
        }
    )


@dataclass(frozen=True)
class CompiledAppPlan:
    execution: ExecutionBinding
    identity_sha256: str
    resources: tuple[ResourcePlan, ...]
    placement: PlacementAcceptance | None = None

    def __post_init__(self) -> None:
        _sha(self.identity_sha256)
        if (
            type(self.execution) is not ExecutionBinding
            or not 1 <= len(self.resources) <= 256
            or len({r.path for r in self.resources}) != len(self.resources)
        ):
            raise ApplyError("INVALID_APPLY_PLAN")
        for row in self.resources:
            if type(row) is not ResourcePlan or not _NAME.fullmatch(row.namespace) or not _NAME.fullmatch(row.name):
                raise ApplyError("INVALID_APPLY_PLAN")
            _sha(row.request_sha256)
            _sha(row.projection_sha256)
            if row.kind in _CONTROLLERS:
                _sha(row.template_sha256)
                _sha(row.placement_sha256)
                if row.kind != "DaemonSet" and (type(row.replicas) is not int or not 1 <= row.replicas <= 256):
                    raise ApplyError("APPLY_REPLICAS_UNSUPPORTED")
                if row.kind == "DaemonSet" and row.replicas is not None:
                    raise ApplyError("APPLY_REPLICAS_UNSUPPORTED")
        if not 1 <= sum(row.kind in _CONTROLLERS for row in self.resources) <= 16:
            raise ApplyError("APPLY_CONTROLLER_BOUND_EXCEEDED")
        if self.placement is not None and (
            type(self.placement) is not PlacementAcceptance
            or self.placement.controller_binding_sha256
            != controller_binding_sha256(self.execution, self.identity_sha256, self.resources)
        ):
            raise ApplyError("ACCEPTED_CONTROLLER_PLACEMENT_CHANGED")

    @property
    def sha256(self) -> str:
        return _hash({"schema": "astrolift.gcp.app-apply.v2", **asdict(self)})


class ApplyPhase(StrEnum):
    UNSENT = "UNSENT"
    SENT = "SENT"
    EVIDENCE = "EVIDENCE"
    OBSERVED = "OBSERVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ObservedResource:
    resource: ResourcePlan
    uid: str
    resource_version: str
    generation: int | None
    native_template_sha256: str = ""
    native_placement_sha256: str = ""
    native_projection_sha256: str = ""
    native_assignments_sha256: str = ""

    def __post_init__(self) -> None:
        _guid(self.uid)
        _sha(self.native_projection_sha256)
        _sha(self.native_assignments_sha256)
        if not isinstance(self.resource_version, str) or not _VERSION.fullmatch(self.resource_version):
            raise ApplyError("APPLY_VERSION_UNVERIFIED")
        if self.resource.kind in _CONTROLLERS:
            if type(self.generation) is not int or self.generation < 1:
                raise ApplyError("APPLY_GENERATION_UNVERIFIED")
            _sha(self.native_template_sha256)
            _sha(self.native_placement_sha256)


@dataclass(frozen=True)
class ApplyIntent:
    submission_id: str
    phase: ApplyPhase
    resource: ResourcePlan
    method: str
    request_sha256: str
    previous_uid: str = ""
    previous_resource_version: str = ""

    def __post_init__(self) -> None:
        _guid(self.submission_id)
        _sha(self.request_sha256)
        if type(self.phase) is not ApplyPhase or self.method not in ("POST", "PATCH"):
            raise ApplyError("INVALID_APPLY_INTENT")
        if self.method == "PATCH":
            _guid(self.previous_uid)
            if not _VERSION.fullmatch(self.previous_resource_version):
                raise ApplyError("INVALID_APPLY_INTENT")
        elif self.previous_uid or self.previous_resource_version:
            raise ApplyError("INVALID_APPLY_INTENT")

    @property
    def sha256(self) -> str:
        return _hash(asdict(self))


@dataclass(frozen=True)
class NativeRejection:
    intent: ApplyIntent
    http_code: int
    reason: str
    response_sha256: str

    def __post_init__(self) -> None:
        if self.intent.phase != ApplyPhase.SENT or _REJECTIONS.get(self.http_code) != self.reason:
            raise ApplyError("INVALID_APPLY_REJECTION")
        _sha(self.response_sha256)

    @property
    def sha256(self) -> str:
        return _hash(asdict(self))


_REJECTIONS = {400: "BadRequest", 403: "Forbidden", 409: "Conflict", 422: "Invalid"}


class RejectedRequest(ApplyError):
    def __init__(self, code: int, reason: str, response_sha256: str):
        super().__init__("APPLY_REQUEST_REJECTED")
        self.code, self.reason, self.response_sha256 = code, reason, response_sha256


@dataclass(frozen=True)
class ApplyLedger:
    resources: tuple[ObservedResource, ...] = ()
    pending: ApplyIntent | None = None
    rejection: NativeRejection | None = None

    @property
    def sha256(self) -> str:
        return _hash(ledger_payload(self))


@dataclass(frozen=True)
class SubmissionReceipt:
    journal_id: str
    journal_version: int
    operation_id: str
    record_sha256: str
    phase: ApplyPhase


@dataclass(frozen=True)
class NativeEvidence:
    intent: ApplyIntent
    resource: ObservedResource

    @property
    def sha256(self) -> str:
        return _hash(asdict(self))


@dataclass(frozen=True)
class EvidenceReceipt:
    journal_id: str
    journal_version: int
    operation_id: str
    evidence_sha256: str


@dataclass(frozen=True)
class AppApplyReceipt:
    plan_sha256: str
    ledger: ApplyLedger
    configuration_observed: bool
    workload_ready: bool = False


def ledger_payload(ledger: ApplyLedger) -> dict[str, Any]:
    if type(ledger) is not ApplyLedger:
        raise ApplyError("INVALID_APPLY_LEDGER")
    if len(ledger.resources) > 256 or len({r.resource.path for r in ledger.resources}) != len(ledger.resources):
        raise ApplyError("INVALID_APPLY_LEDGER")
    return dict(json.loads(json.dumps(asdict(ledger))))


def _resource(payload: dict[str, Any]) -> ResourcePlan:
    return ResourcePlan(
        **{
            **payload,
            "fields": tuple(payload["fields"]),
            "label_keys": tuple(payload["label_keys"]),
            "annotation_keys": tuple(payload["annotation_keys"]),
        }
    )


def ledger_from_payload(payload: dict[str, Any]) -> ApplyLedger:
    try:
        rows = tuple(ObservedResource(**{**r, "resource": _resource(r["resource"])}) for r in payload["resources"])
        p = payload["pending"]
        pending = (
            ApplyIntent(**{**p, "resource": _resource(p["resource"]), "phase": ApplyPhase(p["phase"])}) if p else None
        )
        rejected = payload["rejection"]
        rejection = (
            NativeRejection(
                **{
                    **rejected,
                    "intent": ApplyIntent(
                        **{
                            **rejected["intent"],
                            "resource": _resource(rejected["intent"]["resource"]),
                            "phase": ApplyPhase(rejected["intent"]["phase"]),
                        }
                    ),
                }
            )
            if rejected
            else None
        )
        ledger = ApplyLedger(rows, pending, rejection)
        if ledger_payload(ledger) != payload or (rejection and (not pending or pending.phase != ApplyPhase.REJECTED)):
            raise ValueError
        return ledger
    except Exception:
        raise ApplyError("INVALID_APPLY_LEDGER") from None


def _defaults(value: dict[str, Any]) -> None:
    """Only reviewed core/apps-v1 defaults, never arbitrary admission equivalence."""
    kind = value["kind"]
    spec = value.get("spec", {})

    def drop(mapping: dict[str, Any], defaults: dict[str, Any]) -> None:
        for key, expected in defaults.items():
            if key in mapping and mapping[key] == expected:
                del mapping[key]

    if kind in _CONTROLLERS:
        drop(spec, {"revisionHistoryLimit": 10})
        if kind == "Deployment":
            drop(spec, {"progressDeadlineSeconds": 600})
        elif kind == "StatefulSet":
            drop(spec, {"podManagementPolicy": "OrderedReady"})
        strategy = spec.get("strategy" if kind == "Deployment" else "updateStrategy")
        if isinstance(strategy, dict):
            drop(strategy, {"type": "RollingUpdate"})
            rolling = strategy.get("rollingUpdate")
            if isinstance(rolling, dict):
                drop(
                    rolling,
                    {"maxSurge": 0, "maxUnavailable": 1}
                    if kind == "DaemonSet"
                    else {"partition": 0}
                    if kind == "StatefulSet"
                    else {"maxSurge": "25%", "maxUnavailable": "25%"},
                )
                if not rolling:
                    strategy.pop("rollingUpdate")
            if not strategy:
                spec.pop("strategy" if kind == "Deployment" else "updateStrategy")
        template = spec.get("template", {})
        drop(template.get("metadata", {}), {"creationTimestamp": None})
        pod = template.get("spec", {})
        drop(
            pod,
            {
                "restartPolicy": "Always",
                "dnsPolicy": "ClusterFirst",
                "schedulerName": "default-scheduler",
                "terminationGracePeriodSeconds": 30,
                "securityContext": {},
            },
        )
        for c in (*pod.get("containers", []), *pod.get("initContainers", [])):
            drop(
                c,
                {"terminationMessagePath": "/dev/termination-log", "terminationMessagePolicy": "File", "resources": {}},
            )
            image = c.get("image", "")
            default_pull = (
                "Always"
                if image.endswith(":latest") or (":" not in image.rsplit("/", 1)[-1] and "@" not in image)
                else "IfNotPresent"
            )
            drop(c, {"imagePullPolicy": default_pull})
            for port in c.get("ports", []):
                drop(port, {"protocol": "TCP"})
            for key in ("readinessProbe", "livenessProbe", "startupProbe"):
                probe = c.get(key)
                if isinstance(probe, dict):
                    drop(
                        probe, {"timeoutSeconds": 1, "periodSeconds": 10, "successThreshold": 1, "failureThreshold": 3}
                    )
                    if isinstance(probe.get("httpGet"), dict):
                        drop(probe["httpGet"], {"scheme": "HTTP"})
        for volume in pod.get("volumes", []):
            for key in ("secret", "configMap", "projected", "downwardAPI"):
                if isinstance(volume.get(key), dict):
                    drop(volume[key], {"defaultMode": 420})
    if kind == "Service":
        drop(spec, {"type": "ClusterIP", "sessionAffinity": "None", "internalTrafficPolicy": "Cluster"})
        if "clusterIP" in spec:
            try:
                if spec["clusterIP"] != "None":
                    ipaddress.ip_address(spec["clusterIP"])
                if any(v != "None" and not ipaddress.ip_address(v) for v in spec.get("clusterIPs", [])):
                    raise ValueError
            except Exception:
                raise ApplyError("SERVICE_ADDRESS_UNVERIFIED") from None
            spec.pop("clusterIP")
            spec.pop("clusterIPs", None)
            families = spec.pop("ipFamilies", [])
            if any(v not in ("IPv4", "IPv6") for v in families) or len(families) > 2:
                raise ApplyError("SERVICE_ADDRESS_UNVERIFIED")
            drop(spec, {"ipFamilyPolicy": "SingleStack"})
        for port in spec.get("ports", []):
            drop(port, {"protocol": "TCP"})
    if kind == "Secret":
        drop(value, {"type": "Opaque"})
    if kind == "PersistentVolumeClaim":
        drop(spec, {"volumeMode": "Filesystem"})
        if "volumeName" in spec:
            if not isinstance(spec["volumeName"], str) or not _NAME.fullmatch(spec["volumeName"]):
                raise ApplyError("PVC_VOLUME_UNVERIFIED")
            spec.pop("volumeName")


def _assignments(body: dict[str, Any]) -> dict[str, Any]:
    spec = body.get("spec", {})
    keys = (
        ("clusterIP", "clusterIPs", "ipFamilies", "ipFamilyPolicy")
        if body.get("kind") == "Service"
        else ("volumeName",)
        if body.get("kind") == "PersistentVolumeClaim"
        else ()
    )
    return {key: spec[key] for key in keys if key in spec}


def projection(body: dict[str, Any], descriptor: ResourcePlan) -> dict[str, Any]:
    value = deepcopy(body)
    meta = value.get("metadata", {})
    if meta.get("deletionTimestamp"):
        raise ApplyError("APPLY_RESOURCE_RETIRING")
    projected = {
        "apiVersion": value.get("apiVersion"),
        "kind": value.get("kind"),
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": {k: meta.get("labels", {}).get(k) for k in descriptor.label_keys},
            "annotations": {k: meta.get("annotations", {}).get(k) for k in descriptor.annotation_keys},
            **{
                k: v
                for k, v in meta.items()
                if k
                not in {
                    "name",
                    "namespace",
                    "labels",
                    "annotations",
                    "uid",
                    "resourceVersion",
                    "generation",
                    "creationTimestamp",
                    "managedFields",
                    "selfLink",
                }
            },
        },
    }
    for key in set(value) - {"apiVersion", "kind", "metadata", "status"}:
        projected[key] = value[key]
    _defaults(projected)
    return projected


def compile_app_plan(
    context: GKEObservationContext,
    execution: ExecutionBinding,
    resources: list[dict[str, Any]],
    *,
    identity_sha256: str,
    placement: PlacementAcceptance | None = None,
) -> tuple[CompiledAppPlan, tuple[dict[str, Any], ...]]:
    if type(context) is not GKEObservationContext or not isinstance(resources, list) or not 1 <= len(resources) <= 256:
        raise ApplyError("INVALID_APPLY_PLAN")
    bodies, descriptors = [], []
    for raw in resources:
        body = deepcopy(raw)
        if (
            not isinstance(body, dict)
            or body.get("kind") not in _ROUTES
            or body.get("apiVersion") != _ROUTES[body["kind"]][0]
        ):
            raise ApplyError("APPLY_RESOURCE_UNSUPPORTED")
        meta = body.get("metadata")
        if (
            not isinstance(meta, dict)
            or not _NAME.fullmatch(meta.get("name", ""))
            or len(meta.get("labels", {})) > 64
            or len(meta.get("annotations", {})) > 64
            or set(meta) - {"name", "namespace", "labels", "annotations"}
        ):
            raise ApplyError("APPLY_METADATA_UNSUPPORTED")
        subject = next((s for s in context.subjects if s.namespace == meta.get("namespace")), None)
        if subject is None or not subject.namespace_uid or not subject.service_account_uid:
            raise ApplyError("APPLY_SUBJECT_UNPREPARED")
        if _assignments(body):
            raise ApplyError("APPLY_EXPLICIT_ALLOCATION_UNSUPPORTED")
        labels = meta.setdefault("labels", {})
        if any(k in labels and labels[k] != v for k, v in context.owner_labels.items()):
            raise ApplyError("APPLY_OWNER_CONFLICT")
        labels.update(context.owner_labels)
        meta.setdefault("annotations", {})[_MARKER] = execution.operation_id
        if body["kind"] == "Secret":
            data = body.setdefault("data", {})
            for k, v in body.pop("stringData", {}).items():
                if not isinstance(v, str):
                    raise ApplyError("INVALID_SECRET")
                data[k] = base64.b64encode(v.encode()).decode()
        if body["kind"] in _CONTROLLERS:
            spec = body.get("spec", {})
            template = spec.get("template", {})
            if (
                template.get("spec", {}).get("serviceAccountName") != subject.name
                or any(
                    template.get("metadata", {}).get("labels", {}).get(k) != v for k, v in context.owner_labels.items()
                )
                or not spec.get("selector")
            ):
                raise ApplyError("APPLY_IDENTITY_UNSTAMPED")
        if body["kind"] in _CONTROLLERS:
            pod = body["spec"]["template"]["spec"]
            if "serviceAccount" in pod or pod.get("hostNetwork", False) is not False:
                raise ApplyError("APPLY_IDENTITY_CONFLICT")
            selected = _selector(json.dumps(body["spec"]["selector"], sort_keys=True, separators=(",", ":")))
            if not _select(selected, body["spec"]["template"]["metadata"]["labels"]):
                raise ApplyError("APPLY_SELECTOR_CONFLICT")
        descriptor = ResourcePlan(
            body["apiVersion"],
            body["kind"],
            meta["namespace"],
            meta["name"],
            _hash(body),
            "0" * 64,
            tuple(sorted(set(body) - {"apiVersion", "kind", "metadata"})),
            tuple(sorted(labels)),
            tuple(sorted(meta["annotations"])),
            template_sha256(body["spec"]["template"]) if body["kind"] in _CONTROLLERS else "",
            placement_sha256(body["spec"]["template"]["spec"]) if body["kind"] in _CONTROLLERS else "",
            body.get("spec", {}).get("replicas") if body["kind"] in ("Deployment", "StatefulSet") else None,
        )
        descriptor = replace(descriptor, projection_sha256=_hash(projection(body, descriptor)))
        bodies.append(body)
        descriptors.append(descriptor)
    plan = CompiledAppPlan(execution, identity_sha256, tuple(descriptors), placement)
    return plan, tuple(bodies)


class KubernetesAppAdapter(KubernetesReadAdapter):
    def request(self, descriptor: ResourcePlan, *, method: str = "GET", body: Any = None) -> dict[str, Any] | None:
        if type(descriptor) is not ResourcePlan or method not in ("GET", "POST", "PATCH"):
            raise ApplyError("INVALID_APPLY_REQUEST")
        path = descriptor.collection if method == "POST" else descriptor.path
        _checkpoint(self.checkpoint)
        try:
            if not self.credentials.valid:
                self.credentials.refresh(self.credential_request)
            _checkpoint(self.checkpoint)
            token = self.credentials.token
            if not isinstance(token, str) or not token or len(token) > 16384 or any(c in token for c in "\r\n"):
                raise ApplyError("CREDENTIAL_UNAVAILABLE")
            data = (
                json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                if body is not None
                else None
            )
            if data is not None and len(data) > MAX_BYTES:
                raise ApplyError("APPLY_BOUND_EXCEEDED")
            response = _http(
                self.base_url + path,
                method=method,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json",
                    "Content-Type": "application/json-patch+json" if method == "PATCH" else "application/json",
                },
                data=data,
                context=self.tls,
            )
            if response.status == 404 and method == "GET":
                _checkpoint(self.checkpoint)
                return None
            if response.status not in (200, 201):
                value = json.loads(response.data)
                if (
                    method != "GET"
                    and response.status in _REJECTIONS
                    and isinstance(value, dict)
                    and (
                        value.get("apiVersion") == "v1"
                        and value.get("kind") == "Status"
                        and value.get("status") == "Failure"
                        and type(value.get("code")) is int
                        and value["code"] == response.status
                        and value.get("reason") == _REJECTIONS[response.status]
                    )
                ):
                    raise RejectedRequest(response.status, value["reason"], _hash(value))
                raise ApplyError("APPLY_TRANSPORT_REFUSED")
            value = json.loads(response.data)
            if not isinstance(value, dict):
                raise ApplyError("APPLY_RESPONSE_INVALID")
        except RejectedRequest:
            raise
        except Exception:
            _checkpoint(self.checkpoint)
            raise ApplyError("APPLY_TRANSPORT_UNCONFIRMED") from None
        if method == "GET":
            _checkpoint(self.checkpoint)
        return value


class GKEAppApply:
    def __init__(self, context: GKEObservationContext, *, clients: Any = None, kubernetes_factory: Any = None) -> None:
        self.context = context
        self.observer = GKEIdentityObserver(
            context, clients=clients, kubernetes_factory=kubernetes_factory or KubernetesAppAdapter
        )

    def close(self) -> None:
        self.observer.close()

    def __enter__(self) -> GKEAppApply:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _admit(self, checkpoint: Callable[[], None], placement: PlacementAcceptance | None = None) -> Any:
        _checkpoint(checkpoint)
        cluster, pools = self.observer._cluster(
            checkpoint, accepted_node_pools=placement.allowed_node_pools if placement is not None else None
        )
        if placement is not None and (
            placement.cluster_resource != self.context.cluster_resource
            or placement.native_cluster_id != cluster.id
            or placement.autopilot != bool(cluster.autopilot.enabled)
            or not set(placement.allowed_node_pools) <= set(pools)
        ):
            raise ApplyError("ACCEPTED_PLACEMENT_UNAVAILABLE")
        adapter = self.observer._kubernetes_factory(
            cluster.endpoint, cluster.master_auth.cluster_ca_certificate, self.observer._credentials, checkpoint
        )
        subjects = self.observer._subjects(adapter, checkpoint)
        if not subjects or not all(row.annotation_matches for row in subjects):
            raise ApplyError("APPLY_IDENTITY_CONFIGURATION_UNOBSERVED")
        _checkpoint(checkpoint)
        return adapter

    def capture_placement(self, plan: CompiledAppPlan, *, checkpoint: Callable[[], None]) -> CompiledAppPlan:
        """Read native admission before durable acceptance; metadata is not authority."""
        if type(plan) is not CompiledAppPlan or plan.placement is not None:
            raise ApplyError("PLACEMENT_ALREADY_ACCEPTED")
        _checkpoint(checkpoint)
        cluster, pools = self.observer._cluster(checkpoint, accepted_node_pools=())
        acceptance = PlacementAcceptance(
            self.context.cluster_resource,
            cluster.id,
            bool(cluster.autopilot.enabled),
            pools,
            controller_binding_sha256(plan.execution, plan.identity_sha256, plan.resources),
        )
        self._admit(checkpoint, acceptance)
        _checkpoint(checkpoint)
        return replace(plan, placement=acceptance)

    def _observe(self, body: Any, descriptor: ResourcePlan, uid: str = "", *, desired: bool = True) -> ObservedResource:
        meta = body.get("metadata", {})
        if (
            body.get("kind") != descriptor.kind
            or body.get("apiVersion") != descriptor.api_version
            or meta.get("name") != descriptor.name
            or meta.get("namespace") != descriptor.namespace
            or (uid and meta.get("uid") != uid)
            or any(meta.get("labels", {}).get(k) != v for k, v in self.context.owner_labels.items())
            or (desired and _hash(projection(body, descriptor)) != descriptor.projection_sha256)
        ):
            raise ApplyError("APPLY_RESPONSE_UNVERIFIED")
        template = body.get("spec", {}).get("template")
        return ObservedResource(
            descriptor,
            meta.get("uid"),
            meta.get("resourceVersion"),
            meta.get("generation") if descriptor.kind in _CONTROLLERS else None,
            template_sha256(template) if template is not None else "",
            placement_sha256(template["spec"]) if template is not None else "",
            _hash(projection(body, descriptor)),
            _hash(_assignments(body)),
        )

    def apply(
        self,
        plan: CompiledAppPlan,
        bodies: tuple[dict[str, Any], ...],
        *,
        ledger: ApplyLedger,
        checkpoint: Callable[[], None],
        commit_submission: Callable[[ApplyIntent], SubmissionReceipt],
        commit_evidence: Callable[[NativeEvidence], EvidenceReceipt],
        commit_observation: Callable[[NativeEvidence], EvidenceReceipt],
        commit_rejection: Callable[[NativeRejection], EvidenceReceipt],
    ) -> AppApplyReceipt:
        if type(plan) is not CompiledAppPlan or type(ledger) is not ApplyLedger or not callable(checkpoint):
            raise ApplyError("INVALID_APPLY_INPUT")
        if plan.placement is None:
            raise ApplyError("PLACEMENT_ACCEPTANCE_REQUIRED")
        compiled, bound = compile_app_plan(
            self.context, plan.execution, list(bodies), identity_sha256=plan.identity_sha256, placement=plan.placement
        )
        if compiled != plan or tuple(bodies) != bound:
            raise ApplyError("APPLY_COMPILED_BYTES_CHANGED")
        ledger_from_payload(ledger_payload(ledger))
        if ledger.pending and ledger.pending.phase == ApplyPhase.SENT:
            raise ApplyError("APPLY_UNKNOWN_NO_RESEND", ledger)
        if set(r.resource.path for r in ledger.resources) - {r.path for r in plan.resources}:
            raise ApplyError("APPLY_RESOURCE_REMOVAL_REQUIRES_REVIEW", ledger)
        rows = {r.resource.path: r for r in ledger.resources}
        receipt_journal = ""
        receipt_version = 0

        def correlate(receipt: SubmissionReceipt | EvidenceReceipt) -> None:
            nonlocal receipt_journal, receipt_version
            if receipt_journal and (
                receipt.journal_id != receipt_journal or receipt.journal_version <= receipt_version
            ):
                raise ApplyError("APPLY_RECEIPT_FENCE_CHANGED")
            receipt_journal, receipt_version = receipt.journal_id, receipt.journal_version

        try:
            ordered = list(zip(plan.resources, bodies, strict=True))
            if ledger.pending:
                pending_path = ledger.pending.resource.path
                ordered.sort(key=lambda pair: pair[0].path != pending_path)
            for descriptor, body in ordered:
                adapter = self._admit(checkpoint, plan.placement)
                prior = rows.get(descriptor.path)
                pending = ledger.pending
                rejected_submission = ledger.rejection.intent.submission_id if ledger.rejection else ""
                if pending and pending.phase == ApplyPhase.REJECTED:
                    pending = None
                if pending and pending.resource.path != descriptor.path:
                    # Finish only this acknowledged original object before other effects.
                    raise ApplyError("APPLY_PENDING_RESOURCE_REQUIRED")
                native = adapter.request(descriptor)
                _checkpoint(checkpoint)
                if prior is None and native is not None:
                    raise ApplyError("APPLY_UNOWNED_RESOURCE")
                if prior is not None and native is None:
                    raise ApplyError("APPLY_ORIGINAL_RESOURCE_GONE")
                if prior is not None:
                    current_prior = self._observe(
                        native,
                        prior.resource,
                        prior.uid,
                        desired=not (pending and pending.phase == ApplyPhase.EVIDENCE),
                    )
                    if current_prior.native_assignments_sha256 != prior.native_assignments_sha256:
                        raise ApplyError("APPLY_NATIVE_ALLOCATION_CHANGED")
                if pending and pending.phase == ApplyPhase.EVIDENCE:
                    if prior is None:
                        raise ApplyError("APPLY_EVIDENCE_UID_MISSING")
                    observed = self._observe(native, descriptor, prior.uid)
                    receipt = commit_observation(NativeEvidence(pending, observed))
                    self._evidence_receipt(receipt, plan, NativeEvidence(pending, observed))
                    correlate(receipt)
                    rows[descriptor.path] = observed
                    ledger = ApplyLedger(tuple(rows.values()))
                    continue
                if prior is not None and prior.resource == descriptor:
                    observed = self._observe(native, descriptor, prior.uid)
                    rows[descriptor.path] = observed
                    continue
                method = "POST" if prior is None else "PATCH"
                request: Any
                if prior is None:
                    request = body
                else:
                    request = [
                        {"op": "test", "path": "/metadata/uid", "value": prior.uid},
                        {
                            "op": "test",
                            "path": "/metadata/resourceVersion",
                            "value": native["metadata"]["resourceVersion"],
                        },
                    ]
                    if set(prior.resource.fields) - set(descriptor.fields):
                        raise ApplyError("APPLY_FIELD_REMOVAL_REQUIRES_REVIEW")
                    if set(prior.resource.label_keys) - set(descriptor.label_keys) or set(
                        prior.resource.annotation_keys
                    ) - set(descriptor.annotation_keys):
                        raise ApplyError("APPLY_METADATA_REMOVAL_REQUIRES_REVIEW")
                    for key in descriptor.fields:
                        value = deepcopy(body[key])
                        if key == "spec":
                            value.update(_assignments(native))
                        request.append({"op": "add", "path": "/" + key, "value": value})
                    for category, keys in (
                        ("labels", descriptor.label_keys),
                        ("annotations", descriptor.annotation_keys),
                    ):
                        for key in keys:
                            escaped = key.replace("~", "~0").replace("/", "~1")
                            request.append(
                                {
                                    "op": "add",
                                    "path": "/metadata/" + category + "/" + escaped,
                                    "value": body["metadata"][category][key],
                                }
                            )
                submission_id = _hash((plan.sha256, descriptor.path, method, _hash(request), rejected_submission))[:32]
                submission_id = str(UUID(submission_id))
                intent = ApplyIntent(
                    submission_id,
                    ApplyPhase.UNSENT,
                    descriptor,
                    method,
                    _hash(request),
                    prior.uid if prior else "",
                    native["metadata"]["resourceVersion"] if prior else "",
                )
                if pending and pending != intent:
                    raise ApplyError("APPLY_PENDING_INTENT_CHANGED")
                for phase in (ApplyPhase.UNSENT, ApplyPhase.SENT):
                    intent = replace(intent, phase=phase)
                    ledger = ApplyLedger(tuple(rows.values()), intent)
                    submission_receipt = commit_submission(intent)
                    if (
                        type(submission_receipt) is not SubmissionReceipt
                        or submission_receipt.phase != phase
                        or submission_receipt.operation_id != plan.execution.operation_id
                        or submission_receipt.record_sha256 != intent.sha256
                        or type(submission_receipt.journal_version) is not int
                        or submission_receipt.journal_version < 1
                    ):
                        raise ApplyError("APPLY_SUBMISSION_UNCONFIRMED")
                    _guid(submission_receipt.journal_id)
                    correlate(submission_receipt)
                    ledger = ApplyLedger(tuple(rows.values()), intent)
                    _checkpoint(checkpoint)
                try:
                    response = adapter.request(descriptor, method=method, body=request)
                except RejectedRequest as rejected:
                    rejection = NativeRejection(intent, rejected.code, rejected.reason, rejected.response_sha256)
                    receipt = commit_rejection(rejection)
                    self._evidence_receipt(receipt, plan, rejection)
                    correlate(receipt)
                    ledger = ApplyLedger(tuple(rows.values()), replace(intent, phase=ApplyPhase.REJECTED), rejection)
                    _checkpoint(checkpoint)
                    raise ApplyError("APPLY_REQUEST_REJECTED") from None
                observed = self._observe(response, descriptor, prior.uid if prior else "", desired=False)
                evidence = NativeEvidence(intent, observed)
                receipt = commit_evidence(evidence)
                self._evidence_receipt(receipt, plan, evidence)
                correlate(receipt)
                rows[descriptor.path] = observed
                intent = replace(intent, phase=ApplyPhase.EVIDENCE)
                ledger = ApplyLedger(tuple(rows.values()), intent)
                _checkpoint(checkpoint)
                if observed.native_projection_sha256 != descriptor.projection_sha256:
                    raise ApplyError("APPLY_ADMISSION_MUTATION_UNREVIEWED")
                adapter = self._admit(checkpoint, plan.placement)
                current = self._observe(adapter.request(descriptor), descriptor, observed.uid)
                _checkpoint(checkpoint)
                evidence = NativeEvidence(intent, current)
                receipt = commit_observation(evidence)
                self._evidence_receipt(receipt, plan, evidence)
                correlate(receipt)
                rows[descriptor.path] = current
                ledger = ApplyLedger(tuple(rows.values()))
            adapter = self._admit(checkpoint, plan.placement)
            for descriptor in plan.resources:
                current = self._observe(adapter.request(descriptor), descriptor, rows[descriptor.path].uid)
                rows[descriptor.path] = current
                _checkpoint(checkpoint)
            _checkpoint(checkpoint)
            return AppApplyReceipt(plan.sha256, ApplyLedger(tuple(rows.values())), True)
        except Exception as error:
            reason = str(error) if isinstance(error, (ApplyError, GKEObservationError)) else "APPLY_UNCONFIRMED"
            raise ApplyError(reason, ledger) from None

    @staticmethod
    def _evidence_receipt(
        receipt: EvidenceReceipt, plan: CompiledAppPlan, evidence: NativeEvidence | NativeRejection
    ) -> None:
        if (
            type(receipt) is not EvidenceReceipt
            or receipt.operation_id != plan.execution.operation_id
            or receipt.evidence_sha256 != evidence.sha256
            or type(receipt.journal_version) is not int
            or receipt.journal_version < 1
        ):
            raise ApplyError("APPLY_EVIDENCE_UNCONFIRMED")
        _guid(receipt.journal_id)
