"""Amazon Redshift provisioned warehouse managed-service driver."""

from __future__ import annotations

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
from aws.managed._base import ManagedServiceError, handle_for, parse_handle, tags_for
from aws.session import aws_client

KIND = "warehouse"
_SIZE_TO_NODE = {
    "small": ("ra3.xlplus", 1),
    "medium": ("ra3.xlplus", 2),
    "large": ("ra3.4xlarge", 2),
    "xlarge": ("ra3.4xlarge", 4),
}
_STATE = {
    "available": "available",
    "available, prep-for-resize": "updating",
    "available, resize-cleanup": "updating",
    "cancelling-resize": "updating",
    "creating": "provisioning",
    "deleting": "deprovisioning",
    "final-snapshot": "deprovisioning",
    "hardware-failure": "error",
    "incompatible-hsm": "error",
    "incompatible-network": "error",
    "incompatible-parameters": "error",
    "incompatible-restore": "error",
    "modifying": "updating",
    "paused": "error",
    "rebooting": "updating",
    "renaming": "updating",
    "resizing": "updating",
    "rotating-keys": "updating",
    "storage-full": "error",
    "updating-hsm": "updating",
}


@dataclass(frozen=True)
class RedshiftConfig(CredentialedConfig):
    region: str
    account_id: str
    cluster_subnet_group: str
    security_group_ids: list[str] = field(default_factory=list)
    cluster_name_prefix: str = "astrolift"
    node_type_default: str = "ra3.xlplus"
    automated_snapshot_retention_days: int = 7
    manual_snapshot_retention_days: int = 30
    deletion_protection_default: bool = True
    manage_admin_password_default: bool = True
    master_username: str = "astrolift"


