"""GCP Pub/Sub queue managed-service driver (#41)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
from gcp._errors import map_api_error


KIND = "queue"


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
            from google.cloud import pubsub_v1

            self._pub = pubsub_v1.PublisherClient()
        if config.subscriber_client is not None:
            self._sub = config.subscriber_client
        else:
            from google.cloud import pubsub_v1

            self._sub = pubsub_v1.SubscriberClient()

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        topic_id = self._topic_id(spec=spec)
        sub_id = f"{topic_id}-sub"
        topic_path = self._pub.topic_path(self._config.project_id, topic_id)
        sub_path = self._sub.subscription_path(
            self._config.project_id, sub_id,
        )
        try:
            self._pub.create_topic(request={"name": topic_path})
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ != "AlreadyExists":
                return ProvisionResult(
                    ok=False, handle="",
                    message=f"create_topic: {exc}",
                    errors=[str(exc)],
                )
        try:
            self._sub.create_subscription(
                request={"name": sub_path, "topic": topic_path},
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ != "AlreadyExists":
                return ProvisionResult(
                    ok=False, handle="",
                    message=f"create_subscription: {exc}",
                    errors=[str(exc)],
                )
        return ProvisionResult(
            ok=True,
            handle=f"{KIND}/{topic_id}",
            message=f"Pub/Sub topic + subscription provisioned: {topic_id}",
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True, handle=spec.handle,
            message="pubsub mutable settings (ack_deadline, retention) "
                    "via subscription patch are operator-managed",
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        del delete_data, force_destroy
        _, _, topic_id = spec.handle.partition("/")
        sub_id = f"{topic_id}-sub"
        try:
            self._sub.delete_subscription(
                request={
                    "subscription": self._sub.subscription_path(
                        self._config.project_id, sub_id,
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ != "NotFound":
                return DeprovisionResult(
                    ok=False, handle=spec.handle,
                    message=str(exc), errors=[str(exc)],
                )
        try:
            self._pub.delete_topic(
                request={
                    "topic": self._pub.topic_path(
                        self._config.project_id, topic_id,
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ != "NotFound":
                return DeprovisionResult(
                    ok=False, handle=spec.handle,
                    message=str(exc), errors=[str(exc)],
                )
        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=f"Pub/Sub topic + subscription deleted: {topic_id}",
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, _, topic_id = handle.handle.partition("/")
        try:
            self._pub.get_topic(
                request={
                    "topic": self._pub.topic_path(
                        self._config.project_id, topic_id,
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "NotFound":
                return ServiceStatus(
                    handle=handle.handle, state="deprovisioned",
                    message=f"topic {topic_id} does not exist",
                )
            return ServiceStatus(
                handle=handle.handle, state="error", message=str(exc),
            )
        return ServiceStatus(
            handle=handle.handle, state="available",
            message=f"topic {topic_id} reachable",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, topic_id = handle.handle.partition("/")
        topic_path = (
            f"projects/{self._config.project_id}/topics/{topic_id}"
        )
        sub_path = (
            f"projects/{self._config.project_id}"
            f"/subscriptions/{topic_id}-sub"
        )
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

    def snapshot(self, handle):
        raise NotImplementedError("Pub/Sub messages are ephemeral; no snapshot")

    def restore(self, snapshot, target):
        raise NotImplementedError("Pub/Sub doesn't restore")

    def config_schema(self):
        return {"type": "object", "properties": {}}

    def binding_schema(self):
        return BindingSchema(env_vars={
            "PUBSUB_TOPIC": "Full topic resource path",
            "PUBSUB_SUBSCRIPTION": "Full subscription resource path",
            "GCP_PROJECT_ID": "Project ID",
        })

    def _topic_id(self, *, spec: ProvisionSpec) -> str:
        parts = [
            self._config.topic_prefix,
            spec.organization_slug, spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p)
        # Pub/Sub topic IDs: alphanumeric + dash + underscore; max 255
        clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:255]
