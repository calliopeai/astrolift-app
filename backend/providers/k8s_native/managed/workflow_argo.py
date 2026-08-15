"""Argo WorkflowTemplate implementation of the portable workflow-engine contract."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

from _sdk._telemetry import driver_op
from _sdk.k8s_naming import app_namespace, dns_label
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from k8s_native.managed._handle import ParsedHandle
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle

KIND = "workflow_engine"
VARIANT = "argo_workflows"
API_VERSION = "argoproj.io/v1alpha1"
RESOURCE_KIND = "WorkflowTemplate"
WORKFLOW_KIND = "Workflow"
CRON_KIND = "CronWorkflow"
EVENT_KIND = "WorkflowEventBinding"
REQUIRED_CRDS = (
    "workflows.argoproj.io",
    "workflowtemplates.argoproj.io",
    "cronworkflows.argoproj.io",
    "workfloweventbindings.argoproj.io",
)

_OWNER = "app.kubernetes.io/managed-by"
_OWNER_ID = "astrolift.io/managed-service-id"
_CHILDREN = "astrolift.io/managed-workflow-children"
_EXECUTOR_COMPONENT = "argo-workflow-executor"
_SNAPSHOT_OF = "astrolift.io/workflow-snapshot-of"
_SNAPSHOT_SHA256 = "astrolift.io/workflow-snapshot-sha256"
_SNAPSHOT_FORMAT = "astrolift.io/workflow-snapshot-format"
_DELETION_PROTECTION = "astrolift.io/deletion-protection"
_SNAPSHOT_DATA_KEY = "declaration.json"
_SNAPSHOT_FORMAT_VERSION = "argo-workflows/v1"
_LABEL_KEY = re.compile(
    r"^(?:[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$",
)
_LABEL_VALUE = re.compile(r"^(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")
_CONFIG_FIELDS = {
    "workflow_spec",
    "cron_workflows",
    "event_bindings",
    "labels",
    "annotations",
    "deletion_protection",
    "adopt_existing",
    "expected_existing_uid",
}
_CHILD_FIELDS = {
    "name",
    "spec",
    "labels",
    "annotations",
    "adopt_existing",
    "expected_existing_uid",
}
_UNSAFE_BOOLEAN_FIELDS = {
    "hostNetwork",
    "hostPID",
    "hostIPC",
    "privileged",
    "allowPrivilegeEscalation",
    "hostProcess",
}
_REMOVED_V4_WORKFLOW_FIELDS = {"mutex", "podPriority", "schedule", "semaphore"}


@dataclass(frozen=True)
class ArgoWorkflowsConfig:
    cluster_driver: Any = None
    namespace: str | None = None
    watch_all_namespaces: bool = True
    managed_namespaces: tuple[str, ...] = ()
    argo_server_url: str = ""
    service_account_name: str = "argo-workflow"
    allow_service_account_override: bool = False
    allowed_service_accounts: tuple[str, ...] = ()
    allow_workflow_template_refs: bool = False
    allow_cluster_template_refs: bool = False
    trusted_workflow_template_uids: dict[str, str] | None = None
    allow_resource_templates: bool = False
    allow_executor_plugins: bool = False
    allow_external_http_templates: bool = False
    allow_host_access: bool = False
    allow_privileged_pods: bool = False
    allow_pod_spec_patch: bool = False
    allow_tagged_images: bool = False
    allowed_image_prefixes: tuple[str, ...] = ()
    default_parallelism: int = 10
    max_parallelism: int = 50
    default_active_deadline_seconds: int = 3600
    max_active_deadline_seconds: int = 86400
    default_ttl_seconds: int = 86400
    max_ttl_seconds: int = 604800


class ArgoWorkflowsDriver(ManagedServiceDriver):
    """Own a namespace-local WorkflowTemplate and its schedules/event bindings."""

    def __init__(self, *, config: ArgoWorkflowsConfig) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="workflow_argo",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._require_driver()
            if not spec.managed_service_id:
                raise ValueError("Argo Workflows requires a managed_service_id for safe ownership")
            namespace = self._config.namespace or app_namespace(
                organization_slug=spec.organization_slug,
                app_slug=spec.app_slug,
            )
            name = dns_label(
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "workflow",
            )
            self._assert_namespace_managed(namespace)
            cfg = self._normalize(spec.config)
            referenced_specs = self._validate_references(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                cfg=cfg,
            )
            self._assert_service_accounts(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                cfg=cfg,
                referenced_specs=referenced_specs,
            )
            self._assert_adoptable(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
                managed_service_id=spec.managed_service_id,
                cfg=cfg,
            )
            manifests = self._manifests(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
                cfg=cfg,
                owner=self._owner_from_spec(spec),
            )
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_argo_workflows_config"])

        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id,
            namespace=namespace,
            name=name,
        )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            manifests,
        )
        if not result.ok:
            return ProvisionResult(
                False,
                handle,
                "Argo workflow resources were rejected",
                result.summary(),
            )
        return ProvisionResult(
            True,
            handle,
            f"Argo WorkflowTemplate {namespace}/{name} reconciled",
            ready=True,
        )

    @driver_op(cloud="k8s_native", driver="workflow_argo")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require_driver()
            parsed = self._parsed(spec.handle)
            self._assert_namespace_managed(parsed.namespace)
            current = self._template(parsed)
            if current is None:
                return UpdateResult(
                    False,
                    spec.handle,
                    "Argo WorkflowTemplate does not exist",
                    ["resource_not_found"],
                    retryable=False,
                )
            owner = self._assert_owned(current, RESOURCE_KIND)
            cfg = self._normalize(spec.config)
            referenced_specs = self._validate_references(
                cluster_id=parsed.cluster_id,
                namespace=parsed.namespace,
                cfg=cfg,
            )
            self._assert_service_accounts(
                cluster_id=parsed.cluster_id,
                namespace=parsed.namespace,
                cfg=cfg,
                referenced_specs=referenced_specs,
            )
            manifests = self._manifests(
                cluster_id=parsed.cluster_id,
                namespace=parsed.namespace,
                name=parsed.name,
                cfg=cfg,
                owner=self._owner_from_labels(current),
            )
            stale = self._stale_children(current, manifests)
            active = self._active_cron_children(parsed, stale, owner)
            if active:
                raise ValueError(
                    "cannot remove active Argo CronWorkflows: " + ", ".join(active),
                )
            stale_stubs = self._owned_child_stubs(parsed, stale, owner)
        except (TypeError, ValueError) as exc:
            return UpdateResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_argo_workflows_update"],
                retryable=False,
            )
        if stale_stubs:
            deleted = self._config.cluster_driver.delete_manifests(
                parsed.cluster_id,
                parsed.namespace,
                stale_stubs,
                propagation_policy="Orphan",
            )
            if not deleted.ok:
                return UpdateResult(
                    False,
                    spec.handle,
                    "could not prune removed Argo workflow children",
                    deleted.summary(),
                )
        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            manifests,
        )
        if not result.ok:
            return UpdateResult(
                False,
                spec.handle,
                "Argo workflow update was rejected",
                result.summary(),
            )
        return UpdateResult(True, spec.handle, f"Argo WorkflowTemplate {parsed.name} reconciled")

    @driver_op(
        cloud="k8s_native",
        driver="workflow_argo",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        try:
            self._require_driver()
            parsed = self._parsed(spec.handle)
            template = self._template(parsed)
            if template is None:
                return DeprovisionResult(True, spec.handle, "Argo WorkflowTemplate already absent")
            owner = self._assert_owned(template, RESOURCE_KIND)
            children = self._decode_children(template)
            workflows = self._owned_workflows(parsed, owner)
            active = sorted(
                {
                    *self._active_cron_children(parsed, children, owner),
                    *self._active_workflow_names(workflows),
                },
            )
            stubs = self._owned_child_stubs(parsed, children, owner)
        except ValueError as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                str(exc),
                ["ownership_mismatch"],
                retryable=False,
            )
        annotations = dict((template.get("metadata", {}) or {}).get("annotations", {}) or {})
        deletion_protected = annotations.get(_DELETION_PROTECTION) == "true" or bool(
            spec.config.get("deletion_protection", False)
        )
        if deletion_protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Argo WorkflowTemplate deletion protection is enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if active and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Argo workflow resources still have active runs: " + ", ".join(active),
                ["active_workflows"],
                retryable=False,
            )
        if delete_data:
            stubs = [
                *[
                    self._stub(
                        WORKFLOW_KIND,
                        str((workflow.get("metadata", {}) or {}).get("name") or ""),
                        parsed.namespace,
                    )
                    for workflow in workflows
                ],
                *stubs,
            ]
        stubs.append(self._stub(RESOURCE_KIND, parsed.name, parsed.namespace))
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            stubs,
            propagation_policy="Background" if delete_data else "Orphan",
        )
        if not result.ok:
            return DeprovisionResult(
                False,
                spec.handle,
                "Argo workflow resource deletion failed",
                result.summary(),
            )
        return DeprovisionResult(
            True,
            spec.handle,
            f"Argo WorkflowTemplate {parsed.name} deletion submitted",
        )

    @driver_op(cloud="k8s_native", driver="workflow_argo")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._require_driver()
            parsed = self._parsed(handle.handle)
            template = self._template(parsed)
            if template is None:
                return ServiceStatus(handle.handle, "deprovisioned", "Argo WorkflowTemplate not found")
            owner = self._assert_owned(template, RESOURCE_KIND)
            identity_error = self._executor_identity_error(parsed, template)
            if identity_error:
                return ServiceStatus(handle.handle, "error", identity_error)
            children = self._decode_children(template)
            active_schedules = 0
            for child in children:
                resource = self._child(parsed, child)
                if resource is None:
                    return ServiceStatus(
                        handle.handle,
                        "error",
                        f"managed Argo {child['kind']} {child['name']} is missing",
                    )
                self._assert_child_owner(resource, child, owner)
                if child["kind"] == CRON_KIND:
                    error = self._cron_error(resource)
                    if error:
                        return ServiceStatus(handle.handle, "error", error)
                    active_schedules += len((resource.get("status", {}) or {}).get("active", []) or [])
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        suffix = f"; {active_schedules} active scheduled run(s)" if active_schedules else ""
        return ServiceStatus(
            handle.handle,
            "available",
            f"Argo WorkflowTemplate {parsed.namespace}/{parsed.name} available{suffix}",
        )

    @driver_op(cloud="k8s_native", driver="workflow_argo")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._require_driver()
        parsed = self._parsed(handle.handle)
        template = self._template(parsed)
        if template is None:
            raise ValueError("Argo WorkflowTemplate does not exist")
        self._assert_owned(template, RESOURCE_KIND)
        env_vars = {
            "WORKFLOW_ENGINE_ID": ValueRef(literal=parsed.name),
            "WORKFLOW_ENGINE_ARN": ValueRef(
                literal=(f"k8s://{parsed.cluster_id}/{parsed.namespace}/workflowtemplate/{parsed.name}"),
            ),
            "WORKFLOW_ENGINE_REGION": ValueRef(literal="kubernetes"),
            "WORKFLOW_ENGINE_TYPE": ValueRef(literal="ARGO_WORKFLOWS"),
            "ARGO_WORKFLOW_TEMPLATE": ValueRef(literal=parsed.name),
            "ARGO_WORKFLOW_NAMESPACE": ValueRef(literal=parsed.namespace),
        }
        notes = (
            "Submit this WorkflowTemplate through the Kubernetes custom-resource API or an authenticated Argo Server."
        )
        server = self._config.argo_server_url.rstrip("/")
        if server:
            env_vars["ARGO_SERVER_URL"] = ValueRef(literal=server)
            env_vars["ARGO_EVENT_ENDPOINT"] = ValueRef(
                literal=f"{server}/api/v1/events/{quote(parsed.namespace, safe='')}/",
            )
            notes += " Argo Server credentials are intentionally not emitted by this binding."
        return Binding(env_vars=env_vars, notes=notes)

    @driver_op(cloud="k8s_native", driver="workflow_argo")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        self._require_driver()
        parsed = self._parsed(handle.handle)
        template = self._template(parsed)
        if template is None:
            raise ValueError("Argo WorkflowTemplate does not exist")
        owner_id = self._assert_owned(template, RESOURCE_KIND)
        declaration = self._snapshot_declaration(parsed, template, owner_id)
        payload = json.dumps(declaration, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        snapshot_name = dns_label(
            "astrolift",
            "workflow",
            "snapshot",
            digest[:12],
            uuid4().hex[:10],
        )
        manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": snapshot_name,
                "namespace": parsed.namespace,
                "labels": self._labels({}, self._owner_from_labels(template)),
                "annotations": {
                    _SNAPSHOT_OF: handle.handle,
                    _SNAPSHOT_SHA256: digest,
                    _SNAPSHOT_FORMAT: _SNAPSHOT_FORMAT_VERSION,
                },
            },
            "immutable": True,
            "data": {_SNAPSHOT_DATA_KEY: payload},
        }
        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [manifest],
        )
        if not result.ok:
            raise RuntimeError("Argo workflow snapshot failed: " + "; ".join(result.summary()))
        snapshot_id = _pack_handle(
            kind=KIND,
            cluster_id=parsed.cluster_id,
            namespace=parsed.namespace,
            name=snapshot_name,
        )
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snapshot_id,
            created_at=datetime.now(tz=UTC).isoformat(),
        )

    @driver_op(cloud="k8s_native", driver="workflow_argo")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        try:
            self._require_driver()
            parsed = self._parsed(snapshot.snapshot_id)
            resource = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                "v1/ConfigMap",
                parsed.name,
            )
            if resource is None:
                raise ValueError("Argo workflow snapshot does not exist")
            self._assert_owned(resource, "snapshot ConfigMap")
            annotations = dict((resource.get("metadata", {}) or {}).get("annotations", {}) or {})
            if annotations.get(_SNAPSHOT_OF) != snapshot.handle:
                raise ValueError("Argo workflow snapshot provenance does not match")
            if annotations.get(_SNAPSHOT_FORMAT) != _SNAPSHOT_FORMAT_VERSION:
                raise ValueError("Argo workflow snapshot format is unsupported")
            if resource.get("immutable") is not True:
                raise ValueError("Argo workflow snapshot is not immutable")
            payload = str((resource.get("data", {}) or {}).get(_SNAPSHOT_DATA_KEY) or "")
            digest = hashlib.sha256(payload.encode()).hexdigest()
            expected_name = re.fullmatch(
                rf"astrolift-workflow-snapshot-{re.escape(digest[:12])}-[a-f0-9]{{10}}",
                parsed.name,
            )
            if annotations.get(_SNAPSHOT_SHA256) != digest or expected_name is None:
                raise ValueError("Argo workflow snapshot integrity check failed")
            declaration = json.loads(payload)
            if not isinstance(declaration, dict):
                raise ValueError("Argo workflow snapshot declaration is invalid")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_argo_workflows_snapshot"])
        return self.provision(replace(target, config=copy.deepcopy(declaration)))

    @driver_op(cloud="k8s_native", driver="workflow_argo", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        scalar_map = {"type": "object", "additionalProperties": {"type": "string"}}
        child = {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "spec"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 63},
                "spec": {
                    "type": "object",
                    "description": "Native Argo resource spec; Astrolift injects the managed WorkflowTemplate ref.",
                },
                "labels": scalar_map,
                "annotations": scalar_map,
                "adopt_existing": {"type": "boolean", "default": False},
                "expected_existing_uid": {"type": "string"},
            },
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["workflow_spec"],
            "properties": {
                "workflow_spec": {
                    "type": "object",
                    "description": "Native argoproj.io/v1alpha1 WorkflowTemplate spec.",
                },
                "cron_workflows": {"type": "array", "items": child},
                "event_bindings": {"type": "array", "items": child},
                "labels": scalar_map,
                "annotations": scalar_map,
                "deletion_protection": {"type": "boolean", "default": False},
                "adopt_existing": {"type": "boolean", "default": False},
                "expected_existing_uid": {"type": "string"},
            },
        }

    @driver_op(cloud="k8s_native", driver="workflow_argo", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "WORKFLOW_ENGINE_ID": "WorkflowTemplate name",
                "WORKFLOW_ENGINE_ARN": "Portable Kubernetes WorkflowTemplate locator",
                "WORKFLOW_ENGINE_REGION": "Always kubernetes",
                "WORKFLOW_ENGINE_TYPE": "Always ARGO_WORKFLOWS",
                "ARGO_WORKFLOW_TEMPLATE": "Argo WorkflowTemplate name",
                "ARGO_WORKFLOW_NAMESPACE": "Argo resource namespace",
                "ARGO_SERVER_URL": "Optional operator-configured Argo Server base URL",
                "ARGO_EVENT_ENDPOINT": "Optional authenticated Argo event endpoint",
            },
        )

    def editable_fields(self) -> list[str]:
        return sorted(_CONFIG_FIELDS - {"adopt_existing", "expected_existing_uid"})

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        cfg = copy.deepcopy(raw or {})
        unknown = set(cfg) - _CONFIG_FIELDS
        if unknown:
            raise ValueError("unsupported Argo config fields: " + ", ".join(sorted(unknown)))
        workflow_spec = cfg.get("workflow_spec")
        if not isinstance(workflow_spec, dict) or not workflow_spec:
            raise ValueError("Argo Workflows requires a non-empty workflow_spec object")
        if "priority" in workflow_spec:
            raise ValueError("WorkflowTemplate does not support workflow_spec.priority")
        removed = _REMOVED_V4_WORKFLOW_FIELDS.intersection(workflow_spec)
        if removed:
            raise ValueError(
                "Argo Workflows 4.x removed workflow fields: " + ", ".join(sorted(removed)),
            )
        if not workflow_spec.get("templates") and not workflow_spec.get("workflowTemplateRef"):
            raise ValueError("workflow_spec requires templates or workflowTemplateRef")
        self._apply_workflow_defaults(workflow_spec)
        self._validate_workflow_spec(workflow_spec)
        self._validate_metadata(cfg.get("labels"), cfg.get("annotations"))
        self._normalize_children(cfg, "cron_workflows", CRON_KIND)
        self._normalize_children(cfg, "event_bindings", EVENT_KIND)
        return cfg

    def _normalize_children(self, cfg: dict[str, Any], field: str, kind: str) -> None:
        children = cfg.get(field) or []
        if not isinstance(children, list):
            raise ValueError(f"{field} must be an array")
        names: set[str] = set()
        for child in children:
            if not isinstance(child, dict):
                raise ValueError(f"{field} must contain objects")
            unknown = set(child) - _CHILD_FIELDS
            if unknown:
                raise ValueError(
                    f"unsupported {kind} fields: " + ", ".join(sorted(unknown)),
                )
            name = dns_label(child.get("name") or "")
            if name in names:
                raise ValueError(f"duplicate {kind} {name}")
            native_spec = child.get("spec")
            if not isinstance(native_spec, dict) or not native_spec:
                raise ValueError(f"{kind} {name} requires a non-empty spec object")
            self._validate_metadata(child.get("labels"), child.get("annotations"))
            if kind == CRON_KIND:
                self._normalize_cron_spec(native_spec)
            else:
                self._normalize_event_spec(native_spec)
            names.add(name)

    def _normalize_cron_spec(self, spec: dict[str, Any]) -> None:
        if "schedule" in spec:
            raise ValueError("CronWorkflow spec.schedule was removed in Argo Workflows 4.x; use schedules")
        schedules = spec.get("schedules")
        if (
            not isinstance(schedules, list)
            or not schedules
            or any(not isinstance(value, str) or not value.strip() for value in schedules)
        ):
            raise ValueError("CronWorkflow spec.schedules must be a non-empty string array")
        policy = str(spec.get("concurrencyPolicy") or "Forbid")
        if policy not in {"Allow", "Forbid", "Replace"}:
            raise ValueError("CronWorkflow concurrencyPolicy must be Allow, Forbid, or Replace")
        spec["concurrencyPolicy"] = policy
        for field, default, maximum in (
            ("successfulJobsHistoryLimit", 3, 100),
            ("failedJobsHistoryLimit", 1, 100),
        ):
            value = spec.get(field, default)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ValueError(f"CronWorkflow {field} must be an integer between 0 and {maximum}")
            spec[field] = value
        workflow_spec = spec.setdefault("workflowSpec", {})
        if not isinstance(workflow_spec, dict):
            raise ValueError("CronWorkflow workflowSpec must be an object")
        if workflow_spec.get("templates") or workflow_spec.get("entrypoint"):
            raise ValueError("CronWorkflow must execute the managed WorkflowTemplate, not inline templates")
        self._apply_workflow_defaults(workflow_spec)
        self._validate_workflow_spec(workflow_spec, allow_empty_template=True)

    def _normalize_event_spec(self, spec: dict[str, Any]) -> None:
        event = spec.get("event")
        submit = spec.get("submit")
        if not isinstance(event, dict) or not str(event.get("selector") or "").strip():
            raise ValueError("WorkflowEventBinding requires event.selector")
        if not isinstance(submit, dict):
            raise ValueError("WorkflowEventBinding requires submit")
        reference = submit.get("workflowTemplateRef")
        if reference is not None and not isinstance(reference, dict):
            raise ValueError("WorkflowEventBinding workflowTemplateRef must be an object")
        self._validate_security(spec, location="event binding")

    def _apply_workflow_defaults(self, spec: dict[str, Any]) -> None:
        account = str(spec.get("serviceAccountName") or self._config.service_account_name)
        if not account:
            raise ValueError("Argo Workflows requires an explicit workflow service account")
        dns_label(account)
        if account != self._config.service_account_name and not self._config.allow_service_account_override:
            raise ValueError("workflow service-account override is disabled by cluster policy")
        if self._config.allowed_service_accounts and account not in self._config.allowed_service_accounts:
            raise ValueError(f"workflow service account {account!r} is not enabled for this cluster")
        spec["serviceAccountName"] = account

        parallelism = spec.get("parallelism", self._config.default_parallelism)
        if (
            isinstance(parallelism, bool)
            or not isinstance(parallelism, int)
            or parallelism < 1
            or parallelism > self._config.max_parallelism
        ):
            raise ValueError(
                f"workflow parallelism must be between 1 and {self._config.max_parallelism}",
            )
        spec["parallelism"] = parallelism
        deadline = spec.get(
            "activeDeadlineSeconds",
            self._config.default_active_deadline_seconds,
        )
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, int)
            or deadline < 1
            or deadline > self._config.max_active_deadline_seconds
        ):
            raise ValueError(
                f"workflow activeDeadlineSeconds must be between 1 and {self._config.max_active_deadline_seconds}",
            )
        spec["activeDeadlineSeconds"] = deadline
        ttl = spec.setdefault("ttlStrategy", {})
        if not isinstance(ttl, dict):
            raise ValueError("workflow ttlStrategy must be an object")
        completion_ttl = ttl.get("secondsAfterCompletion", self._config.default_ttl_seconds)
        if (
            isinstance(completion_ttl, bool)
            or not isinstance(completion_ttl, int)
            or completion_ttl < 0
            or completion_ttl > self._config.max_ttl_seconds
        ):
            raise ValueError(
                f"workflow ttlStrategy.secondsAfterCompletion must be between 0 and {self._config.max_ttl_seconds}",
            )
        ttl["secondsAfterCompletion"] = completion_ttl
        pod_gc = spec.setdefault("podGC", {"strategy": "OnWorkflowCompletion"})
        if not isinstance(pod_gc, dict):
            raise ValueError("workflow podGC must be an object")

    def _validate_workflow_spec(
        self,
        spec: dict[str, Any],
        *,
        allow_empty_template: bool = False,
    ) -> None:
        reference = spec.get("workflowTemplateRef")
        if reference is not None:
            if not isinstance(reference, dict):
                raise ValueError("workflowTemplateRef must be an object")
            if not self._config.allow_workflow_template_refs:
                raise ValueError("WorkflowTemplate references are disabled by cluster policy")
            if bool(reference.get("clusterScope")) and not self._config.allow_cluster_template_refs:
                raise ValueError("ClusterWorkflowTemplate references are disabled by cluster policy")
        if not allow_empty_template and not spec.get("templates") and reference is None:
            raise ValueError("workflow_spec requires templates or workflowTemplateRef")
        workflow_metadata = spec.get("workflowMetadata") or {}
        if not isinstance(workflow_metadata, dict):
            raise ValueError("workflowMetadata must be an object")
        self._validate_metadata(
            workflow_metadata.get("labels"),
            workflow_metadata.get("annotations"),
        )
        labels_from = workflow_metadata.get("labelsFrom") or {}
        if not isinstance(labels_from, dict):
            raise ValueError("workflowMetadata.labelsFrom must be an object")
        for key, source in labels_from.items():
            if str(key) == _OWNER or str(key).startswith("astrolift.io/"):
                raise ValueError(f"workflowMetadata.labelsFrom key {key!r} is reserved by Astrolift")
            if len(str(key)) > 253 or not _LABEL_KEY.fullmatch(str(key)):
                raise ValueError(f"invalid workflowMetadata.labelsFrom key {key!r}")
            if (
                not isinstance(source, dict)
                or not isinstance(source.get("expression"), str)
                or not source["expression"].strip()
            ):
                raise ValueError(
                    f"workflowMetadata.labelsFrom value for {key!r} requires an expression",
                )
        self._validate_security(spec, location="workflow spec")

    def _validate_security(self, value: Any, *, location: str) -> None:
        def walk(node: Any, path: str) -> None:
            if isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, f"{path}[{index}]")
                return
            if not isinstance(node, dict):
                return
            for field in _UNSAFE_BOOLEAN_FIELDS:
                if node.get(field) is True:
                    if field.startswith("host") and not self._config.allow_host_access:
                        raise ValueError(f"{path}.{field} is disabled by cluster policy")
                    if not field.startswith("host") and not self._config.allow_privileged_pods:
                        raise ValueError(f"{path}.{field} is disabled by cluster policy")
            if "hostPath" in node and not self._config.allow_host_access:
                raise ValueError(f"{path}.hostPath is disabled by cluster policy")
            if "podSpecPatch" in node and not self._config.allow_pod_spec_patch:
                raise ValueError(f"{path}.podSpecPatch is disabled by cluster policy")
            if "executorPlugins" in node and not self._config.allow_executor_plugins:
                raise ValueError(f"{path}.executorPlugins is disabled by cluster policy")
            if "templateRef" in node and not self._config.allow_workflow_template_refs:
                raise ValueError(f"{path}.templateRef is disabled by cluster policy")
            if node.get("clusterScope") is True and not self._config.allow_cluster_template_refs:
                raise ValueError(f"{path}.clusterScope is disabled by cluster policy")
            if isinstance(node.get("resource"), dict) and not self._config.allow_resource_templates:
                raise ValueError(f"{path}.resource templates are disabled by cluster policy")
            if isinstance(node.get("plugin"), dict) and not self._config.allow_executor_plugins:
                raise ValueError(f"{path}.plugin templates are disabled by cluster policy")
            if isinstance(node.get("http"), dict) and not self._config.allow_external_http_templates:
                raise ValueError(f"{path}.http templates are disabled by cluster policy")
            if "runAsUser" in node and node.get("runAsUser") == 0 and not self._config.allow_privileged_pods:
                raise ValueError(f"{path}.runAsUser=0 is disabled by cluster policy")
            if node.get("runAsNonRoot") is False and not self._config.allow_privileged_pods:
                raise ValueError(f"{path}.runAsNonRoot=false is disabled by cluster policy")
            capabilities = node.get("capabilities") or {}
            if isinstance(capabilities, dict) and capabilities.get("add") and not self._config.allow_privileged_pods:
                raise ValueError(f"{path}.capabilities.add is disabled by cluster policy")
            if node.get("procMount") not in (None, "Default") and not self._config.allow_privileged_pods:
                raise ValueError(f"{path}.procMount is disabled by cluster policy")
            seccomp = node.get("seccompProfile") or {}
            if (
                isinstance(seccomp, dict)
                and seccomp.get("type") == "Unconfined"
                and not self._config.allow_privileged_pods
            ):
                raise ValueError(f"{path}.seccompProfile is disabled by cluster policy")
            if node.get("sysctls") and not self._config.allow_privileged_pods:
                raise ValueError(f"{path}.sysctls is disabled by cluster policy")
            if node.get("hostPort") not in (None, 0) and not self._config.allow_host_access:
                raise ValueError(f"{path}.hostPort is disabled by cluster policy")
            if "image" in node:
                self._validate_image(node["image"], path=f"{path}.image")
            if "serviceAccountName" in node:
                account = str(node.get("serviceAccountName") or "")
                if account != self._config.service_account_name and not self._config.allow_service_account_override:
                    raise ValueError(f"{path}.serviceAccountName override is disabled by cluster policy")
                if self._config.allowed_service_accounts and account not in self._config.allowed_service_accounts:
                    raise ValueError(f"{path}.serviceAccountName is not enabled for this cluster")
            for key, item in node.items():
                walk(item, f"{path}.{key}")

        walk(value, location)

    def _validate_image(self, raw: Any, *, path: str) -> None:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{path} must be a non-empty string")
        image = raw.strip()
        image_path = image.split("@", 1)[0]
        allowed = any(
            image_path == prefix.rstrip("/") or image_path.startswith(f"{prefix.rstrip('/')}/")
            for prefix in self._config.allowed_image_prefixes
            if prefix.rstrip("/")
        )
        if self._config.allowed_image_prefixes and not allowed:
            raise ValueError(f"{path} is outside the cluster image allowlist")
        if not self._config.allow_tagged_images and not re.search(
            r"@sha256:[a-f0-9]{64}$",
            image,
        ):
            raise ValueError(f"{path} must use an immutable sha256 digest")

    def _assert_namespace_managed(self, namespace: str) -> None:
        if self._config.watch_all_namespaces:
            return
        if namespace not in self._config.managed_namespaces:
            raise ValueError(
                f"Argo controller is not configured to watch namespace {namespace!r}",
            )

    def _assert_service_accounts(
        self,
        *,
        cluster_id: str,
        namespace: str,
        cfg: dict[str, Any],
        referenced_specs: list[dict[str, Any]] | None = None,
    ) -> None:
        accounts: set[str] = set()

        def collect(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    collect(item)
                return
            if not isinstance(value, dict):
                return
            if "serviceAccountName" in value:
                account = str(value.get("serviceAccountName") or "")
                if account:
                    accounts.add(account)
            for item in value.values():
                collect(item)

        collect(cfg["workflow_spec"])
        for cron in cfg.get("cron_workflows") or []:
            collect((cron.get("spec") or {}).get("workflowSpec") or {})
        for referenced in referenced_specs or []:
            collect(referenced)
        for account in sorted(accounts):
            resource = self._config.cluster_driver.get_manifest(
                cluster_id,
                namespace,
                "v1/ServiceAccount",
                account,
            )
            if resource is None:
                if account == self._config.service_account_name:
                    # The driver creates the configured baseline executor
                    # identity with least-privilege workflowtaskresults RBAC.
                    continue
                raise ValueError(
                    f"Argo workflow service account {namespace}/{account} does not exist; "
                    "create it with workflowtaskresults create/patch RBAC before provisioning",
                )

    def _validate_references(
        self,
        *,
        cluster_id: str,
        namespace: str,
        cfg: dict[str, Any],
    ) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        stack: set[tuple[str, str]] = set()
        trusted = dict(self._config.trusted_workflow_template_uids or {})

        def resolve(reference: Any) -> None:
            if not isinstance(reference, dict):
                raise ValueError("Argo template reference must be an object")
            name = str(reference.get("name") or "")
            if not name:
                raise ValueError("Argo template reference requires a name")
            dns_label(name)
            cluster_scope = bool(reference.get("clusterScope"))
            kind = "ClusterWorkflowTemplate" if cluster_scope else RESOURCE_KIND
            target_namespace = None if cluster_scope else namespace
            trust_key = f"cluster/{name}" if cluster_scope else f"{namespace}/{name}"
            identity = (kind, trust_key)
            if identity in stack:
                raise ValueError(f"cyclic Argo template reference detected at {trust_key}")
            if identity in seen:
                return
            resource = self._config.cluster_driver.get_manifest(
                cluster_id,
                target_namespace,
                f"{API_VERSION}/{kind}",
                name,
            )
            if resource is None:
                raise ValueError(f"referenced Argo {kind} {trust_key} does not exist")
            metadata = dict(resource.get("metadata", {}) or {})
            labels = dict(metadata.get("labels", {}) or {})
            uid = str(metadata.get("uid") or "")
            astrolift_owned = labels.get(_OWNER) == "astrolift" and bool(labels.get(_OWNER_ID))
            if not astrolift_owned and (not uid or trusted.get(trust_key) != uid):
                raise ValueError(
                    f"referenced Argo {kind} {trust_key} is not Astrolift-owned or pinned to its exact UID",
                )
            native_spec = copy.deepcopy(resource.get("spec") or {})
            if not isinstance(native_spec, dict) or not native_spec:
                raise ValueError(f"referenced Argo {kind} {trust_key} has no spec")
            self._strip_injected_workflow_labels(native_spec)
            stack.add(identity)
            self._validate_workflow_spec(native_spec)
            walk(native_spec)
            stack.remove(identity)
            seen.add(identity)
            resolved.append(native_spec)

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if not isinstance(value, dict):
                return
            for key, item in value.items():
                if key in {"workflowTemplateRef", "templateRef"}:
                    resolve(item)
                else:
                    walk(item)

        walk(cfg["workflow_spec"])
        for cron in cfg.get("cron_workflows") or []:
            walk((cron.get("spec") or {}).get("workflowSpec") or {})
        return resolved

    @staticmethod
    def _strip_injected_workflow_labels(workflow_spec: dict[str, Any]) -> None:
        workflow_metadata = workflow_spec.get("workflowMetadata") or {}
        if not isinstance(workflow_metadata, dict):
            return
        labels = workflow_metadata.get("labels") or {}
        if not isinstance(labels, dict):
            return
        for key in list(labels):
            if key == _OWNER or str(key).startswith("astrolift.io/"):
                labels.pop(key, None)

    @staticmethod
    def _user_metadata(values: Any) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in (values or {}).items()
            if key != _OWNER and not str(key).startswith("astrolift.io/")
        }

    def _snapshot_declaration(
        self,
        parsed: ParsedHandle,
        template: dict[str, Any],
        owner: str,
    ) -> dict[str, Any]:
        metadata = dict(template.get("metadata", {}) or {})
        annotations = dict(metadata.get("annotations", {}) or {})
        workflow_spec = copy.deepcopy(template.get("spec") or {})
        self._strip_injected_workflow_labels(workflow_spec)
        declaration: dict[str, Any] = {
            "workflow_spec": workflow_spec,
            "cron_workflows": [],
            "event_bindings": [],
            "labels": self._user_metadata(metadata.get("labels")),
            "annotations": self._user_metadata(annotations),
            "deletion_protection": annotations.get(_DELETION_PROTECTION) == "true",
        }
        for child in self._decode_children(template):
            resource = self._child(parsed, child)
            if resource is None:
                raise ValueError(
                    f"managed Argo {child['kind']} {child['name']} is missing",
                )
            self._assert_child_owner(resource, child, owner)
            child_metadata = dict(resource.get("metadata", {}) or {})
            native_spec = copy.deepcopy(resource.get("spec") or {})
            if child["kind"] == CRON_KIND:
                workflow = native_spec.get("workflowSpec") or {}
                if not isinstance(workflow, dict):
                    raise ValueError("managed Argo CronWorkflow workflowSpec is invalid")
                workflow.pop("workflowTemplateRef", None)
                self._strip_injected_workflow_labels(workflow)
                field = "cron_workflows"
            else:
                submit = native_spec.get("submit") or {}
                if not isinstance(submit, dict):
                    raise ValueError("managed Argo WorkflowEventBinding submit is invalid")
                submit.pop("workflowTemplateRef", None)
                submit_metadata = submit.get("metadata") or {}
                if isinstance(submit_metadata, dict):
                    submit_metadata["labels"] = self._user_metadata(
                        submit_metadata.get("labels"),
                    )
                field = "event_bindings"
            declaration[field].append(
                {
                    "name": child["logical_name"],
                    "spec": native_spec,
                    "labels": self._user_metadata(child_metadata.get("labels")),
                    "annotations": self._user_metadata(child_metadata.get("annotations")),
                },
            )
        return declaration

    @staticmethod
    def _validate_metadata(labels: Any, annotations: Any) -> None:
        for field, values, bounded in (
            ("labels", labels, True),
            ("annotations", annotations, False),
        ):
            values = values or {}
            if not isinstance(values, dict):
                raise ValueError(f"{field} must be an object")
            for key, value in values.items():
                if str(key) in {_OWNER, _OWNER_ID, _CHILDREN} or str(key).startswith("astrolift.io/"):
                    raise ValueError(f"metadata key {key!r} is reserved by Astrolift")
                if len(str(key)) > 253 or not _LABEL_KEY.fullmatch(str(key)):
                    raise ValueError(f"invalid Kubernetes metadata key {key!r}")
                if not isinstance(value, str):
                    raise ValueError(f"{field} must map keys to strings")
                if bounded and (len(value) > 63 or not _LABEL_VALUE.fullmatch(value)):
                    raise ValueError(f"invalid Kubernetes label value for {key!r}")

    def _manifests(
        self,
        *,
        cluster_id: str,
        namespace: str,
        name: str,
        cfg: dict[str, Any],
        owner: dict[str, str],
    ) -> list[dict[str, Any]]:
        children: list[dict[str, str]] = []
        identity_manifests = self._executor_identity_manifests(
            cluster_id=cluster_id,
            namespace=namespace,
            owner=owner,
        )
        manifests: list[dict[str, Any]] = []
        for field, kind in (
            ("cron_workflows", CRON_KIND),
            ("event_bindings", EVENT_KIND),
        ):
            for child in cfg.get(field) or []:
                child_name = dns_label(name, child["name"])
                native_spec = copy.deepcopy(child["spec"])
                self._bind_child_to_template(native_spec, kind=kind, template_name=name)
                if kind == CRON_KIND:
                    self._inject_workflow_owner(native_spec["workflowSpec"], owner)
                else:
                    submit_metadata = native_spec["submit"].setdefault("metadata", {})
                    if not isinstance(submit_metadata, dict):
                        raise ValueError("WorkflowEventBinding submit.metadata must be an object")
                    submit_labels = submit_metadata.setdefault("labels", {})
                    if not isinstance(submit_labels, dict):
                        raise ValueError(
                            "WorkflowEventBinding submit.metadata.labels must be an object",
                        )
                    submit_labels.update(self._labels({}, owner))
                manifest = {
                    "apiVersion": API_VERSION,
                    "kind": kind,
                    "metadata": {
                        "name": child_name,
                        "namespace": namespace,
                        "labels": self._labels(child.get("labels"), owner),
                        "annotations": dict(child.get("annotations") or {}),
                    },
                    "spec": native_spec,
                }
                self._assert_child_adoptable(
                    cluster_id,
                    namespace,
                    manifest,
                    child,
                    dns_label(owner[_OWNER_ID]),
                )
                children.append(
                    {
                        "kind": kind,
                        "name": child_name,
                        "logical_name": dns_label(child["name"]),
                    },
                )
                manifests.append(manifest)

        template_spec = copy.deepcopy(cfg["workflow_spec"])
        self._inject_workflow_owner(template_spec, owner)
        annotations = dict(cfg.get("annotations") or {})
        annotations[_DELETION_PROTECTION] = "true" if bool(cfg.get("deletion_protection", False)) else "false"
        annotations[_CHILDREN] = json.dumps(
            sorted(children, key=lambda row: (row["kind"], row["name"])),
            separators=(",", ":"),
        )
        template = {
            "apiVersion": API_VERSION,
            "kind": RESOURCE_KIND,
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": self._labels(cfg.get("labels"), owner),
                "annotations": annotations,
            },
            "spec": template_spec,
        }
        return [*identity_manifests, template, *manifests]

    def _executor_identity_manifests(
        self,
        *,
        cluster_id: str,
        namespace: str,
        owner: dict[str, str],
    ) -> list[dict[str, Any]]:
        account = self._config.service_account_name
        existing = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            "v1/ServiceAccount",
            account,
        )
        managed_account = False
        if existing is not None:
            existing_labels = dict((existing.get("metadata", {}) or {}).get("labels", {}) or {})
            managed_account = (
                existing_labels.get(_OWNER) == "astrolift"
                and existing_labels.get("astrolift.io/component") == _EXECUTOR_COMPONENT
            )
            if not managed_account:
                return []
        role_name = dns_label("astrolift", account, "executor")
        shared_owner = {
            "app.kubernetes.io/managed-by": "astrolift",
            "astrolift.io/component": _EXECUTOR_COMPONENT,
            "astrolift.io/organization": owner.get("astrolift.io/organization", ""),
        }
        labels = {key: dns_label(value) for key, value in shared_owner.items() if value}
        for kind, name in (("Role", role_name), ("RoleBinding", role_name)):
            current = self._config.cluster_driver.get_manifest(
                cluster_id,
                namespace,
                f"rbac.authorization.k8s.io/v1/{kind}",
                name,
            )
            if current is None:
                continue
            current_labels = dict((current.get("metadata", {}) or {}).get("labels", {}) or {})
            if (
                current_labels.get(_OWNER) != "astrolift"
                or current_labels.get("astrolift.io/component") != _EXECUTOR_COMPONENT
            ):
                raise ValueError(f"Argo executor {kind} {namespace}/{name} is owned by another controller")
        manifests: list[dict[str, Any]] = []
        if not managed_account:
            manifests.append(
                {
                    "apiVersion": "v1",
                    "kind": "ServiceAccount",
                    "metadata": {"name": account, "namespace": namespace, "labels": labels},
                },
            )
        manifests.extend(
            [
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "Role",
                    "metadata": {"name": role_name, "namespace": namespace, "labels": labels},
                    "rules": [
                        {
                            "apiGroups": ["argoproj.io"],
                            "resources": ["workflowtaskresults"],
                            "verbs": ["create", "patch"],
                        },
                    ],
                },
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "RoleBinding",
                    "metadata": {"name": role_name, "namespace": namespace, "labels": labels},
                    "subjects": [
                        {
                            "kind": "ServiceAccount",
                            "name": account,
                            "namespace": namespace,
                        },
                    ],
                    "roleRef": {
                        "apiGroup": "rbac.authorization.k8s.io",
                        "kind": "Role",
                        "name": role_name,
                    },
                },
            ],
        )
        return manifests

    def _executor_identity_error(
        self,
        parsed: ParsedHandle,
        template: dict[str, Any],
    ) -> str:
        account = str((template.get("spec", {}) or {}).get("serviceAccountName") or "")
        service_account = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            "v1/ServiceAccount",
            account,
        )
        if service_account is None:
            return f"Argo workflow service account {parsed.namespace}/{account} is missing"
        labels = dict((service_account.get("metadata", {}) or {}).get("labels", {}) or {})
        if labels.get(_OWNER) != "astrolift" or labels.get("astrolift.io/component") != _EXECUTOR_COMPONENT:
            return ""
        role_name = dns_label("astrolift", account, "executor")
        for kind in ("Role", "RoleBinding"):
            resource = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                f"rbac.authorization.k8s.io/v1/{kind}",
                role_name,
            )
            if resource is None:
                return f"Argo executor {kind} {parsed.namespace}/{role_name} is missing"
        return ""

    def _inject_workflow_owner(
        self,
        workflow_spec: dict[str, Any],
        owner: dict[str, str],
    ) -> None:
        workflow_metadata = workflow_spec.setdefault("workflowMetadata", {})
        if not isinstance(workflow_metadata, dict):
            raise ValueError("workflowMetadata must be an object")
        metadata_labels = workflow_metadata.setdefault("labels", {})
        if not isinstance(metadata_labels, dict):
            raise ValueError("workflowMetadata.labels must be an object")
        metadata_labels.update(self._labels({}, owner))

    def _bind_child_to_template(
        self,
        spec: dict[str, Any],
        *,
        kind: str,
        template_name: str,
    ) -> None:
        if kind == CRON_KIND:
            workflow_spec = spec.setdefault("workflowSpec", {})
            reference = workflow_spec.setdefault("workflowTemplateRef", {})
        else:
            submit = spec.setdefault("submit", {})
            reference = submit.setdefault("workflowTemplateRef", {})
        if not isinstance(reference, dict):
            raise ValueError(f"{kind} workflowTemplateRef must be an object")
        configured_name = str(reference.get("name") or "")
        if configured_name and configured_name != template_name:
            raise ValueError(f"{kind} must reference its managed WorkflowTemplate {template_name!r}")
        if bool(reference.get("clusterScope")):
            raise ValueError(f"{kind} cannot replace its managed template with a cluster-scoped template")
        reference["name"] = template_name
        reference.pop("clusterScope", None)

    def _assert_adoptable(
        self,
        *,
        cluster_id: str,
        namespace: str,
        name: str,
        managed_service_id: str,
        cfg: dict[str, Any],
    ) -> None:
        current = self._template(ParsedHandle(KIND, cluster_id, namespace, name))
        if current is None:
            return
        metadata = dict(current.get("metadata", {}) or {})
        labels = dict(metadata.get("labels", {}) or {})
        owner = str(labels.get(_OWNER_ID) or "")
        if labels.get(_OWNER) == "astrolift" and owner == dns_label(managed_service_id):
            return
        if labels.get(_OWNER) == "astrolift" and owner:
            raise ValueError("Argo WorkflowTemplate belongs to another Astrolift resource")
        if not cfg.get("adopt_existing") or str(cfg.get("expected_existing_uid") or "") != str(
            metadata.get("uid") or "",
        ):
            raise ValueError("adopting an Argo WorkflowTemplate requires its exact expected_existing_uid")

    def _assert_child_adoptable(
        self,
        cluster_id: str,
        namespace: str,
        manifest: dict[str, Any],
        child: dict[str, Any],
        owner_id: str,
    ) -> None:
        current = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            f"{API_VERSION}/{manifest['kind']}",
            manifest["metadata"]["name"],
        )
        if current is None:
            return
        metadata = dict(current.get("metadata", {}) or {})
        labels = dict(metadata.get("labels", {}) or {})
        if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID) == owner_id:
            return
        if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID):
            raise ValueError(
                f"{manifest['kind']} {manifest['metadata']['name']} belongs to another resource",
            )
        if not child.get("adopt_existing") or str(child.get("expected_existing_uid") or "") != str(
            metadata.get("uid") or "",
        ):
            raise ValueError(
                f"adopting {manifest['kind']} {manifest['metadata']['name']} requires its exact expected_existing_uid",
            )

    def _assert_owned(self, resource: dict[str, Any], kind: str) -> str:
        labels = dict((resource.get("metadata", {}) or {}).get("labels", {}) or {})
        owner = str(labels.get(_OWNER_ID) or "")
        if labels.get(_OWNER) != "astrolift" or not owner:
            raise ValueError(f"Argo {kind} is not owned by an Astrolift managed resource")
        return owner

    @staticmethod
    def _assert_child_owner(
        resource: dict[str, Any],
        child: dict[str, str],
        owner: str,
    ) -> None:
        labels = dict((resource.get("metadata", {}) or {}).get("labels", {}) or {})
        if labels.get(_OWNER) != "astrolift" or labels.get(_OWNER_ID) != owner:
            raise ValueError(f"managed Argo {child['kind']} {child['name']} has foreign ownership")

    def _owned_child_stubs(
        self,
        parsed: ParsedHandle,
        children: list[dict[str, str]],
        owner: str,
    ) -> list[dict[str, Any]]:
        stubs: list[dict[str, Any]] = []
        for child in children:
            current = self._child(parsed, child)
            if current is None:
                continue
            self._assert_child_owner(current, child, owner)
            stubs.append(self._stub(child["kind"], child["name"], parsed.namespace))
        return stubs

    def _active_cron_children(
        self,
        parsed: ParsedHandle,
        children: list[dict[str, str]],
        owner: str,
    ) -> list[str]:
        active: list[str] = []
        cron_names = {child["name"] for child in children if child["kind"] == CRON_KIND}
        for child in children:
            if child["kind"] != CRON_KIND:
                continue
            current = self._child(parsed, child)
            if current is None:
                continue
            self._assert_child_owner(current, child, owner)
            if (current.get("status", {}) or {}).get("active"):
                active.append(child["name"])
        for workflow in self._owned_workflows(parsed, owner):
            if not self._active_workflow_names([workflow]):
                continue
            references = (workflow.get("metadata", {}) or {}).get("ownerReferences", []) or []
            if any(
                reference.get("kind") == CRON_KIND and reference.get("name") in cron_names
                for reference in references
                if isinstance(reference, dict)
            ):
                active.append(str((workflow.get("metadata", {}) or {}).get("name") or "unknown"))
        return sorted(active)

    def _owned_workflows(
        self,
        parsed: ParsedHandle,
        owner: str,
    ) -> list[dict[str, Any]]:
        rows = self._config.cluster_driver.list_manifests(
            parsed.cluster_id,
            parsed.namespace,
            f"{API_VERSION}/{WORKFLOW_KIND}",
        )
        return [
            row
            for row in rows
            if ((row.get("metadata", {}) or {}).get("labels", {}) or {}).get(_OWNER) == "astrolift"
            and ((row.get("metadata", {}) or {}).get("labels", {}) or {}).get(_OWNER_ID) == owner
        ]

    @staticmethod
    def _active_workflow_names(workflows: list[dict[str, Any]]) -> list[str]:
        terminal = {"Succeeded", "Failed", "Error"}
        return sorted(
            str((workflow.get("metadata", {}) or {}).get("name") or "unknown")
            for workflow in workflows
            if str((workflow.get("status", {}) or {}).get("phase") or "") not in terminal
        )

    def _stale_children(
        self,
        template: dict[str, Any],
        desired: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        desired_keys = {
            (str(row["kind"]), str(row["metadata"]["name"]))
            for row in desired
            if row.get("kind") in {CRON_KIND, EVENT_KIND}
        }
        return [
            child for child in self._decode_children(template) if (child["kind"], child["name"]) not in desired_keys
        ]

    @staticmethod
    def _decode_children(template: dict[str, Any]) -> list[dict[str, str]]:
        raw = str(
            ((template.get("metadata", {}) or {}).get("annotations", {}) or {}).get(
                _CHILDREN,
            )
            or "[]",
        )
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Argo managed-child inventory is invalid") from exc
        if not isinstance(value, list):
            raise ValueError("Argo managed-child inventory is invalid")
        children: list[dict[str, str]] = []
        for child in value:
            if (
                not isinstance(child, dict)
                or set(child) != {"kind", "name", "logical_name"}
                or child.get("kind") not in {CRON_KIND, EVENT_KIND}
                or not isinstance(child.get("name"), str)
                or not child["name"]
                or not isinstance(child.get("logical_name"), str)
                or not child["logical_name"]
            ):
                raise ValueError("Argo managed-child inventory is invalid")
            children.append(
                {
                    "kind": child["kind"],
                    "name": child["name"],
                    "logical_name": child["logical_name"],
                },
            )
        return children

    @staticmethod
    def _cron_error(resource: dict[str, Any]) -> str:
        status = resource.get("status", {}) or {}
        for condition in status.get("conditions", []) or []:
            condition_type = str(condition.get("type") or "")
            condition_status = str(condition.get("status") or "").lower()
            if condition_status == "true" and condition_type in {
                "SpecError",
                "SubmissionError",
            }:
                return str(
                    condition.get("message") or condition.get("reason") or f"CronWorkflow {condition_type}",
                )
        return ""

    @staticmethod
    def _labels(raw: Any, owner: dict[str, str]) -> dict[str, str]:
        return {
            **{str(key): str(value) for key, value in (raw or {}).items()},
            _OWNER: "astrolift",
            **{key: dns_label(value) for key, value in owner.items() if value},
        }

    @staticmethod
    def _owner_from_spec(spec: ProvisionSpec) -> dict[str, str]:
        return {
            _OWNER_ID: spec.managed_service_id,
            "astrolift.io/organization": spec.organization_slug,
            "astrolift.io/app": spec.app_slug,
            "astrolift.io/environment": spec.environment_name,
        }

    @staticmethod
    def _owner_from_labels(resource: dict[str, Any]) -> dict[str, str]:
        labels = dict((resource.get("metadata", {}) or {}).get("labels", {}) or {})
        return {
            key: str(labels.get(key) or "")
            for key in (
                _OWNER_ID,
                "astrolift.io/organization",
                "astrolift.io/app",
                "astrolift.io/environment",
            )
        }

    def _require_driver(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("Argo Workflows requires a live cluster driver")

    @staticmethod
    def _parsed(handle: str) -> ParsedHandle:
        parsed = _unpack_handle(handle)
        if parsed.is_legacy:
            raise ValueError("legacy Argo WorkflowTemplate handle has no cluster locator")
        return parsed

    def _template(self, parsed: ParsedHandle) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                f"{API_VERSION}/{RESOURCE_KIND}",
                parsed.name,
            ),
        )

    def _child(
        self,
        parsed: ParsedHandle,
        child: dict[str, str],
    ) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                f"{API_VERSION}/{child['kind']}",
                child["name"],
            ),
        )

    @staticmethod
    def _stub(kind: str, name: str, namespace: str) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "kind": kind,
            "metadata": {"name": name, "namespace": namespace},
        }
