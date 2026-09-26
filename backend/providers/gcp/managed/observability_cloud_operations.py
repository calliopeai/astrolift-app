"""Google Cloud Operations observability-bundle lifecycle driver.

The bundle owns declarative Cloud Logging and Cloud Monitoring resources while
Managed Service for Prometheus remains the time-series storage capability.  All
provider-native request fields remain available through declaration ``body``
objects; Astrolift reserves only identity and ownership fields.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic, sleep
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

KIND = "observability"
_LOGGING_ROOT = "https://logging.googleapis.com"
_MONITORING_ROOT = "https://monitoring.googleapis.com"
_BUNDLE_LABEL = "astrolift_io_observability_bundle"
_RESOURCE_LABEL = "astrolift_io_observability_resource"
_SENSITIVE_CHANNEL_LABEL_PARTS = ("token", "password", "secret", "key", "credential")
_CONFIG_KEYS = {
    "location",
    "deletion_protection",
    "prune",
    "access_mode",
    "log_bucket",
    "log_views",
    "log_links",
    "log_sinks",
    "log_metrics",
    "log_exclusions",
    "log_scopes",
    "saved_queries",
    "dashboards",
    "notification_channels",
    "alert_policies",
    "groups",
    "uptime_checks",
    "metric_descriptors",
    "services",
}
#: A declaration's ``adopt`` flag used to let tenant config take over an
#: existing resource under a tenant-chosen id. Rejected rather than ignored.
_ADOPT_REMOVED = (
    "adopt is not accepted: adoption of an existing resource is a separate, operator-authorized "
    "operation and cannot be granted by tenant config"
)


class CloudOperationsError(Exception):
    """Provider or declaration error surfaced through lifecycle results."""


class CloudOperationsNotFound(CloudOperationsError):
    """A Cloud Logging or Monitoring resource does not exist."""


@dataclass(frozen=True)
class CloudOperationsConfig:
    project_id: str
    location: str = "global"
    name_prefix: str = "astrolift-observability"
    retention_days_default: int = 30
    deletion_protection_default: bool = True
    secret_id_prefix: str = "astrolift"
    logging_api_endpoint: str = _LOGGING_ROOT
    monitoring_api_endpoint: str = _MONITORING_ROOT
    request_timeout_seconds: float = 30
    operation_timeout_seconds: float = 900
    operation_poll_interval_seconds: float = 2


@dataclass(frozen=True)
class _ResourceKind:
    key: str
    version: str
    collection: str
    response_key: str
    identity: str
    ownership: str
    id_param: str = ""
    name_body_field: str = ""
    update_method: str = "PATCH"
    update_mask: bool = True


_BUCKET = _ResourceKind("log_bucket", "v2", "buckets", "buckets", "deterministic", "description", "bucketId")
_VIEW = _ResourceKind("log_views", "v2", "views", "views", "deterministic", "description", "viewId")
_LINK = _ResourceKind(
    "log_links",
    "v2",
    "links",
    "links",
    "deterministic",
    "description",
    "linkId",
    update_method="",
)
_SINK = _ResourceKind("log_sinks", "v2", "sinks", "sinks", "deterministic", "description", name_body_field="name")
_LOG_METRIC = _ResourceKind(
    "log_metrics",
    "v2",
    "metrics",
    "metrics",
    "deterministic",
    "description",
    name_body_field="name",
    update_method="PUT",
    update_mask=False,
)
_EXCLUSION = _ResourceKind(
    "log_exclusions",
    "v2",
    "exclusions",
    "exclusions",
    "deterministic",
    "description",
    name_body_field="name",
)
_LOG_SCOPE = _ResourceKind("log_scopes", "v2", "logScopes", "logScopes", "deterministic", "description", "logScopeId")
_SAVED_QUERY = _ResourceKind(
    "saved_queries",
    "v2",
    "savedQueries",
    "savedQueries",
    "deterministic",
    "description",
    "savedQueryId",
)
_DASHBOARD = _ResourceKind("dashboards", "v1", "dashboards", "dashboards", "generated", "labels", update_mask=False)
_CHANNEL = _ResourceKind(
    "notification_channels",
    "v3",
    "notificationChannels",
    "notificationChannels",
    "generated",
    "userLabels",
)
_ALERT = _ResourceKind("alert_policies", "v3", "alertPolicies", "alertPolicies", "generated", "userLabels")
_UPTIME = _ResourceKind("uptime_checks", "v3", "uptimeCheckConfigs", "uptimeCheckConfigs", "generated", "userLabels")
_GROUP = _ResourceKind(
    "groups",
    "v3",
    "groups",
    "group",
    "generated",
    "displayName",
    update_method="PUT",
    update_mask=False,
)
_METRIC_DESCRIPTOR = _ResourceKind(
    "metric_descriptors",
    "v3",
    "metricDescriptors",
    "metricDescriptors",
    "deterministic",
    "description",
    name_body_field="type",
    update_method="",
)
_SERVICE = _ResourceKind("services", "v3", "services", "services", "deterministic", "userLabels", "serviceId")
_SLO = _ResourceKind(
    "service_level_objectives",
    "v3",
    "serviceLevelObjectives",
    "serviceLevelObjectives",
    "deterministic",
    "userLabels",
    "serviceLevelObjectiveId",
)


class CloudOperationsRestClient:
    """Small JSON REST adapter for Cloud Logging v2 and Monitoring v1/v3."""

    def __init__(
        self,
        *,
        api_endpoint: str,
        timeout_seconds: float = 30,
        operation_timeout_seconds: float = 900,
        operation_poll_interval_seconds: float = 2,
        session: Any | None = None,
    ) -> None:
        self._api_endpoint = api_endpoint.rstrip("/")
        self._timeout = timeout_seconds
        self._operation_timeout = operation_timeout_seconds
        self._operation_poll = operation_poll_interval_seconds
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",),
            )
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get(self, resource: _ResourceKind, name: str) -> dict[str, Any]:
        return self._request("GET", resource.version, name)

    def list_resources(self, resource: _ResourceKind, parent: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = ""
        while True:
            params: dict[str, str] = {"pageSize": "1000"}
            if token:
                params["pageToken"] = token
            response = self._request(
                "GET",
                resource.version,
                f"{parent}/{resource.collection}",
                params=params,
            )
            raw = response.get(resource.response_key) or []
            if not isinstance(raw, list):
                raise CloudOperationsError(
                    f"{resource.key} list returned a non-list {resource.response_key}",
                )
            items.extend(dict(item) for item in raw if isinstance(item, Mapping))
            token = str(response.get("nextPageToken") or "")
            if not token:
                return items

    def list_locations(self, project: str) -> list[str]:
        locations: list[str] = []
        token = ""
        while True:
            params: dict[str, str] = {"pageSize": "1000"}
            if token:
                params["pageToken"] = token
            response = self._request(
                "GET",
                "v2",
                f"{project}/locations",
                params=params,
            )
            for item in response.get("locations") or []:
                if isinstance(item, Mapping) and item.get("locationId"):
                    locations.append(str(item["locationId"]))
            token = str(response.get("nextPageToken") or "")
            if not token:
                return sorted(set(locations))

    def create(
        self,
        resource: _ResourceKind,
        parent: str,
        resource_id: str,
        body: dict[str, Any],
        *,
        request_params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        params = dict(request_params or {})
        if resource.id_param:
            params[resource.id_param] = resource_id
        response = self._request(
            "POST",
            resource.version,
            f"{parent}/{resource.collection}",
            params=params or None,
            json=body,
        )
        return self._wait_operation(response) if resource is _LINK else response

    def update(
        self,
        resource: _ResourceKind,
        name: str,
        body: dict[str, Any],
        fields: Iterable[str],
        *,
        request_params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if not resource.update_method:
            raise CloudOperationsError(f"{resource.key} cannot be updated in place")
        payload = dict(body)
        if resource.name_body_field != "type":
            payload["name"] = name
        params = dict(request_params or {})
        fields = sorted(set(fields))
        if resource.update_mask and fields:
            params["updateMask"] = ",".join(fields)
        return self._request(
            resource.update_method,
            resource.version,
            name,
            params=params or None,
            json=payload,
        )

    def delete(self, resource: _ResourceKind, name: str, *, force: bool = False) -> None:
        params = {"force": "true"} if force and resource is _CHANNEL else None
        response = self._request("DELETE", resource.version, name, params=params)
        if resource is _LINK:
            self._wait_operation(response)

    def verify_notification_channel(self, name: str, code: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "v3",
            f"{name}:verify",
            json={"code": code},
        )

    def _request(
        self,
        method: str,
        version: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            f"{self._api_endpoint}/{version}/{path.lstrip('/')}",
            params=params,
            json=json,
            timeout=self._timeout,
        )
        if response.status_code == 404:
            raise CloudOperationsNotFound(path)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise CloudOperationsError(
                f"Google Cloud Operations HTTP {response.status_code}: {detail}",
            )
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, Mapping) else {}

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation.get("name"):
            return operation
        deadline = monotonic() + self._operation_timeout
        current = operation
        while not current.get("done"):
            if monotonic() >= deadline:
                raise CloudOperationsError(
                    f"Cloud Logging operation {operation['name']} did not finish before timeout",
                )
            sleep(self._operation_poll)
            current = self._request("GET", "v2", str(operation["name"]))
        if current.get("error"):
            error = current["error"]
            detail = error.get("message") if isinstance(error, Mapping) else error
            raise CloudOperationsError(f"Cloud Logging operation failed: {detail}")
        response = current.get("response")
        return dict(response) if isinstance(response, Mapping) else {}


class CloudOperationsDriver(ManagedServiceDriver):
    """Own one coherent Cloud Logging and Cloud Monitoring bundle."""

    def __init__(
        self,
        *,
        config: CloudOperationsConfig,
        logging_client: Any | None = None,
        monitoring_client: Any | None = None,
        secret_reader: Callable[[str], Mapping[str, str] | None] | Any | None = None,
    ) -> None:
        self._config = config
        self._logging = logging_client or CloudOperationsRestClient(
            api_endpoint=config.logging_api_endpoint,
            timeout_seconds=config.request_timeout_seconds,
            operation_timeout_seconds=config.operation_timeout_seconds,
            operation_poll_interval_seconds=config.operation_poll_interval_seconds,
        )
        self._monitoring = monitoring_client or CloudOperationsRestClient(
            api_endpoint=config.monitoring_api_endpoint,
            timeout_seconds=config.request_timeout_seconds,
            operation_timeout_seconds=config.operation_timeout_seconds,
            operation_poll_interval_seconds=config.operation_poll_interval_seconds,
        )
        self._secret_reader = secret_reader

    @driver_op(
        cloud="gcp",
        driver="cloud_operations",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = _validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_cloud_operations_config"])
        bundle_id = _bundle_id(
            str(cfg.get("name") or spec.service_handle_hint or spec.app_slug),
            prefix=self._config.name_prefix,
        )
        location = str(cfg.get("location") or self._config.location)
        handle = _handle_for(bundle_id, location)
        try:
            counts = self._reconcile(
                bundle_id,
                cfg,
                base_labels=_labels_for_spec(spec),
            )
        except Exception as exc:
            return ProvisionResult(
                False,
                handle,
                f"provision Google Cloud Operations bundle: {exc}",
                [str(exc)],
            )
        return ProvisionResult(
            True,
            handle,
            _count_message(bundle_id, counts, action="reconciled"),
            ready=True,
        )

    @driver_op(cloud="gcp", driver="cloud_operations")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            location, bundle_id = _parse_handle(spec.handle)
        except CloudOperationsError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = spec.config or {}
        error = _validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_cloud_operations_config"])
        requested_location = str(cfg.get("location") or location)
        if requested_location != location:
            return UpdateResult(
                False,
                spec.handle,
                "Cloud Logging location is immutable; reprovision the bundle in the new location",
                ["immutable_location"],
            )
        cfg = {**cfg, "location": location}
        try:
            counts = self._reconcile(bundle_id, cfg, base_labels=None)
        except Exception as exc:
            return UpdateResult(
                False,
                spec.handle,
                f"update Google Cloud Operations bundle: {exc}",
                [str(exc)],
            )
        return UpdateResult(True, spec.handle, _count_message(bundle_id, counts, action="reconciled"))

    @driver_op(
        cloud="gcp",
        driver="cloud_operations",
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
            location, bundle_id = _parse_handle(spec.handle)
        except CloudOperationsError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        protected = bool(
            spec.config.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Cloud Operations bundle {bundle_id} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            inventory = self._inventory(bundle_id, location)
            locked = [item["name"] for item in inventory[_BUCKET.key] if item.get("locked")]
            if delete_data and locked:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "locked Cloud Logging buckets cannot be deleted: " + ", ".join(locked),
                    ["locked_log_bucket"],
                    retryable=False,
                )
            external_refs = self._external_channel_references(
                bundle_id,
                inventory[_CHANNEL.key],
            )
            if external_refs and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "notification channels are referenced by unmanaged alert policies: " + ", ".join(external_refs),
                    ["external_notification_channel_reference"],
                    retryable=False,
                )
            external_slos = self._external_slo_references(
                bundle_id,
                inventory[_SERVICE.key],
            )
            if external_slos:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "owned services contain unmanaged SLOs: " + ", ".join(external_slos),
                    ["external_service_level_objective"],
                    retryable=False,
                )
            external_groups = self._external_group_references(
                bundle_id,
                inventory[_GROUP.key],
            )
            if external_groups:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "owned Monitoring groups are referenced by unmanaged resources: " + ", ".join(external_groups),
                    ["external_monitoring_group_reference"],
                    retryable=False,
                )
            external_views = self._external_bucket_views(
                bundle_id,
                inventory[_BUCKET.key],
            )
            if delete_data and external_views:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "owned log buckets contain unmanaged views: " + ", ".join(external_views),
                    ["external_log_view"],
                    retryable=False,
                )
            external_bucket_refs = self._external_bucket_references(
                bundle_id,
                location,
                inventory[_BUCKET.key],
            )
            if delete_data and external_bucket_refs:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "owned log buckets are referenced by unmanaged resources: " + ", ".join(external_bucket_refs),
                    ["external_log_bucket_reference"],
                    retryable=False,
                )
            external_metric_refs = self._external_metric_references(
                bundle_id,
                inventory[_METRIC_DESCRIPTOR.key],
            )
            if delete_data and external_metric_refs:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "owned metric descriptors are referenced by unmanaged resources: "
                    + ", ".join(external_metric_refs),
                    ["external_metric_reference"],
                    retryable=False,
                )
            self._delete_inventory(
                inventory,
                delete_data=delete_data,
                force_destroy=force_destroy,
            )
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"delete Google Cloud Operations bundle: {exc}",
                [str(exc)],
            )
        retained: list[str] = []
        if not delete_data and inventory[_BUCKET.key]:
            retained.append("Cloud Logging buckets, views, and analytics links")
        if not delete_data and inventory[_METRIC_DESCRIPTOR.key]:
            retained.append("custom metric descriptors and time series")
        suffix = "; retained " + " plus ".join(retained) if retained else ""
        return DeprovisionResult(
            True,
            spec.handle,
            f"Cloud Operations bundle {bundle_id} configuration deleted{suffix}",
        )

    @driver_op(cloud="gcp", driver="cloud_operations")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            location, bundle_id = _parse_handle(handle.handle)
            inventory = self._inventory(bundle_id, location)
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Cloud Operations bundle: {exc}")
        counts = {key: len(value) for key, value in inventory.items()}
        total = sum(counts.values())
        if not total:
            return ServiceStatus(
                handle.handle,
                "deprovisioned",
                f"Cloud Operations bundle {bundle_id} has no resources",
            )
        return ServiceStatus(
            handle.handle,
            "available",
            _count_message(bundle_id, counts, action="available"),
        )

    @driver_op(cloud="gcp", driver="cloud_operations")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        location, bundle_id = _parse_handle(handle.handle)
        cfg = config or {}
        inventory = self._inventory(bundle_id, location)
        bucket = next(iter(inventory[_BUCKET.key]), {})
        dashboard = next(iter(inventory[_DASHBOARD.key]), {})
        bucket_name = str(bucket.get("name") or "")
        dashboard_name = str(dashboard.get("name") or "")
        dashboard_url = _console_url(self._config.project_id, dashboard_name)
        return Binding(
            env_vars={
                "OBSERVABILITY_PROVIDER": ValueRef(literal="cloud_operations"),
                "LOG_GROUP": ValueRef(literal=bucket_name),
                "METRICS_ENDPOINT": ValueRef(literal="https://monitoring.googleapis.com"),
                "DASHBOARD_URL": ValueRef(literal=dashboard_url),
                "GCP_PROJECT_ID": ValueRef(literal=self._config.project_id),
                "GCP_CLOUD_OPERATIONS_BUNDLE": ValueRef(literal=bundle_id),
                "GCP_LOG_BUCKET": ValueRef(literal=bucket_name),
                "GCP_LOGGING_LOCATION": ValueRef(literal=location),
                "GCP_LOGGING_ENDPOINT": ValueRef(literal="https://logging.googleapis.com"),
                "GCP_MONITORING_ENDPOINT": ValueRef(literal="https://monitoring.googleapis.com"),
                "GCP_MONITORING_DASHBOARD": ValueRef(literal=dashboard_name),
            },
            iam_grants=_binding_grants(self._config.project_id, cfg),
            notes=(
                "Cloud Logging and Cloud Monitoring use Application Default Credentials through "
                "Workload Identity; notification-channel secrets remain in Secret Manager."
            ),
        )

    @driver_op(cloud="gcp", driver="cloud_operations")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise CloudOperationsError(
            "Cloud Operations configuration is reconstructed from the manifest; log data has no snapshot API",
        )

    @driver_op(cloud="gcp", driver="cloud_operations")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "Cloud Operations bundles restore by reprovisioning their declarative configuration",
            ["not_implemented"],
        )

    @driver_op(cloud="gcp", driver="cloud_operations", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        declaration: dict[str, Any] = {
            "type": "object",
            "required": ["id", "body"],
            "properties": {
                "id": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,62}$"},
                "body": {
                    "type": "object",
                    "description": (
                        "Provider-native Google API request body; identity and ownership fields are reserved."
                    ),
                },
                "body_secret_refs": {
                    "type": "object",
                    "description": (
                        "Dotted native body paths mapped to Secret Manager references; values never persist in TOML."
                    ),
                    "additionalProperties": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "required": ["secret_ref"],
                                "properties": {
                                    "secret_ref": {"type": "string"},
                                    "field": {"type": "string", "default": "value"},
                                },
                            },
                        ],
                    },
                },
            },
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "location": {"type": "string"},
                "deletion_protection": {"type": "boolean", "default": True},
                "prune": {"type": "boolean", "default": True},
                "access_mode": {
                    "type": "string",
                    "enum": ["read", "write", "both", "logs", "metrics", "none"],
                    "default": "both",
                },
                "log_bucket": {
                    "oneOf": [
                        {"type": "boolean"},
                        {
                            "type": "object",
                            "properties": {
                                "enabled": {"type": "boolean", "default": True},
                                "id": {"type": "string"},
                                "retention_days": {"type": "integer", "minimum": 1},
                                "locked": {"type": "boolean"},
                                "analytics_enabled": {"type": "boolean"},
                                "body": {"type": "object"},
                            },
                        },
                    ],
                    "default": True,
                },
                **{
                    key: {"type": "array", "items": declaration}
                    for key in (
                        "log_views",
                        "log_links",
                        "log_metrics",
                        "log_exclusions",
                        "log_scopes",
                        "saved_queries",
                        "dashboards",
                        "metric_descriptors",
                    )
                },
                "notification_channels": {
                    "type": "array",
                    "items": {
                        **declaration,
                        "properties": {
                            **declaration["properties"],
                            "label_secret_refs": {
                                "type": "object",
                                "additionalProperties": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {
                                            "type": "object",
                                            "required": ["secret_ref"],
                                            "properties": {
                                                "secret_ref": {"type": "string"},
                                                "field": {"type": "string", "default": "value"},
                                            },
                                        },
                                    ],
                                },
                            },
                            "verification_code_secret_ref": {
                                "oneOf": [
                                    {"type": "string"},
                                    {
                                        "type": "object",
                                        "required": ["secret_ref"],
                                        "properties": {
                                            "secret_ref": {"type": "string"},
                                            "field": {"type": "string", "default": "value"},
                                        },
                                    },
                                ],
                                "description": (
                                    "Optional one-time verification code reference for channel types that require it."
                                ),
                            },
                        },
                    },
                },
                "log_sinks": {
                    "type": "array",
                    "items": {
                        **declaration,
                        "properties": {
                            **declaration["properties"],
                            "unique_writer_identity": {
                                "type": "boolean",
                                "default": True,
                                "description": "Give the sink a dedicated service account writer identity.",
                            },
                            "custom_writer_identity": {
                                "type": "string",
                                "description": "Explicit supported service account used as the sink writer.",
                            },
                        },
                    },
                },
                "alert_policies": {
                    "type": "array",
                    "items": {
                        **declaration,
                        "properties": {
                            **declaration["properties"],
                            "notification_channel_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": ("Bundle-local notification channel IDs resolved to provider names."),
                            },
                        },
                    },
                },
                "groups": {
                    "type": "array",
                    "items": {
                        **declaration,
                        "properties": {
                            **declaration["properties"],
                            "parent_group_id": {
                                "type": "string",
                                "description": "Bundle-local parent group ID.",
                            },
                        },
                    },
                },
                "uptime_checks": {
                    "type": "array",
                    "items": {
                        **declaration,
                        "properties": {
                            **declaration["properties"],
                            "group_id": {
                                "type": "string",
                                "description": "Bundle-local Monitoring group ID for resourceGroup.",
                            },
                        },
                    },
                },
                "services": {
                    "type": "array",
                    "items": {
                        **declaration,
                        "properties": {
                            **declaration["properties"],
                            "service_level_objectives": {
                                "type": "array",
                                "items": declaration,
                            },
                        },
                    },
                },
            },
        }

    @driver_op(cloud="gcp", driver="cloud_operations", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "OBSERVABILITY_PROVIDER": "Portable observability provider identifier",
                "LOG_GROUP": "Portable log destination; full Cloud Logging bucket resource name",
                "METRICS_ENDPOINT": "Portable Cloud Monitoring API endpoint",
                "DASHBOARD_URL": "Google Cloud console URL for the first owned dashboard",
                "GCP_PROJECT_ID": "Google Cloud project containing the bundle",
                "GCP_CLOUD_OPERATIONS_BUNDLE": "Astrolift bundle ID",
                "GCP_LOG_BUCKET": "Full Cloud Logging bucket resource name",
                "GCP_LOGGING_LOCATION": "Cloud Logging bucket location",
                "GCP_LOGGING_ENDPOINT": "Cloud Logging API endpoint",
                "GCP_MONITORING_ENDPOINT": "Cloud Monitoring API endpoint",
                "GCP_MONITORING_DASHBOARD": "First owned dashboard resource name",
            },
        )

    def _reconcile(
        self,
        bundle_id: str,
        cfg: dict[str, Any],
        *,
        base_labels: dict[str, str] | None,
    ) -> dict[str, int]:
        cfg = self._resolve_config_secrets(cfg)
        project = self._project
        location = str(cfg.get("location") or self._config.location)
        location_parent = f"{project}/locations/{location}"
        desired: dict[str, set[str]] = {}

        bucket_decl = _bucket_declaration(
            cfg.get("log_bucket", True),
            bundle_id,
            self._config.retention_days_default,
        )
        bucket_name = ""
        if bucket_decl is not None:
            bucket_name = f"{location_parent}/buckets/{bucket_decl['id']}"
            item = self._reconcile_one(
                self._logging,
                _BUCKET,
                location_parent,
                bucket_decl,
                bundle_id,
                base_labels=base_labels,
                explicit_name=bucket_name,
            )
            desired[_BUCKET.key] = {str(item["name"])}
        else:
            desired[_BUCKET.key] = set()

        views = _declarations(cfg, _VIEW.key)
        if views and not bucket_name:
            raise CloudOperationsError("log_views require log_bucket.enabled=true")
        self._reconcile_many(
            self._logging,
            _VIEW,
            bucket_name,
            views,
            bundle_id,
            desired,
            base_labels,
        )
        links = _declarations(cfg, _LINK.key)
        if links and not bucket_name:
            raise CloudOperationsError("log_links require log_bucket.enabled=true")
        self._reconcile_many(
            self._logging,
            _LINK,
            bucket_name,
            links,
            bundle_id,
            desired,
            base_labels,
        )
        for resource, parent in (
            (_SINK, project),
            (_LOG_METRIC, project),
            (_EXCLUSION, project),
            (_LOG_SCOPE, location_parent),
            (_SAVED_QUERY, location_parent),
        ):
            self._reconcile_many(
                self._logging,
                resource,
                parent,
                _declarations(cfg, resource.key),
                bundle_id,
                desired,
                base_labels,
            )

        channel_names = self._reconcile_many(
            self._monitoring,
            _CHANNEL,
            project,
            _declarations(cfg, _CHANNEL.key),
            bundle_id,
            desired,
            base_labels,
        )
        channel_by_id = {
            _resource_owner(item).get("resource", ""): str(item.get("name") or "") for item in channel_names
        }

        group_by_id: dict[str, str] = {}
        desired[_GROUP.key] = set()
        for declaration in _ordered_groups(_declarations(cfg, _GROUP.key)):
            body = dict(declaration["body"])
            parent_id = declaration.get("parent_group_id")
            if parent_id is not None:
                body["parentName"] = group_by_id[str(parent_id)]
            items = self._reconcile_many(
                self._monitoring,
                _GROUP,
                project,
                [{**declaration, "body": body}],
                bundle_id,
                desired,
                base_labels,
            )
            group_by_id[str(declaration["id"])] = str(items[0]["name"])

        services = _declarations(cfg, _SERVICE.key)
        service_items = self._reconcile_many(
            self._monitoring,
            _SERVICE,
            project,
            services,
            bundle_id,
            desired,
            base_labels,
        )
        desired[_SLO.key] = set()
        services_by_id = {_resource_owner(item).get("resource", ""): item for item in service_items}
        for declaration in services:
            service_id = str(declaration["id"])
            service = services_by_id.get(service_id)
            if service is None:
                raise CloudOperationsError(f"service {service_id!r} did not reconcile")
            self._reconcile_many(
                self._monitoring,
                _SLO,
                str(service["name"]),
                _nested_declarations(declaration, "service_level_objectives"),
                bundle_id,
                desired,
                base_labels,
            )

        for resource in (_METRIC_DESCRIPTOR, _DASHBOARD):
            self._reconcile_many(
                self._monitoring,
                resource,
                project,
                _declarations(cfg, resource.key),
                bundle_id,
                desired,
                base_labels,
            )

        def uptime_body(declaration: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
            group_id = declaration.get("group_id")
            if group_id is not None:
                provider_name = group_by_id[str(group_id)]
                body["resourceGroup"] = {
                    **dict(body.get("resourceGroup") or {}),
                    "groupId": provider_name.rsplit("/", 1)[-1],
                }
            return body

        self._reconcile_many(
            self._monitoring,
            _UPTIME,
            project,
            _declarations(cfg, _UPTIME.key),
            bundle_id,
            desired,
            base_labels,
            transform=uptime_body,
        )

        def alert_body(declaration: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
            ids = declaration.get("notification_channel_ids")
            if ids is None:
                return body
            missing = [str(item) for item in ids if str(item) not in channel_by_id]
            if missing:
                raise CloudOperationsError(
                    "alert policy references unknown notification channel IDs: " + ", ".join(missing),
                )
            body["notificationChannels"] = [channel_by_id[str(item)] for item in ids]
            return body

        self._reconcile_many(
            self._monitoring,
            _ALERT,
            project,
            _declarations(cfg, _ALERT.key),
            bundle_id,
            desired,
            base_labels,
            transform=alert_body,
        )

        if bool(cfg.get("prune", True)):
            self._prune(bundle_id, desired, location)
        inventory = self._inventory(bundle_id, location)
        return {key: len(value) for key, value in inventory.items()}

    @property
    def _project(self) -> str:
        return f"projects/{self._config.project_id}"

    def _reconcile_many(
        self,
        client: Any,
        resource: _ResourceKind,
        parent: str,
        declarations: list[dict[str, Any]],
        bundle_id: str,
        desired: dict[str, set[str]],
        base_labels: dict[str, str] | None,
        *,
        transform: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        desired.setdefault(resource.key, set())
        out: list[dict[str, Any]] = []
        for declaration in declarations:
            body = dict(declaration.get("body") or {})
            if transform is not None:
                body = transform(declaration, body)
            prepared = {**declaration, "body": body}
            explicit_name = _deterministic_name(resource, parent, bundle_id, str(declaration["id"]))
            item = self._reconcile_one(
                client,
                resource,
                parent,
                prepared,
                bundle_id,
                base_labels=base_labels,
                explicit_name=explicit_name,
            )
            if (
                resource is _CHANNEL
                and declaration.get("_verification_code")
                and str(item.get("verificationStatus") or "") != "VERIFIED"
            ):
                verified: Any = client.verify_notification_channel(
                    str(item["name"]),
                    str(declaration["_verification_code"]),
                )
                item = dict(verified)
            desired[resource.key].add(str(item["name"]))
            out.append(item)
        return out

    def _reconcile_one(
        self,
        client: Any,
        resource: _ResourceKind,
        parent: str,
        declaration: dict[str, Any],
        bundle_id: str,
        *,
        base_labels: dict[str, str] | None,
        explicit_name: str,
    ) -> dict[str, Any]:
        resource_id = str(declaration["id"])
        body = _stamp_body(
            resource,
            dict(declaration.get("body") or {}),
            bundle_id,
            resource_id,
            base_labels=base_labels,
        )
        if resource.name_body_field:
            body[resource.name_body_field] = _create_body_name(
                resource,
                bundle_id,
                resource_id,
            )

        existing: dict[str, Any] | None
        if resource.identity == "deterministic":
            try:
                existing = client.get(resource, explicit_name)
            except Exception as exc:
                if not _not_found(exc):
                    raise
                existing = None
        else:
            existing = next(
                (item for item in client.list_resources(resource, parent) if _is_owned(item, bundle_id, resource_id)),
                None,
            )

        if existing is None:
            created: Any = client.create(
                resource,
                parent,
                _provider_resource_id(resource, resource_id),
                body,
                request_params=_request_params(resource, declaration),
            )
            return dict(created)
        if not _is_owned(existing, bundle_id, resource_id):
            # Declaration ids are tenant-chosen, so a deterministic name can
            # land on an existing resource somebody else created in the same
            # project. Adoption of an existing resource is a separate,
            # operator-authorized operation (#1365) that no tenant config flag
            # may grant (#2021).
            raise CloudOperationsError(
                f"refusing to adopt existing {resource.key} {explicit_name!r}; adoption is a separate, "
                "operator-authorized operation and cannot be granted by tenant config",
            )
        if resource is _BUCKET and existing.get("locked"):
            changed = _changed_fields(existing, body)
            if changed:
                raise CloudOperationsError(
                    "locked Cloud Logging bucket cannot be reconciled; requested changes: " + ", ".join(changed),
                )
        fields = _changed_fields(existing, body)
        request_params = _request_params(resource, declaration)
        request_changed = _request_params_changed(resource, declaration, existing)
        if not fields and not request_changed:
            return existing
        if resource is _METRIC_DESCRIPTOR:
            raise CloudOperationsError(
                f"metric descriptor {resource_id!r} has immutable provider fields; use a new id",
            )
        if resource is _DASHBOARD and existing.get("etag"):
            body["etag"] = existing["etag"]
        updated: Any = client.update(
            resource,
            str(existing["name"]),
            body,
            fields,
            request_params=request_params,
        )
        return dict(updated)

    def _channel_body(
        self,
        declaration: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        labels = dict(body.get("labels") or {})
        refs = declaration.get("label_secret_refs") or {}
        for key, reference in refs.items():
            path, field = _secret_reference(reference)
            payload = self._read_secret(path)
            if payload is None or field not in payload:
                raise CloudOperationsError(
                    f"notification channel secret reference {path!r} has no field {field!r}",
                )
            labels[str(key)] = str(payload[field])
        body["labels"] = labels
        return body

    def _resolve_config_secrets(self, cfg: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(cfg)
        for key in _CONFIG_KEYS - {
            "location",
            "deletion_protection",
            "prune",
            "access_mode",
            "log_bucket",
        }:
            declarations: list[dict[str, Any]] = []
            for item in _declarations(cfg, key):
                declaration = self._resolve_declaration_secrets(item)
                if key == _CHANNEL.key:
                    declaration["body"] = self._channel_body(
                        declaration,
                        dict(declaration["body"]),
                    )
                if key == _SERVICE.key:
                    declaration["service_level_objectives"] = [
                        self._resolve_declaration_secrets(child)
                        for child in _nested_declarations(
                            declaration,
                            "service_level_objectives",
                        )
                    ]
                declarations.append(declaration)
            resolved[key] = declarations
        return resolved

    def _resolve_declaration_secrets(self, declaration: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(declaration)
        body = deepcopy(dict(resolved.get("body") or {}))
        for path, reference in (resolved.get("body_secret_refs") or {}).items():
            secret_path, field = _secret_reference(reference)
            payload = self._read_secret(secret_path)
            if payload is None or field not in payload:
                raise CloudOperationsError(
                    f"body secret reference {secret_path!r} has no field {field!r}",
                )
            _set_body_path(body, str(path), str(payload[field]))
        if resolved.get("verification_code_secret_ref") is not None:
            secret_path, field = _secret_reference(resolved["verification_code_secret_ref"])
            payload = self._read_secret(secret_path)
            if payload is None or field not in payload:
                raise CloudOperationsError(
                    f"verification code secret reference {secret_path!r} has no field {field!r}",
                )
            resolved["_verification_code"] = str(payload[field])
        resolved["body"] = body
        return resolved

    def _read_secret(self, path: str) -> Mapping[str, str] | None:
        reader = self._secret_reader
        if reader is None:
            from gcp.secrets import GCPSecretsBackend, GCPSecretsConfig

            reader = GCPSecretsBackend(
                config=GCPSecretsConfig(
                    project_id=self._config.project_id,
                    secret_id_prefix=self._config.secret_id_prefix,
                ),
            )
            self._secret_reader = reader
        result = reader(path) if callable(reader) else reader.get(path)
        return result if isinstance(result, Mapping) else None

    def _inventory(
        self,
        bundle_id: str,
        location: str,
    ) -> dict[str, list[dict[str, Any]]]:
        project = self._project
        out: dict[str, list[dict[str, Any]]] = {}
        buckets = [
            item
            for item in self._logging.list_resources(
                _BUCKET,
                f"{project}/locations/{location}",
            )
            if _is_owned(item, bundle_id)
        ]
        out[_BUCKET.key] = buckets
        out[_VIEW.key] = [
            item
            for bucket in buckets
            for item in self._logging.list_resources(_VIEW, str(bucket["name"]))
            if _is_owned(item, bundle_id)
        ]
        out[_LINK.key] = [
            item
            for bucket in buckets
            for item in self._logging.list_resources(_LINK, str(bucket["name"]))
            if _is_owned(item, bundle_id)
        ]
        for resource, parent in (
            (_SINK, project),
            (_LOG_METRIC, project),
            (_EXCLUSION, project),
        ):
            out[resource.key] = [
                item for item in self._logging.list_resources(resource, parent) if _is_owned(item, bundle_id)
            ]
        location_resources: dict[str, list[dict[str, Any]]] = {
            _LOG_SCOPE.key: [],
            _SAVED_QUERY.key: [],
        }
        parent = f"{project}/locations/{location}"
        for resource in (_LOG_SCOPE, _SAVED_QUERY):
            location_resources[resource.key].extend(
                item for item in self._logging.list_resources(resource, parent) if _is_owned(item, bundle_id)
            )
        out.update(location_resources)
        for resource in (
            _DASHBOARD,
            _CHANNEL,
            _ALERT,
            _UPTIME,
            _GROUP,
            _METRIC_DESCRIPTOR,
            _SERVICE,
        ):
            out[resource.key] = [
                item for item in self._monitoring.list_resources(resource, project) if _is_owned(item, bundle_id)
            ]
        out[_SLO.key] = [
            item
            for service in out[_SERVICE.key]
            for item in self._monitoring.list_resources(_SLO, str(service["name"]))
            if _is_owned(item, bundle_id)
        ]
        return out

    def _prune(
        self,
        bundle_id: str,
        desired: dict[str, set[str]],
        location: str,
    ) -> None:
        inventory = self._inventory(bundle_id, location)
        protected_omissions = [
            str(item.get("name") or "")
            for resource in (_BUCKET, _LINK, _METRIC_DESCRIPTOR)
            for item in inventory[resource.key]
            if str(item.get("name") or "") not in desired.get(resource.key, set())
        ]
        if protected_omissions:
            raise CloudOperationsError(
                "data-bearing Cloud Operations resources cannot be pruned by update; "
                "retain their declarations or deprovision with delete_data=true: " + ", ".join(protected_omissions),
            )
        channels_to_delete = [
            item
            for item in inventory[_CHANNEL.key]
            if str(item.get("name") or "") not in desired.get(_CHANNEL.key, set())
        ]
        external_refs = self._external_channel_references(bundle_id, channels_to_delete)
        if external_refs:
            raise CloudOperationsError(
                "notification channels are referenced by unmanaged alert policies: " + ", ".join(external_refs),
            )
        services_to_delete = [
            item
            for item in inventory[_SERVICE.key]
            if str(item.get("name") or "") not in desired.get(_SERVICE.key, set())
        ]
        external_slos = self._external_slo_references(bundle_id, services_to_delete)
        if external_slos:
            raise CloudOperationsError(
                "services contain unmanaged SLOs: " + ", ".join(external_slos),
            )
        groups_to_delete = [
            item for item in inventory[_GROUP.key] if str(item.get("name") or "") not in desired.get(_GROUP.key, set())
        ]
        external_groups = self._external_group_references(bundle_id, groups_to_delete)
        if external_groups:
            raise CloudOperationsError(
                "Monitoring groups are referenced by unmanaged resources: " + ", ".join(external_groups),
            )
        for resource, client in _delete_order():
            if resource in {_BUCKET, _LINK, _METRIC_DESCRIPTOR}:
                continue
            for item in _deletion_items(resource, inventory.get(resource.key, [])):
                name = str(item.get("name") or "")
                if name not in desired.get(resource.key, set()):
                    (self._logging if client == "logging" else self._monitoring).delete(
                        resource,
                        name,
                    )

    def _delete_inventory(
        self,
        inventory: dict[str, list[dict[str, Any]]],
        *,
        delete_data: bool,
        force_destroy: bool,
    ) -> None:
        for resource, client_name in _delete_order():
            if resource in {_VIEW, _LINK, _BUCKET, _METRIC_DESCRIPTOR} and not delete_data:
                continue
            client = self._logging if client_name == "logging" else self._monitoring
            for item in _deletion_items(resource, inventory.get(resource.key, [])):
                client.delete(resource, str(item["name"]), force=force_destroy)

    def _external_channel_references(
        self,
        bundle_id: str,
        channels: list[dict[str, Any]],
    ) -> list[str]:
        names = {str(item.get("name") or "") for item in channels}
        if not names:
            return []
        refs: list[str] = []
        for alert in self._monitoring.list_resources(_ALERT, self._project):
            if _is_owned(alert, bundle_id):
                continue
            for name in alert.get("notificationChannels") or []:
                if str(name) in names:
                    refs.append(f"{alert.get('name', '?')} -> {name}")
        return sorted(set(refs))

    def _external_slo_references(
        self,
        bundle_id: str,
        services: list[dict[str, Any]],
    ) -> list[str]:
        refs: list[str] = []
        for service in services:
            service_name = str(service.get("name") or "")
            for slo in self._monitoring.list_resources(_SLO, service_name):
                if not _is_owned(slo, bundle_id):
                    refs.append(str(slo.get("name") or "?"))
        return sorted(set(refs))

    def _external_bucket_views(
        self,
        bundle_id: str,
        buckets: list[dict[str, Any]],
    ) -> list[str]:
        refs: list[str] = []
        for bucket in buckets:
            for view in self._logging.list_resources(
                _VIEW,
                str(bucket.get("name") or ""),
            ):
                if not _is_owned(view, bundle_id):
                    refs.append(str(view.get("name") or "?"))
        return sorted(set(refs))

    def _external_group_references(
        self,
        bundle_id: str,
        groups: list[dict[str, Any]],
    ) -> list[str]:
        names = {str(item.get("name") or "") for item in groups}
        ids = {name.rsplit("/", 1)[-1] for name in names}
        if not names:
            return []
        refs: list[str] = []
        for group in self._monitoring.list_resources(_GROUP, self._project):
            if _is_owned(group, bundle_id):
                continue
            if str(group.get("parentName") or "") in names:
                refs.append(str(group.get("name") or "?"))
        for uptime in self._monitoring.list_resources(_UPTIME, self._project):
            if _is_owned(uptime, bundle_id):
                continue
            resource_group = uptime.get("resourceGroup") or {}
            if isinstance(resource_group, Mapping) and str(resource_group.get("groupId") or "") in ids:
                refs.append(str(uptime.get("name") or "?"))
        return sorted(set(refs))

    def _external_bucket_references(
        self,
        bundle_id: str,
        location: str,
        buckets: list[dict[str, Any]],
    ) -> list[str]:
        names = [str(bucket.get("name") or "") for bucket in buckets]
        if not names:
            return []
        resources: list[dict[str, Any]] = []
        for resource, client, parent in (
            (_SINK, self._logging, self._project),
            (_LOG_SCOPE, self._logging, f"{self._project}/locations/{location}"),
            (_SAVED_QUERY, self._logging, f"{self._project}/locations/{location}"),
            (_DASHBOARD, self._monitoring, self._project),
            (_ALERT, self._monitoring, self._project),
        ):
            resources.extend(item for item in client.list_resources(resource, parent) if not _is_owned(item, bundle_id))
        return sorted(
            str(item.get("name") or "?") for item in resources if any(_contains_text(item, name) for name in names)
        )

    def _external_metric_references(
        self,
        bundle_id: str,
        descriptors: list[dict[str, Any]],
    ) -> list[str]:
        metric_types = [str(item.get("type") or "") for item in descriptors]
        if not metric_types:
            return []
        resources: list[dict[str, Any]] = []
        for resource in (_ALERT, _DASHBOARD, _SERVICE):
            resources.extend(
                item
                for item in self._monitoring.list_resources(resource, self._project)
                if not _is_owned(item, bundle_id)
            )
        for service in list(resources):
            name = str(service.get("name") or "")
            if "/services/" not in name:
                continue
            resources.extend(
                slo for slo in self._monitoring.list_resources(_SLO, name) if not _is_owned(slo, bundle_id)
            )
        return sorted(
            str(item.get("name") or "?")
            for item in resources
            if any(_contains_text(item, metric_type) for metric_type in metric_types)
        )


def _bucket_declaration(
    value: Any,
    bundle_id: str,
    retention_days_default: int,
) -> dict[str, Any] | None:
    if value is False:
        return None
    raw = {} if value is True or value is None else dict(value)
    if not bool(raw.get("enabled", True)):
        return None
    body = dict(raw.get("body") or {})
    body.setdefault("retentionDays", int(raw.get("retention_days", retention_days_default)))
    if "locked" in raw:
        body["locked"] = bool(raw["locked"])
    if "analytics_enabled" in raw:
        body["analyticsEnabled"] = bool(raw["analytics_enabled"])
    return {
        "id": str(raw.get("id") or _resource_id(bundle_id, "logs")),
        "body": body,
    }


def _declarations(cfg: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    raw = cfg.get(key) or []
    return [dict(item) for item in raw]


def _nested_declarations(declaration: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    return [dict(item) for item in declaration.get(key) or []]


def _ordered_groups(declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item["id"]): item for item in declarations}
    ordered: list[dict[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(group_id: str) -> None:
        if group_id in visited:
            return
        if group_id in visiting:
            raise CloudOperationsError(f"monitoring group parent cycle includes {group_id!r}")
        visiting.add(group_id)
        parent_id = by_id[group_id].get("parent_group_id")
        if parent_id is not None:
            visit(str(parent_id))
        visiting.remove(group_id)
        visited.add(group_id)
        ordered.append(by_id[group_id])

    for group_id in by_id:
        visit(group_id)
    return ordered


def _stamp_body(
    resource: _ResourceKind,
    body: dict[str, Any],
    bundle_id: str,
    resource_id: str,
    *,
    base_labels: dict[str, str] | None,
) -> dict[str, Any]:
    if resource.ownership == "displayName":
        marker = _display_marker(bundle_id, resource_id)
        display_name = str(body.get("displayName") or resource_id).strip()
        maximum_prefix = max(0, 255 - len(marker) - 1)
        body["displayName"] = f"{display_name[:maximum_prefix]} {marker}".strip()
        return body
    if resource.ownership == "description":
        description = str(body.get("description") or "").strip()
        marker = _marker(bundle_id, resource_id)
        body["description"] = f"{description}\n{marker}".strip()
        return body
    labels = dict(body.get(resource.ownership) or {})
    if base_labels:
        for key, value in base_labels.items():
            labels.setdefault(key, value)
    labels[_BUNDLE_LABEL] = _label_value(bundle_id)
    labels[_RESOURCE_LABEL] = _label_value(resource_id)
    body[resource.ownership] = labels
    return body


def _resource_owner(item: Mapping[str, Any]) -> dict[str, str]:
    for label_field in ("labels", "userLabels"):
        labels = item.get(label_field)
        if isinstance(labels, Mapping) and labels.get(_BUNDLE_LABEL):
            return {
                "bundle": str(labels.get(_BUNDLE_LABEL) or ""),
                "resource": str(labels.get(_RESOURCE_LABEL) or ""),
            }
    description = str(item.get("description") or "")
    prefix = "[astrolift-observability bundle="
    for line in description.splitlines():
        if not line.startswith(prefix) or not line.endswith("]"):
            continue
        values = line[len(prefix) : -1].split(" resource=", 1)
        if len(values) == 2:
            return {"bundle": values[0], "resource": values[1]}
    display_name = str(item.get("displayName") or "")
    display_prefix = "[astrolift:"
    if display_name.endswith("]") and display_prefix in display_name:
        marker = display_name.rsplit(display_prefix, 1)[-1][:-1]
        values = marker.split(":", 1)
        if len(values) == 2:
            return {"bundle": values[0], "resource": values[1]}
    return {}


def _is_owned(item: Mapping[str, Any], bundle_id: str, resource_id: str | None = None) -> bool:
    owner = _resource_owner(item)
    if owner.get("bundle") != _label_value(bundle_id):
        return False
    return resource_id is None or owner.get("resource") == _label_value(resource_id)


def _marker(bundle_id: str, resource_id: str) -> str:
    return f"[astrolift-observability bundle={_label_value(bundle_id)} resource={_label_value(resource_id)}]"


def _display_marker(bundle_id: str, resource_id: str) -> str:
    return f"[astrolift:{_label_value(bundle_id)}:{_label_value(resource_id)}]"


def _changed_fields(existing: Mapping[str, Any], desired: Mapping[str, Any]) -> list[str]:
    return sorted(
        key for key, value in desired.items() if key != "name" and not _desired_matches(existing.get(key), value)
    )


def _desired_matches(actual: Any, desired: Any) -> bool:
    if isinstance(desired, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _desired_matches(actual[key], value) for key, value in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(desired)
            and all(
                _desired_matches(actual_item, desired_item)
                for actual_item, desired_item in zip(actual, desired, strict=True)
            )
        )
    return bool(actual == desired)


def _request_params(
    resource: _ResourceKind,
    declaration: Mapping[str, Any],
) -> dict[str, str]:
    if resource is not _SINK:
        return {}
    custom_writer = declaration.get("custom_writer_identity")
    if custom_writer is not None:
        return {"customWriterIdentity": str(custom_writer)}
    return {
        "uniqueWriterIdentity": str(
            bool(declaration.get("unique_writer_identity", True)),
        ).lower(),
    }


def _request_params_changed(
    resource: _ResourceKind,
    declaration: Mapping[str, Any],
    existing: Mapping[str, Any],
) -> bool:
    if resource is not _SINK:
        return False
    custom_writer = declaration.get("custom_writer_identity")
    if custom_writer is not None:
        return str(existing.get("writerIdentity") or "") != str(custom_writer)
    return bool(declaration.get("unique_writer_identity", True)) and not bool(
        existing.get("writerIdentity"),
    )


def _deterministic_name(
    resource: _ResourceKind,
    parent: str,
    bundle_id: str,
    resource_id: str,
) -> str:
    if resource.identity != "deterministic":
        return ""
    if resource is _METRIC_DESCRIPTOR:
        metric_type = _create_body_name(resource, bundle_id, resource_id)
        return f"{parent}/metricDescriptors/{metric_type}"
    return f"{parent}/{resource.collection}/{_provider_resource_id(resource, resource_id)}"


def _provider_resource_id(resource: _ResourceKind, resource_id: str) -> str:
    return resource_id.replace("-", "_") if resource is _LINK else resource_id


def _create_body_name(resource: _ResourceKind, bundle_id: str, resource_id: str) -> str:
    if resource is _METRIC_DESCRIPTOR:
        return f"custom.googleapis.com/astrolift/{_label_value(bundle_id)}/{resource_id}"
    return resource_id


def _resource_id(*parts: str) -> str:
    raw = "-".join(parts).lower()
    clean = "".join(char if char.isalnum() else "-" for char in raw)
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-") or "resource"
    if not clean[0].isalpha():
        clean = "r-" + clean
    return clean[:63].rstrip("-")


def _bundle_id(value: str, *, prefix: str) -> str:
    return _resource_id(prefix, value)


def _handle_for(bundle_id: str, location: str) -> str:
    return f"{KIND}/{location}/{bundle_id}"


def _parse_handle(handle: str) -> tuple[str, str]:
    parts = handle.split("/")
    if len(parts) != 3 or parts[0] != KIND or not _valid_location(parts[1]) or not _valid_id(parts[2]):
        raise CloudOperationsError(
            f"handle {handle!r} must be 'observability/<location>/<bundle-id>'",
        )
    return parts[1], parts[2]


def _label_value(value: str) -> str:
    return _resource_id(value)[:63]


def _labels_for_spec(spec: ProvisionSpec) -> dict[str, str]:
    labels = {
        "astrolift_io_managed_by": "platform",
        "astrolift_io_organization": spec.organization_slug,
        "astrolift_io_app": spec.app_slug,
        "astrolift_io_environment": spec.environment_name,
        "astrolift_io_cluster": spec.tenant_cluster_id,
    }
    if spec.binding_id:
        labels["astrolift_io_binding"] = spec.binding_id
    if spec.managed_service_id:
        labels["astrolift_io_managed_service_id"] = spec.managed_service_id
    labels.update({str(key): str(value) for key, value in spec.tags.items()})
    return {_resource_id(key).replace("-", "_"): _label_value(value) for key, value in labels.items()}


def _secret_reference(reference: Any) -> tuple[str, str]:
    if isinstance(reference, str):
        path, separator, field = reference.partition("#")
        return path, field if separator else "value"
    if isinstance(reference, Mapping):
        return str(reference.get("secret_ref") or ""), str(reference.get("field") or "value")
    raise CloudOperationsError("notification channel secret reference must be a string or object")


def _validate_config(cfg: Mapping[str, Any]) -> str:
    unknown = sorted(set(cfg) - _CONFIG_KEYS)
    if unknown:
        return "unsupported Cloud Operations config keys: " + ", ".join(unknown)
    if "location" in cfg and not _valid_location(str(cfg["location"])):
        return "config.location must contain lowercase letters, digits, and hyphens"
    if str(cfg.get("access_mode") or "both") not in {
        "read",
        "write",
        "both",
        "logs",
        "metrics",
        "none",
    }:
        return f"unsupported Cloud Operations access_mode {cfg.get('access_mode')!r}"
    bucket = cfg.get("log_bucket", True)
    if not isinstance(bucket, (bool, Mapping)):
        return "config.log_bucket must be a boolean or object"
    if isinstance(bucket, Mapping):
        if "adopt" in bucket:
            return f"config.log_bucket: {_ADOPT_REMOVED}"
        body = bucket.get("body") or {}
        if not isinstance(body, Mapping):
            return "config.log_bucket.body must be an object"
        reserved = {"name"}.intersection(body)
        if reserved:
            return "config.log_bucket.body cannot override provider identity field name"
        bucket_id = str(bucket.get("id") or "")
        if bucket_id and not _valid_id(bucket_id):
            return "config.log_bucket.id must match ^[a-z][a-z0-9-]{0,62}$"
        if "[astrolift-observability bundle=" in str(body.get("description") or ""):
            return "config.log_bucket.body.description cannot contain Astrolift ownership markers"
    seen: dict[str, set[str]] = {}
    for key in _CONFIG_KEYS - {
        "location",
        "deletion_protection",
        "prune",
        "access_mode",
        "log_bucket",
    }:
        raw = cfg.get(key, [])
        if not isinstance(raw, list):
            return f"config.{key} must be an array"
        seen[key] = set()
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                return f"config.{key}[{index}] must be an object"
            if any(str(field).startswith("_") for field in item):
                return f"config.{key}[{index}] contains a reserved internal field"
            if "adopt" in item:
                return f"config.{key}[{index}]: {_ADOPT_REMOVED}"
            resource_id = str(item.get("id") or "")
            if not _valid_id(resource_id):
                return f"config.{key}[{index}].id must match ^[a-z][a-z0-9-]{{0,62}}$"
            if resource_id in seen[key]:
                return f"config.{key} contains duplicate id {resource_id!r}"
            seen[key].add(resource_id)
            body = item.get("body")
            if not isinstance(body, Mapping):
                return f"config.{key}[{index}].body must be an object"
            body_refs = item.get("body_secret_refs") or {}
            if not isinstance(body_refs, Mapping):
                return f"config.{key}[{index}].body_secret_refs must be an object"
            for path, reference in body_refs.items():
                path = str(path)
                if not _valid_body_path(path):
                    return f"config.{key}[{index}] has invalid body secret path {path!r}"
                if _reserved_secret_path(path, key):
                    return f"config.{key}[{index}] body secret path {path!r} is Astrolift-owned"
                if _body_path_exists(body, path):
                    return f"config.{key}[{index}] must omit body.{path} when body_secret_refs supplies it"
                try:
                    path_ref, field = _secret_reference(reference)
                except CloudOperationsError as exc:
                    return str(exc)
                if not path_ref or not field:
                    return f"config.{key}[{index}] body secret references require a path and field"
            if "name" in body or (key == _METRIC_DESCRIPTOR.key and "type" in body):
                return f"config.{key}[{index}].body cannot override provider identity fields"
            if key == _SINK.key:
                if "writerIdentity" in body:
                    return (
                        f"config.{key}[{index}].body.writerIdentity is provider-owned; use "
                        "unique_writer_identity or custom_writer_identity"
                    )
                unique_writer = item.get("unique_writer_identity", True)
                custom_writer = item.get("custom_writer_identity")
                if not isinstance(unique_writer, bool):
                    return f"config.{key}[{index}].unique_writer_identity must be a boolean"
                if custom_writer is not None and not str(custom_writer).strip():
                    return f"config.{key}[{index}].custom_writer_identity cannot be empty"
                if custom_writer is not None and unique_writer:
                    return f"config.{key}[{index}] custom_writer_identity requires unique_writer_identity=false"
            if "[astrolift-observability bundle=" in str(body.get("description") or ""):
                return f"config.{key}[{index}].body.description cannot contain ownership markers"
            if key == _GROUP.key and "[astrolift:" in str(body.get("displayName") or ""):
                return f"config.{key}[{index}].body.displayName cannot contain ownership markers"
            ownership = "labels" if key == _DASHBOARD.key else "userLabels"
            if key in {
                _DASHBOARD.key,
                _CHANNEL.key,
                _ALERT.key,
                _UPTIME.key,
                _SERVICE.key,
            }:
                labels = body.get(ownership) or {}
                if not isinstance(labels, Mapping):
                    return f"config.{key}[{index}].body.{ownership} must be an object"
                if {_BUNDLE_LABEL, _RESOURCE_LABEL}.intersection(labels):
                    return f"config.{key}[{index}] cannot override Astrolift ownership labels"
            if key == _CHANNEL.key:
                labels = body.get("labels") or {}
                refs = item.get("label_secret_refs") or {}
                if not isinstance(labels, Mapping) or not isinstance(refs, Mapping):
                    return f"config.{key}[{index}] labels and label_secret_refs must be objects"
                direct_sensitive = sorted(str(label) for label in labels if _sensitive_label(str(label)))
                if direct_sensitive:
                    return f"config.{key}[{index}] sensitive labels must use label_secret_refs: " + ", ".join(
                        direct_sensitive
                    )
                for reference in refs.values():
                    try:
                        path, field = _secret_reference(reference)
                    except CloudOperationsError as exc:
                        return str(exc)
                    if not path or not field:
                        return f"config.{key}[{index}] secret references require a path and field"
                if item.get("verification_code_secret_ref") is not None:
                    try:
                        path, field = _secret_reference(item["verification_code_secret_ref"])
                    except CloudOperationsError as exc:
                        return str(exc)
                    if not path or not field:
                        return f"config.{key}[{index}] verification code reference requires a path and field"
            if key == _ALERT.key and "notification_channel_ids" in item:
                ids = item["notification_channel_ids"]
                if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
                    return f"config.{key}[{index}].notification_channel_ids must be a string array"
            if key == _UPTIME.key:
                http_check = body.get("httpCheck") or {}
                if not isinstance(http_check, Mapping):
                    return f"config.{key}[{index}].body.httpCheck must be an object"
                auth_info = http_check.get("authInfo") or {}
                if not isinstance(auth_info, Mapping):
                    return f"config.{key}[{index}].body.httpCheck.authInfo must be an object"
                if auth_info.get("password") is not None:
                    return f"config.{key}[{index}] HTTP basic-auth passwords must use body_secret_refs"
                headers = http_check.get("headers") or {}
                if not isinstance(headers, Mapping):
                    return f"config.{key}[{index}].body.httpCheck.headers must be an object"
                sensitive_headers = sorted(str(header) for header in headers if _sensitive_header(str(header)))
                if sensitive_headers:
                    return f"config.{key}[{index}] sensitive HTTP headers must use body_secret_refs: " + ", ".join(
                        sensitive_headers
                    )
            if key == _SERVICE.key:
                nested = item.get("service_level_objectives") or []
                if not isinstance(nested, list):
                    return f"config.{key}[{index}].service_level_objectives must be an array"
                nested_ids: set[str] = set()
                for child_index, child in enumerate(nested):
                    if not isinstance(child, Mapping) or not _valid_id(str(child.get("id") or "")):
                        return f"config.{key}[{index}].service_level_objectives[{child_index}] requires a valid id"
                    if str(child["id"]) in nested_ids:
                        return f"service {resource_id!r} contains duplicate SLO id {child['id']!r}"
                    if "adopt" in child:
                        return f"service {resource_id!r} SLO {child['id']!r}: {_ADOPT_REMOVED}"
                    nested_ids.add(str(child["id"]))
                    if not isinstance(child.get("body"), Mapping):
                        return f"service {resource_id!r} SLO {child['id']!r} body must be an object"
                    if "name" in child["body"]:
                        return f"service {resource_id!r} SLO {child['id']!r} cannot override name"
                    slo_labels = child["body"].get("userLabels") or {}
                    if not isinstance(slo_labels, Mapping) or {
                        _BUNDLE_LABEL,
                        _RESOURCE_LABEL,
                    }.intersection(slo_labels):
                        return f"service {resource_id!r} SLO {child['id']!r} has invalid ownership labels"
    bucket_enabled = bucket is not False and not (isinstance(bucket, Mapping) and not bool(bucket.get("enabled", True)))
    if cfg.get("log_views") and not bucket_enabled:
        return "config.log_views require log_bucket"
    if cfg.get("log_links"):
        if not bucket_enabled:
            return "config.log_links require log_bucket"
        bucket_body = bucket.get("body") or {} if isinstance(bucket, Mapping) else {}
        analytics_enabled = bool(
            (bucket.get("analytics_enabled") if isinstance(bucket, Mapping) else False)
            or (bucket_body.get("analyticsEnabled") if isinstance(bucket_body, Mapping) else False),
        )
        if not analytics_enabled:
            return "config.log_links require log_bucket.analytics_enabled=true"
    channel_ids = seen.get(_CHANNEL.key, set())
    for index, alert in enumerate(cfg.get(_ALERT.key) or []):
        missing = sorted(
            str(value) for value in alert.get("notification_channel_ids") or [] if str(value) not in channel_ids
        )
        if missing:
            return f"config.alert_policies[{index}] references unknown notification channel IDs: " + ", ".join(missing)
    group_ids = seen.get(_GROUP.key, set())
    for index, group in enumerate(cfg.get(_GROUP.key) or []):
        parent_id = group.get("parent_group_id")
        if parent_id is not None and str(parent_id) not in group_ids:
            return f"config.groups[{index}] references unknown parent_group_id {parent_id!r}"
    for index, uptime in enumerate(cfg.get(_UPTIME.key) or []):
        group_id = uptime.get("group_id")
        if group_id is not None and str(group_id) not in group_ids:
            return f"config.uptime_checks[{index}] references unknown group_id {group_id!r}"
    try:
        _ordered_groups([dict(item) for item in cfg.get(_GROUP.key) or []])
    except CloudOperationsError as exc:
        return str(exc)
    return ""


def _valid_id(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 63
        and value[0].isalpha()
        and all(char.islower() or char.isdigit() or char == "-" for char in value)
    )


def _valid_location(value: str) -> bool:
    return bool(value) and len(value) <= 63 and all(char.islower() or char.isdigit() or char == "-" for char in value)


def _sensitive_label(value: str) -> bool:
    lowered = value.lower()
    return any(part in lowered for part in _SENSITIVE_CHANNEL_LABEL_PARTS)


def _sensitive_header(value: str) -> bool:
    normalized = value.lower().replace("_", "-")
    return normalized in {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
    } or any(part in normalized for part in ("token", "secret", "password"))


def _valid_body_path(path: str) -> bool:
    parts = path.split(".")
    return bool(parts) and all(
        part and part[0].isalpha() and all(char.isalnum() or char in "_-" for char in part) for part in parts
    )


def _reserved_secret_path(path: str, key: str) -> bool:
    if path == "name" or path.startswith("name."):
        return True
    if key == _METRIC_DESCRIPTOR.key and (path == "type" or path.startswith("type.")):
        return True
    return any(
        path == f"{field}.{label}" or path.startswith(f"{field}.{label}.")
        for field in ("labels", "userLabels")
        for label in (_BUNDLE_LABEL, _RESOURCE_LABEL)
    )


def _body_path_exists(body: Mapping[str, Any], path: str) -> bool:
    current: Any = body
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return True


def _contains_text(value: Any, needle: str) -> bool:
    if not needle:
        return False
    if isinstance(value, str):
        return needle in value
    if isinstance(value, Mapping):
        return any(_contains_text(item, needle) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_text(item, needle) for item in value)
    return False


def _set_body_path(body: dict[str, Any], path: str, value: str) -> None:
    current = body
    parts = path.split(".")
    for part in parts[:-1]:
        nested = current.setdefault(part, {})
        if not isinstance(nested, dict):
            raise CloudOperationsError(
                f"body secret path {path!r} crosses non-object field {part!r}",
            )
        current = nested
    current[parts[-1]] = value


def _binding_grants(project_id: str, cfg: Mapping[str, Any]) -> list[Grant]:
    mode = str(cfg.get("access_mode") or "both")
    roles: list[str] = []
    if mode in {"read", "both", "logs"}:
        roles.append("roles/logging.viewer")
    if mode in {"write", "both", "logs"}:
        roles.append("roles/logging.logWriter")
    if mode in {"read", "both", "metrics"}:
        roles.append("roles/monitoring.viewer")
    if mode in {"write", "both", "metrics"}:
        roles.append("roles/monitoring.metricWriter")
    return [Grant(resource=f"projects/{project_id}", actions=roles)] if roles else []


def _console_url(project_id: str, dashboard_name: str) -> str:
    if not dashboard_name:
        return f"https://console.cloud.google.com/monitoring?project={project_id}"
    dashboard_id = dashboard_name.rsplit("/", 1)[-1]
    return f"https://console.cloud.google.com/monitoring/dashboards/builder/{dashboard_id}?project={project_id}"


def _delete_order() -> tuple[tuple[_ResourceKind, str], ...]:
    return (
        (_ALERT, "monitoring"),
        (_UPTIME, "monitoring"),
        (_GROUP, "monitoring"),
        (_DASHBOARD, "monitoring"),
        (_SLO, "monitoring"),
        (_SERVICE, "monitoring"),
        (_CHANNEL, "monitoring"),
        (_METRIC_DESCRIPTOR, "monitoring"),
        (_SAVED_QUERY, "logging"),
        (_LOG_SCOPE, "logging"),
        (_EXCLUSION, "logging"),
        (_LOG_METRIC, "logging"),
        (_SINK, "logging"),
        (_LINK, "logging"),
        (_VIEW, "logging"),
        (_BUCKET, "logging"),
    )


def _deletion_items(
    resource: _ResourceKind,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if resource is not _GROUP:
        return items
    by_name = {str(item.get("name") or ""): item for item in items}

    def depth(item: Mapping[str, Any]) -> int:
        count = 0
        parent = str(item.get("parentName") or "")
        seen: set[str] = set()
        while parent in by_name and parent not in seen:
            seen.add(parent)
            count += 1
            parent = str(by_name[parent].get("parentName") or "")
        return count

    return sorted(items, key=depth, reverse=True)


def _not_found(exc: Exception) -> bool:
    return isinstance(exc, CloudOperationsNotFound) or type(exc).__name__ in {
        "NotFound",
        "ResourceNotFound",
    }


def _count_message(bundle_id: str, counts: Mapping[str, int], *, action: str) -> str:
    total = sum(counts.values())
    return (
        f"Cloud Operations bundle {bundle_id} {action} with {total} resources "
        f"({counts.get(_BUCKET.key, 0)} buckets, {counts.get(_DASHBOARD.key, 0)} dashboards, "
        f"{counts.get(_ALERT.key, 0)} alerts, {counts.get(_CHANNEL.key, 0)} channels, "
        f"{counts.get(_SERVICE.key, 0)} services, {counts.get(_SLO.key, 0)} SLOs)"
    )
