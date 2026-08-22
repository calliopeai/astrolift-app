"""Amazon SNS standard and FIFO topic managed-service drivers."""

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
from aws.managed._base import ManagedServiceError, handle_for, parse_handle, tags_for
from aws.session import aws_client

KIND = "topic"
_PROTOCOLS = {
    "application",
    "email",
    "email-json",
    "firehose",
    "http",
    "https",
    "lambda",
    "sms",
    "sqs",
}
_TOPIC_ATTRIBUTE_FIELDS = {
    "archive_retention_days": "ArchivePolicy",
    "content_based_deduplication": "ContentBasedDeduplication",
    "delivery_policy": "DeliveryPolicy",
    "display_name": "DisplayName",
    "fifo_throughput_scope": "FifoThroughputScope",
    "kms_master_key_id": "KmsMasterKeyId",
    "policy": "Policy",
    "signature_version": "SignatureVersion",
    "tracing_config": "TracingConfig",
}


@dataclass(frozen=True)
class SNSConfig(CredentialedConfig):
    region: str
    account_id: str
    topic_name_prefix: str = "astrolift"
    kms_key_id: str = ""
    tracing_config_default: str = "PassThrough"


class SNSTopicDriver(ManagedServiceDriver):
    FIFO = False

    def __init__(self, *, config: SNSConfig, client: Any | None = None) -> None:
        self._config = config
        if client is None:
            client = aws_client("sns", region=config.region, credential=config.credential)
        self._sns = client

    @driver_op(
        cloud="aws",
        driver="topic_sns",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_sns_config"])
        topic_name = self._topic_name(spec)
        attributes = self._topic_attributes(cfg, creating=True)
        request: dict[str, Any] = {
            "Name": topic_name,
            "Attributes": attributes,
            "Tags": tags_for(spec),
        }
        if cfg.get("data_protection_policy") not in (None, "", {}):
            request["DataProtectionPolicy"] = _json_document(
                cfg["data_protection_policy"],
                field="data_protection_policy",
            )
        try:
            topic_arn = str(self._sns.create_topic(**request)["TopicArn"])
            self._reconcile_topic(topic_arn, cfg)
            pending = self._reconcile_subscriptions(topic_arn, cfg)
        except Exception as exc:
            return ProvisionResult(False, "", f"provision SNS topic: {exc}", [str(exc)])
        message = f"SNS {'FIFO' if self.FIFO else 'standard'} topic {topic_name} available"
        if pending:
            message += f"; {pending} subscription(s) await endpoint confirmation"
        return ProvisionResult(True, handle_for(kind=KIND, resource_id=topic_arn), message, ready=True)

    @driver_op(cloud="aws", driver="topic_sns")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, topic_arn = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg, partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_sns_config"])
        try:
            current = self._topic(topic_arn)
            if (
                self.FIFO
                and current.get("FifoThroughputScope") == "MessageGroup"
                and cfg.get("fifo_throughput_scope") == "Topic"
            ):
                return UpdateResult(
                    False,
                    spec.handle,
                    "SNS FIFO high-throughput scope cannot be reverted from MessageGroup to Topic",
                    ["irreversible_fifo_throughput_scope"],
                )
            self._reconcile_topic(topic_arn, cfg)
            pending = self._reconcile_subscriptions(topic_arn, cfg)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, f"SNS topic {topic_arn} not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update SNS topic: {exc}", [str(exc)])
        message = f"SNS topic {topic_arn} reconciled"
        if pending:
            message += f"; {pending} subscription(s) await endpoint confirmation"
        return UpdateResult(True, spec.handle, message)

    @driver_op(
        cloud="aws",
        driver="topic_sns",
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
        del force_destroy
        _, topic_arn = parse_handle(spec.handle)
        try:
            attrs = self._topic(topic_arn)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"SNS topic {topic_arn} already gone")
            return _deprovision_error(spec.handle, "describe SNS topic", exc)
        archive = _json_object(attrs.get("ArchivePolicy", "{}"))
        if archive.get("MessageRetentionPeriod") and not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "SNS FIFO archive contains retained messages; set delete_data=true to clear it before deletion",
                ["retained_archive_requires_delete_data"],
                retryable=False,
            )
        try:
            if archive.get("MessageRetentionPeriod"):
                self._sns.set_topic_attributes(
                    TopicArn=topic_arn,
                    AttributeName="ArchivePolicy",
                    AttributeValue="{}",
                )
            self._sns.delete_topic(TopicArn=topic_arn)
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete SNS topic", exc)
        return DeprovisionResult(True, spec.handle, f"SNS topic {topic_arn} deleted")

    @driver_op(cloud="aws", driver="topic_sns")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, topic_arn = parse_handle(handle.handle)
        try:
            attrs = self._topic(topic_arn)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", f"SNS topic {topic_arn} does not exist")
            return ServiceStatus(handle.handle, "error", f"describe SNS topic: {exc}")
        mode = "FIFO" if attrs.get("FifoTopic") == "true" else "standard"
        encrypted = bool(attrs.get("KmsMasterKeyId"))
        return ServiceStatus(
            handle.handle,
            "available",
            f"SNS {mode} topic available ({'KMS encrypted' if encrypted else 'unencrypted'})",
        )

    @driver_op(cloud="aws", driver="topic_sns")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, topic_arn = parse_handle(handle.handle)
        attrs = self._topic(topic_arn)
        topic_name = topic_arn.rsplit(":", 1)[-1]
        cfg = config or {}
        access_mode = str(cfg.get("access_mode") or "publish")
        actions = ["sns:GetTopicAttributes", "sns:Publish"]
        if access_mode in {"subscribe", "manage"}:
            actions.extend(["sns:ListSubscriptionsByTopic", "sns:Subscribe"])
        if access_mode == "manage":
            actions.extend(
                [
                    "sns:SetSubscriptionAttributes",
                    "sns:SetTopicAttributes",
                    "sns:Unsubscribe",
                ],
            )
        grants = [Grant(resource=topic_arn, actions=sorted(set(actions)))]
        kms_key = str(attrs.get("KmsMasterKeyId") or "")
        if kms_key and not kms_key.startswith("alias/aws/"):
            grants.append(
                Grant(
                    resource=kms_key,
                    actions=["kms:Decrypt", "kms:GenerateDataKey"],
                ),
            )
        return Binding(
            env_vars={
                "TOPIC_ARN_OR_ID": ValueRef(literal=topic_arn),
                "TOPIC_NAME": ValueRef(literal=topic_name),
                "TOPIC_REGION": ValueRef(literal=self._config.region),
                "SNS_TOPIC_ARN": ValueRef(literal=topic_arn),
                "SNS_TOPIC_NAME": ValueRef(literal=topic_name),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=grants,
            notes=(
                f"SNS {access_mode} access; publishing to FIFO topics also requires message group/deduplication fields"
            ),
        )

    @driver_op(cloud="aws", driver="topic_sns")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "SNS topics do not expose portable snapshots; FIFO archives are retained "
            "in-place and replayed per subscription",
        )

    @driver_op(cloud="aws", driver="topic_sns")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise ManagedServiceError("SNS topics cannot be restored from a snapshot")

    @driver_op(cloud="aws", driver="topic_sns", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "access_mode": {"type": "string", "enum": ["publish", "subscribe", "manage"], "default": "publish"},
            "kms_master_key_id": {"type": "string"},
            "display_name": {"type": "string", "maxLength": 100},
            "policy": {"type": ["object", "string"]},
            "delivery_policy": {"type": ["object", "string"]},
            "signature_version": {"type": "string", "enum": ["1", "2"]},
            "tracing_config": {"type": "string", "enum": ["PassThrough", "Active"]},
            "data_protection_policy": {"type": ["object", "string"]},
            "subscriptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["protocol", "endpoint"],
                    "properties": {
                        "protocol": {"type": "string", "enum": sorted(_PROTOCOLS)},
                        "endpoint": {"type": "string", "minLength": 1},
                        "filter_policy": {"type": ["object", "string"]},
                        "filter_policy_scope": {"type": "string", "enum": ["MessageAttributes", "MessageBody"]},
                        "raw_message_delivery": {"type": "boolean"},
                        "dead_letter_queue_arn": {"type": "string"},
                        "redrive_policy": {"type": ["object", "string"]},
                        "replay_policy": {"type": ["object", "string"]},
                        "delivery_policy": {"type": ["object", "string"]},
                        "subscription_role_arn": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "prune_subscriptions": {"type": "boolean", "default": False},
        }
        if self.FIFO:
            properties.update(
                {
                    "content_based_deduplication": {"type": "boolean", "default": False},
                    "fifo_throughput_scope": {"type": "string", "enum": ["Topic", "MessageGroup"], "default": "Topic"},
                    "archive_retention_days": {"type": "integer", "minimum": 1, "maximum": 365},
                },
            )
        return {"type": "object", "properties": properties, "additionalProperties": False}

    @driver_op(cloud="aws", driver="topic_sns", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "TOPIC_ARN_OR_ID": "Portable topic ARN or provider identifier",
                "TOPIC_NAME": "Portable topic name",
                "TOPIC_REGION": "Portable topic region",
                "SNS_TOPIC_ARN": "Amazon SNS topic ARN",
                "SNS_TOPIC_NAME": "Amazon SNS topic name",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "archive_retention_days",
            "content_based_deduplication",
            "data_protection_policy",
            "delivery_policy",
            "display_name",
            "fifo_throughput_scope",
            "kms_master_key_id",
            "policy",
            "prune_subscriptions",
            "signature_version",
            "subscriptions",
            "tracing_config",
        ]

    def _topic_name(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            part
            for part in (
                self._config.topic_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "topic",
            )
            if part
        )
        clean = "".join(char if char.isalnum() or char in "-_" else "-" for char in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        suffix = ".fifo" if self.FIFO else ""
        return f"{clean.strip('-_')[: 256 - len(suffix)]}{suffix}"

    def _topic_attributes(self, cfg: dict[str, Any], *, creating: bool) -> dict[str, str]:
        attrs: dict[str, str] = {}
        if creating and self.FIFO:
            attrs["FifoTopic"] = "true"
        for key, attribute in _TOPIC_ATTRIBUTE_FIELDS.items():
            if key not in cfg:
                continue
            value = cfg[key]
            if key in {"content_based_deduplication"}:
                attrs[attribute] = _bool_string(bool(value))
            elif key == "archive_retention_days":
                attrs[attribute] = json.dumps({"MessageRetentionPeriod": str(value)}, separators=(",", ":"))
            elif key in {"policy", "delivery_policy"}:
                attrs[attribute] = _json_document(value, field=key)
            else:
                attrs[attribute] = str(value)
        if "kms_master_key_id" not in cfg and self._config.kms_key_id:
            attrs["KmsMasterKeyId"] = self._config.kms_key_id
        if "tracing_config" not in cfg and self._config.tracing_config_default:
            attrs["TracingConfig"] = self._config.tracing_config_default
        return attrs

    def _reconcile_topic(self, topic_arn: str, cfg: dict[str, Any]) -> None:
        for attribute, value in self._topic_attributes(cfg, creating=False).items():
            self._sns.set_topic_attributes(
                TopicArn=topic_arn,
                AttributeName=attribute,
                AttributeValue=value,
            )
        if "data_protection_policy" in cfg:
            value = cfg["data_protection_policy"]
            document = "" if value in (None, "", {}) else _json_document(value, field="data_protection_policy")
            self._sns.put_data_protection_policy(
                ResourceArn=topic_arn,
                DataProtectionPolicy=document,
            )

    def _reconcile_subscriptions(self, topic_arn: str, cfg: dict[str, Any]) -> int:
        if "subscriptions" not in cfg:
            return 0
        desired = list(cfg.get("subscriptions") or [])
        existing = self._subscriptions(topic_arn)
        by_identity = {(str(item.get("Protocol") or ""), str(item.get("Endpoint") or "")): item for item in existing}
        wanted: set[tuple[str, str]] = set()
        pending = 0
        for subscription in desired:
            protocol = str(subscription["protocol"])
            endpoint = str(subscription["endpoint"])
            identity = (protocol, endpoint)
            wanted.add(identity)
            attributes = _subscription_attributes(subscription)
            current = by_identity.get(identity)
            subscription_arn = str((current or {}).get("SubscriptionArn") or "")
            if not subscription_arn:
                response = self._sns.subscribe(
                    TopicArn=topic_arn,
                    Protocol=protocol,
                    Endpoint=endpoint,
                    Attributes=attributes,
                    ReturnSubscriptionArn=True,
                )
                subscription_arn = str(response.get("SubscriptionArn") or "")
            if not subscription_arn or _pending_subscription(subscription_arn):
                pending += 1
                continue
            for name, value in attributes.items():
                self._sns.set_subscription_attributes(
                    SubscriptionArn=subscription_arn,
                    AttributeName=name,
                    AttributeValue=value,
                )
        if cfg.get("prune_subscriptions"):
            for identity, subscription in by_identity.items():
                subscription_arn = str(subscription.get("SubscriptionArn") or "")
                if identity not in wanted and subscription_arn and not _pending_subscription(subscription_arn):
                    self._sns.unsubscribe(SubscriptionArn=subscription_arn)
        return pending

    def _subscriptions(self, topic_arn: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"TopicArn": topic_arn}
            if token:
                request["NextToken"] = token
            response = self._sns.list_subscriptions_by_topic(**request)
            rows.extend(response.get("Subscriptions") or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return rows

    def _topic(self, topic_arn: str) -> dict[str, str]:
        return dict(self._sns.get_topic_attributes(TopicArn=topic_arn).get("Attributes") or {})

    def _validate_config(self, cfg: dict[str, Any], *, partial: bool = False) -> str:
        del partial
        fifo_only = {"archive_retention_days", "content_based_deduplication", "fifo_throughput_scope"}
        if not self.FIFO and fifo_only.intersection(cfg):
            return "archive, content deduplication, and FIFO throughput controls require the sns_fifo variant"
        if "archive_retention_days" in cfg:
            value = cfg["archive_retention_days"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 365:
                return "archive_retention_days must be an integer from 1 through 365"
        if cfg.get("fifo_throughput_scope") not in (None, "Topic", "MessageGroup"):
            return "fifo_throughput_scope must be Topic or MessageGroup"
        if cfg.get("signature_version") not in (None, "1", "2", 1, 2):
            return "signature_version must be 1 or 2"
        if cfg.get("tracing_config") not in (None, "PassThrough", "Active"):
            return "tracing_config must be PassThrough or Active"
        if cfg.get("access_mode", "publish") not in {"publish", "subscribe", "manage"}:
            return "access_mode must be publish, subscribe, or manage"
        subscriptions = cfg.get("subscriptions", [])
        if not isinstance(subscriptions, list):
            return "subscriptions must be an array"
        identities: set[tuple[str, str]] = set()
        for item in subscriptions:
            if not isinstance(item, dict):
                return "each subscription must be an object"
            protocol = item.get("protocol")
            endpoint = item.get("endpoint")
            if protocol not in _PROTOCOLS or not isinstance(endpoint, str) or not endpoint:
                return "each subscription requires a supported protocol and non-empty endpoint"
            if self.FIFO and protocol != "sqs":
                return "SNS FIFO topics support only SQS subscription endpoints"
            identity = (str(protocol), endpoint)
            if identity in identities:
                return f"duplicate subscription for {protocol}:{endpoint}"
            identities.add(identity)
            if item.get("filter_policy_scope") not in (None, "MessageAttributes", "MessageBody"):
                return "filter_policy_scope must be MessageAttributes or MessageBody"
            if "raw_message_delivery" in item and not isinstance(item["raw_message_delivery"], bool):
                return "raw_message_delivery must be a boolean"
            if "dead_letter_queue_arn" in item and "redrive_policy" in item:
                return "set either dead_letter_queue_arn or redrive_policy, not both"
        for key in ("policy", "delivery_policy", "data_protection_policy"):
            if key in cfg:
                try:
                    _json_document(cfg[key], field=key)
                except (TypeError, ValueError) as exc:
                    return str(exc)
        return ""


class SNSStandardTopicDriver(SNSTopicDriver):
    FIFO = False


class SNSFifoTopicDriver(SNSTopicDriver):
    FIFO = True


def _subscription_attributes(config: dict[str, Any]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, attribute in (
        ("delivery_policy", "DeliveryPolicy"),
        ("filter_policy", "FilterPolicy"),
        ("filter_policy_scope", "FilterPolicyScope"),
        ("replay_policy", "ReplayPolicy"),
        ("subscription_role_arn", "SubscriptionRoleArn"),
    ):
        if key not in config:
            continue
        value = config[key]
        attrs[attribute] = _json_document(value, field=key) if key.endswith("policy") else str(value)
    if "raw_message_delivery" in config:
        attrs["RawMessageDelivery"] = _bool_string(bool(config["raw_message_delivery"]))
    if "dead_letter_queue_arn" in config:
        attrs["RedrivePolicy"] = json.dumps(
            {"deadLetterTargetArn": str(config["dead_letter_queue_arn"])},
            separators=(",", ":"),
        )
    elif "redrive_policy" in config:
        attrs["RedrivePolicy"] = _json_document(config["redrive_policy"], field="redrive_policy")
    return attrs


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


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _bool_string(value: bool) -> str:
    return "true" if value else "false"


def _pending_subscription(subscription_arn: str) -> bool:
    normalized = "".join(char for char in subscription_arn.lower() if char.isalnum())
    return normalized in {"pendingconfirmation", "deleted"}


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return code in {"NotFound", "NotFoundException"} or "not found" in str(exc).lower()


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
    retryable = status >= 500 or code.startswith("Throttl") or code in {"InternalError", "ConcurrentAccess"}
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [str(exc)], retryable=retryable)
