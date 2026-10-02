"""AWS KMS customer-managed key lifecycle driver."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime
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
from _sdk.physical_naming import managed_service_identity, physical_name
from aws._naming import iam_role_name
from aws.managed._base import ManagedServiceError, handle_for, live_ownership_refusal, parse_handle, tags_for
from aws.session import aws_client

KIND = "encryption_key"
_MANAGED_GRANT_PREFIX = "astrolift-"
_ROTATABLE_SPEC = "SYMMETRIC_DEFAULT"
_ROTATABLE_USAGE = "ENCRYPT_DECRYPT"


@dataclass(frozen=True)
class KMSConfig(CredentialedConfig):
    region: str
    alias_name_prefix: str = "alias/astrolift"
    deletion_protection_default: bool = True
    pending_window_days_default: int = 30
    account_id: str = ""


class KMSDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: KMSConfig,
        client: Any | None = None,
        regional_clients: dict[str, Any] | None = None,
    ) -> None:
        self._config = config
        if client is None:
            client = aws_client("kms", region=config.region, credential=config.credential)
        self._kms = client
        self._regional_clients = dict(regional_clients or {})
        self._regional_clients.setdefault(config.region, client)

    @driver_op(
        cloud="aws",
        driver="kms",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_kms_config"])
        if not self._config.region or re.fullmatch(r"[0-9]{12}", self._config.account_id) is None:
            return ProvisionResult(False, "", "KMS requires a region and 12-digit account_id", ["invalid_kms_identity"])
        key_arn = ""
        try:
            managed_service_identity(spec.managed_service_id)
            alias_name = self._primary_alias(spec, cfg)
            metadata = self._find_owned_key(spec, alias_name)
            if metadata is None:
                response = self._kms.create_key(**self._create_request(spec, cfg))
                metadata = dict(response["KeyMetadata"])
            key_arn = self._target(handle_for(kind=KIND, resource_id=str(metadata["Arn"])))
            metadata = self._metadata(self._kms, key_arn)
            self._assert_owner(self._kms, key_arn, spec.managed_service_id)
            if spec.recorded_handle and not self._alias_from_config(cfg):
                alias_name = self._first_managed_alias(self._kms, key_arn) or alias_name
            self._preflight_children(self._kms, metadata, cfg, alias_name=alias_name)
            self._replica_targets(metadata, spec.managed_service_id)
            self._preflight_replica_aliases(metadata, cfg, alias_name)
            self._reconcile_key(self._kms, metadata, cfg, alias_name=alias_name)
            metadata = self._metadata(self._kms, key_arn)
            self._ensure_replicas(metadata, cfg, spec.managed_service_id)
        except Exception as exc:
            handle = handle_for(kind=KIND, resource_id=key_arn) if key_arn else ""
            return ProvisionResult(False, handle, f"provision KMS key: {exc}", [str(exc)])
        state = str(metadata.get("KeyState") or "Enabled")
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=key_arn),
            f"KMS key {metadata.get('KeyId')} is {state}",
            ready=state == "Enabled",
        )

    @driver_op(cloud="aws", driver="kms")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        key_arn = self._target(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_kms_config"])
        try:
            metadata = self._metadata(self._kms, key_arn)
            self._assert_owner(self._kms, key_arn, spec.managed_service_id)
            alias_name = self._alias_from_config(cfg) or self._first_managed_alias(self._kms, key_arn)
            self._preflight_children(self._kms, metadata, cfg, alias_name=alias_name)
            self._replica_targets(metadata, spec.managed_service_id)
            self._preflight_replica_aliases(metadata, cfg, alias_name)
            if metadata.get("KeyState") in {"PendingDeletion", "PendingReplicaDeletion"}:
                if not cfg.get("cancel_pending_deletion"):
                    return UpdateResult(
                        False,
                        spec.handle,
                        "KMS key is pending deletion; set cancel_pending_deletion=true to recover it",
                        ["pending_deletion"],
                    )
                self._kms.cancel_key_deletion(KeyId=key_arn)
                metadata = self._metadata(self._kms, key_arn)
            self._reconcile_key(self._kms, metadata, cfg, alias_name=alias_name)
            metadata = self._metadata(self._kms, key_arn)
            self._ensure_replicas(metadata, cfg, spec.managed_service_id)
        except ManagedServiceError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["resource_not_owned"], retryable=False)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, "KMS key not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update KMS key: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, "KMS key reconciled")

    @driver_op(
        cloud="aws",
        driver="kms",
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
        key_arn = self._target(spec.handle)
        try:
            metadata = self._metadata(self._kms, key_arn)
            self._assert_owner(self._kms, key_arn, spec.managed_service_id)
            self._aliases(self._kms, key_id=key_arn)
            self._grants(self._kms, key_arn)
            replicas = self._replica_targets(metadata, spec.managed_service_id)
        except ManagedServiceError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["resource_not_owned"], retryable=False)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, "KMS key already gone")
            return DeprovisionResult(False, spec.handle, f"describe KMS key: {exc}", [str(exc)])
        if metadata.get("KeyState") in {"PendingDeletion", "PendingReplicaDeletion"}:
            return DeprovisionResult(True, spec.handle, f"KMS key is {metadata.get('KeyState')}")
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
                "KMS key has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "KMS key material cannot be retained independently; set delete_data=true to schedule deletion",
                ["delete_data_required"],
                retryable=False,
            )
        pending_window = int(
            spec.config.get(
                "pending_window_days",
                self._config.pending_window_days_default,
            ),
        )
        if not 7 <= pending_window <= 30:
            return DeprovisionResult(
                False,
                spec.handle,
                "KMS pending_window_days must be between 7 and 30",
                ["invalid_pending_window"],
                retryable=False,
            )
        try:
            self._schedule_replicas(replicas, pending_window)
            self._revoke_managed_grants(self._kms, str(metadata["KeyId"]), desired_names=set())
            response = self._kms.schedule_key_deletion(
                KeyId=str(metadata["KeyId"]),
                PendingWindowInDays=pending_window,
            )
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"schedule KMS key deletion: {exc}", [str(exc)])
        return DeprovisionResult(
            True,
            spec.handle,
            f"KMS key deletion scheduled for {response.get('DeletionDate')}",
        )

    @driver_op(cloud="aws", driver="kms")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            key_arn = self._target(handle.handle)
            metadata = self._metadata(self._kms, key_arn)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", "KMS key does not exist")
            return ServiceStatus(handle.handle, "error", f"describe KMS key: {exc}")
        key_state = str(metadata.get("KeyState") or "Unavailable")
        state = {
            "Enabled": "available",
            "Disabled": "available",
            "Creating": "provisioning",
            "PendingImport": "provisioning",
            "Updating": "updating",
            "PendingDeletion": "deprovisioning",
            "PendingReplicaDeletion": "deprovisioning",
        }.get(key_state, "error")
        return ServiceStatus(handle.handle, state, f"KMS key is {key_state}")

    @driver_op(cloud="aws", driver="kms")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        key_arn = self._target(handle.handle)
        metadata = self._metadata(self._kms, key_arn)
        key_id = str(metadata["KeyId"])
        key_spec = str(metadata.get("KeySpec") or metadata.get("CustomerMasterKeySpec") or "SYMMETRIC_DEFAULT")
        key_usage = str(metadata.get("KeyUsage") or "ENCRYPT_DECRYPT")
        alias_name = self._first_managed_alias(self._kms, key_id)
        actions = _binding_actions(
            key_spec=key_spec,
            key_usage=key_usage,
            access_mode=str((config or {}).get("access_mode") or "use"),
        )
        return Binding(
            env_vars={
                "ENCRYPTION_KEY_ID": ValueRef(literal=key_id),
                "ENCRYPTION_KEY_ARN": ValueRef(literal=key_arn),
                "ENCRYPTION_KEY_ALIAS": ValueRef(literal=alias_name),
                "ENCRYPTION_KEY_SPEC": ValueRef(literal=key_spec),
                "ENCRYPTION_KEY_USAGE": ValueRef(literal=key_usage),
                "ENCRYPTION_KEY_MULTI_REGION": ValueRef(literal=str(bool(metadata.get("MultiRegion"))).lower()),
                "KMS_KEY_ID": ValueRef(literal=key_id),
                "KMS_KEY_ARN": ValueRef(literal=key_arn),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=[Grant(resource=key_arn, actions=actions)],
            notes="AWS KMS customer-managed key. Key policy must also authorize the workload principal.",
        )

    @driver_op(cloud="aws", driver="kms")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "AWS KMS key material is non-exportable; use a multi-Region replica or alias rotation instead of snapshot",
        )

    @driver_op(cloud="aws", driver="kms")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "AWS KMS key material cannot be restored from a snapshot",
            ["not_implemented"],
        )

    @driver_op(cloud="aws", driver="kms", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "alias": {"type": "string", "description": "KMS alias name, with or without alias/ prefix."},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "policy": {"description": "KMS key policy as an object or serialized JSON string."},
                "bypass_policy_lockout_safety_check": {"type": "boolean", "default": False},
                "key": {
                    "type": "object",
                    "description": (
                        "Native boto3 create_key fields except Astrolift-owned "
                        "description, policy, tags, and MultiRegion."
                    ),
                },
                "enabled": {"type": "boolean", "default": True},
                "rotation_enabled": {"type": "boolean"},
                "rotation_period_days": {"type": "integer", "minimum": 90, "maximum": 2560},
                "multi_region": {"type": "boolean", "default": False},
                "replica_regions": {"type": "array", "items": {"type": "string"}},
                "replica": {
                    "type": "object",
                    "description": (
                        "Native replicate_key fields except KeyId, ReplicaRegion, tags, description, and policy."
                    ),
                },
                "grants": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "request"],
                        "properties": {"name": {"type": "string"}, "request": {"type": "object"}},
                    },
                },
                "import_key_material": {
                    "type": "object",
                    "description": "EncryptedKeyMaterial and ImportToken as base64 plus native expiration controls.",
                    "required": ["encrypted_key_material_b64", "import_token_b64"],
                    "properties": {
                        "encrypted_key_material_b64": {"type": "string", "contentEncoding": "base64"},
                        "import_token_b64": {"type": "string", "contentEncoding": "base64"},
                        "expiration_model": {
                            "type": "string",
                            "enum": ["KEY_MATERIAL_EXPIRES", "KEY_MATERIAL_DOES_NOT_EXPIRE"],
                            "default": "KEY_MATERIAL_DOES_NOT_EXPIRE",
                        },
                        "valid_to": {"type": "string", "format": "date-time"},
                    },
                },
                "access_mode": {
                    "type": "string",
                    "enum": ["use", "encrypt", "decrypt", "public"],
                    "default": "use",
                },
                "cancel_pending_deletion": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
                "pending_window_days": {"type": "integer", "minimum": 7, "maximum": 30},
            },
        }

    @driver_op(cloud="aws", driver="kms", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "ENCRYPTION_KEY_ID": "KMS key ID",
                "ENCRYPTION_KEY_ARN": "KMS key ARN",
                "ENCRYPTION_KEY_ALIAS": "Primary KMS alias",
                "ENCRYPTION_KEY_SPEC": "KMS key spec",
                "ENCRYPTION_KEY_USAGE": "KMS key usage",
                "ENCRYPTION_KEY_MULTI_REGION": "Whether this is a multi-Region key",
                "KMS_KEY_ID": "AWS-native key ID alias",
                "KMS_KEY_ARN": "AWS-native key ARN alias",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return ["*"]

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        for key in ("key", "replica", "import_key_material"):
            if key in cfg and not isinstance(cfg[key], dict):
                return f"config.{key} must be an object"
        for key in ("aliases", "replica_regions", "grants"):
            if key in cfg and not isinstance(cfg[key], list):
                return f"config.{key} must be an array"
        reserved = {
            "Description",
            "Policy",
            "Tags",
            "MultiRegion",
        }.intersection((cfg.get("key") or {}).keys())
        if reserved:
            return f"config.key cannot override Astrolift-owned fields: {', '.join(sorted(reserved))}"
        reserved_replica = {"KeyId", "ReplicaRegion", "Description", "Policy", "Tags"}.intersection(
            (cfg.get("replica") or {}).keys(),
        )
        if reserved_replica:
            return f"config.replica cannot override Astrolift-owned fields: {', '.join(sorted(reserved_replica))}"
        try:
            aliases = self._alias_names(cfg)
        except ValueError as exc:
            return str(exc)
        if len(aliases) != len(set(aliases)):
            return "KMS alias names must be unique"
        replicas = [str(region) for region in cfg.get("replica_regions") or []]
        if len(replicas) != len(set(replicas)):
            return "config.replica_regions must be unique"
        if self._config.region in replicas:
            return "config.replica_regions cannot include the primary region"
        if replicas:
            from botocore.session import get_session

            session = get_session()
            partition = session.get_partition_for_region(self._config.region)
            if any(
                re.fullmatch(r"(?:[a-z]+-){2,3}[0-9]+", region) is None
                or session.get_partition_for_region(region) != partition
                for region in replicas
            ):
                return "replica_regions must be valid region IDs in the primary AWS partition"
        if replicas and cfg.get("multi_region") is False:
            return "replica_regions require multi_region=true or omission of multi_region"
        if cfg.get("rotation_period_days") is not None:
            try:
                period = int(cfg["rotation_period_days"])
            except (TypeError, ValueError):
                return "rotation_period_days must be an integer"
            if not 90 <= period <= 2560:
                return "rotation_period_days must be between 90 and 2560"
        if cfg.get("pending_window_days") is not None:
            try:
                period = int(cfg["pending_window_days"])
            except (TypeError, ValueError):
                return "pending_window_days must be an integer"
            if not 7 <= period <= 30:
                return "pending_window_days must be between 7 and 30"
        access_mode = str(cfg.get("access_mode") or "use")
        if access_mode not in {"use", "encrypt", "decrypt", "public"}:
            return "access_mode must be use, encrypt, decrypt, or public"
        policy = cfg.get("policy")
        if policy is not None:
            try:
                parsed = json.loads(_policy(policy))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return f"config.policy must be valid JSON: {exc}"
            if not isinstance(parsed, dict):
                return "config.policy must encode an object"
        material = cfg.get("import_key_material") or {}
        if material:
            if (cfg.get("key") or {}).get("Origin") != "EXTERNAL":
                return "import_key_material requires key.Origin=EXTERNAL"
            if not material.get("encrypted_key_material_b64") or not material.get("import_token_b64"):
                return "import_key_material requires encrypted_key_material_b64 and import_token_b64"
            try:
                base64.b64decode(str(material["encrypted_key_material_b64"]), validate=True)
                base64.b64decode(str(material["import_token_b64"]), validate=True)
            except (ValueError, TypeError):
                return "import_key_material values must be valid base64"
            expiration_model = str(material.get("expiration_model") or "KEY_MATERIAL_DOES_NOT_EXPIRE")
            if expiration_model not in {"KEY_MATERIAL_EXPIRES", "KEY_MATERIAL_DOES_NOT_EXPIRE"}:
                return "import_key_material.expiration_model is invalid"
            if expiration_model == "KEY_MATERIAL_EXPIRES" and material.get("valid_to") is None:
                return "import_key_material.valid_to is required when key material expires"
            if expiration_model == "KEY_MATERIAL_DOES_NOT_EXPIRE" and material.get("valid_to") is not None:
                return "import_key_material.valid_to requires KEY_MATERIAL_EXPIRES"
            if material.get("valid_to") is not None:
                try:
                    _timestamp(material["valid_to"])
                except ManagedServiceError as exc:
                    return str(exc)
        grant_names: set[str] = set()
        for index, grant in enumerate(cfg.get("grants") or []):
            if not isinstance(grant, dict) or not str(grant.get("name") or ""):
                return f"config.grants[{index}] requires a non-empty name"
            if not isinstance(grant.get("request"), dict):
                return f"config.grants[{index}].request must be an object"
            reserved_grant = {"KeyId", "GrantId", "Name"}.intersection(grant["request"])
            if reserved_grant:
                return (
                    f"config.grants[{index}].request cannot override Astrolift-owned fields: "
                    f"{', '.join(sorted(reserved_grant))}"
                )
            name = iam_role_name(_MANAGED_GRANT_PREFIX.rstrip("-"), str(grant["name"]), max_len=256)
            if name in grant_names:
                return "KMS grant names must be unique after provider normalization"
            grant_names.add(name)
        return ""

    def _create_request(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, Any]:
        request = dict(cfg.get("key") or {})
        request["Description"] = str(
            cfg.get("description")
            or f"Astrolift managed key for {spec.organization_slug}/{spec.app_slug}/{spec.environment_name}"
        )
        if "policy" in cfg:
            request["Policy"] = _policy(cfg["policy"])
        request["MultiRegion"] = bool(cfg.get("multi_region") or cfg.get("replica_regions"))
        tags = tags_for(spec)
        tags.append({"Key": "astrolift.io/resource-hint", "Value": spec.service_handle_hint})
        request["Tags"] = [{"TagKey": item["Key"], "TagValue": item["Value"]} for item in tags]
        return request

    def _find_owned_key(self, spec: ProvisionSpec, alias_name: str) -> dict[str, Any] | None:
        if spec.recorded_handle:
            arn = self._target(spec.recorded_handle)
            metadata = self._metadata(self._kms, arn)
            self._assert_owner(self._kms, arn, spec.managed_service_id)
            return metadata
        alias = self._find_alias(self._kms, alias_name)
        if alias is not None:
            metadata = self._metadata(self._kms, str(alias["TargetKeyId"]))
            self._assert_owner(self._kms, str(metadata["Arn"]), spec.managed_service_id)
            return metadata
        found: dict[str, Any] | None = None
        for item in self._keys(self._kms):
            key_id = str(item["KeyId"])
            tags = self._tags(self._kms, key_id)
            if tags.get("astrolift.io/managed_service_id") == spec.managed_service_id:
                self._assert_owner(self._kms, key_id, spec.managed_service_id)
                metadata = self._metadata(self._kms, key_id)
                if found and found["Arn"] != metadata["Arn"]:
                    raise ManagedServiceError("managed-service identity resolves to multiple KMS keys")
                found = metadata
        return found

    def _target(self, handle: str, *, region: str = "") -> str:
        from botocore.session import get_session

        kind, arn = parse_handle(handle)
        region = region or self._config.region
        fields = arn.split(":", 5)
        partition = get_session().get_partition_for_region(region)
        if (
            kind != KIND
            or not region
            or re.fullmatch(r"[0-9]{12}", self._config.account_id) is None
            or len(fields) != 6
            or fields[:5] != ["arn", partition, "kms", region, self._config.account_id]
            or re.fullmatch(r"key/(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|mrk-[0-9a-f]{32})", fields[5]) is None
        ):
            raise ManagedServiceError("recorded KMS ARN does not match the configured key target")
        return arn

    def _metadata(self, client: Any, key_id: str, *, region: str = "") -> dict[str, Any]:
        metadata = dict(client.describe_key(KeyId=key_id)["KeyMetadata"])
        arn = self._target(handle_for(kind=KIND, resource_id=str(metadata.get("Arn") or "")), region=region)
        if (
            metadata.get("KeyId") != arn.rsplit("/", 1)[1]
            or metadata.get("AWSAccountId") != self._config.account_id
            or metadata.get("KeyManager") != "CUSTOMER"
            or key_id not in {arn, metadata.get("KeyId")}
        ):
            raise ManagedServiceError("live KMS key does not match the exact recorded identity")
        return metadata

    def _assert_owner(self, client: Any, key_id: str, identity: str) -> None:
        refusal = live_ownership_refusal(self._tags(client, key_id), managed_service_id=identity, resource="KMS key")
        if refusal:
            raise ManagedServiceError(refusal)

    def _preflight_children(
        self, client: Any, metadata: dict[str, Any], cfg: dict[str, Any], *, alias_name: str
    ) -> None:
        key_id = str(metadata["KeyId"])
        self._aliases(client, key_id=key_id)
        self._grants(client, str(metadata["Arn"]))
        aliases = {str(item["AliasName"]): item for item in self._aliases(client)}
        for name in self._alias_names(cfg, default=alias_name):
            alias = aliases.get(name)
            if alias and alias.get("TargetKeyId") != key_id:
                raise ManagedServiceError(f"alias {name!r} already targets another KMS key")

    def _reconcile_key(
        self,
        client: Any,
        metadata: dict[str, Any],
        cfg: dict[str, Any],
        *,
        alias_name: str,
    ) -> None:
        key_id = str(metadata["KeyId"])
        self._validate_immutable(metadata, cfg)
        material = cfg.get("import_key_material") or {}
        if metadata.get("KeyState") == "PendingImport" and material:
            request: dict[str, Any] = {
                "KeyId": key_id,
                "EncryptedKeyMaterial": base64.b64decode(
                    str(material["encrypted_key_material_b64"]),
                    validate=True,
                ),
                "ImportToken": base64.b64decode(str(material["import_token_b64"]), validate=True),
                "ExpirationModel": str(material.get("expiration_model") or "KEY_MATERIAL_DOES_NOT_EXPIRE"),
            }
            if material.get("valid_to") is not None:
                request["ValidTo"] = _timestamp(material["valid_to"])
            client.import_key_material(**request)
            metadata = client.describe_key(KeyId=key_id)["KeyMetadata"]
        if "description" in cfg and str(metadata.get("Description") or "") != str(cfg["description"]):
            client.update_key_description(KeyId=key_id, Description=str(cfg["description"]))
        if "policy" in cfg:
            client.put_key_policy(
                KeyId=key_id,
                PolicyName="default",
                Policy=_policy(cfg["policy"]),
                BypassPolicyLockoutSafetyCheck=bool(cfg.get("bypass_policy_lockout_safety_check", False)),
            )
        enabled = bool(cfg.get("enabled", True))
        state = str(metadata.get("KeyState") or "")
        if enabled and state == "Disabled":
            client.enable_key(KeyId=key_id)
        elif not enabled and state == "Enabled":
            client.disable_key(KeyId=key_id)
        self._reconcile_rotation(client, metadata, cfg)
        for name in self._alias_names(cfg, default=alias_name):
            self._ensure_alias(client, name, key_id)
        self._reconcile_grants(client, key_id, cfg)

    def _validate_immutable(self, metadata: dict[str, Any], cfg: dict[str, Any]) -> None:
        native = cfg.get("key") or {}
        expected = {
            "KeySpec": native.get("KeySpec") or native.get("CustomerMasterKeySpec"),
            "KeyUsage": native.get("KeyUsage"),
            "Origin": native.get("Origin"),
            "MultiRegion": bool(cfg.get("multi_region") or cfg.get("replica_regions"))
            if "multi_region" in cfg or cfg.get("replica_regions")
            else None,
        }
        for field, value in expected.items():
            if value is not None and metadata.get(field) != value:
                raise ManagedServiceError(
                    f"KMS {field} is immutable "
                    f"({metadata.get(field)!r} != requested {value!r}); "
                    "reprovision is required",
                )

    def _reconcile_rotation(self, client: Any, metadata: dict[str, Any], cfg: dict[str, Any]) -> None:
        eligible = _rotation_eligible(metadata)
        requested = cfg.get("rotation_enabled")
        if requested is None:
            requested = eligible
        if requested and not eligible:
            raise ManagedServiceError(
                "automatic rotation is supported only for AWS_KMS-origin symmetric encryption primary keys",
            )
        if not eligible:
            return
        key_id = str(metadata["KeyId"])
        if requested:
            request: dict[str, Any] = {"KeyId": key_id}
            if cfg.get("rotation_period_days") is not None:
                request["RotationPeriodInDays"] = int(cfg["rotation_period_days"])
            client.enable_key_rotation(**request)
        else:
            client.disable_key_rotation(KeyId=key_id)

    def _reconcile_grants(self, client: Any, key_id: str, cfg: dict[str, Any]) -> None:
        existing = [item for item in self._grants(client, key_id) if _is_managed_grant(item)]
        desired_names: set[str] = set()
        for declaration in cfg.get("grants") or []:
            name = iam_role_name(_MANAGED_GRANT_PREFIX.rstrip("-"), str(declaration["name"]), max_len=256)
            desired_names.add(name)
            request = dict(declaration["request"])
            current = next((item for item in existing if item.get("Name") == name), None)
            if current is not None and _grant_matches(current, request):
                continue
            if current is not None:
                client.revoke_grant(KeyId=key_id, GrantId=str(current["GrantId"]))
            client.create_grant(KeyId=key_id, Name=name, **request)
        self._revoke_managed_grants(client, key_id, desired_names=desired_names)

    def _revoke_managed_grants(self, client: Any, key_id: str, *, desired_names: set[str]) -> None:
        for grant in self._grants(client, key_id):
            name = str(grant.get("Name") or "")
            if name.startswith(_MANAGED_GRANT_PREFIX) and name not in desired_names:
                client.revoke_grant(KeyId=key_id, GrantId=str(grant["GrantId"]))

    def _replica_targets(self, metadata: dict[str, Any], identity: str) -> list[tuple[Any, dict[str, Any]]]:
        configuration = metadata.get("MultiRegionConfiguration") or {}
        declared = configuration.get("ReplicaKeys") or []
        if declared and configuration.get("MultiRegionKeyType") != "PRIMARY":
            raise ManagedServiceError("replica list does not belong to a primary KMS key")
        if configuration.get("MultiRegionKeyType") == "PRIMARY" and configuration.get("PrimaryKey") != {
            "Arn": metadata["Arn"],
            "Region": self._config.region,
        }:
            raise ManagedServiceError("KMS primary metadata points to another key")
        targets: list[tuple[Any, dict[str, Any]]] = []
        seen: set[str] = set()
        for item in declared:
            region, arn = str(item.get("Region") or ""), str(item.get("Arn") or "")
            if not region or region == self._config.region or region in seen:
                raise ManagedServiceError("KMS replica region cannot be verified")
            seen.add(region)
            self._target(handle_for(kind=KIND, resource_id=arn), region=region)
            if arn.rsplit("/", 1)[1] != metadata["KeyId"]:
                raise ManagedServiceError("KMS replica belongs to another primary key")
            client = self._client(region)
            replica = self._metadata(client, arn, region=region)
            self._verify_replica(metadata, replica, region)
            self._assert_owner(client, arn, identity)
            self._aliases(client, key_id=str(replica["KeyId"]))
            self._grants(client, arn)
            targets.append((client, replica))
        return targets

    def _preflight_replica_aliases(self, metadata: dict[str, Any], cfg: dict[str, Any], alias_name: str) -> None:
        for region in cfg.get("replica_regions") or []:
            client = self._client(str(region))
            aliases = {str(item["AliasName"]): item for item in self._aliases(client)}
            for name in self._alias_names(cfg, default=alias_name):
                alias = aliases.get(name)
                if alias and alias.get("TargetKeyId") != metadata["KeyId"]:
                    raise ManagedServiceError(f"replica alias {name!r} already targets another KMS key")

    def _verify_replica(self, primary: dict[str, Any], replica: dict[str, Any], region: str) -> None:
        self._target(handle_for(kind=KIND, resource_id=str(replica.get("Arn") or "")), region=region)
        configuration = replica.get("MultiRegionConfiguration") or {}
        if (
            replica.get("KeyId") != primary["KeyId"]
            or replica.get("Arn") != self._arn_prefix(region) + "key/" + str(primary["KeyId"])
            or replica.get("AWSAccountId") != self._config.account_id
            or replica.get("KeyManager") != "CUSTOMER"
            or not replica.get("MultiRegion")
            or configuration.get("MultiRegionKeyType") != "REPLICA"
            or configuration.get("PrimaryKey") != {"Arn": primary["Arn"], "Region": self._config.region}
        ):
            raise ManagedServiceError("KMS replica metadata does not match the exact primary")

    def _ensure_replicas(self, metadata: dict[str, Any], cfg: dict[str, Any], identity: str) -> None:
        desired = [str(region) for region in cfg.get("replica_regions") or []]
        if not desired:
            return
        if not metadata.get("MultiRegion"):
            raise ManagedServiceError("replica_regions require a multi-Region primary KMS key")
        configuration = metadata.get("MultiRegionConfiguration") or {}
        if configuration.get("MultiRegionKeyType") != "PRIMARY":
            raise ManagedServiceError("replicas can be added only from the multi-Region primary key")
        current = {
            str(item.get("Region")): str(item.get("Arn") or "") for item in configuration.get("ReplicaKeys", []) or []
        }
        primary_tags = [
            {"TagKey": key, "TagValue": value} for key, value in self._tags(self._kms, str(metadata["Arn"])).items()
        ]
        alias = self._alias_from_config(cfg) or self._first_managed_alias(self._kms, str(metadata["KeyId"]))
        for region in desired:
            client = self._client(region)
            if region not in current:
                request = dict(cfg.get("replica") or {})
                request.update(
                    KeyId=str(metadata["KeyId"]),
                    ReplicaRegion=region,
                    Description=str(cfg.get("description") or metadata.get("Description") or ""),
                    Tags=primary_tags,
                )
                if "policy" in cfg:
                    request["Policy"] = _policy(cfg["policy"])
                response = self._kms.replicate_key(**request)
                returned = dict(response["ReplicaKeyMetadata"])
                self._verify_replica(metadata, returned, region)
                arn = str(returned["Arn"])
            else:
                arn = current[region]
            replica = self._metadata(client, arn, region=region)
            self._verify_replica(metadata, replica, region)
            self._assert_owner(client, arn, identity)
            self._preflight_children(client, replica, cfg, alias_name=alias)
            self._reconcile_key(client, replica, cfg, alias_name=alias)

    def _schedule_replicas(self, targets: list[tuple[Any, dict[str, Any]]], pending_window: int) -> None:
        for client, metadata in targets:
            if metadata.get("KeyState") == "PendingDeletion":
                continue
            self._revoke_managed_grants(client, str(metadata["KeyId"]), desired_names=set())
            client.schedule_key_deletion(KeyId=str(metadata["KeyId"]), PendingWindowInDays=pending_window)

    def _client(self, region: str) -> Any:
        if region not in self._regional_clients:
            # A per-region client cache. The region varies; the identity
            # does not, so it comes off the config rather than the argument.
            self._regional_clients[region] = aws_client("kms", region=region, credential=self._config.credential)
        return self._regional_clients[region]

    def _primary_alias(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        explicit = self._alias_from_config(cfg)
        if explicit:
            return explicit
        prefix = self._config.alias_name_prefix.removeprefix("alias/").rstrip("-/")
        return "alias/" + physical_name(spec.managed_service_id, prefix=prefix, max_length=250)

    def _alias_names(self, cfg: dict[str, Any], *, default: str = "") -> list[str]:
        names: list[str] = []
        primary = self._alias_from_config(cfg) or default
        if primary:
            names.append(primary)
        for raw in cfg.get("aliases") or []:
            names.append(_alias(str(raw)))
        return names

    @staticmethod
    def _alias_from_config(cfg: dict[str, Any]) -> str:
        raw = str(cfg.get("alias") or "").strip()
        return _alias(raw) if raw else ""

    def _ensure_alias(self, client: Any, alias_name: str, key_id: str) -> None:
        existing = self._find_alias(client, alias_name)
        if existing is None:
            client.create_alias(AliasName=alias_name, TargetKeyId=key_id)
        elif str(existing.get("TargetKeyId") or "") != key_id:
            raise ManagedServiceError(f"alias {alias_name!r} already targets another KMS key")

    def _first_managed_alias(self, client: Any, key_id: str) -> str:
        aliases = self._aliases(client, key_id=key_id)
        preferred_prefix = self._config.alias_name_prefix.rstrip("-/")
        item = next(
            (alias for alias in aliases if str(alias.get("AliasName") or "").startswith(preferred_prefix)),
            None,
        )
        return str(
            (
                item
                or next(
                    (alias for alias in aliases if not str(alias.get("AliasName") or "").startswith("alias/aws/")), {}
                )
            ).get("AliasName")
            or ""
        )

    def _find_alias(self, client: Any, alias_name: str) -> dict[str, Any] | None:
        return next(
            (item for item in self._aliases(client) if item.get("AliasName") == alias_name),
            None,
        )

    def _aliases(self, client: Any, *, key_id: str = "") -> list[dict[str, Any]]:
        aliases: list[dict[str, Any]] = []
        targets: dict[str, str | None] = {}
        marker = ""
        seen: set[str] = set()
        region = self._client_region(client)
        expected = key_id.rsplit("/", 1)[-1] if key_id else ""
        while True:
            request: dict[str, Any] = {"Limit": 100}
            if key_id:
                request["KeyId"] = key_id
            if marker:
                request["Marker"] = marker
            response = client.list_aliases(**request)
            for item in response.get("Aliases", []) or []:
                name = str(item.get("AliasName") or "")
                expected_arn = self._arn_prefix(region) + name
                if not name.startswith("alias/") or item.get("AliasArn") != expected_arn:
                    raise ManagedServiceError("KMS alias identity cannot be verified")
                if expected and item.get("TargetKeyId") != expected:
                    raise ManagedServiceError("KMS alias belongs to another key")
                if name in targets and targets[name] != item.get("TargetKeyId"):
                    raise ManagedServiceError("KMS alias resolves to ambiguous key identities")
                targets[name] = item.get("TargetKeyId")
                aliases.append(dict(item))
            marker = self._next_marker(response, seen)
            if not marker:
                return aliases

    def _keys(self, client: Any) -> list[dict[str, Any]]:
        keys: list[dict[str, Any]] = []
        marker = ""
        seen: set[str] = set()
        while True:
            request: dict[str, Any] = {"Limit": 1000}
            if marker:
                request["Marker"] = marker
            response = client.list_keys(**request)
            for item in response.get("Keys", []) or []:
                arn = self._target(handle_for(kind=KIND, resource_id=str(item.get("KeyArn") or "")))
                if item.get("KeyId") != arn.rsplit("/", 1)[1]:
                    raise ManagedServiceError("listed KMS key identity cannot be verified")
                keys.append(dict(item))
            marker = self._next_marker(response, seen)
            if not marker:
                return keys

    def _grants(self, client: Any, key_id: str) -> list[dict[str, Any]]:
        grants: list[dict[str, Any]] = []
        marker = ""
        seen: set[str] = set()
        region = self._client_region(client)
        expected_arn = key_id if key_id.startswith("arn:") else self._arn_prefix(region) + "key/" + key_id
        self._target(handle_for(kind=KIND, resource_id=expected_arn), region=region)
        while True:
            request: dict[str, Any] = {"KeyId": key_id, "Limit": 100}
            if marker:
                request["Marker"] = marker
            response = client.list_grants(**request)
            for item in response.get("Grants", []) or []:
                if (
                    item.get("KeyId") not in {expected_arn, expected_arn.rsplit("/", 1)[1]}
                    or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(item.get("GrantId") or "")) is None
                ):
                    raise ManagedServiceError("KMS grant does not belong to the exact key")
                grants.append(dict(item))
            marker = self._next_marker(response, seen)
            if not marker:
                return grants

    def _tags(self, client: Any, key_id: str) -> dict[str, str]:
        tags: dict[str, str] = {}
        marker = ""
        seen: set[str] = set()
        while True:
            request: dict[str, Any] = {"KeyId": key_id, "Limit": 50}
            if marker:
                request["Marker"] = marker
            response = client.list_resource_tags(**request)
            for item in response.get("Tags", []) or []:
                name, value = item.get("TagKey"), item.get("TagValue")
                if not isinstance(name, str) or not isinstance(value, str) or name in tags:
                    raise ManagedServiceError("KMS ownership tags cannot be verified")
                tags[name] = value
            marker = self._next_marker(response, seen)
            if not marker:
                return tags

    def _client_region(self, client: Any) -> str:
        return str(
            getattr(getattr(client, "meta", None), "region_name", "")
            or getattr(client, "region", "")
            or self._config.region
        )

    def _arn_prefix(self, region: str) -> str:
        from botocore.session import get_session

        return f"arn:{get_session().get_partition_for_region(region)}:kms:{region}:{self._config.account_id}:"

    @staticmethod
    def _next_marker(response: dict[str, Any], seen: set[str]) -> str:
        if not response.get("Truncated"):
            return ""
        marker = response.get("NextMarker")
        if not isinstance(marker, str) or not marker or marker in seen:
            raise ManagedServiceError("KMS pagination cannot be verified")
        seen.add(marker)
        return marker


def _policy(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str):
        return value
    raise TypeError("policy must be an object or JSON string")


def _alias(value: str) -> str:
    alias_name = value if value.startswith("alias/") else f"alias/{value}"
    if alias_name.startswith("alias/aws/"):
        raise ValueError("aliases in the reserved alias/aws/ namespace cannot be managed")
    if len(alias_name) > 256 or len(alias_name) < 7:
        raise ValueError("KMS alias must be between 1 and 250 characters after alias/")
    if any(not (character.isascii() and (character.isalnum() or character in "-_/:")) for character in alias_name):
        raise ValueError("KMS alias contains unsupported characters")
    return alias_name


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ManagedServiceError("valid_to must be an ISO 8601 timestamp") from exc
    raise ManagedServiceError("valid_to must be an ISO 8601 timestamp")


def _is_managed_grant(item: dict[str, Any]) -> bool:
    return str(item.get("Name") or "").startswith(_MANAGED_GRANT_PREFIX)


def _rotation_eligible(metadata: dict[str, Any]) -> bool:
    multi_region = metadata.get("MultiRegionConfiguration") or {}
    return (
        (metadata.get("KeySpec") or metadata.get("CustomerMasterKeySpec")) == _ROTATABLE_SPEC
        and metadata.get("KeyUsage") == _ROTATABLE_USAGE
        and metadata.get("Origin") == "AWS_KMS"
        and multi_region.get("MultiRegionKeyType", "PRIMARY") != "REPLICA"
    )


def _binding_actions(*, key_spec: str, key_usage: str, access_mode: str) -> list[str]:
    if access_mode == "public":
        actions = ["kms:DescribeKey"]
        if key_spec != _ROTATABLE_SPEC:
            actions.append("kms:GetPublicKey")
        return actions
    if key_usage == "SIGN_VERIFY":
        actions = ["kms:DescribeKey", "kms:GetPublicKey"]
        if access_mode in {"use", "encrypt"}:
            actions.append("kms:Sign")
        if access_mode in {"use", "decrypt"}:
            actions.append("kms:Verify")
        return actions
    if key_usage == "GENERATE_VERIFY_MAC":
        actions = ["kms:DescribeKey"]
        if access_mode in {"use", "encrypt"}:
            actions.append("kms:GenerateMac")
        if access_mode in {"use", "decrypt"}:
            actions.append("kms:VerifyMac")
        return actions
    actions = ["kms:DescribeKey"]
    if access_mode in {"use", "encrypt"}:
        actions.append("kms:Encrypt")
        if key_spec == _ROTATABLE_SPEC:
            actions.extend(["kms:GenerateDataKey", "kms:GenerateDataKeyWithoutPlaintext"])
    if access_mode in {"use", "decrypt"}:
        actions.append("kms:Decrypt")
    if access_mode == "use":
        actions.extend(["kms:ReEncryptFrom", "kms:ReEncryptTo"])
    return actions


def _grant_matches(existing: dict[str, Any], request: dict[str, Any]) -> bool:
    fields = ("GranteePrincipal", "RetiringPrincipal", "Operations", "Constraints")
    return all(existing.get(field) == request.get(field) for field in fields)


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    return code == "NotFoundException" or "not found" in str(exc).lower()
