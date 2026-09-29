"""Google Cloud Private Service Connect consumer endpoint lifecycle."""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from _sdk._telemetry import driver_op
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
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL, readable_keys

KIND = "private_endpoint"
VARIANT = "private_service_connect"
_API_ROOT = "https://compute.googleapis.com/compute/v1"
_ID_PATTERN = re.compile(r"[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?")
_GOOGLE_API_ID_PATTERN = re.compile(r"[a-z][a-z0-9]{0,19}")
_UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: Every spelling of the managed-service id is reserved, not just the one this
#: driver writes, so tenant labels cannot plant one (#2086).
_RESERVED_LABELS = {
    "astrolift-managed-by",
    "astrolift-private-endpoint",
    "astrolift-adopted",
    *readable_keys("gcp"),
}
_ADDRESS_OWNED_FIELDS = {
    "name",
    "description",
    "address",
    "addressType",
    "ipVersion",
    "network",
    "subnetwork",
    "purpose",
    "labels",
}
_FORWARDING_OWNED_FIELDS = {
    "name",
    "description",
    "IPAddress",
    "network",
    "target",
    "loadBalancingScheme",
    "allowPscGlobalAccess",
    "serviceDirectoryRegistrations",
    "noAutomateDnsZone",
    "labels",
}
_OUTPUT_FIELDS = {
    "creationTimestamp",
    "fingerprint",
    "id",
    "kind",
    "labelFingerprint",
    "pscConnectionId",
    "pscConnectionStatus",
    "region",
    "selfLink",
    "selfLinkWithId",
    "serviceName",
    "status",
    "users",
}


class PrivateServiceConnectError(RuntimeError):
    pass


class PrivateServiceConnectNotFound(PrivateServiceConnectError):
    pass


class PrivateServiceConnectAlreadyExists(PrivateServiceConnectError):
    pass


@dataclass(frozen=True)
class PrivateServiceConnectConfig:
    project_id: str
    region: str
    network: str = ""
    subnetwork: str = ""
    name_prefix: str = "astrolift"
    labels: dict[str, str] = field(default_factory=dict)
    deletion_protection_default: bool = True
    api_endpoint: str = _API_ROOT
    operation_timeout_seconds: float = 900.0
    poll_interval_seconds: float = 2.0


