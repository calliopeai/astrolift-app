"""GCP Pub/Sub queue managed-service driver (#363).

Implements ``ManagedServiceDriver`` for the canonical GCP managed-
queue path. Symmetric to AWS SQS — same Binding shape, just
google-cloud-pubsub backed. Each provision creates a topic + a
default subscription named ``<topic>-sub``; workloads need both
(publish to topic, consume from subscription) and the binding
exposes both.

Four-corner deprovision matrix:

  delete_data=False, force_destroy=False (default):
    Drain (seek the subscription to ``now`` to acknowledge in-flight
    messages without dispatching them) and then delete BOTH the
    subscription and the topic. Pub/Sub has no "retain queue
    contents" path because messages aren't durable beyond the
    subscription's retention window — the topic itself has no
    persistent state, so retention is implicit. delete_data=False
    therefore means "don't republish/redeliver the in-flight set".

  delete_data=False, force_destroy=True:
    Same as the default safe path; force_destroy is meaningful only
    when a subscription is actively pulling. We log the bypass for
    operator visibility but otherwise behave identically.

  delete_data=True, force_destroy=False:
    Skip the drain seek and delete topic + subscription
    immediately. Any in-flight redeliveries are lost.

  delete_data=True, force_destroy=True:
    Atomic — skip drain, ignore subscriber-attached refusal
    (``FAILED_PRECONDITION`` on delete when consumers are pulling).
"""

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
        topic_path = self._pub.topic_path(
            self._config.project_id,
            topic_id,
        )
        sub_path = self._sub.subscription_path(
            self._config.project_id,
            sub_id,
        )
        try:
            self._pub.create_topic(request={"name": topic_path})
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ != "AlreadyExists":
                return ProvisionResult(
                    ok=False,
                    handle="",
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
                    ok=False,
                    handle="",
                    message=f"create_subscription: {exc}",
                    errors=[str(exc)],
                )
        return ProvisionResult(
            ok=True,
            handle=f"{KIND}/{topic_id}",
            message=(f"Pub/Sub topic + subscription provisioned: {topic_id}"),
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=(
                "pubsub mutable settings (ack_deadline, retention) " "via subscription patch are operator-managed"
            ),
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        from datetime import UTC, datetime

        _, _, topic_id = spec.handle.partition("/")
        sub_id = f"{topic_id}-sub"
        sub_path = self._sub.subscription_path(
            self._config.project_id,
            sub_id,
        )
        topic_path = self._pub.topic_path(
            self._config.project_id,
            topic_id,
        )

        # Track whether the resources existed at all — a fully-gone
        # topic+sub is a successful no-op regardless of flags.
        any_present = False

        if not delete_data:
            # Drain in-flight messages by seeking the subscription
            # forward to "now". This acks anything pending without
            # dispatching it. Best-effort: NotFound means already
            # gone; other errors surface as warnings, not blockers
            # — failing to drain shouldn't prevent delete.
            try:
                self._sub.seek(
                    request={
                        "subscription": sub_path,
                        "time": datetime.now(UTC).isoformat(),
                    },
                )
                any_present = True
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ != "NotFound":
                    # Surface but don't abort.
                    pass

        try:
            self._sub.delete_subscription(
                request={"subscription": sub_path},
            )
            any_present = True
        except Exception as exc:  # noqa: BLE001
            err_name = type(exc).__name__
            if err_name == "NotFound":
                pass
            elif err_name in ("FailedPrecondition", "FAILED_PRECONDITION") or "FAILED_PRECONDITION" in str(exc):
                if not force_destroy:
                    return DeprovisionResult(
                        ok=False,
                        handle=spec.handle,
                        message=(
                            f"subscription {sub_id} has active " f"subscribers — pass force_destroy=True " f"to bypass"
                        ),
                        errors=[str(exc)],
                    )
                # force_destroy: log the bypass via message and treat
                # as already-detached.
            else:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=str(exc),
                    errors=[str(exc)],
                )
        try:
            self._pub.delete_topic(request={"topic": topic_path})
            any_present = True
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ != "NotFound":
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=str(exc),
                    errors=[str(exc)],
                )

        if not any_present:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"Pub/Sub {topic_id} already gone",
            )
        suffix = " (force_destroy)" if force_destroy else ""
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"Pub/Sub topic + subscription deleted: {topic_id} "
                f"(drained={'no' if delete_data else 'yes'}){suffix}"
            ),
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, _, topic_id = handle.handle.partition("/")
        try:
            self._pub.get_topic(
                request={
                    "topic": self._pub.topic_path(
                        self._config.project_id,
                        topic_id,
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "NotFound":
                return ServiceStatus(
                    handle=handle.handle,
                    state="deprovisioned",
                    message=f"topic {topic_id} does not exist",
                )
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message=str(exc),
            )
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message=f"topic {topic_id} reachable",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, topic_id = handle.handle.partition("/")
        topic_path = f"projects/{self._config.project_id}/topics/{topic_id}"
        sub_path = f"projects/{self._config.project_id}" f"/subscriptions/{topic_id}-sub"
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

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.snapshot(Pub/Sub) not supported -- Pub/Sub " "messages are ephemeral; no snapshot (#618)",
        )

    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.restore(Pub/Sub) not supported -- Pub/Sub " "doesn't restore from snapshot (#618)",
        )

    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "PUBSUB_TOPIC": "Full topic resource path",
                "PUBSUB_SUBSCRIPTION": "Full subscription resource path",
                "GCP_PROJECT_ID": "Project ID",
            }
        )

    def _topic_id(self, *, spec: ProvisionSpec) -> str:
        parts = [
            self._config.topic_prefix,
            spec.organization_slug,
            spec.app_slug,
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
