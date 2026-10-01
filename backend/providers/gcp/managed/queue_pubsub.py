"""GCP queue/pubsub owns one topic and default subscription.

New targets retain the complete persisted service UUID. Cleanup requires current
ownership, complete bounded child inventory and explicit data deletion; no seek,
fictional drain or force-success bypass is performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
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
    unsupported_update,
)
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp.managed._ownership import label_identity_refusal
from gcp.managed.topic_pubsub import _already_exists, _get, _not_found, _resource_id, _service_identity

KIND = "queue"


class PubSubQueueError(RuntimeError):
    def __init__(self, message: str, code: str = "ownership_unknown") -> None:
        super().__init__(message)
        self.code = code


class _CallBudget:
    def __init__(self) -> None:
        self.deadline = monotonic() + 20

    def call(self, method: Any, request: dict[str, Any]) -> Any:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise PubSubQueueError("Pub/Sub queue observation budget exceeded")
        return method(request=request, timeout=min(5, remaining), retry=None)


@dataclass(frozen=True)
class PubSubConfig:
    project_id: str
    topic_prefix: str = "astrolift"
    publisher_client: Any | None = None
    subscriber_client: Any | None = None


class PubSubDriver(ManagedServiceDriver):
    def __init__(self, *, config: PubSubConfig) -> None:
        self._config = config
        if config.publisher_client is not None:
            self._pub = config.publisher_client
        else:
            import google.cloud.pubsub_v1 as pubsub_v1

            self._pub = pubsub_v1.PublisherClient()
        if config.subscriber_client is not None:
            self._sub = config.subscriber_client
        else:
            import google.cloud.pubsub_v1 as pubsub_v1

            self._sub = pubsub_v1.SubscriberClient()

    @driver_op(
        cloud="gcp",
        driver="queue_pubsub",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        budget = _CallBudget()
        try:
            _service_identity(spec.managed_service_id)
            topic_id = _parse_handle(spec.recorded_handle) if spec.recorded_handle else self._topic_id(spec=spec)
        except ValueError as exc:
            return ProvisionResult(False, "", str(exc), ["invalid_resource_identity"])
        record_proves = spec.recorded_handle_exclusive and spec.recorded_handle == f"{KIND}/{topic_id}"
        try:
            topic_path, sub_path = self._paths(topic_id)
            topic, child = self._resources(topic_id, spec.managed_service_id, record_proves, budget)
            if topic is None:
                try:
                    budget.call(self._pub.create_topic, {"name": topic_path, "labels": _labels_for(spec)})
                except Exception as exc:
                    if not _already_exists(exc):
                        raise
                topic, child = self._resources(topic_id, spec.managed_service_id, False, budget)
                if topic is None:
                    raise PubSubQueueError("created Pub/Sub queue topic could not be observed")
            if child is None:
                try:
                    budget.call(
                        self._sub.create_subscription,
                        {
                            "name": sub_path,
                            "topic": topic_path,
                            "labels": {MANAGED_SERVICE_ID_LABEL: spec.managed_service_id},
                        },
                    )
                except Exception as exc:
                    if not _already_exists(exc):
                        raise
            topic, child = self._resources(topic_id, spec.managed_service_id, record_proves, budget)
            if topic is None or child is None:
                raise PubSubQueueError("Pub/Sub queue creation has not been observed")
        except Exception as exc:
            return ProvisionResult(False, "", f"provision Pub/Sub queue: {exc}", [_error_code(exc)])
        return ProvisionResult(
            True, f"{KIND}/{topic_id}", "Pub/Sub queue topic and default subscription observed", ready=True
        )

    @driver_op(cloud="gcp", driver="queue_pubsub")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            topic_id = _parse_handle(spec.handle)
            self._resources(topic_id, spec.managed_service_id, spec.recorded_handle_exclusive, _CallBudget())
        except Exception as exc:
            return UpdateResult(
                False, spec.handle, f"verify Pub/Sub queue update target: {exc}", [_error_code(exc)], retryable=False
            )
        return unsupported_update(spec.handle, "Pub/Sub queue has no editable settings")

    @driver_op(
        cloud="gcp",
        driver="queue_pubsub",
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
        budget = _CallBudget()
        try:
            topic_id = _parse_handle(spec.handle)
            topic, child = self._resources(topic_id, spec.managed_service_id, spec.recorded_handle_exclusive, budget)
            topic_path, sub_path = self._paths(topic_id)
            if topic is None and child is None:
                return DeprovisionResult(True, spec.handle, "Pub/Sub queue recorded targets already gone")
            if not delete_data:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Pub/Sub queue may retain messages; explicit delete_data=true is required; snapshot is unsupported",
                    ["retained_messages_require_delete_data"],
                    retryable=False,
                )
            paths = self._inventory(topic_path, budget)
            outside = set(paths) - {sub_path}
            if outside and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Pub/Sub queue has subscriptions outside its default declaration; force_destroy is required",
                    ["foreign_subscriptions_require_force_destroy", "ownership_unknown"],
                    retryable=False,
                )
            owned = {sub_path} if child is not None else set()
            for path in outside:
                current = self._read(self._sub.get_subscription, {"subscription": path}, budget)
                if current is not None:
                    self._assert_child(current, topic_path, spec.managed_service_id, expected_path=path, legacy=False)
                    owned.add(path)
            for path in sorted(owned):
                try:
                    budget.call(self._sub.delete_subscription, {"subscription": path})
                except Exception as exc:
                    if not _not_found(exc):
                        raise
            try:
                budget.call(self._pub.delete_topic, {"topic": topic_path})
            except Exception as exc:
                if not _not_found(exc):
                    raise
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"delete Pub/Sub queue: {exc}",
                [_error_code(exc)],
                retryable=_error_code(exc) != "ownership_refused",
            )
        return DeprovisionResult(True, spec.handle, "Pub/Sub queue observed topic and owned subscriptions deleted")

    @driver_op(cloud="gcp", driver="queue_pubsub")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            topic, child = self._resources(
                _parse_handle(handle.handle), handle.managed_service_id, handle.recorded_handle_exclusive, _CallBudget()
            )
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"verify Pub/Sub queue: {exc}")
        if topic is None and child is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Pub/Sub queue recorded targets absent")
        if topic is None or child is None:
            return ServiceStatus(handle.handle, "error", "Pub/Sub queue target is incomplete")
        return ServiceStatus(handle.handle, "available", "Pub/Sub queue topic and default subscription observed")

    @driver_op(cloud="gcp", driver="queue_pubsub")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        topic_id = _parse_handle(handle.handle)
        topic, child = self._resources(
            topic_id, handle.managed_service_id, handle.recorded_handle_exclusive, _CallBudget()
        )
        if topic is None or child is None:
            raise PubSubQueueError("Pub/Sub queue binding target is incomplete")
        topic_path, sub_path = self._paths(topic_id)
        return Binding(
            env_vars={
                "PUBSUB_TOPIC": ValueRef(literal=topic_path),
                "PUBSUB_SUBSCRIPTION": ValueRef(literal=sub_path),
                "GCP_PROJECT_ID": ValueRef(
                    literal=self._config.project_id,
                ),
            },
            iam_grants=[
                Grant(
                    resource=topic_path,
                    actions=["roles/pubsub.publisher"],
                ),
                Grant(
                    resource=sub_path,
                    actions=["roles/pubsub.subscriber"],
                ),
            ],
            notes="Publisher + subscriber role bindings.",
        )

    @driver_op(cloud="gcp", driver="queue_pubsub")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.snapshot(Pub/Sub) not supported -- Pub/Sub messages are ephemeral; no snapshot (#618)",
        )

    @driver_op(cloud="gcp", driver="queue_pubsub")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.restore(Pub/Sub) not supported -- Pub/Sub doesn't restore from snapshot (#618)",
        )

    @driver_op(cloud="gcp", driver="queue_pubsub", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @driver_op(cloud="gcp", driver="queue_pubsub", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "PUBSUB_TOPIC": "Full topic resource path",
                "PUBSUB_SUBSCRIPTION": "Full subscription resource path",
                "GCP_PROJECT_ID": "Project ID",
            }
        )

    def editable_fields(self) -> list[str]:
        """No config key can be applied without a reprovision (#1376)."""
        # ack_deadline / retention would need a subscription patch, which this
        # driver only issues while provisioning.
        return []

    def _topic_id(self, *, spec: ProvisionSpec) -> str:
        identity = _service_identity(spec.managed_service_id)
        if not isinstance(self._config.topic_prefix, str):
            raise ValueError("Pub/Sub queue prefix must be a string")
        prefix = _resource_id(self._config.topic_prefix or "astrolift", max_length=218)
        return f"{prefix}-{identity}"

    def _paths(self, topic_id: str) -> tuple[str, str]:
        project = self._config.project_id
        if not isinstance(project, str) or not project or project.strip() != project or "/" in project:
            raise PubSubQueueError("Pub/Sub queue configured project identity is unavailable")
        return (
            self._pub.topic_path(self._config.project_id, topic_id),
            self._sub.subscription_path(self._config.project_id, f"{topic_id}-sub"),
        )

    @staticmethod
    def _read(method: Any, request: dict[str, Any], budget: _CallBudget) -> Any | None:
        try:
            return budget.call(method, request)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _resources(
        self, topic_id: str, managed_service_id: str, exclusive: bool, budget: _CallBudget
    ) -> tuple[Any | None, Any | None]:
        _service_identity(managed_service_id)
        topic_path, sub_path = self._paths(topic_id)
        topic = self._read(self._pub.get_topic, {"topic": topic_path}, budget)
        if topic is not None:
            if _get(topic, "name", "") != topic_path:
                raise PubSubQueueError("Pub/Sub queue topic response does not match the recorded physical target")
            refusal = label_identity_refusal(
                dict(_get(topic, "labels", {}) or {}),
                managed_service_id,
                record_proves=exclusive,
                resource="Pub/Sub queue topic",
            )
            if refusal:
                raise PubSubQueueError(refusal, "ownership_refused")
        child = self._read(self._sub.get_subscription, {"subscription": sub_path}, budget)
        if child is not None:
            if topic is None:
                raise PubSubQueueError("Pub/Sub queue parent is absent while its default subscription remains")
            self._assert_child(child, topic_path, managed_service_id, expected_path=sub_path, legacy=True)
        return topic, child

    @staticmethod
    def _assert_child(
        child: Any, topic_path: str, managed_service_id: str, *, expected_path: str, legacy: bool
    ) -> None:
        if _get(child, "name", "") != expected_path:
            raise PubSubQueueError("Pub/Sub queue subscription response does not match the recorded physical target")
        if _get(child, "topic", "") != topic_path:
            raise PubSubQueueError("Pub/Sub queue subscription belongs to another topic", "ownership_refused")
        refusal = label_identity_refusal(
            dict(_get(child, "labels", {}) or {}),
            managed_service_id,
            record_proves=legacy,
            resource="Pub/Sub queue subscription",
        )
        if refusal:
            raise PubSubQueueError(refusal, "ownership_refused")

    def _inventory(self, topic_path: str, budget: _CallBudget) -> list[str]:
        paths: list[str] = []
        token = ""
        seen = set()
        for _ in range(10):
            reply = budget.call(
                self._pub.list_topic_subscriptions, {"topic": topic_path, "page_size": 100, "page_token": token}
            )
            page = next(iter(reply.pages)) if hasattr(reply, "pages") else reply
            rows = _get(page, "subscriptions", page)
            for row in rows:
                path = str(_get(row, "name", row))
                if not path.startswith(f"projects/{self._config.project_id}/subscriptions/"):
                    raise PubSubQueueError(
                        "Pub/Sub queue has a subscription outside its configured project", "ownership_refused"
                    )
                if path in paths or len(paths) >= 1000:
                    raise PubSubQueueError("Pub/Sub queue subscription inventory is invalid or exceeds its item budget")
                paths.append(path)
            token = str(_get(page, "next_page_token", "") or "")
            if not token:
                return paths
            if token in seen:
                raise PubSubQueueError("Pub/Sub queue subscription pagination repeated a cursor")
            seen.add(token)
        raise PubSubQueueError("Pub/Sub queue subscription inventory exceeds its page budget")


def _parse_handle(handle: str) -> str:
    import re

    kind, separator, topic_id = handle.partition("/")
    if (
        kind != KIND
        or not separator
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9._~+%\-]{2,250}", topic_id)
        or topic_id.lower().startswith("goog")
    ):
        raise ValueError("invalid Pub/Sub queue handle; expected exact queue/<topic-id> with room for -sub")
    return topic_id


def _error_code(exc: Exception) -> str:
    return exc.code if isinstance(exc, PubSubQueueError) else "ownership_unknown"


def _labels_for(spec: ProvisionSpec) -> dict[str, str]:
    """Pub/Sub labels: lowercase [a-z0-9_-] keys and values, at most 63 chars."""

    def _clean(value: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in str(value).lower())[:63]

    labels = {
        "astrolift-managed-by": "platform",
        "astrolift-organization": _clean(spec.organization_slug),
        "astrolift-app": _clean(spec.app_slug),
        "astrolift-environment": _clean(spec.environment_name),
    }
    if spec.managed_service_id:
        labels["astrolift-managed-service-id"] = spec.managed_service_id
        labels[MANAGED_SERVICE_ID_LABEL] = spec.managed_service_id
    return labels