class ComputePscRestClient:
    """Authenticated request-shaped adapter for regional and global PSC resources."""

    def __init__(
        self,
        *,
        project_id: str,
        endpoint: str = _API_ROOT,
        session: Any | None = None,
    ) -> None:
        self._project_id = project_id
        self._endpoint = endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=("https://www.googleapis.com/auth/cloud-platform",),
            )
            session = AuthorizedSession(credentials)
        self._session = session

    def get_address(self, location: str, name: str) -> dict[str, Any]:
        return self._request("GET", f"{self._scope(location)}/addresses/{quote(name, safe='')}")

    def insert_address(
        self,
        location: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request("POST", f"{self._scope(location)}/addresses", json=body)

    def delete_address(self, location: str, name: str) -> dict[str, Any]:
        return self._request("DELETE", f"{self._scope(location)}/addresses/{quote(name, safe='')}")

    def set_address_labels(
        self,
        location: str,
        name: str,
        *,
        labels: dict[str, str],
        fingerprint: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self._scope(location)}/addresses/{quote(name, safe='')}/setLabels",
            json={"labels": labels, "labelFingerprint": fingerprint},
        )

    def get_forwarding_rule(self, location: str, name: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{self._scope(location)}/forwardingRules/{quote(name, safe='')}",
        )

    def insert_forwarding_rule(
        self,
        location: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request("POST", f"{self._scope(location)}/forwardingRules", json=body)

    def patch_forwarding_rule(
        self,
        location: str,
        name: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"{self._scope(location)}/forwardingRules/{quote(name, safe='')}",
            json=body,
        )

    def delete_forwarding_rule(self, location: str, name: str) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"{self._scope(location)}/forwardingRules/{quote(name, safe='')}",
        )

    def set_forwarding_rule_labels(
        self,
        location: str,
        name: str,
        *,
        labels: dict[str, str],
        fingerprint: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self._scope(location)}/forwardingRules/{quote(name, safe='')}/setLabels",
            json={"labels": labels, "labelFingerprint": fingerprint},
        )

    def get_operation(self, location: str, name: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{self._scope(location)}/operations/{quote(name.rsplit('/', 1)[-1], safe='')}",
        )

    @staticmethod
    def _scope(location: str) -> str:
        return "global" if location == "global" else f"regions/{quote(location, safe='')}"

    def _request(
        self,
        method: str,
        resource: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            f"{self._endpoint}/projects/{quote(self._project_id, safe='')}/{resource.lstrip('/')}",
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise PrivateServiceConnectNotFound(resource)
        if response.status_code == 409:
            raise PrivateServiceConnectAlreadyExists(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with contextlib.suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise PrivateServiceConnectError(f"Compute HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class PrivateServiceConnectDriver(ManagedServiceDriver):
    """Own consumer endpoints to published services or Google APIs."""

    def __init__(
        self,
        *,
        config: PrivateServiceConnectConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._compute = client or ComputePscRestClient(
            project_id=config.project_id,
            endpoint=config.api_endpoint,
        )
        self._sleep = sleep
        self._monotonic = monotonic

    @driver_op(
        cloud="gcp",
        driver="private_service_connect",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_private_service_connect_config"])
        if not spec.managed_service_id:
            return ProvisionResult(False, "", _MISSING_IDENTITY, ["invalid_private_service_connect_config"])
        endpoint_type = _endpoint_type(cfg)
        location = _location(cfg, self._config.region)
        endpoint_id = str(cfg.get("endpoint_id") or self._endpoint_id(spec, endpoint_type, location))
        handle = _handle(location, endpoint_id)
        labels = _labels(
            endpoint_id,
            self._config.labels,
            cfg.get("labels") or {},
            managed_service_id=spec.managed_service_id,
        )
        description = _description(endpoint_id, spec)
        try:
            endpoint, _address = self._ensure_stack(
                endpoint_id,
                location,
                cfg,
                labels=labels,
                description=description,
                allow_create=True,
                owner=_Owner(
                    spec.managed_service_id,
                    record_proves=spec.recorded_handle_exclusive and spec.recorded_handle == handle,
                ),
            )
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Private Service Connect: {exc}", [str(exc)])
        state, ready = _state(endpoint, endpoint_type)
        return ProvisionResult(
            True,
            handle,
            f"Private Service Connect endpoint {endpoint_id} is {state}",
            ready=ready,
        )

    @driver_op(cloud="gcp", driver="private_service_connect")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            location, endpoint_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg, expected_location=location, expected_id=endpoint_id)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_private_service_connect_config"])
        owner = _Owner(spec.managed_service_id, record_proves=spec.recorded_handle_exclusive)
        try:
            current = self._compute.get_forwarding_rule(location, endpoint_id)
            self._assert_owned(current, endpoint_id, owner)
            labels = _labels(
                endpoint_id,
                self._config.labels,
                cfg.get("labels") or {},
                managed_service_id=spec.managed_service_id,
                adopted=_adopted(current),
            )
            self._ensure_stack(
                endpoint_id,
                location,
                cfg,
                labels=labels,
                description=str(current.get("description") or "Astrolift Private Service Connect"),
                allow_create=False,
                owner=owner,
            )
        except PrivateServiceConnectNotFound:
            return UpdateResult(False, spec.handle, "Private Service Connect endpoint not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Private Service Connect: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Private Service Connect endpoint {endpoint_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="private_service_connect",
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
            location, endpoint_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        cfg = dict(spec.config or {})
        protected = bool(
            cfg.get("deletion_protection", self._config.deletion_protection_default),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Private Service Connect endpoint has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        owner = _Owner(spec.managed_service_id, record_proves=spec.recorded_handle_exclusive)
        endpoint = self._get_forwarding_optional(location, endpoint_id)
        if endpoint is not None:
            refusal = "" if _owned(endpoint, endpoint_id) else "is not owned by Astrolift"
            refusal = refusal or _identity_refusal(endpoint, owner)
            if refusal:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"refusing to delete Private Service Connect endpoint {endpoint_id}: it {refusal}",
                    ["resource_not_owned"],
                    retryable=False,
                )
            if _adopted(endpoint) and not cfg.get("delete_adopted_resources"):
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "adopted endpoint requires delete_adopted_resources=true",
                    ["adopted_resource"],
                    retryable=False,
                )
        managed_address = not cfg.get("address_resource")
        address_name = str(cfg.get("address_id") or _name(endpoint_id, "ip"))
        address = self._get_address_optional(location, address_name) if managed_address else None
        if address is not None:
            refusal = "" if _owned(address, endpoint_id) else "is not owned by Astrolift"
            refusal = refusal or _identity_refusal(address, owner)
            if refusal:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"refusing to delete Private Service Connect address {address_name}: it {refusal}",
                    ["resource_not_owned"],
                    retryable=False,
                )
            if _adopted(address) and not cfg.get("delete_adopted_resources"):
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "adopted address requires delete_adopted_resources=true",
                    ["adopted_resource"],
                    retryable=False,
                )
            endpoint_link = str((endpoint or {}).get("selfLink") or "")
            external_users = [
                str(user) for user in address.get("users") or [] if not endpoint_link or str(user) != endpoint_link
            ]
            if external_users and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Private Service Connect address has external dependents: {external_users}",
                    ["address_in_use"],
                    retryable=False,
                )
        try:
            if endpoint is not None:
                self._wait_operation(
                    self._compute.delete_forwarding_rule(location, endpoint_id),
                    location,
                )
            if address is not None:
                refreshed = self._get_address_optional(location, address_name)
                users = list((refreshed or {}).get("users") or [])
                if users and not force_destroy:
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        f"Private Service Connect address still has dependents: {users}",
                        ["address_in_use"],
                        retryable=False,
                    )
                self._wait_operation(
                    self._compute.delete_address(location, address_name),
                    location,
                )
        except PrivateServiceConnectNotFound:
            pass
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Private Service Connect: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"Private Service Connect endpoint {endpoint_id} deleted")

    @driver_op(cloud="gcp", driver="private_service_connect")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            location, endpoint_id = _parse_handle(handle.handle)
            endpoint = self._compute.get_forwarding_rule(location, endpoint_id)
        except PrivateServiceConnectNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "Private Service Connect endpoint is gone")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Private Service Connect: {exc}")
        if not _owned(endpoint, endpoint_id) or _names_another_service(endpoint, handle.managed_service_id):
            return ServiceStatus(handle.handle, "error", "Private Service Connect endpoint is not owned")
        endpoint_type = "google_apis" if location == "global" else "service_attachment"
        provider_state, ready = _state(endpoint, endpoint_type)
        if ready:
            state = "available"
        elif provider_state in {"REJECTED", "NEEDS_ATTENTION", "CLOSED"}:
            state = "error"
        else:
            state = "provisioning"
        return ServiceStatus(handle.handle, state, f"Private Service Connect endpoint is {provider_state}")

    @driver_op(cloud="gcp", driver="private_service_connect")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        location, endpoint_id = _parse_handle(handle.handle)
        endpoint = self._compute.get_forwarding_rule(location, endpoint_id)
        self._assert_owned(
            endpoint,
            endpoint_id,
            _Owner(handle.managed_service_id, record_proves=handle.recorded_handle_exclusive),
        )
        cfg = dict(config or {})
        ip_address = str(endpoint.get("IPAddress") or "")
        dns_name = str(cfg.get("dns_name") or "")
        destination = dns_name or ip_address
        scheme = str(cfg.get("url_scheme") or "")
        url = _endpoint_url(scheme, destination, cfg.get("port"))
        endpoint_type = "google_apis" if location == "global" else "service_attachment"
        target = str(endpoint.get("target") or "")
        connection_status = str(
            endpoint.get("pscConnectionStatus") or ("ACTIVE" if location == "global" else "PENDING"),
        )
        endpoint_uri = str(endpoint.get("selfLinkWithId") or endpoint.get("selfLink") or "")
        return Binding(
            env_vars={
                "PRIVATE_ENDPOINT_ID": ValueRef(literal=endpoint_id),
                "PRIVATE_ENDPOINT_URL": ValueRef(literal=url),
                "PRIVATE_ENDPOINT_DNS": ValueRef(literal=dns_name),
                "PRIVATE_ENDPOINT_IPS": ValueRef(
                    literal=json.dumps([ip_address] if ip_address else [], separators=(",", ":")),
                ),
                "PRIVATE_ENDPOINT_TYPE": ValueRef(literal=endpoint_type),
                "PRIVATE_ENDPOINT_SERVICE_NAME": ValueRef(literal=target),
                "PRIVATE_ENDPOINT_DNS_NAME": ValueRef(literal=dns_name),
                "PRIVATE_ENDPOINT_DNS_NAMES": ValueRef(
                    literal=json.dumps([dns_name] if dns_name else [], separators=(",", ":")),
                ),
                "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS": ValueRef(literal="[]"),
                "PRIVATE_ENDPOINT_PREFIX_LIST_ID": ValueRef(literal=""),
                "GCP_PSC_PROJECT": ValueRef(literal=self._config.project_id),
                "GCP_PSC_REGION": ValueRef(literal=location),
                "GCP_PSC_ENDPOINT": ValueRef(literal=endpoint_id),
                "GCP_PSC_ENDPOINT_URI": ValueRef(literal=endpoint_uri),
                "GCP_PSC_IP_ADDRESS": ValueRef(literal=ip_address),
                "GCP_PSC_TARGET": ValueRef(literal=target),
                "GCP_PSC_CONNECTION_STATUS": ValueRef(literal=connection_status),
            },
            notes=(
                "Private Service Connect does not create application DNS records. "
                "Attach a private DNS record or pass dns_name when clients require a hostname. "
                "Service Directory namespaces and private zones created by Google are shared and "
                "are intentionally retained when this endpoint is deleted."
            ),
        )

    @driver_op(cloud="gcp", driver="private_service_connect")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise PrivateServiceConnectError(
            "Private Service Connect has no durable snapshot semantic; keep declarative config",
        )

    @driver_op(cloud="gcp", driver="private_service_connect")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "Private Service Connect has no provider snapshot to restore; provision from declarative config",
            ["not_implemented"],
        )

    @driver_op(cloud="gcp", driver="private_service_connect", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "size": {
                    "type": "string",
                    "enum": ["small", "medium", "large", "xlarge", "custom"],
                },
                "endpoint_type": {
                    "type": "string",
                    "enum": ["service_attachment", "google_apis"],
                    "default": "service_attachment",
                },
                "endpoint_id": {"type": "string"},
                "address_id": {"type": "string"},
                "region": {"type": "string"},
                "network": {"type": "string"},
                "subnetwork": {"type": "string"},
                "service_attachment": {"type": "string"},
                "api_bundle": {
                    "type": "string",
                    "enum": ["all-apis", "vpc-sc"],
                    "default": "all-apis",
                },
                "ip_address": {"type": "string"},
                "ip_version": {
                    "type": "string",
                    "enum": ["IPV4", "IPV6"],
                    "default": "IPV4",
                },
                "address_resource": {
                    "type": "string",
                    "description": "Existing address name or URI; retained on endpoint deletion.",
                },
                "allow_global_access": {"type": "boolean", "default": False},
                "service_directory": {
                    "type": "object",
                    "properties": {
                        "namespace": {"type": "string"},
                        "service": {"type": "string"},
                        "region": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "no_automate_dns_zone": {"type": "boolean", "default": False},
                "dns_name": {"type": "string"},
                "url_scheme": {
                    "type": "string",
                    "description": "Optional application protocol used to render PRIVATE_ENDPOINT_URL.",
                },
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "labels": {"type": "object", "additionalProperties": {"type": "string"}},
                "address": {"type": "object"},
                "forwarding_rule": {"type": "object"},
                "delete_adopted_resources": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
            },
            "allOf": [
                {
                    "if": {
                        "properties": {"endpoint_type": {"const": "google_apis"}},
                        "required": ["endpoint_type"],
                    },
                    "then": {
                        "anyOf": [
                            {"required": ["ip_address"]},
                            {"required": ["address_resource"]},
                        ],
                    },
                    "else": {"required": ["service_attachment"]},
                },
            ],
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="private_service_connect", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "PRIVATE_ENDPOINT_ID": "Portable endpoint identifier",
                "PRIVATE_ENDPOINT_URL": "Optional protocol URL using the DNS name or endpoint IP",
                "PRIVATE_ENDPOINT_DNS": "Configured private DNS hostname",
                "PRIVATE_ENDPOINT_IPS": "JSON array containing the endpoint IP",
                "PRIVATE_ENDPOINT_TYPE": "service_attachment or google_apis",
                "PRIVATE_ENDPOINT_SERVICE_NAME": "PSC service attachment or Google APIs bundle",
                "PRIVATE_ENDPOINT_DNS_NAME": "Configured private DNS hostname",
                "PRIVATE_ENDPOINT_DNS_NAMES": "JSON array of private DNS hostnames",
                "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS": "Portable empty compatibility array",
                "PRIVATE_ENDPOINT_PREFIX_LIST_ID": "Portable empty compatibility value",
                "GCP_PSC_PROJECT": "Google Cloud project ID",
                "GCP_PSC_REGION": "Endpoint region or global",
                "GCP_PSC_ENDPOINT": "Forwarding rule name",
                "GCP_PSC_ENDPOINT_URI": "ID-based endpoint URI when returned by Compute",
                "GCP_PSC_IP_ADDRESS": "Private Service Connect endpoint IP",
                "GCP_PSC_TARGET": "Service attachment or Google APIs bundle",
                "GCP_PSC_CONNECTION_STATUS": "Provider connection status",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "allow_global_access",
            "labels",
            "dns_name",
            "url_scheme",
            "port",
            "deletion_protection",
            "delete_adopted_resources",
        ]

    def _ensure_stack(
        self,
        endpoint_id: str,
        location: str,
        cfg: dict[str, Any],
        *,
        labels: dict[str, str],
        description: str,
        allow_create: bool,
        owner: _Owner,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        address, address_external = self._ensure_address(
            endpoint_id,
            location,
            cfg,
            labels=labels,
            description=description,
            allow_create=allow_create,
            owner=owner,
        )
        address_value = str(address.get("address") or address.get("selfLink") or "")
        if not address_value:
            raise PrivateServiceConnectError("reserved address has no provider-assigned address")
        endpoint = self._ensure_forwarding_rule(
            endpoint_id,
            location,
            cfg,
            address_value=address_value,
            labels=labels,
            description=description,
            allow_create=allow_create,
            owner=owner,
        )
        if address_external:
            return endpoint, address
        return endpoint, address

    def _ensure_address(
        self,
        endpoint_id: str,
        location: str,
        cfg: dict[str, Any],
        *,
        labels: dict[str, str],
        description: str,
        allow_create: bool,
        owner: _Owner,
    ) -> tuple[dict[str, Any], bool]:
        external = bool(cfg.get("address_resource"))
        address_name = _resource_name(
            str(cfg.get("address_resource") or cfg.get("address_id") or _name(endpoint_id, "ip")),
        )
        current = self._get_address_optional(location, address_name)
        if current is None:
            if external:
                raise PrivateServiceConnectNotFound(
                    f"external address {location}/{address_name} does not exist",
                )
            if not allow_create:
                raise PrivateServiceConnectNotFound(f"address {location}/{address_name}")
            desired = self._address_body(
                endpoint_id,
                location,
                address_name,
                cfg,
                labels=labels,
                description=description,
            )
            self._wait_operation(self._compute.insert_address(location, desired), location)
            current = self._compute.get_address(location, address_name)
        desired = self._address_body(
            endpoint_id,
            location,
            address_name,
            cfg,
            labels=labels,
            description=description,
        )
        self._assert_address_compatible(current, desired, location, external=external)
        if external:
            return current, True
        if not _owned(current, endpoint_id):
            # The astrolift-adopted label this driver used to write on the
            # tenant's say-so proves nothing about who authorized it. Adoption
            # of an existing resource is a separate, operator-authorized
            # operation (#1365) that no tenant config flag may grant (#2021).
            raise PrivateServiceConnectError(
                f"address {location}/{address_name} exists but is not owned by this endpoint; adoption is a "
                "separate, operator-authorized operation and cannot be granted by tenant config",
            )
        refusal = _identity_refusal(current, owner)
        if refusal:
            raise PrivateServiceConnectError(f"address {location}/{address_name} {refusal}")
        current = self._ensure_labels(
            "address",
            location,
            address_name,
            current,
            labels,
        )
        return current, False

    def _ensure_forwarding_rule(
        self,
        endpoint_id: str,
        location: str,
        cfg: dict[str, Any],
        *,
        address_value: str,
        labels: dict[str, str],
        description: str,
        allow_create: bool,
        owner: _Owner,
    ) -> dict[str, Any]:
        desired = self._forwarding_body(
            endpoint_id,
            location,
            cfg,
            address_value=address_value,
            labels=labels,
            description=description,
        )
        current = self._get_forwarding_optional(location, endpoint_id)
        if current is None:
            if not allow_create:
                raise PrivateServiceConnectNotFound(f"forwarding rule {location}/{endpoint_id}")
            self._wait_operation(
                self._compute.insert_forwarding_rule(location, desired),
                location,
            )
            return self._compute.get_forwarding_rule(location, endpoint_id)
        if not _owned(current, endpoint_id):
            raise PrivateServiceConnectError(
                f"forwarding rule {location}/{endpoint_id} exists but is not owned by Astrolift; adoption is a "
                "separate, operator-authorized operation and cannot be granted by tenant config",
            )
        refusal = _identity_refusal(current, owner)
        if refusal:
            raise PrivateServiceConnectError(f"forwarding rule {location}/{endpoint_id} {refusal}")
        self._assert_forwarding_compatible(current, desired, location)
        current = self._ensure_labels(
            "forwarding",
            location,
            endpoint_id,
            current,
            labels,
        )
        if location != "global":
            desired_global_access = bool(desired.get("allowPscGlobalAccess"))
            if bool(current.get("allowPscGlobalAccess")) != desired_global_access:
                self._wait_operation(
                    self._compute.patch_forwarding_rule(
                        location,
                        endpoint_id,
                        {"allowPscGlobalAccess": desired_global_access},
                    ),
                    location,
                )
                current = self._compute.get_forwarding_rule(location, endpoint_id)
        return current

    def _address_body(
        self,
        endpoint_id: str,
        location: str,
        address_name: str,
        cfg: dict[str, Any],
        *,
        labels: dict[str, str],
        description: str,
    ) -> dict[str, Any]:
        body = {
            **_safe_raw(cfg, "address", _ADDRESS_OWNED_FIELDS),
            "name": address_name,
            "description": description,
            "addressType": "INTERNAL",
            "labels": labels,
        }
        if cfg.get("ip_address"):
            body["address"] = str(cfg["ip_address"])
        if location == "global":
            body.update(
                purpose="PRIVATE_SERVICE_CONNECT",
                network=_network_ref(
                    str(cfg.get("network") or self._config.network),
                    self._config.project_id,
                ),
            )
        else:
            body.update(
                ipVersion=str(cfg.get("ip_version") or "IPV4"),
                subnetwork=_subnetwork_ref(
                    str(cfg.get("subnetwork") or self._config.subnetwork),
                    self._config.project_id,
                    location,
                ),
            )
        return body

    def _forwarding_body(
        self,
        endpoint_id: str,
        location: str,
        cfg: dict[str, Any],
        *,
        address_value: str,
        labels: dict[str, str],
        description: str,
    ) -> dict[str, Any]:
        endpoint_type = _endpoint_type(cfg)
        network = _network_ref(
            str(cfg.get("network") or self._config.network),
            self._config.project_id,
        )
        body: dict[str, Any] = {
            **_safe_raw(cfg, "forwarding_rule", _FORWARDING_OWNED_FIELDS),
            "name": endpoint_id,
            "description": description,
            "IPAddress": address_value,
            "network": network,
            "target": (
                str(cfg.get("api_bundle") or "all-apis")
                if endpoint_type == "google_apis"
                else _service_attachment_ref(str(cfg["service_attachment"]))
            ),
            "labels": labels,
        }
        if endpoint_type == "google_apis":
            body["loadBalancingScheme"] = ""
        else:
            body["allowPscGlobalAccess"] = bool(cfg.get("allow_global_access"))
        service_directory = dict(cfg.get("service_directory") or {})
        if service_directory:
            registration: dict[str, str] = {}
            if service_directory.get("namespace"):
                registration["namespace"] = str(service_directory["namespace"])
            if service_directory.get("service"):
                registration["service"] = str(service_directory["service"])
            if location == "global" and service_directory.get("region"):
                registration["serviceDirectoryRegion"] = str(service_directory["region"])
            body["serviceDirectoryRegistrations"] = [registration]
        if "no_automate_dns_zone" in cfg:
            body["noAutomateDnsZone"] = bool(cfg["no_automate_dns_zone"])
        return body

    def _assert_address_compatible(
        self,
        current: dict[str, Any],
        desired: dict[str, Any],
        location: str,
        *,
        external: bool,
    ) -> None:
        fields = (
            ("addressType", "purpose", "network")
            if location == "global"
            else (
                "addressType",
                "ipVersion",
                "subnetwork",
            )
        )
        for field_name in fields:
            wanted = desired.get(field_name)
            if wanted is None:
                continue
            actual = current.get(field_name)
            matches = _same_resource(actual, wanted) if field_name in {"network", "subnetwork"} else actual == wanted
            if not matches:
                kind = "external" if external else "existing"
                raise PrivateServiceConnectError(
                    f"{kind} address is incompatible: {field_name}={actual!r}, expected {wanted!r}",
                )
        requested_ip = str(desired.get("address") or "")
        if requested_ip and str(current.get("address") or "") != requested_ip:
            raise PrivateServiceConnectError(
                f"address IP is {current.get('address')!r}, expected {requested_ip!r}",
            )

    def _assert_forwarding_compatible(
        self,
        current: dict[str, Any],
        desired: dict[str, Any],
        location: str,
    ) -> None:
        for field_name in ("target", "network"):
            if not _same_resource(current.get(field_name), desired.get(field_name)):
                raise PrivateServiceConnectError(
                    f"forwarding rule {field_name} is immutable; reprovision is required",
                )
        actual_ip = str(current.get("IPAddress") or "")
        desired_ip = str(desired.get("IPAddress") or "")
        if actual_ip != desired_ip:
            raise PrivateServiceConnectError(
                "forwarding rule IPAddress is immutable; reprovision is required",
            )
        for field_name in (
            "loadBalancingScheme",
            "serviceDirectoryRegistrations",
            "noAutomateDnsZone",
        ):
            if field_name not in desired:
                continue
            actual = current.get(field_name)
            wanted = desired[field_name]
            if field_name == "loadBalancingScheme":
                actual = actual or ""
            if not _contains(actual, wanted):
                raise PrivateServiceConnectError(
                    f"forwarding rule {field_name} is immutable; reprovision is required",
                )
        if location == "global" and bool(current.get("allowPscGlobalAccess")):
            raise PrivateServiceConnectError(
                "global Google APIs endpoints cannot enable allowPscGlobalAccess",
            )

    def _ensure_labels(
        self,
        resource_type: str,
        location: str,
        name: str,
        current: dict[str, Any],
        desired: dict[str, str],
    ) -> dict[str, Any]:
        labels = {**dict(current.get("labels") or {}), **desired}
        if labels == dict(current.get("labels") or {}):
            return current
        fingerprint = str(current.get("labelFingerprint") or "")
        if resource_type == "address":
            operation = self._compute.set_address_labels(
                location,
                name,
                labels=labels,
                fingerprint=fingerprint,
            )
        else:
            operation = self._compute.set_forwarding_rule_labels(
                location,
                name,
                labels=labels,
                fingerprint=fingerprint,
            )
        self._wait_operation(operation, location)
        if resource_type == "address":
            return self._compute.get_address(location, name)
        return self._compute.get_forwarding_rule(location, name)

    def _assert_owned(self, resource: dict[str, Any], endpoint_id: str, owner: _Owner) -> None:
        if not _owned(resource, endpoint_id):
            raise PrivateServiceConnectError(
                "resource is not owned by this Private Service Connect endpoint",
            )
        refusal = _identity_refusal(resource, owner)
        if refusal:
            raise PrivateServiceConnectError(f"Private Service Connect endpoint {endpoint_id} {refusal}")

    def _get_address_optional(self, location: str, name: str) -> dict[str, Any] | None:
        try:
            return self._compute.get_address(location, name)
        except PrivateServiceConnectNotFound:
            return None

    def _get_forwarding_optional(self, location: str, name: str) -> dict[str, Any] | None:
        try:
            return self._compute.get_forwarding_rule(location, name)
        except PrivateServiceConnectNotFound:
            return None

    def _wait_operation(self, operation: dict[str, Any], location: str) -> dict[str, Any]:
        if not operation:
            return {}
        current = operation
        name = str(operation.get("name") or "")
        deadline = self._monotonic() + self._config.operation_timeout_seconds
        while str(current.get("status") or "") != "DONE":
            if not name:
                raise PrivateServiceConnectError("Compute operation response has no name")
            if self._monotonic() >= deadline:
                raise PrivateServiceConnectError(f"Compute operation {name} timed out")
            self._sleep(self._config.poll_interval_seconds)
            current = self._compute.get_operation(location, name)
        error = current.get("error") or {}
        if error:
            raise PrivateServiceConnectError(
                f"Compute operation {name} failed: {error.get('errors') or error}",
            )
        return current

    def _validate_config(
        self,
        cfg: dict[str, Any],
        *,
        expected_location: str = "",
        expected_id: str = "",
    ) -> str:
        unknown = sorted(set(cfg) - set(self.config_schema()["properties"]))
        if unknown:
            return f"unknown Private Service Connect config keys: {', '.join(unknown)}"
        endpoint_type = _endpoint_type(cfg)
        if endpoint_type not in {"service_attachment", "google_apis"}:
            return "endpoint_type must be service_attachment or google_apis"
        location = _location(cfg, self._config.region)
        if not location:
            return "region is required for a published-service endpoint"
        if expected_location and location != expected_location:
            return "endpoint location is immutable; reprovision is required"
        endpoint_id = cfg.get("endpoint_id")
        if endpoint_id is not None and not isinstance(endpoint_id, str):
            return "endpoint_id must be a string"
        if expected_id and endpoint_id and endpoint_id != expected_id:
            return "endpoint_id is immutable; reprovision is required"
        candidate_id = str(endpoint_id or expected_id or "a")
        if endpoint_type == "google_apis":
            if endpoint_id and not _GOOGLE_API_ID_PATTERN.fullmatch(candidate_id):
                return "Google APIs endpoint_id must be 1-20 lowercase letters or numbers and start with a letter"
        elif endpoint_id and not _ID_PATTERN.fullmatch(candidate_id):
            return "endpoint_id must be a valid RFC1035 Compute resource ID"
        for key in ("address_id",):
            value = cfg.get(key)
            if value is not None and (not isinstance(value, str) or not _ID_PATTERN.fullmatch(value)):
                return f"{key} must be a valid RFC1035 Compute resource ID"
        network = cfg.get("network") or self._config.network
        if not isinstance(network, str) or not network:
            return "network is required"
        if endpoint_type == "service_attachment":
            attachment = cfg.get("service_attachment")
            if not isinstance(attachment, str) or not attachment:
                return "service_attachment is required for a published-service endpoint"
            if not re.fullmatch(
                r"projects/[^/]+/regions/[^/]+/serviceAttachments/[^/]+",
                _resource_path(attachment),
            ):
                return "service_attachment must be a service attachment resource URI"
            attachment_region = _service_attachment_region(attachment)
            if attachment_region and attachment_region != location:
                return "endpoint region must match the service attachment region"
            subnetwork = cfg.get("subnetwork") or self._config.subnetwork
            if not isinstance(subnetwork, str) or not subnetwork:
                return "subnetwork is required for a published-service endpoint"
            if cfg.get("api_bundle"):
                return "api_bundle is only valid for google_apis endpoints"
        else:
            if cfg.get("service_attachment"):
                return "service_attachment is not valid for google_apis endpoints"
            if cfg.get("subnetwork"):
                return "subnetwork is not valid for global Google APIs endpoints"
            if cfg.get("allow_global_access"):
                return "allow_global_access is only valid for published-service endpoints"
            if cfg.get("ip_version", "IPV4") != "IPV4":
                return "Google APIs endpoints require IPV4"
            if not cfg.get("ip_address") and not cfg.get("address_resource"):
                return "google_apis endpoints require ip_address or address_resource"
        if cfg.get("api_bundle", "all-apis") not in {"all-apis", "vpc-sc"}:
            return "api_bundle must be all-apis or vpc-sc"
        if cfg.get("address_resource") and cfg.get("address_id"):
            return "address_resource and address_id are mutually exclusive"
        address_location = _address_resource_location(str(cfg.get("address_resource") or ""))
        if address_location and address_location != location:
            return "address_resource scope must match the endpoint location"
        if cfg.get("ip_version", "IPV4") not in {"IPV4", "IPV6"}:
            return "ip_version must be IPV4 or IPV6"
        if cfg.get("ip_address"):
            try:
                address = ipaddress.ip_address(str(cfg["ip_address"]))
            except ValueError:
                return "ip_address must be a valid IPv4 or IPv6 address"
            expected_version = 4 if cfg.get("ip_version", "IPV4") == "IPV4" else 6
            if address.version != expected_version:
                return "ip_address does not match ip_version"
            if not address.is_private:
                return "Private Service Connect ip_address must be private"
        for key in ("region", "address_resource", "dns_name", "url_scheme"):
            if key in cfg and (not isinstance(cfg[key], str) or not cfg[key]):
                return f"{key} must be a non-empty string"
        if cfg.get("dns_name") and not _valid_domain(str(cfg["dns_name"]).lower().rstrip(".")):
            return "dns_name must be a valid hostname"
        if cfg.get("url_scheme") and not re.fullmatch(
            r"[a-z][a-z0-9+.-]*",
            str(cfg["url_scheme"]),
        ):
            return "url_scheme must be a valid lowercase URI scheme"
        if "port" in cfg and (
            isinstance(cfg["port"], bool) or not isinstance(cfg["port"], int) or not 1 <= cfg["port"] <= 65535
        ):
            return "port must be an integer between 1 and 65535"
        for key in (
            "allow_global_access",
            "no_automate_dns_zone",
            "delete_adopted_resources",
            "deletion_protection",
        ):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        if "size" in cfg and cfg["size"] not in {"small", "medium", "large", "xlarge", "custom"}:
            return "size must be small, medium, large, xlarge, or custom"
        labels = cfg.get("labels")
        operator_label_error = _validate_labels(self._config.labels)
        if operator_label_error:
            return f"provider config {operator_label_error}"
        if labels is not None:
            if not isinstance(labels, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
            ):
                return "labels must be a string-to-string object"
            reserved = sorted(set(labels).intersection(_RESERVED_LABELS))
            if reserved:
                return f"labels cannot override Astrolift ownership labels: {', '.join(reserved)}"
            label_error = _validate_labels(labels)
            if label_error:
                return label_error
        service_directory = cfg.get("service_directory")
        if service_directory is not None:
            if not isinstance(service_directory, dict):
                return "service_directory must be an object"
            unknown_directory = sorted(set(service_directory) - {"namespace", "service", "region"})
            if unknown_directory:
                return f"unknown service_directory keys: {', '.join(unknown_directory)}"
            if not service_directory:
                return "service_directory must contain namespace, service, or region"
            for key, value in service_directory.items():
                if not isinstance(value, str) or not value:
                    return f"service_directory.{key} must be a non-empty string"
            if endpoint_type != "google_apis" and service_directory.get("region"):
                return "service_directory.region is only valid for global Google APIs endpoints"
        for key, owned in (
            ("address", _ADDRESS_OWNED_FIELDS),
            ("forwarding_rule", _FORWARDING_OWNED_FIELDS),
        ):
            value = cfg.get(key)
            if value is not None and not isinstance(value, dict):
                return f"{key} must be an object"
            reserved = sorted(set(value or {}).intersection(owned | _OUTPUT_FIELDS))
            if reserved:
                return f"{key} cannot override Astrolift-owned fields: {', '.join(reserved)}"
        return ""

    def _endpoint_id(self, spec: ProvisionSpec, endpoint_type: str, location: str) -> str:
        raw = "-".join(
            filter(
                None,
                (
                    self._config.name_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint or "psc",
                ),
            ),
        )

        def derive(value: str) -> str:
            return _google_api_id(value) if endpoint_type == "google_apis" else _resource_id(value, maximum=48)

        legacy = derive(raw)
        if spec.recorded_handle == _handle(location, legacy):
            # Named before #2086 and recorded that way: keep it rather than
            # derive a new name and create a second endpoint beside it.
            return legacy
        # Slug-joined names collide across orgs (acme + web-prod and acme-web +
        # prod), so new names carry a digest of the managed-service id.
        return derive(f"{raw}-{hashlib.sha256(spec.managed_service_id.encode()).hexdigest()[:8]}")


def _safe_raw(cfg: dict[str, Any], key: str, owned_fields: set[str]) -> dict[str, Any]:
    raw = dict(cfg.get(key) or {})
    reserved = sorted(set(raw).intersection(owned_fields | _OUTPUT_FIELDS))
    if reserved:
        raise PrivateServiceConnectError(
            f"{key} cannot override Astrolift-owned fields: {', '.join(reserved)}",
        )
    return raw


def _endpoint_type(cfg: dict[str, Any]) -> str:
    return str(cfg.get("endpoint_type") or "service_attachment")


def _location(cfg: dict[str, Any], default_region: str) -> str:
    return "global" if _endpoint_type(cfg) == "google_apis" else str(cfg.get("region") or default_region)


def _handle(location: str, endpoint_id: str) -> str:
    return f"{KIND}/{location}/{endpoint_id}"


def _parse_handle(handle: str) -> tuple[str, str]:
    parts = handle.split("/")
    if len(parts) != 3 or parts[0] != KIND or not parts[1] or not parts[2]:
        raise ValueError(
            f"handle {handle!r} must be 'private_endpoint/<region-or-global>/<endpoint>'",
        )
    location, endpoint_id = parts[1], parts[2]
    if location == "global":
        if not _GOOGLE_API_ID_PATTERN.fullmatch(endpoint_id):
            raise ValueError("global Private Service Connect endpoint handle has an invalid ID")
    elif not _ID_PATTERN.fullmatch(endpoint_id):
        raise ValueError("regional Private Service Connect endpoint handle has an invalid ID")
    return location, endpoint_id


def _description(endpoint_id: str, spec: ProvisionSpec) -> str:
    boundary = "/".join(
        (
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.tenant_cluster_id,
        ),
    )
    identity = spec.managed_service_id or spec.service_handle_hint or endpoint_id
    value = f"Astrolift Private Service Connect; endpoint={endpoint_id}; boundary={boundary}; resource={identity}"
    if len(value) <= 256:
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"{value[: 256 - len(digest) - 1]}-{digest}"


def _labels(
    endpoint_id: str,
    operator: dict[str, str],
    service: dict[str, str],
    *,
    managed_service_id: str,
    adopted: bool = False,
) -> dict[str, str]:
    labels = {**operator, **service}
    labels.update(
        {
            "astrolift-managed-by": "platform",
            "astrolift-private-endpoint": _label_value(endpoint_id),
            MANAGED_SERVICE_ID_LABEL: _label_value(managed_service_id),
        },
    )
    if adopted:
        labels["astrolift-adopted"] = "true"
    return labels


def _owned(resource: dict[str, Any], endpoint_id: str) -> bool:
    labels = resource.get("labels") or {}
    return labels.get("astrolift-managed-by") == "platform" and labels.get(
        "astrolift-private-endpoint",
    ) == _label_value(endpoint_id)


_MISSING_IDENTITY = "Private Service Connect needs the managed-service id to mark the resources it owns"


@dataclass(frozen=True)
class _Owner:
    """The service asking, and whether its record vouches for a resource that predates its label."""

    managed_service_id: str
    record_proves: bool


def _identity_refusal(resource: dict[str, Any], owner: _Owner) -> str:
    """Why ``owner`` may not act on a resource ``_owned`` already accepted, or ``""`` (#2086).

    ``_owned`` keys on the endpoint id, which a tenant chooses, so it says only
    that Astrolift made the resource. Whose it is comes from, in order: the
    managed-service label; the description written at create since #1330, which
    tenant config cannot set and this driver never rewrites; the platform's
    exclusive record of the handle, for a resource that has neither.
    """
    if not owner.managed_service_id:
        return f"cannot be checked: {_MISSING_IDENTITY}"
    ours = _label_value(owner.managed_service_id)
    labeled = str((resource.get("labels") or {}).get(MANAGED_SERVICE_ID_LABEL) or "")
    if labeled:
        return "" if labeled == ours else "belongs to another managed service"
    described = _described_service(resource)
    if described == owner.managed_service_id:
        return ""
    if _UUID_PATTERN.fullmatch(described):
        return "was created for another managed service"
    if owner.record_proves:
        return ""
    return (
        "predates the managed-service ownership label, and neither its description nor an exclusive platform "
        "record says it is this service's; an operator must mark its owner"
    )


def _names_another_service(resource: dict[str, Any], managed_service_id: str) -> bool:
    if not managed_service_id:
        return False
    labeled = str((resource.get("labels") or {}).get(MANAGED_SERVICE_ID_LABEL) or "")
    if labeled:
        return labeled != _label_value(managed_service_id)
    described = _described_service(resource)
    return bool(_UUID_PATTERN.fullmatch(described)) and described != managed_service_id


def _described_service(resource: dict[str, Any]) -> str:
    """The managed-service id ``_description`` wrote, or ``""``.

    A description at the 256-character cap may have been cut through the id and
    had a digest appended, so it is not read at all.
    """
    description = str(resource.get("description") or "")
    match = re.search(r"; resource=([^;]+)$", description)
    if match is None or len(description) >= 256:
        return ""
    return match.group(1)


def _adopted(resource: dict[str, Any]) -> bool:
    return (resource.get("labels") or {}).get("astrolift-adopted") == "true"


def _label_value(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    if len(clean) <= 63:
        return clean or "endpoint"
    digest = hashlib.sha256(clean.encode()).hexdigest()[:10]
    return f"{clean[:52].rstrip('-_')}-{digest}"


def _validate_labels(labels: dict[str, str]) -> str:
    key_pattern = re.compile(r"[a-z][-_a-z0-9]{0,62}")
    value_pattern = re.compile(r"[-_a-z0-9]{0,63}")
    for key, value in labels.items():
        if not key_pattern.fullmatch(key):
            return f"invalid GCP label key {key!r}"
        if not value_pattern.fullmatch(value):
            return f"invalid GCP label value for {key!r}"
    return ""


def _resource_id(value: str, *, maximum: int) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not normalized or not normalized[0].isalpha():
        normalized = f"a-{normalized}"
    if len(normalized) > maximum:
        digest = hashlib.sha256(normalized.encode()).hexdigest()[:10]
        normalized = f"{normalized[: maximum - len(digest) - 1].rstrip('-')}-{digest}"
    return normalized.rstrip("-") or "endpoint"


def _google_api_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
    if not normalized or not normalized[0].isalpha():
        normalized = f"a{normalized}"
    if len(normalized) > 20:
        digest = hashlib.sha256(normalized.encode()).hexdigest()[:8]
        normalized = f"{normalized[:12]}{digest}"
    return normalized


def _name(endpoint_id: str, suffix: str) -> str:
    return _resource_id(f"{endpoint_id}-{suffix}", maximum=63)


def _resource_name(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1]


def _address_resource_location(value: str) -> str:
    if not value or "/" not in value:
        return ""
    path = _resource_path(value)
    if "/global/addresses/" in f"/{path}":
        return "global"
    match = re.search(r"(?:^|/)regions/([^/]+)/addresses/", path)
    return match.group(1) if match else ""


def _network_ref(value: str, project_id: str) -> str:
    return _compute_ref(value, project_id, "global/networks")


def _subnetwork_ref(value: str, project_id: str, region: str) -> str:
    return _compute_ref(value, project_id, f"regions/{region}/subnetworks")


def _compute_ref(value: str, project_id: str, collection: str) -> str:
    if value.startswith(("https://", "http://")):
        return value
    if value.startswith("projects/"):
        return f"https://www.googleapis.com/compute/v1/{value}"
    if value.startswith(("global/", "regions/")):
        return f"https://www.googleapis.com/compute/v1/projects/{project_id}/{value}"
    return f"https://www.googleapis.com/compute/v1/projects/{project_id}/{collection}/{value}"


def _service_attachment_ref(value: str) -> str:
    if value.startswith(("https://", "http://")):
        return value
    return f"https://www.googleapis.com/compute/v1/{value.lstrip('/')}"


def _service_attachment_region(value: str) -> str:
    match = re.search(r"(?:^|/)regions/([^/]+)/serviceAttachments/", value)
    return match.group(1) if match else ""


def _resource_path(value: Any) -> str:
    raw = str(value or "").rstrip("/")
    marker = "/projects/"
    if marker in raw:
        return f"projects/{raw.split(marker, 1)[1]}"
    return raw.lstrip("/")


def _same_resource(left: Any, right: Any) -> bool:
    return _resource_path(left) == _resource_path(right)


def _valid_domain(value: str) -> bool:
    if len(value) > 253 or "." not in value:
        return False
    return all(
        1 <= len(label) <= 63 and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in value.split(".")
    )


def _endpoint_url(scheme: str, destination: str, port: Any) -> str:
    if not scheme or not destination:
        return ""
    authority = destination
    with contextlib.suppress(ValueError):
        if ipaddress.ip_address(destination).version == 6:
            authority = f"[{destination}]"
    if port is not None:
        authority = f"{authority}:{port}"
    return f"{scheme}://{authority}"


def _contains(actual: Any, desired: Any) -> bool:
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value) for key, value in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(desired)
            and all(_contains(left, right) for left, right in zip(actual, desired, strict=True))
        )
    return actual == desired


def _state(endpoint: dict[str, Any], endpoint_type: str) -> tuple[str, bool]:
    if endpoint_type == "google_apis":
        provider_state = str(endpoint.get("status") or "ACTIVE")
        return provider_state, provider_state in {"ACTIVE", "READY"}
    provider_state = str(endpoint.get("pscConnectionStatus") or "PENDING")
    return provider_state, provider_state == "ACCEPTED"


__all__ = [
    "ComputePscRestClient",
    "PrivateServiceConnectAlreadyExists",
    "PrivateServiceConnectConfig",
    "PrivateServiceConnectDriver",
    "PrivateServiceConnectError",
    "PrivateServiceConnectNotFound",
]
