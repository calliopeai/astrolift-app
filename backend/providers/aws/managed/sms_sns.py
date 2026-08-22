"""Amazon SNS project SMS capability and dedicated topic lifecycle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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

KIND = "sms"
_OWNERSHIP_TAG = "astrolift.io/sms-binding"
_TOPIC_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_E164_RE = re.compile(r"^\+[1-9][0-9]{1,14}$")
_SENDER_ID_RE = re.compile(r"^(?=.{1,11}$)(?=.*[A-Za-z])[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
_ALLOWED_CONFIG = {
    "data_protection_policy",
    "deletion_protection",
    "delivery_mode",
    "display_name",
    "entity_id",
    "kms_master_key_id",
    "max_price_usd",
    "origination_number",
    "phone_numbers",
    "prune_phone_numbers",
    "sender_id",
    "sms_type",
    "template_id",
    "topic_name",
    "topic_policy",
    "tracing_config",
}


@dataclass(frozen=True)
class SNSSmsConfig(CredentialedConfig):
    region: str
    account_id: str
    topic_name_prefix: str = "astrolift-sms"
    kms_key_id: str = ""
    sender_id_default: str = ""
    sms_type_default: str = "Transactional"
    deletion_protection_default: bool = True
    tracing_config_default: str = "PassThrough"


class SNSSmsDriver(ManagedServiceDriver):
    """Own a dedicated SNS topic and bind a workload to SNS SMS publishing."""

    def __init__(self, *, config: SNSSmsConfig, client: Any | None = None) -> None:
        self._config = config
        if client is None:
            client = aws_client("sns", region=config.region, credential=config.credential)
        self._sns = client

    @driver_op(
        cloud="aws",
        driver="sms_sns",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config if spec.config is not None else {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_sns_sms_config"])
        topic_name = str(cfg.get("topic_name") or self._topic_name(spec))
        expected_arn = self._topic_arn(topic_name)
        try:
            existing = self._topic(expected_arn)
            if existing is not None:
                self._assert_owned(expected_arn, topic_name)
                topic_arn = expected_arn
                self._sns.tag_resource(ResourceArn=topic_arn, Tags=self._tags(spec, topic_name))
            else:
                response = self._sns.create_topic(
                    Name=topic_name,
                    Attributes=self._topic_attributes(cfg, creating=True),
                    Tags=self._tags(spec, topic_name),
                )
                topic_arn = str(response.get("TopicArn") or "")
                if topic_arn != expected_arn:
                    raise ManagedServiceError(
                        f"SNS returned unexpected topic ARN {topic_arn!r}; expected {expected_arn!r}",
                    )
                self._assert_owned(topic_arn, topic_name)
            self._reconcile(topic_arn, cfg)
            sandbox = self._sandbox_status()
        except Exception as exc:
            return ProvisionResult(False, "", f"provision SNS SMS binding: {exc}", [str(exc)])
        suffix = " (SMS sandbox: verified recipients only)" if sandbox is True else ""
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=topic_arn),
            f"SNS SMS topic {topic_name} available{suffix}",
            ready=True,
        )

    @driver_op(cloud="aws", driver="sms_sns")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        cfg = spec.config if spec.config is not None else {}
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_sns_sms_config"])
        try:
            topic_arn = self._sms_topic_arn(spec.handle)
            topic_name = topic_arn.rsplit(":", 1)[-1]
            if cfg.get("topic_name") not in (None, topic_name):
                return UpdateResult(
                    False,
                    spec.handle,
                    "SNS SMS topic_name is immutable; reprovision the binding to rename it",
                    ["immutable_topic_name"],
                )
            if self._topic(topic_arn) is None:
                return UpdateResult(False, spec.handle, "SNS SMS topic does not exist", ["not_found"])
            self._assert_owned(topic_arn, topic_name)
            self._reconcile(topic_arn, cfg)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, "SNS SMS topic does not exist", ["not_found"])
            return UpdateResult(False, spec.handle, f"update SNS SMS binding: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"SNS SMS topic {topic_name} reconciled")

    @driver_op(
        cloud="aws",
        driver="sms_sns",
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
        protected = bool(
            spec.config.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "SNS SMS binding has deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            topic_arn = self._sms_topic_arn(spec.handle)
            topic_name = topic_arn.rsplit(":", 1)[-1]
            if self._topic(topic_arn) is None:
                return DeprovisionResult(True, spec.handle, f"SNS SMS topic {topic_name} already gone")
            self._assert_owned(topic_arn, topic_name)
            self._sns.delete_topic(TopicArn=topic_arn)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, "SNS SMS topic already gone")
            return DeprovisionResult(False, spec.handle, f"delete SNS SMS binding: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"SNS SMS topic {topic_name} deleted")

    @driver_op(cloud="aws", driver="sms_sns")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            topic_arn = self._sms_topic_arn(handle.handle)
            topic_name = topic_arn.rsplit(":", 1)[-1]
            if self._topic(topic_arn) is None:
                return ServiceStatus(handle.handle, "deprovisioned", f"SNS SMS topic {topic_name} is absent")
            self._assert_owned(topic_arn, topic_name)
            subscriptions = self._subscriptions(topic_arn)
            sandbox = self._sandbox_status()
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", "SNS SMS topic is absent")
            return ServiceStatus(handle.handle, "error", f"describe SNS SMS binding: {exc}")
        sandbox_message = (
            "sandboxed; only verified recipients are reachable"
            if sandbox is True
            else "production SMS access"
            if sandbox is False
            else "sandbox state unavailable"
        )
        return ServiceStatus(
            handle.handle,
            "available",
            f"SNS SMS topic has {sum(item.get('Protocol') == 'sms' for item in subscriptions)} "
            f"phone subscription(s); {sandbox_message}",
        )

    @driver_op(cloud="aws", driver="sms_sns")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        topic_arn = self._sms_topic_arn(handle.handle)
        topic_name = topic_arn.rsplit(":", 1)[-1]
        attrs = self._topic(topic_arn)
        if attrs is None:
            raise ManagedServiceError(f"SNS SMS topic {topic_name!r} does not exist")
        self._assert_owned(topic_arn, topic_name)
        cfg = config or {}
        mode = str(cfg.get("delivery_mode") or "topic")
        sender_id = str(cfg["sender_id"]) if "sender_id" in cfg else self._config.sender_id_default
        origination_number = str(cfg.get("origination_number") or "")
        sms_type = str(cfg.get("sms_type") or self._config.sms_type_default)
        publish_resource = "*" if mode in {"direct", "both"} else topic_arn
        grants = [Grant(resource=publish_resource, actions=["sns:Publish"])]
        kms_key = str(attrs.get("KmsMasterKeyId") or "")
        if mode in {"topic", "both"} and kms_key and not kms_key.startswith("alias/aws/"):
            grants.append(Grant(resource=kms_key, actions=["kms:Decrypt", "kms:GenerateDataKey"]))
        sandbox = self._sandbox_status()
        return Binding(
            env_vars={
                "SMS_PROVIDER": ValueRef(literal="aws_sns"),
                "SMS_REGION": ValueRef(literal=self._config.region),
                "SMS_FROM": ValueRef(literal=origination_number or sender_id),
                "SMS_SENDER_ID": ValueRef(literal=sender_id),
                "SMS_ORIGINATION_NUMBER": ValueRef(literal=origination_number),
                "SMS_TYPE": ValueRef(literal=sms_type),
                "SMS_MAX_PRICE_USD": ValueRef(literal=str(cfg.get("max_price_usd") or "")),
                "SMS_ENTITY_ID": ValueRef(literal=str(cfg.get("entity_id") or "")),
                "SMS_TEMPLATE_ID": ValueRef(literal=str(cfg.get("template_id") or "")),
                "SMS_DELIVERY_MODE": ValueRef(literal=mode),
                "SMS_TOPIC_ARN": ValueRef(literal=topic_arn),
                "SMS_SANDBOX": ValueRef(literal="unknown" if sandbox is None else str(sandbox).lower()),
                "SNS_TOPIC_ARN": ValueRef(literal=topic_arn),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=grants,
            notes=(
                f"SNS SMS {mode} publishing. "
                + (
                    "Direct-recipient publishing requires sns:Publish on all resources. "
                    if mode in {"direct", "both"}
                    else "Topic fan-out remains scoped to the dedicated project topic. "
                )
                + ("Account is in the SMS sandbox." if sandbox is True else "")
            ).strip(),
        )

    @driver_op(cloud="aws", driver="sms_sns")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError("SNS SMS topics do not expose portable snapshots")

    @driver_op(cloud="aws", driver="sms_sns")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "SNS SMS bindings are reconstructed from configuration and cannot be restored",
            ["not_implemented"],
        )

    @driver_op(cloud="aws", driver="sms_sns", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "description": (
                "Project-scoped SNS SMS topic and workload publishing defaults. "
                "Account-wide spend, delivery logging, and default-SMS preferences remain operator-owned."
            ),
            "properties": {
                "topic_name": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9_-]{1,256}$",
                    "description": "Optional immutable SNS topic name; Astrolift derives one when omitted.",
                },
                "delivery_mode": {
                    "type": "string",
                    "enum": ["topic", "direct", "both"],
                    "default": "topic",
                    "description": (
                        "Topic fan-out, direct E.164 recipients, or both. Direct modes require wildcard "
                        "sns:Publish because AWS cannot resource-scope a PhoneNumber publish."
                    ),
                },
                "phone_numbers": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^\\+[1-9][0-9]{1,14}$"},
                    "uniqueItems": True,
                    "description": "Declarative E.164 subscribers for topic fan-out.",
                },
                "prune_phone_numbers": {
                    "type": "boolean",
                    "default": True,
                    "description": "Remove SMS subscriptions that are no longer declared.",
                },
                "sender_id": {
                    "type": "string",
                    "maxLength": 11,
                    "description": "Registered/default alphanumeric sender ID where the destination supports it.",
                },
                "origination_number": {
                    "type": "string",
                    "pattern": "^\\+[1-9][0-9]{1,14}$",
                    "description": "Registered E.164 long code, 10DLC, toll-free number, or short code.",
                },
                "sms_type": {
                    "type": "string",
                    "enum": ["Transactional", "Promotional"],
                    "default": "Transactional",
                    "description": "Per-message cost/reliability class.",
                },
                "max_price_usd": {
                    "type": ["number", "string"],
                    "description": "Maximum USD price for each SMS delivery.",
                },
                "entity_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 50,
                    "description": "India TRAI principal entity ID; requires template_id.",
                },
                "template_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 50,
                    "description": "India TRAI template ID; requires entity_id.",
                },
                "display_name": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "SNS topic display prefix prepended to topic-delivered SMS content.",
                },
                "kms_master_key_id": {
                    "type": "string",
                    "description": "Symmetric KMS key ID, alias, or ARN for topic server-side encryption.",
                },
                "topic_policy": {
                    "type": ["object", "string", "null"],
                    "description": "Native SNS topic resource policy; null restores the owner-only default.",
                },
                "data_protection_policy": {
                    "type": ["object", "string", "null"],
                    "description": "SNS data-protection policy; null removes the policy.",
                },
                "tracing_config": {
                    "type": "string",
                    "enum": ["PassThrough", "Active"],
                    "description": "Enable active X-Ray tracing or pass through upstream trace context.",
                },
                "deletion_protection": {
                    "type": "boolean",
                    "default": True,
                    "description": "Require an explicit force-destroy operation to delete the topic.",
                },
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="aws", driver="sms_sns", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "SMS_PROVIDER": "Portable SMS provider identifier",
                "SMS_REGION": "Portable SMS provider region",
                "SMS_FROM": "Portable sender ID or origination number",
                "SMS_SENDER_ID": "Default AWS SNS SMS sender ID",
                "SMS_ORIGINATION_NUMBER": "Default E.164 origination identity",
                "SMS_TYPE": "Transactional or Promotional message default",
                "SMS_MAX_PRICE_USD": "Maximum per-message delivery price in USD",
                "SMS_ENTITY_ID": "India TRAI entity ID",
                "SMS_TEMPLATE_ID": "India TRAI template ID",
                "SMS_DELIVERY_MODE": "topic, direct, or both",
                "SMS_TOPIC_ARN": "Dedicated project SNS topic ARN",
                "SMS_SANDBOX": "Whether the AWS account is SMS-sandboxed",
                "SNS_TOPIC_ARN": "AWS-native SNS topic ARN",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return sorted(_ALLOWED_CONFIG)

    def _reconcile(self, topic_arn: str, cfg: dict[str, Any]) -> None:
        for name, value in self._topic_attributes(cfg, creating=False).items():
            self._sns.set_topic_attributes(
                TopicArn=topic_arn,
                AttributeName=name,
                AttributeValue=value,
            )
        if "topic_policy" in cfg:
            policy = cfg.get("topic_policy")
            self._sns.set_topic_attributes(
                TopicArn=topic_arn,
                AttributeName="Policy",
                AttributeValue=(
                    _default_topic_policy(topic_arn, self._config.account_id)
                    if policy in (None, "", {})
                    else _json_document(policy, field="topic_policy")
                ),
            )
        if "data_protection_policy" in cfg:
            policy = cfg.get("data_protection_policy")
            self._sns.put_data_protection_policy(
                ResourceArn=topic_arn,
                DataProtectionPolicy=(
                    "" if policy in (None, "", {}) else _json_document(policy, field="data_protection_policy")
                ),
            )
        self._reconcile_phone_numbers(topic_arn, cfg)

    def _reconcile_phone_numbers(self, topic_arn: str, cfg: dict[str, Any]) -> None:
        desired = set(cfg.get("phone_numbers") or [])
        existing = {
            str(item.get("Endpoint") or ""): item
            for item in self._subscriptions(topic_arn)
            if item.get("Protocol") == "sms"
        }
        for phone_number in sorted(desired - existing.keys()):
            self._sns.subscribe(
                TopicArn=topic_arn,
                Protocol="sms",
                Endpoint=phone_number,
                ReturnSubscriptionArn=True,
            )
        if cfg.get("prune_phone_numbers", True):
            for phone_number in sorted(existing.keys() - desired):
                subscription_arn = str(existing[phone_number].get("SubscriptionArn") or "")
                if subscription_arn and not _pending_subscription(subscription_arn):
                    self._sns.unsubscribe(SubscriptionArn=subscription_arn)

    def _subscriptions(self, topic_arn: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"TopicArn": topic_arn}
            if token:
                request["NextToken"] = token
            response = self._sns.list_subscriptions_by_topic(**request)
            rows.extend(dict(item) for item in response.get("Subscriptions") or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return rows

    def _topic_attributes(self, cfg: dict[str, Any], *, creating: bool) -> dict[str, str]:
        attrs = {
            "TracingConfig": str(cfg.get("tracing_config") or self._config.tracing_config_default),
        }
        kms_key = str(cfg["kms_master_key_id"]) if "kms_master_key_id" in cfg else self._config.kms_key_id
        if kms_key or not creating:
            attrs["KmsMasterKeyId"] = kms_key
        if "display_name" in cfg:
            attrs["DisplayName"] = str(cfg["display_name"])
        elif not creating:
            attrs["DisplayName"] = ""
        if "topic_policy" in cfg and cfg.get("topic_policy") not in (None, "", {}):
            attrs["Policy"] = _json_document(cfg["topic_policy"], field="topic_policy")
        return attrs

    def _topic(self, topic_arn: str) -> dict[str, str] | None:
        try:
            return dict(self._sns.get_topic_attributes(TopicArn=topic_arn).get("Attributes") or {})
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _assert_owned(self, topic_arn: str, topic_name: str) -> None:
        tags = {
            str(item.get("Key") or ""): str(item.get("Value") or "")
            for item in self._sns.list_tags_for_resource(ResourceArn=topic_arn).get("Tags") or []
        }
        if tags.get(_OWNERSHIP_TAG) != topic_name:
            raise ManagedServiceError(f"refusing to adopt foreign SNS SMS topic {topic_name!r}")

    def _sandbox_status(self) -> bool | None:
        operation = getattr(self._sns, "get_sms_sandbox_account_status", None)
        if operation is None:
            return None
        try:
            response = operation()
            return bool(response["IsInSandbox"]) if "IsInSandbox" in response else None
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = str((response.get("Error") or {}).get("Code") or "")
            if code in {"AuthorizationError", "AuthorizationErrorException", "InvalidAction"}:
                return None
            raise

    def _tags(self, spec: ProvisionSpec, topic_name: str) -> list[dict[str, str]]:
        return [*tags_for(spec), {"Key": _OWNERSHIP_TAG, "Value": topic_name}]

    def _topic_name(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            part
            for part in (
                self._config.topic_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "sms",
            )
            if part
        )
        value = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")
        return (value or "astrolift-sms")[:256]

    def _topic_arn(self, topic_name: str) -> str:
        partition = (
            "aws-cn"
            if self._config.region.startswith("cn-")
            else "aws-us-gov"
            if self._config.region.startswith("us-gov-")
            else "aws"
        )
        return f"arn:{partition}:sns:{self._config.region}:{self._config.account_id}:{topic_name}"

    @staticmethod
    def _sms_topic_arn(handle: str) -> str:
        kind, topic_arn = parse_handle(handle)
        if kind != KIND:
            raise ManagedServiceError(f"expected an sms handle, got {kind!r}")
        return topic_arn

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        if not isinstance(cfg, dict):
            return "SNS SMS config must be an object"
        if not re.fullmatch(r"[0-9]{12}", self._config.account_id):
            return "operator SNS SMS account_id must contain exactly 12 digits"
        if not self._config.region:
            return "operator SNS SMS region must be configured"
        unknown = sorted(set(cfg) - _ALLOWED_CONFIG)
        if unknown:
            return "unsupported SNS SMS config fields: " + ", ".join(unknown)
        if "topic_name" in cfg and (
            not isinstance(cfg["topic_name"], str) or not _TOPIC_NAME_RE.fullmatch(cfg["topic_name"])
        ):
            return "topic_name must contain 1-256 letters, digits, hyphens, or underscores"
        if cfg.get("delivery_mode", "topic") not in {"topic", "direct", "both"}:
            return "delivery_mode must be topic, direct, or both"
        phone_numbers = cfg.get("phone_numbers", [])
        if not isinstance(phone_numbers, list):
            return "phone_numbers must be an array of E.164 strings"
        if any(not isinstance(value, str) or not _E164_RE.fullmatch(value) for value in phone_numbers):
            return "phone_numbers must contain only E.164 phone numbers"
        if len(phone_numbers) != len(set(phone_numbers)):
            return "phone_numbers must be unique"
        if "prune_phone_numbers" in cfg and not isinstance(cfg["prune_phone_numbers"], bool):
            return "prune_phone_numbers must be a boolean"
        sender_id = cfg.get("sender_id", self._config.sender_id_default)
        if sender_id and (not isinstance(sender_id, str) or not _SENDER_ID_RE.fullmatch(sender_id)):
            return "sender_id must be 1-11 letters, digits, or internal hyphens and contain a letter"
        origination = cfg.get("origination_number")
        if origination and (not isinstance(origination, str) or not _E164_RE.fullmatch(origination)):
            return "origination_number must be an E.164 phone number"
        sms_type = cfg.get("sms_type", self._config.sms_type_default)
        if sms_type not in {"Transactional", "Promotional"}:
            return "sms_type must be Transactional or Promotional"
        if "max_price_usd" in cfg:
            try:
                price = Decimal(str(cfg["max_price_usd"]))
            except (InvalidOperation, ValueError):
                return "max_price_usd must be a positive decimal"
            if not price.is_finite() or price <= 0:
                return "max_price_usd must be a positive decimal"
        for field in ("entity_id", "template_id"):
            if field in cfg and (not isinstance(cfg[field], str) or not 1 <= len(cfg[field]) <= 50):
                return f"{field} must contain 1-50 characters"
        if bool(cfg.get("entity_id")) != bool(cfg.get("template_id")):
            return "entity_id and template_id must be supplied together"
        if "display_name" in cfg and (not isinstance(cfg["display_name"], str) or len(cfg["display_name"]) > 100):
            return "display_name must be a string of at most 100 characters"
        if "kms_master_key_id" in cfg and not isinstance(cfg["kms_master_key_id"], str):
            return "kms_master_key_id must be a string"
        if cfg.get("tracing_config", self._config.tracing_config_default) not in {"PassThrough", "Active"}:
            return "tracing_config must be PassThrough or Active"
        if "deletion_protection" in cfg and not isinstance(cfg["deletion_protection"], bool):
            return "deletion_protection must be a boolean"
        for field in ("topic_policy", "data_protection_policy"):
            if field in cfg and cfg[field] not in (None, "", {}):
                try:
                    _json_document(cfg[field], field=field)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    return str(exc)
        return ""


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


def _default_topic_policy(topic_arn: str, account_id: str) -> str:
    return json.dumps(
        {
            "Version": "2008-10-17",
            "Id": "__default_policy_ID",
            "Statement": [
                {
                    "Sid": "__default_statement_ID",
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": [
                        "SNS:GetTopicAttributes",
                        "SNS:SetTopicAttributes",
                        "SNS:AddPermission",
                        "SNS:RemovePermission",
                        "SNS:DeleteTopic",
                        "SNS:Subscribe",
                        "SNS:ListSubscriptionsByTopic",
                        "SNS:Publish",
                    ],
                    "Resource": topic_arn,
                    "Condition": {"StringEquals": {"AWS:SourceOwner": account_id}},
                },
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _pending_subscription(subscription_arn: str) -> bool:
    normalized = "".join(character for character in subscription_arn.lower() if character.isalnum())
    return normalized in {"pendingconfirmation", "deleted"}


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    return code in {"NotFound", "NotFoundException"} or "not found" in str(exc).lower()
