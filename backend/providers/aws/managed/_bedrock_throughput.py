"""Native paid-throughput identity; no process-local lifecycle state."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from botocore.exceptions import ClientError

from aws.managed._base import ManagedServiceError, parse_handle, tags_for

if TYPE_CHECKING:
    from _sdk.managed_service import ProvisionSpec
    from aws.managed.model_endpoint_bedrock import AmazonBedrockConfig

_PREFIX = "model_endpoint/pt2|"
_TAG = "astrolift.io/bedrock-"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PT = re.compile(r"arn:(aws(?:-[a-z0-9-]+)?):bedrock:([a-z0-9-]{1,20}):(\d{12}):provisioned-model/[a-z0-9]{12}\Z")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity(value: str) -> str:
    try:
        result = str(UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ManagedServiceError("Bedrock throughput requires an exact UUID identity") from exc
    if result != value:
        raise ManagedServiceError("Bedrock throughput requires a canonical UUID identity")
    return result


def model_arn(model: object, config: AmazonBedrockConfig) -> str:
    if not isinstance(model, str) or len(model) > 1011:
        raise ManagedServiceError("Bedrock throughput model identity is invalid")
    partition = (
        "aws-us-gov" if config.region.startswith("us-gov-") else "aws-cn" if config.region.startswith("cn-") else "aws"
    )
    if not model.startswith("arn:"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,200}(?::[a-z0-9.-]+){0,2}", model):
            raise ManagedServiceError("Bedrock throughput needs a foundation model ID or exact model ARN")
        return f"arn:{partition}:bedrock:{config.region}::foundation-model/{model}"
    parts = model.split(":", 5)
    if len(parts) != 6 or parts[:4] != ["arn", partition, "bedrock", config.region]:
        raise ManagedServiceError("Bedrock throughput model region or partition does not match")
    account = config.credential.declared_account if config.credential else ""
    resource = parts[5]
    if resource.startswith("foundation-model/"):
        if parts[4] or not re.fullmatch(r"foundation-model/[a-z0-9][a-z0-9.:-]+", resource):
            raise ManagedServiceError("Bedrock foundation model ARN is invalid")
    elif resource.startswith("custom-model/"):
        if (
            not re.fullmatch(r"\d{12}", parts[4])
            or not account
            or account != parts[4]
            or not re.fullmatch(r"custom-model/[a-zA-Z0-9./:_-]+", resource)
        ):
            raise ManagedServiceError("Bedrock custom model ARN is invalid or foreign")
    else:
        raise ManagedServiceError("Bedrock provisioned throughput needs a foundation or custom model")
    return model


@dataclass(frozen=True)
class PaidHandle:
    arn: str
    service: str
    organization: str
    units: int
    term: str
    model_hash: str
    intent: str
    legacy_tags_hash: str = ""
    legacy_name: str = ""
    log_prefix_hash: str = ""

    def encode(self) -> str:
        fields = [self.arn, self.service, self.organization, str(self.units), self.term, self.model_hash, self.intent]
        prefix = "model_endpoint/pt1|"
        if self.log_prefix_hash:
            fields[6] = base64.urlsafe_b64encode(bytes.fromhex(self.intent)).rstrip(b"=").decode("ascii")
            fields.append(self.log_prefix_hash)
            prefix = _PREFIX
        value = prefix + "|".join(fields)
        if self.legacy_tags_hash:
            value += "|" + self.legacy_tags_hash + "|" + self.legacy_name
        if len(value) > 512:
            raise ManagedServiceError("Bedrock throughput handle exceeds durable storage")
        return value

    @property
    def record_name(self) -> str:
        return self.legacy_name or "astrolift-" + self.service.replace("-", "")

    @classmethod
    def parse(cls, handle: str) -> PaidHandle | None:
        kind, resource = parse_handle(handle)
        if kind != "model_endpoint" or len(handle) > 512:
            raise ManagedServiceError("Bedrock service handle is invalid")
        if not resource.startswith(("pt1|", "pt2|")):
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", resource):
                raise ManagedServiceError("Bedrock legacy handle is invalid")
            return None
        fields = resource[4:].split("|")
        current = resource.startswith("pt2|")
        if len(fields) not in ((8, 10) if current else (7, 9)):
            raise ManagedServiceError("Bedrock throughput handle is malformed")
        arn, service, org, units, term, model_hash, intent = fields[:7]
        log_prefix_hash = fields[7] if current else ""
        if current:
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", intent) or not _HEX.fullmatch(log_prefix_hash):
                raise ManagedServiceError("Bedrock throughput fingerprints are malformed")
            decoded = base64.urlsafe_b64decode(intent + "=")
            if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != intent:
                raise ManagedServiceError("Bedrock throughput fingerprint is not canonical")
            intent = decoded.hex()
        offset = 8 if current else 7
        legacy_tags_hash = fields[offset] if len(fields) == offset + 2 else ""
        legacy_name = fields[offset + 1] if legacy_tags_hash else ""
        if legacy_tags_hash and (
            not _HEX.fullmatch(legacy_tags_hash) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", legacy_name)
        ):
            raise ManagedServiceError("Bedrock legacy ownership fingerprint is malformed")
        if (
            not _PT.fullmatch(arn)
            or not re.fullmatch(r"[1-9][0-9]{0,9}", units)
            or int(units) > 2147483647
            or term not in ("OneMonth", "SixMonths")
            or not _HEX.fullmatch(model_hash)
            or not _HEX.fullmatch(intent)
        ):
            raise ManagedServiceError("Bedrock throughput handle is malformed")
        return cls(
            arn,
            identity(service),
            identity(org),
            int(units),
            term,
            model_hash,
            intent,
            legacy_tags_hash,
            legacy_name,
            log_prefix_hash,
        )


class Throughput:
    def __init__(self, client: Any, config: AmazonBedrockConfig):
        self.client = client
        self.config = config

    def validate_arn(self, arn: object) -> None:
        match = _PT.fullmatch(arn) if isinstance(arn, str) else None
        if not match or match[2] != self.config.region:
            raise ManagedServiceError("Bedrock throughput native identity is invalid or foreign")
        partition = (
            "aws-us-gov"
            if self.config.region.startswith("us-gov-")
            else "aws-cn"
            if self.config.region.startswith("cn-")
            else "aws"
        )
        account = self.config.credential.declared_account if self.config.credential else ""
        if match[1] != partition or (account and match[3] != account):
            raise ManagedServiceError("Bedrock throughput account or partition does not match")

    def get(self, target: str) -> dict[str, Any] | None:
        try:
            result = self.client.get_provisioned_model_throughput(provisionedModelId=target)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                return None
            raise ManagedServiceError("Bedrock throughput read unavailable") from exc
        except Exception as exc:
            raise ManagedServiceError("Bedrock throughput read unavailable") from exc
        if not isinstance(result, dict):
            raise ManagedServiceError("Bedrock throughput read returned an invalid response")
        self.validate_arn(result.get("provisionedModelArn"))
        if target.startswith("arn:") and result["provisionedModelArn"] != target:
            raise ManagedServiceError("Bedrock throughput read substituted the native identity")
        if not target.startswith("arn:") and result.get("provisionedModelName") != target:
            raise ManagedServiceError("Bedrock throughput read substituted the exact name")
        return result

    def tags(self, arn: str) -> dict[str, str]:
        try:
            rows = self.client.list_tags_for_resource(resourceARN=arn).get("tags")
        except Exception as exc:
            raise ManagedServiceError("Bedrock throughput ownership read unavailable") from exc
        if not isinstance(rows, list) or len(rows) > 200:
            raise ManagedServiceError("Bedrock throughput ownership tags are invalid")
        tags = {}
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"key", "value"}
                or not isinstance(row["key"], str)
                or not isinstance(row["value"], str)
                or row["key"] in tags
            ):
                raise ManagedServiceError("Bedrock throughput ownership tags are invalid")
            tags[row["key"]] = row["value"]
        return tags

    def expected(self, spec: ProvisionSpec, model: str) -> tuple[PaidHandle, list[dict[str, str]]]:
        cfg = spec.config or {}
        units = cfg.get("model_units", 1)
        term = cfg.get("provisioned_throughput")
        if type(units) is not int or not 1 <= units <= 2147483647 or term not in ("OneMonth", "SixMonths"):
            raise ManagedServiceError("Bedrock throughput units or commitment is invalid")
        service, org = identity(spec.managed_service_id), identity(spec.organization_id)
        source = model_arn(model, self.config)
        source_hash = digest(source)
        intent = digest(
            [
                service,
                org,
                spec.app_id,
                spec.environment_id,
                spec.tenant_cluster_id,
                self.config.region,
                source,
                units,
                term,
            ]
        )
        prefix_hash = digest(self.config.invocation_log_group_prefix)
        handle = PaidHandle("", service, org, units, term, source_hash, intent, log_prefix_hash=prefix_hash)
        tags = {row["Key"]: row["Value"] for row in tags_for(spec)}
        tags.update(
            {
                _TAG + "organization-id": org,
                _TAG + "units": str(units),
                _TAG + "term": term,
                _TAG + "model": source_hash,
                _TAG + "intent": intent,
                _TAG + "log-prefix": prefix_hash,
            }
        )
        if len(tags) > 200 or any(
            not isinstance(k, str) or not isinstance(v, str) or not 1 <= len(k) <= 256 or len(v) > 256
            for k, v in tags.items()
        ):
            raise ManagedServiceError("Bedrock throughput request tags exceed AWS bounds")
        return handle, [{"key": key, "value": value} for key, value in tags.items()]

    def verify(self, row: dict[str, Any], handle: PaidHandle, *, service: str, organization: str = "") -> None:
        self.validate_arn(handle.arn)
        if handle.log_prefix_hash and handle.log_prefix_hash != digest(self.config.invocation_log_group_prefix):
            raise ManagedServiceError("Bedrock recorded log prefix differs from current driver configuration")
        if identity(service) != handle.service or identity(organization) != handle.organization:
            raise ManagedServiceError("Bedrock throughput caller ownership does not match")
        tags = self.tags(handle.arn)
        expected = {"astrolift.io/managed-by": "platform", "astrolift.io/managed_service_id": handle.service}
        if handle.legacy_tags_hash:
            if digest(tags) != handle.legacy_tags_hash:
                raise ManagedServiceError("Legacy Bedrock ownership fingerprint changed")
        else:
            expected.update(
                {
                    _TAG + "organization-id": handle.organization,
                    _TAG + "units": str(handle.units),
                    _TAG + "term": handle.term,
                    _TAG + "model": handle.model_hash,
                    _TAG + "intent": handle.intent,
                }
            )
        if not handle.legacy_tags_hash and handle.log_prefix_hash:
            expected[_TAG + "log-prefix"] = handle.log_prefix_hash
        if any(tags.get(key) != value for key, value in expected.items()):
            raise ManagedServiceError(
                "Bedrock throughput live ownership or recorded intent does not match; explicit recovery required"
            )
        if (
            row.get("provisionedModelArn") != handle.arn
            or row.get("commitmentDuration") != handle.term
            or row.get("provisionedModelName") != handle.record_name
        ):
            raise ManagedServiceError("Bedrock throughput native identity or commitment changed")
        # Updating must not hide a changed desired target while the old target remains active.
        for key in ("modelArn", "desiredModelArn"):
            source = model_arn(row.get(key), self.config)
            if source.split(":", 5)[4] and source.split(":", 5)[4] != handle.arn.split(":", 5)[4]:
                raise ManagedServiceError("Bedrock throughput native custom model account changed")
            if digest(source) != handle.model_hash:
                raise ManagedServiceError("Bedrock throughput native model changed")
        for key in ("modelUnits", "desiredModelUnits"):
            if type(row.get(key)) is not int or row[key] != handle.units:
                raise ManagedServiceError("Bedrock throughput native model units changed")

    def recover_legacy(self, spec: ProvisionSpec, expected: PaidHandle, row: dict[str, Any]) -> PaidHandle:
        cfg = spec.config or {}
        if not all(key in cfg for key in ("model_id", "model_units", "provisioned_throughput")) or not cfg["model_id"]:
            raise ManagedServiceError("Legacy recovery needs the original explicit model, units and commitment")
        tags = self.tags(row["provisionedModelArn"])
        original = {tag["Key"]: tag["Value"] for tag in tags_for(spec)}
        required = (
            "astrolift.io/managed-by",
            "astrolift.io/managed_service_id",
            "astrolift.io/organization",
            "astrolift.io/app",
            "astrolift.io/environment",
            "astrolift.io/cluster",
            "astrolift.io/isolation",
        )
        if any(not original.get(key) for key in required) or tags != original:
            raise ManagedServiceError(
                "Legacy recovery ownership dimensions are missing or differ from the original spec"
            )
        saved = replace(
            expected,
            arn=row["provisionedModelArn"],
            legacy_tags_hash=digest(tags),
            legacy_name=row["provisionedModelName"],
        )
        saved.encode()
        self.verify(row, saved, service=expected.service, organization=expected.organization)
        return saved

    def observe(
        self, handle: str, *, service: str, organization: str = ""
    ) -> tuple[PaidHandle | None, dict[str, Any] | None]:
        saved = PaidHandle.parse(handle)
        target = saved.arn if saved else parse_handle(handle)[1]
        if saved:
            self.validate_arn(saved.arn)
            if saved.log_prefix_hash and saved.log_prefix_hash != digest(self.config.invocation_log_group_prefix):
                raise ManagedServiceError("Bedrock recorded log prefix differs from current driver configuration")
            if identity(service) != saved.service or identity(organization) != saved.organization:
                raise ManagedServiceError("Bedrock throughput caller ownership does not match")
        row = self.get(target)
        if row is None:
            return saved, None
        if saved is None:
            tags = self.tags(row["provisionedModelArn"])
            try:
                saved = PaidHandle(
                    row["provisionedModelArn"],
                    tags["astrolift.io/managed_service_id"],
                    tags[_TAG + "organization-id"],
                    int(tags[_TAG + "units"]),
                    tags[_TAG + "term"],
                    tags[_TAG + "model"],
                    tags[_TAG + "intent"],
                    log_prefix_hash=tags.get(_TAG + "log-prefix", ""),
                )
                if not saved.log_prefix_hash:
                    raise ManagedServiceError("Legacy Bedrock throughput lacks recorded log identity")
                saved = PaidHandle.parse(saved.encode())
            except (KeyError, ValueError) as exc:
                raise ManagedServiceError(
                    "Legacy Bedrock throughput lacks durable intent proof; explicit recovery required"
                ) from exc
        if saved is None:
            raise ManagedServiceError("Bedrock throughput durable identity is missing")
        self.verify(row, saved, service=service, organization=organization)
        return saved, row

    @staticmethod
    def commitment_active(row: dict[str, Any]) -> bool:
        expiry = row.get("commitmentExpirationTime")
        return not isinstance(expiry, datetime) or expiry.tzinfo is None or expiry > datetime.now(UTC)
