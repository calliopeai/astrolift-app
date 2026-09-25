"""Amazon MemoryDB durable Valkey/Redis-compatible managed-service driver."""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field, replace
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

KIND = "redis"
_ENGINES = {"valkey", "redis"}
_SIZE_TO_NODE_TYPE = {
    "small": "db.t4g.small",
    "medium": "db.t4g.medium",
    "large": "db.r7g.large",
    "xlarge": "db.r7g.xlarge",
}
_STATE = {
    "available": "available",
    "creating": "provisioning",
    "updating": "updating",
    "snapshotting": "updating",
    "deleting": "deprovisioning",
    "create-failed": "error",
}


@dataclass(frozen=True)
class MemoryDBConfig(CredentialedConfig):
    region: str
    subnet_group: str
    security_group_ids: list[str] = field(default_factory=list)
    engine: str = "valkey"
    cluster_name_prefix: str = "astrolift"
    engine_version: str = ""
    snapshot_retention_days: int = 7
    kms_key_arn: str = ""
    tls_enabled_default: bool = True
    secrets_manager_prefix: str = "astrolift/memorydb"
    auth_mode_default: str = "password"


class MemoryDBDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: MemoryDBConfig,
        memorydb_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if memorydb_client is None:
            memorydb_client = aws_client("memorydb", region=config.region, credential=config.credential)
        if secrets_client is None:
            secrets_client = aws_client("secretsmanager", region=config.region, credential=config.credential)
        self._memorydb = memorydb_client
        self._secrets = secrets_client

    @driver_op(
        cloud="aws",
        driver="memorydb",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_memorydb_config"])
        name = self._cluster_name(spec)
        handle = handle_for(kind=KIND, resource_id=name)
        # Ownership before the access resources, which are keyed by name (#1961).
        existing = self._describe_cluster(name)
        if existing is not None:
            refusal = adoption_refusal(
                self._tags_of(str(existing.get("ARN", ""))), spec, resource=f"MemoryDB cluster {name}"
            )
            if refusal is not None:
                return ProvisionResult(False, "", refusal, [refusal])
        try:
            acl_name = self._ensure_access(name, spec, cfg)
        except Exception as exc:
            return ProvisionResult(False, handle, f"create MemoryDB access resources: {exc}", [str(exc)])
        if existing is not None:
            updated = self.update(UpdateSpec(handle=handle, size=spec.size, config=cfg))
            return ProvisionResult(updated.ok, handle, updated.message, updated.errors)

        node_type = str(cfg.get("node_type") or _SIZE_TO_NODE_TYPE.get(spec.size, "db.t4g.small"))
        kwargs: dict[str, Any] = {
            "ClusterName": name,
            "NodeType": node_type,
            "ACLName": acl_name,
            "Engine": self._config.engine,
            "Description": f"Astrolift durable cache for {spec.app_slug}",
            "NumShards": int(cfg.get("num_shards", 1)),
            "NumReplicasPerShard": int(
                cfg.get("num_replicas_per_shard", 1 if spec.isolation == "dedicated" else 0),
            ),
            "SubnetGroupName": self._config.subnet_group,
            "SecurityGroupIds": list(self._config.security_group_ids),
            "TLSEnabled": bool(cfg.get("tls_enabled", self._config.tls_enabled_default)),
            "SnapshotRetentionLimit": int(
                cfg.get("snapshot_retention_days", self._config.snapshot_retention_days),
            ),
            "Tags": tags_for(spec),
        }
        engine_version = str(cfg.get("engine_version") or self._config.engine_version)
        if engine_version:
            kwargs["EngineVersion"] = engine_version
        kms_key = str(cfg.get("kms_key_arn") or self._config.kms_key_arn)
        if kms_key:
            kwargs["KmsKeyId"] = kms_key
        for key, aws_key in (
            ("parameter_group", "ParameterGroupName"),
            ("maintenance_window", "MaintenanceWindow"),
            ("snapshot_window", "SnapshotWindow"),
            ("sns_topic_arn", "SnsTopicArn"),
            ("network_type", "NetworkType"),
            ("ip_discovery", "IpDiscovery"),
        ):
            if cfg.get(key):
                kwargs[aws_key] = cfg[key]
        for key, aws_key in (
            ("auto_minor_version_upgrade", "AutoMinorVersionUpgrade"),
            ("data_tiering", "DataTiering"),
        ):
            if key in cfg:
                kwargs[aws_key] = bool(cfg[key])
        if cfg.get("snapshot_arns_to_restore"):
            kwargs["SnapshotArns"] = list(cfg["snapshot_arns_to_restore"])
        if cfg.get("snapshot_name_to_restore"):
            kwargs["SnapshotName"] = str(cfg["snapshot_name_to_restore"])
        try:
            self._memorydb.create_cluster(**kwargs)
        except Exception as exc:
            return ProvisionResult(False, handle, f"create_cluster: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"MemoryDB cluster {name} provisioning")

    @driver_op(cloud="aws", driver="memorydb")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, name = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_update_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_memorydb_config"])
        kwargs: dict[str, Any] = {"ClusterName": name}
        if spec.size:
            node_type = str(cfg.get("node_type") or _SIZE_TO_NODE_TYPE.get(spec.size, ""))
            if node_type:
                kwargs["NodeType"] = node_type
        for key, aws_key, cast in (
            ("description", "Description", str),
            ("engine_version", "EngineVersion", str),
            ("parameter_group", "ParameterGroupName", str),
            ("maintenance_window", "MaintenanceWindow", str),
            ("snapshot_retention_days", "SnapshotRetentionLimit", int),
            ("snapshot_window", "SnapshotWindow", str),
            ("sns_topic_arn", "SnsTopicArn", str),
            ("ip_discovery", "IpDiscovery", str),
            ("acl_name", "ACLName", str),
        ):
            if key in cfg:
                kwargs[aws_key] = cast(cfg[key])
        if "security_group_ids" in cfg:
            kwargs["SecurityGroupIds"] = list(cfg["security_group_ids"])
        if "num_shards" in cfg:
            kwargs["ShardConfiguration"] = {"ShardCount": int(cfg["num_shards"])}
        if "num_replicas_per_shard" in cfg:
            kwargs["ReplicaConfiguration"] = {
                "ReplicaCount": int(cfg["num_replicas_per_shard"]),
            }
        if len(kwargs) == 1:
            return UpdateResult(True, spec.handle, "no MemoryDB changes requested")
        try:
            self._memorydb.update_cluster(**kwargs)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update_cluster: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"MemoryDB cluster {name} update queued")

    @driver_op(
        cloud="aws",
        driver="memorydb",
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
        cluster = self._describe_cluster(name)
        if cluster is not None:
            if cluster.get("Status") == "deleting":
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"MemoryDB cluster {name} deletion is still in progress",
                    ["cluster_deletion_in_progress"],
                    retryable=True,
                )
            kwargs = {"ClusterName": name}
            if not delete_data:
                kwargs["FinalSnapshotName"] = _snapshot_name(name, "final")
            try:
                self._memorydb.delete_cluster(**kwargs)
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"delete_cluster: {exc}",
                    [str(exc)],
                    retryable=_retryable_cloud_error(exc),
                )
            return DeprovisionResult(
                False,
                spec.handle,
                f"MemoryDB cluster {name} deletion queued",
                ["cluster_deletion_in_progress"],
                retryable=True,
            )
        try:
            cleaned = self._cleanup_access(name, cfg)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"delete MemoryDB access resources: {exc}",
                [str(exc)],
                retryable=_retryable_cloud_error(exc),
            )
        if not cleaned:
            return DeprovisionResult(
                False,
                spec.handle,
                f"MemoryDB access cleanup for {name} is still in progress",
                ["access_cleanup_in_progress"],
                retryable=True,
            )
        return DeprovisionResult(True, spec.handle, f"MemoryDB cluster {name} already gone")

    @driver_op(cloud="aws", driver="memorydb")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, name = parse_handle(handle.handle)
        cluster = self._describe_cluster(name)
        if cluster is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"MemoryDB cluster {name} not found")
        state = str(cluster.get("Status", "unknown"))
        return ServiceStatus(handle.handle, _STATE.get(state, "updating"), f"MemoryDB reports {state}")

    @driver_op(cloud="aws", driver="memorydb")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, name = parse_handle(handle.handle)
        cluster = self._describe_cluster(name)
        if cluster is None:
            raise ManagedServiceError(f"binding requested for missing MemoryDB cluster {name}")
        cfg = config or {}
        endpoint = cluster.get("ClusterEndpoint") or {}
        host = str(endpoint.get("Address", ""))
        port = str(endpoint.get("Port", 6379))
        auth_mode = self._auth_mode(cfg)
        user_name = str(cfg.get("user_name") or self._user_name(name)) if auth_mode not in {"open"} else "default"
        env_vars = {
            "REDIS_HOST": ValueRef(literal=host),
            "REDIS_PORT": ValueRef(literal=port),
            "REDIS_USER": ValueRef(literal=user_name),
            "REDIS_TLS": ValueRef(literal="1" if cluster.get("TLSEnabled", True) else "0"),
            "REDIS_AUTH_MODE": ValueRef(literal=auth_mode),
            "REDIS_RESOURCE_ARN": ValueRef(literal=str(cluster.get("ARN", ""))),
        }
        grants: list[Grant] = []
        scheme = "rediss" if cluster.get("TLSEnabled", True) else "redis"
        if auth_mode == "password":
            password_ref = self._password_secret_name(name)
            self._ensure_url_secret(name, user_name, host, port, scheme)
            env_vars["REDIS_PASSWORD"] = ValueRef(secret_ref=password_ref)
            env_vars["REDIS_URL"] = ValueRef(secret_ref=self._url_secret_name(name))
            grants.extend(
                Grant(ref, ["secretsmanager:GetSecretValue"]) for ref in (password_ref, self._url_secret_name(name))
            )
        elif auth_mode == "external" and cfg.get("password_secret_ref"):
            ref = str(cfg["password_secret_ref"])
            env_vars["REDIS_PASSWORD"] = ValueRef(secret_ref=ref)
            env_vars["REDIS_URL"] = ValueRef(literal=f"{scheme}://{host}:{port}")
            grants.append(Grant(ref, ["secretsmanager:GetSecretValue"]))
        else:
            env_vars["REDIS_URL"] = ValueRef(literal=f"{scheme}://{host}:{port}")
        if auth_mode == "iam":
            user = self._describe_user(user_name) or {}
            grants.extend(
                Grant(resource, ["memorydb:Connect"])
                for resource in (str(cluster.get("ARN", "")), str(user.get("ARN", "")))
                if resource
            )
        return Binding(env_vars=env_vars, iam_grants=grants, notes="MemoryDB durable cache endpoint")

    @driver_op(cloud="aws", driver="memorydb")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        _, name = parse_handle(handle.handle)
        snapshot_name = _snapshot_name(name, "snapshot")
        try:
            response = self._memorydb.create_snapshot(
                ClusterName=name,
                SnapshotName=snapshot_name,
            )
        except Exception as exc:
            raise ManagedServiceError(f"create_snapshot: {exc}") from exc
        snapshot = response.get("Snapshot") or {}
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=str(snapshot.get("ARN") or snapshot_name),
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="memorydb")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        cfg = dict(target.config or {})
        if snapshot.snapshot_id.startswith("arn:"):
            cfg["snapshot_arns_to_restore"] = [snapshot.snapshot_id]
        else:
            cfg["snapshot_name_to_restore"] = snapshot.snapshot_id
        return self.provision(replace(target, config=cfg))

    @driver_op(cloud="aws", driver="memorydb", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "node_type": {"type": "string"},
                "engine_version": {"type": "string"},
                "num_shards": {"type": "integer", "minimum": 1, "maximum": 500},
                "num_replicas_per_shard": {"type": "integer", "minimum": 0, "maximum": 5},
                "auth_mode": {"type": "string", "enum": ["password", "iam", "external", "open"]},
                "allow_open_access": {"type": "boolean"},
                "acl_name": {"type": "string"},
                "user_name": {"type": "string"},
                "password_secret_ref": {"type": "string"},
                "access_string": {"type": "string"},
                "tls_enabled": {"type": "boolean"},
                "kms_key_arn": {"type": "string"},
                "snapshot_retention_days": {"type": "integer", "minimum": 0, "maximum": 35},
                "snapshot_window": {"type": "string"},
                "maintenance_window": {"type": "string"},
                "parameter_group": {"type": "string"},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "sns_topic_arn": {"type": "string"},
                "network_type": {"type": "string", "enum": ["ipv4", "ipv6", "dual_stack"]},
                "ip_discovery": {"type": "string", "enum": ["ipv4", "ipv6"]},
                "data_tiering": {"type": "boolean"},
                "auto_minor_version_upgrade": {"type": "boolean"},
                "description": {"type": "string"},
            },
        }

    @driver_op(cloud="aws", driver="memorydb", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "REDIS_HOST": "MemoryDB cluster endpoint host",
                "REDIS_PORT": "MemoryDB cluster endpoint port",
                "REDIS_USER": "MemoryDB ACL or IAM user",
                "REDIS_PASSWORD": "Password secret when password authentication is selected",
                "REDIS_TLS": "1 when in-transit encryption is enabled",
                "REDIS_URL": "Valkey/Redis connection URL",
                "REDIS_AUTH_MODE": "password, iam, external, or open",
                "REDIS_RESOURCE_ARN": "MemoryDB cluster ARN",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "node_type",
            "engine_version",
            "num_shards",
            "num_replicas_per_shard",
            "description",
            "parameter_group",
            "maintenance_window",
            "snapshot_retention_days",
            "snapshot_window",
            "security_group_ids",
            "sns_topic_arn",
            "ip_discovery",
            "acl_name",
        ]

    def _tags_of(self, arn: str) -> list[dict[str, str]]:
        """Tags of an existing resource; unreadable counts as untagged (#1961)."""
        try:
            resp = self._memorydb.list_tags(ResourceArn=arn)
            return list(resp.get("TagList") or [])
        except Exception:  # ownership unverifiable, so not adopted
            return []

    def _describe_cluster(self, name: str) -> dict[str, Any] | None:
        try:
            rows = self._memorydb.describe_clusters(ClusterName=name).get("Clusters", [])
        except Exception as exc:
            if _not_found(exc, "ClusterNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _ensure_access(self, name: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> str:
        mode = self._auth_mode(cfg)
        if mode == "external":
            return str(cfg["acl_name"])
        if mode == "open":
            return "open-access"
        user_name = str(cfg.get("user_name") or self._user_name(name))
        authentication: dict[str, Any] = {"Type": mode}
        if mode == "password":
            authentication["Passwords"] = [self._ensure_password_secret(name, spec)]
        access_string = str(cfg.get("access_string", "on ~* &* +@all"))
        user = self._describe_user(user_name)
        if user is None:
            self._memorydb.create_user(
                UserName=user_name,
                AccessString=access_string,
                AuthenticationMode=authentication,
                Tags=tags_for(spec),
            )
        else:
            update: dict[str, Any] = {"UserName": user_name}
            live_auth = user.get("Authentication") or user.get("AuthenticationMode") or {}
            if live_auth.get("Type") != mode:
                update["AuthenticationMode"] = authentication
            if user.get("AccessString") != access_string:
                update["AccessString"] = access_string
            if len(update) > 1:
                self._memorydb.update_user(**update)
        acl_name = self._acl_name(name)
        acl = self._describe_acl(acl_name)
        if acl is None:
            self._memorydb.create_acl(
                ACLName=acl_name,
                UserNames=[user_name],
                Tags=tags_for(spec),
            )
        elif user_name not in (acl.get("UserNames") or []):
            self._memorydb.update_acl(ACLName=acl_name, UserNamesToAdd=[user_name])
        return acl_name

    def _cleanup_access(self, name: str, cfg: dict[str, Any]) -> bool:
        if self._auth_mode(cfg) in {"external", "open"}:
            return True
        acl_name = self._acl_name(name)
        acl = self._describe_acl(acl_name)
        if acl is not None:
            if acl.get("Status") != "deleting":
                self._memorydb.delete_acl(ACLName=acl_name)
            return False
        user_name = str(cfg.get("user_name") or self._user_name(name))
        user = self._describe_user(user_name)
        if user is not None:
            if user.get("Status") != "deleting":
                self._memorydb.delete_user(UserName=user_name)
            return False
        self._delete_secret(self._password_secret_name(name))
        self._delete_secret(self._url_secret_name(name))
        return True

    def _describe_user(self, name: str) -> dict[str, Any] | None:
        try:
            rows = self._memorydb.describe_users(UserName=name).get("Users", [])
        except Exception as exc:
            if _not_found(exc, "UserNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _describe_acl(self, name: str) -> dict[str, Any] | None:
        try:
            rows = self._memorydb.describe_acls(ACLName=name).get("ACLs", [])
        except Exception as exc:
            if _not_found(exc, "ACLNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _ensure_password_secret(self, name: str, spec: ProvisionSpec) -> str:
        secret_name = self._password_secret_name(name)
        try:
            return str(self._secrets.get_secret_value(SecretId=secret_name)["SecretString"])
        except Exception as exc:
            if not _not_found(exc, "ResourceNotFound"):
                raise
        password = _password()
        self._secrets.create_secret(
            Name=secret_name,
            SecretString=password,
            Tags=tags_for(spec),
        )
        return password

    def _ensure_url_secret(self, name: str, user: str, host: str, port: str, scheme: str) -> None:
        password = str(
            self._secrets.get_secret_value(SecretId=self._password_secret_name(name))["SecretString"],
        )
        value = f"{scheme}://{user}:{password}@{host}:{port}"
        secret_name = self._url_secret_name(name)
        try:
            self._secrets.create_secret(Name=secret_name, SecretString=value)
        except Exception as exc:
            if not _not_found(exc, "ResourceExists"):
                raise
            self._secrets.put_secret_value(SecretId=secret_name, SecretString=value)

    def _delete_secret(self, name: str) -> None:
        try:
            self._secrets.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
        except Exception as exc:
            if not _not_found(exc, "ResourceNotFound"):
                raise

    def _cluster_name(self, spec: ProvisionSpec) -> str:
        return _name(
            "m",
            self._config.cluster_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "cache",
        )

    @staticmethod
    def _user_name(name: str) -> str:
        return _name("u", name)

    @staticmethod
    def _acl_name(name: str) -> str:
        return _name("a", name)

    def _password_secret_name(self, name: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{name}/password"

    def _url_secret_name(self, name: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{name}/url"

    def _auth_mode(self, cfg: dict[str, Any]) -> str:
        return str(cfg.get("auth_mode", self._config.auth_mode_default))

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        if self._config.engine not in _ENGINES:
            return f"engine must be one of {sorted(_ENGINES)}"
        error = self._validate_update_config(cfg)
        if error:
            return error
        mode = self._auth_mode(cfg)
        if mode not in {"password", "iam", "external", "open"}:
            return "auth_mode must be password, iam, external, or open"
        if mode == "external" and not cfg.get("acl_name"):
            return "auth_mode=external requires acl_name"
        if mode == "external" and not cfg.get("user_name"):
            return "auth_mode=external requires user_name"
        if mode == "open" and cfg.get("allow_open_access") is not True:
            return "auth_mode=open requires allow_open_access=true"
        tls_enabled = bool(cfg.get("tls_enabled", self._config.tls_enabled_default))
        if not tls_enabled and mode != "open":
            return "MemoryDB clusters without TLS must use auth_mode=open"
        if mode == "iam" and cfg.get("engine_version"):
            try:
                major = float(str(cfg["engine_version"]).split(".", 2)[0])
            except (TypeError, ValueError):
                return "engine_version must begin with a numeric version"
            if major < 7:
                return "IAM authentication requires engine version 7 or newer"
        node_type = str(cfg.get("node_type") or "")
        if cfg.get("data_tiering") and not node_type.startswith("db.r6gd."):
            return "data_tiering requires an r6gd node_type"
        for key in ("tls_enabled", "allow_open_access", "data_tiering", "auto_minor_version_upgrade"):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        return ""

    @staticmethod
    def _validate_update_config(cfg: dict[str, Any]) -> str:
        bounds = {
            "num_shards": (1, 500),
            "num_replicas_per_shard": (0, 5),
            "snapshot_retention_days": (0, 35),
        }
        for key, (minimum, maximum) in bounds.items():
            if key not in cfg:
                continue
            value = cfg[key]
            if not isinstance(value, int) or isinstance(value, bool):
                return f"{key} must be an integer"
            if not minimum <= value <= maximum:
                return f"{key} must be from {minimum} through {maximum}"
        return ""


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
    # MemoryDB caps snapshot names at 40 characters. Preserve both the
    # discriminator and timestamp even when the cluster name already fills it.
    tail = f"-{suffix[:5]}-{stamp}"
    return f"{name[: 40 - len(tail)]}{tail}".rstrip("-")


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
            "InvalidClusterStateFault",
        }
    )
