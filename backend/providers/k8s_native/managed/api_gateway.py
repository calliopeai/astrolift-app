"""Kubernetes Gateway API implementation of the portable API gateway contract."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, cast

from _sdk import UnsupportedOperationError
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

KIND = "api_gateway"
VARIANT = "gateway_api"
API_VERSION = "gateway.networking.k8s.io/v1"
RESOURCE_KIND = "Gateway"
REQUIRED_CRDS = (
    "gateways.gateway.networking.k8s.io",
    "httproutes.gateway.networking.k8s.io",
)

_OWNER = "app.kubernetes.io/managed-by"
_OWNER_ID = "astrolift.io/managed-service-id"
_CHILDREN = "astrolift.io/managed-children"
_LABEL_KEY = re.compile(
    r"^(?:[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$",
)
_LABEL_VALUE = re.compile(r"^(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")
_SECTION_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
_ROUTE_VERSIONS = {
    "HTTPRoute": API_VERSION,
    "GRPCRoute": API_VERSION,
    "TLSRoute": API_VERSION,
    "TCPRoute": "gateway.networking.k8s.io/v1alpha2",
    "UDPRoute": "gateway.networking.k8s.io/v1alpha2",
}
_EXPERIMENTAL_ROUTES = {"TCPRoute", "UDPRoute"}
_AUXILIARY_VERSIONS = {
    "BackendTLSPolicy": API_VERSION,
    "ListenerSet": API_VERSION,
}
_CONFIG_FIELDS = {
    "gateway_class_name",
    "listeners",
    "addresses",
    "infrastructure",
    "allowed_listeners",
    "routes",
    "resources",
    "labels",
    "annotations",
    "deletion_protection",
}


@dataclass(frozen=True)
class GatewayAPIConfig:
    cluster_driver: Any = None
    namespace: str | None = None
    gateway_class_name: str = ""
    allow_class_override: bool = False
    allow_cross_namespace_routes: bool = False
    allow_cross_namespace_backends: bool = False
    allow_cross_namespace_certificates: bool = False
    allow_custom_backends: bool = False
    allow_extension_refs: bool = False
    allow_experimental_routes: bool = False
    allow_listener_sets: bool = False


class GatewayAPIDriver(ManagedServiceDriver):
    """Own one Gateway and its explicitly declared same-namespace Routes."""

    @driver_op(
        cloud="k8s_native",
        driver="api_gateway",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._require_driver()
            if not spec.managed_service_id:
                raise ValueError("Gateway API requires a managed_service_id for safe ownership")
            namespace = self._config.namespace or app_namespace(
                organization_slug=spec.organization_slug,
                app_slug=spec.app_slug,
            )
            name = dns_label(
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "gateway",
            )
            cfg = self._normalize(spec.config)
            self._assert_adoptable(
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
                managed_service_id=spec.managed_service_id,
            )
            manifests = self._manifests(
                namespace=namespace,
                name=name,
                cfg=cfg,
                owner=self._owner_from_spec(spec),
                cluster_id=spec.tenant_cluster_id,
            )
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_gateway_api_config"])

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
            return ProvisionResult(False, handle, "Gateway API resources were rejected", result.summary())
        return ProvisionResult(
            True,
            handle,
            f"Gateway {namespace}/{name} submitted for reconciliation",
            ready=False,
        )

    def __init__(self, *, config: GatewayAPIConfig) -> None:
        self._config = config

    @driver_op(cloud="k8s_native", driver="api_gateway")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require_driver()
            parsed = self._parsed(spec.handle)
            current = self._gateway(parsed.cluster_id, parsed.namespace, parsed.name)
            if current is None:
                return UpdateResult(
                    False,
                    spec.handle,
                    "Gateway does not exist",
                    ["resource_not_found"],
                    retryable=False,
                )
            owner = self._assert_owned(current)
            cfg = self._normalize(spec.config)
            current_class = str((current.get("spec", {}) or {}).get("gatewayClassName") or "")
            desired_class = self._gateway_class(cfg)
            if current_class and current_class != desired_class:
                raise ValueError("gateway_class_name is immutable and requires replacement")
            manifests = self._manifests(
                namespace=parsed.namespace,
                name=parsed.name,
                cfg=cfg,
                owner=self._owner_from_labels(current),
                cluster_id=parsed.cluster_id,
            )
            stale = self._stale_children(current, manifests)
            stale_stubs = self._owned_child_stubs(parsed, stale, owner)
        except (TypeError, ValueError) as exc:
            return UpdateResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_gateway_api_update"],
                retryable=False,
            )

        if stale_stubs:
            deleted = self._config.cluster_driver.delete_manifests(
                parsed.cluster_id,
                parsed.namespace,
                stale_stubs,
            )
            if not deleted.ok:
                return UpdateResult(False, spec.handle, "could not prune removed Gateway routes", deleted.summary())
        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            manifests,
        )
        if not result.ok:
            return UpdateResult(False, spec.handle, "Gateway API update was rejected", result.summary())
        return UpdateResult(True, spec.handle, f"Gateway {parsed.name} update submitted")

    @driver_op(
        cloud="k8s_native",
        driver="api_gateway",
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
            self._require_driver()
            parsed = self._parsed(spec.handle)
            gateway = self._gateway(parsed.cluster_id, parsed.namespace, parsed.name)
            if gateway is None:
                return DeprovisionResult(True, spec.handle, "Gateway already absent")
            owner = self._assert_owned(gateway)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["ownership_mismatch"], retryable=False)
        if bool(spec.config.get("deletion_protection", False)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Gateway deletion protection is enabled; pass force_destroy to delete it",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            children = self._decode_children(gateway)
            stubs = self._owned_child_stubs(parsed, children, owner)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["ownership_mismatch"], retryable=False)
        stubs.append(self._stub(API_VERSION, RESOURCE_KIND, parsed.name, parsed.namespace))
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            stubs,
        )
        if not result.ok:
            return DeprovisionResult(False, spec.handle, "Gateway API deletion failed", result.summary())
        return DeprovisionResult(True, spec.handle, f"Gateway {parsed.name} deletion submitted")

    @driver_op(cloud="k8s_native", driver="api_gateway")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._require_driver()
            parsed = self._parsed(handle.handle)
            gateway = self._gateway(parsed.cluster_id, parsed.namespace, parsed.name)
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        if gateway is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Gateway not found")
        status = dict(gateway.get("status", {}) or {})
        accepted = self._condition(status, "Accepted")
        programmed = self._condition(status, "Programmed")
        failed = next(
            (
                condition
                for condition in (accepted, programmed)
                if condition and str(condition.get("status", "")).lower() == "false"
            ),
            None,
        )
        if failed:
            return ServiceStatus(
                handle.handle,
                "error",
                str(failed.get("message") or failed.get("reason") or "Gateway reconciliation failed"),
            )
        if self._is_true(accepted) and self._is_true(programmed) and status.get("addresses"):
            try:
                route_state, route_detail = self._route_state(parsed, self._decode_children(gateway))
            except ValueError as exc:
                return ServiceStatus(handle.handle, "error", str(exc))
            if route_state != "available":
                return ServiceStatus(handle.handle, route_state, route_detail)
            return ServiceStatus(handle.handle, "available", "Gateway accepted and programmed")
        return ServiceStatus(handle.handle, "provisioning", "Gateway reconciliation in progress")

    @driver_op(cloud="k8s_native", driver="api_gateway")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._require_driver()
        parsed = self._parsed(handle.handle)
        gateway = self._gateway(parsed.cluster_id, parsed.namespace, parsed.name)
        if gateway is None:
            raise ValueError("Gateway does not exist")
        status = dict(gateway.get("status", {}) or {})
        if not self._is_true(self._condition(status, "Accepted")) or not self._is_true(
            self._condition(status, "Programmed"),
        ):
            raise ValueError("Gateway has not been programmed")
        addresses = list(status.get("addresses") or [])
        if not addresses or not addresses[0].get("value"):
            raise ValueError("Gateway has no assigned address")
        host = str(addresses[0]["value"])
        listeners = list((gateway.get("spec", {}) or {}).get("listeners") or [])
        secure = any(str(row.get("protocol") or "").upper() in {"HTTPS", "TLS"} for row in listeners)
        port = next(
            (
                int(row["port"])
                for row in listeners
                if str(row.get("protocol") or "").upper() in ({"HTTPS", "TLS"} if secure else {"HTTP"})
            ),
            443 if secure else 80,
        )
        scheme = "https" if secure else "http"
        port_suffix = "" if (scheme, port) in {("http", 80), ("https", 443)} else f":{port}"
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        url = f"{scheme}://{url_host}{port_suffix}"
        return Binding(
            env_vars={
                "API_GATEWAY_ID": ValueRef(literal=parsed.name),
                "API_GATEWAY_URL": ValueRef(literal=url),
                "API_GATEWAY_HOST": ValueRef(literal=host),
                "API_GATEWAY_PORT": ValueRef(literal=str(port)),
                "API_GATEWAY_NAMESPACE": ValueRef(literal=parsed.namespace),
            },
            notes="Gateway API address; actual reachability is determined by the selected GatewayClass.",
        )

    @driver_op(cloud="k8s_native", driver="api_gateway")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError("Gateway API configuration is declarative and has no snapshot semantic")

    @driver_op(cloud="k8s_native", driver="api_gateway")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise UnsupportedOperationError("reconcile Gateway API resources from their declarative config instead")

    @driver_op(cloud="k8s_native", driver="api_gateway", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        scalar_map = {"type": "object", "additionalProperties": {"type": "string"}}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["listeners"],
            "properties": {
                "gateway_class_name": {"type": "string", "minLength": 1},
                "listeners": {"type": "array", "minItems": 1, "maxItems": 64, "items": {"type": "object"}},
                "addresses": {"type": "array", "items": {"type": "object"}},
                "infrastructure": {"type": "object"},
                "allowed_listeners": {"type": "object"},
                "routes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "name", "spec"],
                        "properties": {
                            "kind": {"type": "string", "enum": sorted(_ROUTE_VERSIONS)},
                            "name": {"type": "string", "minLength": 1, "maxLength": 63},
                            "spec": {"type": "object"},
                            "labels": scalar_map,
                            "annotations": scalar_map,
                        },
                    },
                },
                "resources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "name", "spec"],
                        "properties": {
                            "kind": {"type": "string", "enum": sorted(_AUXILIARY_VERSIONS)},
                            "name": {"type": "string", "minLength": 1, "maxLength": 63},
                            "spec": {"type": "object"},
                            "labels": scalar_map,
                            "annotations": scalar_map,
                        },
                    },
                },
                "labels": scalar_map,
                "annotations": scalar_map,
                "deletion_protection": {"type": "boolean", "default": False},
            },
        }

    @driver_op(cloud="k8s_native", driver="api_gateway", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "API_GATEWAY_ID": "Gateway resource name",
                "API_GATEWAY_URL": "Programmed Gateway base URL",
                "API_GATEWAY_HOST": "Programmed hostname or IP address",
                "API_GATEWAY_PORT": "Selected HTTP or HTTPS listener port",
                "API_GATEWAY_NAMESPACE": "Gateway namespace",
            },
        )

    def editable_fields(self) -> list[str]:
        return sorted(_CONFIG_FIELDS)

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        cfg = copy.deepcopy(raw or {})
        unknown = set(cfg) - _CONFIG_FIELDS
        if unknown:
            raise ValueError("unsupported Gateway config fields: " + ", ".join(sorted(unknown)))
        listeners = cfg.get("listeners")
        if not isinstance(listeners, list) or not listeners or len(listeners) > 64:
            raise ValueError("Gateway listeners must contain between 1 and 64 entries")
        self._validate_listeners(listeners, field_name="Gateway listeners")
        if cfg.get("allowed_listeners") and not self._config.allow_listener_sets:
            raise ValueError("ListenerSet attachment is disabled by cluster policy")
        if cfg.get("addresses") is not None and not isinstance(cfg["addresses"], list):
            raise ValueError("Gateway addresses must be an array")
        if cfg.get("infrastructure") is not None and not isinstance(cfg["infrastructure"], dict):
            raise ValueError("Gateway infrastructure must be an object")
        if cfg.get("allowed_listeners") is not None and not isinstance(cfg["allowed_listeners"], dict):
            raise ValueError("Gateway allowed_listeners must be an object")
        allowed_listener_namespaces = dict((cfg.get("allowed_listeners") or {}).get("namespaces") or {})
        if (
            allowed_listener_namespaces.get("from") in {"All", "Selector"}
            and not self._config.allow_cross_namespace_routes
        ):
            raise ValueError("cross-namespace ListenerSet attachment is disabled by cluster policy")
        self._validate_metadata(cfg.get("labels"), cfg.get("annotations"))
        route_names: set[tuple[str, str]] = set()
        for route in cfg.get("routes") or []:
            if not isinstance(route, dict):
                raise ValueError("Gateway routes must be objects")
            unknown_route = set(route) - {
                "kind",
                "name",
                "spec",
                "labels",
                "annotations",
            }
            if unknown_route:
                raise ValueError("unsupported route fields: " + ", ".join(sorted(unknown_route)))
            kind = str(route.get("kind") or "")
            name = str(route.get("name") or "")
            if kind not in _ROUTE_VERSIONS or not name:
                raise ValueError("routes require a supported kind and non-empty name")
            if kind in _EXPERIMENTAL_ROUTES and not self._config.allow_experimental_routes:
                raise ValueError(f"{kind} requires the cluster experimental-route policy")
            canonical_name = dns_label(name)
            if (kind, canonical_name) in route_names:
                raise ValueError(f"duplicate Gateway route {kind}/{name}")
            spec = route.get("spec")
            if not isinstance(spec, dict):
                raise ValueError("route spec must be an object")
            self._validate_route_refs(spec)
            self._validate_metadata(route.get("labels"), route.get("annotations"))
            route_names.add((kind, canonical_name))
        resource_names: set[tuple[str, str]] = set()
        for resource in cfg.get("resources") or []:
            if not isinstance(resource, dict):
                raise ValueError("Gateway auxiliary resources must be objects")
            unknown_resource = set(resource) - {
                "kind",
                "name",
                "spec",
                "labels",
                "annotations",
            }
            if unknown_resource:
                raise ValueError("unsupported auxiliary resource fields: " + ", ".join(sorted(unknown_resource)))
            kind = str(resource.get("kind") or "")
            name = str(resource.get("name") or "")
            if kind not in _AUXILIARY_VERSIONS or not name or not isinstance(resource.get("spec"), dict):
                raise ValueError("auxiliary resources require a supported kind, name, and object spec")
            if kind == "ListenerSet" and not self._config.allow_listener_sets:
                raise ValueError("ListenerSet resources are disabled by cluster policy")
            if kind == "ListenerSet":
                listener_set_listeners = resource["spec"].get("listeners")
                if not isinstance(listener_set_listeners, list) or not listener_set_listeners:
                    raise ValueError("ListenerSet resources require a non-empty listeners array")
                self._validate_listeners(listener_set_listeners, field_name="ListenerSet listeners")
            if kind == "BackendTLSPolicy":
                targets = resource["spec"].get("targetRefs")
                if not isinstance(targets, list) or not targets:
                    raise ValueError("BackendTLSPolicy requires a non-empty targetRefs array")
                for target in targets:
                    if not isinstance(target, dict):
                        raise ValueError("BackendTLSPolicy targetRefs must contain objects")
                    group = str(target.get("group") or "")
                    target_kind = str(target.get("kind") or "Service")
                    if (group not in {"", "core"} or target_kind != "Service") and not (
                        self._config.allow_custom_backends
                    ):
                        raise ValueError("custom BackendTLSPolicy targets are disabled by cluster policy")
            canonical_name = dns_label(name)
            if (kind, canonical_name) in resource_names:
                raise ValueError(f"duplicate Gateway auxiliary resource {kind}/{name}")
            self._validate_metadata(resource.get("labels"), resource.get("annotations"))
            resource_names.add((kind, canonical_name))
        return cfg

    def _validate_listeners(self, listeners: list[Any], *, field_name: str) -> None:
        names: set[str] = set()
        for listener in listeners:
            if not isinstance(listener, dict):
                raise ValueError(f"{field_name} must be objects")
            name = str(listener.get("name") or "")
            protocol = str(listener.get("protocol") or "").upper()
            port = int(listener.get("port", 0))
            if not _SECTION_NAME.fullmatch(name) or len(name) > 253 or name in names:
                raise ValueError(f"{field_name} names must be non-empty and unique")
            if protocol not in {"HTTP", "HTTPS", "TLS", "TCP", "UDP"} or not 1 <= port <= 65535:
                raise ValueError(f"{field_name} require a supported protocol and valid port")
            if protocol in {"HTTPS", "TLS"} and not isinstance(listener.get("tls"), dict):
                raise ValueError(f"HTTPS and TLS {field_name.lower()} require a tls object")
            certificate_refs = list((listener.get("tls") or {}).get("certificateRefs") or [])
            if (
                any(isinstance(ref, dict) and ref.get("namespace") for ref in certificate_refs)
                and not self._config.allow_cross_namespace_certificates
            ):
                raise ValueError("cross-namespace Gateway certificates are disabled by cluster policy")
            allowed = dict(listener.get("allowedRoutes") or {})
            namespaces = dict(allowed.get("namespaces") or {})
            if namespaces.get("from") in {"All", "Selector"} and not self._config.allow_cross_namespace_routes:
                raise ValueError("cross-namespace Gateway routes are disabled by cluster policy")
            names.add(name)

    def _validate_route_refs(self, value: Any, *, key: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                self._validate_route_refs(item, key=key)
            return
        if not isinstance(value, dict):
            return
        if key in {"backendRef", "backendRefs"} or "backendRef" in key:
            namespace = str(value.get("namespace") or "")
            if namespace and not self._config.allow_cross_namespace_backends:
                raise ValueError("cross-namespace route backends are disabled by cluster policy")
            group = str(value.get("group") or "")
            kind = str(value.get("kind") or "Service")
            if (group not in {"", "core"} or kind != "Service") and not self._config.allow_custom_backends:
                raise ValueError("custom Gateway backends are disabled by cluster policy")
        if value.get("type") == "ExtensionRef" and not self._config.allow_extension_refs:
            raise ValueError("Gateway ExtensionRef filters are disabled by cluster policy")
        for child_key, child in value.items():
            self._validate_route_refs(child, key=str(child_key))

    @staticmethod
    def _validate_metadata(labels: Any, annotations: Any) -> None:
        for field, values, bounded in (("labels", labels, True), ("annotations", annotations, False)):
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
        namespace: str,
        name: str,
        cfg: dict[str, Any],
        owner: dict[str, str],
        cluster_id: str,
    ) -> list[dict[str, Any]]:
        labels = self._labels(cfg.get("labels"), owner)
        children: list[dict[str, str]] = []
        routes: list[dict[str, Any]] = []
        for route in cfg.get("routes") or []:
            route_name = dns_label(route["name"])
            route_spec = copy.deepcopy(route["spec"])
            parent_refs = route_spec.get("parentRefs")
            if parent_refs is None:
                route_spec["parentRefs"] = [{"name": name}]
            else:
                self._validate_parent_refs(parent_refs, name, namespace)
            route_labels = self._labels(route.get("labels"), owner)
            manifest = {
                "apiVersion": _ROUTE_VERSIONS[route["kind"]],
                "kind": route["kind"],
                "metadata": {
                    "name": route_name,
                    "namespace": namespace,
                    "labels": route_labels,
                    "annotations": dict(route.get("annotations") or {}),
                },
                "spec": route_spec,
            }
            self._assert_child_adoptable(
                cluster_id,
                namespace,
                manifest,
                dns_label(owner[_OWNER_ID]),
            )
            children.append(self._child_ref(manifest))
            routes.append(manifest)
        resources: list[dict[str, Any]] = []
        for resource in cfg.get("resources") or []:
            resource_name = dns_label(resource["name"])
            resource_spec = copy.deepcopy(resource["spec"])
            if resource["kind"] == "ListenerSet":
                self._validate_listener_set_parent(resource_spec, name, namespace)
            manifest = {
                "apiVersion": _AUXILIARY_VERSIONS[resource["kind"]],
                "kind": resource["kind"],
                "metadata": {
                    "name": resource_name,
                    "namespace": namespace,
                    "labels": self._labels(resource.get("labels"), owner),
                    "annotations": dict(resource.get("annotations") or {}),
                },
                "spec": resource_spec,
            }
            self._assert_child_adoptable(
                cluster_id,
                namespace,
                manifest,
                dns_label(owner[_OWNER_ID]),
            )
            children.append(self._child_ref(manifest))
            resources.append(manifest)
        annotations = dict(cfg.get("annotations") or {})
        annotations[_CHILDREN] = json.dumps(children, sort_keys=True, separators=(",", ":"))
        gateway_spec: dict[str, Any] = {
            "gatewayClassName": self._gateway_class(cfg),
            "listeners": copy.deepcopy(cfg["listeners"]),
        }
        for source, target in (
            ("addresses", "addresses"),
            ("infrastructure", "infrastructure"),
            ("allowed_listeners", "allowedListeners"),
        ):
            if cfg.get(source) not in (None, [], {}):
                gateway_spec[target] = copy.deepcopy(cfg[source])
        gateway = {
            "apiVersion": API_VERSION,
            "kind": RESOURCE_KIND,
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
            },
            "spec": gateway_spec,
        }
        return [gateway, *routes, *resources]

    def _gateway_class(self, cfg: dict[str, Any]) -> str:
        configured = str(cfg.get("gateway_class_name") or "")
        default = self._config.gateway_class_name
        if configured and configured != default and default and not self._config.allow_class_override:
            raise ValueError("gateway_class_name override is disabled by cluster policy")
        value = configured or default
        if not value:
            raise ValueError("Gateway API requires an installed gateway_class_name")
        return value

    def _validate_parent_refs(self, refs: Any, name: str, namespace: str) -> None:
        if not isinstance(refs, list) or not refs:
            raise ValueError("route parentRefs must be a non-empty array")
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError("route parentRefs must contain objects")
            if str(ref.get("name") or "") != name:
                raise ValueError("managed routes may attach only to their Astrolift Gateway")
            if str(ref.get("namespace") or namespace) != namespace:
                raise ValueError("managed routes must remain in the Gateway namespace")
            if str(ref.get("group") or "gateway.networking.k8s.io") != "gateway.networking.k8s.io":
                raise ValueError("managed routes require a Gateway API parent")
            if str(ref.get("kind") or "Gateway") != "Gateway":
                raise ValueError("managed routes require a Gateway parent")

    @staticmethod
    def _validate_listener_set_parent(spec: dict[str, Any], name: str, namespace: str) -> None:
        parent = spec.get("parentRef")
        if not isinstance(parent, dict):
            raise ValueError("ListenerSet requires an object parentRef")
        if str(parent.get("name") or "") != name:
            raise ValueError("managed ListenerSets may attach only to their Astrolift Gateway")
        if str(parent.get("namespace") or namespace) != namespace:
            raise ValueError("managed ListenerSets must remain in the Gateway namespace")
        if str(parent.get("group") or "gateway.networking.k8s.io") != "gateway.networking.k8s.io":
            raise ValueError("managed ListenerSets require a Gateway API parent")
        if str(parent.get("kind") or "Gateway") != "Gateway":
            raise ValueError("managed ListenerSets require a Gateway parent")

    def _assert_adoptable(
        self,
        *,
        cluster_id: str,
        namespace: str,
        name: str,
        managed_service_id: str,
    ) -> None:
        current = self._gateway(cluster_id, namespace, name)
        if current is None:
            return
        metadata = dict(current.get("metadata", {}) or {})
        labels = dict(metadata.get("labels", {}) or {})
        owner = str(labels.get(_OWNER_ID) or "")
        expected_owner = dns_label(managed_service_id)
        if labels.get(_OWNER) == "astrolift" and owner == expected_owner:
            return
        if labels.get(_OWNER) == "astrolift" and owner:
            raise ValueError("Gateway belongs to another Astrolift managed resource")
        # An object's uid is a precondition, not an authorization: in an
        # operator-fixed shared namespace the object may be anyone's. Adoption
        # of an existing resource is a separate, operator-authorized operation
        # (#1365) that no tenant config flag may grant (#2021).
        raise ValueError(
            "Gateway already exists and is not owned by this managed service; adoption is a separate, "
            "operator-authorized operation and cannot be granted by tenant config",
        )

    def _assert_child_adoptable(
        self,
        cluster_id: str,
        namespace: str,
        manifest: dict[str, Any],
        owner_id: str,
    ) -> None:
        current = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            f"{manifest['apiVersion']}/{manifest['kind']}",
            manifest["metadata"]["name"],
        )
        if current is None:
            return
        metadata = dict(current.get("metadata", {}) or {})
        labels = dict(metadata.get("labels", {}) or {})
        if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID) == owner_id:
            return
        if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID):
            raise ValueError(f"{manifest['kind']} {manifest['metadata']['name']} belongs to another resource")
        raise ValueError(
            f"{manifest['kind']} {manifest['metadata']['name']} already exists and is not owned by this "
            "managed service; adoption is a separate, operator-authorized operation and cannot be granted "
            "by tenant config",
        )

    def _assert_owned(self, resource: dict[str, Any]) -> str:
        labels = dict((resource.get("metadata", {}) or {}).get("labels", {}) or {})
        owner = str(labels.get(_OWNER_ID) or "")
        if labels.get(_OWNER) != "astrolift" or not owner:
            raise ValueError("Gateway is not owned by an Astrolift managed resource")
        return owner

    def _owned_child_stubs(
        self,
        parsed: ParsedHandle,
        children: list[dict[str, str]],
        owner: str,
    ) -> list[dict[str, Any]]:
        stubs: list[dict[str, Any]] = []
        for ref in children:
            current = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                f"{ref['apiVersion']}/{ref['kind']}",
                ref["name"],
            )
            if current is None:
                continue
            labels = dict((current.get("metadata", {}) or {}).get("labels", {}) or {})
            if labels.get(_OWNER) != "astrolift" or labels.get(_OWNER_ID) != owner:
                raise ValueError(f"refusing to delete foreign Gateway child {ref['kind']}/{ref['name']}")
            stubs.append(self._stub(ref["apiVersion"], ref["kind"], ref["name"], parsed.namespace))
        return stubs

    def _stale_children(
        self,
        gateway: dict[str, Any],
        desired: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        desired_refs = {json.dumps(self._child_ref(row), sort_keys=True) for row in desired[1:]}
        return [ref for ref in self._decode_children(gateway) if json.dumps(ref, sort_keys=True) not in desired_refs]

    def _route_state(self, parsed: ParsedHandle, children: list[dict[str, str]]) -> tuple[str, str]:
        for ref in children:
            if ref["kind"] not in _ROUTE_VERSIONS:
                continue
            route = self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                f"{ref['apiVersion']}/{ref['kind']}",
                ref["name"],
            )
            if route is None:
                return "error", f"managed route {ref['kind']}/{ref['name']} is missing"
            parents = list((route.get("status", {}) or {}).get("parents") or [])
            conditions = [condition for parent in parents for condition in parent.get("conditions", []) or []]
            failed = next(
                (
                    condition
                    for condition in conditions
                    if condition.get("type") in {"Accepted", "ResolvedRefs"}
                    and str(condition.get("status") or "").lower() == "false"
                ),
                None,
            )
            if failed:
                return "error", str(
                    failed.get("message") or failed.get("reason") or f"{ref['kind']} was rejected",
                )
            accepted = next((row for row in conditions if row.get("type") == "Accepted"), None)
            resolved = next((row for row in conditions if row.get("type") == "ResolvedRefs"), None)
            if not self._is_true(accepted) or not self._is_true(resolved):
                return "provisioning", f"managed route {ref['kind']}/{ref['name']} is reconciling"
        return "available", "all managed routes are accepted"

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

    @staticmethod
    def _child_ref(manifest: dict[str, Any]) -> dict[str, str]:
        return {
            "apiVersion": str(manifest["apiVersion"]),
            "kind": str(manifest["kind"]),
            "name": str(manifest["metadata"]["name"]),
        }

    @staticmethod
    def _decode_children(gateway: dict[str, Any]) -> list[dict[str, str]]:
        raw = str(((gateway.get("metadata", {}) or {}).get("annotations", {}) or {}).get(_CHILDREN) or "[]")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Gateway managed-child inventory is invalid") from exc
        if not isinstance(value, list) or any(
            not isinstance(row, dict) or set(row) != {"apiVersion", "kind", "name"} for row in value
        ):
            raise ValueError("Gateway managed-child inventory is invalid")
        return [{str(key): str(item) for key, item in row.items()} for row in value]

    def _require_driver(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("Gateway API requires a live cluster driver")

    @staticmethod
    def _parsed(handle: str) -> ParsedHandle:
        parsed = _unpack_handle(handle)
        if parsed.is_legacy:
            raise ValueError("legacy Gateway handle has no cluster locator")
        return parsed

    def _gateway(self, cluster_id: str, namespace: str, name: str) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._config.cluster_driver.get_manifest(
                cluster_id,
                namespace,
                f"{API_VERSION}/{RESOURCE_KIND}",
                name,
            ),
        )

    @staticmethod
    def _condition(status: dict[str, Any], condition_type: str) -> dict[str, Any] | None:
        return next(
            (row for row in status.get("conditions", []) or [] if row.get("type") == condition_type),
            None,
        )

    @staticmethod
    def _is_true(condition: dict[str, Any] | None) -> bool:
        return bool(condition and str(condition.get("status") or "").lower() == "true")

    @staticmethod
    def _stub(api_version: str, kind: str, name: str, namespace: str) -> dict[str, Any]:
        return {
            "apiVersion": api_version,
            "kind": kind,
            "metadata": {"name": name, "namespace": namespace},
        }
