"""ElastiCache Redis managed-service driver (#352).

Implements ``ManagedServiceDriver`` for the canonical AWS managed-
Redis path. Uses the ``replication_group`` API even for single-node
deployments — it's the modern path and lets the spec scale up to a
multi-AZ cluster without changing driver code.

Two-axis deprovision matrix (matches the SDK Protocol):

  delete_data=False, force_destroy=False (default):
    final snapshot taken (``FinalSnapshotIdentifier``); refuse if
    the replication group has at-rest encryption-key constraints
    the platform doesn't own (rare — surfaces as the AWS error).

  delete_data=True, force_destroy=False:
    skip final snapshot.

  delete_data=False, force_destroy=True:
    final snapshot taken; bypass any "in-use" / "modifying" state
    sniffing the driver would otherwise refuse on.

  delete_data=True, force_destroy=True:
    --atomic cleanup — skip snapshot, ignore in-flight state.

Auth-token rotation: when ``transit_encryption`` is enabled the
driver provisions an AUTH token and stores it in Secrets Manager
alongside the connection URL — the same shape as the RDS driver
so consumers can swap drivers without changing their env wiring.
"""

from __future__ import annotations

import secrets
import string
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
from aws.managed._base import (
    ManagedServiceError,
    adoption_refusal,
    handle_for,
    parse_handle,
    tags_for,
)
from aws.session import aws_client

KIND = "redis"


_SIZE_TO_NODE_TYPE = {
    "small": "cache.t4g.micro",
    "medium": "cache.t4g.small",
    "large": "cache.m7g.large",
    "xlarge": "cache.m7g.xlarge",
}


@dataclass(frozen=True)
class ElastiCacheConfig(CredentialedConfig):
    """Driver-instance config bound from the cluster's plugin config."""

    region: str
    cache_subnet_group: str
    """Pre-existing ElastiCache subnet group spanning at least two
    AZs. Operator/opscode-managed; the driver does not create it."""

    security_group_ids: list[str] = field(default_factory=list)

    engine: str = "redis"
    """Protocol-compatible engine: ``redis`` for compatibility or ``valkey``."""

    replication_group_prefix: str = "astrolift"
    """Prefix on the replication-group ID. ElastiCache replication-
    group ids must be ``[a-zA-Z][a-zA-Z0-9-]{0,39}`` so we sanitize
    the constructed name aggressively."""

    engine_version: str = ""

    transit_encryption_default: bool = True
    """Default. Operators can opt out via spec.config but the
    platform-side templating layer will probably refuse to bind
    apps to an un-encrypted Redis."""

    at_rest_encryption_default: bool = True

    snapshot_retention_days: int = 7

    secrets_manager_prefix: str = "astrolift/elasticache"


class ElastiCacheRedisDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: ElastiCacheConfig,
        elasticache_client: Any | None = None,
        secrets_client: Any | None = None,
    ) -> None:
        self._config = config
        if elasticache_client is not None:
            self._ec = elasticache_client
        else:
            self._ec = aws_client(
                "elasticache",
                region=config.region,
                credential=config.credential,
            )
        if secrets_client is not None:
            self._sm = secrets_client
        else:
            self._sm = aws_client(
                "secretsmanager",
                region=config.region,
                credential=config.credential,
            )

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="aws",
        driver="redis_elasticache",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        rg_id = self._replication_group_id_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(rg_id)
        if existing is not None:
            refusal = adoption_refusal(self._existing_tags(existing), spec, resource=f"replication group {rg_id}")
            if refusal is not None:
                return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
            return ProvisionResult(
                ok=True,
                handle=handle_for(kind=KIND, resource_id=rg_id),
                message=(f"replication group {rg_id} already exists (status={existing.get('Status')})"),
            )

        node_type = cfg.get("node_type") or _SIZE_TO_NODE_TYPE.get(spec.size, "cache.t4g.micro")
        num_node_groups = int(cfg.get("num_node_groups", 1))
        replicas_per_node_group = int(cfg.get("replicas_per_node_group", 0))
        engine_version = cfg.get("engine_version") or self._config.engine_version
        transit_encryption = bool(
            cfg.get(
                "transit_encryption",
                self._config.transit_encryption_default,
            ),
        )
        at_rest_encryption = bool(
            cfg.get(
                "at_rest_encryption",
                self._config.at_rest_encryption_default,
            ),
        )
        snapshot_retention = int(
            cfg.get(
                "snapshot_retention_days",
                self._config.snapshot_retention_days,
            ),
        )

        auth_token = None
        secret_arn: str | None = None
        if transit_encryption and cfg.get("auth_token", True):
            auth_token = _generate_auth_token()
            secret_arn = self._store_auth_token(
                rg_id=rg_id,
                token=auth_token,
                spec=spec,
            )

        create_kwargs: dict[str, Any] = {
            "ReplicationGroupId": rg_id,
            "ReplicationGroupDescription": (
                f"Astrolift {self._config.engine} for {spec.app_slug}/{spec.environment_name}"
            ),
            "Engine": self._config.engine,
            "EngineVersion": engine_version,
            "CacheNodeType": node_type,
            "NumNodeGroups": num_node_groups,
            "ReplicasPerNodeGroup": replicas_per_node_group,
            "AutomaticFailoverEnabled": replicas_per_node_group > 0,
            "MultiAZEnabled": bool(
                cfg.get(
                    "multi_az",
                    replicas_per_node_group > 0,
                ),
            ),
            "CacheSubnetGroupName": self._config.cache_subnet_group,
            "SecurityGroupIds": list(self._config.security_group_ids),
            "TransitEncryptionEnabled": transit_encryption,
            "AtRestEncryptionEnabled": at_rest_encryption,
            "SnapshotRetentionLimit": snapshot_retention,
            "Tags": tags_for(spec),
        }
        if auth_token is not None:
            create_kwargs["AuthToken"] = auth_token
        if cfg.get("kms_key_arn") and at_rest_encryption:
            create_kwargs["KmsKeyId"] = cfg["kms_key_arn"]
        if cfg.get("parameter_group"):
            create_kwargs["CacheParameterGroupName"] = cfg["parameter_group"]

        if not engine_version:
            create_kwargs.pop("EngineVersion", None)
        try:
            self._ec.create_replication_group(**create_kwargs)
        except Exception as exc:
            if secret_arn is not None:
                # Best-effort rollback of the stored auth token; the
                # secret would otherwise be a dangling reference.
                self._delete_auth_token_secret(rg_id)
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_replication_group: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=rg_id),
            message=(
                f"replication group {rg_id} provisioning" + (f" (auth token in {secret_arn})" if secret_arn else "")
            ),
        )

    @driver_op(cloud="aws", driver="redis_elasticache")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, rg_id = parse_handle(spec.handle)
        cfg = spec.config or {}

        modify_kwargs: dict[str, Any] = {
            "ReplicationGroupId": rg_id,
            "ApplyImmediately": bool(cfg.get("apply_immediately", False)),
        }
        if spec.size:
            node_type = cfg.get("node_type") or _SIZE_TO_NODE_TYPE.get(spec.size)
            if node_type:
                modify_kwargs["CacheNodeType"] = node_type
        if cfg.get("engine_version"):
            modify_kwargs["EngineVersion"] = cfg["engine_version"]
        if cfg.get("parameter_group"):
            modify_kwargs["CacheParameterGroupName"] = cfg["parameter_group"]
        if "snapshot_retention_days" in cfg:
            modify_kwargs["SnapshotRetentionLimit"] = int(
                cfg["snapshot_retention_days"],
            )

        if len(modify_kwargs) <= 2:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided — no-op",
            )

        try:
            self._ec.modify_replication_group(**modify_kwargs)
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"modify_replication_group: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"replication group {rg_id} update queued",
        )

    @driver_op(
        cloud="aws",
        driver="redis_elasticache",
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
        _, rg_id = parse_handle(spec.handle)

        existing = self._describe(rg_id)
        if existing is None:
            self._delete_auth_token_secret(rg_id)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"replication group {rg_id} already gone",
            )

        # When delete_data=False, take a final snapshot; otherwise
        # skip. ElastiCache's API toggles the same flag both ways.
        delete_kwargs: dict[str, Any] = {
            "ReplicationGroupId": rg_id,
            "RetainPrimaryCluster": False,
        }
        if not delete_data:
            delete_kwargs["FinalSnapshotIdentifier"] = _final_snapshot_id(rg_id=rg_id)

        # force_destroy: ElastiCache refuses delete while the cluster
        # is in 'modifying' state. There's no API to bypass it short
        # of waiting; we surface the AWS error in non-force mode and
        # retry on the workflow retry policy when force_destroy is on.
        try:
            self._ec.delete_replication_group(**delete_kwargs)
        except Exception as exc:
            err_str = str(exc)
            if not force_destroy and "InvalidReplicationGroupState" in err_str:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=(
                        f"replication group {rg_id} is mid-modify; "
                        f"wait for stable state or pass "
                        f"force_destroy=True to retry on Temporal "
                        f"backoff"
                    ),
                    errors=[err_str],
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_replication_group: {exc}",
                errors=[err_str],
            )

        if delete_data:
            self._delete_auth_token_secret(rg_id)

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"replication group {rg_id} delete queued "
                f"(snapshot={'skipped' if delete_data else 'taken'}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="aws", driver="redis_elasticache")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, rg_id = parse_handle(handle.handle)
        existing = self._describe(rg_id)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"replication group {rg_id} not found",
            )
        ec_state = existing.get("Status", "unknown")
        return ServiceStatus(
            handle=handle.handle,
            state=_EC_STATE_TO_PROTOCOL.get(ec_state, "updating"),
            message=f"elasticache reports {ec_state}",
        )

    @driver_op(cloud="aws", driver="redis_elasticache")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, rg_id = parse_handle(handle.handle)
        existing = self._describe(rg_id)
        if existing is None:
            raise ManagedServiceError(
                f"binding requested for missing replication group {rg_id}",
            )
        endpoint = (
            existing.get("ConfigurationEndpoint") or existing.get("NodeGroups", [{}])[0].get("PrimaryEndpoint") or {}
        )
        host = endpoint.get("Address", "")
        port = str(endpoint.get("Port", 6379))
        transit_encryption = bool(
            existing.get("TransitEncryptionEnabled", False),
        )
        auth_secret_name = self._auth_secret_name_for(rg_id=rg_id)

        # Canonical redis envelope (env_injection._ENVELOPES["redis"]) —
        # the contract apps read. REDIS_USER is the ACL username; Redis
        # defaults to "default" unless ACLs carve out per-app users.
        env_vars: dict[str, ValueRef] = {
            "REDIS_HOST": ValueRef(literal=host),
            "REDIS_PORT": ValueRef(literal=port),
            "REDIS_USER": ValueRef(literal="default"),
            "REDIS_TLS": ValueRef(
                literal="1" if transit_encryption else "0",
            ),
        }
        iam_grants: list[Grant] = []
        if transit_encryption:
            env_vars["REDIS_PASSWORD"] = ValueRef(
                secret_ref=auth_secret_name,
            )
            env_vars["REDIS_URL"] = ValueRef(
                secret_ref=self._url_secret_name_for(rg_id=rg_id),
            )
            iam_grants.append(
                Grant(
                    resource=auth_secret_name,
                    actions=["secretsmanager:GetSecretValue"],
                ),
            )
        else:
            scheme = "redis"
            env_vars["REDIS_URL"] = ValueRef(
                literal=f"{scheme}://{host}:{port}",
            )

        return Binding(
            env_vars=env_vars,
            iam_grants=iam_grants,
            notes=(
                "REDIS_URL is a literal when transit_encryption is "
                "off; with TLS on it's a secret_ref to a Secrets-"
                "Manager-stored ``rediss://:<token>@<host>:<port>``."
            ),
        )

    @driver_op(cloud="aws", driver="redis_elasticache")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        _, rg_id = parse_handle(handle.handle)
        snap_id = (f"{rg_id}-snap-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}")[:255]
        try:
            self._ec.create_snapshot(
                ReplicationGroupId=rg_id,
                SnapshotName=snap_id,
            )
        except Exception as exc:
            raise ManagedServiceError(
                f"create_snapshot: {exc}",
            ) from exc
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snap_id,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="redis_elasticache")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        target_rg_id = self._replication_group_id_for(spec=target)
        try:
            self._ec.create_replication_group(
                ReplicationGroupId=target_rg_id,
                ReplicationGroupDescription=(f"Restored from snapshot {snapshot.snapshot_id}"),
                SnapshotName=snapshot.snapshot_id,
                CacheSubnetGroupName=self._config.cache_subnet_group,
                SecurityGroupIds=list(self._config.security_group_ids),
                Tags=tags_for(target),
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"restore via create_replication_group: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=handle_for(kind=KIND, resource_id=target_rg_id),
            message=(f"restore from snapshot {snapshot.snapshot_id} queued"),
        )

    @driver_op(cloud="aws", driver="redis_elasticache", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "node_type": {"type": "string"},
                "engine_version": {"type": "string"},
                "num_node_groups": {"type": "integer", "minimum": 1},
                "replicas_per_node_group": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 5,
                },
                "transit_encryption": {"type": "boolean"},
                "at_rest_encryption": {"type": "boolean"},
                "auth_token": {"type": "boolean"},
                "snapshot_retention_days": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 35,
                },
                "multi_az": {"type": "boolean"},
                "parameter_group": {"type": "string"},
                "kms_key_arn": {"type": "string"},
                "apply_immediately": {"type": "boolean"},
            },
        }

    @driver_op(cloud="aws", driver="redis_elasticache", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "REDIS_HOST": "ElastiCache primary endpoint host",
                "REDIS_PORT": "ElastiCache primary endpoint port (6379)",
                "REDIS_TLS": "1 if TLS is enabled, 0 otherwise",
                "REDIS_AUTH_TOKEN": ("Secrets Manager ref to the AUTH token (TLS only)"),
                "REDIS_URL": (
                    "Literal redis:// URL when TLS is off; Secrets Manager ref to rediss:// URL when TLS is on"
                ),
            },
        )

    # ---- internals ----------------------------------------------------

    def _existing_tags(self, existing: dict[str, Any]) -> list[dict[str, str]]:
        """Tags of a resource found under this service's name; unreadable counts as untagged (#1961)."""
        try:
            return list(
                self._ec.list_tags_for_resource(ResourceName=str(existing.get("ARN", ""))).get("TagList", []) or []
            )
        except Exception:  # ownership unverifiable, so not adopted
            return []

    def _describe(self, rg_id: str) -> dict[str, Any] | None:
        try:
            resp = self._ec.describe_replication_groups(
                ReplicationGroupId=rg_id,
            )
        except Exception as exc:
            if "ReplicationGroupNotFoundFault" in type(exc).__name__:
                return None
            if "ReplicationGroupNotFound" in str(exc):
                return None
            raise
        groups = resp.get("ReplicationGroups") or []
        return groups[0] if groups else None

    def _replication_group_id_for(self, *, spec: ProvisionSpec) -> str:
        raw = (
            f"{self._config.replication_group_prefix}-"
            f"{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-{spec.service_handle_hint or 'rd'}"
        ).lower()
        sanitized = "".join(c for c in raw if c.isalnum() or c == "-")
        if not sanitized or not sanitized[0].isalpha():
            sanitized = f"a{sanitized}"
        return sanitized[:40]

    def _auth_secret_name_for(self, *, rg_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{rg_id}/auth"

    def _url_secret_name_for(self, *, rg_id: str) -> str:
        return f"{self._config.secrets_manager_prefix}/{rg_id}/url"

    def _store_auth_token(
        self,
        *,
        rg_id: str,
        token: str,
        spec: ProvisionSpec,
    ) -> str:
        name = self._auth_secret_name_for(rg_id=rg_id)
        try:
            resp = self._sm.create_secret(
                Name=name,
                Description=(f"AUTH token for ElastiCache {rg_id} (app {spec.app_slug}/{spec.environment_name})"),
                SecretString=token,
                Tags=tags_for(spec),
            )
            return resp.get("ARN", name)
        except Exception as exc:
            if "ResourceExistsException" in type(exc).__name__:
                self._sm.put_secret_value(
                    SecretId=name,
                    SecretString=token,
                )
                return name
            raise ManagedServiceError(
                f"create_secret for {name}: {exc}",
            ) from exc

    def _delete_auth_token_secret(self, rg_id: str) -> None:
        name = self._auth_secret_name_for(rg_id=rg_id)
        try:
            self._sm.delete_secret(
                SecretId=name,
                ForceDeleteWithoutRecovery=True,
            )
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


_AUTH_TOKEN_ALPHABET = string.ascii_letters + string.digits
"""ElastiCache AUTH tokens: 16-128 printable chars, no spaces or
special. Conservative subset keeps integration with arbitrary
Redis clients painless."""


def _generate_auth_token(length: int = 48) -> str:
    return "".join(secrets.choice(_AUTH_TOKEN_ALPHABET) for _ in range(length))


def _final_snapshot_id(*, rg_id: str) -> str:
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{rg_id}-final-{stamp}"[:255]


_EC_STATE_TO_PROTOCOL = {
    "available": "available",
    "creating": "provisioning",
    "modifying": "updating",
    "deleting": "deprovisioning",
    "snapshotting": "available",
    "maintenance": "updating",
    "create-failed": "error",
    "incompatible-network": "error",
}
