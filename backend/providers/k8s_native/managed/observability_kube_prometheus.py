"""Project-scoped monitoring resources for an existing kube-prometheus-stack."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

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

KIND = "observability"
VARIANT = "kube_prometheus_stack"

_OWNER = "app.kubernetes.io/managed-by"
_OWNER_ID = "astrolift.io/managed-service-id"
_COMPONENT = "astrolift.io/component"
_CHILDREN = "astrolift.io/children"
_DELETION_PROTECTION = "astrolift.io/deletion-protection"
_ROOT_KIND = "v1/ConfigMap"
_DASHBOARD_LABEL_KEY = "grafana_dashboard"
_DASHBOARD_LABEL_VALUE = "1"
_PROMETHEUS_ACCESS_LABEL = "astrolift.io/trusted-observability-access"
_REQUIRED_CRDS = (
    "prometheuses.monitoring.coreos.com",
    "prometheusrules.monitoring.coreos.com",
    "servicemonitors.monitoring.coreos.com",
    "podmonitors.monitoring.coreos.com",
)
_LABEL_KEY = re.compile(
    r"^(?:[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$",
)
_LABEL_VALUE = re.compile(r"^(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_PROM_NAME = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*$")
_DURATION = re.compile(r"^([1-9][0-9]*)(ms|s|m|h)$")
_CONFIG_FIELDS = {
    "service_monitors",
    "pod_monitors",
    "rule_groups",
    "dashboards",
    "standard_rules",
    "default_dashboard",
    "deletion_protection",
}
_MONITOR_FIELDS = {
    "name",
    "selector",
    "namespace_names",
    "endpoints",
    "labels",
    "sample_limit",
    "target_limit",
}
_ENDPOINT_FIELDS = {
    "port",
    "path",
    "scheme",
    "interval",
    "scrape_timeout",
    "honor_labels",
    "honor_timestamps",
}
_RULE_FIELDS = {"alert", "record", "expr", "for", "keep_firing_for", "labels", "annotations"}


@dataclass(frozen=True)
class KubePrometheusConfig:
    cluster_driver: Any = None
    monitoring_namespace: str = "astrolift-system"
    prometheus_service_name: str = "astrolift-kube-prometheus-prometheus"
    alertmanager_service_name: str = "astrolift-kube-prometheus-alertmanager"
    grafana_service_name: str = "astrolift-kube-prometheus-stack-grafana"
    prometheus_url: str = ""
    grafana_url: str = ""
    verify_crds: bool = True
    verify_services: bool = True
    verify_selection: bool = True
    allow_workload_prometheus_access: bool = False
    allow_cross_namespace: bool = False
    allowed_target_namespaces: tuple[str, ...] = ()
    allow_custom_rules: bool = False
    allow_custom_dashboards: bool = False
    allow_honor_labels: bool = False
    min_scrape_interval_seconds: int = 15
    max_monitors: int = 20
    max_endpoints_per_monitor: int = 10
    max_samples_per_scrape: int = 50_000
    max_targets_per_monitor: int = 100
    max_rule_groups: int = 20
    max_rules: int = 100
    max_dashboards: int = 10
    max_dashboard_bytes: int = 512_000


class KubePrometheusStackDriver(ManagedServiceDriver):
    """Own project monitors, rules, and dashboards, not the shared stack."""

    def __init__(self, *, config: KubePrometheusConfig) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="kube_prometheus_stack",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        handle = ""
        try:
            self._require_driver()
            if not spec.managed_service_id:
                raise ValueError("kube-prometheus bundle requires a managed_service_id")
            namespace = app_namespace(
                organization_slug=spec.organization_slug,
                app_slug=spec.app_slug,
            )
            name = dns_label(
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "monitoring",
            )
            handle = _pack_handle(
                kind=KIND,
                cluster_id=spec.tenant_cluster_id,
                namespace=namespace,
                name=name,
            )
            owner = {
                "managed_service_id": spec.managed_service_id,
                "organization": spec.organization_slug,
                "app": spec.app_slug,
                "environment": spec.environment_name,
            }
            cfg = self._normalize(spec.config, namespace=namespace)
            self._preflight(spec.tenant_cluster_id)
            current = self._root(spec.tenant_cluster_id, namespace, name)
            self._assert_adoptable(current, spec.managed_service_id)
            manifests = self._manifests(namespace=namespace, name=name, cfg=cfg, owner=owner)
            self._assert_children_adoptable(spec.tenant_cluster_id, namespace, manifests[1:], owner)
            stale = self._stale_children(current, manifests)
            stale_stubs = self._owned_child_stubs(
                ParsedHandle(KIND, spec.tenant_cluster_id, namespace, name),
                stale,
                owner["managed_service_id"],
            )
            transition_root = self._transition_root(current, manifests[0])
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, handle, str(exc), ["invalid_kube_prometheus_config"])

        error = self._reconcile(
            cluster_id=spec.tenant_cluster_id,
            namespace=namespace,
            transition_root=transition_root,
            desired=manifests,
            stale_stubs=stale_stubs,
        )
        if error is not None:
            message, errors = error
            return ProvisionResult(False, handle, message, errors)
        return ProvisionResult(
            True,
            handle,
            f"kube-prometheus bundle {name} reconciled with {len(manifests) - 1} project resources",
            ready=True,
        )

    @driver_op(cloud="k8s_native", driver="kube_prometheus_stack")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require_driver()
            parsed = self._parsed(spec.handle)
            current = self._root(parsed.cluster_id, parsed.namespace, parsed.name)
            if current is None:
                return UpdateResult(
                    False,
                    spec.handle,
                    "kube-prometheus bundle does not exist",
                    ["resource_not_found"],
                    retryable=False,
                )
            owner = self._owner(current)
            cfg = self._normalize(spec.config, namespace=parsed.namespace)
            self._preflight(parsed.cluster_id)
            manifests = self._manifests(
                namespace=parsed.namespace,
                name=parsed.name,
                cfg=cfg,
                owner=owner,
            )
            self._assert_children_adoptable(parsed.cluster_id, parsed.namespace, manifests[1:], owner)
            stale = self._stale_children(current, manifests)
            stale_stubs = self._owned_child_stubs(
                parsed,
                stale,
                owner["managed_service_id"],
            )
            transition_root = self._transition_root(current, manifests[0])
        except (TypeError, ValueError) as exc:
            return UpdateResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_kube_prometheus_update"],
                retryable=False,
            )
        error = self._reconcile(
            cluster_id=parsed.cluster_id,
            namespace=parsed.namespace,
            transition_root=transition_root,
            desired=manifests,
            stale_stubs=stale_stubs,
        )
        if error is not None:
            message, errors = error
            return UpdateResult(False, spec.handle, message, errors)
        return UpdateResult(True, spec.handle, f"kube-prometheus bundle {parsed.name} reconciled")

    @driver_op(
        cloud="k8s_native",
        driver="kube_prometheus_stack",
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
        if delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "project bundles cannot delete metrics or shared kube-prometheus data",
                ["shared_observability_data_deletion_unsupported"],
                retryable=False,
            )
        try:
            self._require_driver()
            parsed = self._parsed(spec.handle)
            root = self._root(parsed.cluster_id, parsed.namespace, parsed.name)
            if root is None:
                return DeprovisionResult(True, spec.handle, "kube-prometheus bundle already absent")
            owner = self._owner(root)
            annotations = dict((root.get("metadata", {}) or {}).get("annotations", {}) or {})
            if annotations.get(_DELETION_PROTECTION) == "true" and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "kube-prometheus bundle deletion protection is enabled",
                    ["deletion_protection_enabled"],
                    retryable=False,
                )
            children = self._decode_children(root)
            child_stubs = self._owned_child_stubs(parsed, children, owner["managed_service_id"])
        except ValueError as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_kube_prometheus_bundle"],
                retryable=False,
            )
        if child_stubs:
            result = self._config.cluster_driver.delete_manifests(
                parsed.cluster_id,
                parsed.namespace,
                child_stubs,
            )
            if not result.ok:
                return DeprovisionResult(False, spec.handle, "kube-prometheus unlink failed", result.summary())
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [self._stub("v1", "ConfigMap", parsed.name, parsed.namespace)],
        )
        if not result.ok:
            return DeprovisionResult(False, spec.handle, "kube-prometheus unlink failed", result.summary())
        return DeprovisionResult(
            True,
            spec.handle,
            "project monitors, rules, and dashboards removed; shared metrics data and stack retained",
        )

    @driver_op(cloud="k8s_native", driver="kube_prometheus_stack")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._require_driver()
            parsed = self._parsed(handle.handle)
            root = self._root(parsed.cluster_id, parsed.namespace, parsed.name)
            if root is None:
                return ServiceStatus(handle.handle, "deprovisioned", "kube-prometheus bundle not found")
            self._preflight(parsed.cluster_id)
            owner = self._owner(root)
            children = self._decode_children(root)
            missing: list[str] = []
            for ref in children:
                child = self._config.cluster_driver.get_manifest(
                    parsed.cluster_id,
                    parsed.namespace,
                    f"{ref['apiVersion']}/{ref['kind']}",
                    ref["name"],
                )
                if child is None:
                    missing.append(f"{ref['kind']}/{ref['name']}")
                    continue
                self._assert_child_owned(child, owner["managed_service_id"])
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        if missing:
            return ServiceStatus(handle.handle, "error", "missing project monitoring resources: " + ", ".join(missing))
        return ServiceStatus(
            handle.handle,
            "available",
            f"kube-prometheus bundle has {len(children)} project resources; "
            "shared data-plane reachability is not probed",
        )

    @driver_op(cloud="k8s_native", driver="kube_prometheus_stack")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._require_driver()
        parsed = self._parsed(handle.handle)
        root = self._root(parsed.cluster_id, parsed.namespace, parsed.name)
        if root is None:
            raise ValueError("kube-prometheus bundle does not exist")
        self._preflight(parsed.cluster_id)
        owner = self._owner(root)
        self._bundle_document(root)
        env_vars = {
            "OBSERVABILITY_PROVIDER": ValueRef(literal="kube-prometheus-stack"),
            "DASHBOARD_URL": ValueRef(literal=self._dashboard_url(root, owner)),
            "GRAFANA_URL": ValueRef(literal=self._grafana_url()),
            "OBSERVABILITY_NAMESPACE": ValueRef(literal=parsed.namespace),
            "OBSERVABILITY_BUNDLE": ValueRef(literal=parsed.name),
        }
        if self._config.allow_workload_prometheus_access:
            namespace = self._config.cluster_driver.get_namespace(parsed.cluster_id, parsed.namespace)
            if namespace is None or namespace.labels.get(_PROMETHEUS_ACCESS_LABEL) != "true":
                raise ValueError(
                    "direct Prometheus access requires the cluster operator to label the workload namespace "
                    f"{_PROMETHEUS_ACCESS_LABEL}=true",
                )
            env_vars.update(
                {
                    "METRICS_ENDPOINT": ValueRef(literal=self._prometheus_url()),
                    "PROMETHEUS_URL": ValueRef(literal=self._prometheus_url()),
                },
            )
        return Binding(
            env_vars=env_vars,
            notes=(
                "Project monitors, rules, and dashboards use the shared operator-owned kube-prometheus-stack. "
                + (
                    "Direct Prometheus access is enabled by trusted operator policy; the endpoint has cluster-wide "
                    "read visibility and carries no credentials. "
                    if self._config.allow_workload_prometheus_access
                    else "Direct Prometheus access is denied by default. "
                )
                + "Alertmanager is operator-only and is never exposed to workload bindings. Grafana must enforce "
                "authentication and authorization at its own boundary."
            ),
        )

    @driver_op(cloud="k8s_native", driver="kube_prometheus_stack")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError(
            "kube-prometheus project bundles are declarative configuration; metrics data snapshots are unsupported",
        )

    @driver_op(cloud="k8s_native", driver="kube_prometheus_stack")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise UnsupportedOperationError(
            "kube-prometheus metrics restore is unsupported; reconcile the project bundle from configuration",
        )

    @driver_op(cloud="k8s_native", driver="kube_prometheus_stack", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        label_map = {
            "type": "object",
            "propertyNames": {"type": "string", "pattern": _LABEL_KEY.pattern, "maxLength": 253},
            "additionalProperties": {
                "type": "string",
                "pattern": _LABEL_VALUE.pattern,
                "maxLength": 63,
            },
        }
        tenant_label_map = {
            **label_map,
            "propertyNames": {
                "allOf": [
                    label_map["propertyNames"],
                    {
                        "not": {
                            "anyOf": [
                                {"pattern": r"^astrolift\.io/"},
                                {"enum": [_OWNER]},
                            ],
                        },
                    },
                ],
            },
        }
        rule_metadata_map = {
            "type": "object",
            "propertyNames": {"not": {"pattern": r"^astrolift\.io/"}},
            "additionalProperties": {"type": "string"},
        }
        selector_expression = {
            "type": "object",
            "additionalProperties": False,
            "required": ["key", "operator"],
            "properties": {
                "key": {"type": "string", "pattern": _LABEL_KEY.pattern},
                "operator": {"type": "string", "enum": ["In", "NotIn", "Exists", "DoesNotExist"]},
                "values": {"type": "array", "items": {"type": "string"}},
            },
            "allOf": [
                {
                    "if": {"properties": {"operator": {"enum": ["In", "NotIn"]}}},
                    "then": {"required": ["values"], "properties": {"values": {"minItems": 1}}},
                },
                {
                    "if": {"properties": {"operator": {"enum": ["Exists", "DoesNotExist"]}}},
                    "then": {"properties": {"values": {"maxItems": 0}}},
                },
            ],
        }
        selector = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "match_labels": {**label_map, "minProperties": 1},
                "match_expressions": {
                    "type": "array",
                    "items": selector_expression,
                    "minItems": 1,
                },
            },
            "anyOf": [
                {"required": ["match_labels"]},
                {"required": ["match_expressions"]},
            ],
        }
        endpoint = {
            "type": "object",
            "additionalProperties": False,
            "required": ["port"],
            "properties": {
                "port": {"type": "string", "pattern": _DNS_LABEL.pattern, "maxLength": 63},
                "path": {
                    "type": "string",
                    "pattern": r"^/(?!.*(?:^|/)\.\.(?:/|$))[^?#]*$",
                    "maxLength": 512,
                    "default": "/metrics",
                },
                "scheme": {"type": "string", "enum": ["http", "https"], "default": "http"},
                "interval": {"type": "string", "pattern": _DURATION.pattern, "default": "30s"},
                "scrape_timeout": {"type": "string", "pattern": _DURATION.pattern, "default": "10s"},
                "honor_labels": {"type": "boolean", "default": False},
                "honor_timestamps": {"type": "boolean", "default": True},
            },
        }
        monitor = {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "selector", "endpoints"],
            "properties": {
                "name": {"type": "string", "pattern": r"\S"},
                "selector": selector,
                "namespace_names": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "pattern": _DNS_LABEL.pattern, "maxLength": 63},
                },
                "endpoints": {"type": "array", "minItems": 1, "items": endpoint},
                "labels": tenant_label_map,
                "sample_limit": {"type": "integer", "minimum": 1},
                "target_limit": {"type": "integer", "minimum": 1},
            },
        }
        rule = {
            "type": "object",
            "additionalProperties": False,
            "required": ["expr"],
            "properties": {
                "alert": {"type": "string", "pattern": _PROM_NAME.pattern},
                "record": {"type": "string", "pattern": _PROM_NAME.pattern},
                "expr": {"type": "string", "pattern": r"\S", "maxLength": 16_384},
                "for": {"type": "string", "pattern": _DURATION.pattern},
                "keep_firing_for": {"type": "string", "pattern": _DURATION.pattern},
                "labels": rule_metadata_map,
                "annotations": rule_metadata_map,
            },
            "oneOf": [{"required": ["alert"]}, {"required": ["record"]}],
        }
        rule_group = {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "rules"],
            "properties": {
                "name": {"type": "string", "pattern": r"\S"},
                "interval": {"type": "string", "pattern": _DURATION.pattern},
                "rules": {"type": "array", "minItems": 1, "items": rule},
            },
        }
        dashboard = {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "document"],
            "properties": {
                "name": {"type": "string", "pattern": r"\S"},
                "document": {"type": "object"},
            },
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "service_monitors": {"type": "array", "items": monitor},
                "pod_monitors": {"type": "array", "items": monitor},
                "rule_groups": {"type": "array", "items": rule_group},
                "dashboards": {"type": "array", "items": dashboard},
                "standard_rules": {"type": "boolean", "default": True},
                "default_dashboard": {"type": "boolean", "default": True},
                "deletion_protection": {"type": "boolean", "default": False},
            },
        }

    @driver_op(cloud="k8s_native", driver="kube_prometheus_stack", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "OBSERVABILITY_PROVIDER": "Portable provider identifier",
                "METRICS_ENDPOINT": "Trusted-operator opt-in Prometheus HTTP endpoint",
                "DASHBOARD_URL": "Portable Grafana dashboard base URL",
                "PROMETHEUS_URL": "Trusted-operator opt-in Prometheus HTTP endpoint",
                "GRAFANA_URL": "Grafana HTTP endpoint",
                "OBSERVABILITY_NAMESPACE": "Project namespace containing monitoring resources",
                "OBSERVABILITY_BUNDLE": "Project observability bundle name",
            },
        )

    @driver_op(cloud="k8s_native", driver="kube_prometheus_stack", heartbeat=False)
    def editable_fields(self) -> list[str]:
        return sorted(_CONFIG_FIELDS)

    def _normalize(self, raw: dict[str, Any], *, namespace: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("kube-prometheus config must be an object")
        unknown = sorted(set(raw) - _CONFIG_FIELDS)
        if unknown:
            raise ValueError(f"kube-prometheus config contains unsupported fields: {unknown}")
        for field in ("standard_rules", "default_dashboard", "deletion_protection"):
            if field in raw and not isinstance(raw[field], bool):
                raise ValueError(f"kube-prometheus {field} must be a boolean")
        service_monitors = self._normalize_monitors(raw.get("service_monitors"), namespace, "ServiceMonitor")
        pod_monitors = self._normalize_monitors(raw.get("pod_monitors"), namespace, "PodMonitor")
        if len(service_monitors) + len(pod_monitors) > self._config.max_monitors:
            raise ValueError("kube-prometheus monitor count exceeds cluster policy")
        rule_groups = self._normalize_rule_groups(raw.get("rule_groups"))
        dashboards = self._normalize_dashboards(raw.get("dashboards"))
        standard_rules = bool(raw.get("standard_rules", True))
        default_dashboard = bool(raw.get("default_dashboard", True))
        if standard_rules and any(group["name"] == "astrolift-workload-health" for group in rule_groups):
            raise ValueError("custom Prometheus rule group collides with the standard rule group")
        if default_dashboard and any(dashboard["name"] == "overview" for dashboard in dashboards):
            raise ValueError("custom Grafana dashboard collides with the default overview dashboard")
        return {
            "service_monitors": service_monitors,
            "pod_monitors": pod_monitors,
            "rule_groups": rule_groups,
            "dashboards": dashboards,
            "standard_rules": standard_rules,
            "default_dashboard": default_dashboard,
            "deletion_protection": bool(raw.get("deletion_protection", False)),
        }

    def _normalize_monitors(self, raw: Any, namespace: str, kind: str) -> list[dict[str, Any]]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ValueError(f"{kind} declarations must be an array")
        normalized: list[dict[str, Any]] = []
        names: set[str] = set()
        for item in raw:
            if not isinstance(item, dict) or set(item) - _MONITOR_FIELDS:
                raise ValueError(f"{kind} declaration has unsupported fields")
            raw_name = str(item.get("name") or "")
            name = dns_label(raw_name)
            if not raw_name or name in names:
                raise ValueError(f"{kind} names must be non-empty and unique")
            names.add(name)
            selector = self._selector(item.get("selector"))
            namespaces = self._target_namespaces(item.get("namespace_names"), namespace)
            endpoints = item.get("endpoints")
            if not isinstance(endpoints, list) or not endpoints:
                raise ValueError(f"{kind} {name} requires at least one endpoint")
            if len(endpoints) > self._config.max_endpoints_per_monitor:
                raise ValueError(f"{kind} {name} endpoint count exceeds cluster policy")
            monitor: dict[str, Any] = {
                "name": name,
                "selector": selector,
                "namespace_names": namespaces,
                "endpoints": [self._endpoint(endpoint, kind) for endpoint in endpoints],
                "labels": self._metadata_labels(item.get("labels")),
            }
            for field, limit in (
                ("sample_limit", self._config.max_samples_per_scrape),
                ("target_limit", self._config.max_targets_per_monitor),
            ):
                value = item.get(field, limit)
                if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > limit:
                    raise ValueError(f"{kind} {field} must be between 1 and cluster policy limit {limit}")
                monitor[field] = value
            normalized.append(monitor)
        return normalized

    def _selector(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) - {"match_labels", "match_expressions"}:
            raise ValueError("monitor selector must contain match_labels and/or match_expressions")
        labels = raw.get("match_labels", {})
        expressions = raw.get("match_expressions", [])
        if not isinstance(labels, dict) or not isinstance(expressions, list) or (not labels and not expressions):
            raise ValueError("monitor selector cannot be empty")
        normalized_labels = self._metadata_labels(labels, allow_reserved=True)
        normalized_expressions: list[dict[str, Any]] = []
        for expression in expressions:
            if not isinstance(expression, dict) or set(expression) - {"key", "operator", "values"}:
                raise ValueError("monitor selector expression is invalid")
            key = str(expression.get("key") or "")
            operator = str(expression.get("operator") or "")
            values = expression.get("values", [])
            self._validate_label_key(key)
            if operator not in {"In", "NotIn", "Exists", "DoesNotExist"}:
                raise ValueError("monitor selector expression operator is invalid")
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError("monitor selector expression values must be strings")
            if (operator in {"In", "NotIn"} and not values) or (operator in {"Exists", "DoesNotExist"} and values):
                raise ValueError("monitor selector expression values do not match its operator")
            normalized_expressions.append({"key": key, "operator": operator, "values": list(values)})
        selector: dict[str, Any] = {}
        if normalized_labels:
            selector["matchLabels"] = normalized_labels
        if normalized_expressions:
            selector["matchExpressions"] = normalized_expressions
        return selector

    def _target_namespaces(self, raw: Any, project_namespace: str) -> list[str]:
        names = [project_namespace] if raw is None else raw
        if not isinstance(names, list) or not names or any(not isinstance(value, str) or not value for value in names):
            raise ValueError("monitor namespace_names must be a non-empty string array")
        result = sorted(set(names))
        for name in result:
            if len(name) > 63 or not _DNS_LABEL.fullmatch(name):
                raise ValueError(f"monitor target namespace {name!r} is invalid")
            if name == project_namespace:
                continue
            if not self._config.allow_cross_namespace or name not in self._config.allowed_target_namespaces:
                raise ValueError(f"cross-namespace scrape target {name!r} is blocked by cluster policy")
        return result

    def _endpoint(self, raw: Any, kind: str) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) - _ENDPOINT_FIELDS:
            raise ValueError(f"{kind} endpoint has unsupported fields")
        port = raw.get("port")
        if not isinstance(port, str) or len(port) > 63 or not _DNS_LABEL.fullmatch(port):
            raise ValueError(f"{kind} endpoint port must be a non-empty named port")
        path = str(raw.get("path") or "/metrics")
        if not path.startswith("/") or ".." in path.split("/") or "?" in path or "#" in path or len(path) > 512:
            raise ValueError(f"{kind} endpoint path must be an absolute non-traversing path")
        scheme = str(raw.get("scheme") or "http")
        if scheme not in {"http", "https"}:
            raise ValueError(f"{kind} endpoint scheme must be http or https")
        interval = str(raw.get("interval") or "30s")
        interval_ms = self._duration_ms(interval)
        timeout = str(raw.get("scrape_timeout") or (interval if interval_ms < 10_000 else "10s"))
        timeout_ms = self._duration_ms(timeout)
        if interval_ms < self._config.min_scrape_interval_seconds * 1000:
            raise ValueError(f"{kind} endpoint interval is below cluster policy")
        if timeout_ms > interval_ms:
            raise ValueError(f"{kind} scrape_timeout cannot exceed interval")
        honor_labels = raw.get("honor_labels", False)
        honor_timestamps = raw.get("honor_timestamps", True)
        if not isinstance(honor_labels, bool) or not isinstance(honor_timestamps, bool):
            raise ValueError(f"{kind} honor flags must be booleans")
        if honor_labels and not self._config.allow_honor_labels:
            raise ValueError(f"{kind} honor_labels is blocked by cluster policy")
        endpoint: dict[str, Any] = {
            "port": port,
            "path": path,
            "scheme": scheme,
            "interval": interval,
            "scrapeTimeout": timeout,
            "honorLabels": honor_labels,
            "honorTimestamps": honor_timestamps,
            "followRedirects": False,
        }
        return endpoint

    def _normalize_rule_groups(self, raw: Any) -> list[dict[str, Any]]:
        if raw in (None, []):
            return []
        if not self._config.allow_custom_rules:
            raise ValueError("custom Prometheus rules are blocked by cluster policy")
        if not isinstance(raw, list) or len(raw) > self._config.max_rule_groups:
            raise ValueError("Prometheus rule_groups must be an array within cluster limits")
        result: list[dict[str, Any]] = []
        total = 0
        names: set[str] = set()
        for group in raw:
            if not isinstance(group, dict) or set(group) - {"name", "interval", "rules"}:
                raise ValueError("Prometheus rule group has unsupported fields")
            raw_name = str(group.get("name") or "")
            name = dns_label(raw_name)
            rules = group.get("rules")
            if not raw_name or name in names or not isinstance(rules, list) or not rules:
                raise ValueError("Prometheus rule groups require unique names and non-empty rules")
            names.add(name)
            total += len(rules)
            if total > self._config.max_rules:
                raise ValueError("Prometheus rule count exceeds cluster policy")
            normalized = {"name": name, "rules": [self._rule(rule) for rule in rules]}
            if group.get("interval") is not None:
                interval = str(group["interval"])
                if self._duration_ms(interval) < self._config.min_scrape_interval_seconds * 1000:
                    raise ValueError("Prometheus rule interval is below cluster policy")
                normalized["interval"] = interval
            result.append(normalized)
        return result

    def _rule(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) - _RULE_FIELDS:
            raise ValueError("Prometheus rule has unsupported fields")
        alert = raw.get("alert")
        record = raw.get("record")
        if bool(alert) == bool(record) or not isinstance(raw.get("expr"), str) or not raw["expr"].strip():
            raise ValueError("Prometheus rule requires exactly one alert or record and a non-empty expr")
        rule_name = str(alert or record)
        if not _PROM_NAME.fullmatch(rule_name):
            raise ValueError("Prometheus alert or record name is invalid")
        if len(raw["expr"]) > 16_384:
            raise ValueError("Prometheus rule expression exceeds cluster policy")
        result: dict[str, Any] = {
            "alert" if alert else "record": rule_name,
            "expr": raw["expr"],
        }
        for field in ("for", "keep_firing_for"):
            if raw.get(field) is not None:
                self._duration_ms(str(raw[field]))
                result["keep_firing_for" if field == "keep_firing_for" else field] = str(raw[field])
        for field in ("labels", "annotations"):
            values = raw.get(field) or {}
            if not isinstance(values, dict) or any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()
            ):
                raise ValueError(f"Prometheus rule {field} must map strings to strings")
            if any(key.startswith("astrolift.io/") for key in values):
                raise ValueError(f"Prometheus rule {field} cannot override Astrolift metadata")
            if values:
                result[field] = dict(values)
        return result

    def _normalize_dashboards(self, raw: Any) -> list[dict[str, Any]]:
        if raw in (None, []):
            return []
        if not self._config.allow_custom_dashboards:
            raise ValueError("custom Grafana dashboards are blocked by cluster policy")
        if not isinstance(raw, list) or len(raw) > self._config.max_dashboards:
            raise ValueError("Grafana dashboards must be an array within cluster limits")
        result: list[dict[str, Any]] = []
        names: set[str] = set()
        for item in raw:
            if not isinstance(item, dict) or set(item) != {"name", "document"}:
                raise ValueError("Grafana dashboard requires only name and document")
            raw_name = str(item.get("name") or "")
            name = dns_label(raw_name)
            document = item.get("document")
            if not raw_name or name in names or not isinstance(document, dict):
                raise ValueError("Grafana dashboards require unique names and object documents")
            encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
            if len(encoded.encode()) > self._config.max_dashboard_bytes:
                raise ValueError("Grafana dashboard exceeds cluster size policy")
            names.add(name)
            result.append({"name": name, "document": copy.deepcopy(document)})
        return result

    def _manifests(
        self,
        *,
        namespace: str,
        name: str,
        cfg: dict[str, Any],
        owner: dict[str, str],
    ) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        for kind, key, endpoint_key in (
            ("ServiceMonitor", "service_monitors", "endpoints"),
            ("PodMonitor", "pod_monitors", "podMetricsEndpoints"),
        ):
            for monitor in cfg[key]:
                child_name = dns_label(name, monitor["name"], "service" if kind == "ServiceMonitor" else "pod")
                children.append(
                    {
                        "apiVersion": "monitoring.coreos.com/v1",
                        "kind": kind,
                        "metadata": {
                            "name": child_name,
                            "namespace": namespace,
                            "labels": {**monitor["labels"], **self._labels(owner, kind.lower())},
                        },
                        "spec": {
                            "selector": monitor["selector"],
                            "namespaceSelector": {"matchNames": monitor["namespace_names"]},
                            endpoint_key: copy.deepcopy(monitor["endpoints"]),
                            **({"sampleLimit": monitor["sample_limit"]} if "sample_limit" in monitor else {}),
                            **({"targetLimit": monitor["target_limit"]} if "target_limit" in monitor else {}),
                        },
                    },
                )
        groups = copy.deepcopy(cfg["rule_groups"])
        if cfg["standard_rules"]:
            groups.insert(0, self._standard_rule_group(namespace))
        if groups:
            children.append(
                {
                    "apiVersion": "monitoring.coreos.com/v1",
                    "kind": "PrometheusRule",
                    "metadata": {
                        "name": dns_label(name, "rules"),
                        "namespace": namespace,
                        "labels": self._labels(owner, "prometheus-rules"),
                    },
                    "spec": {"groups": groups},
                },
            )
        dashboards = list(cfg["dashboards"])
        if cfg["default_dashboard"]:
            dashboards.insert(0, {"name": "overview", "document": self._default_dashboard(name, namespace)})
        for dashboard in dashboards:
            child_name = dns_label(name, dashboard["name"], "dashboard")
            dashboard_uid = self._dashboard_uid(owner, dashboard["name"])
            document = copy.deepcopy(dashboard["document"])
            document.pop("id", None)
            document["uid"] = dashboard_uid
            document.setdefault("title", f"Astrolift · {name} · {dashboard['name']}")
            children.append(
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": child_name,
                        "namespace": namespace,
                        "labels": {
                            **self._labels(owner, "grafana-dashboard"),
                            _DASHBOARD_LABEL_KEY: _DASHBOARD_LABEL_VALUE,
                        },
                    },
                    "data": {f"{dashboard_uid}.json": json.dumps(document, sort_keys=True, separators=(",", ":"))},
                },
            )
        refs = [self._ref(child) for child in children]
        bundle = {
            "version": 1,
            "children": refs,
        }
        root = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": self._labels(owner, "kube-prometheus-bundle"),
                "annotations": {
                    _CHILDREN: json.dumps(refs, sort_keys=True, separators=(",", ":")),
                    _DELETION_PROTECTION: "true" if cfg["deletion_protection"] else "false",
                },
            },
            "data": {"bundle.json": json.dumps(bundle, sort_keys=True, separators=(",", ":"))},
        }
        return [root, *children]

    @staticmethod
    def _standard_rule_group(namespace: str) -> dict[str, Any]:
        escaped = namespace.replace('"', '\\"')
        return {
            "name": "astrolift-workload-health",
            "rules": [
                {
                    "alert": "AstroliftDeploymentUnavailable",
                    "expr": f'kube_deployment_status_replicas_unavailable{{namespace="{escaped}"}} > 0',
                    "for": "10m",
                    "labels": {"severity": "warning"},
                    "annotations": {"summary": "Deployment has unavailable replicas"},
                },
                {
                    "alert": "AstroliftContainerRestarting",
                    "expr": (f'increase(kube_pod_container_status_restarts_total{{namespace="{escaped}"}}[15m]) > 3'),
                    "for": "5m",
                    "labels": {"severity": "warning"},
                    "annotations": {"summary": "Container is restarting repeatedly"},
                },
            ],
        }

    @staticmethod
    def _default_dashboard(name: str, namespace: str) -> dict[str, Any]:
        return {
            "schemaVersion": 39,
            "title": f"Astrolift · {name}",
            "tags": ["astrolift", namespace],
            "refresh": "30s",
            "time": {"from": "now-6h", "to": "now"},
            "panels": [
                {
                    "id": 1,
                    "type": "timeseries",
                    "title": "CPU cores by pod",
                    "targets": [
                        {
                            "refId": "A",
                            "expr": (
                                "sum by (pod) (rate(container_cpu_usage_seconds_total{"
                                f'namespace="{namespace}",container!=""}}[5m]))'
                            ),
                        },
                    ],
                    "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                },
                {
                    "id": 2,
                    "type": "timeseries",
                    "title": "Working set memory by pod",
                    "targets": [
                        {
                            "refId": "A",
                            "expr": (
                                "sum by (pod) (container_memory_working_set_bytes{"
                                f'namespace="{namespace}",container!=""}})'
                            ),
                        },
                    ],
                    "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
                },
            ],
        }

    def _preflight(self, cluster_id: str) -> None:
        if self._config.verify_crds:
            for crd_name in _REQUIRED_CRDS:
                crd = self._config.cluster_driver.get_manifest(
                    cluster_id,
                    None,
                    "apiextensions.k8s.io/v1/CustomResourceDefinition",
                    crd_name,
                )
                if crd is None:
                    raise ValueError(f"kube-prometheus-stack CRD {crd_name} is not installed")
        if self._config.verify_services:
            for service in (
                self._config.prometheus_service_name,
                self._config.alertmanager_service_name,
                self._config.grafana_service_name,
            ):
                if (
                    self._config.cluster_driver.get_manifest(
                        cluster_id,
                        self._config.monitoring_namespace,
                        "v1/Service",
                        service,
                    )
                    is None
                ):
                    raise ValueError(
                        f"kube-prometheus-stack service {self._config.monitoring_namespace}/{service} is not installed",
                    )
        if self._config.verify_selection:
            prometheus = self._config.cluster_driver.get_manifest(
                cluster_id,
                self._config.monitoring_namespace,
                "monitoring.coreos.com/v1/Prometheus",
                self._config.prometheus_service_name,
            )
            if prometheus is None:
                raise ValueError("kube-prometheus-stack Prometheus selection resource is not installed")
            prometheus_spec = prometheus.get("spec")
            if not isinstance(prometheus_spec, dict) or any(
                prometheus_spec.get(field) != {}
                for field in (
                    "serviceMonitorSelector",
                    "serviceMonitorNamespaceSelector",
                    "podMonitorSelector",
                    "podMonitorNamespaceSelector",
                    "ruleSelector",
                    "ruleNamespaceSelector",
                )
            ):
                raise ValueError(
                    "kube-prometheus-stack selectors do not watch all Astrolift project monitors and rules",
                )
            grafana = self._config.cluster_driver.get_manifest(
                cluster_id,
                self._config.monitoring_namespace,
                "apps/v1/Deployment",
                self._config.grafana_service_name,
            )
            if grafana is None or not self._grafana_sidecar_ready(grafana):
                raise ValueError(
                    "kube-prometheus-stack Grafana sidecar does not watch the fixed Astrolift dashboard contract",
                )
            for policy_name in (
                "astrolift-prometheus-trusted-ingress",
                "astrolift-alertmanager-operator-ingress",
            ):
                policy = self._config.cluster_driver.get_manifest(
                    cluster_id,
                    self._config.monitoring_namespace,
                    "networking.k8s.io/v1/NetworkPolicy",
                    policy_name,
                )
                if policy is None or not self._network_policy_ready(policy_name, policy):
                    raise ValueError(
                        f"kube-prometheus-stack access boundary {policy_name} is not installed or was weakened",
                    )

    def _assert_adoptable(self, current: dict[str, Any] | None, managed_service_id: str) -> None:
        if current is None:
            return
        labels = dict((current.get("metadata", {}) or {}).get("labels", {}) or {})
        owner = str(labels.get(_OWNER_ID) or "")
        if labels.get(_OWNER) == "astrolift" and owner == dns_label(managed_service_id):
            self._decode_children(current)
            return
        if labels.get(_OWNER) == "astrolift" and owner:
            raise ValueError("kube-prometheus bundle belongs to another Astrolift resource")
        raise ValueError("kube-prometheus bundle name collides with a foreign ConfigMap")

    def _assert_children_adoptable(
        self,
        cluster_id: str,
        namespace: str,
        children: list[dict[str, Any]],
        owner: dict[str, str],
    ) -> None:
        expected = dns_label(owner["managed_service_id"])
        for child in children:
            current = self._config.cluster_driver.get_manifest(
                cluster_id,
                namespace,
                f"{child['apiVersion']}/{child['kind']}",
                child["metadata"]["name"],
            )
            if current is None:
                continue
            labels = dict((current.get("metadata", {}) or {}).get("labels", {}) or {})
            if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID) == expected:
                continue
            if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID):
                raise ValueError(f"{child['kind']} {child['metadata']['name']} belongs to another resource")
            raise ValueError(f"{child['kind']} {child['metadata']['name']} collides with a foreign resource")

    def _owner(self, resource: dict[str, Any]) -> dict[str, str]:
        labels = dict((resource.get("metadata", {}) or {}).get("labels", {}) or {})
        values = {
            "managed_service_id": str(labels.get(_OWNER_ID) or ""),
            "organization": str(labels.get("astrolift.io/organization") or ""),
            "app": str(labels.get("astrolift.io/app") or ""),
            "environment": str(labels.get("astrolift.io/environment") or ""),
        }
        if (
            labels.get(_OWNER) != "astrolift"
            or labels.get(_COMPONENT) != "kube-prometheus-bundle"
            or not all(values.values())
        ):
            raise ValueError("kube-prometheus bundle is not owned by Astrolift")
        return values

    @staticmethod
    def _assert_child_owned(resource: dict[str, Any], owner_id: str) -> None:
        labels = dict((resource.get("metadata", {}) or {}).get("labels", {}) or {})
        if labels.get(_OWNER) != "astrolift" or labels.get(_OWNER_ID) != dns_label(owner_id):
            raise ValueError("kube-prometheus child ownership changed")

    def _owned_child_stubs(
        self,
        parsed: ParsedHandle,
        children: list[dict[str, str]],
        owner_id: str,
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
            self._assert_child_owned(current, owner_id)
            # Pass the manifest ownership was validated against, so the delete
            # is conditional on it still being the same object.
            stubs.append(
                self._stub(
                    ref["apiVersion"],
                    ref["kind"],
                    ref["name"],
                    parsed.namespace,
                    observed=current,
                )
            )
        return stubs

    def _stale_children(
        self,
        current: dict[str, Any] | None,
        desired: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        if current is None:
            return []
        desired_refs = {json.dumps(self._ref(row), sort_keys=True) for row in desired[1:]}
        return [ref for ref in self._decode_children(current) if json.dumps(ref, sort_keys=True) not in desired_refs]

    def _transition_root(
        self,
        current: dict[str, Any] | None,
        desired: dict[str, Any],
    ) -> dict[str, Any]:
        transition = copy.deepcopy(current if current is not None else desired)
        refs = self._decode_children(desired)
        if current is not None:
            refs = [*self._decode_children(current), *refs]
        unique = {json.dumps(ref, sort_keys=True, separators=(",", ":")): ref for ref in refs}
        children = [unique[key] for key in sorted(unique)]
        transition["metadata"]["annotations"][_CHILDREN] = json.dumps(
            children,
            sort_keys=True,
            separators=(",", ":"),
        )
        document = self._bundle_document(transition)
        document["children"] = children
        transition["data"]["bundle.json"] = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return transition

    def _reconcile(
        self,
        *,
        cluster_id: str,
        namespace: str,
        transition_root: dict[str, Any],
        desired: list[dict[str, Any]],
        stale_stubs: list[dict[str, Any]],
    ) -> tuple[str, list[str]] | None:
        for manifests in ([transition_root], desired[1:]):
            if not manifests:
                continue
            result = self._config.cluster_driver.apply_manifests(cluster_id, namespace, manifests)
            if not result.ok:
                return "kube-prometheus bundle was rejected", result.summary()
        if stale_stubs:
            result = self._config.cluster_driver.delete_manifests(cluster_id, namespace, stale_stubs)
            if not result.ok:
                return (
                    "kube-prometheus stale-resource pruning failed: " + ", ".join(result.summary()),
                    ["kube_prometheus_prune_failed"],
                )
        result = self._config.cluster_driver.apply_manifests(cluster_id, namespace, [desired[0]])
        if not result.ok:
            return "kube-prometheus bundle inventory finalization was rejected", result.summary()
        return None

    def _root(self, cluster_id: str, namespace: str, name: str) -> dict[str, Any] | None:
        root = self._config.cluster_driver.get_manifest(cluster_id, namespace, _ROOT_KIND, name)
        if root is not None and not isinstance(root, dict):
            raise ValueError("kube-prometheus bundle response is malformed")
        return root

    def _decode_children(self, root: dict[str, Any]) -> list[dict[str, str]]:
        annotations = dict((root.get("metadata", {}) or {}).get("annotations", {}) or {})
        raw = annotations.get(_CHILDREN)
        if not isinstance(raw, str):
            raise ValueError("kube-prometheus bundle has malformed child inventory")
        try:
            children = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("kube-prometheus bundle has malformed child inventory") from exc
        if not isinstance(children, list):
            raise ValueError("kube-prometheus bundle child inventory must be an array")
        result: list[dict[str, str]] = []
        for ref in children:
            if not isinstance(ref, dict) or set(ref) != {"apiVersion", "kind", "name"}:
                raise ValueError("kube-prometheus bundle contains an invalid child reference")
            values = {key: str(ref[key]) for key in ("apiVersion", "kind", "name")}
            expected_versions = {
                "ServiceMonitor": "monitoring.coreos.com/v1",
                "PodMonitor": "monitoring.coreos.com/v1",
                "PrometheusRule": "monitoring.coreos.com/v1",
                "ConfigMap": "v1",
            }
            if not all(values.values()) or expected_versions.get(values["kind"]) != values["apiVersion"]:
                raise ValueError("kube-prometheus bundle contains an unsupported child reference")
            result.append(values)
        return result

    @staticmethod
    def _ref(resource: dict[str, Any]) -> dict[str, str]:
        return {
            "apiVersion": str(resource["apiVersion"]),
            "kind": str(resource["kind"]),
            "name": str(resource["metadata"]["name"]),
        }

    @staticmethod
    def _stub(
        api_version: str,
        kind: str,
        name: str,
        namespace: str,
        *,
        observed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A delete stub, carrying the identity of the object that was inspected.

        ``observed`` is the manifest the ownership check ran against. Its uid
        travels into the stub so the delete is conditional on still being that
        object (#1389): a name-only stub deletes whatever holds the name at
        delete time, and between the GET and the delete a reconciler can have
        recreated it. Omitted, the stub behaves exactly as before.
        """
        metadata: dict[str, Any] = {"name": name, "namespace": namespace}
        observed_metadata = (observed or {}).get("metadata") or {}
        uid = observed_metadata.get("uid")
        if uid:
            metadata["uid"] = uid
        return {"apiVersion": api_version, "kind": kind, "metadata": metadata}

    def _bundle_document(self, root: dict[str, Any]) -> dict[str, Any]:
        raw = (root.get("data") or {}).get("bundle.json")
        if not isinstance(raw, str):
            raise ValueError("kube-prometheus bundle document is malformed")
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("kube-prometheus bundle document is malformed") from exc
        if not isinstance(document, dict) or document.get("version") != 1:
            raise ValueError("kube-prometheus bundle document has an unsupported version")
        if not isinstance(document.get("children"), list):
            raise ValueError("kube-prometheus bundle document is missing children")
        return document

    @staticmethod
    def _dashboard_uid(owner: dict[str, str], dashboard_name: str) -> str:
        return dns_label(
            "astrolift",
            dns_label(owner["organization"]),
            dns_label(owner["managed_service_id"]),
            dashboard_name,
            max_length=40,
        )

    def _dashboard_url(self, root: dict[str, Any], owner: dict[str, str]) -> str:
        root_name = str((root.get("metadata") or {}).get("name") or "")
        overview_name = dns_label(root_name, "overview", "dashboard")
        if any(ref["kind"] == "ConfigMap" and ref["name"] == overview_name for ref in self._decode_children(root)):
            return f"{self._grafana_url().rstrip('/')}/d/{self._dashboard_uid(owner, 'overview')}"
        return self._grafana_url()

    @staticmethod
    def _grafana_sidecar_ready(deployment: dict[str, Any]) -> bool:
        try:
            containers = deployment["spec"]["template"]["spec"]["containers"]
        except (KeyError, TypeError):
            return False
        if not isinstance(containers, list):
            return False
        for container in containers:
            if not isinstance(container, dict):
                continue
            env = {str(row.get("name")): row.get("value") for row in container.get("env", []) if isinstance(row, dict)}
            if env.get("LABEL") == _DASHBOARD_LABEL_KEY:
                return env.get("LABEL_VALUE") == _DASHBOARD_LABEL_VALUE and env.get("NAMESPACE") == "ALL"
        return False

    @staticmethod
    def _network_policy_ready(name: str, policy: dict[str, Any]) -> bool:
        spec = policy.get("spec")
        if not isinstance(spec, dict) or spec.get("policyTypes") != ["Ingress"]:
            return False
        ingress = spec.get("ingress")
        if not isinstance(ingress, list) or len(ingress) != 1 or not isinstance(ingress[0], dict):
            return False
        sources = ingress[0].get("from")
        monitoring_source = {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "astrolift-system"},
            },
        }
        if name == "astrolift-alertmanager-operator-ingress":
            return (
                spec.get("podSelector")
                == {
                    "matchLabels": {
                        "alertmanager": "astrolift-kube-prometheus-alertmanager",
                        "app.kubernetes.io/name": "alertmanager",
                    },
                }
                and sources == [monitoring_source]
                and "ports" not in ingress[0]
            )
        trusted_source = {
            "namespaceSelector": {
                "matchLabels": {_PROMETHEUS_ACCESS_LABEL: "true"},
            },
        }
        return (
            spec.get("podSelector")
            == {
                "matchLabels": {
                    "app.kubernetes.io/name": "prometheus",
                    "prometheus": "astrolift-kube-prometheus-prometheus",
                },
            }
            and sources == [monitoring_source, trusted_source]
            and ingress[0].get("ports") == [{"port": 9090, "protocol": "TCP"}]
        )

    def _labels(self, owner: dict[str, str], component: str) -> dict[str, str]:
        return {
            _OWNER: "astrolift",
            _OWNER_ID: dns_label(owner["managed_service_id"]),
            _COMPONENT: component,
            "astrolift.io/organization": dns_label(owner["organization"]),
            "astrolift.io/app": dns_label(owner["app"]),
            "astrolift.io/environment": dns_label(owner["environment"]),
        }

    def _metadata_labels(self, raw: Any, *, allow_reserved: bool = False) -> dict[str, str]:
        values = raw or {}
        if not isinstance(values, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()
        ):
            raise ValueError("Kubernetes labels must map strings to strings")
        result: dict[str, str] = {}
        for key, value in values.items():
            self._validate_label_key(key)
            if not allow_reserved and (key in {_OWNER, _OWNER_ID, _COMPONENT} or key.startswith("astrolift.io/")):
                raise ValueError(f"Kubernetes label {key!r} is reserved by Astrolift")
            if len(value) > 63 or not _LABEL_VALUE.fullmatch(value):
                raise ValueError(f"invalid Kubernetes label value for {key!r}")
            result[key] = value
        return result

    @staticmethod
    def _validate_label_key(key: str) -> None:
        _, _, name = key.rpartition("/")
        if len(key) > 253 or len(name) > 63 or not _LABEL_KEY.fullmatch(key):
            raise ValueError(f"invalid Kubernetes label key {key!r}")

    @staticmethod
    def _duration_ms(value: str) -> int:
        match = _DURATION.fullmatch(value)
        if match is None:
            raise ValueError(f"invalid Prometheus duration {value!r}")
        amount = int(match.group(1))
        multiplier = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000}[match.group(2)]
        return amount * multiplier

    def _prometheus_url(self) -> str:
        return self._config.prometheus_url or (
            f"http://{self._config.prometheus_service_name}.{self._config.monitoring_namespace}.svc.cluster.local:9090"
        )

    def _grafana_url(self) -> str:
        return self._config.grafana_url or (
            f"http://{self._config.grafana_service_name}.{self._config.monitoring_namespace}.svc.cluster.local:80"
        )

    def _require_driver(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("kube-prometheus bundle requires a live cluster driver")
        for string_field, string_value in (
            ("monitoring_namespace", self._config.monitoring_namespace),
            ("prometheus_service_name", self._config.prometheus_service_name),
            ("alertmanager_service_name", self._config.alertmanager_service_name),
            ("grafana_service_name", self._config.grafana_service_name),
        ):
            if len(string_value) > 63 or not _DNS_LABEL.fullmatch(string_value):
                raise ValueError(f"kube-prometheus {string_field} must be a Kubernetes DNS label")
        for name in self._config.allowed_target_namespaces:
            if len(name) > 63 or not _DNS_LABEL.fullmatch(name):
                raise ValueError(f"kube-prometheus allowed target namespace {name!r} is invalid")
        for positive_field, positive_value in (
            ("min_scrape_interval_seconds", self._config.min_scrape_interval_seconds),
            ("max_endpoints_per_monitor", self._config.max_endpoints_per_monitor),
            ("max_samples_per_scrape", self._config.max_samples_per_scrape),
            ("max_targets_per_monitor", self._config.max_targets_per_monitor),
            ("max_dashboard_bytes", self._config.max_dashboard_bytes),
        ):
            if isinstance(positive_value, bool) or not isinstance(positive_value, int) or positive_value < 1:
                raise ValueError(f"kube-prometheus {positive_field} must be a positive integer")
        for count_field, count_value in (
            ("max_monitors", self._config.max_monitors),
            ("max_rule_groups", self._config.max_rule_groups),
            ("max_rules", self._config.max_rules),
            ("max_dashboards", self._config.max_dashboards),
        ):
            if isinstance(count_value, bool) or not isinstance(count_value, int) or count_value < 0:
                raise ValueError(f"kube-prometheus {count_field} must be a non-negative integer")
        for boolean_field, boolean_value in (
            ("verify_crds", self._config.verify_crds),
            ("verify_services", self._config.verify_services),
            ("verify_selection", self._config.verify_selection),
            ("allow_workload_prometheus_access", self._config.allow_workload_prometheus_access),
            ("allow_cross_namespace", self._config.allow_cross_namespace),
            ("allow_custom_rules", self._config.allow_custom_rules),
            ("allow_custom_dashboards", self._config.allow_custom_dashboards),
            ("allow_honor_labels", self._config.allow_honor_labels),
        ):
            if not isinstance(boolean_value, bool):
                raise ValueError(f"kube-prometheus {boolean_field} must be a boolean")
        for field, value in (
            ("prometheus_url", self._prometheus_url()),
            ("grafana_url", self._grafana_url()),
        ):
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"kube-prometheus {field} must be an HTTP(S) URL without embedded credentials")

    @staticmethod
    def _parsed(handle: str) -> ParsedHandle:
        parsed = _unpack_handle(handle)
        if parsed.kind != KIND:
            raise ValueError(f"kube-prometheus handle kind must be {KIND!r}, got {parsed.kind!r}")
        if parsed.is_legacy:
            raise ValueError("legacy kube-prometheus handle has no cluster locator")
        return parsed
