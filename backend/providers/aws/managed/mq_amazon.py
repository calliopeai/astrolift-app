"""Amazon MQ managed brokers for RabbitMQ and ActiveMQ.

Broker API payloads stay AWS-native, except passwords: plaintext passwords are
never accepted in a managed-service document. User descriptors point at an
existing Secrets Manager value (optionally ``#field`` within a JSON bundle), or
the driver creates a deterministic managed password secret.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import string
import time
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
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

KIND = "mq"
_UPDATE_PLAN_TAG = "astrolift.io/update-plan-sha256"
_PASSWORD_SPECIAL = "!#$%&()*+-.;<>?[]^_{}~"
_PASSWORD_ALPHABET = string.ascii_letters + string.digits + _PASSWORD_SPECIAL
_ACTIVEMQ_USER_RE = re.compile(r"^[A-Za-z0-9._~-]{2,100}$")
_RABBITMQ_USER_RE = re.compile(r"^[A-Za-z0-9._-]{2,100}$")
_ACTIVEMQ_GROUP_RE = re.compile(r"^[A-Za-z0-9._~-]{2,100}$")
_SIZE_INSTANCE = {
    "small": "mq.t3.micro",
    "medium": "mq.m5.large",
    "large": "mq.m5.xlarge",
    "xlarge": "mq.m5.2xlarge",
}
_PROTOCOL_SCHEMES = {
    "amqp": ("amqp+ssl", "amqps"),
    "amqps": ("amqps", "amqp+ssl"),
    "openwire": ("ssl",),
    "stomp": ("stomp+ssl",),
    "mqtt": ("mqtt+ssl",),
    "wss": ("wss",),
}


@dataclass(frozen=True)
class AmazonMQConfig:
    region: str
    account_id: str
    broker_name_prefix: str = "astrolift"
    subnet_ids: tuple[str, ...] = ()
    security_group_ids: tuple[str, ...] = ()
    kms_key_id: str = ""
    engine_version_default: str = ""
    secrets_manager_prefix: str = "astrolift/mq"
    admin_username: str = "astrolift"
    deletion_protection_default: bool = True
    poll_delay_seconds: float = 10
    max_poll_attempts: int = 90


class AmazonMQDriver(ManagedServiceDriver):
    engine_type = "RABBITMQ"

    def __init__(
        self,
        *,
        config: AmazonMQConfig,
        client: Any | None = None,
        secrets_client: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._config = config
        if client is None or secrets_client is None:
            import boto3

            client = client or boto3.client("mq", region_name=config.region)
            secrets_client = secrets_client or boto3.client("secretsmanager", region_name=config.region)
        self._mq = client
        self._secrets = secrets_client
        self._sleep = sleep

    @driver_op(
        cloud="aws",
        driver="mq_amazon",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg, size=spec.size)
        if error:
            return ProvisionResult(False, "", error, ["invalid_amazon_mq_config"])
        name = self._broker_name(spec)
        existing = self._find_broker(name)
        created = False
        broker_id = str(existing.get("BrokerId") or "")
        broker_arn = str(existing.get("BrokerArn") or "")
        try:
            if broker_id:
                description = self._await_state(broker_id, {"RUNNING", "REPLICA"})
                if not self._is_managed(description):
                    raise ManagedServiceError(
                        f"Amazon MQ broker {name} already exists outside this resource declaration",
                    )
            else:
                users = self._materialize_users(name, cfg, spec=spec)
                request = self._create_request(name, spec, users=users)
                response = self._mq.create_broker(**request)
                broker_id = str(response.get("BrokerId") or "")
                broker_arn = str(response.get("BrokerArn") or "")
                created = True
                description = self._await_state(broker_id, {"RUNNING", "REPLICA"})
            broker_arn = str(description.get("BrokerArn") or broker_arn)
            self._verify_engine(description)
            if not created:
                self._tag(description, _tag_map(spec))
                broker_changed, update_tag = self._apply_broker_update(description, cfg)
                description = self._await_state(broker_id, {"RUNNING", "REPLICA"})
                users_changed = self._reconcile_users(
                    description,
                    cfg,
                    spec=spec,
                    use_default=False,
                )
                self._finish_update(
                    description,
                    cfg,
                    broker_changed=broker_changed,
                    users_changed=users_changed,
                    update_tag=update_tag,
                )
        except Exception as exc:
            resource_id = broker_arn or broker_id
            handle = handle_for(kind=KIND, resource_id=resource_id) if resource_id else ""
            action = "configure new" if created else "reconcile existing"
            return ProvisionResult(False, handle, f"{action} Amazon MQ broker: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=broker_arn or broker_id),
            f"Amazon MQ for {self.engine_type} broker {name} available",
            ready=True,
        )

    @driver_op(cloud="aws", driver="mq_amazon")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, resource_id = parse_handle(spec.handle)
        broker_id = _broker_id(resource_id)
        cfg = spec.config or {}
        error = self._validate_config(cfg, size=spec.size or "custom", partial=True)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_amazon_mq_config"])
        try:
            description = self._description(broker_id)
            self._verify_engine(description)
            broker_changed, update_tag = self._apply_broker_update(description, cfg)
            description = self._await_state(broker_id, {"RUNNING", "REPLICA"})
            users_changed = self._reconcile_users(description, cfg, use_default=False)
            self._finish_update(
                description,
                cfg,
                broker_changed=broker_changed,
                users_changed=users_changed,
                update_tag=update_tag,
            )
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, "Amazon MQ broker not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update Amazon MQ broker: {exc}", [str(exc)])
        message = "Amazon MQ broker reconciled"
        if users_changed and not cfg.get("reboot_after_update"):
            message += "; ActiveMQ user changes apply at the next maintenance window"
        return UpdateResult(True, spec.handle, message)

    @driver_op(
        cloud="aws",
        driver="mq_amazon",
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
        broker_id = _broker_id(resource_id)
        try:
            description = self._description(broker_id)
        except Exception as exc:
            if _not_found(exc):
                self._delete_generated_user_secrets(
                    spec.config or {},
                    broker_id=broker_id,
                    broker_name=_broker_name_from_resource(resource_id),
                )
                return DeprovisionResult(True, spec.handle, "Amazon MQ broker already gone")
            return _deprovision_error(spec.handle, "describe Amazon MQ broker", exc)
        if bool(spec.config.get("deletion_protection", self._config.deletion_protection_default)) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "Amazon MQ broker has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                "Amazon MQ has no broker snapshot API; deletion destroys queued messages, so set delete_data=true",
                ["retained_broker_data_requires_delete_data"],
                retryable=False,
            )
        try:
            self._mq.delete_broker(BrokerId=broker_id)
            self._delete_generated_user_secrets(
                spec.config or {},
                broker_id=broker_id,
                broker_name=str(description.get("BrokerName") or broker_id),
            )
        except Exception as exc:
            return _deprovision_error(spec.handle, "delete Amazon MQ broker", exc)
        return DeprovisionResult(True, spec.handle, "Amazon MQ broker deletion queued")

    @driver_op(cloud="aws", driver="mq_amazon")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, resource_id = parse_handle(handle.handle)
        broker_id = _broker_id(resource_id)
        try:
            description = self._description(broker_id)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", "Amazon MQ broker does not exist")
            return ServiceStatus(handle.handle, "error", f"describe Amazon MQ broker: {exc}")
        provider_state = str(description.get("BrokerState") or "UNKNOWN")
        state = {
            "RUNNING": "available",
            "REPLICA": "available",
            "CREATION_IN_PROGRESS": "provisioning",
            "REBOOT_IN_PROGRESS": "updating",
            "DELETION_IN_PROGRESS": "deprovisioning",
        }.get(provider_state, "error")
        return ServiceStatus(handle.handle, state, f"Amazon MQ reports {provider_state}")

    @driver_op(cloud="aws", driver="mq_amazon")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, resource_id = parse_handle(handle.handle)
        broker_id = _broker_id(resource_id)
        cfg = config or {}
        description = self._description(broker_id)
        self._verify_engine(description)
        endpoints = [
            str(endpoint)
            for instance in description.get("BrokerInstances") or []
            for endpoint in instance.get("Endpoints") or []
        ]
        protocol = str(cfg.get("protocol") or ("amqps" if self.engine_type == "RABBITMQ" else "amqp"))
        endpoint = _select_endpoint(endpoints, protocol)
        if not endpoint:
            raise ManagedServiceError(
                f"Amazon MQ returned no endpoint for protocol {protocol}; available={endpoints}",
            )
        username, password_ref = self._binding_credentials(description, cfg)
        broker_arn = str(description.get("BrokerArn") or "")
        auth_strategy = self._authentication_strategy(cfg, description=description)
        console_urls = [
            str(instance.get("ConsoleURL"))
            for instance in description.get("BrokerInstances") or []
            if instance.get("ConsoleURL")
        ]
        env_vars = {
            "MQ_ENDPOINT": ValueRef(literal=endpoint),
            "MQ_PROTOCOL": ValueRef(literal=protocol),
            "MQ_AUTH_STRATEGY": ValueRef(literal=auth_strategy),
            "AMAZON_MQ_BROKER_ID": ValueRef(literal=broker_id),
            "AMAZON_MQ_BROKER_ARN": ValueRef(literal=broker_arn),
            "AMAZON_MQ_ENGINE_TYPE": ValueRef(literal=self.engine_type),
            "AMAZON_MQ_ENDPOINTS_JSON": ValueRef(literal=json.dumps(endpoints, separators=(",", ":"))),
            "AMAZON_MQ_CONSOLE_URL": ValueRef(literal=console_urls[0] if console_urls else ""),
            "AWS_REGION": ValueRef(literal=self._config.region),
        }
        grants: list[Grant] = []
        if username:
            env_vars["MQ_USERNAME"] = ValueRef(literal=username)
        if password_ref:
            env_vars["MQ_PASSWORD"] = ValueRef(secret_ref=password_ref)
            grants.append(Grant(_secret_resource(password_ref, self._config), ["secretsmanager:GetSecretValue"]))
        if password_ref and self._config.kms_key_id:
            grants.append(Grant(self._config.kms_key_id, ["kms:Decrypt", "kms:DescribeKey"]))
        if cfg.get("access_mode") == "manage" and broker_arn and self.engine_type == "ACTIVEMQ":
            grants.append(Grant(broker_arn, ["mq:DescribeBroker", "mq:ListUsers", "mq:UpdateUser"]))
        return Binding(
            env_vars=env_vars,
            iam_grants=grants,
            notes=f"Amazon MQ {self.engine_type} TLS endpoint using {auth_strategy} authentication",
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError("Amazon MQ has no broker snapshot API")

    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise ManagedServiceError("Amazon MQ brokers cannot be restored from a service snapshot")

    @driver_op(cloud="aws", driver="mq_amazon", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        native = {"type": "object", "additionalProperties": True}
        user = {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "password_secret_ref": {"type": "string"},
                "console_access": {"type": "boolean"},
                "groups": {"type": "array", "items": {"type": "string"}},
                "replication_user": {"type": "boolean"},
            },
            "required": ["username"],
            "additionalProperties": False,
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "broker": native,
                "engine_version": {"type": "string"},
                "host_instance_type": {"type": "string"},
                "deployment_mode": {
                    "type": "string",
                    "enum": ["SINGLE_INSTANCE", "ACTIVE_STANDBY_MULTI_AZ", "CLUSTER_MULTI_AZ"],
                },
                "publicly_accessible": {"type": "boolean"},
                "subnet_ids": {"type": "array", "items": {"type": "string"}},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "users": {"type": "array", "items": user, "minItems": 1},
                "prune_users": {"type": "boolean", "default": False},
                "binding_username": {"type": "string"},
                "binding_password_secret_ref": {"type": "string"},
                "ldap_service_account_password_secret_ref": {"type": "string"},
                "broker_update": native,
                "reboot_after_update": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
                "protocol": {"type": "string", "enum": sorted(_PROTOCOL_SCHEMES)},
                "access_mode": {"type": "string", "enum": ["connect", "manage"]},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="aws", driver="mq_amazon", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MQ_ENDPOINT": "Portable TLS broker endpoint",
                "MQ_USERNAME": "Portable broker username",
                "MQ_PASSWORD": "Portable broker password secret",
                "MQ_PROTOCOL": "amqp, amqps, openwire, stomp, mqtt, or wss",
                "MQ_AUTH_STRATEGY": "SIMPLE, LDAP, or CONFIG_MANAGED",
                "AMAZON_MQ_BROKER_ID": "Amazon MQ broker ID",
                "AMAZON_MQ_BROKER_ARN": "Amazon MQ broker ARN",
                "AMAZON_MQ_ENGINE_TYPE": "RABBITMQ or ACTIVEMQ",
                "AMAZON_MQ_ENDPOINTS_JSON": "All broker endpoints as JSON",
                "AMAZON_MQ_CONSOLE_URL": "Broker management console URL",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "broker_update",
            "reboot_after_update",
            "users",
            "prune_users",
            "binding_username",
            "binding_password_secret_ref",
            "ldap_service_account_password_secret_ref",
            "protocol",
            "access_mode",
            "deletion_protection",
        ]

    def _create_request(
        self,
        name: str,
        spec: ProvisionSpec,
        *,
        users: list[dict[str, Any]],
        validate_only: bool = False,
    ) -> dict[str, Any]:
        cfg = spec.config or {}
        request = dict(cfg.get("broker") or {})
        if self._authentication_strategy(cfg) == "LDAP":
            ldap = dict(request.get("LdapServerMetadata") or {})
            password_ref = str(cfg.get("ldap_service_account_password_secret_ref") or "")
            ldap["ServiceAccountPassword"] = (
                "Astrolift-Validation-Password-123!"
                if validate_only
                else self._read_secret_scalar(
                    password_ref,
                    label="LDAP service-account password",
                )
            )
            request["LdapServerMetadata"] = ldap
        deployment = str(
            cfg.get("deployment_mode") or request.get("DeploymentMode") or self._default_deployment(spec.size)
        )
        subnets = list(cfg.get("subnet_ids") or self._config.subnet_ids)
        required_subnets = _required_subnets(self.engine_type, deployment)
        request.update(
            {
                "BrokerName": name,
                "EngineType": self.engine_type,
                "EngineVersion": str(
                    cfg.get("engine_version") or request.get("EngineVersion") or self._config.engine_version_default
                ),
                "HostInstanceType": str(
                    cfg.get("host_instance_type")
                    or request.get("HostInstanceType")
                    or _SIZE_INSTANCE.get(spec.size)
                    or ""
                ),
                "DeploymentMode": deployment,
                "PubliclyAccessible": bool(
                    cfg.get("publicly_accessible", request.get("PubliclyAccessible", False)),
                ),
                "CreatorRequestId": str(request.get("CreatorRequestId") or _request_token(name)),
                "Tags": _tag_map(spec),
                "Users": users,
            },
        )
        if "SubnetIds" not in request and subnets:
            request["SubnetIds"] = subnets[:required_subnets]
        security_groups = list(cfg.get("security_group_ids") or self._config.security_group_ids)
        if "SecurityGroups" not in request and security_groups:
            request["SecurityGroups"] = security_groups
        request.setdefault("AutoMinorVersionUpgrade", True)
        if "EncryptionOptions" not in request:
            request["EncryptionOptions"] = (
                {"UseAwsOwnedKey": False, "KmsKeyId": self._config.kms_key_id}
                if self._config.kms_key_id
                else {"UseAwsOwnedKey": True}
            )
        return request

    def _apply_broker_update(
        self,
        description: dict[str, Any],
        cfg: dict[str, Any],
    ) -> tuple[bool, tuple[str, str] | None]:
        update = cfg.get("broker_update")
        if not update:
            return False, None
        broker_id = str(description.get("BrokerId") or "")
        broker_arn = str(description.get("BrokerArn") or "")
        request = dict(update)
        request["BrokerId"] = broker_id
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        if (description.get("Tags") or {}).get(_UPDATE_PLAN_TAG) == digest:
            return False, None
        _validate_request("UpdateBroker", request)
        self._mq.update_broker(**request)
        return True, (broker_arn, digest) if broker_arn else None

    def _finish_update(
        self,
        description: dict[str, Any],
        cfg: dict[str, Any],
        *,
        broker_changed: bool,
        users_changed: bool,
        update_tag: tuple[str, str] | None,
    ) -> None:
        broker_id = str(description.get("BrokerId") or "")
        if cfg.get("reboot_after_update") and (broker_changed or users_changed):
            self._mq.reboot_broker(BrokerId=broker_id)
            self._await_state(broker_id, {"RUNNING", "REPLICA"})
        if update_tag is not None:
            broker_arn, digest = update_tag
            self._mq.create_tags(ResourceArn=broker_arn, Tags={_UPDATE_PLAN_TAG: digest})

    def _reconcile_users(
        self,
        description: dict[str, Any],
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None = None,
        use_default: bool = True,
    ) -> bool:
        if "users" not in cfg and not use_default:
            return False
        if self._authentication_strategy(cfg) != "SIMPLE":
            return False
        # AWS's List/Create/Update/DeleteUser API is ActiveMQ-only. A
        # RabbitMQ broker accepts exactly one bootstrap administrator through
        # CreateBroker; subsequent users belong to the RabbitMQ management API.
        if self.engine_type == "RABBITMQ":
            return False
        broker_id = str(description.get("BrokerId") or "")
        broker_name = str(description.get("BrokerName") or broker_id)
        desired = self._user_descriptors(cfg)
        current = {str(user.get("Username")) for user in self._list_users(broker_id) if user.get("Username")}
        requests = self._materialize_users(broker_name, cfg, spec=spec)
        for request in requests:
            username = str(request["Username"])
            payload = {"BrokerId": broker_id, **request}
            operation = "UpdateUser" if username in current else "CreateUser"
            _validate_request(operation, payload)
            getattr(self._mq, "update_user" if username in current else "create_user")(**payload)
        if cfg.get("prune_users"):
            desired_names = {str(user["username"]) for user in desired}
            if not desired_names:
                raise ManagedServiceError("prune_users cannot remove every broker user")
            for username in sorted(current - desired_names):
                self._mq.delete_user(BrokerId=broker_id, Username=username)
        return bool(requests or (cfg.get("prune_users") and current - desired_names))

    def _materialize_users(
        self,
        broker_name: str,
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None,
    ) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        for descriptor in self._user_descriptors(cfg):
            username = str(descriptor["username"])
            password_ref = str(
                descriptor.get("password_secret_ref") or self._generated_secret_name_for(broker_name, username),
            )
            password = self._ensure_password(
                password_ref,
                spec=spec,
                allow_create=not bool(descriptor.get("password_secret_ref")),
            )
            request: dict[str, Any] = {"Username": username, "Password": password}
            for config_key, aws_key in (
                ("console_access", "ConsoleAccess"),
                ("groups", "Groups"),
                ("replication_user", "ReplicationUser"),
            ):
                if config_key in descriptor:
                    request[aws_key] = descriptor[config_key]
            requests.append(request)
        return requests

    def _ensure_password(
        self,
        secret_ref: str,
        *,
        spec: ProvisionSpec | None,
        allow_create: bool,
    ) -> str:
        try:
            return self._read_password(secret_ref)
        except Exception as exc:
            if _pending_deletion(exc) and allow_create:
                secret_id = secret_ref.split("#", 1)[0]
                self._secrets.restore_secret(SecretId=secret_id)
                return self._read_password(secret_ref)
            if not _not_found(exc) or not allow_create:
                raise
        password = _generate_password()
        request: dict[str, Any] = {
            "Name": secret_ref,
            "Description": (
                f"Amazon MQ user password for {spec.app_slug}/{spec.environment_name}"
                if spec is not None
                else "Astrolift-managed Amazon MQ user password"
            ),
            "SecretString": password,
        }
        if spec is not None:
            request["Tags"] = tags_for(spec)
        if self._config.kms_key_id:
            request["KmsKeyId"] = self._config.kms_key_id
        try:
            self._secrets.create_secret(**request)
        except Exception as exc:
            if not _already_exists(exc):
                raise
            return self._read_password(secret_ref)
        return password

    def _read_password(self, secret_ref: str) -> str:
        value = self._read_secret_scalar(secret_ref, label="broker user password")
        error = _password_error(value)
        if error:
            raise ManagedServiceError(f"password secret {secret_ref.split('#', 1)[0]} {error}")
        return value

    def _read_secret_scalar(self, secret_ref: str, *, label: str) -> str:
        if not secret_ref:
            raise ManagedServiceError(f"{label} requires a secret reference")
        secret_id, _, field = secret_ref.partition("#")
        response = self._secrets.get_secret_value(SecretId=secret_id)
        value = str(response.get("SecretString") or "")
        if field:
            payload = json.loads(value)
            if not isinstance(payload, dict) or field not in payload:
                raise ManagedServiceError(f"{label} secret {secret_id} does not contain field {field}")
            selected = payload[field]
            if isinstance(selected, (dict, list)):
                raise ManagedServiceError(f"{label} secret {secret_id}#{field} must contain a scalar")
            value = str(selected)
        else:
            try:
                payload = json.loads(value)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                if "password" in payload:
                    selected = payload["password"]
                elif len(payload) == 1:
                    selected = next(iter(payload.values()))
                else:
                    raise ManagedServiceError(
                        f"{label} secret {secret_id} has multiple fields; select one with #field",
                    )
                if isinstance(selected, (dict, list)):
                    raise ManagedServiceError(f"{label} secret {secret_id} must contain a scalar")
                value = str(selected)
            elif isinstance(payload, list):
                raise ManagedServiceError(f"{label} secret {secret_id} must contain a scalar")
        if not value:
            raise ManagedServiceError(f"{label} secret {secret_id} is empty")
        return value

    def _delete_generated_user_secrets(
        self,
        cfg: dict[str, Any],
        *,
        broker_id: str,
        broker_name: str = "",
    ) -> None:
        name = broker_name or broker_id
        for user in self._user_descriptors(cfg):
            if user.get("password_secret_ref"):
                continue
            secret_name = self._generated_secret_name_for(name, str(user["username"]))
            try:
                self._secrets.delete_secret(SecretId=secret_name, RecoveryWindowInDays=7)
            except Exception as exc:
                if not _not_found(exc):
                    raise

    def _user_descriptors(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        users = cfg.get("users")
        if users is None:
            if self._authentication_strategy(cfg) != "SIMPLE":
                return []
            default = {"username": self._config.admin_username}
            if self.engine_type == "ACTIVEMQ":
                default["console_access"] = True
            return [default]
        return [dict(user) for user in users]

    def _binding_credentials(
        self,
        description: dict[str, Any],
        cfg: dict[str, Any],
    ) -> tuple[str, str]:
        users = self._user_descriptors(cfg)
        requested = str(cfg.get("binding_username") or "")
        explicit_ref = str(cfg.get("binding_password_secret_ref") or "")
        if explicit_ref:
            return requested, explicit_ref
        if requested:
            for user in users:
                if user.get("username") == requested:
                    return requested, str(
                        user.get("password_secret_ref") or self._generated_secret_name(description, requested),
                    )
            raise ManagedServiceError(f"binding_username {requested!r} is not declared in users")
        if not users:
            return "", ""
        user = users[0]
        username = str(user.get("username") or "")
        return username, str(
            user.get("password_secret_ref") or self._generated_secret_name(description, username),
        )

    def _authentication_strategy(
        self,
        cfg: dict[str, Any],
        *,
        description: dict[str, Any] | None = None,
    ) -> str:
        broker = cfg.get("broker") if isinstance(cfg.get("broker"), dict) else {}
        return str(
            broker.get("AuthenticationStrategy") or (description or {}).get("AuthenticationStrategy") or "SIMPLE",
        ).upper()

    def _list_users(self, broker_id: str) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        token = ""
        while True:
            request: dict[str, Any] = {"BrokerId": broker_id, "MaxResults": 100}
            if token:
                request["NextToken"] = token
            response = self._mq.list_users(**request)
            users.extend(dict(user) for user in response.get("Users") or [])
            token = str(response.get("NextToken") or "")
            if not token:
                return users

    def _description(self, broker_id: str) -> dict[str, Any]:
        return dict(self._mq.describe_broker(BrokerId=broker_id))

    def _find_broker(self, name: str) -> dict[str, Any]:
        token = ""
        while True:
            request: dict[str, Any] = {"MaxResults": 100}
            if token:
                request["NextToken"] = token
            response = self._mq.list_brokers(**request)
            for broker in response.get("BrokerSummaries") or []:
                if broker.get("BrokerName") == name:
                    return dict(broker)
            token = str(response.get("NextToken") or "")
            if not token:
                return {}

    def _await_state(self, broker_id: str, desired: set[str]) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for attempt in range(self._config.max_poll_attempts):
            last = self._description(broker_id)
            state = str(last.get("BrokerState") or "UNKNOWN")
            if state in desired:
                return last
            if state in {"CREATION_FAILED", "CRITICAL_ACTION_REQUIRED"}:
                actions = last.get("ActionsRequired") or []
                raise ManagedServiceError(f"Amazon MQ entered terminal state {state}: {actions}")
            if attempt + 1 < self._config.max_poll_attempts:
                self._sleep(self._config.poll_delay_seconds)
        raise ManagedServiceError(
            f"Amazon MQ did not reach {sorted(desired)}; last state was {last.get('BrokerState', 'UNKNOWN')}",
        )

    def _tag(self, description: dict[str, Any], tags: dict[str, str]) -> None:
        arn = str(description.get("BrokerArn") or "")
        if arn:
            self._mq.create_tags(ResourceArn=arn, Tags=tags)

    def _is_managed(self, description: dict[str, Any]) -> bool:
        return (description.get("Tags") or {}).get("astrolift.io/managed-by") == "platform"

    def _verify_engine(self, description: dict[str, Any]) -> None:
        live = str(description.get("EngineType") or "")
        if live and live != self.engine_type:
            raise ManagedServiceError(
                f"live Amazon MQ engine {live} does not match requested {self.engine_type}; reprovision is required",
            )

    def _validate_config(self, cfg: dict[str, Any], *, size: str, partial: bool = False) -> str:
        if self._config.poll_delay_seconds < 0 or self._config.max_poll_attempts < 1:
            return "Amazon MQ polling defaults require a non-negative delay and at least one attempt"
        allowed = set(self.config_schema()["properties"])
        unknown = sorted(set(cfg) - allowed)
        if unknown:
            return f"unsupported Amazon MQ config fields: {unknown}"
        if cfg.get("protocol", "amqps" if self.engine_type == "RABBITMQ" else "amqp") not in _PROTOCOL_SCHEMES:
            return f"protocol must be one of {sorted(_PROTOCOL_SCHEMES)}"
        if cfg.get("access_mode", "connect") not in {"connect", "manage"}:
            return "access_mode must be connect or manage"
        for key in ("deletion_protection", "prune_users", "reboot_after_update", "publicly_accessible"):
            if key in cfg and not isinstance(cfg[key], bool):
                return f"{key} must be a boolean"
        broker = cfg.get("broker", {})
        if not isinstance(broker, dict):
            return "broker must be an object"
        sensitive_path = _sensitive_native_path(broker)
        if sensitive_path:
            return (
                f"broker.{sensitive_path} is forbidden because plaintext passwords cannot be stored in config; "
                "use secret-reference fields"
            )
        ldap_metadata = broker.get("LdapServerMetadata")
        if ldap_metadata is not None and not isinstance(ldap_metadata, dict):
            return "broker.LdapServerMetadata must be an object"
        auth_strategy = self._authentication_strategy(cfg)
        allowed_auth = {"SIMPLE", "CONFIG_MANAGED"} if self.engine_type == "RABBITMQ" else {"SIMPLE", "LDAP"}
        if auth_strategy not in allowed_auth:
            return f"{self.engine_type} AuthenticationStrategy must be one of {sorted(allowed_auth)}"
        ldap_ref = cfg.get("ldap_service_account_password_secret_ref")
        if ldap_ref is not None and (not isinstance(ldap_ref, str) or not ldap_ref):
            return "ldap_service_account_password_secret_ref must be a non-empty string"
        if auth_strategy == "LDAP" and not ldap_ref:
            return "LDAP authentication requires ldap_service_account_password_secret_ref"
        if auth_strategy != "LDAP" and ldap_ref:
            return "ldap_service_account_password_secret_ref is valid only with LDAP authentication"
        binding_password_ref = cfg.get("binding_password_secret_ref")
        if binding_password_ref is not None and (not isinstance(binding_password_ref, str) or not binding_password_ref):
            return "binding_password_secret_ref must be a non-empty string"
        users = cfg.get("users")
        if users is not None:
            if not isinstance(users, list):
                return "users must be an array"
            if not users and auth_strategy == "SIMPLE":
                return "SIMPLE authentication requires a non-empty users array"
            if auth_strategy == "LDAP" and users:
                return "LDAP authentication manages identities externally and cannot declare broker users"
            if self.engine_type == "RABBITMQ" and len(users) > 1:
                return "RabbitMQ accepts exactly one bootstrap administrator during broker creation"
            if partial and self.engine_type == "RABBITMQ":
                return "RabbitMQ users cannot be reconciled through the Amazon MQ API after broker creation"
            names: list[str] = []
            for user in users:
                if not isinstance(user, dict):
                    return "each user must be an object"
                allowed_user = {"username", "password_secret_ref", "console_access", "groups", "replication_user"}
                if set(user) - allowed_user:
                    return f"unsupported user fields: {sorted(set(user) - allowed_user)}"
                username = user.get("username")
                if not isinstance(username, str) or not username:
                    return "each user requires a non-empty username"
                username_pattern = _RABBITMQ_USER_RE if self.engine_type == "RABBITMQ" else _ACTIVEMQ_USER_RE
                if not username_pattern.fullmatch(username):
                    return f"invalid {self.engine_type} username {username!r}"
                if self.engine_type == "RABBITMQ" and username.lower() == "guest":
                    return "RabbitMQ does not permit the guest username"
                names.append(username)
                if "password_secret_ref" in user and (
                    not isinstance(user["password_secret_ref"], str) or not user["password_secret_ref"]
                ):
                    return "password_secret_ref must be a non-empty string"
                if "groups" in user and (
                    not isinstance(user["groups"], list)
                    or any(not isinstance(group, str) or not group for group in user["groups"])
                ):
                    return "user groups must be an array of non-empty strings"
                if self.engine_type == "RABBITMQ" and any(
                    key in user for key in ("console_access", "groups", "replication_user")
                ):
                    return "console_access, groups, and replication_user apply only to ActiveMQ users"
                if self.engine_type == "ACTIVEMQ" and "groups" in user:
                    groups = user["groups"]
                    if len(groups) > 20 or any(not _ACTIVEMQ_GROUP_RE.fullmatch(group) for group in groups):
                        return "ActiveMQ user groups must contain at most 20 valid 2-100 character names"
                for boolean_key in ("console_access", "replication_user"):
                    if boolean_key in user and not isinstance(user[boolean_key], bool):
                        return f"user {boolean_key} must be a boolean"
            if len(names) != len(set(names)):
                return "users cannot contain duplicate usernames"
            if cfg.get("binding_username") and cfg["binding_username"] not in names:
                return "binding_username must identify a declared user"
        elif auth_strategy == "SIMPLE" and not partial:
            default_user_pattern = _RABBITMQ_USER_RE if self.engine_type == "RABBITMQ" else _ACTIVEMQ_USER_RE
            if not default_user_pattern.fullmatch(self._config.admin_username):
                return f"invalid default {self.engine_type} admin username"
        if self.engine_type == "RABBITMQ" and cfg.get("prune_users"):
            return "RabbitMQ users cannot be pruned through the Amazon MQ API"
        deployment = str(cfg.get("deployment_mode") or broker.get("DeploymentMode") or self._default_deployment(size))
        allowed_deployments = (
            {"SINGLE_INSTANCE", "CLUSTER_MULTI_AZ"}
            if self.engine_type == "RABBITMQ"
            else {"SINGLE_INSTANCE", "ACTIVE_STANDBY_MULTI_AZ"}
        )
        if deployment not in allowed_deployments:
            return f"{self.engine_type} deployment_mode must be one of {sorted(allowed_deployments)}"
        update = cfg.get("broker_update")
        if update is not None and (not isinstance(update, dict) or not update):
            return "broker_update must be a non-empty object"
        try:
            if update:
                _validate_request("UpdateBroker", {**update, "BrokerId": "broker-id"})
            if not partial:
                users_for_validation = [
                    {
                        "Username": str(user["username"]),
                        "Password": "Astrolift-Validation-Password-123!",
                        **({"ConsoleAccess": bool(user["console_access"])} if "console_access" in user else {}),
                        **({"Groups": list(user["groups"])} if "groups" in user else {}),
                        **({"ReplicationUser": bool(user["replication_user"])} if "replication_user" in user else {}),
                    }
                    for user in self._user_descriptors(cfg)
                ]
                _validate_request(
                    "CreateBroker",
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
                        users=users_for_validation,
                        validate_only=True,
                    ),
                )
        except Exception as exc:
            return f"invalid AWS Amazon MQ request structure: {exc}"
        if not partial:
            engine_version = str(
                cfg.get("engine_version") or broker.get("EngineVersion") or self._config.engine_version_default
            )
            if not engine_version:
                return "Amazon MQ requires engine_version or an operator engine_version_default"
            instance_type = str(
                cfg.get("host_instance_type") or broker.get("HostInstanceType") or _SIZE_INSTANCE.get(size) or ""
            )
            if not instance_type:
                return "custom Amazon MQ size requires host_instance_type"
            required = _required_subnets(self.engine_type, deployment)
            subnets = list(broker.get("SubnetIds") or cfg.get("subnet_ids") or self._config.subnet_ids)
            if len(subnets) < required:
                return f"{deployment} requires at least {required} subnet(s)"
        return ""

    def _default_deployment(self, size: str) -> str:
        if size == "small":
            return "SINGLE_INSTANCE"
        return "CLUSTER_MULTI_AZ" if self.engine_type == "RABBITMQ" else "ACTIVE_STANDBY_MULTI_AZ"

    def _broker_name(self, spec: ProvisionSpec) -> str:
        return _name(
            "-".join(
                part
                for part in (
                    self._config.broker_name_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint or self.engine_type.lower(),
                )
                if part
            ),
        )

    def _generated_secret_name(self, description: dict[str, Any], username: str) -> str:
        broker_name = str(description.get("BrokerName") or description.get("BrokerId") or "broker")
        return self._generated_secret_name_for(broker_name, username)

    def _generated_secret_name_for(self, broker_name: str, username: str) -> str:
        return f"{self._config.secrets_manager_prefix.rstrip('/')}/{_name(broker_name)}/{_name(username)}"


class AmazonMQRabbitMQDriver(AmazonMQDriver):
    engine_type = "RABBITMQ"


class AmazonMQActiveMQDriver(AmazonMQDriver):
    engine_type = "ACTIVEMQ"


def _validate_request(operation: str, request: dict[str, Any]) -> None:
    from botocore.session import Session
    from botocore.validate import validate_parameters

    service = Session().get_service_model("mq")
    validate_parameters(request, service.operation_model(operation).input_shape)


def _tag_map(spec: ProvisionSpec) -> dict[str, str]:
    return {tag["Key"]: tag["Value"] for tag in tags_for(spec)}


def _request_token(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _generate_password(length: int = 40) -> str:
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(_PASSWORD_SPECIAL),
    ]
    required.extend(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length - len(required)))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)


def _password_error(value: str) -> str:
    if not 12 <= len(value) <= 250:
        return "must be 12-250 characters long"
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        return "must contain only printable ASCII characters"
    if len(set(value)) < 4:
        return "must contain at least four unique characters"
    if any(char in value for char in ",:="):
        return "must not contain commas, colons, or equals signs"
    return ""


def _sensitive_native_path(value: Any, prefix: str = "") -> str:
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            normalized = str(key).replace("_", "").lower()
            if normalized in {"password", "serviceaccountpassword", "users"}:
                return path
            found = _sensitive_native_path(child, path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _sensitive_native_path(child, f"{prefix}[{index}]")
            if found:
                return found
    return ""


def _required_subnets(engine: str, deployment: str) -> int:
    if deployment == "SINGLE_INSTANCE":
        return 1
    return 3 if engine == "RABBITMQ" else 2


def _select_endpoint(endpoints: list[str], protocol: str) -> str:
    schemes = _PROTOCOL_SCHEMES.get(protocol, ())
    for scheme in schemes:
        for endpoint in endpoints:
            if endpoint.lower().startswith(f"{scheme}://"):
                return endpoint
    return ""


def _secret_resource(secret_ref: str, config: AmazonMQConfig) -> str:
    secret_id = secret_ref.split("#", 1)[0]
    if secret_id.startswith("arn:"):
        return secret_id
    return f"arn:aws:secretsmanager:{config.region}:{config.account_id}:secret:{secret_id}-*"


def _broker_id(resource_id: str) -> str:
    return resource_id.rsplit(":", 1)[-1] if resource_id.startswith("arn:") else resource_id


def _broker_name_from_resource(resource_id: str) -> str:
    if not resource_id.startswith("arn:"):
        return resource_id
    parts = resource_id.rsplit(":", 2)
    return parts[-2] if len(parts) >= 3 else resource_id


def _name(value: str) -> str:
    clean = "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-_")
    if not clean:
        raise ManagedServiceError("Amazon MQ name cannot be empty")
    return clean[:50].rstrip("-_")


def _already_exists(exc: Exception) -> bool:
    code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
    return (
        code
        in {
            "ResourceAlreadyExistsException",
            "ResourceExistsException",
            "ConflictException",
            "BadRequestException",
        }
        and "exist" in str(exc).lower()
    )


def _not_found(exc: Exception) -> bool:
    code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
    return code in {"NotFoundException", "ResourceNotFoundException"} or "not found" in str(exc).lower()


def _pending_deletion(exc: Exception) -> bool:
    error = (getattr(exc, "response", {}) or {}).get("Error") or {}
    return (
        str(error.get("Code") or "") == "InvalidRequestException"
        and "delet"
        in str(
            error.get("Message") or "",
        ).lower()
    )


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
    retryable = code not in {"BadRequestException", "ForbiddenException", "UnauthorizedException"}
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [code or str(exc)], retryable=retryable)
