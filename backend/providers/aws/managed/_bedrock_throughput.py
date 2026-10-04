"""Native paid-throughput identity; no process-local lifecycle state."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from botocore.exceptions import ClientError

from aws.managed._base import ManagedServiceError, parse_handle, tags_for

if TYPE_CHECKING:
    from _sdk.managed_service import ProvisionSpec
    from aws.managed.model_endpoint_bedrock import AmazonBedrockConfig

_PREFIX = "model_endpoint/pt1|"
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


def model_arn(model: str, config: AmazonBedrockConfig) -> str:
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
            or (account and account != parts[4])
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

    def encode(self) -> str:
        value = _PREFIX + "|".join(
            (self.arn, self.service, self.organization, str(self.units), self.term, self.model_hash, self.intent)
        )
        if len(value) > 512:
            raise ManagedServiceError("Bedrock throughput handle exceeds durable storage")
        return value

    @classmethod
    def parse(cls, handle: str) -> PaidHandle | None:
        kind, resource = parse_handle(handle)
        if kind != "model_endpoint" or len(handle) > 512:
            raise ManagedServiceError("Bedrock service handle is invalid")
        if not resource.startswith("pt1|"):
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", resource):
                raise ManagedServiceError("Bedrock legacy handle is invalid")
            return None
        fields = resource[4:].split("|")
        if len(fields) != 7:
            raise ManagedServiceError("Bedrock throughput handle is malformed")
        arn, service, org, units, term, model_hash, intent = fields
        if (
            not _PT.fullmatch(arn)
            or not re.fullmatch(r"[1-9][0-9]{0,9}", units)
            or int(units) > 2147483647
            or term not in ("OneMonth", "SixMonths")
            or not _HEX.fullmatch(model_hash)
            or not _HEX.fullmatch(intent)
        ):
            raise ManagedServiceError("Bedrock throughput handle is malformed")
        return cls(arn, identity(service), identity(org), int(units), term, model_hash, intent)


class Throughput:
    def __init__(self, client: Any, config: AmazonBedrockConfig):
        self.client = client
        self.config = config

    def validate_arn(self, arn: str) -> None:
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
        handle = PaidHandle("", service, org, units, term, source_hash, intent)
        tags = {row["Key"]: row["Value"] for row in tags_for(spec)}
        tags.update(
            {
                _TAG + "organization-id": org,
                _TAG + "units": str(units),
                _TAG + "term": term,
                _TAG + "model": source_hash,
                _TAG + "intent": intent,
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
        if identity(service) != handle.service or (organization and identity(organization) != handle.organization):
            raise ManagedServiceError("Bedrock throughput caller ownership does not match")
        tags = self.tags(handle.arn)
        expected = {
            "astrolift.io/managed-by": "platform",
            "astrolift.io/managed_service_id": handle.service,
            _TAG + "organization-id": handle.organization,
            _TAG + "units": str(handle.units),
            _TAG + "term": handle.term,
            _TAG + "model": handle.model_hash,
            _TAG + "intent": handle.intent,
        }
        if any(tags.get(key) != value for key, value in expected.items()):
            raise ManagedServiceError(
                "Bedrock throughput live ownership or recorded intent does not match; explicit recovery required"
            )
        if row.get("provisionedModelArn") != handle.arn or row.get("commitmentDuration") != handle.term:
            raise ManagedServiceError("Bedrock throughput native identity or commitment changed")
        # Updating must not hide a changed desired target while the old target remains active.
        for key in ("modelArn", "desiredModelArn"):
            if digest(model_arn(row.get(key), self.config)) != handle.model_hash:
                raise ManagedServiceError("Bedrock throughput native model changed")
        for key in ("modelUnits", "desiredModelUnits"):
            if type(row.get(key)) is not int or row[key] != handle.units:
                raise ManagedServiceError("Bedrock throughput native model units changed")

    def observe(
        self, handle: str, *, service: str, organization: str = ""
    ) -> tuple[PaidHandle | None, dict[str, Any] | None]:
        saved = PaidHandle.parse(handle)
        target = saved.arn if saved else parse_handle(handle)[1]
        if saved:
            self.validate_arn(saved.arn)
            if identity(service) != saved.service or (organization and identity(organization) != saved.organization):
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
                )
                saved = PaidHandle.parse(saved.encode())
            except (KeyError, ValueError) as exc:
                raise ManagedServiceError(
                    "Legacy Bedrock throughput lacks durable intent proof; explicit recovery required"
                ) from exc
        self.verify(row, saved, service=service, organization=organization)
        return saved, row

    @staticmethod
    def commitment_active(row: dict[str, Any]) -> bool:
        expiry = row.get("commitmentExpirationTime")
        return not isinstance(expiry, datetime) or expiry.tzinfo is None or expiry > datetime.now(UTC)
