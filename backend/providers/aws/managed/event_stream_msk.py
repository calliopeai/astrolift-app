"""Amazon MSK provisioned and Serverless managed-service drivers.

The service-specific portions of ``CreateClusterV2`` are accepted in their
native AWS shape.  That keeps new broker, authentication, monitoring, and
network controls available without waiting for an Astrolift release, while the
driver still owns naming, tags, collision safety, lifecycle polling, bindings,
and destructive-operation guards.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
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

KIND = "event_stream"
_SIZE_INSTANCE = {
    "small": "kafka.m5.large",
    "medium": "kafka.m5.xlarge",
    "large": "kafka.m5.2xlarge",
    "xlarge": "kafka.m5.4xlarge",
}
_SIZE_BROKERS = {"small": 3, "medium": 3, "large": 6, "xlarge": 9}
_UPDATE_OPERATIONS = {
    "broker_count": ("UpdateBrokerCount", "update_broker_count"),
    "broker_storage": ("UpdateBrokerStorage", "update_broker_storage"),
    "broker_type": ("UpdateBrokerType", "update_broker_type"),
    "cluster_configuration": ("UpdateClusterConfiguration", "update_cluster_configuration"),
    "kafka_version": ("UpdateClusterKafkaVersion", "update_cluster_kafka_version"),
    "connectivity": ("UpdateConnectivity", "update_connectivity"),
    "monitoring": ("UpdateMonitoring", "update_monitoring"),
    "rebalancing": ("UpdateRebalancing", "update_rebalancing"),
    "security": ("UpdateSecurity", "update_security"),
    "storage": ("UpdateStorage", "update_storage"),
    "topic": ("UpdateTopic", "update_topic"),
}
_BOOTSTRAP_KEYS = {
    ("iam", "private", False): "BootstrapBrokerStringSaslIam",
    ("iam", "public", False): "BootstrapBrokerStringPublicSaslIam",
    ("iam", "vpc", False): "BootstrapBrokerStringVpcConnectivitySaslIam",
    ("iam", "private", True): "BootstrapBrokerStringSaslIamIpv6",
    ("scram", "private", False): "BootstrapBrokerStringSaslScram",
    ("scram", "public", False): "BootstrapBrokerStringPublicSaslScram",
    ("scram", "vpc", False): "BootstrapBrokerStringVpcConnectivitySaslScram",
    ("scram", "private", True): "BootstrapBrokerStringSaslScramIpv6",
    ("tls", "private", False): "BootstrapBrokerStringTls",
    ("tls", "public", False): "BootstrapBrokerStringPublicTls",
    ("tls", "vpc", False): "BootstrapBrokerStringVpcConnectivityTls",
    ("tls", "private", True): "BootstrapBrokerStringTlsIpv6",
    ("plaintext", "private", False): "BootstrapBrokerString",
    ("plaintext", "private", True): "BootstrapBrokerStringIpv6",
}
_UPDATE_PLAN_TAG = "astrolift.io/update-plan-sha256"


@dataclass(frozen=True)
class MSKConfig(CredentialedConfig):
    region: str
    account_id: str
    cluster_name_prefix: str = "astrolift"
    subnet_ids: tuple[str, ...] = ()
    security_group_ids: tuple[str, ...] = ()
    kms_key_arn: str = ""
    kafka_version_default: str = ""
    deletion_protection_default: bool = True
    poll_delay_seconds: float = 15
    max_poll_attempts: int = 80


class MSKDriver(ManagedServiceDriver):
    cluster_type = "PROVISIONED"

    def __init__(
        self,
        *,
        config: MSKConfig,
        client: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._config = config
        if client is None:
            client = aws_client("kafka", region=config.region, credential=config.credential)
        self._msk = client
        self._sleep = sleep

    @driver_op(
        cloud="aws",
        driver="event_stream_msk",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg, size=spec.size)
        if error:
            return ProvisionResult(False, "", error, ["invalid_msk_config"])
        name = self._cluster_name(spec)
        request = self._create_request(name, spec)
        created = False
        arn = ""
        try:
            response = self._msk.create_cluster_v2(**request)
            arn = str(response.get("ClusterArn") or "")
            created = True
        except Exception as exc:
            if not _already_exists(exc):
                return ProvisionResult(False, "", f"create MSK cluster: {exc}", [str(exc)])
            existing = self._find_cluster(name)
            arn = str(existing.get("ClusterArn") or "")
            if not arn:
                return ProvisionResult(
                    False,
                    "",
                    f"MSK reported cluster {name} already exists but it could not be discovered",
                    ["cluster_collision_not_discoverable"],
                )
        handle = handle_for(kind=KIND, resource_id=arn) if arn else ""
        try:
            cluster = self._await_state(arn, {"ACTIVE"})
            if not created and not self._is_managed(arn, spec):
                raise ManagedServiceError(
                    f"MSK cluster {name} already exists outside this resource declaration",
                )
            self._verify_cluster_type(cluster)
            self._tag(arn, spec)
            self._apply_update_operations(arn, cfg)
            self._reconcile_associations(arn, cfg)
        except Exception as exc:
            action = "configure new" if created else "reconcile existing"
            return ProvisionResult(False, handle, f"{action} MSK cluster: {exc}", [str(exc)])
        return ProvisionResult(True, handle, f"MSK {self.cluster_type.lower()} cluster {name} available", ready=True)

    @driver_op(cloud="aws", driver="event_stream_msk")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, arn = parse_handle(spec.handle)
        cfg = spec.config or {}
        error = self._validate_config(cfg, size=spec.size or "custom", partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_msk_config"])
        try:
            cluster = self._cluster(arn)
            self._verify_cluster_type(cluster)
            self._apply_update_operations(arn, cfg)
            self._reconcile_associations(arn, cfg)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, "MSK cluster not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update MSK cluster: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, "MSK cluster reconciled")

    @driver_op(
        cloud="aws",
        driver="event_stream_msk",
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
        _, arn = parse_handle(spec.handle)
        try:
            cluster = self._cluster(arn)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, "MSK cluster already gone")
            return _deprovision_error(spec.handle, "describe MSK cluster", exc)
        if bool(spec.config.get("deletion_protection", self._config.deletion_protection_default)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "MSK cluster has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                (
                    "MSK has no cluster snapshot API; deleting it can orphan or destroy retained "
                    "broker data, so set delete_data=true"
                ),
                ["retained_cluster_data_requires_delete_data"],
                retryable=False,
            )
        try:
            request: dict[str, Any] = {"ClusterArn": arn}
            current_version = str(cluster.get("CurrentVersion") or "")
            if current_version:
                request["CurrentVersion"] = current_version
            self._msk.delete_cluster(**request)
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete MSK cluster", exc)
        return DeprovisionResult(True, spec.handle, "MSK cluster deletion queued")

    @driver_op(cloud="aws", driver="event_stream_msk")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, arn = parse_handle(handle.handle)
        try:
            cluster = self._cluster(arn)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", "MSK cluster does not exist")
            return ServiceStatus(handle.handle, "error", f"describe MSK cluster: {exc}")
        provider_state = str(cluster.get("State") or "UNKNOWN")
        state = {
            "ACTIVE": "available",
            "CREATING": "provisioning",
            "UPDATING": "updating",
            "HEALING": "updating",
            "MAINTENANCE": "updating",
            "REBOOTING_BROKER": "updating",
            "DELETING": "deprovisioning",
        }.get(provider_state, "error")
        return ServiceStatus(handle.handle, state, f"MSK reports {provider_state}")

    @driver_op(cloud="aws", driver="event_stream_msk")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, arn = parse_handle(handle.handle)
        cfg = config or {}
        cluster = self._cluster(arn)
        self._verify_cluster_type(cluster)
        bootstrap = dict(self._msk.get_bootstrap_brokers(ClusterArn=arn))
        auth = str(cfg.get("auth_mode") or self._infer_auth(cluster))
        scope = str(cfg.get("bootstrap_scope") or "private")
        ipv6 = bool(cfg.get("bootstrap_ipv6", False))
        key = _BOOTSTRAP_KEYS.get((auth, scope, ipv6))
        brokers = str(bootstrap.get(key or "") or "")
        if not brokers:
            available = sorted(k for k, value in bootstrap.items() if k.startswith("BootstrapBrokerString") and value)
            raise ManagedServiceError(
                f"MSK did not return {key or 'a compatible bootstrap broker string'}; available={available}",
            )
        name = str(cluster.get("ClusterName") or _cluster_name_from_arn(arn))
        env_vars = {
            "EVENT_STREAM_BROKERS": ValueRef(literal=brokers),
            "EVENT_STREAM_TLS": ValueRef(literal="false" if auth == "plaintext" else "true"),
            "EVENT_STREAM_AUTH_MECHANISM": ValueRef(literal=auth),
            "AWS_REGION": ValueRef(literal=self._config.region),
            "MSK_CLUSTER_ARN": ValueRef(literal=arn),
            "MSK_CLUSTER_NAME": ValueRef(literal=name),
            "MSK_BOOTSTRAP_BROKERS": ValueRef(literal=brokers),
            "MSK_AUTH_MECHANISM": ValueRef(literal=auth),
        }
        if cfg.get("username_secret_ref"):
            env_vars["EVENT_STREAM_USERNAME"] = ValueRef(secret_ref=str(cfg["username_secret_ref"]))
        if cfg.get("password_secret_ref"):
            env_vars["EVENT_STREAM_PASSWORD"] = ValueRef(secret_ref=str(cfg["password_secret_ref"]))
        if cfg.get("client_certificate_secret_ref"):
            env_vars["EVENT_STREAM_CLIENT_CERT"] = ValueRef(
                secret_ref=str(cfg["client_certificate_secret_ref"]),
            )
        if cfg.get("client_key_secret_ref"):
            env_vars["EVENT_STREAM_CLIENT_KEY"] = ValueRef(secret_ref=str(cfg["client_key_secret_ref"]))
        if cfg.get("ca_certificate_secret_ref"):
            env_vars["EVENT_STREAM_CA_CERT"] = ValueRef(secret_ref=str(cfg["ca_certificate_secret_ref"]))
        grants = self._binding_grants(arn, cluster, cfg, auth=auth)
        return Binding(
            env_vars=env_vars,
            iam_grants=grants,
            notes=f"Amazon MSK {self.cluster_type.lower()} Kafka cluster using {auth}",
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError(
            "Amazon MSK has no cluster snapshot API; use Kafka replication or an archival sink",
        )

    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise ManagedServiceError("Amazon MSK clusters cannot be restored from a service snapshot")

    @driver_op(cloud="aws", driver="event_stream_msk", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        native = {"type": "object", "additionalProperties": True}
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "provisioned": native,
                "serverless": native,
                "kafka_version": {"type": "string"},
                "instance_type": {"type": "string"},
                "broker_count": {"type": "integer", "minimum": 1},
                "volume_size_gib": {"type": "integer", "minimum": 1},
                "client_subnets": {"type": "array", "items": {"type": "string"}},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "client_authentication": native,
                "encryption_info": native,
                "enhanced_monitoring": {"type": "string"},
                "open_monitoring": native,
                "logging_info": native,
                "storage_mode": {"type": "string"},
                "configuration_info": native,
                "rebalancing": native,
                "update_operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation": {"type": "string", "enum": sorted(_UPDATE_OPERATIONS)},
                            "parameters": native,
                        },
                        "required": ["operation", "parameters"],
                        "additionalProperties": False,
                    },
                },
                "scram_secret_arns": {"type": "array", "items": {"type": "string"}},
                "prune_scram_secrets": {"type": "boolean", "default": False},
                "resource_policy": {"type": ["object", "null"]},
                "deletion_protection": {"type": "boolean", "default": True},
                "auth_mode": {"type": "string", "enum": ["iam", "scram", "tls", "plaintext"]},
                "bootstrap_scope": {"type": "string", "enum": ["private", "public", "vpc"]},
                "bootstrap_ipv6": {"type": "boolean"},
                "username_secret_ref": {"type": "string"},
                "password_secret_ref": {"type": "string"},
                "client_certificate_secret_ref": {"type": "string"},
                "client_key_secret_ref": {"type": "string"},
                "ca_certificate_secret_ref": {"type": "string"},
                "access_mode": {"type": "string", "enum": ["read", "write", "read_write", "manage"]},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="aws", driver="event_stream_msk", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EVENT_STREAM_BROKERS": "Portable Kafka bootstrap broker list",
                "EVENT_STREAM_USERNAME": "Portable Kafka username secret, when configured",
                "EVENT_STREAM_PASSWORD": "Portable Kafka password secret, when configured",
                "EVENT_STREAM_TLS": "Whether Kafka transport uses TLS",
                "EVENT_STREAM_AUTH_MECHANISM": "iam, scram, tls, or plaintext",
                "EVENT_STREAM_CLIENT_CERT": "mTLS client certificate secret, when configured",
                "EVENT_STREAM_CLIENT_KEY": "mTLS client private key secret, when configured",
                "EVENT_STREAM_CA_CERT": "trusted CA certificate secret, when configured",
                "AWS_REGION": "AWS region",
                "MSK_CLUSTER_ARN": "Amazon MSK cluster ARN",
                "MSK_CLUSTER_NAME": "Amazon MSK cluster name",
                "MSK_BOOTSTRAP_BROKERS": "Selected Amazon MSK bootstrap broker list",
                "MSK_AUTH_MECHANISM": "Selected Amazon MSK authentication mechanism",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "auth_mode",
            "bootstrap_scope",
            "bootstrap_ipv6",
            "username_secret_ref",
            "password_secret_ref",
            "client_certificate_secret_ref",
            "client_key_secret_ref",
            "ca_certificate_secret_ref",
            "deletion_protection",
            "update_operations",
            "scram_secret_arns",
            "prune_scram_secrets",
            "resource_policy",
        ]

    def _create_request(self, name: str, spec: ProvisionSpec) -> dict[str, Any]:
        cfg = spec.config or {}
        request: dict[str, Any] = {
            "ClusterName": name,
            "Tags": _tag_map(spec),
        }
        if self.cluster_type == "SERVERLESS":
            request["Serverless"] = dict(cfg.get("serverless") or self._serverless_request(cfg))
        else:
            request["Provisioned"] = dict(cfg.get("provisioned") or self._provisioned_request(cfg, spec.size))
        return request

    def _provisioned_request(self, cfg: dict[str, Any], size: str) -> dict[str, Any]:
        subnets = list(cfg.get("client_subnets") or self._config.subnet_ids)
        security_groups = list(cfg.get("security_group_ids") or self._config.security_group_ids)
        broker_info: dict[str, Any] = {
            "ClientSubnets": subnets,
            "InstanceType": str(cfg.get("instance_type") or _SIZE_INSTANCE.get(size) or ""),
        }
        if security_groups:
            broker_info["SecurityGroups"] = security_groups
        if "volume_size_gib" in cfg:
            broker_info["StorageInfo"] = {"EbsStorageInfo": {"VolumeSize": int(cfg["volume_size_gib"])}}
        version = str(cfg.get("kafka_version") or self._config.kafka_version_default)
        request: dict[str, Any] = {
            "BrokerNodeGroupInfo": broker_info,
            "KafkaVersion": version,
            "NumberOfBrokerNodes": int(cfg.get("broker_count") or _default_broker_count(size, len(subnets))),
            "ClientAuthentication": dict(
                cfg.get("client_authentication") or {"Sasl": {"Iam": {"Enabled": True}}},
            ),
        }
        encryption = dict(cfg.get("encryption_info") or {})
        if not encryption:
            encryption = {"EncryptionInTransit": {"ClientBroker": "TLS", "InCluster": True}}
            if self._config.kms_key_arn:
                encryption["EncryptionAtRest"] = {"DataVolumeKMSKeyId": self._config.kms_key_arn}
        request["EncryptionInfo"] = encryption
        for config_key, aws_key in (
            ("configuration_info", "ConfigurationInfo"),
            ("rebalancing", "Rebalancing"),
            ("enhanced_monitoring", "EnhancedMonitoring"),
            ("open_monitoring", "OpenMonitoring"),
            ("logging_info", "LoggingInfo"),
            ("storage_mode", "StorageMode"),
        ):
            if config_key in cfg:
                request[aws_key] = cfg[config_key]
        return request

    def _serverless_request(self, cfg: dict[str, Any]) -> dict[str, Any]:
        subnets = list(cfg.get("client_subnets") or self._config.subnet_ids)
        security_groups = list(cfg.get("security_group_ids") or self._config.security_group_ids)
        vpc: dict[str, Any] = {"SubnetIds": subnets}
        if security_groups:
            vpc["SecurityGroupIds"] = security_groups
        return {
            "VpcConfigs": [vpc],
            "ClientAuthentication": {"Sasl": {"Iam": {"Enabled": True}}},
        }

    def _apply_update_operations(self, arn: str, cfg: dict[str, Any]) -> None:
        operations = list(cfg.get("update_operations") or [])
        if not operations:
            return
        digest = hashlib.sha256(
            json.dumps(operations, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        tags = dict(self._msk.list_tags_for_resource(ResourceArn=arn).get("Tags") or {})
        if tags.get(_UPDATE_PLAN_TAG) == digest:
            return
        for item in operations:
            operation = str(item["operation"])
            api_operation, method_name = _UPDATE_OPERATIONS[operation]
            parameters = dict(item.get("parameters") or {})
            parameters["ClusterArn"] = arn
            if "CurrentVersion" in _operation_members(api_operation):
                cluster = self._await_state(arn, {"ACTIVE"})
                parameters["CurrentVersion"] = str(cluster.get("CurrentVersion") or "")
            _validate_request(api_operation, parameters)
            getattr(self._msk, method_name)(**parameters)
            if operation != "topic":
                self._await_state(arn, {"ACTIVE"})
        self._msk.tag_resource(ResourceArn=arn, Tags={_UPDATE_PLAN_TAG: digest})

    def _reconcile_associations(self, arn: str, cfg: dict[str, Any]) -> None:
        if "scram_secret_arns" in cfg:
            desired = {str(value) for value in cfg.get("scram_secret_arns") or []}
            current = set(self._scram_secrets(arn))
            add = sorted(desired - current)
            remove = sorted(current - desired) if cfg.get("prune_scram_secrets") else []
            for secret_batch in _batches(add, 10):
                response = self._msk.batch_associate_scram_secret(
                    ClusterArn=arn,
                    SecretArnList=secret_batch,
                )
                _raise_unprocessed_scram(response, operation="associate")
            for secret_batch in _batches(remove, 10):
                response = self._msk.batch_disassociate_scram_secret(
                    ClusterArn=arn,
                    SecretArnList=secret_batch,
                )
                _raise_unprocessed_scram(response, operation="disassociate")
        if "resource_policy" in cfg:
            policy = cfg["resource_policy"]
            if policy is None:
                try:
                    self._msk.delete_cluster_policy(ClusterArn=arn)
                except Exception as exc:
                    if not _not_found(exc):
                        raise
            else:
                request: dict[str, Any] = {
                    "ClusterArn": arn,
                    "Policy": json.dumps(policy, sort_keys=True, separators=(",", ":")),
                }
                existing: dict[str, Any] = {}
                try:
                    existing = self._msk.get_cluster_policy(ClusterArn=arn)
                except Exception as exc:
                    if not _not_found(exc):
                        raise
                else:
                    if existing.get("CurrentVersion"):
                        request["CurrentVersion"] = str(existing["CurrentVersion"])
                desired_policy = request["Policy"]
                current_policy = _canonical_policy(existing.get("Policy"))
                if desired_policy != current_policy:
                    self._msk.put_cluster_policy(**request)

    def _binding_grants(
        self,
        arn: str,
        cluster: dict[str, Any],
        cfg: dict[str, Any],
        *,
        auth: str,
    ) -> list[Grant]:
        mode = str(cfg.get("access_mode") or "read_write")
        grants = [Grant(arn, ["kafka:DescribeClusterV2", "kafka:GetBootstrapBrokers"])]
        if mode == "manage":
            grants[0] = Grant(arn, ["kafka:*"])
        if auth == "iam":
            name = str(cluster.get("ClusterName") or _cluster_name_from_arn(arn))
            cluster_uuid = arn.rsplit("/", 1)[-1]
            prefix = arn.split(":cluster/", 1)[0]
            cluster_resource = f"{prefix}:cluster/{name}/{cluster_uuid}"
            topic_resource = f"{prefix}:topic/{name}/{cluster_uuid}/*"
            group_resource = f"{prefix}:group/{name}/{cluster_uuid}/*"
            txn_resource = f"{prefix}:transactional-id/{name}/{cluster_uuid}/*"
            if mode == "manage":
                grants.extend(
                    [
                        Grant(cluster_resource, ["kafka-cluster:*"]),
                        Grant(topic_resource, ["kafka-cluster:*"]),
                        Grant(group_resource, ["kafka-cluster:*"]),
                        Grant(txn_resource, ["kafka-cluster:*"]),
                    ],
                )
            else:
                cluster_actions = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster"]
                if mode in {"write", "read_write"}:
                    # WriteDataIdempotently is a cluster-level permission,
                    # not a topic permission (AWS MSK IAM semantics).
                    cluster_actions.append("kafka-cluster:WriteDataIdempotently")
                grants.append(Grant(cluster_resource, cluster_actions))
                if mode in {"read", "read_write"}:
                    grants.extend(
                        [
                            Grant(topic_resource, ["kafka-cluster:DescribeTopic", "kafka-cluster:ReadData"]),
                            Grant(group_resource, ["kafka-cluster:DescribeGroup", "kafka-cluster:AlterGroup"]),
                        ],
                    )
                if mode in {"write", "read_write"}:
                    grants.extend(
                        [
                            Grant(
                                topic_resource,
                                [
                                    "kafka-cluster:DescribeTopic",
                                    "kafka-cluster:WriteData",
                                ],
                            ),
                            Grant(
                                txn_resource,
                                ["kafka-cluster:DescribeTransactionalId", "kafka-cluster:AlterTransactionalId"],
                            ),
                        ],
                    )
        return _merge_grants(grants)

    def _validate_config(
        self,
        cfg: dict[str, Any],
        *,
        size: str,
        partial: bool = False,
    ) -> str:
        if self._config.poll_delay_seconds < 0 or self._config.max_poll_attempts < 1:
            return "MSK polling defaults require a non-negative delay and at least one attempt"
        allowed = set(self.config_schema()["properties"])
        unknown = sorted(set(cfg) - allowed)
        if unknown:
            return f"unsupported MSK config fields: {unknown}"
        if cfg.get("access_mode", "read_write") not in {"read", "write", "read_write", "manage"}:
            return "access_mode must be read, write, read_write, or manage"
        if cfg.get("auth_mode", "iam") not in {"iam", "scram", "tls", "plaintext"}:
            return "auth_mode must be iam, scram, tls, or plaintext"
        if cfg.get("bootstrap_scope", "private") not in {"private", "public", "vpc"}:
            return "bootstrap_scope must be private, public, or vpc"
        bootstrap_key = (
            str(cfg.get("auth_mode") or "iam"),
            str(cfg.get("bootstrap_scope") or "private"),
            bool(cfg.get("bootstrap_ipv6", False)),
        )
        if bootstrap_key not in _BOOTSTRAP_KEYS:
            return "the requested auth_mode, bootstrap_scope, and bootstrap_ipv6 combination is unavailable"
        if "deletion_protection" in cfg and not isinstance(cfg["deletion_protection"], bool):
            return "deletion_protection must be a boolean"
        if (
            "resource_policy" in cfg
            and cfg["resource_policy"] is not None
            and not isinstance(
                cfg["resource_policy"],
                dict,
            )
        ):
            return "resource_policy must be an object or null"
        if "scram_secret_arns" in cfg:
            secrets = cfg["scram_secret_arns"]
            if not isinstance(secrets, list) or any(not isinstance(value, str) or not value for value in secrets):
                return "scram_secret_arns must be an array of non-empty strings"
            if len(secrets) != len(set(secrets)):
                return "scram_secret_arns cannot contain duplicates"
        for secret_ref_key in (
            "username_secret_ref",
            "password_secret_ref",
            "client_certificate_secret_ref",
            "client_key_secret_ref",
            "ca_certificate_secret_ref",
        ):
            if secret_ref_key in cfg and (not isinstance(cfg[secret_ref_key], str) or not cfg[secret_ref_key]):
                return f"{secret_ref_key} must be a non-empty string"
        if self.cluster_type == "SERVERLESS" and "provisioned" in cfg:
            return "MSK Serverless cannot use provisioned configuration"
        if self.cluster_type == "PROVISIONED" and "serverless" in cfg:
            return "MSK provisioned cannot use serverless configuration"
        if self.cluster_type == "SERVERLESS" and cfg.get("auth_mode") not in (None, "iam"):
            return "MSK Serverless supports IAM authentication only"
        operations = cfg.get("update_operations", [])
        if not isinstance(operations, list):
            return "update_operations must be an array"
        try:
            for item in operations:
                if not isinstance(item, dict) or item.get("operation") not in _UPDATE_OPERATIONS:
                    return f"update operation must be one of {sorted(_UPDATE_OPERATIONS)}"
                if not isinstance(item.get("parameters"), dict):
                    return "update operation parameters must be an object"
                operation_name = _UPDATE_OPERATIONS[str(item["operation"])][0]
                parameters = dict(item["parameters"])
                parameters["ClusterArn"] = "arn:aws:kafka:us-east-1:123456789012:cluster/example/id"
                if "CurrentVersion" in _operation_members(operation_name):
                    parameters["CurrentVersion"] = "K1"
                _validate_request(operation_name, parameters)
            if not partial:
                _validate_request(
                    "CreateClusterV2",
                    self._create_request(
                        "astrolift-validation",
                        ProvisionSpec(
                            organization_id="validation",
                            organization_slug="validation",
                            app_id="validation",
                            app_slug="validation",
                            environment_id="validation",
                            environment_name="validation",
                            tenant_cluster_id="validation",
                            service_handle_hint="validation",
                            size=size,
                            config=cfg,
                        ),
                    ),
                )
        except Exception as exc:
            return f"invalid AWS MSK request structure: {exc}"
        if not partial and self.cluster_type == "PROVISIONED" and not cfg.get("provisioned"):
            if not (cfg.get("kafka_version") or self._config.kafka_version_default):
                return "MSK provisioned requires kafka_version or an operator kafka_version_default"
            if not (cfg.get("client_subnets") or self._config.subnet_ids):
                return "MSK provisioned requires client_subnets"
            if not (cfg.get("instance_type") or _SIZE_INSTANCE.get(size)):
                return "custom MSK provisioned size requires instance_type"
            subnet_count = len(cfg.get("client_subnets") or self._config.subnet_ids)
            broker_count = int(cfg.get("broker_count") or _default_broker_count(size, subnet_count))
            if subnet_count and broker_count % subnet_count:
                return "MSK broker_count must be a multiple of the number of client_subnets"
        if (
            not partial
            and self.cluster_type == "SERVERLESS"
            and not cfg.get("serverless")
            and not (cfg.get("client_subnets") or self._config.subnet_ids)
        ):
            return "MSK Serverless requires client_subnets"
        return ""

    def _cluster(self, arn: str) -> dict[str, Any]:
        response = self._msk.describe_cluster_v2(ClusterArn=arn)
        return dict(response.get("ClusterInfo") or {})

    def _find_cluster(self, name: str) -> dict[str, Any]:
        token = ""
        while True:
            request: dict[str, Any] = {"ClusterNameFilter": name, "MaxResults": 100}
            if token:
                request["NextToken"] = token
            response = self._msk.list_clusters_v2(**request)
            for cluster in response.get("ClusterInfoList") or []:
                if cluster.get("ClusterName") == name:
                    return dict(cluster)
            token = str(response.get("NextToken") or "")
            if not token:
                return {}

    def _await_state(self, arn: str, desired: set[str]) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for attempt in range(self._config.max_poll_attempts):
            last = self._cluster(arn)
            state = str(last.get("State") or "UNKNOWN")
            if state in desired:
                return last
            if state in {"FAILED", "CRITICAL_ACTION_REQUIRED"}:
                detail = (last.get("StateInfo") or {}).get("Code") or ""
                raise ManagedServiceError(f"MSK entered terminal state {state}: {detail}")
            if attempt + 1 < self._config.max_poll_attempts:
                self._sleep(self._config.poll_delay_seconds)
        raise ManagedServiceError(
            f"MSK did not reach {sorted(desired)}; last state was {last.get('State', 'UNKNOWN')}",
        )

    def _scram_secrets(self, arn: str) -> list[str]:
        secrets: list[str] = []
        token = ""
        while True:
            request: dict[str, Any] = {"ClusterArn": arn, "MaxResults": 100}
            if token:
                request["NextToken"] = token
            response = self._msk.list_scram_secrets(**request)
            secrets.extend(str(value) for value in response.get("SecretArnList") or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return secrets

    def _tag(self, arn: str, spec: ProvisionSpec) -> None:
        self._msk.tag_resource(ResourceArn=arn, Tags=_tag_map(spec))

    def _is_managed(self, arn: str, spec: ProvisionSpec) -> bool:
        """Platform-made is not enough: it must be this service's (#1961)."""
        response = self._msk.list_tags_for_resource(ResourceArn=arn)
        tags = response.get("Tags") or {}
        return tags.get("astrolift.io/managed-by") == "platform" and (
            adoption_refusal(tags, spec, resource="MSK cluster") is None
        )

    def _verify_cluster_type(self, cluster: dict[str, Any]) -> None:
        live = str(cluster.get("ClusterType") or "")
        if live and live != self.cluster_type:
            raise ManagedServiceError(
                f"live MSK cluster type {live} does not match requested {self.cluster_type}; reprovision is required",
            )

    def _infer_auth(self, cluster: dict[str, Any]) -> str:
        details = cluster.get("Provisioned") or cluster.get("Serverless") or {}
        auth = details.get("ClientAuthentication") or {}
        sasl = auth.get("Sasl") or {}
        if (sasl.get("Iam") or {}).get("Enabled"):
            return "iam"
        if (sasl.get("Scram") or {}).get("Enabled"):
            return "scram"
        if (auth.get("Tls") or {}).get("Enabled"):
            return "tls"
        return "plaintext"

    def _cluster_name(self, spec: ProvisionSpec) -> str:
        return _name(
            "-".join(
                part
                for part in (
                    self._config.cluster_name_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint or "kafka",
                )
                if part
            ),
        )


