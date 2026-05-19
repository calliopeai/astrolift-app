"""SQS Queue managed-service driver (#35).

Spec ref: spec 23-provider-plugin-aws + _sdk/managed_service.py.
"""

from __future__ import annotations

import json
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
from aws._errors import map_client_error
from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)


KIND = "queue"


@dataclass(frozen=True)
class SQSConfig:
    region: str
    queue_name_prefix: str = "astrolift"

    default_visibility_timeout_seconds: int = 30
    """Standard. Operator overrides per-binding via spec.config."""

    default_message_retention_seconds: int = 4 * 86400
    """4-day default. SQS allows up to 14 days."""

    fifo_default: bool = False
    """When True, provisioned queues are FIFO (.fifo suffix +
    ContentBasedDeduplication=true). False = standard."""


# Map ProvisionSpec.size → SQS-equivalent settings. SQS doesn't
# have 'sizes' the way RDS does — bigger 'sizes' translate to
# longer retention + higher visibility timeout.
SIZE_TO_SETTINGS = {
    "small": {"visibility_timeout": 30, "retention_days": 4},
    "medium": {"visibility_timeout": 60, "retention_days": 7},
    "large": {"visibility_timeout": 300, "retention_days": 14},
    "xlarge": {"visibility_timeout": 900, "retention_days": 14},
    # 'custom' uses spec.config explicitly
}


