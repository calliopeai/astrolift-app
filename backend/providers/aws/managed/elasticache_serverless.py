"""Amazon ElastiCache Serverless managed-service driver."""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from aws.managed._base import ManagedServiceError, adoption_refusal, handle_for, parse_handle, tags_for
from aws.session import aws_client

_ENGINES = {"valkey", "redis", "memcached"}
_STATE = {
    "available": "available",
    "creating": "provisioning",
    "modifying": "updating",
    "deleting": "deprovisioning",
    "create-failed": "error",
}
_SIZE_LIMITS = {
    "small": (10, 5_000),
    "medium": (50, 20_000),
    "large": (250, 80_000),
    "xlarge": (1_000, 300_000),
}


@dataclass(frozen=True)
class ElastiCacheServerlessConfig(CredentialedConfig):
    region: str
    subnet_ids: list[str] = field(default_factory=list)
    security_group_ids: list[str] = field(default_factory=list)
    engine: str = "valkey"
    cache_name_prefix: str = "astrolift"
    snapshot_retention_days: int = 7
    kms_key_arn: str = ""
    network_type: str = "ipv4"
    secrets_manager_prefix: str = "astrolift/elasticache-serverless"
    auth_mode_default: str = "password"


class ElastiCacheServerlessDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: ElastiCacheServerlessConfig,
        elasticache_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if elasticache_client is None:
            elasticache_client = aws_client("elasticache", region=config.region, credential=config.credential)
        if secrets_client is None:
            secrets_client = aws_client("secretsmanager", region=config.region, credential=config.credential)
        self._ec = elasticache_client
        self._sm = secrets_client

    @property
    def kind(self) -> str:
        return "cache" if self._config.engine == "memcached" else "redis"

    @driver_op(
        cloud="aws",
        driver="elasticache_serverless",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        return self._provision(spec)

    def _provision(self, spec: ProvisionSpec, *, snapshot_arn: str = "") -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_serverless_cache_config"])
        # restore() takes only the snapshot the platform retained for this
        # service's own app; a config naming one itself skipped that check
        # (#2087).
        if "snapshot_arns_to_restore" in cfg:
            return ProvisionResult(
                False,
                "",
                "ElastiCache Serverless config cannot name a snapshot to copy data from "
                "(snapshot_arns_to_restore); restore from a snapshot Astrolift retained for this app instead",
                ["invalid_serverless_cache_config"],
            )
        name = self._cache_name(spec)
        handle = handle_for(kind=self.kind, resource_id=name)
        existing = self._describe(name)
        if existing is not None:
            refusal = adoption_refusal(self._existing_tags(existing), spec, resource=f"serverless cache {name}")
            if refusal is not None:
                return ProvisionResult(False, "", refusal, [refusal])
            reconciled = self.update(UpdateSpec(handle=handle, size=spec.size, config=cfg))
            return ProvisionResult(
                reconciled.ok,
                handle,
                reconciled.message,
                reconciled.errors,
            )

        try:
            user_group_id = self._ensure_access(name, spec, cfg)
            kwargs: dict[str, Any] = {
                "ServerlessCacheName": name,
                "Engine": self._config.engine,
                "Description": f"Astrolift {self._config.engine} cache for {spec.app_slug}",
                "SubnetIds": list(self._config.subnet_ids),
                "SecurityGroupIds": list(self._config.security_group_ids),
                "NetworkType": str(cfg.get("network_type", self._config.network_type)),
                "Tags": tags_for(spec),
            }
            usage_limits = self._usage_limits(spec.size, cfg)
            if usage_limits:
                kwargs["CacheUsageLimits"] = usage_limits
            if cfg.get("major_engine_version"):
                kwargs["MajorEngineVersion"] = str(cfg["major_engine_version"])
            kms_key = str(cfg.get("kms_key_arn") or self._config.kms_key_arn)
            if kms_key:
                kwargs["KmsKeyId"] = kms_key
            if self._config.engine != "memcached":
                kwargs["SnapshotRetentionLimit"] = int(
                    cfg.get("snapshot_retention_days", self._config.snapshot_retention_days),
                )
                if cfg.get("daily_snapshot_time"):
                    kwargs["DailySnapshotTime"] = str(cfg["daily_snapshot_time"])
                if user_group_id:
                    kwargs["UserGroupId"] = user_group_id
            if snapshot_arn:
                kwargs["SnapshotArnsToRestore"] = [snapshot_arn]
            self._ec.create_serverless_cache(**kwargs)
        except Exception as exc:
            return ProvisionResult(False, handle, f"create_serverless_cache: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"ElastiCache Serverless {name} provisioning")

    @driver_op(cloud="aws", driver="elasticache_serverless")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, name = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_update_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_serverless_cache_config"])
        kwargs: dict[str, Any] = {"ServerlessCacheName": name}
        usage_limits = self._usage_limits(spec.size or "", cfg, only_if_requested=True)
        if usage_limits:
            kwargs["CacheUsageLimits"] = usage_limits
        for key, aws_key, cast in (
            ("description", "Description", str),
            ("major_engine_version", "MajorEngineVersion", str),
            ("snapshot_retention_days", "SnapshotRetentionLimit", int),
            ("daily_snapshot_time", "DailySnapshotTime", str),
            ("user_group_id", "UserGroupId", str),
        ):
            if key in cfg:
                kwargs[aws_key] = cast(cfg[key])
        if cfg.get("remove_user_group"):
            kwargs["RemoveUserGroup"] = True
        if "security_group_ids" in cfg:
            kwargs["SecurityGroupIds"] = list(cfg["security_group_ids"])
        if len(kwargs) == 1:
            return UpdateResult(True, spec.handle, "no ElastiCache Serverless changes requested")
        try:
            self._ec.modify_serverless_cache(**kwargs)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"modify_serverless_cache: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"ElastiCache Serverless {name} update queued")

    @driver_op(
        cloud="aws",
        driver="elasticache_serverless",
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
        _, name = parse_handle(spec.handle)
        cfg = spec.config or {}
        existing = self._describe(name)
        if existing is None:
            try:
                cleaned = self._cleanup_access(name, cfg)
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"delete ElastiCache access resources: {exc}",
                    [str(exc)],
                    retryable=_retryable_cloud_error(exc),
                )
            if not cleaned:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"ElastiCache access cleanup for {name} is still in progress",
                    ["access_cleanup_in_progress"],
                    retryable=True,
                )
            return DeprovisionResult(True, spec.handle, f"serverless cache {name} already gone")
        if existing.get("Status") == "deleting":
            return DeprovisionResult(
                False,
                spec.handle,
                f"serverless cache {name} deletion is still in progress",
                ["cache_deletion_in_progress"],
                retryable=True,
            )
        kwargs = {"ServerlessCacheName": name}
        if not delete_data and self._config.engine != "memcached":
            kwargs["FinalSnapshotName"] = _snapshot_name(name, "final")
        try:
            self._ec.delete_serverless_cache(**kwargs)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"delete_serverless_cache: {exc}",
                [str(exc)],
                retryable=_retryable_cloud_error(exc),
            )
        return DeprovisionResult(
            False,
            spec.handle,
            f"serverless cache {name} deletion queued",
            ["cache_deletion_in_progress"],
            retryable=True,
        )

    @driver_op(cloud="aws", driver="elasticache_serverless")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, name = parse_handle(handle.handle)
        cache = self._describe(name)
        if cache is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"serverless cache {name} not found")
        state = str(cache.get("Status", "unknown"))
        return ServiceStatus(handle.handle, _STATE.get(state, "updating"), f"ElastiCache reports {state}")

    @driver_op(cloud="aws", driver="elasticache_serverless")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, name = parse_handle(handle.handle)
        cache = self._describe(name)
        if cache is None:
            raise ManagedServiceError(f"binding requested for missing serverless cache {name}")
        cfg = config or {}
        endpoint = cache.get("Endpoint") or {}
        host = str(endpoint.get("Address", ""))
        port = str(endpoint.get("Port", 11211 if self._config.engine == "memcached" else 6379))
        arn = str(cache.get("ARN", ""))
        if self._config.engine == "memcached":
            # CACHE_NODES is a comma-separated endpoint list across every cache
            # driver, and this driver's own binding_schema already calls it
            # that (#1403). Serverless exposes exactly one endpoint, so the
            # joined string is byte-identical to the bare "host:port" this used
            # to emit -- but a consumer that splits on "," is now right about
            # both this driver and the node-based elasticache_memcached one.
            nodes = [f"{host}:{port}"]
            node_list = ",".join(nodes)
            return Binding(
                env_vars={
                    "CACHE_HOST": ValueRef(literal=host),
                    "CACHE_PORT": ValueRef(literal=port),
                    "CACHE_PROTOCOL": ValueRef(literal="memcached"),
                    "CACHE_NODES": ValueRef(literal=node_list),
                    "CACHE_TLS": ValueRef(literal="1"),
                    "CACHE_RESOURCE_ARN": ValueRef(literal=arn),
                },
                notes="TLS-only ElastiCache Serverless Memcached endpoint",
            )

        auth_mode = self._auth_mode(cfg)
        user_name = str(cfg.get("user_name") or ("default" if self._config.engine == "redis" else "astrolift"))
        env_vars = {
            "REDIS_HOST": ValueRef(literal=host),
            "REDIS_PORT": ValueRef(literal=port),
            "REDIS_USER": ValueRef(literal=user_name),
            "REDIS_TLS": ValueRef(literal="1"),
            "REDIS_AUTH_MODE": ValueRef(literal=auth_mode),
            "REDIS_RESOURCE_ARN": ValueRef(literal=arn),
        }
        grants: list[Grant] = []
        if auth_mode == "password":
            password_ref = self._password_secret_name(name)
            self._ensure_url_secret(name, user_name, host, port)
            env_vars["REDIS_PASSWORD"] = ValueRef(secret_ref=password_ref)
            env_vars["REDIS_URL"] = ValueRef(secret_ref=self._url_secret_name(name))
            grants.extend(
                Grant(ref, ["secretsmanager:GetSecretValue"]) for ref in (password_ref, self._url_secret_name(name))
            )
        elif auth_mode == "external" and cfg.get("password_secret_ref"):
            password_ref = str(cfg["password_secret_ref"])
            env_vars["REDIS_PASSWORD"] = ValueRef(secret_ref=password_ref)
            env_vars["REDIS_URL"] = ValueRef(literal=f"rediss://{host}:{port}")
            grants.append(Grant(password_ref, ["secretsmanager:GetSecretValue"]))
        else:
            env_vars["REDIS_URL"] = ValueRef(literal=f"rediss://{host}:{port}")
        if auth_mode == "iam":
            user = self._describe_user(self._user_id(name)) or {}
            user_arn = str(user.get("ARN", ""))
            grants.extend(Grant(resource, ["elasticache:Connect"]) for resource in (arn, user_arn) if resource)
        return Binding(env_vars=env_vars, iam_grants=grants, notes="ElastiCache Serverless endpoint")

    @driver_op(cloud="aws", driver="elasticache_serverless")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        if self._config.engine == "memcached":
            raise ManagedServiceError("ElastiCache Memcached does not support snapshots")
        _, name = parse_handle(handle.handle)
        snapshot_name = _snapshot_name(name, "snapshot")
        try:
            response = self._ec.create_serverless_cache_snapshot(
                ServerlessCacheName=name,
                ServerlessCacheSnapshotName=snapshot_name,
            )
        except Exception as exc:
            raise ManagedServiceError(f"create_serverless_cache_snapshot: {exc}") from exc
        snapshot = response.get("ServerlessCacheSnapshot") or {}
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=str(snapshot.get("ARN") or snapshot_name),
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="elasticache_serverless")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        if self._config.engine == "memcached":
            raise ManagedServiceError("ElastiCache Memcached does not support snapshots")
        return self._provision(target, snapshot_arn=snapshot.snapshot_id)

    @driver_op(cloud="aws", driver="elasticache_serverless", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "major_engine_version": {"type": "string"},
                "data_storage_min_gb": {"type": "integer", "minimum": 1, "maximum": 5000},
                "data_storage_max_gb": {"type": "integer", "minimum": 1, "maximum": 5000},
                "ecpu_min": {"type": "integer", "minimum": 1000},
                "ecpu_max": {"type": "integer", "minimum": 1000},
                "snapshot_retention_days": {"type": "integer", "minimum": 0, "maximum": 35},
                "daily_snapshot_time": {"type": "string"},
                "kms_key_arn": {"type": "string"},
                "network_type": {"type": "string", "enum": ["ipv4", "ipv6", "dual_stack"]},
                "auth_mode": {"type": "string", "enum": ["password", "iam", "external", "none"]},
                "user_group_id": {"type": "string"},
                "user_name": {"type": "string"},
                "password_secret_ref": {"type": "string"},
                "access_string": {"type": "string"},
                "description": {"type": "string"},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "remove_user_group": {"type": "boolean"},
            },
        }

    @driver_op(cloud="aws", driver="elasticache_serverless", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "REDIS_HOST": "Valkey/Redis-compatible endpoint host",
                "REDIS_PORT": "Valkey/Redis-compatible endpoint port",
                "REDIS_USER": "ACL or IAM-authenticated user",
                "REDIS_PASSWORD": "Password secret when password authentication is selected",
                "REDIS_TLS": "TLS is always required for serverless caches",
                "REDIS_URL": "TLS connection URL",
                "REDIS_AUTH_MODE": "password, iam, external, or none",
                "REDIS_RESOURCE_ARN": "Serverless cache ARN",
                "CACHE_HOST": "Memcached endpoint host",
                "CACHE_PORT": "Memcached endpoint port",
                "CACHE_PROTOCOL": "memcached",
                "CACHE_NODES": "Comma-separated cache endpoints",
                "CACHE_TLS": "TLS is always required for serverless caches",
                "CACHE_RESOURCE_ARN": "Serverless cache ARN",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "description",
            "major_engine_version",
            "data_storage_min_gb",
            "data_storage_max_gb",
            "ecpu_min",
            "ecpu_max",
            "snapshot_retention_days",
            "daily_snapshot_time",
            "security_group_ids",
            "user_group_id",
            "remove_user_group",
        ]

    def _existing_tags(self, existing: dict[str, Any]) -> list[dict[str, str]]:
        """Tags of a cache found under this service's name; unreadable counts as untagged (#1961)."""
        try:
            resp = self._ec.list_tags_for_resource(ResourceName=str(existing.get("ARN", "")))
            return list(resp.get("TagList") or [])
        except Exception:  # ownership unverifiable, so not adopted
            return []

    def _describe(self, name: str) -> dict[str, Any] | None:
        try:
            rows = self._ec.describe_serverless_caches(ServerlessCacheName=name).get(
                "ServerlessCaches",
                [],
            )
        except Exception as exc:
            if _not_found(exc, "ServerlessCacheNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _ensure_access(self, name: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        if self._config.engine == "memcached":
            return ""
        mode = self._auth_mode(cfg)
        if mode == "external":
            return str(cfg["user_group_id"])
        if mode == "none":
            return ""
        user_id = self._user_id(name)
        user_name = str(cfg.get("user_name") or ("default" if self._config.engine == "redis" else "astrolift"))
        if self._describe_user(user_id) is None:
            authentication: dict[str, Any] = {"Type": mode}
            if mode == "password":
                authentication["Passwords"] = [self._ensure_password_secret(name, spec)]
            self._ec.create_user(
                UserId=user_id,
                UserName=user_name,
                Engine=self._config.engine,
                AccessString=str(cfg.get("access_string", "on ~* +@all")),
                AuthenticationMode=authentication,
                Tags=tags_for(spec),
            )
        group_id = self._group_id(name)
        if self._describe_group(group_id) is None:
            self._ec.create_user_group(
                UserGroupId=group_id,
                Engine=self._config.engine,
                UserIds=[user_id],
                Tags=tags_for(spec),
            )
        return group_id

    def _cleanup_access(self, name: str, cfg: dict[str, Any]) -> bool:
        if self._config.engine == "memcached" or self._auth_mode(cfg) in ("none", "external"):
            return True
        group_id = self._group_id(name)
        group = self._describe_group(group_id)
        if group is not None:
            if group.get("Status") != "deleting":
                self._ec.delete_user_group(UserGroupId=group_id)
            return False
        user_id = self._user_id(name)
        user = self._describe_user(user_id)
        if user is not None:
            if user.get("Status") != "deleting":
                self._ec.delete_user(UserId=user_id)
            return False
        self._delete_secret(self._password_secret_name(name))
        self._delete_secret(self._url_secret_name(name))
        return True

    def _describe_user(self, user_id: str) -> dict[str, Any] | None:
        try:
            rows = self._ec.describe_users(UserId=user_id).get("Users", [])
        except Exception as exc:
            if _not_found(exc, "UserNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _describe_group(self, group_id: str) -> dict[str, Any] | None:
        try:
            rows = self._ec.describe_user_groups(UserGroupId=group_id).get("UserGroups", [])
        except Exception as exc:
            if _not_found(exc, "UserGroupNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _ensure_password_secret(self, name: str, spec: ProvisionSpec) -> str:
        secret_name = self._password_secret_name(name)
        try:
            return str(self._sm.get_secret_value(SecretId=secret_name)["SecretString"])
        except Exception as exc:
            if not _not_found(exc, "ResourceNotFound"):
                raise
        password = _password()
        self._sm.create_secret(
            Name=secret_name,
            SecretString=password,
            Tags=tags_for(spec),
        )
        return password

    def _ensure_url_secret(self, name: str, user_name: str, host: str, port: str) -> None:
        password = str(self._sm.get_secret_value(SecretId=self._password_secret_name(name))["SecretString"])
        url = f"rediss://{user_name}:{password}@{host}:{port}"
        secret_name = self._url_secret_name(name)
        try:
            self._sm.create_secret(Name=secret_name, SecretString=url)
        except Exception as exc:
            if not _not_found(exc, "ResourceExists"):
                raise
            self._sm.put_secret_value(SecretId=secret_name, SecretString=url)

    def _delete_secret(self, secret_name: str) -> None:
        try:
            self._sm.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
        except Exception as exc:
            if not _not_found(exc, "ResourceNotFound"):
                raise

    def _cache_name(self, spec: ProvisionSpec) -> str:
        return _name(
            self._config.cache_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "cache",
        )

    @staticmethod
    def _user_id(name: str) -> str:
        # Prefix the discriminator so truncation at ElastiCache's 40-char
        # identifier limit cannot collapse the user and group to one ID.
        return _name("u", name)

    @staticmethod
    def _group_id(name: str) -> str:
        return _name("g", name)

    def _password_secret_name(self, name: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{name}/password"

    def _url_secret_name(self, name: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{name}/url"

    def _auth_mode(self, cfg: dict[str, Any]) -> str:
        if self._config.engine == "memcached":
            return "none"
        return str(cfg.get("auth_mode", self._config.auth_mode_default))

    @staticmethod
    def _usage_limits(
        size: str,
        cfg: dict[str, Any],
        *,
        only_if_requested: bool = False,
    ) -> dict[str, Any]:
        requested = any(key in cfg for key in ("data_storage_min_gb", "data_storage_max_gb", "ecpu_min", "ecpu_max"))
        defaults = _SIZE_LIMITS.get(size)
        if only_if_requested and not requested and defaults is None:
            return {}
        data_max, ecpu_max = defaults or (None, None)
        data: dict[str, Any] = {"Unit": "GB"}
        ecpu: dict[str, Any] = {}
        if cfg.get("data_storage_min_gb") is not None:
            data["Minimum"] = int(cfg["data_storage_min_gb"])
        if cfg.get("data_storage_max_gb") is not None or data_max is not None:
            data["Maximum"] = int(cfg.get("data_storage_max_gb", data_max))
        if cfg.get("ecpu_min") is not None:
            ecpu["Minimum"] = int(cfg["ecpu_min"])
        if cfg.get("ecpu_max") is not None or ecpu_max is not None:
            ecpu["Maximum"] = int(cfg.get("ecpu_max", ecpu_max))
        out: dict[str, Any] = {}
        if len(data) > 1:
            out["DataStorage"] = data
        if ecpu:
            out["ECPUPerSecond"] = ecpu
        return out

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        if self._config.engine not in _ENGINES:
            return f"engine must be one of {sorted(_ENGINES)}"
        error = self._validate_update_config(cfg)
        if error:
            return error
        mode = self._auth_mode(cfg)
        if self._config.engine == "memcached" and mode != "none":
            return "Memcached does not support Valkey/Redis user-group authentication"
        if self._config.engine == "memcached" and cfg.get("snapshot_arns_to_restore"):
            return "Memcached does not support snapshot restore"
        if mode == "external" and not cfg.get("user_group_id"):
            return "auth_mode=external requires user_group_id"
        if mode == "iam" and cfg.get("major_engine_version"):
            try:
                major_version = float(cfg["major_engine_version"])
            except (TypeError, ValueError):
                return "major_engine_version must begin with a numeric version"
            if major_version < 7:
                return "IAM authentication requires engine version 7 or newer"
        return ""

    @staticmethod
    def _validate_update_config(cfg: dict[str, Any]) -> str:
        if cfg.get("auth_mode", "password") not in ("password", "iam", "external", "none"):
            return "auth_mode must be password, iam, external, or none"
        limits = {
            "data_storage_min_gb": (1, 5_000),
            "data_storage_max_gb": (1, 5_000),
            "ecpu_min": (1_000, None),
            "ecpu_max": (1_000, None),
            "snapshot_retention_days": (0, 35),
        }
        for key, (minimum, maximum) in limits.items():
            if key not in cfg:
                continue
            value = cfg[key]
            if not isinstance(value, int) or isinstance(value, bool):
                return f"{key} must be an integer"
            if value < minimum or (maximum is not None and value > maximum):
                high = f" through {maximum}" if maximum is not None else " or greater"
                return f"{key} must be {minimum}{high}"
        for low, high in (
            ("data_storage_min_gb", "data_storage_max_gb"),
            ("ecpu_min", "ecpu_max"),
        ):
            if low in cfg and high in cfg and cfg[low] > cfg[high]:
                return f"{low} cannot exceed {high}"
        return ""


class ElastiCacheServerlessRedisDriver(ElastiCacheServerlessDriver):
    @driver_op(cloud="aws", driver="elasticache_serverless", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "REDIS_HOST": "Valkey/Redis-compatible endpoint host",
                "REDIS_PORT": "Valkey/Redis-compatible endpoint port",
                "REDIS_USER": "ACL or IAM-authenticated user",
                "REDIS_PASSWORD": "Password secret when password authentication is selected",
                "REDIS_TLS": "TLS is always required for serverless caches",
                "REDIS_URL": "TLS connection URL",
                "REDIS_AUTH_MODE": "password, iam, external, or none",
                "REDIS_RESOURCE_ARN": "Serverless cache ARN",
            },
        )


class ElastiCacheServerlessMemcachedDriver(ElastiCacheServerlessDriver):
    @driver_op(cloud="aws", driver="elasticache_serverless", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "CACHE_HOST": "Memcached endpoint host",
                "CACHE_PORT": "Memcached endpoint port",
                "CACHE_PROTOCOL": "memcached",
                "CACHE_NODES": "Comma-separated cache endpoints",
                "CACHE_TLS": "TLS is always required for serverless caches",
                "CACHE_RESOURCE_ARN": "Serverless cache ARN",
            },
        )


def _name(*parts: str) -> str:
    raw = "-".join(str(part).lower() for part in parts if part)
    clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw)
    while "--" in clean:
        clean = clean.replace("--", "-")
    if not clean or not clean[0].isalpha():
        clean = f"a-{clean}"
    return clean.strip("-")[:40].rstrip("-")


def _password(length: int = 48) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _snapshot_name(name: str, suffix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{name}-{suffix}-{stamp}"[:255].rstrip("-")


def _not_found(exc: Exception, marker: str) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return marker in code or marker in type(exc).__name__ or marker in str(exc)


def _retryable_cloud_error(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
    return (
        status >= 500
        or code.startswith("Throttl")
        or code
        in {
            "InternalFailure",
            "ServiceUnavailable",
            "RequestTimeout",
        }
    )
