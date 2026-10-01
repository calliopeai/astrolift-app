"""SQS Queue managed-service driver (#35).

Spec ref: spec 23-provider-plugin-aws + _sdk/managed_service.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.cloud_credentials import CredentialedConfig
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
    adoption_refusal,
    assert_resource_arn,
    handle_for,
    live_ownership_refusal,
    parse_handle,
    tags_for,
)
from aws.session import aws_client

KIND = "queue"


@dataclass(frozen=True)
class SQSConfig(CredentialedConfig):
    region: str
    account_id: str = ""
    queue_name_prefix: str = "astrolift"

    default_visibility_timeout_seconds: int = 30
    """Standard. Operator overrides per-binding via spec.config."""

    default_message_retention_seconds: int = 4 * 86400
    """4-day default. SQS allows up to 14 days."""

    fifo_default: bool = False
    """When True, provisioned queues are FIFO (.fifo suffix +
    ContentBasedDeduplication=true). False = standard."""

    kms_key_id: str = ""
    sqs_managed_sse_default: bool = True


_CONFIG_TO_ATTRIBUTE = {
    "content_based_deduplication": "ContentBasedDeduplication",
    "deduplication_scope": "DeduplicationScope",
    "delay_seconds": "DelaySeconds",
    "fifo_throughput_limit": "FifoThroughputLimit",
    "kms_data_key_reuse_period_seconds": "KmsDataKeyReusePeriodSeconds",
    "kms_master_key_id": "KmsMasterKeyId",
    "maximum_message_size": "MaximumMessageSize",
    "message_retention_seconds": "MessageRetentionPeriod",
    "policy": "Policy",
    "receive_message_wait_time_seconds": "ReceiveMessageWaitTimeSeconds",
    "redrive_allow_policy": "RedriveAllowPolicy",
    "redrive_policy": "RedrivePolicy",
    "sqs_managed_sse_enabled": "SqsManagedSseEnabled",
    "visibility_timeout_seconds": "VisibilityTimeout",
}


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
        self,
        *,
        config: SQSConfig,
        client: Any | None = None,
    ) -> None:
        self._config = config
        if client is not None:
            self._sqs = client
        else:
            self._sqs = aws_client("sqs", region=config.region, credential=config.credential)

    # ---- lifecycle ------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="queue_sqs",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_sqs_config"])
        queue_name = self._queue_name_for(spec=spec)
        is_fifo = bool(cfg.get("fifo", self._config.fifo_default))
        if is_fifo and not queue_name.endswith(".fifo"):
            queue_name += ".fifo"
        attributes = self._attributes(cfg, size=spec.size, fifo=is_fifo, creating=True)

        # create_queue returns an existing queue's URL when the attributes match,
        # and the reconcile below re-tags it: only this service's is adopted (#1961).
        refusal = self._queue_refusal(queue_name, spec)
        if refusal is not None:
            return ProvisionResult(False, "", refusal, [refusal])
        try:
            response = self._sqs.create_queue(
                QueueName=queue_name,
                Attributes=attributes,
                tags={t["Key"]: t["Value"] for t in tags_for(spec)},
            )
        except Exception as exc:
            if not _queue_exists_error(exc):
                return ProvisionResult(False, "", f"create_queue: {exc}", [str(exc)])
            try:
                response = self._sqs.get_queue_url(QueueName=queue_name)
                mutable = {key: value for key, value in attributes.items() if key != "FifoQueue"}
                if mutable:
                    self._sqs.set_queue_attributes(
                        QueueUrl=response["QueueUrl"],
                        Attributes=mutable,
                    )
                self._sqs.tag_queue(
                    QueueUrl=response["QueueUrl"],
                    Tags={tag["Key"]: tag["Value"] for tag in tags_for(spec)},
                )
            except Exception as lookup_exc:
                return ProvisionResult(
                    False,
                    "",
                    f"queue exists but reconciliation failed: {lookup_exc}",
                    [str(lookup_exc)],
                )

        queue_url = response["QueueUrl"]
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=queue_name),
            f"SQS {'FIFO' if is_fifo else 'standard'} queue {queue_name} available at {queue_url}",
            ready=True,
        )

    def _queue_refusal(self, queue_name: str, spec: ProvisionSpec) -> str | None:
        try:
            queue_url = self._sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
        except Exception as exc:
            if "NonExistentQueue" in str(exc) or "QueueDoesNotExist" in f"{type(exc).__name__} {exc}":
                return None  # no such queue: nothing to adopt
            return f"queue {queue_name}: ownership could not be verified: {exc}"
        try:
            tags = self._sqs.list_queue_tags(QueueUrl=queue_url).get("Tags") or {}
        except Exception:  # ownership unverifiable, so not adopted
            tags = {}
        return adoption_refusal(tags, spec, resource=f"queue {queue_name}")

    @driver_op(cloud="aws", driver="queue_sqs")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, queue_name = parse_handle(spec.handle)
        cfg = spec.config or {}
        is_fifo = queue_name.endswith(".fifo")
        error = self._validate_config(cfg, updating=True, fifo=is_fifo)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_sqs_config"])
        try:
            queue_url = self._queue_url(queue_name=queue_name)
        except ManagedServiceError as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[str(exc)],
            )

        attributes = self._attributes(cfg, size=spec.size or "", fifo=is_fifo, creating=False)

        if not attributes:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no updatable attributes in spec",
            )

        try:
            self._sqs.set_queue_attributes(
                QueueUrl=queue_url,
                Attributes=attributes,
            )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"set_queue_attributes: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
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
        _, queue_name = parse_handle(spec.handle)
        try:
            queue_url = self._queue_url(queue_name=queue_name)
        except ManagedServiceError:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"queue {queue_name} already gone",
            )

        try:
            if spec.config.get("deletion_protection", False) and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"SQS queue {queue_name} has Astrolift deletion protection enabled",
                    ["deletion_protection_enabled"],
                    retryable=False,
                )
            attrs = self._sqs.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=[
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                    "ApproximateNumberOfMessagesDelayed",
                ],
            ).get("Attributes", {})
            messages = sum(
                int(attrs.get(key, 0) or 0)
                for key in (
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                    "ApproximateNumberOfMessagesDelayed",
                )
            )
            if messages and not delete_data:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"SQS queue {queue_name} still contains approximately {messages} message(s); "
                    "drain it or set delete_data=true",
                    ["queue_not_empty"],
                    retryable=False,
                )
            self._sqs.delete_queue(QueueUrl=queue_url)
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_queue: {exc}",
                errors=[str(exc)],
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"queue {queue_name} deleted",
        )

    # ---- read-only ops --------------------------------------------

    @driver_op(cloud="aws", driver="queue_sqs")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, queue_name = parse_handle(handle.handle)
        try:
            queue_url = self._queue_url(queue_name=queue_name)
            attrs = self._sqs.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=[
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                    "FifoQueue",
                ],
            ).get("Attributes", {})
        except ManagedServiceError:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"queue {queue_name} does not exist",
            )
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe SQS queue: {exc}")
        visible = int(attrs.get("ApproximateNumberOfMessages", 0) or 0)
        in_flight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0) or 0)
        mode = "FIFO" if attrs.get("FifoQueue") == "true" else "standard"
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message=f"SQS {mode} queue available; approximately {visible} visible and {in_flight} in-flight message(s)",
        )

    @driver_op(cloud="aws", driver="queue_sqs")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, queue_name = parse_handle(handle.handle)
        cfg = config or {}
        try:
            queue_url = self._queue_url(queue_name=queue_name)
            tags = self._sqs.list_queue_tags(QueueUrl=queue_url).get("Tags", {})
            refusal = live_ownership_refusal(
                tags, managed_service_id=handle.managed_service_id, resource=f"queue {queue_name}"
            )
            if refusal:
                raise ManagedServiceError(refusal)
            response = self._sqs.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=["QueueArn", "KmsMasterKeyId"],
            )
            queue_arn = response["Attributes"]["QueueArn"]
            assert_resource_arn(
                queue_arn,
                service="sqs",
                region=self._config.region,
                account=self._config.account_id,
                resource=queue_name,
            )
            kms_key = str(response["Attributes"].get("KmsMasterKeyId") or "")
        except Exception as exc:
            raise ManagedServiceError(
                f"cannot bind SQS queue {queue_name}: {exc}",
            ) from exc

        access_mode = str(cfg.get("access_mode") or "both")
        actions = ["sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
        if access_mode in {"send", "both", "manage"}:
            actions.extend(["sqs:SendMessage", "sqs:SendMessageBatch"])
        if access_mode in {"consume", "both", "manage"}:
            actions.extend(
                [
                    "sqs:ChangeMessageVisibility",
                    "sqs:ChangeMessageVisibilityBatch",
                    "sqs:DeleteMessage",
                    "sqs:DeleteMessageBatch",
                    "sqs:ReceiveMessage",
                ],
            )
        if access_mode == "manage":
            # Grant has no IAM Condition/Deny representation. Tag writes can
            # replace our live owner; unrestricted SetQueueAttributes also
            # permits a resource-policy edit that can grant tag authority.
            # Workload manage retains message operations and queue purging.
            actions.append("sqs:PurgeQueue")
        grants = [Grant(resource=queue_arn, actions=sorted(set(actions)))]
        if kms_key and not kms_key.startswith("alias/aws/"):
            grants.append(
                Grant(
                    resource=kms_key,
                    actions=["kms:Decrypt", "kms:GenerateDataKey"],
                ),
            )

        return Binding(
            env_vars={
                "QUEUE_URL": ValueRef(literal=queue_url),
                "QUEUE_ARN_OR_ID": ValueRef(literal=queue_arn),
                "QUEUE_NAME": ValueRef(literal=queue_name),
                "QUEUE_REGION": ValueRef(literal=self._config.region),
                "SQS_QUEUE_NAME": ValueRef(literal=queue_name),
                "SQS_QUEUE_URL": ValueRef(literal=queue_url),
                "SQS_QUEUE_ARN": ValueRef(literal=queue_arn),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=grants,
            notes=f"SQS {access_mode} access"
            + (
                "; manage adds queue purging; tag and permission-policy administration are platform-only"
                if access_mode == "manage"
                else ""
            ),
        )

    @driver_op(cloud="aws", driver="queue_sqs")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """SQS doesn't support snapshots — in-flight messages are
        ephemeral by definition. Per platform-side #87/#127 policy:
        queues never snapshot."""
        raise ManagedServiceError(
            "SQS does not support snapshots — in-flight messages have no recovery value (per platform-side #87/#127)",
        )

    @driver_op(cloud="aws", driver="queue_sqs")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        raise ManagedServiceError(
            "SQS does not support snapshots, hence no restore",
        )

    @driver_op(cloud="aws", driver="queue_sqs", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "access_mode": {
                    "type": "string",
                    "enum": ["send", "consume", "both", "manage"],
                    "description": (
                        "Manage adds queue purging to message operations; "
                        "tags and permission policy stay platform-only."
                    ),
                },
                "fifo": {"type": "boolean", "description": "Provision a FIFO queue."},
                "content_based_deduplication": {"type": "boolean"},
                "deduplication_scope": {"type": "string", "enum": ["queue", "messageGroup"]},
                "fifo_throughput_limit": {"type": "string", "enum": ["perQueue", "perMessageGroupId"]},
                "delay_seconds": {"type": "integer", "minimum": 0, "maximum": 900},
                "visibility_timeout_seconds": {"type": "integer", "minimum": 0, "maximum": 43200},
                "message_retention_seconds": {"type": "integer", "minimum": 60, "maximum": 14 * 86400},
                "maximum_message_size": {"type": "integer", "minimum": 1024, "maximum": 1048576},
                "receive_message_wait_time_seconds": {"type": "integer", "minimum": 0, "maximum": 20},
                "kms_master_key_id": {"type": "string"},
                "kms_data_key_reuse_period_seconds": {"type": "integer", "minimum": 60, "maximum": 86400},
                "sqs_managed_sse_enabled": {"type": "boolean"},
                "dead_letter_queue_arn": {"type": "string"},
                "max_receive_count": {"type": "integer", "minimum": 1},
                "redrive_policy": {"type": ["object", "string"]},
                "redrive_allow_policy": {"type": ["object", "string"]},
                "policy": {"type": ["object", "string"]},
                "deletion_protection": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="aws", driver="queue_sqs", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "QUEUE_URL": "Portable queue URL",
                "QUEUE_ARN_OR_ID": "Portable queue ARN or provider identifier",
                "QUEUE_NAME": "Portable queue name",
                "QUEUE_REGION": "Portable queue region",
                "SQS_QUEUE_NAME": "Queue name",
                "SQS_QUEUE_URL": "SQS HTTPS URL for SDK calls",
                "SQS_QUEUE_ARN": "Full queue ARN",
                "AWS_REGION": "Queue's region",
            }
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "content_based_deduplication",
            "dead_letter_queue_arn",
            "deduplication_scope",
            "delay_seconds",
            "deletion_protection",
            "fifo_throughput_limit",
            "kms_data_key_reuse_period_seconds",
            "kms_master_key_id",
            "max_receive_count",
            "maximum_message_size",
            "message_retention_seconds",
            "policy",
            "receive_message_wait_time_seconds",
            "redrive_allow_policy",
            "redrive_policy",
            "sqs_managed_sse_enabled",
            "visibility_timeout_seconds",
        ]

    # ---- internals ------------------------------------------------

    def _attributes(
        self,
        cfg: dict[str, Any],
        *,
        size: str,
        fifo: bool,
        creating: bool,
    ) -> dict[str, str]:
        attributes: dict[str, str] = {}
        size_settings = SIZE_TO_SETTINGS.get(size, {})
        if creating or size:
            attributes["VisibilityTimeout"] = str(
                cfg.get(
                    "visibility_timeout_seconds",
                    size_settings.get(
                        "visibility_timeout",
                        self._config.default_visibility_timeout_seconds,
                    ),
                ),
            )
            retention_days = size_settings.get(
                "retention_days",
                self._config.default_message_retention_seconds // 86400,
            )
            attributes["MessageRetentionPeriod"] = str(
                cfg.get("message_retention_seconds", retention_days * 86400),
            )
        for key, attribute in _CONFIG_TO_ATTRIBUTE.items():
            if key not in cfg:
                continue
            value = cfg[key]
            if key in {"policy", "redrive_policy", "redrive_allow_policy"}:
                attributes[attribute] = "" if value in (None, "", {}) else _json_document(value, field=key)
            elif key in {"content_based_deduplication", "sqs_managed_sse_enabled"}:
                attributes[attribute] = _bool_string(bool(value))
            else:
                attributes[attribute] = str(value)
        if "dead_letter_queue_arn" in cfg:
            attributes["RedrivePolicy"] = json.dumps(
                {
                    "deadLetterTargetArn": str(cfg["dead_letter_queue_arn"]),
                    "maxReceiveCount": str(cfg.get("max_receive_count", 5)),
                },
                separators=(",", ":"),
            )
        if creating:
            attributes = {key: value for key, value in attributes.items() if value != ""}
            if fifo:
                attributes["FifoQueue"] = "true"
                attributes.setdefault("ContentBasedDeduplication", "true")
            kms_key = str(cfg.get("kms_master_key_id") or self._config.kms_key_id or "")
            if kms_key:
                attributes.setdefault("KmsMasterKeyId", kms_key)
            elif "sqs_managed_sse_enabled" not in cfg:
                attributes["SqsManagedSseEnabled"] = _bool_string(
                    self._config.sqs_managed_sse_default,
                )
        return attributes

    def _validate_config(
        self,
        cfg: dict[str, Any],
        *,
        updating: bool = False,
        fifo: bool | None = None,
    ) -> str:
        if updating and "fifo" in cfg:
            return "fifo is immutable; reprovision the queue to change its type"
        fifo = bool(cfg.get("fifo", self._config.fifo_default)) if fifo is None else fifo
        fifo_only = {"content_based_deduplication", "deduplication_scope", "fifo_throughput_limit"}
        if not fifo and fifo_only.intersection(cfg):
            return "content deduplication and FIFO throughput controls require fifo=true"
        if cfg.get("deduplication_scope") not in (None, "queue", "messageGroup"):
            return "deduplication_scope must be queue or messageGroup"
        if cfg.get("fifo_throughput_limit") not in (None, "perQueue", "perMessageGroupId"):
            return "fifo_throughput_limit must be perQueue or perMessageGroupId"
        if cfg.get("fifo_throughput_limit") == "perMessageGroupId" and cfg.get("deduplication_scope") != "messageGroup":
            return "perMessageGroupId throughput requires deduplication_scope=messageGroup"
        if "dead_letter_queue_arn" in cfg and "redrive_policy" in cfg:
            return "set either dead_letter_queue_arn or redrive_policy, not both"
        if "max_receive_count" in cfg and "dead_letter_queue_arn" not in cfg:
            return "max_receive_count requires dead_letter_queue_arn"
        dlq = str(cfg.get("dead_letter_queue_arn") or "")
        if dlq and dlq.endswith(".fifo") != fifo:
            return "source queue and dead-letter queue must both be FIFO or both be standard"
        if "max_receive_count" in cfg:
            value = cfg["max_receive_count"]
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                return "max_receive_count must be a positive integer"
        for key, minimum, maximum in (
            ("delay_seconds", 0, 900),
            ("visibility_timeout_seconds", 0, 43200),
            ("message_retention_seconds", 60, 14 * 86400),
            ("maximum_message_size", 1024, 1048576),
            ("receive_message_wait_time_seconds", 0, 20),
            ("kms_data_key_reuse_period_seconds", 60, 86400),
        ):
            if key in cfg:
                value = cfg[key]
                if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                    return f"{key} must be an integer from {minimum} through {maximum}"
        if cfg.get("access_mode", "both") not in {"send", "consume", "both", "manage"}:
            return "access_mode must be send, consume, both, or manage"
        if cfg.get("kms_master_key_id") and cfg.get("sqs_managed_sse_enabled") is True:
            return "kms_master_key_id and sqs_managed_sse_enabled=true are mutually exclusive"
        for key in (
            "content_based_deduplication",
            "deletion_protection",
            "fifo",
            "sqs_managed_sse_enabled",
        ):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        for key in ("policy", "redrive_policy", "redrive_allow_policy"):
            if key in cfg and cfg[key] not in (None, "", {}):
                try:
                    _json_document(cfg[key], field=key)
                except (TypeError, ValueError) as exc:
                    return str(exc)
        return ""

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
        clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in raw)
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
        except Exception as exc:
            raise map_client_error(exc) from exc
        return response["QueueUrl"]


def _json_document(value: Any, *, field: str) -> str:
    if isinstance(value, str):
        parsed = json.loads(value)
    elif isinstance(value, dict):
        parsed = value
    else:
        raise TypeError(f"{field} must be a JSON object or JSON object string")
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def _bool_string(value: bool) -> str:
    return "true" if value else "false"


def _queue_exists_error(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return code in {"QueueNameExists", "QueueAlreadyExists"} or "already exists" in str(exc).lower()
