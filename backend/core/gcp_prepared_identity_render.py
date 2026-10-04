"""Private render metadata; only a current receipt-bound caller may apply output."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, NoReturn
from uuid import UUID


class PreparedIdentityRenderError(ValueError):
    pass


def _refuse(reason: str) -> NoReturn:
    raise PreparedIdentityRenderError(reason)


def _guid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        _refuse("PREPARED_RENDER_IDENTITY_INVALID")


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        _refuse("PREPARED_RENDER_NAME_INVALID")


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        _refuse("PREPARED_RENDER_FENCE_INVALID")


def _hash(value):
    try:
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        _refuse("PREPARED_RENDER_SPEC_INVALID")
    if len(body) > 2 * 1024 * 1024:
        _refuse("PREPARED_RENDER_SPEC_BOUND_EXCEEDED")
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class PreparedGCPIdentity:
    deployment_guid: str
    organization_guid: str
    app_guid: str
    environment_guid: str
    cluster_guid: str
    provider_guid: str
    namespace: str
    namespace_uid: str
    service_account_name: str
    service_account_uid: str
    gsa_email: str
    gsa_unique_id: str
    preparation_journal_guid: str
    preparation_journal_version: int
    operation_id: str
    generation: int
    preparation_target_sha256: str
    source_snapshot_sha256: str
    desired_union_sha256: str
    iam_journal_guid: str
    iam_journal_version: int

    def __post_init__(self):
        for field in (
            "deployment_guid",
            "organization_guid",
            "app_guid",
            "environment_guid",
            "cluster_guid",
            "provider_guid",
            "namespace_uid",
            "service_account_uid",
            "preparation_journal_guid",
            "operation_id",
            "iam_journal_guid",
        ):
            _guid(getattr(self, field))
        _name(self.namespace)
        _name(self.service_account_name)
        if (
            not isinstance(self.gsa_email, str)
            or not re.fullmatch(
                r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com",
                self.gsa_email,
            )
            or not isinstance(self.gsa_unique_id, str)
            or not re.fullmatch(r"[1-9][0-9]{0,31}", self.gsa_unique_id)
        ):
            _refuse("PREPARED_RENDER_GSA_INVALID")
        for field in ("preparation_journal_version", "generation", "iam_journal_version"):
            if type(getattr(self, field)) is not int or getattr(self, field) < 1:
                _refuse("PREPARED_RENDER_FENCE_INVALID")
        for field in ("preparation_target_sha256", "source_snapshot_sha256", "desired_union_sha256"):
            _digest(getattr(self, field))

    @property
    def owner_labels(self):
        return {
            "astrolift.io/managed-by": "platform",
            "astrolift.io/organization-id": self.organization_guid,
            "astrolift.io/app-id": self.app_guid,
            "astrolift.io/cluster-id": self.cluster_guid,
        }

    @property
    def fingerprint(self):
        return _hash({"schema": "astrolift.prepared-gcp-render.v1", **asdict(self)})


def prepared_identity_for_deployment(deployment, store, reservation, *, iam, checkpoint, iam_checkpoint):
    """Read actual completed journals; supplied callbacks still carry current authority."""
    from astrolift_services.gcp_workload_identity_journal import JournalCheckpoint, OperationIdentity

    if not callable(iam_checkpoint):
        _refuse("PREPARED_RENDER_CURRENT_IAM_REQUIRED")
    with store._locked(store._operation(reservation), checkpoint, iam=iam) as (row, accepted, iam_row):
        row = store._bound(row, reservation)
        store._annotation_current(row, accepted, iam, iam_row)
        if (
            row.state != "OBSERVED"
            or row.observed_revision != row.desired_revision
            or row.observed_at is None
        ):
            _refuse("PREPARED_RENDER_CONFIGURATION_UNOBSERVED")
        current = store._checkpoint_context
        if (
            iam_checkpoint(
                JournalCheckpoint(
                    current.organization,
                    current.team,
                    current.project,
                    current.app,
                    current.cluster,
                    current.provider,
                    iam_row,
                    iam[0].target,
                    OperationIdentity(
                        str(iam_row.operation_id),
                        iam_row.workflow_id,
                        iam_row.execution_id,
                        iam_row.desired_revision,
                        iam_row.desired_union_sha256,
                        iam_row.authority_reference_sha256,
                    ),
                )
            )
            is not None
        ):
            _refuse("PREPARED_RENDER_CURRENT_IAM_UNCONFIRMED")
        operation = store._accepted_operation(accepted, row)
        aliases = [
            subject
            for subject in operation.template.subjects
            if str(deployment.app_environment.guid) in subject.environment_ids
        ]
        if len(aliases) != 1:
            _refuse("PREPARED_RENDER_ENVIRONMENT_CHANGED")
        subject = aliases[0]
        objects = store._ledger(row).objects
        namespace = next(
            (
                obj
                for obj in objects
                if obj.kind == "Namespace" and obj.path == f"/api/v1/namespaces/{subject.namespace}"
            ),
            None,
        )
        account = next(
            (
                obj
                for obj in objects
                if obj.kind == "ServiceAccount"
                and obj.path == f"/api/v1/namespaces/{subject.namespace}/serviceaccounts/{subject.name}"
            ),
            None,
        )
        if namespace is None or account is None:
            _refuse("PREPARED_RENDER_UID_UNOBSERVED")
        native = store.target.context.identity
        identity = PreparedGCPIdentity(
            str(deployment.guid),
            native.organization_id,
            native.app_id,
            str(deployment.app_environment.guid),
            native.cluster_id,
            store.target.provider_id,
            subject.namespace,
            namespace.uid,
            subject.name,
            account.uid,
            native.email,
            native.service_account_unique_id,
            str(row.guid),
            row.version,
            str(row.operation_id),
            row.generation,
            row.target_sha256,
            operation.template.source_snapshot_sha256,
            row.derived_native_union_sha256,
            str(iam_row.guid),
            iam_row.version,
        )
        assert_deployment_identity(deployment, deployment.app_environment.tenant_cluster, identity)
        return identity


def assert_deployment_identity(deployment, cluster, identity):
    if type(identity) is not PreparedGCPIdentity or cluster is None:
        _refuse("PREPARED_RENDER_IDENTITY_REQUIRED")
    env, app = deployment.app_environment, deployment.registered_app
    actual = (
        str(deployment.guid),
        str(app.organization.guid),
        str(app.guid),
        str(env.guid),
        str(cluster.guid),
        str(cluster.provider_plugin.guid),
    )
    expected = (
        identity.deployment_guid,
        identity.organization_guid,
        identity.app_guid,
        identity.environment_guid,
        identity.cluster_guid,
        identity.provider_guid,
    )
    if (
        actual != expected
        or env.registered_app_id != app.pk
        or env.tenant_cluster_id != cluster.pk
        or cluster.provider_plugin.slug != "gcp"
    ):
        _refuse("PREPARED_RENDER_TARGET_CHANGED")


_SUPPORTED = {"Deployment", "StatefulSet", "DaemonSet"}
_ANCILLARY = {
    "Service": "v1",
    "Secret": "v1",
    "ConfigMap": "v1",
    "PersistentVolumeClaim": "v1",
    "Ingress": "networking.k8s.io/v1",
    "NetworkPolicy": "networking.k8s.io/v1",
    "PodMonitor": "monitoring.coreos.com/v1",
    "HTTPRoute": "gateway.networking.k8s.io/v1",
    "Gateway": "gateway.networking.k8s.io/v1",
}
_IDENTITY_KEYS = {
    "iam.gke.io/gcp-service-account",
    "eks.amazonaws.com/role-arn",
    "azure.workload.identity/client-id",
}


def _metadata(metadata: Any, identity: PreparedGCPIdentity) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        _refuse("PREPARED_RENDER_METADATA_INVALID")
    labels, annotations = metadata.get("labels", {}), metadata.get("annotations", {})
    if not isinstance(labels, dict) or not isinstance(annotations, dict):
        _refuse("PREPARED_RENDER_METADATA_INVALID")
    if any(key in labels and labels[key] != value for key, value in identity.owner_labels.items()):
        _refuse("PREPARED_RENDER_OWNER_CONFLICT")
    marker = annotations.get("astrolift.io/prepared-identity-sha256", identity.fingerprint)
    if marker != identity.fingerprint:
        _refuse("PREPARED_RENDER_IDENTITY_CONFLICT")
    for key in _IDENTITY_KEYS & annotations.keys():
        if key != "iam.gke.io/gcp-service-account" or annotations[key] != identity.gsa_email:
            _refuse("PREPARED_RENDER_IDENTITY_CONFLICT")
    return metadata


def validate_prepared_resources(resources, identity):
    if (
        type(identity) is not PreparedGCPIdentity
        or not isinstance(resources, list)
        or not 1 <= len(resources) <= 256
    ):
        _refuse("PREPARED_RENDER_RESOURCE_BOUND_INVALID")
    controllers = []
    physical = set()
    for resource in resources:
        if not isinstance(resource, dict):
            _refuse("PREPARED_RENDER_SPEC_INVALID")
        _hash(resource)
        kind = resource.get("kind")
        metadata = _metadata(resource.get("metadata"), identity)
        name = metadata.get("name")
        _name(name)
        if (kind, name) in physical:
            _refuse("PREPARED_RENDER_RESOURCE_DUPLICATE")
        physical.add((kind, name))
        if kind == "Namespace":
            if (
                resource.get("apiVersion") != "v1"
                or name != identity.namespace
                or metadata.get("uid", identity.namespace_uid) != identity.namespace_uid
            ):
                _refuse("PREPARED_RENDER_NAMESPACE_CONFLICT")
            continue
        if metadata.get("namespace", identity.namespace) != identity.namespace:
            _refuse("PREPARED_RENDER_NAMESPACE_CONFLICT")
        if kind == "ServiceAccount":
            if (
                resource.get("apiVersion") != "v1"
                or name != identity.service_account_name
                or metadata.get("uid", identity.service_account_uid) != identity.service_account_uid
            ):
                _refuse("PREPARED_RENDER_SERVICE_ACCOUNT_CONFLICT")
            continue
        if kind in {"HorizontalPodAutoscaler", "Job", "CronJob", "ReplicaSet", "Pod"}:
            _refuse("PREPARED_RENDER_WORKLOAD_UNSUPPORTED")
        if kind not in _SUPPORTED:
            if kind not in _ANCILLARY or resource.get("apiVersion") != _ANCILLARY[kind]:
                _refuse("PREPARED_RENDER_RESOURCE_UNSUPPORTED")
            continue
        if resource.get("apiVersion") != "apps/v1":
            _refuse("PREPARED_RENDER_WORKLOAD_UNSUPPORTED")
        if any(key in metadata for key in ("uid", "generation", "resourceVersion", "ownerReferences")):
            _refuse("PREPARED_RENDER_CONTROLLER_IDENTITY_UNESTABLISHED")
        spec = resource.get("spec", {})
        if not isinstance(spec, dict) or not isinstance(spec.get("template"), dict):
            _refuse("PREPARED_RENDER_WORKLOAD_INVALID")
        if kind != "DaemonSet" and (
            type(spec.get("replicas")) is not int or not 1 <= spec["replicas"] <= 256
        ):
            _refuse("PREPARED_RENDER_REPLICAS_UNSUPPORTED")
        if kind == "DaemonSet" and "replicas" in spec:
            _refuse("PREPARED_RENDER_REPLICAS_UNSUPPORTED")
        template = spec["template"]
        _metadata(template.get("metadata", {}), identity)
        pod = template.get("spec")
        if not isinstance(pod, dict) or not isinstance(spec.get("selector"), dict) or not spec["selector"]:
            _refuse("PREPARED_RENDER_WORKLOAD_INVALID")
        if (
            pod.get("serviceAccountName", identity.service_account_name) != identity.service_account_name
            or "serviceAccount" in pod
            or pod.get("hostNetwork", False) is not False
        ):
            _refuse("PREPARED_RENDER_SERVICE_ACCOUNT_CONFLICT")
        from gcp.gke_identity_runtime import GKEObservationError, _select, _selector

        try:
            selector = _selector(json.dumps(spec["selector"], sort_keys=True, separators=(",", ":")))
            if not _select(selector, template.get("metadata", {}).get("labels", {})):
                _refuse("PREPARED_RENDER_SELECTOR_MISMATCH")
        except GKEObservationError:
            _refuse("PREPARED_RENDER_SELECTOR_UNSUPPORTED")
        controllers.append(resource)
    if not 1 <= len(controllers) <= 16:
        _refuse("PREPARED_RENDER_CONTROLLER_BOUND_INVALID")


def inject_prepared_identity(resources, identity):
    validate_prepared_resources(resources, identity)
    result = deepcopy([r for r in resources if r["kind"] not in {"Namespace", "ServiceAccount"}])
    for resource in result:
        resource["metadata"]["namespace"] = identity.namespace
        if resource["kind"] in _SUPPORTED:
            resource["metadata"].setdefault("labels", {}).update(identity.owner_labels)
            resource["metadata"].setdefault("annotations", {})["astrolift.io/prepared-identity-sha256"] = (
                identity.fingerprint
            )
            template = resource["spec"]["template"]
            template.setdefault("metadata", {}).setdefault("labels", {}).update(identity.owner_labels)
            template.setdefault("spec", {})["serviceAccountName"] = identity.service_account_name
    return result


@dataclass(frozen=True)
class RenderedGCPController:
    namespace: str
    kind: str
    name: str
    service_account_name: str
    request_sha256: str
    template_sha256: str
    placement_sha256: str
    selector_json: str
    replicas: int | None


def prepared_controller_fingerprints(resources, identity):
    """Pre-apply reviewed fingerprints, never original UID or rollout targets."""
    from gcp.gke_identity_runtime import placement_sha256, template_sha256

    validate_prepared_resources(resources, identity)
    result = []
    for resource in resources:
        if resource["kind"] not in _SUPPORTED:
            continue
        template = resource["spec"]["template"]
        if template["spec"].get("serviceAccountName") != identity.service_account_name or any(
            template.get("metadata", {}).get("labels", {}).get(key) != value
            for key, value in identity.owner_labels.items()
        ):
            _refuse("PREPARED_RENDER_IDENTITY_UNSTAMPED")
        result.append(
            RenderedGCPController(
                identity.namespace,
                resource["kind"],
                resource["metadata"]["name"],
                identity.service_account_name,
                _hash(resource),
                template_sha256(template),
                placement_sha256(template["spec"]),
                json.dumps(resource["spec"]["selector"], sort_keys=True, separators=(",", ":")),
                resource["spec"].get("replicas") if resource["kind"] != "DaemonSet" else None,
            )
        )
    return tuple(result)