class RedshiftProvisionedDriver(ManagedServiceDriver):
    def __init__(self, *, config: RedshiftConfig, redshift_client: Any | None = None) -> None:
        self._config = config
        if redshift_client is None:
            redshift_client = aws_client("redshift", region=config.region, credential=config.credential)
        self._redshift = redshift_client

    @driver_op(
        cloud="aws",
        driver="redshift",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_redshift_config"])
        _, requested_nodes = self._capacity(spec.size, cfg)
        if cfg.get("multi_az") and requested_nodes < 2:
            return ProvisionResult(
                False,
                "",
                "multi_az Redshift clusters require at least two nodes",
                ["invalid_redshift_config"],
            )
        cluster_id = self._cluster_id(spec)
        handle = handle_for(kind=KIND, resource_id=cluster_id)
        cluster = self._describe_cluster(cluster_id)
        try:
            if cluster is None:
                if cfg.get("snapshot_identifier") or cfg.get("snapshot_arn"):
                    self._restore_cluster(cluster_id, spec, cfg)
                else:
                    self._create_cluster(cluster_id, spec, cfg)
            else:
                updated = self.update(UpdateSpec(handle=handle, size=spec.size, config=cfg))
                return ProvisionResult(updated.ok, handle, updated.message, updated.errors)
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Redshift: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"Redshift cluster {cluster_id} provisioning")

    @driver_op(cloud="aws", driver="redshift")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, cluster_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            return UpdateResult(False, spec.handle, f"Redshift cluster {cluster_id} not found", ["not_found"])
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_redshift_config"])
        _, requested_nodes = self._capacity(spec.size or "", cfg, cluster=cluster)
        if cfg.get("multi_az") and requested_nodes < 2:
            return UpdateResult(
                False,
                spec.handle,
                "multi_az Redshift clusters require at least two nodes",
                ["invalid_redshift_config"],
            )
        kwargs: dict[str, Any] = {"ClusterIdentifier": cluster_id}
        for key, aws_key, cast in (
            ("automated_snapshot_retention_days", "AutomatedSnapshotRetentionPeriod", int),
            ("manual_snapshot_retention_days", "ManualSnapshotRetentionPeriod", int),
            ("allow_version_upgrade", "AllowVersionUpgrade", bool),
            ("cluster_version", "ClusterVersion", str),
            ("cluster_parameter_group", "ClusterParameterGroupName", str),
            ("preferred_maintenance_window", "PreferredMaintenanceWindow", str),
            ("publicly_accessible", "PubliclyAccessible", bool),
            ("enhanced_vpc_routing", "EnhancedVpcRouting", bool),
            ("availability_zone_relocation", "AvailabilityZoneRelocation", bool),
            ("multi_az", "MultiAZ", bool),
            ("ip_address_type", "IpAddressType", str),
            ("port", "Port", int),
            ("maintenance_track_name", "MaintenanceTrackName", str),
            ("encrypted", "Encrypted", bool),
            ("kms_key_id", "KmsKeyId", str),
            (
                "extra_compute_for_automatic_optimization",
                "ExtraComputeForAutomaticOptimization",
                bool,
            ),
        ):
            if key in cfg:
                kwargs[aws_key] = cast(cfg[key])
        if "security_group_ids" in cfg:
            kwargs["VpcSecurityGroupIds"] = list(cfg["security_group_ids"])
        node_type, nodes = self._capacity(spec.size or "", cfg, cluster=cluster)
        if spec.size or "node_type" in cfg or "number_of_nodes" in cfg:
            kwargs["NodeType"] = node_type
            kwargs["ClusterType"] = "single-node" if nodes == 1 else "multi-node"
            if nodes > 1:
                kwargs["NumberOfNodes"] = nodes
        changed = False
        try:
            if len(kwargs) > 1:
                self._redshift.modify_cluster(**kwargs)
                changed = True
            if "iam_roles" in cfg or "default_iam_role_arn" in cfg:
                current = {
                    str(role.get("IamRoleArn") if isinstance(role, dict) else role)
                    for role in (cluster.get("IamRoles") or [])
                }
                desired = set(cfg.get("iam_roles") or current)
                roles: dict[str, Any] = {
                    "ClusterIdentifier": cluster_id,
                    "AddIamRoles": sorted(desired - current),
                    "RemoveIamRoles": sorted(current - desired),
                }
                if "default_iam_role_arn" in cfg:
                    roles["DefaultIamRoleArn"] = str(cfg["default_iam_role_arn"])
                self._redshift.modify_cluster_iam_roles(**roles)
                changed = True
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"modify Redshift: {exc}", [str(exc)])
        return UpdateResult(
            True,
            spec.handle,
            f"Redshift cluster {cluster_id} update queued" if changed else "no Redshift changes requested",
        )

    @driver_op(
        cloud="aws",
        driver="redshift",
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
        _, cluster_id = parse_handle(spec.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            return DeprovisionResult(True, spec.handle, f"Redshift cluster {cluster_id} already gone")
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
                f"Redshift cluster {cluster_id} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if cluster.get("ClusterStatus") in {"deleting", "final-snapshot"}:
            return DeprovisionResult(
                False,
                spec.handle,
                f"Redshift cluster {cluster_id} deletion is still in progress",
                ["cluster_deletion_in_progress"],
            )
        kwargs: dict[str, Any] = {
            "ClusterIdentifier": cluster_id,
            "SkipFinalClusterSnapshot": bool(delete_data),
        }
        if not delete_data:
            kwargs["FinalClusterSnapshotIdentifier"] = _snapshot_name(cluster_id, "final")
            kwargs["FinalClusterSnapshotRetentionPeriod"] = int(
                spec.config.get(
                    "manual_snapshot_retention_days",
                    self._config.manual_snapshot_retention_days,
                ),
            )
        try:
            self._redshift.delete_cluster(**kwargs)
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Redshift cluster", exc)
        return DeprovisionResult(
            False,
            spec.handle,
            f"Redshift cluster {cluster_id} deletion queued",
            ["cluster_deletion_in_progress"],
        )

    @driver_op(cloud="aws", driver="redshift")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"Redshift cluster {cluster_id} not found")
        state = str(cluster.get("ClusterStatus") or "unknown")
        return ServiceStatus(
            handle.handle,
            _STATE.get(state, "updating"),
            f"Redshift reports {state}",
        )

    @driver_op(cloud="aws", driver="redshift")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        _, cluster_id = parse_handle(handle.handle)
        cluster = self._describe_cluster(cluster_id)
        if cluster is None:
            raise ManagedServiceError(f"binding requested for missing Redshift cluster {cluster_id}")
        cfg = config or {}
        endpoint = cluster.get("Endpoint") or {}
        host = str(endpoint.get("Address") or "")
        port = int(endpoint.get("Port") or cfg.get("port") or 5439)
        database = str(cluster.get("DBName") or cfg.get("database") or "dev")
        cluster_arn = _cluster_arn(
            resource_arn=str(cluster.get("ClusterNamespaceArn") or ""),
            region=self._config.region,
            account_id=self._config.account_id,
            cluster_id=cluster_id,
        )
        env_vars = {
            "WAREHOUSE_ENDPOINT": ValueRef(literal=host),
            "WAREHOUSE_PORT": ValueRef(literal=str(port)),
            "WAREHOUSE_DATABASE": ValueRef(literal=database),
            "WAREHOUSE_URL": ValueRef(literal=f"postgresql://{host}:{port}/{database}"),
            "WAREHOUSE_ENGINE": ValueRef(literal="redshift"),
            "WAREHOUSE_DEPLOYMENT": ValueRef(literal="provisioned"),
            "WAREHOUSE_AUTH_MODE": ValueRef(literal="iam"),
            "WAREHOUSE_REGION": ValueRef(literal=self._config.region),
            "WAREHOUSE_RESOURCE_ARN": ValueRef(literal=cluster_arn),
            "WAREHOUSE_CLUSTER_ID": ValueRef(literal=cluster_id),
            "WAREHOUSE_TLS": ValueRef(literal="1"),
        }
        dbname_arn = _database_name_arn(cluster_arn, cluster_id, database)
        grants = [
            Grant(dbname_arn, ["redshift:GetClusterCredentialsWithIAM"]),
            Grant(cluster_arn, ["redshift:DescribeClusters"]),
        ]
        auth_mode = str(cfg.get("auth_mode") or "iam")
        if auth_mode == "admin_secret":
            secret_arn = str(cluster.get("MasterPasswordSecretArn") or "")
            if not secret_arn:
                raise ManagedServiceError(
                    f"Redshift cluster {cluster_id} does not expose a managed admin secret",
                )
            env_vars["WAREHOUSE_AUTH_MODE"] = ValueRef(literal="admin_secret")
            env_vars["WAREHOUSE_CREDENTIALS_REF"] = ValueRef(literal=secret_arn)
            env_vars["WAREHOUSE_USER"] = ValueRef(
                literal=str(cluster.get("MasterUsername") or self._config.master_username),
            )
            grants.append(Grant(secret_arn, ["secretsmanager:GetSecretValue"]))
        if cfg.get("data_api_access"):
            grants.extend(_data_api_grants(cluster_arn))
        return Binding(
            env_vars=env_vars,
            iam_grants=grants,
            notes="TLS PostgreSQL-compatible endpoint; IAM temporary credentials are the default",
        )

    @driver_op(cloud="aws", driver="redshift")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        _, cluster_id = parse_handle(handle.handle)
        snapshot_id = _snapshot_name(cluster_id, "snapshot")
        try:
            response = self._redshift.create_cluster_snapshot(
                ClusterIdentifier=cluster_id,
                SnapshotIdentifier=snapshot_id,
                ManualSnapshotRetentionPeriod=self._config.manual_snapshot_retention_days,
                Tags=self._cluster_tags(cluster_id),
            )
        except Exception as exc:
            raise ManagedServiceError(f"create Redshift cluster snapshot: {exc}") from exc
        snapshot = response.get("Snapshot") or {}
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=str(snapshot.get("SnapshotArn") or snapshot_id),
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="aws", driver="redshift")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        cfg = dict(target.config or {})
        if snapshot.snapshot_id.startswith("arn:"):
            cfg["snapshot_arn"] = snapshot.snapshot_id
        else:
            cfg["snapshot_identifier"] = snapshot.snapshot_id
        return self.provision(replace(target, config=cfg))

    @driver_op(cloud="aws", driver="redshift", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "database": {"type": "string"},
                "master_username": {"type": "string"},
                "manage_admin_password": {"type": "boolean", "const": True},
                "admin_password_secret_kms_key_id": {"type": "string"},
                "auth_mode": {"type": "string", "enum": ["iam", "admin_secret"]},
                "data_api_access": {"type": "boolean"},
                "node_type": {"type": "string"},
                "number_of_nodes": {"type": "integer", "minimum": 1, "maximum": 100},
                "multi_az": {"type": "boolean"},
                "cluster_version": {"type": "string"},
                "allow_version_upgrade": {"type": "boolean"},
                "automated_snapshot_retention_days": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 35,
                },
                "manual_snapshot_retention_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 3653,
                },
                "deletion_protection": {"type": "boolean"},
                "encrypted": {"type": "boolean"},
                "kms_key_id": {"type": "string"},
                "publicly_accessible": {"type": "boolean"},
                "enhanced_vpc_routing": {"type": "boolean"},
                "availability_zone": {"type": "string"},
                "availability_zone_relocation": {"type": "boolean"},
                "ip_address_type": {"type": "string", "enum": ["ipv4", "dualstack"]},
                "port": {"type": "integer"},
                "cluster_parameter_group": {"type": "string"},
                "preferred_maintenance_window": {"type": "string"},
                "maintenance_track_name": {"type": "string"},
                "snapshot_schedule_identifier": {"type": "string"},
                "iam_roles": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "default_iam_role_arn": {"type": "string"},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "extra_compute_for_automatic_optimization": {"type": "boolean"},
                "load_sample_data": {"type": "string"},
            },
        }

    @driver_op(cloud="aws", driver="redshift", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "WAREHOUSE_ENDPOINT": "Redshift writer DNS endpoint",
                "WAREHOUSE_PORT": "PostgreSQL-compatible connection port",
                "WAREHOUSE_DATABASE": "Default database",
                "WAREHOUSE_URL": "Credential-free PostgreSQL-compatible URL",
                "WAREHOUSE_USER": "Admin user when admin_secret auth is explicitly selected",
                "WAREHOUSE_CREDENTIALS_REF": "Managed Secrets Manager credential document ARN",
                "WAREHOUSE_ENGINE": "redshift",
                "WAREHOUSE_DEPLOYMENT": "provisioned",
                "WAREHOUSE_AUTH_MODE": "iam or admin_secret",
                "WAREHOUSE_REGION": "AWS region",
                "WAREHOUSE_RESOURCE_ARN": "Redshift cluster ARN",
                "WAREHOUSE_CLUSTER_ID": "Provisioned cluster identifier",
                "WAREHOUSE_TLS": "Always 1",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "auth_mode",
            "data_api_access",
            "node_type",
            "number_of_nodes",
            "multi_az",
            "cluster_version",
            "allow_version_upgrade",
            "automated_snapshot_retention_days",
            "manual_snapshot_retention_days",
            "deletion_protection",
            "encrypted",
            "kms_key_id",
            "publicly_accessible",
            "enhanced_vpc_routing",
            "availability_zone_relocation",
            "ip_address_type",
            "port",
            "cluster_parameter_group",
            "preferred_maintenance_window",
            "maintenance_track_name",
            "iam_roles",
            "default_iam_role_arn",
            "security_group_ids",
            "extra_compute_for_automatic_optimization",
        ]

    def _create_cluster(self, cluster_id: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> None:
        node_type, nodes = self._capacity(spec.size, cfg)
        kwargs: dict[str, Any] = {
            "ClusterIdentifier": cluster_id,
            "ClusterType": "single-node" if nodes == 1 else "multi-node",
            "NodeType": node_type,
            "DBName": str(cfg.get("database") or "dev"),
            "MasterUsername": str(cfg.get("master_username") or self._config.master_username),
            "ManageMasterPassword": bool(
                cfg.get("manage_admin_password", self._config.manage_admin_password_default),
            ),
            "ClusterSubnetGroupName": self._config.cluster_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "Port": int(cfg.get("port", 5439)),
            "Encrypted": bool(cfg.get("encrypted", True)),
            "PubliclyAccessible": bool(cfg.get("publicly_accessible", False)),
            "EnhancedVpcRouting": bool(cfg.get("enhanced_vpc_routing", True)),
            "AutomatedSnapshotRetentionPeriod": int(
                cfg.get(
                    "automated_snapshot_retention_days",
                    self._config.automated_snapshot_retention_days,
                ),
            ),
            "ManualSnapshotRetentionPeriod": int(
                cfg.get(
                    "manual_snapshot_retention_days",
                    self._config.manual_snapshot_retention_days,
                ),
            ),
            "AllowVersionUpgrade": bool(cfg.get("allow_version_upgrade", True)),
            "Tags": tags_for(spec),
        }
        if nodes > 1:
            kwargs["NumberOfNodes"] = nodes
        for key, aws_key in (
            ("admin_password_secret_kms_key_id", "MasterPasswordSecretKmsKeyId"),
            ("kms_key_id", "KmsKeyId"),
            ("cluster_version", "ClusterVersion"),
            ("availability_zone", "AvailabilityZone"),
            ("cluster_parameter_group", "ClusterParameterGroupName"),
            ("preferred_maintenance_window", "PreferredMaintenanceWindow"),
            ("maintenance_track_name", "MaintenanceTrackName"),
            ("snapshot_schedule_identifier", "SnapshotScheduleIdentifier"),
            ("default_iam_role_arn", "DefaultIamRoleArn"),
            ("ip_address_type", "IpAddressType"),
            ("load_sample_data", "LoadSampleData"),
        ):
            if cfg.get(key):
                kwargs[aws_key] = cfg[key]
        if cfg.get("iam_roles"):
            kwargs["IamRoles"] = list(cfg["iam_roles"])
        for key, aws_key in (
            ("multi_az", "MultiAZ"),
            ("availability_zone_relocation", "AvailabilityZoneRelocation"),
            (
                "extra_compute_for_automatic_optimization",
                "ExtraComputeForAutomaticOptimization",
            ),
        ):
            if key in cfg:
                kwargs[aws_key] = bool(cfg[key])
        self._redshift.create_cluster(**kwargs)

    def _restore_cluster(self, cluster_id: str, spec: ProvisionSpec, cfg: dict[str, Any]) -> None:
        node_type, nodes = self._capacity(spec.size, cfg)
        kwargs: dict[str, Any] = {
            "ClusterIdentifier": cluster_id,
            "NodeType": node_type,
            "ClusterSubnetGroupName": self._config.cluster_subnet_group,
            "VpcSecurityGroupIds": list(self._config.security_group_ids),
            "Port": int(cfg.get("port", 5439)),
            "PubliclyAccessible": bool(cfg.get("publicly_accessible", False)),
            "EnhancedVpcRouting": bool(cfg.get("enhanced_vpc_routing", True)),
            "ManageMasterPassword": bool(
                cfg.get("manage_admin_password", self._config.manage_admin_password_default),
            ),
        }
        if nodes > 1:
            kwargs["NumberOfNodes"] = nodes
        if cfg.get("snapshot_arn"):
            kwargs["SnapshotArn"] = str(cfg["snapshot_arn"])
        else:
            kwargs["SnapshotIdentifier"] = str(cfg["snapshot_identifier"])
        for key, aws_key in (
            ("snapshot_cluster_identifier", "SnapshotClusterIdentifier"),
            ("owner_account", "OwnerAccount"),
            ("admin_password_secret_kms_key_id", "MasterPasswordSecretKmsKeyId"),
            ("kms_key_id", "KmsKeyId"),
            ("availability_zone", "AvailabilityZone"),
            ("cluster_parameter_group", "ClusterParameterGroupName"),
            ("preferred_maintenance_window", "PreferredMaintenanceWindow"),
            ("maintenance_track_name", "MaintenanceTrackName"),
            ("snapshot_schedule_identifier", "SnapshotScheduleIdentifier"),
            ("default_iam_role_arn", "DefaultIamRoleArn"),
            ("ip_address_type", "IpAddressType"),
        ):
            if cfg.get(key):
                kwargs[aws_key] = cfg[key]
        if cfg.get("iam_roles"):
            kwargs["IamRoles"] = list(cfg["iam_roles"])
        for key, aws_key in (
            ("multi_az", "MultiAZ"),
            ("availability_zone_relocation", "AvailabilityZoneRelocation"),
        ):
            if key in cfg:
                kwargs[aws_key] = bool(cfg[key])
        response = self._redshift.restore_from_cluster_snapshot(**kwargs)
        restored = response.get("Cluster") or {}
        resource_arn = str(restored.get("ClusterNamespaceArn") or "")
        if resource_arn:
            self._redshift.create_tags(ResourceName=resource_arn, Tags=tags_for(spec))

    def _describe_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        try:
            rows = self._redshift.describe_clusters(ClusterIdentifier=cluster_id).get("Clusters", [])
        except Exception as exc:
            if _not_found(exc, "ClusterNotFound"):
                return None
            raise
        return rows[0] if rows else None

    def _cluster_tags(self, cluster_id: str) -> list[dict[str, str]]:
        cluster = self._describe_cluster(cluster_id)
        return list((cluster or {}).get("Tags") or [])

    def _cluster_id(self, spec: ProvisionSpec) -> str:
        return _name(
            self._config.cluster_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint or "warehouse",
        )

    def _capacity(
        self,
        size: str,
        cfg: dict[str, Any],
        *,
        cluster: dict[str, Any] | None = None,
    ) -> tuple[str, int]:
        default_type, default_nodes = _SIZE_TO_NODE.get(
            size,
            (self._config.node_type_default, 2),
        )
        current = cluster or {}
        node_type = cfg.get("node_type")
        nodes = cfg.get("number_of_nodes")
        if node_type is None:
            node_type = default_type if size else current.get("NodeType") or default_type
        if nodes is None:
            nodes = default_nodes if size else current.get("NumberOfNodes") or default_nodes
        return (
            str(node_type),
            int(nodes),
        )

    def _validate_config(self, cfg: dict[str, Any]) -> str:
        if cfg.get("manage_admin_password", True) is not True:
            return "Redshift requires managed admin passwords; plaintext config secrets are not accepted"
        if "number_of_nodes" in cfg:
            value = cfg["number_of_nodes"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
                return "number_of_nodes must be an integer from 1 through 100"
        for key, minimum, maximum in (
            ("automated_snapshot_retention_days", 0, 35),
            ("manual_snapshot_retention_days", 1, 3653),
        ):
            if key in cfg:
                value = cfg[key]
                if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                    return f"{key} must be an integer from {minimum} through {maximum}"
        if cfg.get("auth_mode", "iam") not in {"iam", "admin_secret"}:
            return "auth_mode must be iam or admin_secret"
        if cfg.get("auth_mode") == "admin_secret" and not cfg.get(
            "manage_admin_password",
            self._config.manage_admin_password_default,
        ):
            return "admin_secret auth requires manage_admin_password"
        nodes = cfg.get("number_of_nodes")
        if cfg.get("multi_az") and nodes is not None and int(nodes) < 2:
            return "multi_az Redshift clusters require at least two nodes"
        if "ip_address_type" in cfg and cfg["ip_address_type"] not in {"ipv4", "dualstack"}:
            return "ip_address_type must be ipv4 or dualstack"
        if "port" in cfg:
            port = cfg["port"]
            if (
                not isinstance(port, int)
                or isinstance(port, bool)
                or not (5431 <= port <= 5455 or 8191 <= port <= 8215)
            ):
                return "port must be in Redshift ranges 5431-5455 or 8191-8215"
        for key in (
            "manage_admin_password",
            "data_api_access",
            "multi_az",
            "allow_version_upgrade",
            "deletion_protection",
            "encrypted",
            "publicly_accessible",
            "enhanced_vpc_routing",
            "availability_zone_relocation",
            "extra_compute_for_automatic_optimization",
        ):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        return ""


def _name(*parts: str) -> str:
    raw = "-".join(str(part).lower() for part in parts if part)
    clean = "".join(char if char.isalnum() or char == "-" else "-" for char in raw)
    while "--" in clean:
        clean = clean.replace("--", "-")
    if not clean or not clean[0].isalpha():
        clean = f"a-{clean}"
    return clean.strip("-")[:63].rstrip("-")


def _snapshot_name(cluster_id: str, suffix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    tail = f"-{suffix[:8]}-{stamp}"
    return f"{cluster_id[: 255 - len(tail)]}{tail}".rstrip("-")


def _cluster_arn(*, resource_arn: str, region: str, account_id: str, cluster_id: str) -> str:
    parts = resource_arn.split(":")
    partition = parts[1] if len(parts) > 1 and parts[0] == "arn" else "aws"
    account = account_id or (parts[4] if len(parts) > 4 else "")
    if not account:
        raise ManagedServiceError("Redshift IAM binding requires the AWS account id")
    return f"arn:{partition}:redshift:{region}:{account}:cluster:{cluster_id}"


def _database_name_arn(cluster_arn: str, cluster_id: str, database: str) -> str:
    parts = cluster_arn.split(":")
    return f"arn:{parts[1]}:redshift:{parts[3]}:{parts[4]}:dbname:{cluster_id}/{database}"


def _data_api_grants(resource_arn: str) -> list[Grant]:
    return [
        Grant(
            resource_arn,
            [
                "redshift-data:BatchExecuteStatement",
                "redshift-data:DescribeTable",
                "redshift-data:ExecuteStatement",
                "redshift-data:ListDatabases",
                "redshift-data:ListSchemas",
                "redshift-data:ListTables",
            ],
        ),
        Grant(
            "*",
            [
                "redshift-data:CancelStatement",
                "redshift-data:DescribeStatement",
                "redshift-data:GetStatementResult",
                "redshift-data:ListStatements",
            ],
        ),
    ]


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
        or code in {"ClusterNotReady", "InsufficientClusterCapacity", "ServiceUnavailable"}
    )


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(
        False,
        handle,
        f"{operation}: {exc}",
        [str(exc)],
        retryable=_retryable_cloud_error(exc),
    )
