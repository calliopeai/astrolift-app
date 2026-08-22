"""Amazon OpenSearch Serverless search and vector collection drivers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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

_COLLECTION_TYPES = {"SEARCH", "VECTORSEARCH"}
_STATE = {
    "ACTIVE": "available",
    "CREATING": "provisioning",
    "UPDATING": "updating",
    "DELETING": "deprovisioning",
    "FAILED": "error",
}


@dataclass(frozen=True)
class OpenSearchServerlessConfig(CredentialedConfig):
    region: str
    account_id: str
    collection_type: str = "SEARCH"
    collection_name_prefix: str = "astrolift"
    vpc_endpoint_ids: list[str] = field(default_factory=list)
    public_access_default: bool = False
    kms_key_arn: str = ""
    standby_replicas_default: str = "DISABLED"
    deletion_protection_default: bool = True
    data_access_principals: list[str] = field(default_factory=list)
    source_services: list[str] = field(default_factory=list)


class OpenSearchServerlessDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: OpenSearchServerlessConfig,
        opensearch_serverless_client: Any | None = None,
    ) -> None:
        self._config = config
        if opensearch_serverless_client is None:
            opensearch_serverless_client = aws_client(
                "opensearchserverless",
                region=config.region,
                credential=config.credential,
            )
        self._aoss = opensearch_serverless_client

    @property
    def kind(self) -> str:
        return "vector_index" if self._config.collection_type == "VECTORSEARCH" else "search"

    @driver_op(
        cloud="aws",
        driver="opensearch_serverless",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_opensearch_serverless_config"])
        name = self._collection_name(spec)
        handle = handle_for(kind=self.kind, resource_id=name)
        try:
            self._ensure_policies(name, cfg)
        except Exception as exc:
            return ProvisionResult(False, handle, f"reconcile OpenSearch Serverless policies: {exc}", [str(exc)])
        existing = self._describe(name)
        if existing is not None:
            updated = self.update(UpdateSpec(handle=handle, size=spec.size, config=cfg))
            return ProvisionResult(updated.ok, handle, updated.message, updated.errors)
        kwargs: dict[str, Any] = {
            "name": name,
            "type": self._config.collection_type,
            "description": str(cfg.get("description") or f"Astrolift {self.kind} collection for {spec.app_slug}"),
            "standbyReplicas": str(
                cfg.get("standby_replicas", self._config.standby_replicas_default),
            ),
            "deletionProtection": (
                "ENABLED" if cfg.get("deletion_protection", self._config.deletion_protection_default) else "DISABLED"
            ),
            "tags": _aoss_tags(spec),
        }
        if cfg.get("collection_group_name"):
            kwargs["collectionGroupName"] = str(cfg["collection_group_name"])
        if self._config.collection_type == "VECTORSEARCH" and cfg.get("vector_options"):
            kwargs["vectorOptions"] = dict(cfg["vector_options"])
        try:
            self._aoss.create_collection(**kwargs)
        except Exception as exc:
            return ProvisionResult(False, handle, f"create_collection: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"OpenSearch Serverless collection {name} provisioning")

    @driver_op(cloud="aws", driver="opensearch_serverless")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, name = parse_handle(spec.handle)
        cfg = spec.config or {}
        existing = self._describe(name)
        if existing is None:
            return UpdateResult(False, spec.handle, f"OpenSearch collection {name} not found", ["not_found"])
        kwargs: dict[str, Any] = {"id": str(existing["id"])}
        if "description" in cfg:
            kwargs["description"] = str(cfg["description"])
        if "deletion_protection" in cfg:
            kwargs["deletionProtection"] = "ENABLED" if cfg["deletion_protection"] else "DISABLED"
        if self._config.collection_type == "VECTORSEARCH" and "vector_options" in cfg:
            kwargs["vectorOptions"] = dict(cfg["vector_options"])
        try:
            self._ensure_policies(name, cfg)
            if len(kwargs) > 1:
                self._aoss.update_collection(**kwargs)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update OpenSearch Serverless: {exc}", [str(exc)])
        message = "policies reconciled" if len(kwargs) == 1 else "collection update queued"
        return UpdateResult(True, spec.handle, f"OpenSearch Serverless {name}: {message}")

    @driver_op(
        cloud="aws",
        driver="opensearch_serverless",
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
        _, name = parse_handle(spec.handle)
        existing = self._describe(name)
        if existing is not None and not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "OpenSearch Serverless has automatic snapshots but no control-plane export/restore API; "
                "set delete_data=true to acknowledge collection data deletion",
                ["data_retention_not_supported"],
                retryable=False,
            )
        if existing is not None:
            status = str(existing.get("status", ""))
            if status == "DELETING":
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"OpenSearch Serverless collection {name} deletion is still in progress",
                    ["collection_deletion_in_progress"],
                    retryable=True,
                )
            if existing.get("deletionProtection") == "ENABLED":
                if not force_destroy:
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        f"OpenSearch Serverless collection {name} has deletion protection enabled",
                        ["deletion_protection_enabled"],
                        retryable=False,
                    )
                try:
                    self._aoss.update_collection(
                        id=str(existing["id"]),
                        deletionProtection="DISABLED",
                    )
                except Exception as exc:
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        f"disable deletion protection: {exc}",
                        [str(exc)],
                        retryable=_retryable_cloud_error(exc),
                    )
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"OpenSearch Serverless deletion protection disabled for {name}; waiting",
                    ["collection_update_in_progress"],
                    retryable=True,
                )
            try:
                self._aoss.delete_collection(id=str(existing["id"]))
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"delete_collection: {exc}",
                    [str(exc)],
                    retryable=_retryable_cloud_error(exc),
                )
            return DeprovisionResult(
                False,
                spec.handle,
                f"OpenSearch Serverless collection {name} deletion queued",
                ["collection_deletion_in_progress"],
                retryable=True,
            )
        try:
            self._delete_policies(name)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"delete OpenSearch Serverless policies: {exc}",
                [str(exc)],
                retryable=_retryable_cloud_error(exc),
            )
        return DeprovisionResult(True, spec.handle, f"OpenSearch Serverless collection {name} gone")

    @driver_op(cloud="aws", driver="opensearch_serverless")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, name = parse_handle(handle.handle)
        collection = self._describe(name)
        if collection is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"collection {name} not found")
        state = str(collection.get("status", "UNKNOWN"))
        return ServiceStatus(handle.handle, _STATE.get(state, "updating"), f"OpenSearch reports {state}")

    @driver_op(cloud="aws", driver="opensearch_serverless")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, name = parse_handle(handle.handle)
        collection = self._describe(name)
        if collection is None:
            raise ManagedServiceError(f"binding requested for missing OpenSearch collection {name}")
        cfg = config or {}
        endpoint = str(collection.get("collectionEndpoint", ""))
        arn = str(collection.get("arn", ""))
        grants = [Grant(arn, ["aoss:APIAccessAll"])] if arn else []
        if self.kind == "search":
            if arn:
                grants.append(Grant(arn, ["aoss:DashboardsAccessAll"]))
            return Binding(
                env_vars={
                    "SEARCH_ENDPOINT": ValueRef(literal=endpoint),
                    "SEARCH_INDEX_PREFIX": ValueRef(literal=str(cfg.get("index_prefix") or name)),
                },
                iam_grants=grants,
                notes="SigV4-authenticated OpenSearch Serverless search collection",
            )
        return Binding(
            env_vars={
                "VECTOR_ENDPOINT": ValueRef(literal=endpoint),
                "VECTOR_INDEX_NAME": ValueRef(literal=str(cfg.get("index_name") or name)),
                "VECTOR_NAMESPACE": ValueRef(literal=str(cfg.get("namespace") or "default")),
            },
            iam_grants=grants,
            notes="SigV4-authenticated OpenSearch Serverless vector collection",
        )

    @driver_op(cloud="aws", driver="opensearch_serverless")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise ManagedServiceError(
            "OpenSearch Serverless creates automatic snapshots but exposes no control-plane snapshot API",
        )

    @driver_op(cloud="aws", driver="opensearch_serverless")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise ManagedServiceError(
            "OpenSearch Serverless exposes no control-plane snapshot restore API",
        )

    @driver_op(cloud="aws", driver="opensearch_serverless", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "standby_replicas": {"type": "string", "enum": ["ENABLED", "DISABLED"]},
                "deletion_protection": {"type": "boolean"},
                "public_access": {"type": "boolean"},
                "vpc_endpoint_ids": {"type": "array", "items": {"type": "string"}},
                "source_services": {"type": "array", "items": {"type": "string"}},
                "kms_key_arn": {"type": "string"},
                "data_access_principals": {"type": "array", "items": {"type": "string"}},
                "collection_permissions": {"type": "array", "items": {"type": "string"}},
                "index_permissions": {"type": "array", "items": {"type": "string"}},
                "collection_group_name": {"type": "string"},
                "vector_options": {"type": "object"},
                "index_prefix": {"type": "string"},
                "index_name": {"type": "string"},
                "namespace": {"type": "string"},
            },
        }

    @driver_op(cloud="aws", driver="opensearch_serverless", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        if self.kind == "search":
            return BindingSchema(
                env_vars={
                    "SEARCH_ENDPOINT": "OpenSearch Serverless collection endpoint",
                    "SEARCH_INDEX_PREFIX": "Application index prefix",
                }
            )
        return BindingSchema(
            env_vars={
                "VECTOR_ENDPOINT": "OpenSearch Serverless vector collection endpoint",
                "VECTOR_INDEX_NAME": "Default vector index name",
                "VECTOR_NAMESPACE": "Logical application namespace",
            }
        )

    def editable_fields(self) -> list[str]:
        return [
            "description",
            "deletion_protection",
            "public_access",
            "vpc_endpoint_ids",
            "source_services",
            "data_access_principals",
            "collection_permissions",
            "index_permissions",
            "vector_options",
        ]

    def _describe(self, name: str) -> dict[str, Any] | None:
        response = self._aoss.batch_get_collection(names=[name])
        rows = response.get("collectionDetails", [])
        return rows[0] if rows else None

    def _ensure_policies(self, name: str, cfg: dict[str, Any]) -> None:
        encryption: dict[str, Any] = {
            "Rules": [{"ResourceType": "collection", "Resource": [f"collection/{name}"]}],
            "AWSOwnedKey": not bool(cfg.get("kms_key_arn") or self._config.kms_key_arn),
        }
        kms_key = str(cfg.get("kms_key_arn") or self._config.kms_key_arn)
        if kms_key:
            encryption["KmsARN"] = kms_key
        self._ensure_policy("security", "encryption", self._policy_name("e", name), encryption)

        public = bool(cfg.get("public_access", self._config.public_access_default))
        endpoint_ids = list(cfg.get("vpc_endpoint_ids") or self._config.vpc_endpoint_ids)
        source_services = list(cfg.get("source_services") or self._config.source_services)
        rules = [
            {"ResourceType": "collection", "Resource": [f"collection/{name}"]},
            {"ResourceType": "dashboard", "Resource": [f"collection/{name}"]},
        ]
        network: list[dict[str, Any]] = []
        if public:
            network.append({"Rules": rules, "AllowFromPublic": True})
        else:
            if endpoint_ids:
                network.append(
                    {
                        "Rules": rules,
                        "AllowFromPublic": False,
                        "SourceVPCEs": endpoint_ids,
                    }
                )
            elif not source_services:
                raise ValueError("private OpenSearch Serverless collections require a VPC endpoint or source service")
            if source_services:
                network.append(
                    {
                        "Rules": [{"ResourceType": "collection", "Resource": [f"collection/{name}"]}],
                        "AllowFromPublic": False,
                        "SourceServices": source_services,
                    }
                )
        self._ensure_policy("security", "network", self._policy_name("n", name), network)

        principals = list(cfg.get("data_access_principals") or self._config.data_access_principals)
        if not principals and self._config.account_id:
            principals = [f"arn:aws:iam::{self._config.account_id}:root"]
        if not principals:
            raise ValueError("OpenSearch Serverless requires at least one data access principal")
        access = [
            {
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{name}"],
                        "Permission": list(cfg.get("collection_permissions") or ["aoss:*"]),
                    },
                    {
                        "ResourceType": "index",
                        "Resource": [f"index/{name}/*"],
                        "Permission": list(cfg.get("index_permissions") or ["aoss:*"]),
                    },
                ],
                "Principal": principals,
            }
        ]
        self._ensure_policy("access", "data", self._policy_name("d", name), access)

    def _ensure_policy(self, family: str, policy_type: str, name: str, policy: Any) -> None:
        desired = _json(policy)
        getter = self._aoss.get_access_policy if family == "access" else self._aoss.get_security_policy
        creator = self._aoss.create_access_policy if family == "access" else self._aoss.create_security_policy
        updater = self._aoss.update_access_policy if family == "access" else self._aoss.update_security_policy
        detail_key = "accessPolicyDetail" if family == "access" else "securityPolicyDetail"
        try:
            detail = getter(type=policy_type, name=name).get(detail_key) or {}
        except Exception as exc:
            if not _not_found(exc):
                raise
            creator(
                type=policy_type,
                name=name,
                description=f"Astrolift managed {policy_type} policy",
                policy=desired,
            )
            return
        if _json(detail.get("policy")) == desired:
            return
        updater(
            type=policy_type,
            name=name,
            policyVersion=str(detail["policyVersion"]),
            description=f"Astrolift managed {policy_type} policy",
            policy=desired,
        )

    def _delete_policies(self, name: str) -> None:
        for family, policy_type, prefix in (
            ("access", "data", "d"),
            ("security", "network", "n"),
            ("security", "encryption", "e"),
        ):
            policy_name = self._policy_name(prefix, name)
            getter = self._aoss.get_access_policy if family == "access" else self._aoss.get_security_policy
            deleter = self._aoss.delete_access_policy if family == "access" else self._aoss.delete_security_policy
            try:
                getter(type=policy_type, name=policy_name)
            except Exception as exc:
                if _not_found(exc):
                    continue
                raise
            deleter(type=policy_type, name=policy_name)

    def _collection_name(self, spec: ProvisionSpec) -> str:
        return _name(
            self._config.collection_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or self.kind,
        )

    @staticmethod
    def _policy_name(prefix: str, name: str) -> str:
        return _name(prefix, name)

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        if self._config.collection_type not in _COLLECTION_TYPES:
            return f"collection_type must be one of {sorted(_COLLECTION_TYPES)}"
        standby = cfg.get("standby_replicas", self._config.standby_replicas_default)
        if standby not in {"ENABLED", "DISABLED"}:
            return "standby_replicas must be ENABLED or DISABLED"
        for key in ("public_access", "deletion_protection"):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        return ""


class OpenSearchServerlessSearchDriver(OpenSearchServerlessDriver):
    @driver_op(cloud="aws", driver="opensearch_serverless", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "SEARCH_ENDPOINT": "OpenSearch Serverless collection endpoint",
                "SEARCH_INDEX_PREFIX": "Application index prefix",
            }
        )


class OpenSearchServerlessVectorDriver(OpenSearchServerlessDriver):
    @driver_op(cloud="aws", driver="opensearch_serverless", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "VECTOR_ENDPOINT": "OpenSearch Serverless vector collection endpoint",
                "VECTOR_INDEX_NAME": "Default vector index name",
                "VECTOR_NAMESPACE": "Logical application namespace",
            }
        )


def _name(*parts: str) -> str:
    raw = "-".join(str(part).lower() for part in parts if part)
    clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw)
    while "--" in clean:
        clean = clean.replace("--", "-")
    if not clean or not clean[0].isalpha():
        clean = f"a-{clean}"
    clean = clean.strip("-")
    if len(clean) < 3:
        clean = f"{clean}-x"
    return clean[:32].rstrip("-")


def _json(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _aoss_tags(spec: ProvisionSpec) -> list[dict[str, str]]:
    return [{"key": row["Key"], "value": row["Value"]} for row in tags_for(spec)]


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return "notfound" in code.lower() or "notfound" in type(exc).__name__.lower() or "not found" in str(exc).lower()


def _retryable_cloud_error(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
    return (
        status >= 500
        or code.startswith("Throttl")
        or code
        in {
            "ConflictException",
            "InternalServerException",
            "ServiceUnavailableException",
        }
    )
