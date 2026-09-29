"""Amazon Redshift Serverless namespace and workgroup driver."""

from __future__ import annotations

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
from aws.managed.redshift import _data_api_grants
from aws.session import aws_client

KIND = "warehouse"
_SIZE_TO_RPU = {"small": 8, "medium": 32, "large": 128, "xlarge": 256}
_STATE = {
    "AVAILABLE": "available",
    "CREATING": "provisioning",
    "DELETING": "deprovisioning",
    "MODIFYING": "updating",
}


@dataclass(frozen=True)
class RedshiftServerlessConfig(CredentialedConfig):
    region: str
    account_id: str
    subnet_ids: list[str] = field(default_factory=list)
    security_group_ids: list[str] = field(default_factory=list)
    name_prefix: str = "astrolift"
    base_capacity_default: int = 8
    snapshot_retention_days: int = 30
    deletion_protection_default: bool = True
    manage_admin_password_default: bool = True
    admin_username: str = "astrolift"


class RedshiftServerlessDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: RedshiftServerlessConfig,
        serverless_client: Any | None = None,
    ) -> None:
        self._config = config
        if serverless_client is None:
            serverless_client = aws_client("redshift-serverless", region=config.region, credential=config.credential)
        self._serverless = serverless_client

    @driver_op(
        cloud="aws",
        driver="redshift_serverless",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_redshift_serverless_config"])
        resource_id = self._resource_id(spec)
        handle = handle_for(kind=KIND, resource_id=resource_id)
        namespace = self._get_namespace(resource_id)
        workgroup = self._get_workgroup(resource_id)
        for existing, arn_key, label in (
            (namespace, "namespaceArn", f"Redshift Serverless namespace {resource_id}"),
            (workgroup, "workgroupArn", f"Redshift Serverless workgroup {resource_id}"),
        ):
            if existing is not None:
                refusal = adoption_refusal(self._tags_of(str(existing.get(arn_key, ""))), spec, resource=label)
                if refusal is not None:
                    return ProvisionResult(False, "", refusal, [refusal])
        try:
            if namespace is None:
                self._create_namespace(resource_id, spec, cfg)
            if workgroup is None:
                self._create_workgroup(resource_id, spec, cfg)
            if namespace is not None and workgroup is not None:
                updated = self.update(UpdateSpec(handle=handle, size=spec.size, config=cfg))
                return ProvisionResult(updated.ok, handle, updated.message, updated.errors)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Redshift Serverless: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Redshift Serverless namespace and workgroup {resource_id} provisioning",
        )

    @driver_op(cloud="aws", driver="redshift_serverless")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, resource_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        namespace = self._get_namespace(resource_id)
        workgroup = self._get_workgroup(resource_id)
        if namespace is None or workgroup is None:
            return UpdateResult(
                False,
                spec.handle,
                f"Redshift Serverless resource {resource_id} not found",
                ["not_found"],
            )
        validation_cfg = dict(cfg)
        validation_cfg.setdefault("base_capacity", workgroup.get("baseCapacity", 8))
        if workgroup.get("maxCapacity") is not None:
            validation_cfg.setdefault("max_capacity", workgroup["maxCapacity"])
        error = self._validate_config(validation_cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_redshift_serverless_config"])
        namespace_kwargs: dict[str, Any] = {"namespaceName": resource_id}
        for key, aws_key, cast in (
            ("admin_username", "adminUsername", str),
            ("manage_admin_password", "manageAdminPassword", bool),
            ("admin_password_secret_kms_key_id", "adminPasswordSecretKmsKeyId", str),
            ("kms_key_id", "kmsKeyId", str),
            ("default_iam_role_arn", "defaultIamRoleArn", str),
        ):
            if key in cfg:
                namespace_kwargs[aws_key] = cast(cfg[key])
        if "iam_roles" in cfg:
            namespace_kwargs["iamRoles"] = list(cfg["iam_roles"])
        if "log_exports" in cfg:
            namespace_kwargs["logExports"] = list(cfg["log_exports"])
        workgroup_kwargs: dict[str, Any] = {"workgroupName": resource_id}
        for key, aws_key, cast in (
            ("base_capacity", "baseCapacity", int),
            ("max_capacity", "maxCapacity", int),
            ("enhanced_vpc_routing", "enhancedVpcRouting", bool),
            ("extra_compute_for_automatic_optimization", "extraComputeForAutomaticOptimization", bool),
            ("ip_address_type", "ipAddressType", str),
            ("port", "port", int),
            ("publicly_accessible", "publiclyAccessible", bool),
            ("track_name", "trackName", str),
        ):
            if key in cfg:
                workgroup_kwargs[aws_key] = cast(cfg[key])
        if "security_group_ids" in cfg:
            workgroup_kwargs["securityGroupIds"] = list(cfg["security_group_ids"])
        if "subnet_ids" in cfg:
            workgroup_kwargs["subnetIds"] = list(cfg["subnet_ids"])
        if "config_parameters" in cfg:
            workgroup_kwargs["configParameters"] = _config_parameters(cfg["config_parameters"])
        if "price_performance_target" in cfg:
            workgroup_kwargs["pricePerformanceTarget"] = dict(cfg["price_performance_target"])
        if spec.size:
            workgroup_kwargs.setdefault(
                "baseCapacity",
                _SIZE_TO_RPU.get(spec.size, self._config.base_capacity_default),
            )
        changed = False
        try:
            if len(namespace_kwargs) > 1:
                self._serverless.update_namespace(**namespace_kwargs)
                changed = True
            if len(workgroup_kwargs) > 1:
                self._serverless.update_workgroup(**workgroup_kwargs)
                changed = True
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"modify Redshift Serverless: {exc}", [str(exc)])
        return UpdateResult(
            True,
            spec.handle,
            f"Redshift Serverless {resource_id} update queued"
            if changed
            else "no Redshift Serverless changes requested",
        )

    @driver_op(
        cloud="aws",
        driver="redshift_serverless",
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
        _, resource_id = parse_handle(spec.handle)
        namespace = self._get_namespace(resource_id)
        workgroup = self._get_workgroup(resource_id)
        if namespace is None and workgroup is None:
            return DeprovisionResult(
                True,
                spec.handle,
                f"Redshift Serverless resource {resource_id} already gone",
            )
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
                f"Redshift Serverless {resource_id} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if workgroup is not None:
            if workgroup.get("status") != "DELETING":
                try:
                    self._serverless.delete_workgroup(workgroupName=resource_id)
                except Exception as exc:
                    return _deprovision_error(spec.handle, "delete Redshift Serverless workgroup", exc)
            return DeprovisionResult(
                False,
                spec.handle,
                f"Redshift Serverless workgroup {resource_id} is deleting",
                ["workgroup_deletion_in_progress"],
            )
        if namespace is not None:
            if namespace.get("status") == "DELETING":
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Redshift Serverless namespace {resource_id} is deleting",
                    ["namespace_deletion_in_progress"],
                )
            kwargs: dict[str, Any] = {"namespaceName": resource_id}
            if not delete_data:
                kwargs["finalSnapshotName"] = _snapshot_name(resource_id, "final")
                kwargs["finalSnapshotRetentionPeriod"] = int(
                    spec.config.get(
                        "snapshot_retention_days",
                        self._config.snapshot_retention_days,
                    ),
                )
            try:
                self._serverless.delete_namespace(**kwargs)
            except Exception as exc:
                return _deprovision_error(spec.handle, "delete Redshift Serverless namespace", exc)
            return DeprovisionResult(
                False,
                spec.handle,
                f"Redshift Serverless namespace {resource_id} deletion queued",
                ["namespace_deletion_in_progress"],
            )
        return DeprovisionResult(True, spec.handle, f"Redshift Serverless {resource_id} gone")

    @driver_op(cloud="aws", driver="redshift_serverless")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, resource_id = parse_handle(handle.handle)
        namespace = self._get_namespace(resource_id)
        workgroup = self._get_workgroup(resource_id)
        if namespace is None and workgroup is None:
            return ServiceStatus(
                handle.handle,
                "deprovisioned",
                f"Redshift Serverless {resource_id} not found",
            )
        states = [str(row.get("status") or "UNKNOWN") for row in (namespace, workgroup) if row is not None]
        if len(states) == 2 and all(state == "AVAILABLE" for state in states):
            return ServiceStatus(handle.handle, "available", "namespace and workgroup are available")
        state = "error" if "UNKNOWN" in states else _STATE.get(states[-1], "updating")
        return ServiceStatus(
            handle.handle,
            state,
            f"Redshift Serverless reports namespace={states[0] if states else 'missing'} "
            f"workgroup={states[1] if len(states) > 1 else 'missing'}",
        )

    @driver_op(cloud="aws", driver="redshift_serverless")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, resource_id = parse_handle(handle.handle)
        namespace = self._get_namespace(resource_id)
        workgroup = self._get_workgroup(resource_id)
        if namespace is None or workgroup is None:
            raise ManagedServiceError(
                f"binding requested for missing Redshift Serverless resource {resource_id}",
            )
        cfg = config or {}
        endpoint = workgroup.get("endpoint") or {}
        host = str(endpoint.get("address") or "")
        port = int(endpoint.get("port") or workgroup.get("port") or 5439)
        database = str(namespace.get("dbName") or cfg.get("database") or "dev")
        workgroup_arn = str(workgroup.get("workgroupArn") or "")
        env_vars = {
            "WAREHOUSE_ENDPOINT": ValueRef(literal=host),
            "WAREHOUSE_PORT": ValueRef(literal=str(port)),
            "WAREHOUSE_DATABASE": ValueRef(literal=database),
            "WAREHOUSE_URL": ValueRef(literal=f"postgresql://{host}:{port}/{database}"),
            "WAREHOUSE_ENGINE": ValueRef(literal="redshift"),
            "WAREHOUSE_DEPLOYMENT": ValueRef(literal="serverless"),
            "WAREHOUSE_AUTH_MODE": ValueRef(literal="iam"),
            "WAREHOUSE_REGION": ValueRef(literal=self._config.region),
            "WAREHOUSE_RESOURCE_ARN": ValueRef(literal=workgroup_arn),
            "WAREHOUSE_WORKGROUP": ValueRef(literal=resource_id),
            "WAREHOUSE_NAMESPACE": ValueRef(literal=resource_id),
            "WAREHOUSE_TLS": ValueRef(literal="1"),
        }
        grants = [Grant(workgroup_arn, ["redshift-serverless:GetCredentials"])]
        auth_mode = str(cfg.get("auth_mode") or "iam")
        if auth_mode == "admin_secret":
            secret_arn = str(namespace.get("adminPasswordSecretArn") or "")
            if not secret_arn:
                raise ManagedServiceError(
                    f"Redshift Serverless namespace {resource_id} has no managed admin secret",
                )
            env_vars["WAREHOUSE_AUTH_MODE"] = ValueRef(literal="admin_secret")
            # The ARN, for SDK use, and the credential document itself, which
            # the platform materializes like every other driver's secret (#1954).
            env_vars["WAREHOUSE_CREDENTIALS"] = ValueRef(secret_ref=secret_arn)
            env_vars["WAREHOUSE_CREDENTIALS_REF"] = ValueRef(literal=secret_arn)
            env_vars["WAREHOUSE_USER"] = ValueRef(
                literal=str(namespace.get("adminUsername") or self._config.admin_username),
            )
            grants.append(Grant(secret_arn, ["secretsmanager:GetSecretValue"]))
        if cfg.get("data_api_access"):
            grants.extend(_data_api_grants(workgroup_arn))
        return Binding(
            env_vars=env_vars,
            iam_grants=grants,
            notes="TLS Redshift Serverless endpoint; IAM temporary credentials are the default",
        )

    @driver_op(cloud="aws", driver="redshift_serverless")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        _, resource_id = parse_handle(handle.handle)
        snapshot_id = _snapshot_name(resource_id, "snapshot")
        try:
            response = self._serverless.create_snapshot(
                namespaceName=resource_id,
                snapshotName=snapshot_id,
                retentionPeriod=self._config.snapshot_retention_days,
                tags=self._resource_tags(resource_id),
            )
        except Exception as exc:
            raise ManagedServiceError(f"create Redshift Serverless snapshot: {exc}") from exc
        snapshot = response.get("snapshot") or {}
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=str(snapshot.get("snapshotArn") or snapshot.get("snapshotName") or snapshot_id),
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="redshift_serverless")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        resource_id = self._resource_id(target)
        handle = handle_for(kind=KIND, resource_id=resource_id)
        namespace = self._get_namespace(resource_id)
        workgroup = self._get_workgroup(resource_id)
        if namespace is None or workgroup is None:
            provisioned = self.provision(target)
            if not provisioned.ok:
                return provisioned
            namespace = self._get_namespace(resource_id)
            workgroup = self._get_workgroup(resource_id)
        if (
            namespace is None
            or workgroup is None
            or namespace.get("status") != "AVAILABLE"
            or workgroup.get("status") != "AVAILABLE"
        ):
            return ProvisionResult(
                False,
                handle,
                "Redshift Serverless restore target is provisioning; retry restore when available",
                ["restore_target_not_ready"],
            )
        kwargs: dict[str, Any] = {
            "namespaceName": resource_id,
            "workgroupName": resource_id,
            "manageAdminPassword": bool(
                target.config.get(
                    "manage_admin_password",
                    self._config.manage_admin_password_default,
                ),
            ),
        }
        if snapshot.snapshot_id.startswith("arn:"):
            kwargs["snapshotArn"] = snapshot.snapshot_id
        else:
            kwargs["snapshotName"] = snapshot.snapshot_id
        if target.config.get("admin_password_secret_kms_key_id"):
            kwargs["adminPasswordSecretKmsKeyId"] = str(
                target.config["admin_password_secret_kms_key_id"],
            )
        try:
            self._serverless.restore_from_snapshot(**kwargs)
        except Exception as exc:
            return ProvisionResult(False, handle, f"restore Redshift Serverless: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"Redshift Serverless restore queued for {resource_id}")

    @driver_op(cloud="aws", driver="redshift_serverless", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "database": {"type": "string"},
                "admin_username": {"type": "string"},
                "manage_admin_password": {"type": "boolean", "const": True},
                "admin_password_secret_kms_key_id": {"type": "string"},
                "auth_mode": {"type": "string", "enum": ["iam", "admin_secret"]},
                "data_api_access": {"type": "boolean"},
                "kms_key_id": {"type": "string"},
                "default_iam_role_arn": {"type": "string"},
                "iam_roles": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "log_exports": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["useractivitylog", "userlog", "connectionlog"],
                    },
                    "uniqueItems": True,
                },
                "base_capacity": {"type": "integer", "minimum": 4, "maximum": 1024},
                "max_capacity": {"type": "integer", "minimum": 4, "maximum": 1024},
                "enhanced_vpc_routing": {"type": "boolean"},
                "extra_compute_for_automatic_optimization": {"type": "boolean"},
                "ip_address_type": {"type": "string", "enum": ["ipv4", "dualstack"]},
                "port": {"type": "integer"},
                "publicly_accessible": {"type": "boolean"},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "subnet_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 3,
                    "uniqueItems": True,
                },
                "track_name": {"type": "string"},
                "config_parameters": {
                    "type": ["object", "array"],
                },
                "price_performance_target": {
                    "type": "object",
                    "properties": {
                        "level": {"type": "integer"},
                        "status": {"type": "string", "enum": ["ENABLED", "DISABLED"]},
                    },
                },
                "snapshot_retention_days": {"type": "integer", "minimum": 1, "maximum": 3653},
                "deletion_protection": {"type": "boolean"},
            },
        }

    @driver_op(cloud="aws", driver="redshift_serverless", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "WAREHOUSE_ENDPOINT": "Redshift Serverless DNS endpoint",
                "WAREHOUSE_PORT": "PostgreSQL-compatible connection port",
                "WAREHOUSE_DATABASE": "Default database",
                "WAREHOUSE_URL": "Credential-free PostgreSQL-compatible URL",
                "WAREHOUSE_USER": "Admin user when admin_secret auth is explicitly selected",
                "WAREHOUSE_CREDENTIALS": "Managed admin credential document (admin_secret auth mode)",
                "WAREHOUSE_CREDENTIALS_REF": "Managed Secrets Manager credential document ARN",
                "WAREHOUSE_ENGINE": "redshift",
                "WAREHOUSE_DEPLOYMENT": "serverless",
                "WAREHOUSE_AUTH_MODE": "iam or admin_secret",
                "WAREHOUSE_REGION": "AWS region",
                "WAREHOUSE_RESOURCE_ARN": "Redshift Serverless workgroup ARN",
                "WAREHOUSE_WORKGROUP": "Serverless workgroup name",
                "WAREHOUSE_NAMESPACE": "Serverless namespace name",
                "WAREHOUSE_TLS": "Always 1",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "auth_mode",
            "data_api_access",
            "admin_username",
            "admin_password_secret_kms_key_id",
            "kms_key_id",
            "default_iam_role_arn",
            "iam_roles",
            "log_exports",
            "base_capacity",
            "max_capacity",
            "enhanced_vpc_routing",
            "extra_compute_for_automatic_optimization",
            "ip_address_type",
            "port",
            "publicly_accessible",
            "security_group_ids",
            "subnet_ids",
            "track_name",
            "config_parameters",
            "price_performance_target",
            "snapshot_retention_days",
            "deletion_protection",
        ]

    def _create_namespace(self, resource_id: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> None:
        kwargs: dict[str, Any] = {
            "namespaceName": resource_id,
            "dbName": str(cfg.get("database") or "dev"),
            "adminUsername": str(cfg.get("admin_username") or self._config.admin_username),
            "manageAdminPassword": True,
            "tags": _serverless_tags(spec),
        }
        for key, aws_key in (
            ("admin_password_secret_kms_key_id", "adminPasswordSecretKmsKeyId"),
            ("kms_key_id", "kmsKeyId"),
            ("default_iam_role_arn", "defaultIamRoleArn"),
        ):
            if cfg.get(key):
                kwargs[aws_key] = cfg[key]
        if cfg.get("iam_roles"):
            kwargs["iamRoles"] = list(cfg["iam_roles"])
        if cfg.get("log_exports"):
            kwargs["logExports"] = list(cfg["log_exports"])
        self._serverless.create_namespace(**kwargs)

    def _create_workgroup(self, resource_id: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> None:
        kwargs: dict[str, Any] = {
            "workgroupName": resource_id,
            "namespaceName": resource_id,
            "baseCapacity": int(
                cfg.get(
                    "base_capacity",
                    _SIZE_TO_RPU.get(spec.size, self._config.base_capacity_default),
                ),
            ),
            "enhancedVpcRouting": bool(cfg.get("enhanced_vpc_routing", True)),
            "publiclyAccessible": bool(cfg.get("publicly_accessible", False)),
            "port": int(cfg.get("port", 5439)),
            "subnetIds": list(cfg.get("subnet_ids") or self._config.subnet_ids),
            "securityGroupIds": list(
                cfg.get("security_group_ids") or self._config.security_group_ids,
            ),
            "tags": _serverless_tags(spec),
        }
        for key, aws_key, cast in (
            ("max_capacity", "maxCapacity", int),
            (
                "extra_compute_for_automatic_optimization",
                "extraComputeForAutomaticOptimization",
                bool,
            ),
            ("ip_address_type", "ipAddressType", str),
            ("track_name", "trackName", str),
        ):
            if key in cfg:
                kwargs[aws_key] = cast(cfg[key])
        if "config_parameters" in cfg:
            kwargs["configParameters"] = _config_parameters(cfg["config_parameters"])
        if "price_performance_target" in cfg:
            kwargs["pricePerformanceTarget"] = dict(cfg["price_performance_target"])
        self._serverless.create_workgroup(**kwargs)

    def _tags_of(self, arn: str) -> list[dict[str, str]]:
        """Tags of an existing resource; unreadable counts as untagged (#1961)."""
        try:
            resp = self._serverless.list_tags_for_resource(resourceArn=arn)
            return list(resp.get("tags") or [])
        except Exception:  # ownership unverifiable, so not adopted
            return []

    def _get_namespace(self, resource_id: str) -> dict[str, Any] | None:
        try:
            return self._serverless.get_namespace(namespaceName=resource_id).get("namespace")
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _get_workgroup(self, resource_id: str) -> dict[str, Any] | None:
        try:
            return self._serverless.get_workgroup(workgroupName=resource_id).get("workgroup")
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _resource_tags(self, resource_id: str) -> list[dict[str, str]]:
        namespace = self._get_namespace(resource_id) or {}
        arn = str(namespace.get("namespaceArn") or "")
        if not arn:
            return []
        return list(self._serverless.list_tags_for_resource(resourceArn=arn).get("tags", []))

    def _resource_id(self, spec: ProvisionSpec) -> str:
        return _name(
            self._config.name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "warehouse",
        )

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        # ownerAccount restored a snapshot another AWS account owns; a restore
        # takes only the snapshot the platform retained for this app (#2087).
        if "owner_account" in cfg:
            return (
                "owner_account is no longer supported; restore from a snapshot Astrolift retained for this app instead"
            )
        if cfg.get("manage_admin_password", True) is not True:
            return "Redshift Serverless requires managed admin passwords; plaintext config secrets are not accepted"
        if cfg.get("auth_mode", "iam") not in {"iam", "admin_secret"}:
            return "auth_mode must be iam or admin_secret"
        for key in ("base_capacity", "max_capacity"):
            if key in cfg:
                value = cfg[key]
                if not isinstance(value, int) or isinstance(value, bool) or not _valid_rpu(value):
                    return f"{key} must be 4, an 8-RPU increment through 512, or a 32-RPU increment through 1024"
        if "base_capacity" in cfg and "max_capacity" in cfg and int(cfg["base_capacity"]) > int(cfg["max_capacity"]):
            return "base_capacity cannot exceed max_capacity"
        if "port" in cfg:
            port = cfg["port"]
            if (
                not isinstance(port, int)
                or isinstance(port, bool)
                or not (5431 <= port <= 5455 or 8191 <= port <= 8215)
            ):
                return "port must be in Redshift ranges 5431-5455 or 8191-8215"
        if "ip_address_type" in cfg and cfg["ip_address_type"] not in {"ipv4", "dualstack"}:
            return "ip_address_type must be ipv4 or dualstack"
        if "subnet_ids" in cfg:
            subnets = cfg["subnet_ids"]
            if not isinstance(subnets, list) or len(set(subnets)) < 3:
                return "subnet_ids must contain at least three distinct subnets"
        if "log_exports" in cfg:
            exports = cfg["log_exports"]
            allowed = {"useractivitylog", "userlog", "connectionlog"}
            if not isinstance(exports, list) or any(value not in allowed for value in exports):
                return "log_exports may contain only useractivitylog, userlog, and connectionlog"
        if "config_parameters" in cfg:
            try:
                _config_parameters(cfg["config_parameters"])
            except (TypeError, ValueError) as exc:
                return str(exc)
        if "snapshot_retention_days" in cfg:
            value = cfg["snapshot_retention_days"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 3653:
                return "snapshot_retention_days must be an integer from 1 through 3653"
        for key in (
            "manage_admin_password",
            "data_api_access",
            "enhanced_vpc_routing",
            "extra_compute_for_automatic_optimization",
            "publicly_accessible",
            "deletion_protection",
        ):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        return ""


def _config_parameters(value: Any) -> list[dict[str, str]]:
    if isinstance(value, dict):
        return [
            {"parameterKey": str(key), "parameterValue": str(parameter)} for key, parameter in sorted(value.items())
        ]
    if isinstance(value, list) and all(
        isinstance(item, dict) and "parameterKey" in item and "parameterValue" in item for item in value
    ):
        return [
            {
                "parameterKey": str(item["parameterKey"]),
                "parameterValue": str(item["parameterValue"]),
            }
            for item in value
        ]
    raise ValueError("config_parameters must be a key/value object or AWS parameter list")


def _serverless_tags(spec: ProvisionSpec) -> list[dict[str, str]]:
    """Translate the common AWS tag shape to Redshift Serverless' API shape."""
    return [{"key": tag["Key"], "value": tag["Value"]} for tag in tags_for(spec)]


def _valid_rpu(value: int) -> bool:
    return value == 4 or (8 <= value <= 512 and value % 8 == 0) or (512 < value <= 1024 and value % 32 == 0)


def _name(*parts: str) -> str:
    raw = "-".join(str(part).lower() for part in parts if part)
    clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw)
    while "--" in clean:
        clean = clean.replace("--", "-")
    if not clean or not clean[0].isalpha():
        clean = f"a-{clean}"
    return clean.strip("-")[:64].rstrip("-")


def _snapshot_name(resource_id: str, suffix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    tail = f"-{suffix[:8]}-{stamp}"
    return f"{resource_id[: 255 - len(tail)]}{tail}".rstrip("-")


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return "ResourceNotFound" in code or "ResourceNotFound" in type(exc).__name__ or "not found" in str(exc).lower()


def _retryable_cloud_error(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
    return status >= 500 or code.startswith("Throttl") or code in {"ConflictException", "InternalServerException"}


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(
        False,
        handle,
        f"{operation}: {exc}",
        [str(exc)],
        retryable=_retryable_cloud_error(exc),
    )
