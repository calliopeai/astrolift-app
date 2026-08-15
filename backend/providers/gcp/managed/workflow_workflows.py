"""Google Cloud Workflows definition, revision, execution, and binding lifecycle."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
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

KIND = "workflow_engine"
VARIANT = "workflows"
_API_ROOT = "https://workflows.googleapis.com/v1"
_EXECUTIONS_API_ROOT = "https://workflowexecutions.googleapis.com/v1"
_ID_PATTERN = re.compile(r"[A-Za-z](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?")
_REVISION_PATTERN = re.compile(r"[0-9]{6}-[0-9a-f]{3}")
_WORKFLOW_NAME_PATTERN = re.compile(
    r"projects/([^/]+)/locations/([^/]+)/workflows/([^/]+)",
)
_SOURCE_LIMIT_BYTES = 128 * 1024
_ARGUMENT_LIMIT_BYTES = 32 * 1024
_CALL_LOG_LEVELS = {
    "CALL_LOG_LEVEL_UNSPECIFIED",
    "LOG_ALL_CALLS",
    "LOG_ERRORS_ONLY",
    "LOG_NONE",
}
_HISTORY_LEVELS = {
    "EXECUTION_HISTORY_LEVEL_UNSPECIFIED",
    "EXECUTION_HISTORY_BASIC",
    "EXECUTION_HISTORY_DETAILED",
}
_ACTIVE_EXECUTION_STATES = {"ACTIVE", "QUEUED"}
_OWNERSHIP_KEYS = {
    "astrolift_io_managed_by",
    "astrolift_io_organization",
    "astrolift_io_app",
    "astrolift_io_environment",
    "astrolift_io_cluster",
    "astrolift_io_resource_hint",
    "astrolift_io_binding",
    "astrolift_io_managed_service_id",
    "astrolift_io_adopted",
    "astrolift_io_reassigned",
}
_OUTPUT_FIELDS = {
    "name",
    "revisionId",
    "createTime",
    "updateTime",
    "revisionCreateTime",
    "state",
    "stateError",
    "cryptoKeyVersion",
    "allKmsKeys",
    "allKmsKeysVersions",
}
_TYPED_FIELDS = {
    "sourceContents",
    "serviceAccount",
    "description",
    "callLogLevel",
    "executionHistoryLevel",
    "userEnvVars",
    "cryptoKeyName",
    "labels",
    "tags",
}


class WorkflowsError(RuntimeError):
    """A provider or contract error surfaced through managed-service results."""


class WorkflowsNotFound(WorkflowsError):
    pass


class WorkflowsAlreadyExists(WorkflowsError):
    pass


@dataclass(frozen=True)
class WorkflowsConfig:
    project_id: str
    region: str
    workflow_name_prefix: str = "astrolift"
    deletion_protection_default: bool = True
    call_log_level_default: str = "LOG_ERRORS_ONLY"
    execution_history_level_default: str = "EXECUTION_HISTORY_BASIC"
    api_endpoint: str = _API_ROOT
    executions_api_endpoint: str = _EXECUTIONS_API_ROOT
    operation_timeout_seconds: float = 900.0
    poll_interval_seconds: float = 2.0


class WorkflowsRestClient:
    """Authenticated adapter for Workflows v1 and Executions v1 JSON APIs."""

    def __init__(
        self,
        *,
        api_endpoint: str = _API_ROOT,
        executions_api_endpoint: str = _EXECUTIONS_API_ROOT,
        session: Any | None = None,
    ) -> None:
        self._api_endpoint = api_endpoint.rstrip("/")
        self._executions_api_endpoint = executions_api_endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",),
            )
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get_workflow(self, name: str, *, revision_id: str = "") -> dict[str, Any]:
        params = {"revisionId": revision_id} if revision_id else None
        return self._request("GET", name, params=params)

    def create_workflow(
        self,
        parent: str,
        workflow_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/workflows",
            params={"workflowId": workflow_id},
            json=body,
        )

    def patch_workflow(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json={"name": name, **body},
        )

    def delete_workflow(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", name)

    def list_revisions(self, name: str) -> list[dict[str, Any]]:
        return self._paged(
            f"{name}:listRevisions",
            key="workflows",
            page_size="100",
        )

    def get_operation(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def list_executions(
        self,
        workflow_name: str,
        *,
        filter_expression: str = "",
        view: str = "BASIC",
    ) -> list[dict[str, Any]]:
        extra: dict[str, str] = {"view": view}
        if filter_expression:
            extra["filter"] = filter_expression
        return self._paged(
            f"{workflow_name}/executions",
            key="executions",
            page_size="1000" if view == "BASIC" else "100",
            endpoint=self._executions_api_endpoint,
            extra_params=extra,
        )

    def create_execution(self, workflow_name: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{workflow_name}/executions",
            json=body,
            endpoint=self._executions_api_endpoint,
        )

    def cancel_execution(self, name: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{name}:cancel",
            json={},
            endpoint=self._executions_api_endpoint,
        )

    def _paged(
        self,
        resource: str,
        *,
        key: str,
        page_size: str,
        endpoint: str | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            params = {"pageSize": page_size, **(extra_params or {})}
            if token:
                params["pageToken"] = token
            response = self._request("GET", resource, params=params, endpoint=endpoint)
            rows.extend(dict(item) for item in response.get(key) or [])
            token = str(response.get("nextPageToken") or "")
            if not token:
                return rows

    def _request(
        self,
        method: str,
        resource: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        endpoint: str | None = None,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            f"{(endpoint or self._api_endpoint).rstrip('/')}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise WorkflowsNotFound(resource)
        if response.status_code == 409:
            raise WorkflowsAlreadyExists(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise WorkflowsError(f"Workflows HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class WorkflowsDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: WorkflowsConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._workflows = client or WorkflowsRestClient(
            api_endpoint=config.api_endpoint,
            executions_api_endpoint=config.executions_api_endpoint,
        )
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="workflows",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_workflows_config"])
        workflow_id = str(cfg.get("workflow_id") or self._workflow_id(spec))
        name = self._workflow_name(workflow_id)
        handle = _handle(name)
        try:
            current = self._get_workflow(name)
            if current is None:
                body = self._create_body(cfg, labels=_labels_for(spec, cfg))
                self._wait_operation(
                    self._workflows.create_workflow(self._parent(), workflow_id, body),
                )
            else:
                labels = self._claim_labels(current, spec, cfg)
                self._reconcile(name, current, cfg, labels=labels)
            current = self._workflows.get_workflow(name)
            self._assert_owned(current, spec)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Google Workflows: {exc}", [str(exc)])
        state = str(current.get("state") or "STATE_UNSPECIFIED")
        return ProvisionResult(
            True,
            handle,
            f"Google workflow {workflow_id} is {state.lower()}",
            ready=state == "ACTIVE",
        )

    @driver_op(cloud="gcp", driver="workflows")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            name = _parse_handle(spec.handle)
            self._assert_scope(name)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_workflows_config"])
        try:
            current = self._workflows.get_workflow(name)
            if not _owned_by_platform(current):
                return UpdateResult(
                    False,
                    spec.handle,
                    "refusing to update a workflow not owned by Astrolift",
                    ["resource_not_owned"],
                )
            labels = _preserved_ownership_labels(current, cfg)
            self._reconcile(name, current, cfg, labels=labels)
        except WorkflowsNotFound:
            return UpdateResult(False, spec.handle, "Google workflow not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Google Workflows: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, "Google workflow reconciled")

    @driver_op(
        cloud="gcp",
        driver="workflows",
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
            name = _parse_handle(spec.handle)
            self._assert_scope(name)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        try:
            workflow = self._workflows.get_workflow(name)
        except WorkflowsNotFound:
            return DeprovisionResult(True, spec.handle, "Google workflow already gone")
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"describe Google workflow: {exc}", [str(exc)])

        labels = dict(workflow.get("labels") or {})
        if not _owned_by_platform(workflow):
            return DeprovisionResult(
                False,
                spec.handle,
                "refusing to delete a workflow not owned by Astrolift",
                ["resource_not_owned"],
                retryable=False,
            )
        if labels.get("astrolift_io_adopted") == "true" and not cfg.get("delete_adopted_workflow"):
            return DeprovisionResult(
                False,
                spec.handle,
                "adopted workflow deletion requires delete_adopted_workflow=true",
                ["adopted_resource_delete_requires_opt_in"],
                retryable=False,
            )
        protected = bool(cfg.get("deletion_protection", self._config.deletion_protection_default))
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Google workflow has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "deleting a Google workflow destroys execution history and revisions; set delete_data=true",
                ["delete_data_required"],
                retryable=False,
            )
        try:
            running = self._active_executions(name)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"list workflow executions: {exc}", [str(exc)])
        if running and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"workflow has {len(running)} active or queued execution(s); force_destroy is required",
                ["running_executions"],
                retryable=False,
            )
        if force_destroy:
            for execution in running:
                execution_name = str(execution.get("name") or "")
                try:
                    self._workflows.cancel_execution(execution_name)
                except WorkflowsNotFound:
                    continue
                except Exception as exc:
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        f"cancel workflow execution {execution_name}: {exc}",
                        [str(exc)],
                    )
        try:
            self._wait_operation(self._workflows.delete_workflow(name))
        except WorkflowsNotFound:
            pass
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Google workflow: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, "Google workflow and execution history deleted")

    @driver_op(cloud="gcp", driver="workflows")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            name = _parse_handle(handle.handle)
            self._assert_scope(name)
            workflow = self._workflows.get_workflow(name)
        except WorkflowsNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Google workflow does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Google workflow: {exc}")
        provider_state = str(workflow.get("state") or "STATE_UNSPECIFIED")
        state = {
            "ACTIVE": "available",
            "UNAVAILABLE": "error",
        }.get(provider_state, "updating")
        detail = provider_state.lower()
        state_error = workflow.get("stateError") or {}
        if state_error:
            detail += f": {state_error.get('details') or state_error.get('type') or state_error}"
        return ServiceStatus(handle.handle, state, f"Google workflow is {detail}")

    @driver_op(cloud="gcp", driver="workflows")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        name = _parse_handle(handle.handle)
        project_id, region, workflow_id = self._assert_scope(name)
        workflow = self._workflows.get_workflow(name)
        if not _owned_by_platform(workflow):
            raise WorkflowsError("refusing to bind a workflow not owned by Astrolift")
        access_mode = str((config or {}).get("access_mode") or "invoke")
        role = {
            "invoke": "roles/workflows.invoker",
            "observe": "roles/workflows.viewer",
            "manage": "roles/workflows.editor",
        }.get(access_mode)
        if role is None:
            raise WorkflowsError("access_mode must be invoke, observe, or manage")
        executions_url = f"https://workflowexecutions.googleapis.com/v1/{name}/executions"
        console_url = (
            "https://console.cloud.google.com/workflows/workflow/"
            f"{region}/{quote(workflow_id, safe='')}?project={quote(project_id, safe='')}"
        )
        return Binding(
            env_vars={
                "WORKFLOW_ENGINE_ID": ValueRef(literal=workflow_id),
                "WORKFLOW_ENGINE_ARN": ValueRef(literal=name),
                "WORKFLOW_ENGINE_REGION": ValueRef(literal=region),
                "WORKFLOW_ENGINE_TYPE": ValueRef(literal="google_workflows"),
                "GCP_WORKFLOWS_NAME": ValueRef(literal=name),
                "GCP_WORKFLOWS_PROJECT": ValueRef(literal=project_id),
                "GCP_WORKFLOWS_LOCATION": ValueRef(literal=region),
                "GCP_WORKFLOWS_EXECUTIONS_URL": ValueRef(literal=executions_url),
                "GCP_WORKFLOWS_CONSOLE_URL": ValueRef(literal=console_url),
            },
            iam_grants=[Grant(resource=name, actions=[role])],
            notes=f"Google Workflows {access_mode} access through workload identity.",
        )

    @driver_op(cloud="gcp", driver="workflows")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        name = _parse_handle(handle.handle)
        self._assert_scope(name)
        workflow = self._workflows.get_workflow(name)
        if not _owned_by_platform(workflow):
            raise WorkflowsError("refusing to snapshot a workflow not owned by Astrolift")
        revisions = self._workflows.list_revisions(name)
        if not revisions:
            raise WorkflowsError("Google workflow has no revisions")
        latest = max(revisions, key=_revision_ordinal)
        revision_id = str(latest.get("revisionId") or "")
        if not _REVISION_PATTERN.fullmatch(revision_id):
            raise WorkflowsError(f"Google workflow returned invalid revision ID {revision_id!r}")
        created_at = str(latest.get("revisionCreateTime") or latest.get("updateTime") or "")
        if not created_at:
            created_at = datetime.now(UTC).isoformat()
        return SnapshotHandle(handle.handle, f"{name}@{revision_id}", created_at)

    @driver_op(cloud="gcp", driver="workflows")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        try:
            source_name, revision_id = _parse_snapshot_id(snapshot.snapshot_id)
            match = _WORKFLOW_NAME_PATTERN.fullmatch(source_name)
            if match is None:  # Defensive; _parse_snapshot_id already validates this.
                raise ValueError(f"invalid Google workflow resource name {source_name!r}")
            source_project = match.group(1)
        except ValueError as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_snapshot"])
        target_cfg = dict(target.config or {})
        if source_project != self._config.project_id and not target_cfg.get("allow_cross_project_snapshot"):
            return ProvisionResult(
                False,
                "",
                "cross-project workflow restore requires allow_cross_project_snapshot=true",
                ["cross_project_snapshot_requires_opt_in"],
            )
        try:
            revision = self._workflows.get_workflow(source_name, revision_id=revision_id)
        except Exception as exc:
            return ProvisionResult(False, "", f"read Google workflow revision: {exc}", [str(exc)])
        if not _owned_by_platform(revision) and not target_cfg.get("allow_unowned_snapshot"):
            return ProvisionResult(
                False,
                "",
                "restoring an unowned workflow revision requires allow_unowned_snapshot=true",
                ["unowned_snapshot_requires_opt_in"],
            )
        source_contents = revision.get("sourceContents")
        if not isinstance(source_contents, str) or not source_contents:
            return ProvisionResult(False, "", "workflow revision has no source contents", ["invalid_snapshot"])
        target_cfg.pop("definition", None)
        target_cfg["source_contents"] = source_contents
        for config_key, provider_key in (
            ("service_account", "serviceAccount"),
            ("description", "description"),
            ("call_log_level", "callLogLevel"),
            ("execution_history_level", "executionHistoryLevel"),
            ("user_env_vars", "userEnvVars"),
            ("crypto_key_name", "cryptoKeyName"),
        ):
            if config_key not in target_cfg and provider_key in revision:
                target_cfg[config_key] = revision[provider_key]
        return self.provision(replace(target, config=target_cfg))

    @driver_op(cloud="gcp", driver="workflows", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "pattern": "^[A-Za-z](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$",
                },
                "source_contents": {
                    "description": "Resolved Workflows YAML/JSON source, at most 128 KiB.",
                },
                "definition": {
                    "description": "Portable alias for source_contents; objects serialize as JSON.",
                },
                "service_account": {"type": "string"},
                "description": {"type": "string", "maxLength": 1000},
                "call_log_level": {
                    "type": "string",
                    "enum": sorted(_CALL_LOG_LEVELS),
                    "default": self._config.call_log_level_default,
                },
                "execution_history_level": {
                    "type": "string",
                    "enum": sorted(_HISTORY_LEVELS),
                    "default": self._config.execution_history_level_default,
                },
                "user_env_vars": {
                    "type": "object",
                    "maxProperties": 20,
                    "additionalProperties": {"type": "string", "maxLength": 4096},
                },
                "crypto_key_name": {"type": "string"},
                "labels": {"type": "object", "additionalProperties": {"type": "string"}},
                "resource_tags": {
                    "type": "object",
                    "description": "Immutable Resource Manager tags applied only when creating the workflow.",
                    "additionalProperties": {"type": "string"},
                },
                "workflow": {
                    "type": "object",
                    "description": (
                        "Future provider-native Workflow input fields. Output, ownership, and first-class "
                        "fields cannot be overridden here."
                    ),
                },
                "access_mode": {
                    "type": "string",
                    "enum": ["invoke", "observe", "manage"],
                    "default": "invoke",
                },
                "deletion_protection": {"type": "boolean", "default": True},
                "adopt_existing": {"type": "boolean", "default": False},
                "allow_reassignment": {"type": "boolean", "default": False},
                "delete_adopted_workflow": {"type": "boolean", "default": False},
                "allow_cross_project_snapshot": {"type": "boolean", "default": False},
                "allow_unowned_snapshot": {"type": "boolean", "default": False},
            },
            "oneOf": [{"required": ["source_contents"]}, {"required": ["definition"]}],
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="workflows", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "WORKFLOW_ENGINE_ID": "Workflow ID",
                "WORKFLOW_ENGINE_ARN": "Full Google workflow resource name",
                "WORKFLOW_ENGINE_REGION": "Google workflow location",
                "WORKFLOW_ENGINE_TYPE": "google_workflows",
                "GCP_WORKFLOWS_NAME": "Full Google workflow resource name",
                "GCP_WORKFLOWS_PROJECT": "Google Cloud project ID",
                "GCP_WORKFLOWS_LOCATION": "Google workflow location",
                "GCP_WORKFLOWS_EXECUTIONS_URL": "Workflow Executions v1 collection URL",
                "GCP_WORKFLOWS_CONSOLE_URL": "Google Cloud console workflow URL",
            },
        )

    def editable_fields(self) -> list[str]:
        return ["*"]

    def start_execution(
        self,
        handle: ServiceHandle,
        *,
        argument: Any = None,
        labels: dict[str, str] | None = None,
        call_log_level: str = "",
        execution_history_level: str = "",
        disable_concurrency_buffering: bool = False,
    ) -> dict[str, Any]:
        """Start an execution for control-plane callers using the same validated contract."""
        name = _parse_handle(handle.handle)
        self._assert_scope(name)
        workflow = self._workflows.get_workflow(name)
        if not _owned_by_platform(workflow):
            raise WorkflowsError("refusing to execute a workflow not owned by Astrolift")
        body: dict[str, Any] = {}
        if argument is not None:
            serialized = argument if isinstance(argument, str) else json.dumps(argument, separators=(",", ":"))
            if len(serialized.encode("utf-8")) > _ARGUMENT_LIMIT_BYTES:
                raise WorkflowsError("workflow execution argument exceeds 32 KiB")
            body["argument"] = serialized
        if labels:
            _validate_labels(labels, allow_ownership=False)
            body["labels"] = dict(labels)
        if call_log_level:
            if call_log_level not in _CALL_LOG_LEVELS:
                raise WorkflowsError(f"invalid execution call_log_level {call_log_level!r}")
            body["callLogLevel"] = call_log_level
        if execution_history_level:
            if execution_history_level not in _HISTORY_LEVELS:
                raise WorkflowsError(f"invalid execution history level {execution_history_level!r}")
            body["executionHistoryLevel"] = execution_history_level
        if disable_concurrency_buffering:
            body["disableConcurrencyQuotaOverflowBuffering"] = True
        return self._workflows.create_execution(name, body)

    def _create_body(self, cfg: dict[str, Any], *, labels: dict[str, str]) -> dict[str, Any]:
        body = self._desired_body(cfg, labels=labels)
        tags = cfg.get("resource_tags")
        if tags:
            body["tags"] = dict(tags)
        return body

    def _desired_body(self, cfg: dict[str, Any], *, labels: dict[str, str]) -> dict[str, Any]:
        body = dict(cfg.get("workflow") or {})
        body.update(
            sourceContents=_source_contents(cfg),
            description=str(cfg.get("description") or ""),
            callLogLevel=str(cfg.get("call_log_level") or self._config.call_log_level_default),
            executionHistoryLevel=str(
                cfg.get("execution_history_level") or self._config.execution_history_level_default,
            ),
            userEnvVars=dict(cfg.get("user_env_vars") or {}),
            labels=labels,
        )
        body["serviceAccount"] = str(cfg.get("service_account") or "")
        body["cryptoKeyName"] = str(cfg.get("crypto_key_name") or "")
        return body

    def _reconcile(
        self,
        name: str,
        current: dict[str, Any],
        cfg: dict[str, Any],
        *,
        labels: dict[str, str],
    ) -> None:
        desired = self._desired_body(cfg, labels=labels)
        patch: dict[str, Any] = {}
        mask: list[str] = []
        for field, value in desired.items():
            actual = current.get(field)
            if field in {"labels", "userEnvVars"}:
                actual = dict(actual or {})
            if actual != value:
                patch[field] = value
                mask.append(field)
        if mask:
            self._wait_operation(self._workflows.patch_workflow(name, patch, update_mask=mask))

    def _claim_labels(
        self,
        current: dict[str, Any],
        spec: ProvisionSpec,
        cfg: dict[str, Any],
    ) -> dict[str, str]:
        actual = dict(current.get("labels") or {})
        desired = _labels_for(spec, cfg)
        if not _owned_by_platform(current):
            if not cfg.get("adopt_existing"):
                raise WorkflowsError(
                    "existing workflow is not Astrolift-managed; set adopt_existing=true",
                )
            desired["astrolift_io_adopted"] = "true"
            return desired
        boundary_keys = (
            "astrolift_io_organization",
            "astrolift_io_app",
            "astrolift_io_environment",
            "astrolift_io_cluster",
        )
        mismatched = [key for key in boundary_keys if actual.get(key) != desired.get(key)]
        if mismatched and not cfg.get("allow_reassignment"):
            raise WorkflowsError(
                "workflow belongs to another Astrolift boundary; set allow_reassignment=true",
            )
        if mismatched:
            desired["astrolift_io_reassigned"] = "true"
        if actual.get("astrolift_io_adopted") == "true":
            desired["astrolift_io_adopted"] = "true"
        return desired

    def _assert_owned(self, workflow: dict[str, Any], spec: ProvisionSpec) -> None:
        labels = dict(workflow.get("labels") or {})
        expected = _labels_for(spec, {})
        if not _owned_by_platform(workflow):
            raise WorkflowsError("workflow ownership label was not persisted")
        for key in (
            "astrolift_io_organization",
            "astrolift_io_app",
            "astrolift_io_environment",
            "astrolift_io_cluster",
        ):
            if labels.get(key) != expected.get(key):
                raise WorkflowsError(f"workflow ownership boundary label {key} was not persisted")

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        unknown = sorted(set(cfg) - set(self.config_schema()["properties"]))
        if unknown:
            return f"unknown Google Workflows config keys: {', '.join(unknown)}"
        if ("source_contents" in cfg) == ("definition" in cfg):
            return "exactly one of source_contents or definition is required"
        try:
            source = _source_contents(cfg)
        except (TypeError, ValueError) as exc:
            return str(exc)
        if not source.strip():
            return "workflow source cannot be empty"
        if len(source.encode("utf-8")) > _SOURCE_LIMIT_BYTES:
            return "workflow source exceeds the Google Workflows 128 KiB limit"
        workflow_id = cfg.get("workflow_id")
        if workflow_id is not None and (not isinstance(workflow_id, str) or not _ID_PATTERN.fullmatch(workflow_id)):
            return "workflow_id must be 2-64 letters, numbers, underscores, or hyphens"
        description = cfg.get("description")
        if description is not None and (not isinstance(description, str) or len(description) > 1000):
            return "description must be a string of at most 1000 characters"
        call_level = str(cfg.get("call_log_level") or self._config.call_log_level_default)
        if call_level not in _CALL_LOG_LEVELS:
            return f"invalid call_log_level {call_level!r}"
        history_level = str(
            cfg.get("execution_history_level") or self._config.execution_history_level_default,
        )
        if history_level not in _HISTORY_LEVELS:
            return f"invalid execution_history_level {history_level!r}"
        env_error = _validate_user_env_vars(cfg.get("user_env_vars"))
        if env_error:
            return env_error
        try:
            _validate_labels(cfg.get("labels") or {}, allow_ownership=False)
        except WorkflowsError as exc:
            return str(exc)
        for key in ("resource_tags", "workflow"):
            if key in cfg and not isinstance(cfg[key], dict):
                return f"{key} must be an object"
        if cfg.get("resource_tags") and not all(
            isinstance(key, str) and isinstance(value, str) for key, value in cfg["resource_tags"].items()
        ):
            return "resource_tags keys and values must be strings"
        raw = cfg.get("workflow") or {}
        reserved = sorted(set(raw).intersection(_OUTPUT_FIELDS | _TYPED_FIELDS))
        if reserved:
            return f"workflow cannot override Astrolift-owned fields: {', '.join(reserved)}"
        access_mode = str(cfg.get("access_mode") or "invoke")
        if access_mode not in {"invoke", "observe", "manage"}:
            return "access_mode must be invoke, observe, or manage"
        return ""

    def _active_executions(self, name: str) -> list[dict[str, Any]]:
        executions = self._workflows.list_executions(
            name,
            filter_expression='state="ACTIVE" OR state="QUEUED"',
        )
        return [item for item in executions if str(item.get("state") or "") in _ACTIVE_EXECUTION_STATES]

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation:
            return {}
        current = operation
        name = str(operation.get("name") or "")
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        while not current.get("done"):
            if not name:
                raise WorkflowsError("Workflows operation response has no name")
            if self._monotonic() >= deadline:
                raise WorkflowsError(f"Workflows operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._workflows.get_operation(name)
        if current.get("error"):
            error = current["error"]
            raise WorkflowsError(f"Workflows operation {name} failed: {error.get('message') or error}")
        return current

    def _get_workflow(self, name: str) -> dict[str, Any] | None:
        try:
            return self._workflows.get_workflow(name)
        except WorkflowsNotFound:
            return None

    def _workflow_id(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            filter(
                None,
                (
                    self._config.workflow_name_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint or "workflow",
                ),
            ),
        )
        return _resource_id(raw, maximum=64)

    def _parent(self) -> str:
        return f"projects/{self._config.project_id}/locations/{self._config.region}"

    def _workflow_name(self, workflow_id: str) -> str:
        return f"{self._parent()}/workflows/{workflow_id}"

    def _assert_scope(
        self,
        name: str,
        *,
        allow_location_mismatch: bool = False,
    ) -> tuple[str, str, str]:
        match = _WORKFLOW_NAME_PATTERN.fullmatch(name)
        if not match:
            raise ValueError(f"invalid Google workflow resource name {name!r}")
        project_id, location, workflow_id = match.groups()
        if project_id != self._config.project_id:
            raise ValueError("workflow handle project does not match configured GCP project")
        if location != self._config.region and not allow_location_mismatch:
            raise ValueError("workflow handle location does not match configured GCP region")
        return project_id, location, workflow_id


def _source_contents(cfg: dict[str, Any]) -> str:
    value = cfg.get("source_contents") if "source_contents" in cfg else cfg.get("definition")
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    raise TypeError("source_contents or definition must be a string, object, or array")


def _labels_for(spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, str]:
    labels = {
        "astrolift_io_managed_by": "platform",
        "astrolift_io_organization": _label_value(spec.organization_slug),
        "astrolift_io_app": _label_value(spec.app_slug),
        "astrolift_io_environment": _label_value(spec.environment_name),
        "astrolift_io_cluster": _label_value(spec.tenant_cluster_id),
        "astrolift_io_resource_hint": _label_value(spec.service_handle_hint or "workflow"),
    }
    if spec.binding_id:
        labels["astrolift_io_binding"] = _label_value(spec.binding_id)
    if spec.managed_service_id:
        labels["astrolift_io_managed_service_id"] = _label_value(spec.managed_service_id)
    for key, value in spec.tags.items():
        normalized = _label_key(key)
        if normalized not in _OWNERSHIP_KEYS:
            labels[normalized] = _label_value(value)
    for key, value in (cfg.get("labels") or {}).items():
        labels[str(key)] = str(value)
    _validate_labels(labels, allow_ownership=True)
    return labels


def _preserved_ownership_labels(current: dict[str, Any], cfg: dict[str, Any]) -> dict[str, str]:
    labels = {key: str(value) for key, value in (current.get("labels") or {}).items() if key in _OWNERSHIP_KEYS}
    for key, value in (cfg.get("labels") or {}).items():
        labels[str(key)] = str(value)
    _validate_labels(labels, allow_ownership=True)
    return labels


def _validate_labels(labels: Any, *, allow_ownership: bool) -> None:
    if not isinstance(labels, dict):
        raise WorkflowsError("labels must be an object")
    if len(labels) > 64:
        raise WorkflowsError("Google workflow labels cannot exceed 64 entries")
    for key, value in labels.items():
        if not isinstance(key, str) or not _valid_label_token(key, require_letter=True):
            raise WorkflowsError(f"invalid Google workflow label key {key!r}")
        if not isinstance(value, str) or not _valid_label_token(value, require_letter=False):
            raise WorkflowsError(f"invalid Google workflow label value for {key!r}")
        if not allow_ownership and key in _OWNERSHIP_KEYS:
            raise WorkflowsError(f"label {key!r} is reserved for Astrolift ownership")


def _validate_user_env_vars(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, dict):
        return "user_env_vars must be an object"
    if len(value) > 20:
        return "user_env_vars cannot exceed 20 entries"
    for key, item in value.items():
        if not isinstance(key, str) or not key or key.upper().startswith(("GOOGLE", "WORKFLOWS")):
            return f"invalid or reserved workflow environment key {key!r}"
        if not isinstance(item, str) or len(item.encode("utf-8")) > 4096:
            return f"workflow environment value {key!r} must be a string of at most 4 KiB"
    return ""


def _valid_label_token(value: str, *, require_letter: bool) -> bool:
    if len(value) > 63:
        return False
    if not value:
        return not require_letter
    if require_letter and not value[0].isalpha():
        return False
    if value != value.lower():
        return False
    return all(character.isalnum() or character in "_-" for character in value)


def _owned_by_platform(workflow: dict[str, Any]) -> bool:
    return (workflow.get("labels") or {}).get("astrolift_io_managed_by") == "platform"


def _handle(name: str) -> str:
    return f"{KIND}/{name}"


def _parse_handle(handle: str) -> str:
    prefix = f"{KIND}/"
    if not handle.startswith(prefix):
        raise ValueError(f"handle {handle!r} must start with {prefix!r}")
    name = handle[len(prefix) :]
    if not _WORKFLOW_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"handle {handle!r} does not contain a valid Google workflow name")
    return name


def _parse_snapshot_id(snapshot_id: str) -> tuple[str, str]:
    name, separator, revision_id = snapshot_id.rpartition("@")
    if not separator or not _WORKFLOW_NAME_PATTERN.fullmatch(name) or not _REVISION_PATTERN.fullmatch(revision_id):
        raise ValueError(f"invalid Google workflow snapshot ID {snapshot_id!r}")
    return name, revision_id


def _resource_id(value: str, *, maximum: int) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"a-{normalized}"
    if len(normalized) > maximum:
        digest = hashlib.sha256(normalized.encode()).hexdigest()[:10]
        normalized = f"{normalized[: maximum - len(digest) - 1].rstrip('-_')}-{digest}"
    normalized = normalized.rstrip("-_")
    if len(normalized) < 2:
        normalized = f"{normalized or 'a'}0"
    return normalized


def _label_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", str(value).lower()).strip("_-")
    if not normalized or not normalized[0].isalpha():
        normalized = f"a_{normalized}"
    return normalized[:63].rstrip("_-")


def _label_value(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", str(value).lower()).strip("_-")[:63]


def _revision_ordinal(revision: dict[str, Any]) -> tuple[int, str]:
    revision_id = str(revision.get("revisionId") or "")
    head, _, tail = revision_id.partition("-")
    return (int(head) if head.isdigit() else -1, tail)