class MSKProvisionedDriver(MSKDriver):
    cluster_type = "PROVISIONED"


class MSKServerlessDriver(MSKDriver):
    cluster_type = "SERVERLESS"


def _validate_request(operation: str, request: dict[str, Any]) -> None:
    from botocore.session import Session
    from botocore.validate import validate_parameters

    service = Session().get_service_model("kafka")
    validate_parameters(request, service.operation_model(operation).input_shape)


def _operation_members(operation: str) -> set[str]:
    from botocore.session import Session

    service = Session().get_service_model("kafka")
    return set(service.operation_model(operation).input_shape.members)


def _tag_map(spec: ProvisionSpec) -> dict[str, str]:
    return {tag["Key"]: tag["Value"] for tag in tags_for(spec)}


def _batches(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _raise_unprocessed_scram(response: dict[str, Any], *, operation: str) -> None:
    failures = list(response.get("UnprocessedScramSecrets") or [])
    if not failures:
        return
    details = "; ".join(
        f"{item.get('SecretArn', 'unknown')}: {item.get('ErrorCode', 'error')} {item.get('ErrorMessage', '')}".strip()
        for item in failures
    )
    raise ManagedServiceError(f"MSK could not {operation} SCRAM secrets: {details}")


def _canonical_policy(value: Any) -> str:
    if not value:
        return ""
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return str(value)
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def _merge_grants(grants: list[Grant]) -> list[Grant]:
    """Coalesce control-plane and IAM Kafka grants on the cluster ARN."""

    actions_by_resource: dict[str, set[str]] = {}
    for grant in grants:
        actions_by_resource.setdefault(grant.resource, set()).update(grant.actions)
    return [Grant(resource=resource, actions=sorted(actions)) for resource, actions in actions_by_resource.items()]


def _cluster_name_from_arn(arn: str) -> str:
    resource = arn.split(":cluster/", 1)[-1]
    return resource.split("/", 1)[0]


def _default_broker_count(size: str, subnet_count: int) -> int:
    base = _SIZE_BROKERS.get(size) or 3
    if subnet_count < 1:
        return base
    return max(subnet_count, ((base + subnet_count - 1) // subnet_count) * subnet_count)


def _name(value: str) -> str:
    clean = "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-_")
    if not clean:
        raise ManagedServiceError("MSK cluster name cannot be empty")
    return clean[:64].rstrip("-_")


def _already_exists(exc: Exception) -> bool:
    code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
    message = str(exc).lower()
    return code in {"ConflictException", "ResourceInUseException", "BadRequestException"} and "exist" in message


def _not_found(exc: Exception) -> bool:
    code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
    return (
        code
        in {
            "NotFoundException",
            "ResourceNotFoundException",
            "ResourceNotFound",
        }
        or "not found" in str(exc).lower()
    )


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
    retryable = code not in {"BadRequestException", "ForbiddenException", "UnauthorizedException"}
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [code or str(exc)], retryable=retryable)
