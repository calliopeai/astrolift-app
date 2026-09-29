"""Google Cloud Run functions (Cloud Functions v2) managed-service driver."""

from __future__ import annotations

import re
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

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
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp._raw_fields import raw_field_conflicts
from gcp.managed._ownership import is_platform_label_key, label_identity_refusal, reserved_label_keys

KIND = "faas"
_MISSING_IDENTITY = "Cloud Run functions need the managed-service id to mark the function they own"
_API_ROOT = "https://cloudfunctions.googleapis.com/v2"
_FUNCTION_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,61}[a-z0-9]$")
_SECRET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_OUTPUT_ONLY = {
    "createTime",
    "name",
    "satisfiesPzi",
    "satisfiesPzs",
    "state",
    "stateMessages",
    "updateTime",
    "upgradeInfo",
    "url",
}
_STRUCTURED_TOP_LEVEL = {
    "buildConfig",
    "description",
    "environment",
    "eventTrigger",
    "kmsKeyName",
    "labels",
    "serviceConfig",
}
_STRUCTURED_BUILD = {
    "automaticUpdatePolicy",
    "dockerRepository",
    "entryPoint",
    "environmentVariables",
    "onDeployUpdatePolicy",
    "runtime",
    "serviceAccount",
    "source",
    "workerPool",
}
_STRUCTURED_SERVICE = {
    "allTrafficOnLatestRevision",
    "availableCpu",
    "availableMemory",
    "binaryAuthorizationPolicy",
    "directVpcEgress",
    "directVpcNetworkInterface",
    "environmentVariables",
    "ingressSettings",
    "maxInstanceCount",
    "maxInstanceRequestConcurrency",
    "minInstanceCount",
    "secretEnvironmentVariables",
    "secretVolumes",
    "serviceAccountEmail",
    "timeoutSeconds",
    "vpcConnector",
    "vpcConnectorEgressSettings",
}
_STRUCTURED_EVENT = {
    "channel",
    "eventFilters",
    "eventType",
    "pubsubTopic",
    "retryPolicy",
    "serviceAccountEmail",
    "triggerRegion",
}


class CloudFunctionsError(RuntimeError):
    pass


class CloudFunctionsNotFound(CloudFunctionsError):
    pass


class CloudFunctionsConflict(CloudFunctionsError):
    pass


@dataclass(frozen=True)
class CloudFunctionsConfig:
    project_id: str
    region: str
    function_name_prefix: str = "astrolift"
    api_endpoint: str = _API_ROOT
    deletion_protection_default: bool = True
    operation_timeout_seconds: float = 1800
    poll_interval_seconds: float = 5
    allowed_service_accounts: tuple[str, ...] = ()
    """Service account emails a config may run the function or its build as.

    The function's code and its build steps come from the tenant and run as
    that identity, so they can read whatever it can read, Secret Manager
    included. Empty refuses every config-supplied account, which leaves
    Google's default runtime service account, as when the field is omitted."""


