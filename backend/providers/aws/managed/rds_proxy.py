"""Amazon RDS Proxy managed-service driver."""

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
from aws.managed._base import ManagedServiceError, adoption_refusal, handle_for, parse_handle, tags_for
from aws.session import aws_client

KIND = "database_proxy"
_PORTS = {"POSTGRESQL": 5432, "MYSQL": 3306, "SQLSERVER": 1433}
_STATE = {
    "available": "available",
    "creating": "provisioning",
    "modifying": "updating",
    "deleting": "deprovisioning",
    "incompatible-network": "error",
    "insufficient-resource-limits": "error",
    "reactivating": "updating",
    "suspended": "error",
    "suspending": "updating",
}


@dataclass(frozen=True)
class RDSProxyConfig(CredentialedConfig):
    region: str
    vpc_subnet_ids: list[str] = field(default_factory=list)
    vpc_security_group_ids: list[str] = field(default_factory=list)
    role_arn: str = ""
    proxy_name_prefix: str = "astrolift"
    idle_client_timeout: int = 1800
    require_tls_default: bool = True


class RDSProxyDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: RDSProxyConfig,
        rds_client: Any | None = None,
        iam_client: Any | None = None,
    ) -> None:
        self._config = config
        if rds_client is not None:
            self._rds = rds_client
        else:
            self._rds = aws_client("rds", region=config.region, credential=config.credential)
        if iam_client is not None:
            self._iam = iam_client
        else:
            self._iam = aws_client("iam", region=config.region, credential=config.credential)

    @driver_op(
        cloud="aws",
        driver="rds_proxy",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_proxy_config"])
        proxy_name = self._proxy_name(spec)
        existing = self._describe(proxy_name)
        try:
            refusal = self._target_refusal(cfg, spec.organization_slug)
            if existing is not None and not refusal:
                refusal = adoption_refusal(
                    self._rds.list_tags_for_resource(ResourceName=str(existing.get("DBProxyArn", ""))).get(
                        "TagList", []
                    ),
                    spec,
                    resource=f"RDS Proxy {proxy_name}",
                )
        except Exception as exc:
            return ProvisionResult(False, "", f"describe proxy target: {exc}", [str(exc)])
        if refusal:
            return ProvisionResult(False, "", refusal, ["invalid_proxy_config"])
        if existing is None:
            try:
                auth = self._auth_configs(cfg)
                secret_arns = [str(row["SecretArn"]) for row in auth if row.get("SecretArn")]
                role_arn = str(cfg.get("role_arn") or self._config.role_arn) or self._ensure_role(
                    proxy_name,
                    secret_arns,
                    str(cfg.get("kms_key_arn", "")),
                    [str(value) for value in cfg.get("iam_dbuser_arns", [])],
                )
                kwargs: dict[str, Any] = {
                    "DBProxyName": proxy_name,
                    "EngineFamily": str(cfg["engine_family"]),
                    "RoleArn": role_arn,
                    "VpcSubnetIds": list(self._config.vpc_subnet_ids),
                    "VpcSecurityGroupIds": list(self._config.vpc_security_group_ids),
                    "RequireTLS": bool(cfg.get("require_tls", self._config.require_tls_default)),
                    "IdleClientTimeout": int(
                        cfg.get("idle_client_timeout", self._config.idle_client_timeout),
                    ),
                    "DebugLogging": bool(cfg.get("debug_logging", False)),
                    "Tags": tags_for(spec),
                }
                if auth:
                    kwargs["Auth"] = auth
                if cfg.get("default_auth_scheme"):
                    kwargs["DefaultAuthScheme"] = str(cfg["default_auth_scheme"])
                if cfg.get("endpoint_network_type"):
                    kwargs["EndpointNetworkType"] = str(cfg["endpoint_network_type"])
                if cfg.get("target_connection_network_type"):
                    kwargs["TargetConnectionNetworkType"] = str(
                        cfg["target_connection_network_type"],
                    )
                self._rds.create_db_proxy(**kwargs)
            except Exception as exc:
                return ProvisionResult(False, "", f"create_db_proxy: {exc}", [str(exc)])
        else:
            reconciled = self.update(
                UpdateSpec(
                    handle=handle_for(kind=KIND, resource_id=proxy_name),
                    config=cfg,
                ),
            )
            if not reconciled.ok:
                return ProvisionResult(
                    False,
                    reconciled.handle,
                    reconciled.message,
                    reconciled.errors,
                )
        try:
            self._register_target(proxy_name, cfg)
            self._modify_target_group(proxy_name, cfg)
        except Exception as exc:
            return ProvisionResult(
                False,
                handle_for(kind=KIND, resource_id=proxy_name),
                f"register_db_proxy_targets: {exc}",
                [str(exc)],
            )
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=proxy_name),
            f"RDS Proxy {proxy_name} reconciled",
        )

    @driver_op(cloud="aws", driver="rds_proxy")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, proxy_name = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_update_config(cfg)
        if not error and cfg.get("iam_dbuser_arns"):
            try:
                error = self._dbuser_refusal(cfg["iam_dbuser_arns"], self._registered_target_resource_id(proxy_name))
            except Exception as exc:
                return UpdateResult(False, spec.handle, f"describe proxy target: {exc}", [str(exc)])
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_proxy_config"])
        kwargs: dict[str, Any] = {"DBProxyName": proxy_name}
        for key, aws_key, cast in (
            ("require_tls", "RequireTLS", bool),
            ("idle_client_timeout", "IdleClientTimeout", int),
            ("debug_logging", "DebugLogging", bool),
        ):
            if key in cfg:
                kwargs[aws_key] = cast(cfg[key])
        try:
            proxy: dict[str, Any] | None = None
            auth_keys = {"auth", "secret_arn", "secret_arns", "iam_auth"}
            if auth_keys.intersection(cfg):
                explicit_auth = any(key in cfg for key in ("auth", "secret_arn", "secret_arns"))
                if explicit_auth:
                    auth = self._auth_configs(cfg)
                else:
                    proxy = self._describe(proxy_name)
                    if proxy is None:
                        return UpdateResult(
                            False,
                            spec.handle,
                            f"RDS Proxy {proxy_name} not found",
                            ["not_found"],
                        )
                    auth = self._existing_auth_configs(proxy, str(cfg["iam_auth"]))
                    if not auth:
                        return UpdateResult(
                            False,
                            spec.handle,
                            "iam_auth cannot be updated because the proxy has no secret auth entries",
                            ["auth_not_configured"],
                        )
                if not auth:
                    proxy = proxy or self._describe(proxy_name)
                    effective_default_auth = str(
                        cfg.get("default_auth_scheme") or (proxy or {}).get("DefaultAuthScheme") or "NONE"
                    )
                    if effective_default_auth != "IAM_AUTH":
                        return UpdateResult(
                            False,
                            spec.handle,
                            "at least one secret auth entry is required unless default_auth_scheme=IAM_AUTH",
                            ["auth_not_configured"],
                        )
                kwargs["Auth"] = auth

            if "default_auth_scheme" in cfg:
                kwargs["DefaultAuthScheme"] = str(cfg["default_auth_scheme"])

            if self._needs_family_auth_validation(cfg):
                proxy = proxy or self._describe(proxy_name)
                if proxy is None:
                    return UpdateResult(
                        False,
                        spec.handle,
                        f"RDS Proxy {proxy_name} not found",
                        ["not_found"],
                    )
                family_error = self._validate_family_auth(
                    cfg,
                    str(proxy.get("EngineFamily", "")),
                )
                if family_error:
                    return UpdateResult(
                        False,
                        spec.handle,
                        family_error,
                        ["invalid_proxy_config"],
                    )

            configured_role_arn = str(cfg.get("role_arn") or self._config.role_arn)
            if (
                cfg.get("default_auth_scheme") == "IAM_AUTH"
                and not configured_role_arn
                and not cfg.get("iam_dbuser_arns")
            ):
                return UpdateResult(
                    False,
                    spec.handle,
                    "end-to-end IAM auth requires role_arn or iam_dbuser_arns",
                    ["invalid_proxy_config"],
                )
            if configured_role_arn:
                if "role_arn" in cfg:
                    kwargs["RoleArn"] = configured_role_arn
            elif auth_keys.intersection(cfg) or "default_auth_scheme" in cfg:
                auth_for_policy = list(kwargs.get("Auth") or [])
                kwargs["RoleArn"] = self._ensure_role(
                    proxy_name,
                    [str(row["SecretArn"]) for row in auth_for_policy if row.get("SecretArn")],
                    str(cfg.get("kms_key_arn", "")),
                    [str(value) for value in cfg.get("iam_dbuser_arns", [])],
                )

            if len(kwargs) == 1:
                changed_target_group = self._modify_target_group(proxy_name, cfg)
                message = (
                    "RDS Proxy target group update queued" if changed_target_group else "no RDS Proxy changes requested"
                )
                return UpdateResult(True, spec.handle, message)
            self._rds.modify_db_proxy(**kwargs)
            self._modify_target_group(proxy_name, cfg)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"modify_db_proxy: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"RDS Proxy {proxy_name} update queued")

    @driver_op(
        cloud="aws",
        driver="rds_proxy",
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
        del delete_data, force_destroy
        _, proxy_name = parse_handle(spec.handle)
        existing = self._describe(proxy_name)
        if existing is None:
            try:
                self._delete_managed_role(proxy_name, spec.config or {})
            except Exception as exc:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"delete RDS Proxy IAM role: {exc}",
                    [str(exc)],
                    retryable=_retryable_cloud_error(exc),
                )
            return DeprovisionResult(True, spec.handle, f"RDS Proxy {proxy_name} already gone")
        if existing.get("Status") == "deleting":
            return DeprovisionResult(
                False,
                spec.handle,
                f"RDS Proxy {proxy_name} deletion is still in progress",
                ["proxy_deletion_in_progress"],
                retryable=True,
            )
        try:
            self._rds.delete_db_proxy(DBProxyName=proxy_name)
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                f"delete_db_proxy: {exc}",
                [str(exc)],
                retryable=_retryable_cloud_error(exc),
            )
        return DeprovisionResult(
            False,
            spec.handle,
            f"RDS Proxy {proxy_name} deletion queued; retry to finalize IAM cleanup",
            ["proxy_deletion_in_progress"],
            retryable=True,
        )

    @driver_op(cloud="aws", driver="rds_proxy")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, proxy_name = parse_handle(handle.handle)
        proxy = self._describe(proxy_name)
        if proxy is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"RDS Proxy {proxy_name} not found")
        aws_state = str(proxy.get("Status", "unknown"))
        return ServiceStatus(handle.handle, _STATE.get(aws_state, "updating"), f"RDS Proxy reports {aws_state}")

    @driver_op(cloud="aws", driver="rds_proxy")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, proxy_name = parse_handle(handle.handle)
        proxy = self._describe(proxy_name)
        if proxy is None:
            raise ManagedServiceError(f"binding requested for missing RDS Proxy {proxy_name}")
        engine_family = str(proxy.get("EngineFamily", "POSTGRESQL"))
        host = str(proxy.get("Endpoint", ""))
        arn = str(proxy.get("DBProxyArn", ""))
        cfg = config or {}
        auth_rows = list(proxy.get("Auth") or [])
        secret_arns = list(
            dict.fromkeys(str(auth.get("SecretArn", "")) for auth in auth_rows if auth.get("SecretArn")),
        )
        dbuser_arns = [str(value) for value in cfg.get("iam_dbuser_arns", [])]
        if dbuser_arns:
            # Only users of the database this proxy fronts (#1960).
            try:
                target = self._registered_target_resource_id(proxy_name)
            except Exception:  # no target we can verify, no grant
                target = ""
            dbuser_arns = [arn for arn in dbuser_arns if target and not self._dbuser_refusal([arn], target)]
        default_auth = str(proxy.get("DefaultAuthScheme") or "NONE")
        env_vars = {
            "DATABASE_PROXY_HOST": ValueRef(literal=host),
            "DATABASE_PROXY_PORT": ValueRef(literal=str(_PORTS.get(engine_family, ""))),
            "DATABASE_PROXY_ARN": ValueRef(literal=arn),
            "DATABASE_PROXY_TLS": ValueRef(
                literal="require" if proxy.get("RequireTLS") else "prefer",
            ),
            "DATABASE_PROXY_AUTH_MODE": ValueRef(
                literal="iam" if default_auth == "IAM_AUTH" else "secret",
            ),
        }
        if secret_arns:
            env_vars["DATABASE_PROXY_CREDENTIALS"] = ValueRef(secret_ref=secret_arns[0])
            env_vars["DATABASE_PROXY_CREDENTIALS_REF"] = ValueRef(literal=secret_arns[0])
        if dbuser_arns:
            env_vars["DATABASE_PROXY_DB_USER"] = ValueRef(
                literal=dbuser_arns[0].rsplit("/", 1)[-1],
            )
        grants = [Grant(secret_arn, ["secretsmanager:GetSecretValue"]) for secret_arn in secret_arns]
        grants.extend(Grant(dbuser_arn, ["rds-db:connect"]) for dbuser_arn in dbuser_arns)
        return Binding(
            env_vars=env_vars,
            iam_grants=grants,
            notes=f"RDS Proxy endpoint for {engine_family}",
        )

    @driver_op(cloud="aws", driver="rds_proxy")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError("RDS Proxy is stateless and cannot be snapshotted")

    @driver_op(cloud="aws", driver="rds_proxy")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise ManagedServiceError("RDS Proxy is stateless and cannot be restored")

    @driver_op(cloud="aws", driver="rds_proxy", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["engine_family"],
            "properties": {
                "engine_family": {"type": "string", "enum": sorted(_PORTS)},
                "secret_arn": {
                    "type": "string",
                    "pattern": "^arn:aws(?:-[a-z]+)*:secretsmanager:",
                },
                "role_arn": {
                    "type": "string",
                    "pattern": "^arn:aws(?:-[a-z]+)*:iam:",
                },
                "kms_key_arn": {
                    "type": "string",
                    "pattern": "^arn:aws(?:-[a-z]+)*:kms:",
                },
                "secret_arns": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "uniqueItems": True,
                    "items": {
                        "type": "string",
                        "pattern": "^arn:aws(?:-[a-z]+)*:secretsmanager:",
                    },
                },
                "auth": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "items": {
                        "type": "object",
                        "required": ["secret_arn"],
                        "properties": {
                            "secret_arn": {"type": "string"},
                            "iam_auth": {
                                "type": "string",
                                "enum": ["DISABLED", "REQUIRED", "ENABLED"],
                            },
                            "description": {"type": "string", "maxLength": 1000},
                            "username": {"type": "string", "maxLength": 128},
                            "client_password_auth_type": {
                                "type": "string",
                                "enum": [
                                    "MYSQL_NATIVE_PASSWORD",
                                    "MYSQL_CACHING_SHA2_PASSWORD",
                                    "POSTGRES_SCRAM_SHA_256",
                                    "POSTGRES_MD5",
                                    "SQL_SERVER_AUTHENTICATION",
                                ],
                            },
                        },
                    },
                },
                "default_auth_scheme": {"type": "string", "enum": ["NONE", "IAM_AUTH"]},
                "iam_dbuser_arns": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string", "pattern": "^arn:aws(?:-[a-z]+)*:rds-db:"},
                },
                "db_instance_identifier": {"type": "string"},
                "db_cluster_identifier": {"type": "string"},
                "iam_auth": {"type": "string", "enum": ["DISABLED", "REQUIRED", "ENABLED"]},
                "require_tls": {"type": "boolean"},
                "idle_client_timeout": {"type": "integer", "minimum": 1, "maximum": 28800},
                "debug_logging": {"type": "boolean"},
                "endpoint_network_type": {"type": "string", "enum": ["IPV4", "IPV6", "DUAL"]},
                "target_connection_network_type": {"type": "string", "enum": ["IPV4", "IPV6"]},
                "max_connections_percent": {"type": "integer", "minimum": 1, "maximum": 100},
                "max_idle_connections_percent": {"type": "integer", "minimum": 0, "maximum": 100},
                "connection_borrow_timeout": {"type": "integer", "minimum": 0, "maximum": 3600},
                "session_pinning_filters": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": ["EXCLUDE_VARIABLE_SETS"]},
                },
                "init_query": {"type": "string", "maxLength": 65535},
            },
            "oneOf": [
                {"required": ["db_instance_identifier"]},
                {"required": ["db_cluster_identifier"]},
            ],
        }

    @driver_op(cloud="aws", driver="rds_proxy", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "DATABASE_PROXY_HOST": "RDS Proxy endpoint",
                "DATABASE_PROXY_PORT": "Database protocol port",
                "DATABASE_PROXY_ARN": "RDS Proxy ARN",
                "DATABASE_PROXY_TLS": "TLS mode",
                "DATABASE_PROXY_AUTH_MODE": "secret or IAM authentication",
                "DATABASE_PROXY_CREDENTIALS": "Primary secret credential document",
                "DATABASE_PROXY_CREDENTIALS_REF": "Primary credential secret reference",
                "DATABASE_PROXY_DB_USER": "Database user for IAM authentication",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "require_tls",
            "idle_client_timeout",
            "debug_logging",
            "role_arn",
            "secret_arn",
            "secret_arns",
            "auth",
            "iam_auth",
            "default_auth_scheme",
            "max_connections_percent",
            "max_idle_connections_percent",
            "connection_borrow_timeout",
            "session_pinning_filters",
            "init_query",
        ]

    def _describe(self, proxy_name: str) -> dict[str, Any] | None:
        try:
            rows = self._rds.describe_db_proxies(DBProxyName=proxy_name).get("DBProxies", [])
        except Exception as exc:
            if _not_found(exc, "DBProxyNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _target_db(self, *, instance: str = "", cluster: str = "") -> tuple[str, list[dict[str, str]]]:
        """``(resource id, tags)`` of an RDS instance or cluster."""
        if instance:
            [row] = self._rds.describe_db_instances(DBInstanceIdentifier=instance)["DBInstances"]
            return str(row.get("DbiResourceId", "")), list(row.get("TagList") or [])
        [row] = self._rds.describe_db_clusters(DBClusterIdentifier=cluster)["DBClusters"]
        return str(row.get("DbClusterResourceId", "")), list(row.get("TagList") or [])

    def _target_refusal(self, cfg: dict[str, Any], organization_slug: str) -> str:
        """Why the configured target may not sit behind this org's proxy (#1960).

        The identifiers are tenant-set and resolve in the platform's account,
        so a proxy (and ``rds-db:connect``) could front another org's
        database. The target must carry this org's organization tag, and
        every dbuser ARN must name a user of that target.
        """
        resource_id, tags = self._target_db(
            instance=str(cfg.get("db_instance_identifier") or ""),
            cluster=str(cfg.get("db_cluster_identifier") or ""),
        )
        owner = {str(t.get("Key")): str(t.get("Value")) for t in tags}.get("astrolift.io/organization")
        if owner != organization_slug:
            return "the proxy target database is not tagged as this organization's"
        return self._dbuser_refusal(cfg.get("iam_dbuser_arns") or [], resource_id)

    def _registered_target_resource_id(self, proxy_name: str) -> str:
        """Resource id of the database registered behind ``proxy_name``."""
        targets = self._rds.describe_db_proxy_targets(DBProxyName=proxy_name).get("Targets", [])
        for kind, key in (("TRACKED_CLUSTER", "cluster"), ("RDS_INSTANCE", "instance")):
            for target in targets:
                if target.get("Type") == kind and target.get("RdsResourceId"):
                    return self._target_db(**{key: str(target["RdsResourceId"])})[0]
        return ""

    @staticmethod
    def _dbuser_refusal(dbuser_arns: list[str], resource_id: str) -> str:
        """``arn:...:rds-db:<region>:<account>:dbuser:<resource id>/<user>`` only."""
        for arn in dbuser_arns:
            _, _, rest = str(arn).partition(":dbuser:")
            owner, sep, user = rest.partition("/")
            if not resource_id or owner != resource_id or not sep or not user:
                return f"iam_dbuser_arns may only name users of the proxy's target database: {arn}"
        return ""

    def _register_target(self, proxy_name: str, cfg: dict[str, Any]) -> None:
        kwargs: dict[str, Any] = {"DBProxyName": proxy_name, "TargetGroupName": "default"}
        if cfg.get("db_instance_identifier"):
            kwargs["DBInstanceIdentifiers"] = [str(cfg["db_instance_identifier"])]
        else:
            kwargs["DBClusterIdentifiers"] = [str(cfg["db_cluster_identifier"])]
        try:
            self._rds.register_db_proxy_targets(**kwargs)
        except Exception as exc:
            if not _not_found(exc, "DBProxyTargetAlreadyRegistered"):
                raise

    def _modify_target_group(self, proxy_name: str, cfg: dict[str, Any]) -> bool:
        connection_pool: dict[str, Any] = {}
        for key, aws_key in (
            ("max_connections_percent", "MaxConnectionsPercent"),
            ("max_idle_connections_percent", "MaxIdleConnectionsPercent"),
            ("connection_borrow_timeout", "ConnectionBorrowTimeout"),
            ("session_pinning_filters", "SessionPinningFilters"),
            ("init_query", "InitQuery"),
        ):
            if key in cfg:
                connection_pool[aws_key] = cfg[key]
        if not connection_pool:
            return False
        self._rds.modify_db_proxy_target_group(
            DBProxyName=proxy_name,
            TargetGroupName="default",
            ConnectionPoolConfig=connection_pool,
        )
        return True

    def _ensure_role(
        self,
        proxy_name: str,
        secret_arns: list[str],
        kms_key_arn: str,
        iam_dbuser_arns: list[str],
    ) -> str:
        role_name = self._role_name(proxy_name)
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "rds.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                },
            ],
        }
        try:
            response = self._iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust, separators=(",", ":")),
                Description=f"Astrolift RDS Proxy role for {proxy_name}",
                Tags=[{"Key": "astrolift.io/managed", "Value": "true"}],
            )
            role_arn = str(response["Role"]["Arn"])
        except Exception as exc:
            if "EntityAlreadyExists" not in str(exc) and type(exc).__name__ != "EntityAlreadyExistsException":
                raise
            role_arn = str(self._iam.get_role(RoleName=role_name)["Role"]["Arn"])
        statements = []
        if secret_arns:
            statements.append(
                {
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": secret_arns,
                },
            )
        if kms_key_arn:
            statements.append(
                {
                    "Effect": "Allow",
                    "Action": ["kms:Decrypt"],
                    "Resource": kms_key_arn,
                },
            )
        if iam_dbuser_arns:
            statements.append(
                {
                    "Effect": "Allow",
                    "Action": ["rds-db:connect"],
                    "Resource": iam_dbuser_arns,
                },
            )
        self._iam.put_role_policy(
            RoleName=role_name,
            PolicyName="astrolift-rds-proxy-secret",
            PolicyDocument=json.dumps(
                {"Version": "2012-10-17", "Statement": statements},
                separators=(",", ":"),
            ),
        )
        return role_arn

    def _delete_managed_role(self, proxy_name: str, cfg: dict[str, Any]) -> None:
        if cfg.get("role_arn") or self._config.role_arn:
            return
        role_name = self._role_name(proxy_name)
        try:
            self._iam.delete_role_policy(
                RoleName=role_name,
                PolicyName="astrolift-rds-proxy-secret",
            )
        except Exception as exc:
            if not _not_found(exc, "NoSuchEntity"):
                raise
        try:
            self._iam.delete_role(RoleName=role_name)
        except Exception as exc:
            if not _not_found(exc, "NoSuchEntity"):
                raise

    def _proxy_name(self, spec: ProvisionSpec) -> str:
        raw = "-".join(
            part
            for part in (
                self._config.proxy_name_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "proxy",
            )
            if part
        )
        clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw.lower())
        while "--" in clean:
            clean = clean.replace("--", "-")
        if not clean or not clean[0].isalpha():
            clean = f"p-{clean}"
        return clean.strip("-")[:63]

    @staticmethod
    def _role_name(proxy_name: str) -> str:
        return f"astrolift-rds-proxy-{proxy_name}"[:64].rstrip("-")

    @staticmethod
    def _validate_config(cfg: dict[str, Any]) -> str:
        family = str(cfg.get("engine_family", ""))
        if family not in _PORTS:
            return f"engine_family must be one of {sorted(_PORTS)}"
        error = RDSProxyDriver._validate_update_config(cfg)
        if error:
            return error
        error = RDSProxyDriver._validate_family_auth(cfg, family)
        if error:
            return error
        default_auth = str(cfg.get("default_auth_scheme", "NONE"))
        auth = RDSProxyDriver._auth_configs(cfg)
        if default_auth != "IAM_AUTH" and not auth:
            return "secret_arn, secret_arns, or auth is required unless default_auth_scheme=IAM_AUTH"
        if default_auth == "IAM_AUTH" and not (cfg.get("role_arn") or cfg.get("iam_dbuser_arns")):
            return "end-to-end IAM auth requires role_arn or iam_dbuser_arns"
        targets = bool(cfg.get("db_instance_identifier")) + bool(cfg.get("db_cluster_identifier"))
        if targets != 1:
            return "exactly one db_instance_identifier or db_cluster_identifier is required"
        return ""

    @staticmethod
    def _validate_update_config(cfg: dict[str, Any]) -> str:
        if "default_auth_scheme" in cfg and cfg["default_auth_scheme"] not in (
            "NONE",
            "IAM_AUTH",
        ):
            return "default_auth_scheme must be NONE or IAM_AUTH"
        if "iam_auth" in cfg and cfg["iam_auth"] not in (
            "DISABLED",
            "REQUIRED",
            "ENABLED",
        ):
            return "iam_auth must be DISABLED, REQUIRED, or ENABLED"
        if "secret_arns" in cfg:
            secret_arns = cfg["secret_arns"]
            if not isinstance(secret_arns, list) or not all(isinstance(value, str) and value for value in secret_arns):
                return "secret_arns must be a list of non-empty secret ARNs"
        if "iam_dbuser_arns" in cfg:
            dbuser_arns = cfg["iam_dbuser_arns"]
            if not isinstance(dbuser_arns, list) or not all(isinstance(value, str) and value for value in dbuser_arns):
                return "iam_dbuser_arns must be a list of non-empty rds-db ARNs"
        if "auth" in cfg:
            rows = cfg["auth"]
            if not isinstance(rows, list) or not rows:
                return "auth must be a non-empty list"
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("secret_arn"), str) or not row["secret_arn"]:
                    return "every auth entry requires a non-empty secret_arn"
                if row.get("iam_auth", "DISABLED") not in (
                    "DISABLED",
                    "REQUIRED",
                    "ENABLED",
                ):
                    return "auth[].iam_auth must be DISABLED, REQUIRED, or ENABLED"
        for key, minimum, maximum in (
            ("idle_client_timeout", 1, 28800),
            ("max_connections_percent", 1, 100),
            ("max_idle_connections_percent", 0, 100),
            ("connection_borrow_timeout", 0, 3600),
        ):
            value = cfg.get(key)
            if key in cfg and (
                not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum
            ):
                return f"{key} must be an integer from {minimum} through {maximum}"
        return ""

    @staticmethod
    def _needs_family_auth_validation(cfg: dict[str, Any]) -> bool:
        if cfg.get("default_auth_scheme") == "IAM_AUTH" or cfg.get("iam_auth") == "ENABLED":
            return True
        rows = cfg.get("auth") or []
        return isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("iam_auth") == "ENABLED" for row in rows
        )

    @staticmethod
    def _validate_family_auth(cfg: dict[str, Any], family: str) -> str:
        if cfg.get("iam_auth") == "ENABLED" and family != "SQLSERVER":
            return "iam_auth=ENABLED is valid only for SQLSERVER proxies"
        if cfg.get("default_auth_scheme") == "IAM_AUTH" and family == "SQLSERVER":
            return "default_auth_scheme=IAM_AUTH is valid only for MYSQL and POSTGRESQL proxies"
        rows = cfg.get("auth") or []
        if (
            isinstance(rows, list)
            and any(isinstance(row, dict) and row.get("iam_auth") == "ENABLED" for row in rows)
            and family != "SQLSERVER"
        ):
            return "auth[].iam_auth=ENABLED is valid only for SQLSERVER proxies"
        return ""

    @staticmethod
    def _auth_configs(cfg: dict[str, Any]) -> list[dict[str, Any]]:
        rows = cfg.get("auth")
        if rows:
            out: list[dict[str, Any]] = []
            for row in rows:
                auth: dict[str, Any] = {
                    "AuthScheme": "SECRETS",
                    "SecretArn": str(row["secret_arn"]),
                    "IAMAuth": str(row.get("iam_auth", "DISABLED")),
                    "Description": str(
                        row.get("description", "Astrolift managed database credentials"),
                    ),
                }
                if row.get("username"):
                    auth["UserName"] = str(row["username"])
                if row.get("client_password_auth_type"):
                    auth["ClientPasswordAuthType"] = str(row["client_password_auth_type"])
                out.append(auth)
            return out
        secret_arns = [str(value) for value in cfg.get("secret_arns", [])]
        if cfg.get("secret_arn"):
            secret_arns.insert(0, str(cfg["secret_arn"]))
        return [
            {
                "AuthScheme": "SECRETS",
                "SecretArn": secret_arn,
                "IAMAuth": str(cfg.get("iam_auth", "DISABLED")),
                "Description": "Astrolift managed database credentials",
            }
            for secret_arn in dict.fromkeys(secret_arns)
        ]

    @staticmethod
    def _existing_auth_configs(proxy: dict[str, Any], iam_auth: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in proxy.get("Auth") or []:
            auth = {
                key: row[key]
                for key in (
                    "AuthScheme",
                    "SecretArn",
                    "Description",
                    "UserName",
                    "ClientPasswordAuthType",
                )
                if row.get(key) not in (None, "")
            }
            auth["IAMAuth"] = iam_auth
            out.append(auth)
        return out


def _not_found(exc: Exception, marker: str) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return marker in code or marker in type(exc).__name__ or marker in str(exc)


def _retryable_cloud_error(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error") or {}
    code = str(error.get("Code", ""))
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
    return (
        status >= 500
        or code.startswith("Throttl")
        or code
        in {
            "InternalFailure",
            "InternalServiceError",
            "RequestTimeout",
            "RequestTimeoutException",
            "ServiceUnavailable",
        }
    )
