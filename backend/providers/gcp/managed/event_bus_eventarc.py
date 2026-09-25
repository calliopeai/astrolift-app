"""Google Cloud Eventarc Advanced bus and Eventarc Standard routing driver.

Eventarc Advanced allows one message bus per project and region.  The primary
resource represented by this driver is therefore intentionally project-shared;
pipelines, enrollments, Google API sources, Standard triggers, and partner
channels are reconciled as labelled children of that bus declaration.
"""

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

KIND = "event_bus"
_API_ROOT = "https://eventarc.googleapis.com/v1"
_PUBLISHING_ROOT = "https://eventarcpublishing.googleapis.com/v1"
_ID_RE = re.compile(r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_LOG_SEVERITIES = {
    "NONE",
    "DEBUG",
    "INFO",
    "NOTICE",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "ALERT",
    "EMERGENCY",
}
_COLLECTIONS = {
    "channelConnections": "channelConnectionId",
    "pipelines": "pipelineId",
    "enrollments": "enrollmentId",
    "googleApiSources": "googleApiSourceId",
    "triggers": "triggerId",
    "channels": "channelId",
}
_LIST_KEYS = {
    "channelConnections": "channelConnections",
    "pipelines": "pipelines",
    "enrollments": "enrollments",
    "googleApiSources": "googleApiSources",
    "triggers": "triggers",
    "channels": "channels",
}
_DECLARATION_KEYS = {
    "pipelines": "pipelines",
    "enrollments": "enrollments",
    "google_api_sources": "googleApiSources",
    "triggers": "triggers",
    "channels": "channels",
}
_DELETE_ORDER = (
    "enrollments",
    "googleApiSources",
    "triggers",
    "pipelines",
    "channelConnections",
    "channels",
)
_CONTROL_KEYS = {
    "id",
    "adopt_existing",
    "reassign_existing",
    "clear_fields",
    "raw_fields",
}
_OUTPUT_ONLY_FIELDS = {
    "activationToken",
    "conditions",
    "createTime",
    "etag",
    "name",
    "pubsubTopic",
    "satisfiesPzs",
    "state",
    "transport",
    "uid",
    "updateTime",
}
_PROTECTED_PROVIDER_FIELDS = _OUTPUT_ONLY_FIELDS | {"labels"}


class EventarcError(RuntimeError):
    pass


class EventarcNotFound(EventarcError):
    pass


class EventarcConflict(EventarcError):
    pass


@dataclass(frozen=True)
class EventarcConfig:
    project_id: str
    location: str
    message_bus_id: str = "astrolift"
    api_endpoint: str = _API_ROOT
    publishing_endpoint: str = _PUBLISHING_ROOT
    deletion_protection_default: bool = True
    operation_timeout_seconds: float = 900
    poll_interval_seconds: float = 5


class EventarcRestClient:
    """Authenticated adapter for the Eventarc control and publishing APIs."""

    def __init__(
        self,
        *,
        endpoint: str = _API_ROOT,
        publishing_endpoint: str = _PUBLISHING_ROOT,
        session: Any | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._publishing_endpoint = publishing_endpoint.rstrip("/")
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

    def list_resources(self, parent: str, collection: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            params = {"pageSize": "100"}
            if token:
                params["pageToken"] = token
            payload = self._request("GET", f"{parent}/{collection}", params=params)
            rows.extend(payload.get(_LIST_KEYS[collection]) or [])
            token = str(payload.get("nextPageToken") or "")
            if not token:
                return rows

    def list_bus_enrollments(self, bus_name: str) -> list[str]:
        rows: list[str] = []
        token = ""
        while True:
            params = {"pageSize": "100"}
            if token:
                params["pageToken"] = token
            payload = self._request("GET", f"{bus_name}:listEnrollments", params=params)
            rows.extend(str(item) for item in payload.get("enrollments") or [])
            token = str(payload.get("nextPageToken") or "")
            if not token:
                return rows

    def create(
        self,
        parent: str,
        collection: str,
        resource_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        id_parameter = "messageBusId" if collection == "messageBuses" else _COLLECTIONS[collection]
        return self._request(
            "POST",
            f"{parent}/{collection}",
            params={id_parameter: resource_id},
            json=body,
        )

    def patch(
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

    def delete(self, name: str, *, etag: str = "") -> dict[str, Any]:
        params: dict[str, str] = {}
        if "/channels/" not in name and "/channelConnections/" not in name:
            params["allowMissing"] = "true"
            if etag:
                params["etag"] = etag
        return self._request("DELETE", name, params=params or None)

    def get_operation(self, name: str) -> dict[str, Any]:
        return self._request("GET", name)

    def publish(self, bus_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{bus_name}:publish",
            json=payload,
            endpoint=self._publishing_endpoint,
        )

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
            f"{(endpoint or self._endpoint).rstrip('/')}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise EventarcNotFound(resource)
        if response.status_code == 409:
            raise EventarcConflict(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise EventarcError(f"Eventarc HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class EventarcDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: EventarcConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._eventarc = client or EventarcRestClient(
            endpoint=config.api_endpoint,
            publishing_endpoint=config.publishing_endpoint,
        )
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="event_bus_eventarc",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_eventarc_config"])
        location = str(cfg.get("location") or self._config.location)
        bus_id = str(cfg.get("message_bus_id") or self._config.message_bus_id)
        bus_name = self._resource_name(location, "messageBuses", bus_id)
        handle = _handle(location, bus_id)
        labels = self._labels(spec, cfg)
        try:
            current = self._get(bus_name)
            if current is None:
                try:
                    operation = self._eventarc.create(
                        self._parent(location),
                        "messageBuses",
                        bus_id,
                        self._bus_body(cfg, labels),
                    )
                    self._wait_operation(operation)
                    current = self._eventarc.get(bus_name)
                except EventarcConflict:
                    current = self._eventarc.get(bus_name)
                    self._assert_adoptable(
                        current,
                        cfg,
                        managed_service_id=spec.managed_service_id,
                        resource="message bus",
                    )
                    if (
                        cfg.get("adopt_existing")
                        and (current.get("labels") or {}).get("astrolift-io-managed-by") != "platform"
                    ):
                        labels["astrolift-io-adopted"] = "true"
                    self._patch_resource(bus_name, current, self._bus_body(cfg, labels))
            else:
                self._assert_adoptable(
                    current,
                    cfg,
                    managed_service_id=spec.managed_service_id,
                    resource="message bus",
                )
                if (
                    cfg.get("adopt_existing")
                    and (current.get("labels") or {}).get("astrolift-io-managed-by") != "platform"
                ):
                    labels["astrolift-io-adopted"] = "true"
                self._patch_resource(bus_name, current, self._bus_body(cfg, labels))
            self._reconcile_children(
                location=location,
                bus_name=bus_name,
                cfg=cfg,
                labels=labels,
            )
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Eventarc: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Eventarc bus {bus_id} and declared routes reconciled",
            ready=True,
        )

    @driver_op(cloud="gcp", driver="event_bus_eventarc")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            location, bus_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg, update=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_eventarc_config"])
        bus_name = self._resource_name(location, "messageBuses", bus_id)
        try:
            current = self._eventarc.get(bus_name)
            self._assert_managed(current, resource="message bus")
            labels = dict(current.get("labels") or {})
            desired_bus = self._bus_body(cfg, labels, partial=True)
            self._patch_resource(bus_name, current, desired_bus)
            self._reconcile_children(
                location=location,
                bus_name=bus_name,
                cfg=cfg,
                labels=labels,
            )
        except EventarcNotFound:
            return UpdateResult(False, spec.handle, "Eventarc message bus not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Eventarc: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Eventarc bus {bus_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="event_bus_eventarc",
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
            location, bus_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        bus_name = self._resource_name(location, "messageBuses", bus_id)
        current = self._get(bus_name)
        if current is None:
            return DeprovisionResult(True, spec.handle, f"Eventarc bus {bus_id} already gone")
        try:
            self._assert_managed(current, resource="message bus")
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["ownership_guard"], retryable=False)
        labels = dict(current.get("labels") or {})
        if labels.get("astrolift-io-adopted") == "true" and not cfg.get("delete_adopted"):
            return DeprovisionResult(
                False,
                spec.handle,
                "adopted Eventarc buses require delete_adopted=true before deletion",
                ["adopted_resource_guard"],
                retryable=False,
            )
        protected = bool(cfg.get("deletion_protection", self._config.deletion_protection_default))
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Eventarc deletion protection is enabled; use force_destroy to confirm teardown",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        parent_label = labels.get("astrolift-io-resource-parent") or _label_value(bus_id)
        service_id = labels.get("astrolift-io-managed-service-id", "")
        try:
            external = self._external_bus_dependents(
                location,
                bus_name,
                parent_label,
                service_id,
            )
            if external and not (force_destroy and cfg.get("delete_external_dependents")):
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Eventarc bus has dependents outside this declaration; "
                    "force_destroy plus delete_external_dependents=true is required",
                    ["external_dependents_present"],
                    retryable=False,
                )
            if external:
                for name in external:
                    self._delete_named(name)
            for collection in _DELETE_ORDER:
                for resource in self._managed_children(
                    location,
                    collection,
                    parent_label,
                    service_id,
                ):
                    self._delete_named(str(resource["name"]), resource=resource)
            self._delete_named(bus_name, resource=current)
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Eventarc resources", exc)
        return DeprovisionResult(True, spec.handle, f"Eventarc bus {bus_id} and managed routes deleted")

    @driver_op(cloud="gcp", driver="event_bus_eventarc")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            location, bus_id = _parse_handle(handle.handle)
            bus = self._eventarc.get(self._resource_name(location, "messageBuses", bus_id))
        except EventarcNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Eventarc message bus does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Eventarc bus: {exc}")
        labels = dict(bus.get("labels") or {})
        parent_label = labels.get("astrolift-io-resource-parent") or _label_value(bus_id)
        service_id = labels.get("astrolift-io-managed-service-id", "")
        counts: dict[str, int] = {}
        try:
            for collection in _DELETE_ORDER:
                resources = self._managed_children(
                    location,
                    collection,
                    parent_label,
                    service_id,
                )
                counts[collection] = len(resources)
                for resource in resources:
                    if collection == "channels":
                        channel_state = str(resource.get("state") or "")
                        if channel_state == "PENDING":
                            return ServiceStatus(
                                handle.handle,
                                "provisioning",
                                f"{resource.get('name')} is waiting for its partner connection",
                            )
                        if channel_state in {"INACTIVE", "STATE_UNSPECIFIED"}:
                            return ServiceStatus(
                                handle.handle,
                                "error",
                                f"{resource.get('name')} is {channel_state}",
                            )
                    for condition in (resource.get("conditions") or {}).values():
                        code = str(condition.get("code") or "OK")
                        if code != "OK":
                            return ServiceStatus(
                                handle.handle,
                                "error",
                                f"{resource.get('name')} is unhealthy: {condition.get('message') or code}",
                            )
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"list Eventarc routes: {exc}")
        detail = ", ".join(f"{value} {key}" for key, value in counts.items() if value)
        return ServiceStatus(
            handle.handle,
            "available",
            f"Eventarc bus {bus_id} available" + (f" with {detail}" if detail else ""),
        )

    @driver_op(cloud="gcp", driver="event_bus_eventarc")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        location, bus_id = _parse_handle(handle.handle)
        bus_name = self._resource_name(location, "messageBuses", bus_id)
        self._eventarc.get(bus_name)
        access_mode = str((config or {}).get("access_mode") or "publish")
        role = "roles/eventarc.messageBusAdmin" if access_mode == "manage" else "roles/eventarc.messageBusUser"
        publish_url = f"{self._config.publishing_endpoint.rstrip('/')}/{bus_name}:publish"
        grants = [Grant(bus_name, [role])]
        if access_mode == "manage":
            grants.append(Grant(f"projects/{self._config.project_id}", ["roles/eventarc.developer"]))
        return Binding(
            env_vars={
                "EVENT_BUS_NAME": ValueRef(literal=bus_name),
                "EVENT_BUS_ID": ValueRef(literal=bus_id),
                "EVENT_BUS_REGION": ValueRef(literal=location),
                "EVENT_BUS_PUBLISH_URL": ValueRef(literal=publish_url),
                "EVENT_BUS_PROVIDER": ValueRef(literal="gcp_eventarc"),
                "GOOGLE_CLOUD_PROJECT": ValueRef(literal=self._config.project_id),
            },
            iam_grants=grants,
            notes=(
                "Publish CloudEvents with Google application-default credentials; "
                "Eventarc Advanced accepts protoMessage, jsonMessage, or avroMessage."
            ),
        )

    @driver_op(cloud="gcp", driver="event_bus_eventarc")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise EventarcError("Eventarc has no snapshot or archive API; route durable events to Pub/Sub or storage")

    @driver_op(cloud="gcp", driver="event_bus_eventarc")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise EventarcError("Eventarc message buses cannot be restored from snapshots")

    def publish_test_event(self, handle: ServiceHandle, *, json_message: str) -> None:
        location, bus_id = _parse_handle(handle.handle)
        self._eventarc.publish(
            self._resource_name(location, "messageBuses", bus_id),
            {"jsonMessage": json_message},
        )

    def connect_partner_channel(
        self,
        handle: ServiceHandle,
        *,
        connection_id: str,
        channel: str,
        activation_token: str,
    ) -> ServiceStatus:
        """Create a provider-side partner connection without persisting its input-only token."""
        if not _ID_RE.fullmatch(connection_id):
            raise EventarcError(f"invalid Eventarc channel connection ID {connection_id!r}")
        if not activation_token:
            raise EventarcError("Eventarc channel connection requires an activation token")
        location, bus_id = _parse_handle(handle.handle)
        bus = self._eventarc.get(self._resource_name(location, "messageBuses", bus_id))
        self._assert_managed(bus, resource="message bus")
        name = self._resource_name(location, "channelConnections", connection_id)
        resolved_channel = self._resolve_name(location, "channels", channel)
        current = self._get(name)
        if current is not None:
            self._assert_managed(current, resource="channel connection")
            if current.get("channel") != resolved_channel:
                raise EventarcError(
                    "Eventarc channel connection target is immutable; create a replacement connection",
                )
            return ServiceStatus(handle.handle, "available", f"Eventarc connection {connection_id} exists")
        labels = dict(bus.get("labels") or {})
        operation = self._eventarc.create(
            self._parent(location),
            "channelConnections",
            connection_id,
            {
                "channel": resolved_channel,
                "activationToken": activation_token,
                "labels": labels,
            },
        )
        self._wait_operation(operation)
        return ServiceStatus(handle.handle, "available", f"Eventarc connection {connection_id} created")

    def disconnect_partner_channel(
        self,
        handle: ServiceHandle,
        *,
        connection_id: str,
    ) -> ServiceStatus:
        location, _ = _parse_handle(handle.handle)
        name = self._resource_name(location, "channelConnections", connection_id)
        current = self._get(name)
        if current is not None:
            self._assert_managed(current, resource="channel connection")
            self._delete_named(name, resource=current)
        return ServiceStatus(handle.handle, "available", f"Eventarc connection {connection_id} removed")

    @driver_op(cloud="gcp", driver="event_bus_eventarc", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        labels = {"type": "object", "additionalProperties": {"type": "string"}}
        raw_fields = {
            "type": "object",
            "description": "Provider-native Eventarc v1 fields for forward-compatible advanced use.",
            "additionalProperties": True,
        }
        declaration_base = {
            "id": {"type": "string", "pattern": _ID_RE.pattern},
            "labels": labels,
            "adopt_existing": {"type": "boolean", "default": False},
            "reassign_existing": {"type": "boolean", "default": False},
            "clear_fields": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "raw_fields": raw_fields,
        }
        described_declaration = {
            **declaration_base,
            "display_name": {"type": "string"},
            "annotations": labels,
        }
        payload_format = {
            "type": "object",
            "properties": {
                "json": {"type": "object", "additionalProperties": False},
                "avro": {
                    "type": "object",
                    "properties": {"schema_definition": {"type": "string"}},
                    "additionalProperties": False,
                },
                "protobuf": {
                    "type": "object",
                    "properties": {"schema_definition": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "minProperties": 1,
            "maxProperties": 1,
            "additionalProperties": False,
        }
        destination = {
            "type": "object",
            "properties": {
                "http_endpoint": {
                    "type": "object",
                    "required": ["uri"],
                    "properties": {
                        "uri": {"type": "string", "pattern": "^https://"},
                        "message_binding_template": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "message_bus": {"type": "string"},
                "topic": {"type": "string"},
                "workflow": {"type": "string"},
                "network_config": {
                    "type": "object",
                    "required": ["network_attachment"],
                    "properties": {"network_attachment": {"type": "string"}},
                    "additionalProperties": False,
                },
                "authentication_config": {
                    "type": "object",
                    "properties": {
                        "google_oidc": {
                            "type": "object",
                            "required": ["service_account"],
                            "properties": {
                                "service_account": {"type": "string"},
                                "audience": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                        "oauth_token": {
                            "type": "object",
                            "required": ["service_account"],
                            "properties": {
                                "service_account": {"type": "string"},
                                "scope": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "minProperties": 1,
                    "maxProperties": 1,
                    "additionalProperties": False,
                },
                "output_payload_format": payload_format,
            },
            "additionalProperties": False,
        }
        pipeline = {
            "type": "object",
            "required": ["id", "destinations"],
            "properties": {
                **described_declaration,
                "crypto_key_name": {"type": "string"},
                "destinations": {"type": "array", "minItems": 1, "maxItems": 1, "items": destination},
                "mediations": {"type": "array", "maxItems": 1, "items": {"type": "object"}},
                "input_payload_format": payload_format,
                "retry_policy": {
                    "type": "object",
                    "properties": {
                        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 100},
                        "min_retry_delay": {"type": "string"},
                        "max_retry_delay": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "logging_config": _logging_schema(),
            },
            "additionalProperties": False,
        }
        enrollment = {
            "type": "object",
            "required": ["id", "cel_match", "destination_pipeline"],
            "properties": {
                **described_declaration,
                "cel_match": {"type": "string", "minLength": 1},
                "destination_pipeline": {"type": "string"},
                "message_bus": {"type": "string"},
            },
            "additionalProperties": False,
        }
        google_source = {
            "type": "object",
            "required": ["id"],
            "properties": {
                **described_declaration,
                "destination": {"type": "string"},
                "crypto_key_name": {"type": "string"},
                "logging_config": _logging_schema(),
                "project_subscriptions": {"type": "array", "items": {"type": "string"}},
                "organization_subscription": {"type": "boolean"},
            },
            "additionalProperties": False,
        }
        trigger = {
            "type": "object",
            "required": ["id", "event_filters", "destination"],
            "properties": {
                **declaration_base,
                "event_filters": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["attribute", "value"],
                        "properties": {
                            "attribute": {"type": "string"},
                            "value": {"type": "string"},
                            "operator": {
                                "type": "string",
                                "enum": ["path_pattern", "match-path-pattern"],
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "destination": {
                    "type": "object",
                    "properties": {
                        "cloud_run": {
                            "type": "object",
                            "required": ["service", "region"],
                            "properties": {
                                "service": {"type": "string"},
                                "region": {"type": "string"},
                                "path": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                        "gke": {
                            "type": "object",
                            "required": ["cluster", "location", "namespace", "service"],
                            "properties": {
                                "cluster": {"type": "string"},
                                "location": {"type": "string"},
                                "namespace": {"type": "string"},
                                "service": {"type": "string"},
                                "path": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                        "http_endpoint": {
                            "type": "object",
                            "required": ["uri"],
                            "properties": {"uri": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        "network_config": {
                            "type": "object",
                            "required": ["network_attachment"],
                            "properties": {"network_attachment": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        "workflow": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "service_account": {"type": "string"},
                "event_data_content_type": {"type": "string"},
                "channel": {"type": "string"},
                "retry_policy": {
                    "type": "object",
                    "properties": {"max_attempts": {"type": "integer", "enum": [1]}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        }
        channel = {
            "type": "object",
            "required": ["id", "provider"],
            "properties": {
                **declaration_base,
                "provider": {"type": "string"},
                "crypto_key_name": {"type": "string"},
            },
            "additionalProperties": False,
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "message_bus_id": {"type": "string", "pattern": _ID_RE.pattern},
                "location": {"type": "string"},
                "display_name": {"type": "string"},
                "crypto_key_name": {"type": "string"},
                "logging_config": _logging_schema(),
                "labels": labels,
                "annotations": labels,
                "raw_fields": raw_fields,
                "clear_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "adopt_existing": {"type": "boolean", "default": False},
                "reassign_existing": {"type": "boolean", "default": False},
                "delete_adopted": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
                "delete_external_dependents": {"type": "boolean", "default": False},
                "access_mode": {"type": "string", "enum": ["publish", "manage"], "default": "publish"},
                "pipelines": {"type": "array", "maxItems": 100, "items": pipeline},
                "enrollments": {"type": "array", "maxItems": 100, "items": enrollment},
                "google_api_sources": {
                    "type": "array",
                    "maxItems": 1,
                    "items": google_source,
                },
                "triggers": {"type": "array", "items": trigger},
                "channels": {"type": "array", "items": channel},
                "prune_pipelines": {"type": "boolean", "default": True},
                "prune_enrollments": {"type": "boolean", "default": True},
                "prune_google_api_sources": {"type": "boolean", "default": True},
                "prune_triggers": {"type": "boolean", "default": True},
                "prune_channels": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="event_bus_eventarc", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EVENT_BUS_NAME": "Fully qualified Eventarc Advanced message-bus resource name",
                "EVENT_BUS_ID": "Eventarc message-bus ID",
                "EVENT_BUS_REGION": "Eventarc location",
                "EVENT_BUS_PUBLISH_URL": "Authenticated Eventarc Publishing API endpoint",
                "EVENT_BUS_PROVIDER": "Portable provider discriminator",
                "GOOGLE_CLOUD_PROJECT": "Google Cloud project ID",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "annotations",
            "channels",
            "crypto_key_name",
            "deletion_protection",
            "display_name",
            "enrollments",
            "google_api_sources",
            "labels",
            "logging_config",
            "pipelines",
            "prune_channels",
            "prune_enrollments",
            "prune_google_api_sources",
            "prune_pipelines",
            "prune_triggers",
            "raw_fields",
            "triggers",
        ]

    def _validate_config(self, cfg: dict[str, Any], *, update: bool = False) -> str:
        if not self._config.project_id:
            return "Eventarc requires a Google Cloud project ID"
        location = str(cfg.get("location") or self._config.location)
        if not location:
            return "Eventarc requires a location"
        bus_id = str(cfg.get("message_bus_id") or self._config.message_bus_id)
        if not _ID_RE.fullmatch(bus_id):
            return "message_bus_id must match ^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$"
        if update and "location" in cfg:
            return "Eventarc location is immutable; reprovision the shared bus"
        if update and "message_bus_id" in cfg:
            return "Eventarc message_bus_id is immutable; reprovision the shared bus"
        severity = str((cfg.get("logging_config") or {}).get("log_severity") or "NONE")
        if severity not in _LOG_SEVERITIES:
            return f"invalid Eventarc log severity {severity!r}"
        for config_key, collection in _DECLARATION_KEYS.items():
            declarations = list(cfg.get(config_key) or [])
            ids = [str(item.get("id") or "") for item in declarations]
            if len(ids) != len(set(ids)):
                return f"duplicate IDs in Eventarc {config_key}"
            for declaration in declarations:
                resource_id = str(declaration.get("id") or "")
                if not _ID_RE.fullmatch(resource_id):
                    return f"invalid Eventarc {collection} ID {resource_id!r}"
                # Google's JSON parser accepts a field's proto name as well as
                # its lowerCamelCase JSON name, so a raw or cleared field
                # spelled in proto form bypassed the exact-match checks below
                # (#1981).
                proto_names, forbidden = raw_field_conflicts(
                    declaration.get("raw_fields") or {},
                    _PROTECTED_PROVIDER_FIELDS,
                )
                if proto_names:
                    return (
                        "Eventarc raw_fields must use the API's lowerCamelCase JSON field names, "
                        f"not {', '.join(proto_names)}"
                    )
                if forbidden:
                    return f"Eventarc raw_fields cannot set output-only fields: {', '.join(forbidden)}"
                proto_names, forbidden_clear = raw_field_conflicts(
                    declaration.get("clear_fields") or [],
                    _PROTECTED_PROVIDER_FIELDS,
                )
                if proto_names:
                    return (
                        "Eventarc clear_fields must use the API's lowerCamelCase JSON field names, "
                        f"not {', '.join(proto_names)}"
                    )
                if forbidden_clear:
                    return f"Eventarc clear_fields cannot clear protected fields: {', '.join(forbidden_clear)}"
        for pipeline in cfg.get("pipelines") or []:
            destinations = list(pipeline.get("destinations") or [])
            if len(destinations) != 1:
                return f"Eventarc pipeline {pipeline.get('id')} requires exactly one destination"
            destination_kinds = {
                key for key in ("http_endpoint", "message_bus", "topic", "workflow") if destinations[0].get(key)
            }
            if len(destination_kinds) != 1:
                return f"Eventarc pipeline {pipeline.get('id')} destination requires exactly one target"
            if destinations[0].get("output_payload_format") and not pipeline.get("input_payload_format"):
                return f"Eventarc pipeline {pipeline.get('id')} output format requires input_payload_format"
            http_endpoint = destinations[0].get("http_endpoint") or {}
            if http_endpoint and not str(http_endpoint.get("uri") or "").startswith("https://"):
                return f"Eventarc pipeline {pipeline.get('id')} HTTP destination requires an HTTPS URI"
            if (destinations[0].get("network_config") or destinations[0].get("authentication_config")) and not (
                http_endpoint
            ):
                return (
                    f"Eventarc pipeline {pipeline.get('id')} network/authentication config requires an HTTP destination"
                )
            authentication = destinations[0].get("authentication_config") or {}
            auth_kinds = {key for key in ("google_oidc", "oauth_token") if authentication.get(key)}
            if authentication and len(auth_kinds) != 1:
                return f"Eventarc pipeline {pipeline.get('id')} authentication requires exactly one token type"
            for auth_kind in auth_kinds:
                if not authentication[auth_kind].get("service_account"):
                    return f"Eventarc pipeline {pipeline.get('id')} authentication requires service_account"
            for field in ("input_payload_format",):
                format_error = _payload_format_error(pipeline.get(field), field=field)
                if format_error:
                    return f"Eventarc pipeline {pipeline.get('id')} {format_error}"
            format_error = _payload_format_error(
                destinations[0].get("output_payload_format"),
                field="output_payload_format",
            )
            if format_error:
                return f"Eventarc pipeline {pipeline.get('id')} {format_error}"
            if len(pipeline.get("mediations") or []) > 1:
                return f"Eventarc pipeline {pipeline.get('id')} supports one mediation"
            retry = pipeline.get("retry_policy") or {}
            attempts = int(retry.get("max_attempts") or 5)
            if not 1 <= attempts <= 100:
                return "Eventarc pipeline max_attempts must be between 1 and 100"
            minimum = _duration_seconds(str(retry.get("min_retry_delay") or ""))
            maximum = _duration_seconds(str(retry.get("max_retry_delay") or ""))
            if minimum is not None and not 1 <= minimum <= 600:
                return "Eventarc pipeline min_retry_delay must be between 1s and 600s"
            if maximum is not None and not 1 <= maximum <= 600:
                return "Eventarc pipeline max_retry_delay must be between 1s and 600s"
            if minimum is not None and maximum is not None and minimum > maximum:
                return "Eventarc pipeline min_retry_delay cannot exceed max_retry_delay"
        if len(cfg.get("pipelines") or []) > 100:
            return "Eventarc supports at most 100 pipelines per project and region"
        pipeline_ids = {str(item.get("id")) for item in cfg.get("pipelines") or []}
        for enrollment in cfg.get("enrollments") or []:
            if not str(enrollment.get("cel_match") or "").strip():
                return f"Eventarc enrollment {enrollment.get('id')} requires cel_match"
            destination = str(enrollment.get("destination_pipeline") or "")
            if not destination:
                return f"Eventarc enrollment {enrollment.get('id')} requires destination_pipeline"
            if "/" not in destination and pipeline_ids and destination not in pipeline_ids:
                return f"Eventarc enrollment {enrollment.get('id')} references undeclared pipeline {destination}"
        if len(cfg.get("enrollments") or []) > 100:
            return "Eventarc supports at most 100 enrollments per project and region"
        if len(cfg.get("google_api_sources") or []) > 1:
            return "Eventarc supports one Google API source per project and region"
        for source in cfg.get("google_api_sources") or []:
            if source.get("project_subscriptions") and source.get("organization_subscription"):
                return "Eventarc Google API source cannot combine project and organization subscriptions"
        for trigger in cfg.get("triggers") or []:
            if not trigger.get("event_filters"):
                return f"Eventarc trigger {trigger.get('id')} requires event_filters"
            filters = list(trigger.get("event_filters") or [])
            if not any(item.get("attribute") == "type" for item in filters):
                return f"Eventarc trigger {trigger.get('id')} requires a type event filter"
            invalid_operators = {
                str(item.get("operator"))
                for item in filters
                if item.get("operator") not in (None, "", "path_pattern", "match-path-pattern")
            }
            if invalid_operators:
                return f"Eventarc trigger has invalid event-filter operator {sorted(invalid_operators)[0]!r}"
            trigger_destination = trigger.get("destination") or {}
            kinds = {key for key in ("cloud_run", "gke", "http_endpoint", "workflow") if trigger_destination.get(key)}
            if len(kinds) != 1:
                return f"Eventarc trigger {trigger.get('id')} destination requires exactly one target"
            if trigger_destination.get("network_config") and not trigger_destination.get("http_endpoint"):
                return f"Eventarc trigger {trigger.get('id')} network_config requires http_endpoint"
        for channel in cfg.get("channels") or []:
            if not channel.get("provider"):
                return f"Eventarc channel {channel.get('id')} requires provider"
        # Google's JSON parser accepts a field's proto name as well as its
        # lowerCamelCase JSON name, so a raw or cleared field spelled in proto
        # form bypassed the exact-match checks below (#1981).
        proto_names, forbidden = raw_field_conflicts(cfg.get("raw_fields") or {}, _PROTECTED_PROVIDER_FIELDS)
        if proto_names:
            return (
                f"Eventarc raw_fields must use the API's lowerCamelCase JSON field names, not {', '.join(proto_names)}"
            )
        if forbidden:
            return f"Eventarc raw_fields cannot set output-only fields: {', '.join(forbidden)}"
        proto_names, forbidden_clear = raw_field_conflicts(cfg.get("clear_fields") or [], _PROTECTED_PROVIDER_FIELDS)
        if proto_names:
            return (
                "Eventarc clear_fields must use the API's lowerCamelCase JSON field names, "
                f"not {', '.join(proto_names)}"
            )
        if forbidden_clear:
            return f"Eventarc clear_fields cannot clear protected fields: {', '.join(forbidden_clear)}"
        return ""

    def _reconcile_children(
        self,
        *,
        location: str,
        bus_name: str,
        cfg: dict[str, Any],
        labels: dict[str, str],
    ) -> None:
        parent_label = labels.get("astrolift-io-resource-parent") or _label_value(bus_name.rsplit("/", 1)[-1])
        service_id = labels.get("astrolift-io-managed-service-id", "")
        child_labels = dict(labels)
        child_labels["astrolift-io-resource-parent"] = parent_label
        parent = self._parent(location)
        for config_key, collection in _DECLARATION_KEYS.items():
            if config_key not in cfg:
                continue
            desired_ids: set[str] = set()
            for declaration in cfg.get(config_key) or []:
                resource_id = str(declaration["id"])
                desired_ids.add(resource_id)
                name = self._resource_name(location, collection, resource_id)
                body = self._child_body(
                    collection,
                    declaration,
                    bus_name=bus_name,
                    location=location,
                    labels=child_labels,
                )
                current = self._get(name)
                if current is None:
                    try:
                        operation = self._eventarc.create(parent, collection, resource_id, body)
                        self._wait_operation(operation)
                    except EventarcConflict:
                        current = self._eventarc.get(name)
                        self._assert_adoptable(
                            current,
                            declaration,
                            managed_service_id=labels.get("astrolift-io-managed-service-id", ""),
                            resource=collection,
                        )
                        if (
                            declaration.get("adopt_existing")
                            and (current.get("labels") or {}).get("astrolift-io-managed-by") != "platform"
                        ):
                            body["labels"]["astrolift-io-adopted"] = "true"
                        self._assert_child_immutable(collection, current, body)
                        self._patch_resource(name, current, body)
                else:
                    self._assert_adoptable(
                        current,
                        declaration,
                        managed_service_id=labels.get("astrolift-io-managed-service-id", ""),
                        resource=collection,
                    )
                    if (
                        declaration.get("adopt_existing")
                        and (current.get("labels") or {}).get("astrolift-io-managed-by") != "platform"
                    ):
                        body["labels"]["astrolift-io-adopted"] = "true"
                    self._assert_child_immutable(collection, current, body)
                    self._patch_resource(name, current, body)
            prune_key = f"prune_{config_key}"
            if cfg.get(prune_key, True):
                for resource in self._managed_children(
                    location,
                    collection,
                    parent_label,
                    service_id,
                ):
                    resource_id = str(resource.get("name") or "").rsplit("/", 1)[-1]
                    if resource_id not in desired_ids:
                        self._delete_named(str(resource["name"]), resource=resource)

    def _bus_body(
        self,
        cfg: dict[str, Any],
        labels: dict[str, str],
        *,
        partial: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        mapping = {
            "display_name": "displayName",
            "crypto_key_name": "cryptoKeyName",
            "annotations": "annotations",
        }
        for key, api_key in mapping.items():
            if key in cfg:
                body[api_key] = cfg[key]
        if "logging_config" in cfg:
            body["loggingConfig"] = _camelize(cfg["logging_config"])
        if "raw_fields" in cfg:
            body.update(dict(cfg["raw_fields"] or {}))
        for field in cfg.get("clear_fields") or []:
            body[str(field)] = None
        if not partial or "labels" in cfg:
            desired_labels = dict(labels)
            desired_labels.update(_normalized_labels(cfg.get("labels") or {}))
            body["labels"] = desired_labels
        if not partial and "displayName" not in body:
            body["displayName"] = "Astrolift shared event bus"
        return body

    def _child_body(
        self,
        collection: str,
        declaration: dict[str, Any],
        *,
        bus_name: str,
        location: str,
        labels: dict[str, str],
    ) -> dict[str, Any]:
        source = {key: value for key, value in declaration.items() if key not in _CONTROL_KEYS}
        source.pop("id", None)
        if collection == "enrollments":
            pipeline = str(source.pop("destination_pipeline"))
            source["destination"] = self._resolve_name(location, "pipelines", pipeline)
            source["message_bus"] = self._resolve_name(
                location,
                "messageBuses",
                str(source.get("message_bus") or bus_name),
            )
        elif collection == "googleApiSources":
            source["destination"] = self._resolve_name(
                location,
                "messageBuses",
                str(source.get("destination") or bus_name),
            )
            if "project_subscriptions" in source:
                source["project_subscriptions"] = {"list": list(source["project_subscriptions"])}
            if "organization_subscription" in source:
                source["organization_subscription"] = {"enabled": bool(source["organization_subscription"])}
        elif collection == "pipelines":
            destinations: list[dict[str, Any]] = []
            for raw in source.get("destinations") or []:
                destination = dict(raw)
                if destination.get("message_bus"):
                    destination["message_bus"] = self._resolve_name(
                        location,
                        "messageBuses",
                        str(destination["message_bus"]),
                    )
                destinations.append(destination)
            source["destinations"] = destinations
        elif collection == "triggers" and source.get("channel"):
            source["channel"] = self._resolve_name(location, "channels", str(source["channel"]))
        elif collection == "channels":
            source["provider"] = self._resolve_name(location, "providers", str(source["provider"]))
        body = dict(_camelize(source))
        raw_fields = dict(declaration.get("raw_fields") or {})
        body.update(raw_fields)
        for field in declaration.get("clear_fields") or []:
            body[str(field)] = None
        desired_labels = dict(labels)
        desired_labels.update(_normalized_labels(declaration.get("labels") or {}))
        body["labels"] = desired_labels
        return body

    def _patch_resource(
        self,
        name: str,
        current: dict[str, Any],
        desired: dict[str, Any],
    ) -> None:
        changed = {
            key: value for key, value in desired.items() if key not in _OUTPUT_ONLY_FIELDS and value != current.get(key)
        }
        if not changed:
            return
        if current.get("etag"):
            changed["etag"] = current["etag"]
        # FieldMask's JSON representation uses lowerCamel field paths, matching
        # the Eventarc REST resource document rather than protobuf snake_case.
        mask = [key for key in changed if key != "etag"]
        self._wait_operation(self._eventarc.patch(name, changed, update_mask=mask))

    def _assert_adoptable(
        self,
        current: dict[str, Any],
        cfg: dict[str, Any],
        *,
        managed_service_id: str,
        resource: str,
    ) -> None:
        labels = dict(current.get("labels") or {})
        if labels.get("astrolift-io-managed-by") == "platform":
            current_service_id = str(labels.get("astrolift-io-managed-service-id") or "")
            if (
                current_service_id
                and managed_service_id
                and current_service_id != managed_service_id
                and not cfg.get("reassign_existing")
            ):
                raise EventarcError(
                    f"Eventarc {resource} belongs to another managed service; "
                    "set reassign_existing=true to transfer ownership",
                )
            return
        if not cfg.get("adopt_existing"):
            quota_hint = (
                " Eventarc Advanced allows one bus per project and region." if resource == "message bus" else ""
            )
            raise EventarcError(
                f"existing Eventarc {resource} is not Astrolift-owned; "
                f"set adopt_existing=true to claim it.{quota_hint}",
            )

    @staticmethod
    def _assert_managed(current: dict[str, Any], *, resource: str) -> None:
        labels = dict(current.get("labels") or {})
        if labels.get("astrolift-io-managed-by") != "platform":
            raise EventarcError(f"Eventarc {resource} is not owned by Astrolift")

    @staticmethod
    def _assert_child_immutable(
        collection: str,
        current: dict[str, Any],
        desired: dict[str, Any],
    ) -> None:
        if collection == "enrollments" and current.get("messageBus") != desired.get("messageBus"):
            raise EventarcError("Eventarc enrollment message_bus is immutable; reprovision the enrollment")
        if collection == "channels" and current.get("provider") != desired.get("provider"):
            raise EventarcError("Eventarc channel provider is immutable; reprovision the channel")
        if collection == "triggers":
            current_types = {
                str(item.get("value") or "")
                for item in current.get("eventFilters") or []
                if item.get("attribute") == "type"
            }
            desired_types = {
                str(item.get("value") or "")
                for item in desired.get("eventFilters") or []
                if item.get("attribute") == "type"
            }
            if current_types and desired_types and current_types != desired_types:
                raise EventarcError("Eventarc trigger event type is immutable; reprovision the trigger")

    def _external_bus_dependents(
        self,
        location: str,
        bus_name: str,
        parent_label: str,
        service_id: str,
    ) -> list[str]:
        candidates = set(self._eventarc.list_bus_enrollments(bus_name))
        parent = self._parent(location)
        for resource in self._eventarc.list_resources(parent, "googleApiSources"):
            if resource.get("destination") == bus_name:
                candidates.add(str(resource["name"]))
        for resource in self._eventarc.list_resources(parent, "pipelines"):
            if any(item.get("messageBus") == bus_name for item in resource.get("destinations") or []):
                candidates.add(str(resource["name"]))
        external: list[str] = []
        for name in sorted(candidates):
            resource = self._eventarc.get(name)
            labels = dict(resource.get("labels") or {})
            if (
                labels.get("astrolift-io-managed-by") != "platform"
                or labels.get("astrolift-io-resource-parent") != parent_label
                or (service_id and labels.get("astrolift-io-managed-service-id") != service_id)
            ):
                external.append(name)
        return external

    def _managed_children(
        self,
        location: str,
        collection: str,
        parent_label: str,
        service_id: str,
    ) -> list[dict[str, Any]]:
        return [
            resource
            for resource in self._eventarc.list_resources(self._parent(location), collection)
            if (resource.get("labels") or {}).get("astrolift-io-managed-by") == "platform"
            and (resource.get("labels") or {}).get("astrolift-io-resource-parent") == parent_label
            and (not service_id or (resource.get("labels") or {}).get("astrolift-io-managed-service-id") == service_id)
        ]

    def _delete_named(self, name: str, *, resource: dict[str, Any] | None = None) -> None:
        current = resource
        if current is None:
            current = self._get(name)
        if current is None:
            return
        operation = self._eventarc.delete(name, etag=str(current.get("etag") or ""))
        self._wait_operation(operation)

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation:
            return {}
        current = operation
        name = str(operation.get("name") or "")
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        while not current.get("done"):
            if not name:
                raise EventarcError("Eventarc operation response has no name")
            if self._monotonic() > deadline:
                raise EventarcError(f"Eventarc operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._eventarc.get_operation(name)
        if current.get("error"):
            error = current["error"]
            raise EventarcError(f"Eventarc operation {name} failed: {error.get('message') or error}")
        response = current.get("response") or {}
        return dict(response) if isinstance(response, dict) else {}

    def _get(self, name: str) -> dict[str, Any] | None:
        try:
            return self._eventarc.get(name)
        except EventarcNotFound:
            return None

    def _parent(self, location: str) -> str:
        return f"projects/{self._config.project_id}/locations/{location}"

    def _resource_name(self, location: str, collection: str, resource_id: str) -> str:
        return f"{self._parent(location)}/{collection}/{resource_id}"

    def _resolve_name(self, location: str, collection: str, value: str) -> str:
        if value.startswith("projects/"):
            return value
        return self._resource_name(location, collection, value)

    def _labels(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, str]:
        bus_id = str(cfg.get("message_bus_id") or self._config.message_bus_id)
        labels = {
            "astrolift-io-managed-by": "platform",
            "astrolift-io-organization": _label_value(spec.organization_slug),
            "astrolift-io-app": _label_value(spec.app_slug),
            "astrolift-io-environment": _label_value(spec.environment_name),
            "astrolift-io-resource-parent": _label_value(bus_id),
        }
        if spec.binding_id:
            labels["astrolift-io-binding"] = _label_value(spec.binding_id)
        if spec.managed_service_id:
            labels["astrolift-io-managed-service-id"] = _label_value(spec.managed_service_id)
            labels[MANAGED_SERVICE_ID_LABEL] = _label_value(spec.managed_service_id)
        labels.update(_normalized_labels(cfg.get("labels") or {}))
        return labels


def _logging_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"log_severity": {"type": "string", "enum": sorted(_LOG_SEVERITIES)}},
        "additionalProperties": False,
    }


def _handle(location: str, bus_id: str) -> str:
    return f"{KIND}/{location}/{bus_id}"


def _parse_handle(handle: str) -> tuple[str, str]:
    parts = handle.split("/")
    if len(parts) != 3 or parts[0] != KIND or not parts[1] or not _ID_RE.fullmatch(parts[2]):
        raise ValueError("invalid Eventarc handle; expected event_bus/<location>/<message-bus-id>")
    return parts[1], parts[2]


def _normalized_labels(labels: dict[str, Any]) -> dict[str, str]:
    return {_label_key(str(key)): _label_value(str(value)) for key, value in labels.items()}


def _label_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    return (normalized or "label")[:63]


def _label_value(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    return normalized[:63]


def _camelize(value: Any) -> Any:
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    if isinstance(value, dict):
        return {_camel_key(str(key)): _camelize(item) for key, item in value.items()}
    return value


def _camel_key(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _duration_seconds(value: str) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"([1-9][0-9]*)s", value)
    if not match:
        return -1
    return int(match.group(1))


def _payload_format_error(value: Any, *, field: str) -> str:
    if value in (None, {}):
        return ""
    if not isinstance(value, dict):
        return f"{field} must be an object"
    kinds = {key for key in ("json", "avro", "protobuf") if key in value}
    if len(kinds) != 1 or len(value) != 1:
        return f"{field} requires exactly one of json, avro, or protobuf"
    return ""


def _deprovision_error(handle: str, action: str, exc: Exception) -> DeprovisionResult:
    retryable = not isinstance(exc, (EventarcConflict, ValueError))
    return DeprovisionResult(
        False,
        handle,
        f"{action}: {exc}",
        [str(exc)],
        retryable=retryable,
    )