class SQSDriver(ManagedServiceDriver):
    def __init__(
        self, *, config: SQSConfig, client: Any | None = None,
    ) -> None:
        self._config = config
        if client is not None:
            self._sqs = client
        else:
            import boto3

            self._sqs = boto3.client("sqs", region_name=config.region)

    # ---- lifecycle ------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="queue_sqs",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        queue_name = self._queue_name_for(spec=spec)
        is_fifo = bool(
            spec.config.get("fifo", self._config.fifo_default),
        )
        if is_fifo and not queue_name.endswith(".fifo"):
            queue_name = queue_name + ".fifo"

        size_settings = SIZE_TO_SETTINGS.get(spec.size, {})
        visibility = spec.config.get(
            "visibility_timeout_seconds",
            size_settings.get(
                "visibility_timeout",
                self._config.default_visibility_timeout_seconds,
            ),
        )
        retention_days = size_settings.get(
            "retention_days",
            self._config.default_message_retention_seconds // 86400,
        )
        retention_seconds = spec.config.get(
            "message_retention_seconds",
            retention_days * 86400,
        )

        attributes: dict[str, str] = {
            "VisibilityTimeout": str(visibility),
            "MessageRetentionPeriod": str(retention_seconds),
        }
        if is_fifo:
            attributes["FifoQueue"] = "true"
            attributes["ContentBasedDeduplication"] = "true"

        try:
            response = self._sqs.create_queue(
                QueueName=queue_name,
                Attributes=attributes,
                tags={t["Key"]: t["Value"] for t in tags_for(spec)},
            )
        except self._sqs.exceptions.QueueNameExists:
            # Idempotent — same name + attributes is OK
            try:
                response = self._sqs.get_queue_url(QueueName=queue_name)
            except Exception as exc:  # noqa: BLE001
                return ProvisionResult(
                    ok=False, handle="",
                    message=f"queue already exists but lookup failed: {exc}",
                    errors=[str(exc)],
                )
        except Exception as exc:  # noqa: BLE001
            return ProvisionResult(
                ok=False, handle="",
                message=f"create_queue: {exc}",
                errors=[str(exc)],
            )

        queue_url = response["QueueUrl"]
        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=queue_name),
            message=f"queue {queue_name} provisioned at {queue_url}",
        )

    @driver_op(cloud="aws", driver="queue_sqs")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, queue_name = parse_handle(spec.handle)
        try:
            queue_url = self._queue_url(queue_name=queue_name)
        except ManagedServiceError as exc:
            return UpdateResult(
                ok=False, handle=spec.handle, message=str(exc),
                errors=[str(exc)],
            )

        attributes: dict[str, str] = {}
        if spec.size and spec.size in SIZE_TO_SETTINGS:
            settings = SIZE_TO_SETTINGS[spec.size]
            attributes["VisibilityTimeout"] = str(settings["visibility_timeout"])
            attributes["MessageRetentionPeriod"] = str(
                settings["retention_days"] * 86400,
            )

        for key in ("VisibilityTimeout", "MessageRetentionPeriod"):
            cfg_key = key[0].lower() + key[1:]  # camelCase config name
            if cfg_key in spec.config:
                attributes[key] = str(spec.config[cfg_key])

        if not attributes:
            return UpdateResult(
                ok=True, handle=spec.handle,
                message="no updatable attributes in spec",
            )

        try:
            self._sqs.set_queue_attributes(
                QueueUrl=queue_url, Attributes=attributes,
            )
        except Exception as exc:  # noqa: BLE001
            return UpdateResult(
                ok=False, handle=spec.handle,
                message=f"set_queue_attributes: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True, handle=spec.handle,
            message=f"queue {queue_name} updated",
        )

    @driver_op(
        cloud="aws",
        driver="queue_sqs",
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
        # Queues have no persistent-state semantic worth preserving:
        # in-flight messages cannot be snapshotted, and a retained
        # empty queue serves no purpose. Both flags are accepted for
        # Protocol symmetry but neither changes the outcome — the
        # queue is deleted. force_destroy has no SQS-side guard to
        # bypass (no deletion protection, no min-retention).
        del delete_data, force_destroy
        _, queue_name = parse_handle(spec.handle)
        try:
            queue_url = self._queue_url(queue_name=queue_name)
        except ManagedServiceError:
            return DeprovisionResult(
                ok=True, handle=spec.handle,
                message=f"queue {queue_name} already gone",
            )

        try:
            self._sqs.delete_queue(QueueUrl=queue_url)
        except Exception as exc:  # noqa: BLE001
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=f"delete_queue: {exc}",
                errors=[str(exc)],
            )
        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=f"queue {queue_name} deleted",
        )

    # ---- read-only ops --------------------------------------------

    @driver_op(cloud="aws", driver="queue_sqs")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, queue_name = parse_handle(handle.handle)
        try:
            self._queue_url(queue_name=queue_name)
        except ManagedServiceError:
            return ServiceStatus(
                handle=handle.handle, state="deprovisioned",
                message=f"queue {queue_name} does not exist",
            )
        return ServiceStatus(
            handle=handle.handle, state="available",
            message=f"queue {queue_name} reachable",
        )

    @driver_op(cloud="aws", driver="queue_sqs")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, queue_name = parse_handle(handle.handle)
        # Construct the ARN deterministically — derivable from
        # name + region + account; we don't need to query SQS for
        # it on every binding call (SQS exposes via
        # GetQueueAttributes(QueueArn) but that's an extra call).
        # Caller can pass the account_id via config but the ARN
        # is also visible via env_vars below.
        try:
            queue_url = self._queue_url(queue_name=queue_name)
            response = self._sqs.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=["QueueArn"],
            )
            queue_arn = response["Attributes"]["QueueArn"]
        except Exception:
            # Best-effort ARN — derivable form
            queue_arn = (
                f"arn:aws:sqs:{self._config.region}:UNKNOWN:{queue_name}"
            )
            queue_url = (
                f"https://sqs.{self._config.region}.amazonaws.com/UNKNOWN/{queue_name}"
            )

        return Binding(
            env_vars={
                "SQS_QUEUE_NAME": ValueRef(literal=queue_name),
                "SQS_QUEUE_URL": ValueRef(literal=queue_url),
                "SQS_QUEUE_ARN": ValueRef(literal=queue_arn),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=[
                Grant(
                    resource=queue_arn,
                    actions=[
                        "sqs:SendMessage",
                        "sqs:ReceiveMessage",
                        "sqs:DeleteMessage",
                        "sqs:GetQueueAttributes",
                        "sqs:GetQueueUrl",
                    ],
                ),
            ],
            notes=(
                "Standard SQS perms for produce/consume + receive."
            ),
        )

    @driver_op(cloud="aws", driver="queue_sqs")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """SQS doesn't support snapshots — in-flight messages are
        ephemeral by definition. Per platform-side #87/#127 policy:
        queues never snapshot."""
        raise ManagedServiceError(
            "SQS does not support snapshots — in-flight messages "
            "have no recovery value (per platform-side #87/#127)",
        )

    @driver_op(cloud="aws", driver="queue_sqs")
    def restore(
        self, snapshot: SnapshotHandle, target: ProvisionSpec,
    ) -> ProvisionResult:
        raise ManagedServiceError(
            "SQS does not support snapshots, hence no restore",
        )

    @driver_op(cloud="aws", driver="queue_sqs", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "fifo": {
                    "type": "boolean",
                    "description": "Provision a FIFO queue.",
                },
                "visibility_timeout_seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 43200,
                },
                "message_retention_seconds": {
                    "type": "integer",
                    "minimum": 60,
                    "maximum": 14 * 86400,
                },
            },
        }

    @driver_op(cloud="aws", driver="queue_sqs", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(env_vars={
            "SQS_QUEUE_NAME": "Queue name",
            "SQS_QUEUE_URL": "SQS HTTPS URL for SDK calls",
            "SQS_QUEUE_ARN": "Full queue ARN",
            "AWS_REGION": "Queue's region",
        })

    # ---- internals ------------------------------------------------

    def _queue_name_for(self, *, spec: ProvisionSpec) -> str:
        parts = [
            self._config.queue_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p)
        # SQS names: alphanumeric + dash + underscore; max 80 chars
        # (.fifo suffix added separately for FIFO queues, max 80
        # total)
        clean = "".join(
            c if c.isalnum() or c in "-_" else "-"
            for c in raw
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:75]  # leaves room for .fifo suffix

    def _queue_url(self, *, queue_name: str) -> str:
        try:
            response = self._sqs.get_queue_url(QueueName=queue_name)
        except self._sqs.exceptions.QueueDoesNotExist as exc:
            raise ManagedServiceError(
                f"queue {queue_name} does not exist",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
        return response["QueueUrl"]