class CloudFunctionsRestClient:
    """Authenticated request-shaped adapter for the Cloud Functions v2 API."""

    def __init__(self, *, endpoint: str = _API_ROOT, session: Any | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",),
            )
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def create(self, parent: str, function_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/functions",
            params={"functionId": function_id},
            json=body,
        )

    def patch(self, name: str, body: dict[str, Any], update_mask: list[str]) -> dict[str, Any]:
        return self._request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json={"name": name, **body},
        )

    def delete(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", name)

    def get_operation(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def _request(
        self,
        method: str,
        resource: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            f"{self._endpoint}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise CloudFunctionsNotFound(resource)
        if response.status_code == 409:
            raise CloudFunctionsConflict(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise CloudFunctionsError(f"Cloud Functions HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise CloudFunctionsError("Cloud Functions returned a non-object response")
        return dict(payload)


class CloudFunctionsDriver(ManagedServiceDriver):
    KIND = KIND

    def __init__(
        self,
        *,
        config: CloudFunctionsConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._functions = client or CloudFunctionsRestClient(endpoint=config.api_endpoint)
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="faas_cloud_functions_gen2",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate(cfg, update=False)
        if error:
            return ProvisionResult(False, "", error, ["invalid_cloud_functions_config"])
        if not spec.managed_service_id:
            return ProvisionResult(False, "", _MISSING_IDENTITY, ["invalid_cloud_functions_config"])
        region = str(cfg.get("region") or self._config.region)
        function_id = self._function_id(spec, cfg)
        handle = _handle(region, function_id)
        name = self._name(region, function_id)
        labels = self._labels(spec, cfg)
        record_proves = spec.recorded_handle_exclusive and spec.recorded_handle == handle
        try:
            current = self._get(name)
            body = self._body(cfg, labels, partial=False)
            if current is None:
                try:
                    self._wait(self._functions.create(self._parent(region), function_id, body))
                    current = self._functions.get(name)
                except CloudFunctionsConflict:
                    current = self._functions.get(name)
                    self._assert_owned(current, spec.managed_service_id, "function", record_proves=record_proves)
                    labels = self._merged_labels(current, labels)
                    body = self._body(cfg, labels, partial=False)
                    self._patch(name, current, body)
            else:
                self._assert_owned(current, spec.managed_service_id, "function", record_proves=record_proves)
                labels = self._merged_labels(current, labels)
                body = self._body(cfg, labels, partial=False)
                self._patch(name, current, body)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Cloud Run function: {exc}", [str(exc)])
        ready = str((current or {}).get("state") or "") == "ACTIVE"
        return ProvisionResult(
            True,
            handle,
            f"Cloud Run function {function_id} reconciled",
            ready=ready,
        )

    @driver_op(cloud="gcp", driver="faas_cloud_functions_gen2")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            region, function_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate(cfg, update=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_cloud_functions_config"])
        name = self._name(region, function_id)
        try:
            current = self._functions.get(name)
            self._assert_owned(
                current,
                spec.managed_service_id,
                "function",
                record_proves=spec.recorded_handle_exclusive,
            )
            # The live map is the base, but the identity comes from the spec:
            # an id planted on the function is written over, never read back
            # (#2098). ``_patch`` sends nothing when it already matches.
            labels = {**dict(current.get("labels") or {}), **_identity_labels(spec.managed_service_id)}
            body = self._body(cfg, labels, partial=True)
            body["labels"] = _platform_last(labels, _normalized_labels(cfg.get("labels") or {}))
            self._patch(name, current, body)
        except CloudFunctionsNotFound:
            return UpdateResult(False, spec.handle, "Cloud Run function not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Cloud Run function: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Cloud Run function {function_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="faas_cloud_functions_gen2",
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
        del delete_data
        try:
            region, function_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        name = self._name(region, function_id)
        current = self._get(name)
        if current is None:
            return DeprovisionResult(True, spec.handle, f"Cloud Run function {function_id} already gone")
        try:
            self._assert_owned(
                current,
                spec.managed_service_id,
                "function",
                record_proves=spec.recorded_handle_exclusive,
            )
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["ownership_guard"], retryable=False)
        labels = dict(current.get("labels") or {})
        if labels.get("astrolift-io-adopted") == "true" and not cfg.get("delete_adopted"):
            return DeprovisionResult(
                False,
                spec.handle,
                "adopted Cloud Run functions require delete_adopted=true",
                ["adopted_resource_guard"],
                retryable=False,
            )
        if bool(cfg.get("deletion_protection", self._config.deletion_protection_default)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Cloud Run function deletion protection is enabled; use force_destroy",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            self._wait(self._functions.delete(name))
        except CloudFunctionsNotFound:
            pass
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Cloud Run function: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"Cloud Run function {function_id} deleted")

    @driver_op(cloud="gcp", driver="faas_cloud_functions_gen2")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            region, function_id = _parse_handle(handle.handle)
            function = self._functions.get(self._name(region, function_id))
        except CloudFunctionsNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Cloud Run function does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Cloud Run function: {exc}")
        provider_state = str(function.get("state") or "STATE_UNSPECIFIED")
        state = {
            "ACTIVE": "available",
            "DEPLOYING": "provisioning",
            "DELETING": "deprovisioning",
            "DETACHING": "updating",
        }.get(provider_state, "error")
        messages = "; ".join(
            str(item.get("message") or "") for item in function.get("stateMessages") or [] if item.get("message")
        )
        return ServiceStatus(
            handle.handle,
            state,
            f"Cloud Run function {function_id} is {provider_state}" + (f": {messages}" if messages else ""),
        )

    @driver_op(cloud="gcp", driver="faas_cloud_functions_gen2")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        region, function_id = _parse_handle(handle.handle)
        name = self._name(region, function_id)
        function = self._functions.get(name)
        # The grants below are the payoff of a handle two services record.
        self._assert_owned(
            function,
            handle.managed_service_id,
            "function",
            record_proves=handle.recorded_handle_exclusive,
        )
        uri = str((function.get("serviceConfig") or {}).get("uri") or function.get("url") or "")
        access_mode = str((config or {}).get("access_mode") or "invoke")
        grants: list[Grant] = []
        if access_mode in {"invoke", "manage"}:
            grants.append(Grant(f"projects/{self._config.project_id}", ["roles/run.invoker"]))
        if access_mode == "manage":
            grants.append(Grant(f"projects/{self._config.project_id}", ["roles/cloudfunctions.developer"]))
        return Binding(
            env_vars={
                "FUNCTION_NAME": ValueRef(literal=function_id),
                "FUNCTION_URL": ValueRef(literal=uri),
                # The rest of the faas envelope (#1402). FUNCTION_ARN is the
                # portable resource-locator slot; on GCP that is the fully
                # qualified Cloud Functions v2 resource name.
                "FUNCTION_ARN": ValueRef(literal=name),
                "FUNCTION_REGION": ValueRef(literal=region),
                "GCP_CLOUD_FUNCTION_NAME": ValueRef(literal=name),
                "GCP_CLOUD_FUNCTION_URL": ValueRef(literal=uri),
                "GOOGLE_CLOUD_PROJECT": ValueRef(literal=self._config.project_id),
                "GOOGLE_CLOUD_REGION": ValueRef(literal=region),
            },
            iam_grants=grants,
            notes=(
                "Cloud Run functions v2 require an identity token for authenticated HTTP invocation; "
                "the current GCP workload-identity adapter applies roles at project scope."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise CloudFunctionsError(
            "Cloud Run functions have no snapshot API; retain the immutable source artifact and declaration",
        )

    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        return ProvisionResult(
            False,
            "",
            "Cloud Run functions restore by redeploying source, not from a service snapshot",
            ["not_supported"],
        )

    @driver_op(cloud="gcp", driver="faas_cloud_functions_gen2", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        raw = {
            "type": "object",
            "description": "Unknown provider-native Cloud Functions v2 fields; structured fields cannot be overridden.",
            "additionalProperties": True,
        }
        string_map = {"type": "object", "additionalProperties": {"type": "string"}}
        storage_source = {
            "type": "object",
            "required": ["bucket", "object"],
            "properties": {
                "bucket": {"type": "string"},
                "object": {"type": "string"},
                "generation": {"type": "string", "pattern": "^[0-9]+$"},
            },
            "additionalProperties": False,
        }
        repo_source = {
            "type": "object",
            "required": ["repo_name"],
            "properties": {
                "repo_name": {"type": "string"},
                "project_id": {"type": "string"},
                "dir": {"type": "string"},
                "branch_name": {"type": "string"},
                "tag_name": {"type": "string"},
                "commit_sha": {"type": "string"},
            },
            "additionalProperties": False,
        }
        secret_env = {
            "type": "object",
            "required": ["key", "secret", "version"],
            "properties": {
                "key": {"type": "string"},
                "secret": {"type": "string"},
                "version": {"type": "string"},
                "project_id": {"type": "string"},
            },
            "additionalProperties": False,
        }
        secret_volume = {
            "type": "object",
            "required": ["mount_path", "secret", "versions"],
            "properties": {
                "mount_path": {"type": "string"},
                "secret": {"type": "string"},
                "project_id": {"type": "string"},
                "versions": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["version", "path"],
                        "properties": {
                            "version": {"type": "string"},
                            "path": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        }
        direct_vpc = {
            "type": "object",
            "properties": {
                "network": {"type": "string"},
                "subnetwork": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        }
        event_filter = {
            "type": "object",
            "required": ["attribute", "value"],
            "properties": {
                "attribute": {"type": "string"},
                "value": {"type": "string"},
                "operator": {"type": "string", "enum": ["match-path-pattern"]},
            },
            "additionalProperties": False,
        }
        event_trigger = {
            "type": "object",
            "required": ["event_type"],
            "properties": {
                "event_type": {"type": "string"},
                "trigger_region": {"type": "string"},
                "pubsub_topic": {"type": "string"},
                "service_account_email": {"type": "string"},
                "retry_policy": {
                    "type": "string",
                    "enum": ["RETRY_POLICY_UNSPECIFIED", "RETRY_POLICY_DO_NOT_RETRY", "RETRY_POLICY_RETRY"],
                },
                "channel": {"type": "string"},
                "event_filters": {"type": "array", "items": event_filter},
                "raw_fields": raw,
            },
            "additionalProperties": False,
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "function_id": {"type": "string", "pattern": _FUNCTION_ID_RE.pattern},
                "region": {"type": "string"},
                "runtime": {"type": "string"},
                "entry_point": {"type": "string"},
                "storage_source": storage_source,
                "repo_source": repo_source,
                "build_environment": string_map,
                "build_service_account": {"type": "string"},
                "worker_pool": {"type": "string"},
                "docker_repository": {"type": "string"},
                "automatic_runtime_updates": {"type": "boolean"},
                "available_memory": {"type": "string"},
                "available_cpu": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1},
                "min_instances": {"type": "integer", "minimum": 0},
                "max_instances": {"type": "integer", "minimum": 1},
                "max_instance_concurrency": {"type": "integer", "minimum": 1},
                "environment": string_map,
                "secret_environment": {"type": "array", "items": secret_env},
                "secret_volumes": {"type": "array", "items": secret_volume},
                "allow_latest_secret_versions": {"type": "boolean", "default": False},
                "service_account_email": {"type": "string"},
                "ingress": {
                    "type": "string",
                    "enum": ["ALLOW_ALL", "ALLOW_INTERNAL_ONLY", "ALLOW_INTERNAL_AND_GCLB"],
                },
                "all_traffic_on_latest_revision": {"type": "boolean", "default": True},
                "vpc_connector": {"type": "string"},
                "vpc_connector_egress": {
                    "type": "string",
                    "enum": ["PRIVATE_RANGES_ONLY", "ALL_TRAFFIC"],
                },
                "direct_vpc_network_interface": {"type": "array", "maxItems": 1, "items": direct_vpc},
                "direct_vpc_egress": {
                    "type": "string",
                    "enum": ["VPC_EGRESS_PRIVATE_RANGES_ONLY", "VPC_EGRESS_ALL_TRAFFIC"],
                },
                "binary_authorization_policy": {"type": "string"},
                "kms_key": {"type": "string"},
                "event_trigger": event_trigger,
                "description": {"type": "string"},
                "labels": {"type": "object", "additionalProperties": {"type": "string"}},
                "raw_fields": raw,
                "build_raw_fields": raw,
                "service_raw_fields": raw,
                "clear_fields": {"type": "array", "items": {"type": "string"}},
                "delete_adopted": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
                "access_mode": {"type": "string", "enum": ["none", "invoke", "manage"]},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="faas_cloud_functions_gen2", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "FUNCTION_NAME": "Portable function ID",
                "FUNCTION_URL": "Portable authenticated HTTPS endpoint",
                "FUNCTION_ARN": "Portable resource locator; the Cloud Functions v2 resource name",
                "FUNCTION_REGION": "Google Cloud region",
                "GCP_CLOUD_FUNCTION_NAME": "Fully qualified Cloud Functions v2 resource name",
                "GCP_CLOUD_FUNCTION_URL": "Cloud Run service URI",
                "GOOGLE_CLOUD_PROJECT": "Google Cloud project ID",
                "GOOGLE_CLOUD_REGION": "Google Cloud region",
            },
        )

    def editable_fields(self) -> list[str]:
        return sorted(
            {
                *self.config_schema()["properties"],
            }
            - {"function_id", "region"},
        )

    def _validate(self, cfg: dict[str, Any], *, update: bool) -> str:
        if not self._config.project_id:
            return "Cloud Run functions require a Google Cloud project ID"
        if not str(cfg.get("region") or self._config.region):
            return "Cloud Run functions require a region"
        if update and ("region" in cfg or "function_id" in cfg):
            return "Cloud Run function region and function_id are immutable; reprovision the function"
        if "function_id" in cfg and not _FUNCTION_ID_RE.fullmatch(str(cfg["function_id"])):
            return "Cloud Run function_id must be 4-63 lowercase letters, numbers, or hyphens"
        if cfg.get("storage_source") and cfg.get("repo_source"):
            return "storage_source and repo_source are mutually exclusive"
        if not update:
            if not str(cfg.get("runtime") or ""):
                return "Cloud Run function provisioning requires runtime"
            if bool(cfg.get("storage_source")) == bool(cfg.get("repo_source")):
                return "Cloud Run function requires exactly one of storage_source or repo_source"
        storage_source = cfg.get("storage_source") or {}
        if storage_source and not all(str(storage_source.get(key) or "") for key in ("bucket", "object")):
            return "storage_source requires bucket and object"
        if storage_source.get("generation") is not None and not str(storage_source["generation"]).isdigit():
            return "storage_source generation must be numeric"
        repo_source = cfg.get("repo_source") or {}
        if repo_source:
            if not str(repo_source.get("repo_name") or ""):
                return "repo_source requires repo_name"
            revisions = [key for key in ("branch_name", "tag_name", "commit_sha") if str(repo_source.get(key) or "")]
            if len(revisions) != 1:
                return "repo_source requires exactly one of branch_name, tag_name, or commit_sha"
            source_dir = str(repo_source.get("dir") or "")
            if source_dir.startswith("/") or ".." in source_dir.split("/"):
                return "repo_source dir must stay within the source root"
        if cfg.get("vpc_connector") and cfg.get("direct_vpc_network_interface"):
            return "vpc_connector and direct_vpc_network_interface are mutually exclusive"
        if cfg.get("direct_vpc_network_interface") and len(cfg["direct_vpc_network_interface"]) != 1:
            return "Cloud Run functions support exactly one direct_vpc_network_interface"
        min_instances = int(cfg.get("min_instances") or 0)
        max_instances = int(cfg.get("max_instances") or 0)
        if max_instances and min_instances > max_instances:
            return "min_instances cannot exceed max_instances"
        allowed_accounts = {account.strip().casefold() for account in self._config.allowed_service_accounts}
        for field, value in (
            ("service_account_email", cfg.get("service_account_email")),
            ("build_service_account", cfg.get("build_service_account")),
            ("event_trigger.service_account_email", (cfg.get("event_trigger") or {}).get("service_account_email")),
        ):
            account = str(value or "").strip()
            # The build account is a resource name,
            # projects/<project or ->/serviceAccounts/<email>; the email names it.
            email = account.rsplit("/serviceAccounts/", 1)[-1]
            if account and email.casefold() not in allowed_accounts:
                return (
                    f"{field} {email!r} is not allowed by the cluster install policy "
                    "cloud_functions_allowed_service_accounts"
                )
        # _body sends these in the API's JSON spelling (_camelize). A dict that
        # spells one field two ways (project_id and projectId) would reach
        # Google as whichever came last, so it is refused, and the checks below
        # read each entry in the spelling Google gets (#1921).
        for field in ("storage_source", "repo_source", "secret_environment", "secret_volumes"):
            twice = _spelled_twice(cfg.get(field))
            if twice:
                return f"{field} spells {twice} more than one way"
        twice = _spelled_twice(cfg.get("direct_vpc_network_interface")) or _spelled_twice(
            (cfg.get("event_trigger") or {}).get("event_filters")
        )
        if twice:
            return f"a config field spells {twice} more than one way"
        environment = set((cfg.get("environment") or {}).keys())
        secret_keys: set[str] = set()
        allow_latest = bool(cfg.get("allow_latest_secret_versions"))
        for item in _camelize(cfg.get("secret_environment") or []):
            if not isinstance(item, dict):
                return "secret_environment entries must be objects"
            key = str(item.get("key") or "")
            if not key or key in secret_keys:
                return "secret_environment keys must be non-empty and unique"
            if not str(item.get("secret") or "") or not str(item.get("version") or ""):
                return "secret_environment entries require secret and version"
            if not self._secret_in_function_project(item):
                return (
                    f"secret_environment secrets must be secret ids in the function project {self._config.project_id}"
                )
            secret_keys.add(key)
            if str(item.get("version") or "") == "latest" and not allow_latest:
                return "latest secret_environment versions require allow_latest_secret_versions=true"
        if environment & secret_keys:
            return "environment and secret_environment cannot declare the same key"
        volume_mounts: set[str] = set()
        for volume in _camelize(cfg.get("secret_volumes") or []):
            if not isinstance(volume, dict):
                return "secret_volumes entries must be objects"
            mount_path = str(volume.get("mountPath") or "")
            if not mount_path.startswith("/"):
                return "secret volume mount_path must be absolute"
            if mount_path in volume_mounts:
                return "secret volume mount_path values must be unique"
            volume_mounts.add(mount_path)
            if not str(volume.get("secret") or "") or not list(volume.get("versions") or []):
                return "secret volumes require secret and at least one version"
            if not self._secret_in_function_project(volume):
                return f"secret volume secrets must be secret ids in the function project {self._config.project_id}"
            for version in volume.get("versions") or []:
                version_path = str(version.get("path") or "")
                if not str(version.get("version") or "") or not version_path:
                    return "secret volume versions require version and path"
                if version_path.startswith("/") or ".." in version_path.split("/"):
                    return "secret volume version path must stay under mount_path"
                if str(version.get("version") or "") == "latest" and not allow_latest:
                    return "latest secret volume versions require allow_latest_secret_versions=true"
        event_trigger = cfg.get("event_trigger") or {}
        if "event_trigger" in cfg and not str(event_trigger.get("event_type") or ""):
            return "event_trigger requires event_type"
        pubsub_topic = str(event_trigger.get("pubsub_topic") or "")
        if pubsub_topic and not pubsub_topic.startswith(f"projects/{self._config.project_id}/topics/"):
            return "event_trigger pubsub_topic must be in the function project"
        for event_filter in event_trigger.get("event_filters") or []:
            if not str(event_filter.get("attribute") or "") or not str(event_filter.get("value") or ""):
                return "event_trigger filters require attribute and value"
            if event_filter.get("operator") not in {None, "match-path-pattern"}:
                return "event_trigger filter operator must be match-path-pattern"
        if cfg.get("access_mode") not in {None, "none", "invoke", "manage"}:
            return "access_mode must be none, invoke, or manage"
        # Every spelling of every platform label, not only astrolift-io-*: the
        # canonical astrolift_io_managed_service_id slipped past a prefix check
        # (#2098).
        reserved_labels = reserved_label_keys(cfg.get("labels") or {})
        if reserved_labels:
            return f"labels cannot set Astrolift-reserved keys: {', '.join(reserved_labels)}"
        for values, protected, label, verb in (
            (cfg.get("raw_fields"), _OUTPUT_ONLY | _STRUCTURED_TOP_LEVEL, "raw_fields", "set"),
            (
                cfg.get("build_raw_fields"),
                _STRUCTURED_BUILD | {"build", "sourceProvenance"},
                "build_raw_fields",
                "set",
            ),
            (
                cfg.get("service_raw_fields"),
                _STRUCTURED_SERVICE | {"service", "uri", "revision", "securityLevel"},
                "service_raw_fields",
                "set",
            ),
            (
                (cfg.get("event_trigger") or {}).get("raw_fields"),
                _STRUCTURED_EVENT | {"trigger", "service"},
                "event_trigger.raw_fields",
                "set",
            ),
            (
                cfg.get("clear_fields"),
                _OUTPUT_ONLY | {"name", "labels", "environment", "buildConfig", "serviceConfig"},
                "clear_fields",
                "clear",
            ),
        ):
            error = _raw_names_error(values, protected, label, verb=verb)
            if error:
                return error
        return ""

    def _secret_in_function_project(self, entry: dict[str, Any]) -> bool:
        """Whether a secret entry, in the JSON spelling ``_body`` sends, names a
        secret id in the function's project.

        Google reads the secret with the function's runtime identity, so a
        ``project_id`` naming any other project, or a resource path in
        ``secret``, points that read outside the install (#1921). A project
        number cannot be matched against the configured id, so it is refused.
        """
        project = str(entry.get("projectId") or "").strip()
        secret = str(entry.get("secret") or "")
        return bool(_SECRET_ID_RE.fullmatch(secret)) and (not project or project == self._config.project_id)

    def _body(
        self,
        cfg: dict[str, Any],
        labels: dict[str, str],
        *,
        partial: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if not partial:
            body["environment"] = "GEN_2"
        if "description" in cfg:
            body["description"] = str(cfg["description"])
        if "kms_key" in cfg:
            body["kmsKeyName"] = str(cfg["kms_key"])

        build = self._build_config(cfg, partial=partial)
        if build:
            body["buildConfig"] = build
        service = self._service_config(cfg, partial=partial)
        if service:
            body["serviceConfig"] = service
        if "event_trigger" in cfg:
            event = dict(cfg.get("event_trigger") or {})
            event_body = _mapped_body(
                event,
                {
                    "event_type": "eventType",
                    "trigger_region": "triggerRegion",
                    "pubsub_topic": "pubsubTopic",
                    "service_account_email": "serviceAccountEmail",
                    "retry_policy": "retryPolicy",
                    "channel": "channel",
                    "event_filters": "eventFilters",
                },
            )
            if "eventFilters" in event_body:
                event_body["eventFilters"] = _camelize(event_body["eventFilters"])
            event_body.update(dict(event.get("raw_fields") or {}))
            body["eventTrigger"] = event_body
        body.update(dict(cfg.get("raw_fields") or {}))
        for field in cfg.get("clear_fields") or []:
            body[str(field)] = None
        if not partial or "labels" in cfg:
            body["labels"] = _platform_last(labels, _normalized_labels(cfg.get("labels") or {}))
        return body

    def _build_config(self, cfg: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        mapping = {
            "runtime": "runtime",
            "entry_point": "entryPoint",
            "build_environment": "environmentVariables",
            "build_service_account": "serviceAccount",
            "worker_pool": "workerPool",
            "docker_repository": "dockerRepository",
        }
        body = _mapped_body(cfg, mapping)
        if "storage_source" in cfg:
            body["source"] = {"storageSource": _camelize(cfg["storage_source"])}
        elif "repo_source" in cfg:
            body["source"] = {"repoSource": _camelize(cfg["repo_source"])}
        if "automatic_runtime_updates" in cfg:
            if cfg["automatic_runtime_updates"]:
                body["automaticUpdatePolicy"] = {}
                body["onDeployUpdatePolicy"] = None
            else:
                body["automaticUpdatePolicy"] = None
                body["onDeployUpdatePolicy"] = {}
        body.update(dict(cfg.get("build_raw_fields") or {}))
        if not partial and "runtime" not in body:
            body["runtime"] = str(cfg["runtime"])
        return body

    def _service_config(self, cfg: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        body = _mapped_body(
            cfg,
            {
                "available_memory": "availableMemory",
                "available_cpu": "availableCpu",
                "timeout_seconds": "timeoutSeconds",
                "min_instances": "minInstanceCount",
                "max_instances": "maxInstanceCount",
                "max_instance_concurrency": "maxInstanceRequestConcurrency",
                "environment": "environmentVariables",
                "secret_environment": "secretEnvironmentVariables",
                "secret_volumes": "secretVolumes",
                "service_account_email": "serviceAccountEmail",
                "ingress": "ingressSettings",
                "all_traffic_on_latest_revision": "allTrafficOnLatestRevision",
                "vpc_connector": "vpcConnector",
                "vpc_connector_egress": "vpcConnectorEgressSettings",
                "direct_vpc_network_interface": "directVpcNetworkInterface",
                "direct_vpc_egress": "directVpcEgress",
                "binary_authorization_policy": "binaryAuthorizationPolicy",
            },
        )
        for field in ("secretEnvironmentVariables", "secretVolumes", "directVpcNetworkInterface"):
            if field in body:
                body[field] = _camelize(body[field])
        if not partial and "allTrafficOnLatestRevision" not in body:
            body["allTrafficOnLatestRevision"] = True
        body.update(dict(cfg.get("service_raw_fields") or {}))
        return body

    def _function_id(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        explicit = str(cfg.get("function_id") or "")
        if explicit:
            return explicit
        parts = (
            self._config.function_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
        )
        value = re.sub(r"[^a-z0-9-]+", "-", "-".join(parts).lower()).strip("-")
        value = re.sub(r"-+", "-", value)
        if not value or not value[0].isalpha():
            value = f"fn-{value}"
        value = value[:63].rstrip("-")
        if len(value) < 4:
            value = f"{value}-fn"
        return value

    def _parent(self, region: str) -> str:
        return f"projects/{self._config.project_id}/locations/{region}"

    def _name(self, region: str, function_id: str) -> str:
        return f"{self._parent(region)}/functions/{function_id}"

    def _get(self, name: str) -> dict[str, Any] | None:
        try:
            return self._functions.get(name)
        except CloudFunctionsNotFound:
            return None

    def _patch(self, name: str, current: dict[str, Any], desired: dict[str, Any]) -> None:
        changed = {
            key: value for key, value in desired.items() if key not in _OUTPUT_ONLY and current.get(key) != value
        }
        if changed:
            self._wait(self._functions.patch(name, changed, _update_mask(current, changed)))

    def _wait(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation.get("name"):
            return operation
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        current = operation
        while not current.get("done"):
            if self._monotonic() >= deadline:
                raise CloudFunctionsError(f"operation {operation['name']} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._functions.get_operation(str(operation["name"]))
        if current.get("error"):
            error = current["error"]
            raise CloudFunctionsError(str(error.get("message") or error))
        return dict(current.get("response") or current)

    @staticmethod
    def _assert_owned(
        resource: dict[str, Any],
        service_id: str,
        label: str,
        *,
        record_proves: bool,
    ) -> None:
        # function_id is tenant-settable, so neither a function Astrolift never
        # provisioned nor another managed service's may be redeployed from
        # here. Adoption of an existing resource is a separate,
        # operator-authorized operation (#1365) that no tenant config flag may
        # grant (#2021). A function with no managed-service id, as a tenant
        # could leave one before #2098, is this service's only when the
        # platform's exclusive record of the handle says so (#2086).
        labels = dict(resource.get("labels") or {})
        if labels.get("astrolift-io-managed-by") != "platform":
            raise CloudFunctionsError(
                f"existing {label} is not Astrolift-owned; adoption is a separate, operator-authorized "
                "operation and cannot be granted by tenant config",
            )
        refusal = label_identity_refusal(labels, service_id, record_proves=record_proves, resource=f"existing {label}")
        if refusal:
            raise CloudFunctionsError(refusal)

    @staticmethod
    def _merged_labels(resource: dict[str, Any], desired: dict[str, str]) -> dict[str, str]:
        return {**dict(resource.get("labels") or {}), **desired}

    def _labels(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, str]:
        labels = {
            "astrolift-io-organization": _label_value(spec.organization_slug),
            "astrolift-io-app": _label_value(spec.app_slug),
            "astrolift-io-environment": _label_value(spec.environment_name),
            **_identity_labels(spec.managed_service_id),
        }
        if spec.binding_id:
            labels["astrolift-io-binding"] = _label_value(spec.binding_id)
        return _platform_last(labels, _normalized_labels(cfg.get("labels") or {}))


def _handle(region: str, function_id: str) -> str:
    return f"{KIND}/{region}/{function_id}"


def _parse_handle(handle: str) -> tuple[str, str]:
    parts = handle.split("/")
    if len(parts) != 3 or parts[0] != KIND or not parts[1] or not _FUNCTION_ID_RE.fullmatch(parts[2]):
        raise ValueError("invalid Cloud Run function handle; expected faas/<region>/<function-id>")
    return parts[1], parts[2]


def _label_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    return (normalized or "label")[:63]


def _label_value(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")[:63]


def _normalized_labels(labels: dict[str, Any]) -> dict[str, str]:
    return {_label_key(str(key)): _label_value(str(value)) for key, value in labels.items()}


def _identity_labels(managed_service_id: str) -> dict[str, str]:
    """The labels ownership decides on, from the spec only."""
    return {
        "astrolift-io-managed-by": "platform",
        "astrolift-io-managed-service-id": _label_value(managed_service_id),
        MANAGED_SERVICE_ID_LABEL: _label_value(managed_service_id),
    }


def _platform_last(base: dict[str, str], tenant: dict[str, str]) -> dict[str, str]:
    """``base`` with ``tenant`` applied to its tenant keys only: platform labels always win (#2098)."""
    merged = {key: value for key, value in base.items() if not is_platform_label_key(key)}
    merged.update({key: value for key, value in tenant.items() if not is_platform_label_key(key)})
    merged.update({key: value for key, value in base.items() if is_platform_label_key(key)})
    return merged


def _mapped_body(source: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    return {target: source[key] for key, target in mapping.items() if key in source}


def _update_mask(current: dict[str, Any], desired: dict[str, Any]) -> list[str]:
    """Use leaf masks for protobuf messages so partial updates preserve siblings."""
    paths: list[str] = []
    for key, value in desired.items():
        if key not in {"buildConfig", "serviceConfig", "eventTrigger"} or not isinstance(value, dict):
            paths.append(key)
            continue
        candidate = current.get(key)
        current_message: dict[str, Any] = candidate if isinstance(candidate, dict) else {}
        for child, child_value in value.items():
            if current_message.get(child) != child_value:
                paths.append(f"{key}.{child}")
    return paths


def _camelize(value: Any) -> Any:
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = _camel_key(str(key))
            if name in out:
                # Refused, not "last one wins": the checks and Google must see
                # one value (#1921). _validate says so first.
                raise CloudFunctionsError(f"{name} is spelled more than one way")
            out[name] = _camelize(item)
        return out
    return value


def _spelled_twice(value: Any) -> str:
    """The first field ``value`` spells two ways, such as ``project_id`` and
    ``projectId``, in its JSON spelling, or ``""``."""
    if isinstance(value, list):
        return next((found for found in map(_spelled_twice, value) if found), "")
    if isinstance(value, dict):
        seen: set[str] = set()
        for key, item in value.items():
            name = _camel_key(str(key))
            if name in seen:
                return name
            seen.add(name)
            found = _spelled_twice(item)
            if found:
                return found
    return ""


def _raw_names_error(names: Any, protected: set[str], label: str, *, verb: str) -> str:
    """Why the raw field names ``names`` may not reach the API, or ``""``.

    See ``gcp._raw_fields.raw_field_conflicts`` for why both spellings matter
    (#1921, #1947).
    """
    proto_names, forbidden = raw_field_conflicts(names, protected)
    if proto_names:
        return f"{label} must use the API's lowerCamelCase JSON field names, not {', '.join(proto_names)}"
    if forbidden:
        return f"{label} cannot {verb} structured or output fields: {', '.join(forbidden)}"
    return ""


def _camel_key(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
