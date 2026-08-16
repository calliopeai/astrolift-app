"""Google Cloud Managed Service for Apache Kafka lifecycle driver.

The managed boundary is a Kafka cluster and its topics, ACLs, consumer-group
offset operations, Schema Registry instances, Kafka Connect clusters, and
connectors.  Cluster and Connect ownership lives in provider labels.  Schema
Registry has no labels, so a reserved JSON-schema subject records the owning
Astrolift managed-service id instead of treating every existing registry as
safe to mutate.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass
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

KIND = "event_stream"
_API_ROOT = "https://managedkafka.googleapis.com/v1"
_ID_RE = re.compile(r"^[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?$")
_TOPIC_RE = re.compile(r"^[A-Za-z0-9._-]{1,249}$")
_REGISTRY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
_ACL_ID_RE = re.compile(
    r"^(?:cluster|allTopics|allConsumerGroups|allTransactionalIds|"
    r"(?:topic|consumerGroup|transactionalId|topicPrefixed|consumerGroupPrefixed|"
    r"transactionalIdPrefixed)/[^/]+)$",
)
_ACL_OPERATIONS = {
    "ALL",
    "READ",
    "WRITE",
    "CREATE",
    "DELETE",
    "ALTER",
    "DESCRIBE",
    "CLUSTER_ACTION",
    "DESCRIBE_CONFIGS",
    "ALTER_CONFIGS",
    "IDEMPOTENT_WRITE",
}
_SCHEMA_TYPES = {"AVRO", "JSON", "PROTOBUF"}
_COMPATIBILITY = {
    "NONE",
    "BACKWARD",
    "BACKWARD_TRANSITIVE",
    "FORWARD",
    "FORWARD_TRANSITIVE",
    "FULL",
    "FULL_TRANSITIVE",
}
_SCHEMA_MODES = {"READONLY", "READWRITE", "IMPORT"}
_OWNERSHIP_SUBJECT = "_astrolift_ownership"
_GIB = 1024**3
_SIZE_CAPACITY = {
    "small": (3, 12),
    "medium": (6, 24),
    "large": (12, 48),
    "xlarge": (24, 96),
}
_OUTPUT_ONLY = {
    "bootstrapAddress",
    "brokerDetails",
    "createTime",
    "effectiveCapacityConfig",
    "kafkaVersion",
    "name",
    "satisfiesPzi",
    "satisfiesPzs",
    "state",
    "updateTime",
}
_PROTECTED_RAW_FIELDS = _OUTPUT_ONLY | {"labels"}
_CLUSTER_STRUCTURED_FIELDS = {
    "capacityConfig",
    "gcpConfig",
    "rebalanceConfig",
    "tlsConfig",
    "updateOptions",
}
_CONNECT_STRUCTURED_FIELDS = {"kafkaCluster", "capacityConfig", "gcpConfig", "config"}
_CONNECTOR_STRUCTURED_FIELDS = {"configs", "taskRestartPolicy"}


class ManagedKafkaError(RuntimeError):
    pass


class ManagedKafkaNotFound(ManagedKafkaError):
    pass


class ManagedKafkaConflict(ManagedKafkaError):
    pass


@dataclass(frozen=True)
class ManagedKafkaConfig:
    project_id: str
    location: str
    cluster_id_prefix: str = "astrolift"
    subnet_names: tuple[str, ...] = ()
    api_endpoint: str = _API_ROOT
    deletion_protection_default: bool = True
    operation_timeout_seconds: float = 1800
    poll_interval_seconds: float = 5


class ManagedKafkaRestClient:
    """Authenticated request-shaped adapter for Managed Kafka v1."""

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

    def get(self, name: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request("GET", name, params=params)

    def list_resources(
        self,
        parent: str,
        collection: str,
        response_key: str,
        *,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            query = {"pageSize": "1000", **(params or {})}
            if token:
                query["pageToken"] = token
            payload = self._request("GET", f"{parent}/{collection}", params=query)
            rows.extend(payload.get(response_key) or [])
            token = str(payload.get("nextPageToken") or "")
            if not token:
                return rows

    def create(
        self,
        parent: str,
        collection: str,
        resource_id: str,
        id_parameter: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
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

    def delete(self, name: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request("DELETE", name, params=params)

    def action(self, name: str, action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", f"{name}:{action}", json=body or {})

    def create_schema_registry(self, parent: str, registry_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{parent}/schemaRegistries",
            json={"schemaRegistryId": registry_id, "schemaRegistry": {}},
        )

    def lookup_schema(
        self,
        registry_name: str,
        subject: str,
        body: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            return self._request(
                "POST",
                f"{registry_name}/subjects/{quote(subject, safe='')}",
                json=body,
            )
        except ManagedKafkaNotFound:
            return None

    def create_schema_version(
        self,
        registry_name: str,
        subject: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{registry_name}/subjects/{quote(subject, safe='')}/versions",
            json=body,
        )

    def get_schema_version(
        self,
        registry_name: str,
        subject: str,
        version: str = "latest",
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{registry_name}/subjects/{quote(subject, safe='')}/versions/{quote(version, safe='')}",
        )

    def update_schema_setting(
        self,
        registry_name: str,
        setting: str,
        subject: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        suffix = f"/{quote(subject, safe='')}" if subject else ""
        return self._request("PUT", f"{registry_name}/{setting}{suffix}", json=body)

    def delete_schema_subject(
        self,
        registry_name: str,
        subject: str,
        *,
        permanent: bool,
    ) -> dict[str, Any]:
        if permanent:
            self._request(
                "DELETE",
                f"{registry_name}/subjects/{quote(subject, safe='')}",
                params={"permanent": "false"},
            )
        return self._request(
            "DELETE",
            f"{registry_name}/subjects/{quote(subject, safe='')}",
            params={"permanent": str(permanent).lower()},
        )

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
            raise ManagedKafkaNotFound(resource)
        if response.status_code == 409:
            raise ManagedKafkaConflict(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise ManagedKafkaError(f"Managed Kafka HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {"items": payload}


class ManagedKafkaDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: ManagedKafkaConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._kafka = client or ManagedKafkaRestClient(endpoint=config.api_endpoint)
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="event_stream_managed_kafka",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg, size=spec.size)
        if error:
            return ProvisionResult(False, "", error, ["invalid_managed_kafka_config"])
        location = str(cfg.get("location") or self._config.location)
        cluster_id = self._cluster_id(spec, cfg)
        handle = _handle(location, cluster_id)
        name = self._cluster_name(location, cluster_id)
        labels = self._labels(spec, cfg)
        labels.setdefault("astrolift-io-managed-service-id", _label_value(cluster_id))
        try:
            cluster = self._get(name, full=True)
            if cluster is None:
                body = self._cluster_body(cfg, spec.size, labels)
                try:
                    self._wait_operation(
                        self._kafka.create(
                            self._parent(location),
                            "clusters",
                            cluster_id,
                            "clusterId",
                            body,
                        ),
                    )
                    cluster = self._kafka.get(name, params={"view": "FULL"})
                except ManagedKafkaConflict:
                    cluster = self._kafka.get(name, params={"view": "FULL"})
                    self._assert_adoptable(cluster, cfg, spec.managed_service_id, "cluster")
                    labels = self._adoption_labels(cluster, labels, cfg)
                    self._patch_cluster(name, cluster, self._cluster_body(cfg, spec.size, labels))
            else:
                self._assert_adoptable(cluster, cfg, spec.managed_service_id, "cluster")
                labels = self._adoption_labels(cluster, labels, cfg)
                self._patch_cluster(name, cluster, self._cluster_body(cfg, spec.size, labels))
            self._reconcile_cluster_children(name, cfg)
            self._reconcile_schema_registries(location, cfg, spec.managed_service_id)
            self._reconcile_connect_clusters(
                location,
                name,
                cfg,
                labels,
                spec.managed_service_id,
            )
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Managed Kafka: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Managed Kafka cluster {cluster_id} and declared integrations reconciled",
            ready=True,
        )

    @driver_op(cloud="gcp", driver="event_stream_managed_kafka")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            location, cluster_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg, size=spec.size or "custom", update=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_managed_kafka_config"])
        name = self._cluster_name(location, cluster_id)
        try:
            cluster = self._kafka.get(name, params={"view": "FULL"})
            self._assert_managed(cluster, "cluster")
            labels = dict(cluster.get("labels") or {})
            desired = self._cluster_body(cfg, spec.size or "custom", labels, partial=True)
            self._patch_cluster(name, cluster, desired)
            self._reconcile_cluster_children(name, cfg)
            service_id = str(labels.get("astrolift-io-managed-service-id") or _label_value(cluster_id))
            self._reconcile_schema_registries(location, cfg, service_id)
            self._reconcile_connect_clusters(location, name, cfg, labels, service_id)
        except ManagedKafkaNotFound:
            return UpdateResult(False, spec.handle, "Managed Kafka cluster not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Managed Kafka: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Managed Kafka cluster {cluster_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="event_stream_managed_kafka",
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
            location, cluster_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        name = self._cluster_name(location, cluster_id)
        cluster = self._get(name, full=True)
        if cluster is None:
            return DeprovisionResult(True, spec.handle, f"Managed Kafka cluster {cluster_id} already gone")
        try:
            self._assert_managed(cluster, "cluster")
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["ownership_guard"], retryable=False)
        labels = dict(cluster.get("labels") or {})
        if labels.get("astrolift-io-adopted") == "true" and not cfg.get("delete_adopted"):
            return DeprovisionResult(
                False,
                spec.handle,
                "adopted Managed Kafka clusters require delete_adopted=true",
                ["adopted_resource_guard"],
                retryable=False,
            )
        if bool(cfg.get("deletion_protection", self._config.deletion_protection_default)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Managed Kafka deletion protection is enabled; use force_destroy to confirm teardown",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "Managed Kafka has no snapshot API; set delete_data=true to delete topics and schemas",
                ["kafka_data_requires_delete_data"],
                retryable=False,
            )
        service_id = str(labels.get("astrolift-io-managed-service-id") or _label_value(cluster_id))
        try:
            connect_clusters = self._connect_clusters_for_kafka(location, name)
            owned, external = self._partition_owned(connect_clusters, service_id)
            if external and not (force_destroy and cfg.get("delete_external_dependents")):
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "external Kafka Connect clusters still reference this cluster; force_destroy plus "
                    "delete_external_dependents=true is required",
                    ["external_dependents_present"],
                    retryable=False,
                )
            for connect in [*owned, *external]:
                self._delete_connect_cluster(str(connect["name"]))
            for registry in self._owned_schema_registries(location, service_id):
                self._kafka.delete(str(registry["name"]))
            self._wait_operation(self._kafka.delete(name))
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Managed Kafka resources", exc)
        return DeprovisionResult(True, spec.handle, f"Managed Kafka cluster {cluster_id} deletion completed")

    @driver_op(cloud="gcp", driver="event_stream_managed_kafka")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            location, cluster_id = _parse_handle(handle.handle)
            cluster = self._kafka.get(
                self._cluster_name(location, cluster_id),
                params={"view": "FULL"},
            )
        except ManagedKafkaNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Managed Kafka cluster does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Managed Kafka cluster: {exc}")
        provider_state = str(cluster.get("state") or "STATE_UNSPECIFIED")
        state = {
            "CREATING": "provisioning",
            "ACTIVE": "available",
            "UPDATING": "updating",
            "DELETING": "deprovisioning",
        }.get(provider_state, "error")
        if state != "available":
            return ServiceStatus(handle.handle, state, f"Managed Kafka reports {provider_state}")
        labels = dict(cluster.get("labels") or {})
        service_id = str(labels.get("astrolift-io-managed-service-id") or _label_value(cluster_id))
        try:
            connect_clusters = self._owned_connect_clusters(location, service_id)
            connector_counts = {"RUNNING": 0, "PAUSED": 0, "STOPPED": 0}
            for connect in connect_clusters:
                connect_state = str(connect.get("state") or "STATE_UNSPECIFIED")
                if connect_state == "CREATING":
                    return ServiceStatus(handle.handle, "provisioning", f"{connect.get('name')} is CREATING")
                if connect_state == "DELETING":
                    return ServiceStatus(handle.handle, "deprovisioning", f"{connect.get('name')} is DELETING")
                if connect_state != "ACTIVE":
                    return ServiceStatus(handle.handle, "error", f"{connect.get('name')} is {connect_state}")
                for connector in self._list_connectors(str(connect["name"])):
                    connector_state = str(connector.get("state") or "STATE_UNSPECIFIED")
                    if connector_state == "FAILED":
                        return ServiceStatus(handle.handle, "error", f"{connector.get('name')} is FAILED")
                    if connector_state in {"UNASSIGNED", "RESTARTING"}:
                        return ServiceStatus(
                            handle.handle,
                            "updating",
                            f"{connector.get('name')} is {connector_state}",
                        )
                    connector_counts[connector_state] = connector_counts.get(connector_state, 0) + 1
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Managed Kafka integrations: {exc}")
        detail = ", ".join(f"{count} {key.lower()}" for key, count in connector_counts.items() if count)
        return ServiceStatus(
            handle.handle,
            "available",
            f"Managed Kafka {cluster_id} is ACTIVE" + (f"; connectors: {detail}" if detail else ""),
        )

    @driver_op(cloud="gcp", driver="event_stream_managed_kafka")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        location, cluster_id = _parse_handle(handle.handle)
        cfg = dict(config or {})
        name = self._cluster_name(location, cluster_id)
        cluster = self._kafka.get(name, params={"view": "FULL"})
        self._assert_managed(cluster, "cluster")
        bootstrap = str(cluster.get("bootstrapAddress") or cfg.get("bootstrap_address") or "")
        if not bootstrap:
            raise ManagedKafkaError(
                "Managed Kafka did not return bootstrapAddress; set bootstrap_address only for an "
                "explicit provider-compatible override",
            )
        auth = "gcp_iam_mtls" if cfg.get("client_certificate_secret_ref") else "gcp_iam"
        env_vars = {
            "EVENT_STREAM_BROKERS": ValueRef(literal=bootstrap),
            "EVENT_STREAM_TLS": ValueRef(literal="true"),
            "EVENT_STREAM_AUTH_MECHANISM": ValueRef(literal=auth),
            "GCP_MANAGED_KAFKA_CLUSTER": ValueRef(literal=name),
            "GCP_MANAGED_KAFKA_BOOTSTRAP": ValueRef(literal=bootstrap),
            "GOOGLE_CLOUD_PROJECT": ValueRef(literal=self._config.project_id),
            "GOOGLE_CLOUD_REGION": ValueRef(literal=location),
        }
        # One statement per mTLS key rather than a loop over (config key, env
        # key) pairs: a key name assembled from a loop variable is invisible to
        # a reader and to every static consumer of this binding, including the
        # cross-provider envelope guardrail in
        # tests/test_binding_envelope_contract.py (#1400).
        if cfg.get("client_certificate_secret_ref"):
            env_vars["EVENT_STREAM_CLIENT_CERT"] = ValueRef(secret_ref=str(cfg["client_certificate_secret_ref"]))
        if cfg.get("client_key_secret_ref"):
            env_vars["EVENT_STREAM_CLIENT_KEY"] = ValueRef(secret_ref=str(cfg["client_key_secret_ref"]))
        if cfg.get("ca_certificate_secret_ref"):
            env_vars["EVENT_STREAM_CA_CERT"] = ValueRef(secret_ref=str(cfg["ca_certificate_secret_ref"]))
        project_resource = f"projects/{self._config.project_id}"
        grants = [Grant(project_resource, ["roles/managedkafka.client"])]
        registry_id = str(cfg.get("schema_registry_id") or "")
        if registry_id:
            if not _REGISTRY_RE.fullmatch(registry_id):
                raise ManagedKafkaError(f"invalid schema_registry_id {registry_id!r}")
            registry_name = self._registry_name(location, registry_id)
            self._kafka.get(registry_name)
            owner = self._registry_owner(registry_name)
            service_id = str((cluster.get("labels") or {}).get("astrolift-io-managed-service-id") or cluster_id)
            if owner != service_id:
                raise ManagedKafkaError("selected schema registry is not owned by this Managed Kafka declaration")
            registry_url = f"{self._config.api_endpoint.rstrip('/')}/{registry_name}"
            env_vars["SCHEMA_REGISTRY_URL"] = ValueRef(literal=registry_url)
            env_vars["GCP_MANAGED_KAFKA_SCHEMA_REGISTRY"] = ValueRef(literal=registry_name)
            registry_role = (
                "roles/managedkafka.schemaRegistryEditor"
                if cfg.get("schema_registry_access") == "write"
                else "roles/managedkafka.schemaRegistryViewer"
            )
            grants.append(Grant(project_resource, [registry_role]))
        if cfg.get("access_mode") == "manage":
            grants.append(Grant(project_resource, ["roles/managedkafka.admin"]))
        return Binding(
            env_vars=env_vars,
            iam_grants=grants,
            notes="Google Managed Kafka uses TLS plus Google IAM SASL/OAUTHBEARER; Kafka ACLs narrow data access.",
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise ManagedKafkaError(
            "Managed Kafka has no cluster snapshot API; use replication or an archival sink",
        )

    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise ManagedKafkaError("Managed Kafka clusters cannot be restored from a service snapshot")

    def restart_connector(
        self,
        handle: ServiceHandle,
        *,
        connect_cluster_id: str,
        connector_id: str,
    ) -> ServiceStatus:
        if not _ID_RE.fullmatch(connect_cluster_id):
            raise ManagedKafkaError(f"invalid Managed Kafka Connect cluster ID {connect_cluster_id!r}")
        if not _ID_RE.fullmatch(connector_id):
            raise ManagedKafkaError(f"invalid Managed Kafka connector ID {connector_id!r}")
        location, cluster_id = _parse_handle(handle.handle)
        cluster = self._kafka.get(self._cluster_name(location, cluster_id))
        self._assert_managed(cluster, "cluster")
        connect_name = self._connect_name(location, connect_cluster_id)
        connect = self._kafka.get(connect_name)
        self._assert_managed(connect, "Connect cluster")
        if connect.get("kafkaCluster") != self._cluster_name(location, cluster_id):
            raise ManagedKafkaError("Connect cluster is not attached to the selected Kafka cluster")
        name = f"{connect_name}/connectors/{connector_id}"
        self._kafka.action(name, "restart")
        return ServiceStatus(handle.handle, "updating", f"connector {connector_id} restart requested")

    @driver_op(cloud="gcp", driver="event_stream_managed_kafka", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        labels = {"type": "object", "additionalProperties": {"type": "string"}}
        raw = {
            "type": "object",
            "description": "Provider-native Managed Kafka v1 fields for forward-compatible use.",
            "additionalProperties": True,
        }
        capacity = {
            "vcpu_count": {"type": "integer", "minimum": 3},
            "memory_gib": {"type": "integer", "minimum": 3},
            "memory_bytes": {"type": "integer", "minimum": 3 * _GIB},
        }
        topic = {
            "type": "object",
            "required": ["id", "partition_count", "replication_factor"],
            "properties": {
                "id": {"type": "string", "pattern": _TOPIC_RE.pattern},
                "partition_count": {"type": "integer", "minimum": 1},
                "replication_factor": {"type": "integer", "minimum": 1},
                "configs": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "additionalProperties": False,
        }
        acl_entry = {
            "type": "object",
            "required": ["principal", "host", "operation", "permission_type"],
            "properties": {
                "principal": {"type": "string", "pattern": "^User:"},
                "host": {"const": "*"},
                "operation": {"type": "string", "enum": sorted(_ACL_OPERATIONS)},
                "permission_type": {"type": "string", "enum": ["ALLOW", "DENY"]},
            },
            "additionalProperties": False,
        }
        acl = {
            "type": "object",
            "required": ["id", "entries"],
            "properties": {
                "id": {"type": "string", "pattern": _ACL_ID_RE.pattern},
                "entries": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": acl_entry,
                },
            },
            "additionalProperties": False,
        }
        schema_reference = {
            "type": "object",
            "required": ["name", "subject", "version"],
            "properties": {
                "name": {"type": "string"},
                "subject": {"type": "string"},
                "version": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        }
        schema_version = {
            "type": "object",
            "required": ["schema"],
            "properties": {
                "schema": {"type": "string", "minLength": 1},
                "schema_type": {"type": "string", "enum": sorted(_SCHEMA_TYPES), "default": "AVRO"},
                "normalize": {"type": "boolean", "default": False},
                "version": {"type": "integer", "minimum": 1},
                "id": {"type": "integer", "minimum": 1},
                "references": {"type": "array", "items": schema_reference},
            },
            "additionalProperties": False,
        }
        schema_config = {
            "type": "object",
            "required": ["compatibility"],
            "properties": {
                "compatibility": {"type": "string", "enum": sorted(_COMPATIBILITY)},
                "normalize": {"type": "boolean"},
                "alias": {"type": "string"},
            },
            "additionalProperties": False,
        }
        subject = {
            "type": "object",
            "required": ["name", "versions"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "versions": {"type": "array", "minItems": 1, "items": schema_version},
                "config": schema_config,
                "mode": {"type": "string", "enum": sorted(_SCHEMA_MODES)},
            },
            "additionalProperties": False,
        }
        registry = {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "pattern": _REGISTRY_RE.pattern},
                "config": schema_config,
                "mode": {"type": "string", "enum": sorted(_SCHEMA_MODES), "default": "READWRITE"},
                "subjects": {"type": "array", "items": subject},
                "delete_subjects": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "allow_schema_data_delete": {"type": "boolean", "default": False},
                "permanent_schema_delete": {"type": "boolean", "default": False},
                "adopt_existing": {"type": "boolean", "default": False},
                "reassign_existing": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        }
        network_config = {
            "type": "object",
            "required": ["primary_subnet"],
            "properties": {
                "primary_subnet": {"type": "string"},
                "dns_domain_names": {"type": "array", "items": {"type": "string"}},
                "additional_subnets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "deprecated": True,
                },
            },
            "additionalProperties": False,
        }
        restart_policy = {
            "type": "object",
            "properties": {
                "task_retry_disabled": {"type": "boolean"},
                "minimum_backoff": {"type": "string"},
                "maximum_backoff": {"type": "string"},
            },
            "additionalProperties": False,
        }
        connector = {
            "type": "object",
            "required": ["id", "configs"],
            "properties": {
                "id": {"type": "string", "pattern": _ID_RE.pattern},
                "configs": {"type": "object", "additionalProperties": {"type": "string"}},
                "task_restart_policy": restart_policy,
                "desired_state": {
                    "type": "string",
                    "enum": ["RUNNING", "PAUSED", "STOPPED"],
                    "default": "RUNNING",
                },
                "raw_fields": raw,
            },
            "additionalProperties": False,
        }
        connect = {
            "type": "object",
            "required": ["id", "network_configs"],
            "properties": {
                "id": {"type": "string", "pattern": _ID_RE.pattern},
                "size": {"type": "string", "enum": ["small", "medium", "large", "xlarge", "custom"]},
                **capacity,
                "network_configs": {"type": "array", "minItems": 1, "maxItems": 10, "items": network_config},
                "secret_paths": {"type": "array", "maxItems": 32, "items": {"type": "string"}},
                "config": {"type": "object", "additionalProperties": {"type": "string"}},
                "connectors": {"type": "array", "items": connector},
                "delete_connectors": {
                    "type": "array",
                    "items": {"type": "string", "pattern": _ID_RE.pattern},
                },
                "allow_connector_delete": {"type": "boolean", "default": False},
                "labels": labels,
                "raw_fields": raw,
                "clear_fields": {"type": "array", "items": {"type": "string"}},
                "adopt_existing": {"type": "boolean", "default": False},
                "reassign_existing": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        }
        partition_offset = {
            "type": "object",
            "required": ["offset"],
            "properties": {
                "offset": {"type": "integer", "minimum": 0},
                "metadata": {"type": "string"},
            },
            "additionalProperties": False,
        }
        consumer_group = {
            "type": "object",
            "required": ["id", "topics"],
            "properties": {
                "id": {"type": "string"},
                "allow_offset_rewind": {"type": "boolean", "default": False},
                "topics": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["partitions"],
                        "properties": {
                            "partitions": {
                                "type": "object",
                                "additionalProperties": partition_offset,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "cluster_id": {"type": "string", "pattern": _ID_RE.pattern},
                "location": {"type": "string"},
                **capacity,
                "subnet_names": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "string"}},
                "kms_key": {"type": "string"},
                "rebalance_config": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["NO_REBALANCE", "AUTO_REBALANCE_ON_SCALE_UP"],
                        },
                    },
                    "additionalProperties": False,
                },
                "tls_config": {
                    "type": "object",
                    "properties": {
                        "ssl_principal_mapping_rules": {"type": "string"},
                        "trust_config": {
                            "type": "object",
                            "properties": {
                                "cas_configs": {
                                    "type": "array",
                                    "maxItems": 10,
                                    "items": {
                                        "type": "object",
                                        "required": ["ca_pool"],
                                        "properties": {"ca_pool": {"type": "string"}},
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                "update_options": {
                    "type": "object",
                    "properties": {"allow_broker_downscale_on_cluster_upscale": {"type": "boolean"}},
                    "additionalProperties": False,
                },
                "labels": labels,
                "raw_fields": raw,
                "clear_fields": {"type": "array", "items": {"type": "string"}},
                "adopt_existing": {"type": "boolean", "default": False},
                "reassign_existing": {"type": "boolean", "default": False},
                "delete_adopted": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
                "delete_external_dependents": {"type": "boolean", "default": False},
                "topics": {"type": "array", "items": topic},
                "delete_topics": {
                    "type": "array",
                    "items": {"type": "string", "pattern": _TOPIC_RE.pattern},
                },
                "allow_topic_data_delete": {"type": "boolean", "default": False},
                "acls": {"type": "array", "items": acl},
                "delete_acls": {
                    "type": "array",
                    "items": {"type": "string", "pattern": _ACL_ID_RE.pattern},
                },
                "allow_acl_delete": {"type": "boolean", "default": False},
                "consumer_group_offsets": {"type": "array", "items": consumer_group},
                "delete_consumer_groups": {"type": "array", "items": {"type": "string"}},
                "allow_consumer_group_delete": {"type": "boolean", "default": False},
                "schema_registries": {"type": "array", "items": registry},
                "allow_schema_data_delete": {"type": "boolean", "default": False},
                "connect_clusters": {"type": "array", "items": connect},
                "prune_connect_clusters": {"type": "boolean", "default": False},
                "access_mode": {"type": "string", "enum": ["read", "write", "read_write", "manage"]},
                "bootstrap_address": {"type": "string"},
                "schema_registry_id": {"type": "string"},
                "schema_registry_access": {"type": "string", "enum": ["read", "write"]},
                "client_certificate_secret_ref": {"type": "string"},
                "client_key_secret_ref": {"type": "string"},
                "ca_certificate_secret_ref": {"type": "string"},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="event_stream_managed_kafka", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EVENT_STREAM_BROKERS": "Portable Kafka bootstrap address",
                "EVENT_STREAM_TLS": "Always true for Google Managed Kafka",
                "EVENT_STREAM_AUTH_MECHANISM": "gcp_iam or gcp_iam_mtls",
                "EVENT_STREAM_CLIENT_CERT": "mTLS client certificate secret when configured",
                "EVENT_STREAM_CLIENT_KEY": "mTLS client private key secret when configured",
                "EVENT_STREAM_CA_CERT": "trusted CA certificate secret when configured",
                "GCP_MANAGED_KAFKA_CLUSTER": "Fully qualified Google Managed Kafka cluster name",
                "GCP_MANAGED_KAFKA_BOOTSTRAP": "Provider-specific bootstrap address",
                "GCP_MANAGED_KAFKA_SCHEMA_REGISTRY": "Provider schema-registry resource name when selected",
                "SCHEMA_REGISTRY_URL": "Confluent-compatible Google Managed Kafka Schema Registry URL",
                "GOOGLE_CLOUD_PROJECT": "Google Cloud project ID",
                "GOOGLE_CLOUD_REGION": "Google Cloud region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "acls",
            "bootstrap_address",
            "ca_certificate_secret_ref",
            "client_certificate_secret_ref",
            "client_key_secret_ref",
            "connect_clusters",
            "consumer_group_offsets",
            "delete_acls",
            "delete_connectors",
            "delete_consumer_groups",
            "delete_schema_subjects",
            "delete_topics",
            "memory_bytes",
            "memory_gib",
            "raw_fields",
            "rebalance_config",
            "schema_registries",
            "schema_registry_access",
            "schema_registry_id",
            "tls_config",
            "topics",
            "update_options",
            "vcpu_count",
        ]

    def _validate_config(
        self,
        cfg: dict[str, Any],
        *,
        size: str,
        update: bool = False,
    ) -> str:
        if not self._config.project_id:
            return "Managed Kafka requires a Google Cloud project ID"
        location = str(cfg.get("location") or self._config.location)
        if not location:
            return "Managed Kafka requires a location"
        if update and ("location" in cfg or "cluster_id" in cfg):
            return "Managed Kafka location and cluster_id are immutable; reprovision the cluster"
        if "cluster_id" in cfg and not _ID_RE.fullmatch(str(cfg["cluster_id"])):
            return "Managed Kafka cluster_id must be a valid RFC 1035 ID"
        subnets = list(cfg.get("subnet_names", self._config.subnet_names))
        if not update or "subnet_names" in cfg:
            if not 1 <= len(subnets) <= 10:
                return "Managed Kafka requires between 1 and 10 subnet_names"
            if len(subnets) != len(set(subnets)):
                return "Managed Kafka subnet_names must be unique"
        if not update or any(key in cfg for key in ("vcpu_count", "memory_gib", "memory_bytes")):
            capacity_error = _capacity_error(cfg, size)
            if capacity_error:
                return capacity_error
        raw_error = _raw_fields_error(cfg, protected=_CLUSTER_STRUCTURED_FIELDS)
        if raw_error:
            return raw_error
        tls = cfg.get("tls_config") or {}
        cas = (tls.get("trust_config") or {}).get("cas_configs") or []
        if len(cas) > 10:
            return "Managed Kafka TLS trust config supports at most 10 CA pools"
        topic_ids: set[str] = set()
        for topic in cfg.get("topics") or []:
            topic_id = str(topic.get("id") or "")
            if not _TOPIC_RE.fullmatch(topic_id):
                return f"invalid Managed Kafka topic ID {topic_id!r}"
            if topic_id in topic_ids:
                return f"duplicate Managed Kafka topic ID {topic_id!r}"
            topic_ids.add(topic_id)
            if int(topic.get("partition_count") or 0) < 1:
                return f"Managed Kafka topic {topic_id} requires partition_count >= 1"
            if int(topic.get("replication_factor") or 0) < 1:
                return f"Managed Kafka topic {topic_id} requires replication_factor >= 1"
        acl_ids: set[str] = set()
        for acl in cfg.get("acls") or []:
            acl_id = str(acl.get("id") or "")
            if not _ACL_ID_RE.fullmatch(acl_id):
                return f"invalid Managed Kafka ACL ID {acl_id!r}"
            if acl_id in acl_ids:
                return f"duplicate Managed Kafka ACL ID {acl_id!r}"
            acl_ids.add(acl_id)
            entries = list(acl.get("entries") or [])
            if not 1 <= len(entries) <= 100:
                return f"Managed Kafka ACL {acl_id} requires between 1 and 100 entries"
            for entry in entries:
                if entry.get("host") != "*":
                    return f"Managed Kafka ACL {acl_id} host must be '*'"
                if not str(entry.get("principal") or "").startswith("User:"):
                    return f"Managed Kafka ACL {acl_id} principal must start with 'User:'"
                if str(entry.get("operation") or "").upper() not in _ACL_OPERATIONS:
                    return f"Managed Kafka ACL {acl_id} has an invalid operation"
                if str(entry.get("permission_type") or "").upper() not in {"ALLOW", "DENY"}:
                    return f"Managed Kafka ACL {acl_id} permission_type must be ALLOW or DENY"
        for declaration in cfg.get("schema_registries") or []:
            registry_id = str(declaration.get("id") or "")
            if not _REGISTRY_RE.fullmatch(registry_id):
                return f"invalid Managed Kafka schema-registry ID {registry_id!r}"
            mode = str(declaration.get("mode") or "READWRITE")
            if mode not in _SCHEMA_MODES:
                return f"invalid schema-registry mode {mode!r}"
            config = declaration.get("config") or {}
            if config and str(config.get("compatibility") or "") not in _COMPATIBILITY:
                return f"invalid schema-registry compatibility {config.get('compatibility')!r}"
            for subject in declaration.get("subjects") or []:
                if not str(subject.get("name") or ""):
                    return f"schema registry {registry_id} contains a subject without a name"
                if subject.get("name") == _OWNERSHIP_SUBJECT:
                    return f"schema subject {_OWNERSHIP_SUBJECT!r} is reserved by Astrolift"
                subject_mode = str(subject.get("mode") or "READWRITE")
                if subject_mode not in _SCHEMA_MODES:
                    return f"invalid schema subject mode {subject_mode!r}"
                subject_config = subject.get("config") or {}
                if subject_config and str(subject_config.get("compatibility") or "") not in _COMPATIBILITY:
                    return f"invalid schema subject compatibility {subject_config.get('compatibility')!r}"
                versions = list(subject.get("versions") or [])
                if not versions:
                    return f"schema subject {subject.get('name')} requires at least one version"
                for version in versions:
                    if not str(version.get("schema") or ""):
                        return f"schema subject {subject.get('name')} contains an empty schema"
                    if str(version.get("schema_type") or "AVRO") not in _SCHEMA_TYPES:
                        return f"schema subject {subject.get('name')} has an invalid schema_type"
            if declaration.get("delete_subjects") and not (
                declaration.get("allow_schema_data_delete") or cfg.get("allow_schema_data_delete")
            ):
                return f"schema registry {registry_id} delete_subjects requires allow_schema_data_delete=true"
            if any(not str(subject_name) for subject_name in declaration.get("delete_subjects") or []):
                return f"schema registry {registry_id} delete_subjects cannot contain an empty subject name"
        connect_ids: set[str] = set()
        for connect in cfg.get("connect_clusters") or []:
            connect_id = str(connect.get("id") or "")
            if not _ID_RE.fullmatch(connect_id):
                return f"invalid Managed Kafka Connect cluster ID {connect_id!r}"
            if connect_id in connect_ids:
                return f"duplicate Managed Kafka Connect cluster ID {connect_id!r}"
            connect_ids.add(connect_id)
            connect_capacity_error = _capacity_error(connect, str(connect.get("size") or size))
            if connect_capacity_error:
                return f"Connect cluster {connect_id}: {connect_capacity_error}"
            networks = list(connect.get("network_configs") or [])
            if not networks:
                return f"Connect cluster {connect_id} requires network_configs"
            if len(networks) > 10:
                return f"Connect cluster {connect_id} supports at most 10 network configs"
            if len(connect.get("secret_paths") or []) > 32:
                return f"Connect cluster {connect_id} supports at most 32 secret versions"
            for secret_path in connect.get("secret_paths") or []:
                if not re.search(r"/versions/[0-9]+$", str(secret_path)):
                    return f"Connect cluster {connect_id} secret_paths require exact numeric versions"
            raw_error = _raw_fields_error(connect, protected=_CONNECT_STRUCTURED_FIELDS)
            if raw_error:
                return f"Connect cluster {connect_id}: {raw_error}"
            connector_ids: set[str] = set()
            for connector in connect.get("connectors") or []:
                connector_id = str(connector.get("id") or "")
                if not _ID_RE.fullmatch(connector_id):
                    return f"invalid Managed Kafka connector ID {connector_id!r}"
                if connector_id in connector_ids:
                    return f"duplicate Managed Kafka connector ID {connector_id!r}"
                connector_ids.add(connector_id)
                raw_error = _raw_fields_error(connector, protected=_CONNECTOR_STRUCTURED_FIELDS)
                if raw_error:
                    return f"Managed Kafka connector {connector_id}: {raw_error}"
                if not (connector.get("configs") or {}).get("connector.class"):
                    return f"Managed Kafka connector {connector_id} requires configs.connector.class"
                desired_state = str(connector.get("desired_state") or "RUNNING")
                if desired_state not in {"RUNNING", "PAUSED", "STOPPED"}:
                    return f"Managed Kafka connector {connector_id} has invalid desired_state"
            for connector_id in connect.get("delete_connectors") or []:
                if not _ID_RE.fullmatch(str(connector_id)):
                    return f"invalid Managed Kafka connector ID {connector_id!r} in delete_connectors"
        for topic_id in cfg.get("delete_topics") or []:
            if not _TOPIC_RE.fullmatch(str(topic_id)):
                return f"invalid Managed Kafka topic ID {topic_id!r} in delete_topics"
        for acl_id in cfg.get("delete_acls") or []:
            if not _ACL_ID_RE.fullmatch(str(acl_id)):
                return f"invalid Managed Kafka ACL ID {acl_id!r} in delete_acls"
        if cfg.get("delete_topics") and not cfg.get("allow_topic_data_delete"):
            return "delete_topics requires allow_topic_data_delete=true"
        if cfg.get("delete_acls") and not cfg.get("allow_acl_delete"):
            return "delete_acls requires allow_acl_delete=true"
        if cfg.get("delete_consumer_groups") and not cfg.get("allow_consumer_group_delete"):
            return "delete_consumer_groups requires allow_consumer_group_delete=true"
        return ""

    def _reconcile_cluster_children(self, cluster_name: str, cfg: dict[str, Any]) -> None:
        if "topics" in cfg:
            for declaration in cfg.get("topics") or []:
                topic_id = str(declaration["id"])
                name = f"{cluster_name}/topics/{topic_id}"
                body: dict[str, Any] = {
                    "partitionCount": int(declaration["partition_count"]),
                    "replicationFactor": int(declaration["replication_factor"]),
                    "configs": {str(key): str(value) for key, value in (declaration.get("configs") or {}).items()},
                }
                current = self._get(name)
                if current is None:
                    try:
                        self._kafka.create(cluster_name, "topics", topic_id, "topicId", body)
                        continue
                    except ManagedKafkaConflict:
                        current = self._kafka.get(name)
                if int(current.get("replicationFactor") or 0) != body["replicationFactor"]:
                    raise ManagedKafkaError(
                        f"topic {topic_id} replication_factor is immutable; create a replacement topic",
                    )
                if int(current.get("partitionCount") or 0) > body["partitionCount"]:
                    raise ManagedKafkaError(f"topic {topic_id} partition_count cannot be decreased")
                self._patch_direct(name, current, body, immutable={"replicationFactor"})
        for topic_id in cfg.get("delete_topics") or []:
            name = f"{cluster_name}/topics/{topic_id}"
            if self._get(name) is not None:
                self._kafka.delete(name)

        if "acls" in cfg:
            for declaration in cfg.get("acls") or []:
                acl_id = str(declaration["id"])
                name = f"{cluster_name}/acls/{acl_id}"
                body = {
                    "aclEntries": [
                        {
                            "principal": str(entry["principal"]),
                            "host": "*",
                            "operation": str(entry["operation"]).upper(),
                            "permissionType": str(entry["permission_type"]).upper(),
                        }
                        for entry in declaration.get("entries") or []
                    ],
                }
                current = self._get(name)
                if current is None:
                    try:
                        self._kafka.create(cluster_name, "acls", acl_id, "aclId", body)
                        continue
                    except ManagedKafkaConflict:
                        current = self._kafka.get(name)
                if current.get("etag"):
                    body["etag"] = current["etag"]
                self._patch_direct(name, current, body)
        for acl_id in cfg.get("delete_acls") or []:
            name = f"{cluster_name}/acls/{acl_id}"
            if self._get(name) is not None:
                self._kafka.delete(name)

        for declaration in cfg.get("consumer_group_offsets") or []:
            group_id = str(declaration["id"])
            name = f"{cluster_name}/consumerGroups/{quote(group_id, safe='')}"
            current = self._kafka.get(name)
            desired_topics = _consumer_topics(cluster_name, declaration.get("topics") or {})
            if not declaration.get("allow_offset_rewind"):
                _assert_no_offset_rewind(current.get("topics") or {}, desired_topics)
            self._kafka.patch(name, {"topics": desired_topics}, update_mask=["topics"])
        for group_id in cfg.get("delete_consumer_groups") or []:
            name = f"{cluster_name}/consumerGroups/{quote(str(group_id), safe='')}"
            if self._get(name) is not None:
                self._kafka.delete(name)

    def _reconcile_schema_registries(
        self,
        location: str,
        cfg: dict[str, Any],
        service_id: str,
    ) -> None:
        if "schema_registries" not in cfg:
            return
        parent = self._parent(location)
        for declaration in cfg.get("schema_registries") or []:
            registry_id = str(declaration["id"])
            name = self._registry_name(location, registry_id)
            current = self._get(name)
            if current is None:
                try:
                    self._kafka.create_schema_registry(parent, registry_id)
                except ManagedKafkaConflict:
                    current = self._kafka.get(name)
            owner = self._registry_owner(name)
            if owner and owner != service_id and not declaration.get("reassign_existing"):
                raise ManagedKafkaError(
                    f"schema registry {registry_id} belongs to another managed service; "
                    "set reassign_existing=true to transfer ownership",
                )
            if not owner and current is not None and not declaration.get("adopt_existing"):
                raise ManagedKafkaError(
                    f"schema registry {registry_id} is not Astrolift-owned; set adopt_existing=true",
                )
            if owner != service_id:
                self._ensure_registry_marker(name, service_id)
            if declaration.get("config"):
                self._kafka.update_schema_setting(name, "config", "", _camelize(declaration["config"]))
            if declaration.get("mode"):
                self._kafka.update_schema_setting(name, "mode", "", {"mode": declaration["mode"]})
            for subject in declaration.get("subjects") or []:
                subject_name = str(subject["name"])
                for version in subject.get("versions") or []:
                    body = _schema_version_body(version)
                    if self._kafka.lookup_schema(name, subject_name, body) is None:
                        self._kafka.create_schema_version(name, subject_name, body)
                if subject.get("config"):
                    self._kafka.update_schema_setting(
                        name,
                        "config",
                        subject_name,
                        _camelize(subject["config"]),
                    )
                if subject.get("mode"):
                    self._kafka.update_schema_setting(
                        name,
                        "mode",
                        subject_name,
                        {"mode": subject["mode"]},
                    )
            for subject_name in declaration.get("delete_subjects") or []:
                self._kafka.delete_schema_subject(
                    name,
                    str(subject_name),
                    permanent=bool(declaration.get("permanent_schema_delete")),
                )

    def _reconcile_connect_clusters(
        self,
        location: str,
        kafka_cluster: str,
        cfg: dict[str, Any],
        parent_labels: dict[str, str],
        service_id: str,
    ) -> None:
        if "connect_clusters" not in cfg:
            return
        parent = self._parent(location)
        desired_ids: set[str] = set()
        for declaration in cfg.get("connect_clusters") or []:
            connect_id = str(declaration["id"])
            desired_ids.add(connect_id)
            name = self._connect_name(location, connect_id)
            labels = dict(parent_labels)
            labels["astrolift-io-resource-parent"] = _label_value(kafka_cluster.rsplit("/", 1)[-1])
            body = self._connect_body(declaration, kafka_cluster, labels)
            current = self._get(name)
            if current is None:
                try:
                    self._wait_operation(
                        self._kafka.create(
                            parent,
                            "connectClusters",
                            connect_id,
                            "connectClusterId",
                            body,
                        ),
                    )
                    current = self._kafka.get(name)
                except ManagedKafkaConflict:
                    current = self._kafka.get(name)
                    self._assert_adoptable(current, declaration, service_id, "Connect cluster")
            else:
                self._assert_adoptable(current, declaration, service_id, "Connect cluster")
            if str(current.get("kafkaCluster") or "") != kafka_cluster:
                raise ManagedKafkaError(
                    f"Connect cluster {connect_id} kafka_cluster is immutable; create a replacement",
                )
            labels = self._adoption_labels(current, labels, declaration)
            body["labels"] = labels
            self._patch_lro(name, current, body)
            self._reconcile_connectors(name, declaration)
        if cfg.get("prune_connect_clusters"):
            for connect in self._owned_connect_clusters(location, service_id):
                connect_id = str(connect.get("name") or "").rsplit("/", 1)[-1]
                if connect_id not in desired_ids:
                    self._delete_connect_cluster(str(connect["name"]))

    def _reconcile_connectors(self, connect_name: str, declaration: dict[str, Any]) -> None:
        for connector in declaration.get("connectors") or []:
            connector_id = str(connector["id"])
            name = f"{connect_name}/connectors/{connector_id}"
            body: dict[str, Any] = {
                "configs": {str(key): str(value) for key, value in (connector.get("configs") or {}).items()},
            }
            if connector.get("task_restart_policy"):
                body["taskRestartPolicy"] = _camelize(connector["task_restart_policy"])
            body.update(dict(connector.get("raw_fields") or {}))
            current = self._get(name)
            if current is None:
                try:
                    self._kafka.create(connect_name, "connectors", connector_id, "connectorId", body)
                    current = self._kafka.get(name)
                except ManagedKafkaConflict:
                    current = self._kafka.get(name)
            self._patch_direct(name, current, body)
            desired_state = str(connector.get("desired_state") or "RUNNING")
            current_state = str(current.get("state") or "")
            if desired_state == "PAUSED" and current_state == "STOPPED":
                self._kafka.action(name, "resume")
                self._kafka.action(name, "pause")
                continue
            action = {
                ("RUNNING", "PAUSED"): "resume",
                ("RUNNING", "STOPPED"): "resume",
                ("PAUSED", "RUNNING"): "pause",
                ("STOPPED", "RUNNING"): "stop",
                ("STOPPED", "PAUSED"): "stop",
            }.get((desired_state, current_state))
            if action:
                self._kafka.action(name, action)
        if declaration.get("delete_connectors") and not declaration.get("allow_connector_delete"):
            raise ManagedKafkaError("delete_connectors requires allow_connector_delete=true")
        for connector_id in declaration.get("delete_connectors") or []:
            name = f"{connect_name}/connectors/{connector_id}"
            if self._get(name) is not None:
                self._kafka.delete(name)

    def _cluster_body(
        self,
        cfg: dict[str, Any],
        size: str,
        labels: dict[str, str],
        *,
        partial: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        capacity = _capacity_body(cfg, size, required=not partial)
        if capacity:
            body["capacityConfig"] = capacity
        subnets = list(cfg.get("subnet_names") or (self._config.subnet_names if not partial else ()))
        if subnets or "kms_key" in cfg:
            gcp_config: dict[str, Any] = {}
            if subnets:
                gcp_config["accessConfig"] = {
                    "networkConfigs": [{"subnet": str(subnet)} for subnet in subnets],
                }
            if cfg.get("kms_key"):
                gcp_config["kmsKey"] = str(cfg["kms_key"])
            body["gcpConfig"] = gcp_config
        for source, target in (
            ("rebalance_config", "rebalanceConfig"),
            ("tls_config", "tlsConfig"),
            ("update_options", "updateOptions"),
        ):
            if source in cfg:
                body[target] = _camelize(cfg[source])
        body.update(dict(cfg.get("raw_fields") or {}))
        for field in cfg.get("clear_fields") or []:
            body[str(field)] = None
        if not partial or "labels" in cfg:
            desired_labels = dict(labels)
            desired_labels.update(_normalized_labels(cfg.get("labels") or {}))
            body["labels"] = desired_labels
        return body

    def _connect_body(
        self,
        declaration: dict[str, Any],
        kafka_cluster: str,
        labels: dict[str, str],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "kafkaCluster": kafka_cluster,
            "capacityConfig": _capacity_body(
                declaration,
                str(declaration.get("size") or "small"),
                required=True,
            ),
            "gcpConfig": {
                "accessConfig": {
                    "networkConfigs": [_camelize(item) for item in declaration.get("network_configs") or []],
                },
                "secretPaths": [str(value) for value in declaration.get("secret_paths") or []],
            },
            "labels": {
                **labels,
                **_normalized_labels(declaration.get("labels") or {}),
            },
        }
        if declaration.get("config"):
            body["config"] = {str(key): str(value) for key, value in declaration["config"].items()}
        body.update(dict(declaration.get("raw_fields") or {}))
        for field in declaration.get("clear_fields") or []:
            body[str(field)] = None
        body["labels"] = {**labels, **_normalized_labels(declaration.get("labels") or {})}
        return body

    def _patch_cluster(
        self,
        name: str,
        current: dict[str, Any],
        desired: dict[str, Any],
    ) -> None:
        current_gcp = dict(current.get("gcpConfig") or {})
        desired_gcp = dict(desired.get("gcpConfig") or {})
        current_kms = str(current_gcp.get("kmsKey") or "")
        desired_kms = str(desired_gcp.get("kmsKey") or "")
        if desired_kms and current_kms and desired_kms != current_kms:
            raise ManagedKafkaError("Managed Kafka kms_key is immutable; reprovision the cluster")
        if current_kms and desired_gcp and not desired_kms:
            desired_gcp["kmsKey"] = current_kms
            desired["gcpConfig"] = desired_gcp
        self._patch_lro(name, current, desired)

    def _patch_lro(self, name: str, current: dict[str, Any], desired: dict[str, Any]) -> None:
        changed = _changed_fields(current, desired)
        if changed:
            self._wait_operation(self._kafka.patch(name, changed, update_mask=list(changed)))

    def _patch_direct(
        self,
        name: str,
        current: dict[str, Any],
        desired: dict[str, Any],
        *,
        immutable: set[str] | None = None,
    ) -> None:
        changed = _changed_fields(current, desired, immutable=immutable)
        if changed:
            self._kafka.patch(name, changed, update_mask=[key for key in changed if key != "etag"])

    def _delete_connect_cluster(self, name: str) -> None:
        for connector in self._list_connectors(name):
            self._kafka.delete(str(connector["name"]))
        self._wait_operation(self._kafka.delete(name))

    def _list_connectors(self, connect_name: str) -> list[dict[str, Any]]:
        return self._kafka.list_resources(connect_name, "connectors", "connectors")

    def _connect_clusters_for_kafka(self, location: str, kafka_cluster: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self._kafka.list_resources(
                self._parent(location),
                "connectClusters",
                "connectClusters",
            )
            if item.get("kafkaCluster") == kafka_cluster
        ]

    def _owned_connect_clusters(self, location: str, service_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self._kafka.list_resources(
                self._parent(location),
                "connectClusters",
                "connectClusters",
            )
            if _is_owned(item, service_id)
        ]

    @staticmethod
    def _partition_owned(
        resources: list[dict[str, Any]],
        service_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        owned = [item for item in resources if _is_owned(item, service_id)]
        external = [item for item in resources if item not in owned]
        return owned, external

    def _owned_schema_registries(self, location: str, service_id: str) -> list[dict[str, Any]]:
        rows = self._kafka.list_resources(
            self._parent(location),
            "schemaRegistries",
            "schemaRegistries",
        )
        return [item for item in rows if self._registry_owner(str(item["name"])) == service_id]

    def _registry_owner(self, registry_name: str) -> str:
        try:
            version = self._kafka.get_schema_version(registry_name, _OWNERSHIP_SUBJECT)
        except ManagedKafkaNotFound:
            return ""
        try:
            payload = json.loads(str(version.get("schema") or "{}"))
        except json.JSONDecodeError:
            return ""
        return str(payload.get("x-astrolift-managed-service-id") or "")

    def _ensure_registry_marker(self, registry_name: str, service_id: str) -> None:
        schema = json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "Astrolift ownership marker",
                "type": "object",
                "x-astrolift-managed-service-id": service_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        body = {"schema": schema, "schemaType": "JSON", "normalize": True}
        if self._kafka.lookup_schema(registry_name, _OWNERSHIP_SUBJECT, body) is None:
            self._kafka.create_schema_version(registry_name, _OWNERSHIP_SUBJECT, body)

    def _wait_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        if not operation:
            return {}
        current = operation
        name = str(operation.get("name") or "")
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        while not current.get("done"):
            if not name:
                raise ManagedKafkaError("Managed Kafka operation response has no name")
            if self._monotonic() > deadline:
                raise ManagedKafkaError(f"Managed Kafka operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._kafka.get_operation(name)
        if current.get("error"):
            error = current["error"]
            raise ManagedKafkaError(
                f"Managed Kafka operation {name} failed: {error.get('message') or error}",
            )
        response = current.get("response") or {}
        return dict(response) if isinstance(response, dict) else {}

    def _get(self, name: str, *, full: bool = False) -> dict[str, Any] | None:
        try:
            return self._kafka.get(name, params={"view": "FULL"} if full else None)
        except ManagedKafkaNotFound:
            return None

    def _assert_adoptable(
        self,
        current: dict[str, Any],
        cfg: dict[str, Any],
        service_id: str,
        resource: str,
    ) -> None:
        labels = dict(current.get("labels") or {})
        if labels.get("astrolift-io-managed-by") == "platform":
            current_service = str(labels.get("astrolift-io-managed-service-id") or "")
            if current_service and service_id and current_service != service_id and not cfg.get("reassign_existing"):
                raise ManagedKafkaError(
                    f"Managed Kafka {resource} belongs to another managed service; "
                    "set reassign_existing=true to transfer ownership",
                )
            return
        if not cfg.get("adopt_existing"):
            raise ManagedKafkaError(
                f"existing Managed Kafka {resource} is not Astrolift-owned; set adopt_existing=true",
            )

    @staticmethod
    def _assert_managed(current: dict[str, Any], resource: str) -> None:
        if (current.get("labels") or {}).get("astrolift-io-managed-by") != "platform":
            raise ManagedKafkaError(f"Managed Kafka {resource} is not owned by Astrolift")

    @staticmethod
    def _adoption_labels(
        current: dict[str, Any],
        desired: dict[str, str],
        cfg: dict[str, Any],
    ) -> dict[str, str]:
        labels = dict(desired)
        if cfg.get("adopt_existing") and (current.get("labels") or {}).get("astrolift-io-managed-by") != "platform":
            labels["astrolift-io-adopted"] = "true"
        return labels

    def _cluster_id(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        if cfg.get("cluster_id"):
            return str(cfg["cluster_id"])
        suffix = f"{spec.app_slug}-{spec.environment_name}"
        return _dns_id(f"{self._config.cluster_id_prefix}-{suffix}")

    def _parent(self, location: str) -> str:
        return f"projects/{self._config.project_id}/locations/{location}"

    def _cluster_name(self, location: str, cluster_id: str) -> str:
        return f"{self._parent(location)}/clusters/{cluster_id}"

    def _connect_name(self, location: str, connect_id: str) -> str:
        return f"{self._parent(location)}/connectClusters/{connect_id}"

    def _registry_name(self, location: str, registry_id: str) -> str:
        return f"{self._parent(location)}/schemaRegistries/{registry_id}"

    def _labels(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, str]:
        labels = {
            "astrolift-io-managed-by": "platform",
            "astrolift-io-organization": _label_value(spec.organization_slug),
            "astrolift-io-app": _label_value(spec.app_slug),
            "astrolift-io-environment": _label_value(spec.environment_name),
        }
        if spec.binding_id:
            labels["astrolift-io-binding"] = _label_value(spec.binding_id)
        if spec.managed_service_id:
            labels["astrolift-io-managed-service-id"] = _label_value(spec.managed_service_id)
        labels.update(_normalized_labels(cfg.get("labels") or {}))
        return labels


def _handle(location: str, cluster_id: str) -> str:
    return f"{KIND}/{location}/{cluster_id}"


def _parse_handle(handle: str) -> tuple[str, str]:
    parts = handle.split("/")
    if len(parts) != 3 or parts[0] != KIND or not parts[1] or not _ID_RE.fullmatch(parts[2]):
        raise ValueError("invalid Managed Kafka handle; expected event_stream/<location>/<cluster-id>")
    return parts[1], parts[2]


def _dns_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    normalized = re.sub(r"-+", "-", normalized)
    if not normalized or not normalized[0].isalpha():
        normalized = f"k-{normalized}"
    return normalized[:63].rstrip("-")


def _label_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    return (normalized or "label")[:63]


def _label_value(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")[:63]


def _normalized_labels(labels: dict[str, Any]) -> dict[str, str]:
    return {_label_key(str(key)): _label_value(str(value)) for key, value in labels.items()}


def _is_owned(resource: dict[str, Any], service_id: str) -> bool:
    labels = dict(resource.get("labels") or {})
    return labels.get("astrolift-io-managed-by") == "platform" and (
        not service_id or labels.get("astrolift-io-managed-service-id") == service_id
    )


def _capacity_values(cfg: dict[str, Any], size: str) -> tuple[int, int]:
    default_vcpu, default_gib = _SIZE_CAPACITY.get(size, (0, 0))
    vcpu = int(cfg.get("vcpu_count") or default_vcpu)
    if cfg.get("memory_bytes") is not None:
        memory_bytes = int(cfg["memory_bytes"])
    else:
        memory_bytes = int(cfg.get("memory_gib") or default_gib) * _GIB
    return vcpu, memory_bytes


def _capacity_error(cfg: dict[str, Any], size: str) -> str:
    if "memory_bytes" in cfg and "memory_gib" in cfg:
        return "Managed Kafka memory_bytes and memory_gib are mutually exclusive"
    vcpu, memory_bytes = _capacity_values(cfg, size)
    if not vcpu and not memory_bytes:
        return "Managed Kafka custom capacity requires vcpu_count and memory_gib or memory_bytes"
    if vcpu < 3:
        return "Managed Kafka vcpu_count must be at least 3"
    if memory_bytes < 3 * _GIB:
        return "Managed Kafka memory must be at least 3 GiB"
    gib_per_vcpu = memory_bytes / _GIB / vcpu
    if not 1 <= gib_per_vcpu <= 8:
        return "Managed Kafka memory ratio must be between 1 and 8 GiB per vCPU"
    return ""


def _capacity_body(cfg: dict[str, Any], size: str, *, required: bool) -> dict[str, str]:
    if not required and not any(key in cfg for key in ("vcpu_count", "memory_gib", "memory_bytes")):
        return {}
    vcpu, memory_bytes = _capacity_values(cfg, size)
    return {"vcpuCount": str(vcpu), "memoryBytes": str(memory_bytes)}


def _raw_fields_error(cfg: dict[str, Any], *, protected: set[str] | None = None) -> str:
    protected_fields = _PROTECTED_RAW_FIELDS | (protected or set())
    forbidden = sorted(set(cfg.get("raw_fields") or {}) & protected_fields)
    if forbidden:
        return f"raw_fields cannot set protected fields: {', '.join(forbidden)}"
    forbidden_clear = sorted({str(item) for item in cfg.get("clear_fields") or []} & protected_fields)
    if forbidden_clear:
        return f"clear_fields cannot clear protected fields: {', '.join(forbidden_clear)}"
    return ""


def _changed_fields(
    current: dict[str, Any],
    desired: dict[str, Any],
    *,
    immutable: set[str] | None = None,
) -> dict[str, Any]:
    ignored = _OUTPUT_ONLY | (immutable or set())
    return {key: value for key, value in desired.items() if key not in ignored and current.get(key) != value}


def _camelize(value: Any) -> Any:
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    if isinstance(value, dict):
        return {_camel_key(str(key)): _camelize(item) for key, item in value.items()}
    return value


def _camel_key(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _schema_version_body(version: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": str(version["schema"]),
        "schemaType": str(version.get("schema_type") or "AVRO"),
        "normalize": bool(version.get("normalize", False)),
    }
    if version.get("version") is not None:
        body["version"] = int(version["version"])
    if version.get("id") is not None:
        body["id"] = int(version["id"])
    if version.get("references"):
        body["references"] = [_camelize(item) for item in version["references"]]
    return body


def _consumer_topics(cluster_name: str, topics: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for topic, metadata in topics.items():
        name = str(topic) if str(topic).startswith("projects/") else f"{cluster_name}/topics/{topic}"
        partitions = {
            str(partition): {
                "offset": str(values["offset"]),
                **({"metadata": str(values["metadata"])} if values.get("metadata") is not None else {}),
            }
            for partition, values in (metadata.get("partitions") or {}).items()
        }
        result[name] = {"partitions": partitions}
    return result


def _assert_no_offset_rewind(current: dict[str, Any], desired: dict[str, Any]) -> None:
    for topic, desired_metadata in desired.items():
        current_partitions = (current.get(topic) or {}).get("partitions") or {}
        for partition, desired_partition in (desired_metadata.get("partitions") or {}).items():
            if partition not in current_partitions:
                continue
            current_offset = int(current_partitions[partition].get("offset") or 0)
            desired_offset = int(desired_partition.get("offset") or 0)
            if desired_offset < current_offset:
                raise ManagedKafkaError(
                    f"consumer-group offset rewind for {topic} partition {partition} requires allow_offset_rewind=true",
                )


def _deprovision_error(handle: str, action: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(False, handle, f"{action}: {exc}", [str(exc)])
