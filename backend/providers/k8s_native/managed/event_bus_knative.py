"""Knative Broker and Trigger implementation of the portable event-bus contract."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlparse

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

KIND = "event_bus"
VARIANT = "knative_eventing"
API_VERSION = "eventing.knative.dev/v1"
RESOURCE_KIND = "Broker"
TRIGGER_KIND = "Trigger"
REQUIRED_CRDS = (
    "brokers.eventing.knative.dev",
    "triggers.eventing.knative.dev",
)

_OWNER = "app.kubernetes.io/managed-by"
_OWNER_ID = "astrolift.io/managed-service-id"
_TRIGGERS = "astrolift.io/managed-triggers"
_BROKER_CLASS = "eventing.knative.dev/broker.class"
_LABEL_KEY = re.compile(
    r"^(?:[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$",
)
_LABEL_VALUE = re.compile(r"^(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")
_CONFIG_FIELDS = {
    "broker_class",
    "broker_config",
    "delivery",
    "triggers",
    "labels",
    "annotations",
    "deletion_protection",
    "adopt_existing",
    "expected_existing_uid",
}
_DELIVERY_FIELDS = {
    "deadLetterSink",
    "retry",
    "backoffPolicy",
    "backoffDelay",
    "timeout",
    "retryAfterMax",
}


@dataclass(frozen=True)
class KnativeEventingConfig:
    cluster_driver: Any = None
    namespace: str | None = None
    broker_class: str = "MTChannelBasedBroker"
    broker_config: dict[str, Any] | None = None
    allow_class_override: bool = False
    allow_config_override: bool = False
    allow_external_subscribers: bool = False
    allow_cross_namespace_subscribers: bool = False
    allow_alpha_delivery_fields: bool = False
    allowed_broker_classes: tuple[str, ...] = (
        "MTChannelBasedBroker",
        "ChannelBasedBroker",
        "Kafka",
        "RabbitMQBroker",
    )


class KnativeEventingDriver(ManagedServiceDriver):
    """Own one namespace-local Broker and its declarative Trigger set."""

    def __init__(self, *, config: KnativeEventingConfig) -> None:
        self._config = config

    @driver_op(
        cloud="k8s_native",
        driver="event_bus_knative",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            self._require_driver()
            if not spec.managed_service_id:
                raise ValueError("Knative Eventing requires a managed_service_id for safe ownership")
            namespace = self._config.namespace or app_namespace(
                organization_slug=spec.organization_slug,
                app_slug=spec.app_slug,
            )
            name = dns_label(
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "events",
            )
            cfg = self._normalize(spec.config, namespace=namespace)
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
            return ProvisionResult(False, "", str(exc), ["invalid_knative_eventing_config"])

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
            return ProvisionResult(False, handle, "Knative Eventing resources were rejected", result.summary())
        return ProvisionResult(
            True,
            handle,
            f"Knative Broker {namespace}/{name} submitted for reconciliation",
            ready=False,
        )

    @driver_op(cloud="k8s_native", driver="event_bus_knative")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require_driver()
            parsed = self._parsed(spec.handle)
            current = self._broker(parsed)
            if current is None:
                return UpdateResult(
                    False,
                    spec.handle,
                    "Knative Broker does not exist",
                    ["resource_not_found"],
                    retryable=False,
                )
            owner = self._assert_owned(current)
            cfg = self._normalize(spec.config, namespace=parsed.namespace)
            current_class = str(
                ((current.get("metadata", {}) or {}).get("annotations", {}) or {}).get(
                    _BROKER_CLASS,
                )
                or "",
            )
            if current_class and current_class != self._broker_class(cfg):
                raise ValueError("broker_class is immutable and requires replacement")
            manifests = self._manifests(
                cluster_id=parsed.cluster_id,
                namespace=parsed.namespace,
                name=parsed.name,
                cfg=cfg,
                owner=self._owner_from_labels(current),
            )
            stale = self._stale_triggers(current, manifests)
            stale_stubs = self._owned_trigger_stubs(parsed, stale, owner)
        except (TypeError, ValueError) as exc:
            return UpdateResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_knative_eventing_update"],
                retryable=False,
            )
        if stale_stubs:
            deleted = self._config.cluster_driver.delete_manifests(
                parsed.cluster_id,
                parsed.namespace,
                stale_stubs,
            )
            if not deleted.ok:
                return UpdateResult(False, spec.handle, "could not prune removed Triggers", deleted.summary())
        result = self._config.cluster_driver.apply_manifests(
            parsed.cluster_id,
            parsed.namespace,
            manifests,
        )
        if not result.ok:
            return UpdateResult(False, spec.handle, "Knative Eventing update was rejected", result.summary())
        return UpdateResult(True, spec.handle, f"Knative Broker {parsed.name} update submitted")

    @driver_op(
        cloud="k8s_native",
        driver="event_bus_knative",
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
            broker = self._broker(parsed)
            if broker is None:
                return DeprovisionResult(True, spec.handle, "Knative Broker already absent")
            owner = self._assert_owned(broker)
            triggers = self._decode_triggers(broker)
            stubs = self._owned_trigger_stubs(parsed, triggers, owner)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["ownership_mismatch"], retryable=False)
        if bool(spec.config.get("deletion_protection", False)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Knative Broker deletion protection is enabled; pass force_destroy to delete it",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        stubs.append(self._stub(RESOURCE_KIND, parsed.name, parsed.namespace))
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            stubs,
        )
        if not result.ok:
            return DeprovisionResult(False, spec.handle, "Knative Eventing deletion failed", result.summary())
        return DeprovisionResult(True, spec.handle, f"Knative Broker {parsed.name} deletion submitted")

    @driver_op(cloud="k8s_native", driver="event_bus_knative")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._require_driver()
            parsed = self._parsed(handle.handle)
            broker = self._broker(parsed)
        except ValueError as exc:
            return ServiceStatus(handle.handle, "error", str(exc))
        if broker is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Knative Broker not found")
        ready = self._condition(broker, "Ready")
        if ready and str(ready.get("status") or "").lower() == "false":
            return ServiceStatus(
                handle.handle,
                "error",
                str(ready.get("message") or ready.get("reason") or "Knative Broker reconciliation failed"),
            )
        address = self._address(broker)
        if self._is_true(ready) and address:
            try:
                trigger_state, detail = self._trigger_state(parsed, self._decode_triggers(broker))
            except ValueError as exc:
                return ServiceStatus(handle.handle, "error", str(exc))
            if trigger_state != "available":
                return ServiceStatus(handle.handle, trigger_state, detail)
            return ServiceStatus(handle.handle, "available", f"Knative Broker ready at {address}")
        return ServiceStatus(handle.handle, "provisioning", "Knative Broker reconciliation in progress")

    @driver_op(cloud="k8s_native", driver="event_bus_knative")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        self._require_driver()
        parsed = self._parsed(handle.handle)
        broker = self._broker(parsed)
        if broker is None:
            raise ValueError("Knative Broker does not exist")
        if not self._is_true(self._condition(broker, "Ready")):
            raise ValueError("Knative Broker is not ready")
        address = self._address(broker)
        if not address:
            raise ValueError("Knative Broker has no address")
        return Binding(
            env_vars={
                "EVENT_BUS_NAME": ValueRef(literal=parsed.name),
                "EVENT_BUS_ARN": ValueRef(
                    literal=f"k8s://{parsed.cluster_id}/{parsed.namespace}/broker/{parsed.name}",
                ),
                "EVENT_BUS_REGION": ValueRef(literal="kubernetes"),
                "EVENT_BUS_ENDPOINT": ValueRef(literal=address),
                "EVENT_BUS_URI": ValueRef(literal=address),
                "KUBERNETES_NAMESPACE": ValueRef(literal=parsed.namespace),
            },
            notes="Publish CloudEvents to EVENT_BUS_ENDPOINT using structured or binary CloudEvents HTTP mode.",
        )

    @driver_op(cloud="k8s_native", driver="event_bus_knative")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError("Knative Eventing configuration is declarative and has no snapshot semantic")

    @driver_op(cloud="k8s_native", driver="event_bus_knative")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise UnsupportedOperationError("reconcile the Knative Broker and Triggers from declarative config instead")

    @driver_op(cloud="k8s_native", driver="event_bus_knative", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        scalar_map = {"type": "object", "additionalProperties": {"type": "string"}}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "broker_class": {"type": "string", "minLength": 1},
                "broker_config": {"type": "object"},
                "delivery": {"type": "object"},
                "triggers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "subscriber"],
                        "properties": {
                            "name": {"type": "string", "minLength": 1, "maxLength": 63},
                            "subscriber": {"type": "object"},
                            "filter": {"type": "object"},
                            "filters": {"type": "array", "items": {"type": "object"}},
                            "delivery": {"type": "object"},
                            "labels": scalar_map,
                            "annotations": scalar_map,
                            "adopt_existing": {"type": "boolean", "default": False},
                            "expected_existing_uid": {"type": "string"},
                        },
                    },
                },
                "labels": scalar_map,
                "annotations": scalar_map,
                "deletion_protection": {"type": "boolean", "default": False},
                "adopt_existing": {"type": "boolean", "default": False},
                "expected_existing_uid": {"type": "string"},
            },
        }

    @driver_op(cloud="k8s_native", driver="event_bus_knative", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EVENT_BUS_NAME": "Knative Broker name",
                "EVENT_BUS_ARN": "Portable Kubernetes Broker locator",
                "EVENT_BUS_REGION": "Always kubernetes",
                "EVENT_BUS_ENDPOINT": "Broker CloudEvents ingress URL",
                "EVENT_BUS_URI": "Alias of the Broker ingress URL",
                "KUBERNETES_NAMESPACE": "Broker namespace",
            },
        )

    def editable_fields(self) -> list[str]:
        return sorted(_CONFIG_FIELDS - {"adopt_existing", "expected_existing_uid"})

    def _normalize(self, raw: dict[str, Any], *, namespace: str) -> dict[str, Any]:
        cfg = copy.deepcopy(raw or {})
        unknown = set(cfg) - _CONFIG_FIELDS
        if unknown:
            raise ValueError("unsupported Knative Eventing config fields: " + ", ".join(sorted(unknown)))
        broker_class = self._broker_class(cfg)
        if broker_class not in self._config.allowed_broker_classes:
            raise ValueError(f"broker class {broker_class!r} is not enabled for this cluster")
        configured = cfg.get("broker_config")
        if configured is not None and not isinstance(configured, dict):
            raise ValueError("broker_config must be an object")
        if (
            configured is not None
            and not self._config.allow_config_override
            and (self._config.broker_config is None or configured != self._config.broker_config)
        ):
            raise ValueError("broker_config override is disabled by cluster policy")
        effective_config = configured if configured is not None else self._config.broker_config
        if effective_config is not None:
            if not isinstance(effective_config, dict):
                raise ValueError("cluster broker_config must be an object")
            self._validate_reference(effective_config, field="broker_config")
        self._validate_delivery(cfg.get("delivery"), namespace=namespace)
        self._validate_metadata(cfg.get("labels"), cfg.get("annotations"))
        triggers = cfg.get("triggers") or []
        if not isinstance(triggers, list):
            raise ValueError("triggers must be an array")
        names: set[str] = set()
        for trigger in triggers:
            if not isinstance(trigger, dict):
                raise ValueError("triggers must contain objects")
            unknown_trigger = set(trigger) - {
                "name",
                "subscriber",
                "filter",
                "filters",
                "delivery",
                "labels",
                "annotations",
                "adopt_existing",
                "expected_existing_uid",
            }
            if unknown_trigger:
                raise ValueError("unsupported Trigger fields: " + ", ".join(sorted(unknown_trigger)))
            name = dns_label(trigger.get("name") or "")
            if name in names:
                raise ValueError(f"duplicate Knative Trigger {name}")
            subscriber = trigger.get("subscriber")
            if not isinstance(subscriber, dict) or not subscriber:
                raise ValueError("each Trigger requires a subscriber object")
            self._validate_destination(
                subscriber,
                field="subscriber",
                namespace=namespace,
            )
            if trigger.get("filter") is not None and trigger.get("filters") is not None:
                raise ValueError("Trigger filter and filters are mutually exclusive")
            if trigger.get("filter") is not None and not isinstance(trigger["filter"], dict):
                raise ValueError("Trigger filter must be an object")
            if trigger.get("filters") is not None and not isinstance(trigger["filters"], list):
                raise ValueError("Trigger filters must be an array")
            if any(not isinstance(item, dict) for item in (trigger.get("filters") or [])):
                raise ValueError("Trigger filters must contain objects")
            self._validate_delivery(trigger.get("delivery"), namespace=namespace)
            self._validate_metadata(trigger.get("labels"), trigger.get("annotations"))
            names.add(name)
        return cfg

    def _broker_class(self, cfg: dict[str, Any]) -> str:
        configured = str(cfg.get("broker_class") or "")
        default = self._config.broker_class
        if configured and configured != default and not self._config.allow_class_override:
            raise ValueError("broker_class override is disabled by cluster policy")
        value = configured or default
        if not value:
            raise ValueError("Knative Eventing requires a broker class")
        return value

    def _validate_delivery(self, delivery: Any, *, namespace: str) -> None:
        if delivery is None:
            return
        if not isinstance(delivery, dict):
            raise ValueError("delivery must be an object")
        unknown = set(delivery) - _DELIVERY_FIELDS
        if unknown:
            raise ValueError("unsupported delivery fields: " + ", ".join(sorted(unknown)))
        if "retryAfterMax" in delivery and not self._config.allow_alpha_delivery_fields:
            raise ValueError("delivery.retryAfterMax requires the cluster alpha-delivery policy")
        if "retry" in delivery and (
            isinstance(delivery["retry"], bool) or not isinstance(delivery["retry"], int) or delivery["retry"] < 0
        ):
            raise ValueError("delivery.retry must be a non-negative integer")
        if "backoffPolicy" in delivery and delivery["backoffPolicy"] not in {"linear", "exponential"}:
            raise ValueError("delivery.backoffPolicy must be linear or exponential")
        for field in ("backoffDelay", "timeout", "retryAfterMax"):
            if field in delivery and (not isinstance(delivery[field], str) or not delivery[field]):
                raise ValueError(f"delivery.{field} must be a non-empty duration string")
        if delivery.get("deadLetterSink") is not None:
            self._validate_destination(
                delivery["deadLetterSink"],
                field="deadLetterSink",
                namespace=namespace,
            )

    def _validate_destination(
        self,
        destination: Any,
        *,
        field: str,
        namespace: str,
    ) -> None:
        if not isinstance(destination, dict):
            raise ValueError(f"{field} must be an object")
        unknown = set(destination) - {"ref", "uri"}
        if unknown:
            raise ValueError(
                f"unsupported {field} fields: " + ", ".join(sorted(unknown)),
            )
        uri = str(destination.get("uri") or "")
        ref = destination.get("ref")
        if not uri and not isinstance(ref, dict):
            raise ValueError(f"{field} requires ref or uri")
        if uri:
            parsed = urlparse(uri)
            if not parsed.scheme or (parsed.scheme in {"http", "https"} and not parsed.netloc):
                raise ValueError(f"{field}.uri must be absolute")
            if parsed.scheme not in {"http", "https"} and not self._config.allow_external_subscribers:
                raise ValueError(f"{field}.uri scheme is disabled by cluster policy")
            if parsed.scheme in {"http", "https"} and not self._config.allow_external_subscribers:
                host = parsed.hostname or ""
                labels = host.rstrip(".").split(".")
                if len(labels) == 1:
                    pass
                elif "svc" in labels:
                    svc_index = labels.index("svc")
                    destination_namespace = labels[svc_index - 1] if svc_index >= 2 else namespace
                    if destination_namespace != namespace and not self._config.allow_cross_namespace_subscribers:
                        raise ValueError(
                            f"cross-namespace {field}.uri is disabled by cluster policy",
                        )
                else:
                    raise ValueError(f"external {field}.uri is disabled by cluster policy")
        if isinstance(ref, dict):
            self._validate_reference(ref, field=f"{field}.ref")
            destination_namespace = str(ref.get("namespace") or namespace)
            if destination_namespace != namespace and not self._config.allow_cross_namespace_subscribers:
                raise ValueError(f"cross-namespace {field} references are disabled by cluster policy")

    @staticmethod
    def _validate_reference(reference: dict[str, Any], *, field: str) -> None:
        unknown = set(reference) - {"apiVersion", "kind", "name", "namespace"}
        if unknown:
            raise ValueError(
                f"unsupported {field} fields: " + ", ".join(sorted(unknown)),
            )
        for required in ("apiVersion", "kind", "name"):
            if not isinstance(reference.get(required), str) or not reference[required]:
                raise ValueError(f"{field}.{required} is required")
        if "namespace" in reference and (not isinstance(reference["namespace"], str) or not reference["namespace"]):
            raise ValueError(f"{field}.namespace must be a non-empty string")

    @staticmethod
    def _validate_metadata(labels: Any, annotations: Any) -> None:
        for field, values, bounded in (("labels", labels, True), ("annotations", annotations, False)):
            values = values or {}
            if not isinstance(values, dict):
                raise ValueError(f"{field} must be an object")
            for key, value in values.items():
                if str(key) in {_OWNER, _OWNER_ID, _TRIGGERS, _BROKER_CLASS} or str(key).startswith("astrolift.io/"):
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
        trigger_names: list[str] = []
        triggers: list[dict[str, Any]] = []
        for trigger in cfg.get("triggers") or []:
            trigger_name = dns_label(name, trigger["name"])
            trigger_spec: dict[str, Any] = {
                "broker": name,
                "subscriber": copy.deepcopy(trigger["subscriber"]),
            }
            for key in ("filter", "filters", "delivery"):
                if trigger.get(key) not in (None, {}, []):
                    trigger_spec[key] = copy.deepcopy(trigger[key])
            manifest = {
                "apiVersion": API_VERSION,
                "kind": TRIGGER_KIND,
                "metadata": {
                    "name": trigger_name,
                    "namespace": namespace,
                    "labels": self._labels(trigger.get("labels"), owner),
                    "annotations": dict(trigger.get("annotations") or {}),
                },
                "spec": trigger_spec,
            }
            self._assert_trigger_adoptable(
                cluster_id,
                namespace,
                manifest,
                trigger,
                dns_label(owner[_OWNER_ID]),
            )
            trigger_names.append(trigger_name)
            triggers.append(manifest)
        annotations = dict(cfg.get("annotations") or {})
        annotations[_BROKER_CLASS] = self._broker_class(cfg)
        annotations[_TRIGGERS] = json.dumps(sorted(trigger_names), separators=(",", ":"))
        broker_spec: dict[str, Any] = {}
        broker_config = cfg.get("broker_config", self._config.broker_config)
        if broker_config:
            broker_spec["config"] = copy.deepcopy(broker_config)
        if cfg.get("delivery"):
            broker_spec["delivery"] = copy.deepcopy(cfg["delivery"])
        broker = {
            "apiVersion": API_VERSION,
            "kind": RESOURCE_KIND,
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": self._labels(cfg.get("labels"), owner),
                "annotations": annotations,
            },
            "spec": broker_spec,
        }
        return [broker, *triggers]

    def _assert_adoptable(
        self,
        *,
        cluster_id: str,
        namespace: str,
        name: str,
        managed_service_id: str,
        cfg: dict[str, Any],
    ) -> None:
        parsed = ParsedHandle(KIND, cluster_id, namespace, name)
        current = self._broker(parsed)
        if current is None:
            return
        metadata = dict(current.get("metadata", {}) or {})
        labels = dict(metadata.get("labels", {}) or {})
        owner = str(labels.get(_OWNER_ID) or "")
        if labels.get(_OWNER) == "astrolift" and owner == dns_label(managed_service_id):
            return
        if labels.get(_OWNER) == "astrolift" and owner:
            raise ValueError("Knative Broker belongs to another Astrolift managed resource")
        if not cfg.get("adopt_existing") or str(cfg.get("expected_existing_uid") or "") != str(
            metadata.get("uid") or ""
        ):
            raise ValueError("adopting a Knative Broker requires its exact expected_existing_uid")

    def _assert_trigger_adoptable(
        self,
        cluster_id: str,
        namespace: str,
        manifest: dict[str, Any],
        trigger: dict[str, Any],
        owner_id: str,
    ) -> None:
        current = self._config.cluster_driver.get_manifest(
            cluster_id,
            namespace,
            f"{API_VERSION}/{TRIGGER_KIND}",
            manifest["metadata"]["name"],
        )
        if current is None:
            return
        metadata = dict(current.get("metadata", {}) or {})
        labels = dict(metadata.get("labels", {}) or {})
        if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID) == owner_id:
            return
        if labels.get(_OWNER) == "astrolift" and labels.get(_OWNER_ID):
            raise ValueError(f"Trigger {manifest['metadata']['name']} belongs to another resource")
        if not trigger.get("adopt_existing") or str(trigger.get("expected_existing_uid") or "") != str(
            metadata.get("uid") or ""
        ):
            raise ValueError(
                f"adopting Trigger {manifest['metadata']['name']} requires its exact expected_existing_uid",
            )

    def _assert_owned(self, resource: dict[str, Any]) -> str:
        labels = dict((resource.get("metadata", {}) or {}).get("labels", {}) or {})
        owner = str(labels.get(_OWNER_ID) or "")
        if labels.get(_OWNER) != "astrolift" or not owner:
            raise ValueError("Knative Broker is not owned by an Astrolift managed resource")
        return owner

    def _owned_trigger_stubs(
        self,
        parsed: ParsedHandle,
        trigger_names: list[str],
        owner: str,
    ) -> list[dict[str, Any]]:
        stubs: list[dict[str, Any]] = []
        for name in trigger_names:
            current = self._trigger(parsed, name)
            if current is None:
                continue
            labels = dict((current.get("metadata", {}) or {}).get("labels", {}) or {})
            if labels.get(_OWNER) != "astrolift" or labels.get(_OWNER_ID) != owner:
                raise ValueError(f"refusing to delete foreign Knative Trigger {name}")
            stubs.append(self._stub(TRIGGER_KIND, name, parsed.namespace))
        return stubs

    def _stale_triggers(self, broker: dict[str, Any], desired: list[dict[str, Any]]) -> list[str]:
        desired_names = {str(row["metadata"]["name"]) for row in desired[1:]}
        return [name for name in self._decode_triggers(broker) if name not in desired_names]

    def _trigger_state(self, parsed: ParsedHandle, trigger_names: list[str]) -> tuple[str, str]:
        for name in trigger_names:
            trigger = self._trigger(parsed, name)
            if trigger is None:
                return "error", f"managed Trigger {name} is missing"
            ready = self._condition(trigger, "Ready")
            if ready and str(ready.get("status") or "").lower() == "false":
                return "error", str(ready.get("message") or ready.get("reason") or f"Trigger {name} was rejected")
            if not self._is_true(ready):
                return "provisioning", f"managed Trigger {name} is reconciling"
        return "available", "all managed Triggers are ready"

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
    def _decode_triggers(broker: dict[str, Any]) -> list[str]:
        raw = str(((broker.get("metadata", {}) or {}).get("annotations", {}) or {}).get(_TRIGGERS) or "[]")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Knative Broker managed-trigger inventory is invalid") from exc
        if not isinstance(value, list) or any(not isinstance(name, str) or not name for name in value):
            raise ValueError("Knative Broker managed-trigger inventory is invalid")
        return list(value)

    @staticmethod
    def _condition(resource: dict[str, Any], condition_type: str) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in (resource.get("status", {}) or {}).get("conditions", []) or []
                if row.get("type") == condition_type
            ),
            None,
        )

    @staticmethod
    def _is_true(condition: dict[str, Any] | None) -> bool:
        return bool(condition and str(condition.get("status") or "").lower() == "true")

    @staticmethod
    def _address(broker: dict[str, Any]) -> str:
        return str(((broker.get("status", {}) or {}).get("address", {}) or {}).get("url") or "")

    def _require_driver(self) -> None:
        if self._config.cluster_driver is None:
            raise ValueError("Knative Eventing requires a live cluster driver")

    @staticmethod
    def _parsed(handle: str) -> ParsedHandle:
        parsed = _unpack_handle(handle)
        if parsed.is_legacy:
            raise ValueError("legacy Knative Broker handle has no cluster locator")
        return parsed

    def _broker(self, parsed: ParsedHandle) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                f"{API_VERSION}/{RESOURCE_KIND}",
                parsed.name,
            ),
        )

    def _trigger(self, parsed: ParsedHandle, name: str) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._config.cluster_driver.get_manifest(
                parsed.cluster_id,
                parsed.namespace,
                f"{API_VERSION}/{TRIGGER_KIND}",
                name,
            ),
        )

    @staticmethod
    def _stub(kind: str, name: str, namespace: str) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "kind": kind,
            "metadata": {"name": name, "namespace": namespace},
        }
