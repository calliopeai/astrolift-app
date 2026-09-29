from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed._networking import ensure_mq_networking
from aws.managed.mq_amazon import (
    AmazonMQActiveMQDriver,
    AmazonMQConfig,
    AmazonMQRabbitMQDriver,
)

BROKER_NAME = "platform-steadymd-triage-prod-broker"
BROKER_ID = "b-123"
BROKER_ARN = f"arn:aws:mq:us-west-2:123456789012:broker:{BROKER_NAME}:{BROKER_ID}"
KEY_ARN = "arn:aws:kms:us-west-2:123456789012:key/key-1"


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "steadymd",
        "app_id": "app-1",
        "app_slug": "triage",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "broker",
        "size": "small",
        "config": {},
        "tags": {"owner": "agents"},
        "isolation": "shared",
        "binding_id": "binding-1",
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _config(**overrides) -> AmazonMQConfig:
    values = {
        "region": "us-west-2",
        "account_id": "123456789012",
        "broker_name_prefix": "platform",
        "subnet_ids": ("subnet-a", "subnet-b", "subnet-c"),
        "security_group_ids": ("sg-mq",),
        "engine_version_default": "3.13",
        "secrets_manager_prefix": "astrolift/mq",
        "deletion_protection_default": True,
        "poll_delay_seconds": 0,
        "max_poll_attempts": 3,
    }
    values.update(overrides)
    return AmazonMQConfig(**values)


def _description(engine="RABBITMQ", **overrides) -> dict:
    endpoints = (
        ["amqps://b-123.mq.us-west-2.amazonaws.com:5671"]
        if engine == "RABBITMQ"
        else [
            "ssl://b-123.mq.us-west-2.amazonaws.com:61617",
            "amqp+ssl://b-123.mq.us-west-2.amazonaws.com:5671",
            "stomp+ssl://b-123.mq.us-west-2.amazonaws.com:61614",
            "mqtt+ssl://b-123.mq.us-west-2.amazonaws.com:8883",
            "wss://b-123.mq.us-west-2.amazonaws.com:61619",
        ]
    )
    values = {
        "BrokerId": BROKER_ID,
        "BrokerArn": BROKER_ARN,
        "BrokerName": BROKER_NAME,
        "BrokerState": "RUNNING",
        "EngineType": engine,
        "EngineVersion": "3.13",
        "HostInstanceType": "mq.t3.micro",
        "DeploymentMode": "SINGLE_INSTANCE",
        "Tags": {
            "astrolift.io/managed-by": "platform",
            "astrolift.io/organization": "steadymd",
            "astrolift.io/app": "triage",
        },
        "BrokerInstances": [
            {
                "Endpoints": endpoints,
                "ConsoleURL": "https://console.mq.example",
            },
        ],
        "Users": [{"Username": "astrolift"}],
    }
    values.update(overrides)
    return values


def _client(engine="RABBITMQ", **description_overrides) -> MagicMock:
    client = MagicMock()
    client.list_brokers.return_value = {"BrokerSummaries": []}
    client.create_broker.return_value = {"BrokerId": BROKER_ID, "BrokerArn": BROKER_ARN}
    client.describe_broker.return_value = _description(engine, **description_overrides)
    client.list_users.return_value = {"Users": [{"Username": "astrolift"}]}
    return client


def _error(code: str, operation: str, message: str | None = None) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message or code},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation,
    )


def _secrets(initial: dict[str, str] | None = None) -> MagicMock:
    client = MagicMock()
    values = dict(initial or {})
    client.values = values

    def get_secret_value(**request):
        secret_id = request["SecretId"]
        if secret_id not in values:
            raise _error("ResourceNotFoundException", "GetSecretValue", "secret not found")
        return {"SecretString": values[secret_id]}

    def create_secret(**request):
        name = request["Name"]
        if name in values:
            raise _error("ResourceExistsException", "CreateSecret", "secret exists")
        values[name] = request["SecretString"]
        return {"ARN": f"arn:aws:secretsmanager:us-west-2:123456789012:secret:{name}-AbCd"}

    def delete_secret(**request):
        values.pop(request["SecretId"], None)
        return {}

    client.get_secret_value.side_effect = get_secret_value
    client.create_secret.side_effect = create_secret
    client.delete_secret.side_effect = delete_secret
    return client


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("mq")
    validate_parameters(params, service.operation_model(operation).input_shape)


def test_rabbitmq_provision_generates_secret_and_valid_native_request():
    client = _client()
    secret_client = _secrets()
    driver = AmazonMQRabbitMQDriver(
        config=_config(),
        client=client,
        secrets_client=secret_client,
        sleep=lambda _seconds: None,
    )

    result = driver.provision(_spec())

    assert result.ok and result.ready and result.handle == f"mq/{BROKER_ARN}"
    request = client.create_broker.call_args.kwargs
    assert request["EngineType"] == "RABBITMQ"
    assert request["DeploymentMode"] == "SINGLE_INSTANCE"
    assert request["SubnetIds"] == ["subnet-a"]
    assert request["Users"][0]["Username"] == "astrolift"
    assert len(request["Users"][0]["Password"]) == 40
    assert not any(char in request["Users"][0]["Password"] for char in ",:=")
    assert len(set(request["Users"][0]["Password"])) >= 4
    assert request["Tags"]["astrolift.io/managed-by"] == "platform"
    assert request["Tags"]["astrolift.io/binding"] == "binding-1"
    _validate("CreateBroker", request)
    secret_request = secret_client.create_secret.call_args.kwargs
    assert secret_request["Name"] == f"astrolift/mq/{BROKER_NAME}/astrolift"
    assert secret_request["SecretString"] == request["Users"][0]["Password"]
    assert request["Users"][0]["Password"] not in str(_spec().config)


@pytest.mark.parametrize(
    ("driver_cls", "engine", "deployment", "subnet_count"),
    [
        (AmazonMQRabbitMQDriver, "RABBITMQ", "CLUSTER_MULTI_AZ", 3),
        (AmazonMQActiveMQDriver, "ACTIVEMQ", "ACTIVE_STANDBY_MULTI_AZ", 2),
    ],
)
def test_non_small_sizes_choose_engine_specific_multi_az_topology(
    driver_cls,
    engine,
    deployment,
    subnet_count,
):
    client = _client(engine)
    driver = driver_cls(
        config=_config(),
        client=client,
        secrets_client=_secrets(),
        sleep=lambda _seconds: None,
    )

    result = driver.provision(_spec(size="medium"))

    assert result.ok
    request = client.create_broker.call_args.kwargs
    assert request["EngineType"] == engine
    assert request["DeploymentMode"] == deployment
    assert len(request["SubnetIds"]) == subnet_count
    _validate("CreateBroker", request)


def test_native_broker_shape_exposes_auth_logs_storage_ldap_and_replication_options():
    client = _client("ACTIVEMQ")
    driver = AmazonMQActiveMQDriver(
        config=_config(kms_key_id=KEY_ARN),
        client=client,
        secrets_client=_secrets(),
        sleep=lambda _seconds: None,
    )
    broker = {
        "AuthenticationStrategy": "SIMPLE",
        "AutoMinorVersionUpgrade": True,
        "DeploymentMode": "ACTIVE_STANDBY_MULTI_AZ",
        "EngineVersion": "5.18",
        "HostInstanceType": "mq.m5.large",
        "Logs": {"Audit": True, "General": True},
        "MaintenanceWindowStartTime": {
            "DayOfWeek": "SUNDAY",
            "TimeOfDay": "02:00",
            "TimeZone": "UTC",
        },
        "PubliclyAccessible": False,
        "StorageType": "EFS",
        "DataReplicationMode": "NONE",
    }

    result = driver.provision(
        _spec(
            size="custom",
            config={
                "broker": broker,
                "users": [
                    {
                        "username": "ops",
                        "console_access": True,
                        "groups": ["admins"],
                    },
                ],
            },
        ),
    )

    assert result.ok
    request = client.create_broker.call_args.kwargs
    for key, value in broker.items():
        assert request[key] == value
    assert request["EncryptionOptions"] == {"UseAwsOwnedKey": False, "KmsKeyId": KEY_ARN}
    _validate("CreateBroker", request)


def test_existing_external_broker_is_not_adopted_and_no_secret_is_created():
    client = _client(Tags={})
    client.list_brokers.return_value = {
        "BrokerSummaries": [
            {
                "BrokerId": BROKER_ID,
                "BrokerArn": BROKER_ARN,
                "BrokerName": BROKER_NAME,
                "EngineType": "RABBITMQ",
                "DeploymentMode": "SINGLE_INSTANCE",
            },
        ],
    }
    secret_client = _secrets()
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=secret_client)

    result = driver.provision(_spec())

    assert not result.ok and "outside this resource declaration" in result.message
    secret_client.create_secret.assert_not_called()
    client.create_tags.assert_not_called()


def test_existing_managed_rabbit_broker_does_not_call_activemq_user_api():
    secret_name = f"astrolift/mq/{BROKER_NAME}/astrolift"
    secret_client = _secrets({secret_name: "Existing-Password-123!"})
    client = _client()
    client.list_brokers.return_value = {
        "BrokerSummaries": [
            {
                "BrokerId": BROKER_ID,
                "BrokerArn": BROKER_ARN,
                "BrokerName": BROKER_NAME,
                "EngineType": "RABBITMQ",
                "DeploymentMode": "SINGLE_INSTANCE",
            },
        ],
    }
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=secret_client)

    result = driver.provision(_spec())

    assert result.ok
    secret_client.create_secret.assert_not_called()
    client.list_users.assert_not_called()
    client.update_user.assert_not_called()


def test_broker_update_is_valid_reboots_and_is_idempotent_by_plan_hash():
    client = _client()
    driver = AmazonMQRabbitMQDriver(
        config=_config(),
        client=client,
        secrets_client=_secrets(),
        sleep=lambda _seconds: None,
    )
    update = UpdateSpec(
        f"mq/{BROKER_ARN}",
        config={
            "broker_update": {
                "AutoMinorVersionUpgrade": True,
                "EngineVersion": "3.14",
                "HostInstanceType": "mq.m5.large",
                "Logs": {"General": True},
                "SecurityGroups": ["sg-new"],
            },
            "reboot_after_update": True,
        },
    )

    first = driver.update(update)

    assert first.ok
    request = client.update_broker.call_args.kwargs
    assert request["BrokerId"] == BROKER_ID
    _validate("UpdateBroker", request)
    client.reboot_broker.assert_called_once_with(BrokerId=BROKER_ID)
    tag = client.create_tags.call_args.kwargs["Tags"]["astrolift.io/update-plan-sha256"]
    client.describe_broker.return_value = _description(
        Tags={
            "astrolift.io/managed-by": "platform",
            "astrolift.io/update-plan-sha256": tag,
        },
    )

    second = driver.update(update)

    assert second.ok
    client.update_broker.assert_called_once()
    client.reboot_broker.assert_called_once()


def test_declarative_users_read_bundle_fields_create_update_and_prune():
    bundle = "arn:aws:secretsmanager:us-west-2:123456789012:secret:shared/mq-AbCd"
    secret_client = _secrets({bundle: '{"ops":"Ops-Password-123!","app":"App-Password-123!"}'})
    client = _client("ACTIVEMQ")
    client.list_users.return_value = {
        "Users": [
            {"Username": "ops"},
            {"Username": "legacy"},
        ],
    }
    driver = AmazonMQActiveMQDriver(config=_config(), client=client, secrets_client=secret_client)

    result = driver.update(
        UpdateSpec(
            f"mq/{BROKER_ARN}",
            config={
                "users": [
                    {"username": "ops", "password_secret_ref": f"{bundle}#ops"},
                    {"username": "app", "password_secret_ref": f"{bundle}#app"},
                ],
                "prune_users": True,
            },
        ),
    )

    assert result.ok
    assert client.update_user.call_args.kwargs["Username"] == "ops"
    assert client.update_user.call_args.kwargs["Password"] == "Ops-Password-123!"
    assert client.create_user.call_args.kwargs["Username"] == "app"
    assert client.create_user.call_args.kwargs["Password"] == "App-Password-123!"
    client.delete_user.assert_called_once_with(BrokerId=BROKER_ID, Username="legacy")
    secret_client.create_secret.assert_not_called()


def test_activemq_user_changes_can_be_applied_with_one_reboot():
    bundle = "arn:aws:secretsmanager:us-west-2:123456789012:secret:shared/mq-AbCd"
    client = _client("ACTIVEMQ")
    client.list_users.return_value = {"Users": [{"Username": "app"}]}
    driver = AmazonMQActiveMQDriver(
        config=_config(),
        client=client,
        secrets_client=_secrets({bundle: '{"password":"App-Password-123!"}'}),
        sleep=lambda _seconds: None,
    )

    result = driver.update(
        UpdateSpec(
            f"mq/{BROKER_ARN}",
            config={
                "users": [{"username": "app", "password_secret_ref": f"{bundle}#password"}],
                "reboot_after_update": True,
            },
        ),
    )

    assert result.ok
    client.update_user.assert_called_once()
    client.reboot_broker.assert_called_once_with(BrokerId=BROKER_ID)


def test_rabbitmq_user_update_is_rejected_before_the_activemq_only_api():
    client = _client()
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=_secrets())

    result = driver.update(
        UpdateSpec(
            f"mq/{BROKER_ARN}",
            config={"users": [{"username": "admin", "password_secret_ref": "shared/mq"}]},
        ),
    )

    assert not result.ok
    assert "RabbitMQ users cannot be reconciled" in result.message
    client.list_users.assert_not_called()


def test_activemq_ldap_password_is_materialized_from_secret_not_config():
    ldap_secret = "arn:aws:secretsmanager:us-west-2:123456789012:secret:shared/ldap-AbCd"
    client = _client("ACTIVEMQ")
    driver = AmazonMQActiveMQDriver(
        config=_config(),
        client=client,
        secrets_client=_secrets({ldap_secret: '{"service_password":"Ldap-Password-123!"}'}),
        sleep=lambda _seconds: None,
    )
    ldap = {
        "Hosts": ["ldap.internal.example:636"],
        "UserSearchMatching": "uid={0}",
        "UserBase": "ou=users,dc=example,dc=com",
        "RoleSearchMatching": "(member={0})",
        "RoleBase": "ou=groups,dc=example,dc=com",
        "ServiceAccountUsername": "cn=broker,dc=example,dc=com",
    }

    result = driver.provision(
        _spec(
            config={
                "broker": {"AuthenticationStrategy": "LDAP", "LdapServerMetadata": ldap},
                "ldap_service_account_password_secret_ref": f"{ldap_secret}#service_password",
            },
        ),
    )

    assert result.ok
    request = client.create_broker.call_args.kwargs
    assert request["Users"] == []
    assert request["LdapServerMetadata"]["ServiceAccountPassword"] == "Ldap-Password-123!"
    assert "Ldap-Password-123!" not in str(_spec().config)
    _validate("CreateBroker", request)


def test_config_managed_rabbit_binding_omits_nonexistent_static_credentials():
    client = _client(AuthenticationStrategy="CONFIG_MANAGED")
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=_secrets())
    config = {
        "broker": {
            "AuthenticationStrategy": "CONFIG_MANAGED",
            "Configuration": {"Id": "c-123", "Revision": 2},
        },
    }

    result = driver.provision(_spec(config=config))
    binding = driver.binding(ServiceHandle(f"mq/{BROKER_ARN}"), config)

    assert result.ok
    assert client.create_broker.call_args.kwargs["Users"] == []
    assert binding.env_vars["MQ_AUTH_STRATEGY"].literal == "CONFIG_MANAGED"
    assert "MQ_USERNAME" not in binding.env_vars
    assert "MQ_PASSWORD" not in binding.env_vars


def test_update_without_users_never_mutates_credentials():
    client = _client()
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=_secrets())

    result = driver.update(UpdateSpec(f"mq/{BROKER_ARN}", config={"protocol": "amqps"}))

    assert result.ok
    client.create_user.assert_not_called()
    client.update_user.assert_not_called()
    client.delete_user.assert_not_called()


def test_rabbit_binding_is_portable_and_password_is_never_revealed():
    secret_name = f"astrolift/mq/{BROKER_NAME}/astrolift"
    driver = AmazonMQRabbitMQDriver(
        config=_config(),
        client=_client(),
        secrets_client=_secrets({secret_name: "Password-123!"}),
    )

    binding = driver.binding(ServiceHandle(f"mq/{BROKER_ARN}"), {})

    assert binding.env_vars["MQ_ENDPOINT"].literal.startswith("amqps://")
    assert binding.env_vars["MQ_USERNAME"].literal == "astrolift"
    assert binding.env_vars["MQ_PASSWORD"].secret_ref == secret_name
    assert binding.env_vars["MQ_PROTOCOL"].literal == "amqps"
    assert all("Password-123!" not in str(value) for value in binding.env_vars.values())
    grant = next(grant for grant in binding.iam_grants if "secretsmanager" in grant.resource)
    assert grant.resource.endswith(f"secret:{secret_name}-*")


@pytest.mark.parametrize(
    ("protocol", "scheme"),
    [
        ("amqp", "amqp+ssl://"),
        ("openwire", "ssl://"),
        ("stomp", "stomp+ssl://"),
        ("mqtt", "mqtt+ssl://"),
        ("wss", "wss://"),
    ],
)
def test_activemq_binding_selects_requested_wire_protocol(protocol, scheme):
    secret_name = f"astrolift/mq/{BROKER_NAME}/astrolift"
    driver = AmazonMQActiveMQDriver(
        config=_config(),
        client=_client("ACTIVEMQ"),
        secrets_client=_secrets({secret_name: "Password-123!"}),
    )

    binding = driver.binding(ServiceHandle(f"mq/{BROKER_ARN}"), {"protocol": protocol})

    assert binding.env_vars["MQ_ENDPOINT"].literal.startswith(scheme)


def test_binding_reports_requested_endpoint_absence_honestly():
    driver = AmazonMQRabbitMQDriver(
        config=_config(),
        client=_client(),
        secrets_client=_secrets(),
    )

    with pytest.raises(ManagedServiceError, match="no endpoint for protocol mqtt"):
        driver.binding(ServiceHandle(f"mq/{BROKER_ARN}"), {"protocol": "mqtt"})


def test_deprovision_requires_guard_and_data_loss_ack_then_deletes_generated_secret():
    secret_name = f"astrolift/mq/{BROKER_NAME}/astrolift"
    secret_client = _secrets({secret_name: "Password-123!"})
    client = _client()
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=secret_client)
    handle = f"mq/{BROKER_ARN}"

    protected = driver.deprovision(DeprovisionSpec(handle))
    retained = driver.deprovision(
        DeprovisionSpec(handle, config={"deletion_protection": False}),
    )
    deleted = driver.deprovision(
        DeprovisionSpec(handle),
        delete_data=True,
        force_destroy=True,
    )

    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    assert not retained.ok and retained.errors == ["retained_broker_data_requires_delete_data"]
    assert deleted.ok
    client.delete_broker.assert_called_once_with(BrokerId=BROKER_ID)
    secret_client.delete_secret.assert_called_once_with(
        SecretId=secret_name,
        RecoveryWindowInDays=7,
    )


def test_deprovision_preserves_externally_owned_password_secret():
    external = "arn:aws:secretsmanager:us-west-2:123456789012:secret:shared/mq-AbCd#password"
    secret_client = _secrets()
    driver = AmazonMQRabbitMQDriver(
        config=_config(),
        client=_client(),
        secrets_client=secret_client,
    )

    result = driver.deprovision(
        DeprovisionSpec(
            f"mq/{BROKER_ARN}",
            config={"users": [{"username": "app", "password_secret_ref": external}]},
        ),
        delete_data=True,
        force_destroy=True,
    )

    assert result.ok
    secret_client.delete_secret.assert_not_called()


@pytest.mark.parametrize(
    ("provider_state", "state"),
    [
        ("RUNNING", "available"),
        ("REPLICA", "available"),
        ("CREATION_IN_PROGRESS", "provisioning"),
        ("REBOOT_IN_PROGRESS", "updating"),
        ("DELETION_IN_PROGRESS", "deprovisioning"),
        ("CREATION_FAILED", "error"),
    ],
)
def test_status_maps_provider_lifecycle(provider_state, state):
    driver = AmazonMQRabbitMQDriver(
        config=_config(),
        client=_client(BrokerState=provider_state),
        secrets_client=_secrets(),
    )

    assert driver.status(ServiceHandle(f"mq/{BROKER_ARN}")).state == state


def test_missing_paths_and_snapshot_contract_are_honest():
    client = _client()
    client.describe_broker.side_effect = _error("NotFoundException", "DescribeBroker")
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=_secrets())
    handle = f"mq/{BROKER_ARN}"

    assert driver.status(ServiceHandle(handle)).state == "deprovisioned"
    assert driver.deprovision(DeprovisionSpec(handle)).ok
    assert not driver.update(UpdateSpec(handle, config={})).ok
    with pytest.raises(ManagedServiceError, match="no broker snapshot API"):
        driver.snapshot(ServiceHandle(handle))


@pytest.mark.parametrize(
    ("driver_cls", "config", "message"),
    [
        (AmazonMQRabbitMQDriver, {"unknown": True}, "unsupported Amazon MQ config fields"),
        (AmazonMQRabbitMQDriver, {"broker": []}, "broker must be an object"),
        (AmazonMQRabbitMQDriver, {"broker": {"Users": []}}, "plaintext passwords"),
        (AmazonMQRabbitMQDriver, {"broker": {"users": [{"password": "plaintext"}]}}, "plaintext passwords"),
        (AmazonMQRabbitMQDriver, {"deployment_mode": "ACTIVE_STANDBY_MULTI_AZ"}, "deployment_mode"),
        (AmazonMQActiveMQDriver, {"deployment_mode": "CLUSTER_MULTI_AZ"}, "deployment_mode"),
        (AmazonMQRabbitMQDriver, {"users": []}, "non-empty users array"),
        (
            AmazonMQRabbitMQDriver,
            {"users": [{"username": "admin"}, {"username": "app"}]},
            "exactly one bootstrap administrator",
        ),
        (AmazonMQRabbitMQDriver, {"users": [{"username": "guest"}]}, "guest username"),
        (
            AmazonMQRabbitMQDriver,
            {"users": [{"username": "admin", "groups": ["ops"]}]},
            "apply only to ActiveMQ",
        ),
        (
            AmazonMQActiveMQDriver,
            {"users": [{"username": "same"}, {"username": "same"}]},
            "duplicate usernames",
        ),
        (AmazonMQRabbitMQDriver, {"users": [{"username": "a", "password": "plain"}]}, "unsupported user"),
        (
            AmazonMQActiveMQDriver,
            {"broker": {"AuthenticationStrategy": "LDAP", "LdapServerMetadata": {}}},
            "requires ldap_service_account_password_secret_ref",
        ),
        (
            AmazonMQActiveMQDriver,
            {
                "broker": {
                    "AuthenticationStrategy": "LDAP",
                    "LdapServerMetadata": {"ServiceAccountPassword": "plaintext"},
                },
            },
            "plaintext passwords",
        ),
        (
            AmazonMQRabbitMQDriver,
            {"broker": {"AuthenticationStrategy": "LDAP"}},
            "AuthenticationStrategy",
        ),
        (AmazonMQRabbitMQDriver, {"broker_update": {}}, "non-empty object"),
        (AmazonMQRabbitMQDriver, {"protocol": "tcp"}, "protocol must be"),
    ],
)
def test_invalid_configuration_is_rejected_before_cloud(driver_cls, config, message):
    client = _client("ACTIVEMQ" if driver_cls is AmazonMQActiveMQDriver else "RABBITMQ")
    driver = driver_cls(config=_config(), client=client, secrets_client=_secrets())

    result = driver.provision(_spec(config=config))

    assert not result.ok and message in result.message
    client.create_broker.assert_not_called()


def test_missing_engine_version_and_custom_instance_fail_before_cloud():
    client = _client()
    no_version = AmazonMQRabbitMQDriver(
        config=_config(engine_version_default=""),
        client=client,
        secrets_client=_secrets(),
    ).provision(_spec())
    no_instance = AmazonMQRabbitMQDriver(
        config=_config(),
        client=client,
        secrets_client=_secrets(),
    ).provision(_spec(size="custom"))

    assert not no_version.ok and "engine_version" in no_version.message
    assert not no_instance.ok and "host_instance_type" in no_instance.message


@pytest.mark.parametrize("password", ["short", "AAAAAAAAAAAA", "Password,123!", "Password:123!", "Password=123!"])
def test_external_user_password_must_satisfy_amazon_mq_constraints(password):
    secret_ref = "arn:aws:secretsmanager:us-west-2:123456789012:secret:shared/mq-AbCd"
    client = _client("ACTIVEMQ")
    driver = AmazonMQActiveMQDriver(
        config=_config(),
        client=client,
        secrets_client=_secrets({secret_ref: password}),
    )

    result = driver.provision(
        _spec(config={"users": [{"username": "app", "password_secret_ref": secret_ref}]}),
    )

    assert not result.ok and "password secret" in result.message
    client.create_broker.assert_not_called()


def test_mq_networking_discovers_three_azs_and_all_tls_protocol_ports():
    ec2 = MagicMock()
    eks = MagicMock()
    cluster = SimpleNamespace(slug="aws-prod", provider_config={}, auth_config={})
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-1"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {"SubnetId": f"subnet-{az}", "AvailabilityZone": f"us-west-2{az}", "MapPublicIpOnLaunch": False}
            for az in ("a", "b", "c", "d")
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-mq"}

    subnets, groups = ensure_mq_networking(
        cluster,
        region="us-west-2",
        clients=(ec2, eks),
    )

    assert subnets == ["subnet-a", "subnet-b", "subnet-c"]
    assert groups == ["sg-mq"]
    assert [
        call.kwargs["IpPermissions"][0]["FromPort"] for call in ec2.authorize_security_group_ingress.call_args_list
    ] == [443, 5671, 61614, 61617, 61619, 8883]


def test_managed_config_plugin_cost_and_catalogue_are_wired():
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    cluster = SimpleNamespace(
        slug="aws-prod",
        region="us-west-2",
        auth_config={},
        provider_config={
            "account_id": "123456789012",
            "mq_subnet_ids": ["subnet-a", "subnet-b", "subnet-c"],
            "mq_security_group_ids": ["sg-mq"],
            "amazon_mq_rabbitmq_engine_version": "3.13",
            "amazon_mq_activemq_engine_version": "5.18",
        },
    )
    variants = {
        "amazon_mq_rabbitmq": (AmazonMQRabbitMQDriver, "3.13"),
        "amazon_mq_activemq": (AmazonMQActiveMQDriver, "5.18"),
    }

    for variant, (driver_cls, version) in variants.items():
        cfg = managed_config_for("aws", cluster, kind="mq", variant=variant)
        assert cfg.engine_version_default == version
        assert cfg.subnet_ids == ("subnet-a", "subnet-b", "subnet-c")
        assert PLUGIN.managed_service_drivers[("mq", variant)] is driver_cls
        assert SERVICE_CODE_BY_VARIANT[("mq", variant)] == "AmazonMQ"
        entry = next(item for item in MATRIX.managed_services if item.plugin_id == "aws" and item.variant == variant)
        assert entry.status == "preview"
        assert "MQ_ENDPOINT" in entry.binding_envs


def test_a_platform_broker_of_another_org_is_not_adopted():
    """Platform-made is not enough; it must be this service's (#1961)."""
    client = _client(Tags={"astrolift.io/managed-by": "platform", "astrolift.io/organization": "globex"})
    client.list_brokers.return_value = {
        "BrokerSummaries": [{"BrokerId": BROKER_ID, "BrokerArn": BROKER_ARN, "BrokerName": BROKER_NAME}],
    }
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=_secrets({}))

    result = driver.provision(_spec())

    assert not result.ok
    client.create_broker.assert_not_called()


def test_a_plaintext_ldap_password_is_refused_in_broker_update():
    """broker_update reaches UpdateBroker as-is (#1953)."""
    client = _client()
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=_secrets({}))

    result = driver.update(
        UpdateSpec(
            f"mq/{BROKER_ARN}",
            config={"broker_update": {"LdapServerMetadata": {"ServiceAccountPassword": "hunter2"}}},
        )
    )

    assert not result.ok and "ServiceAccountPassword" in result.message
    client.update_broker.assert_not_called()


@pytest.mark.parametrize(
    "replication",
    [
        {
            "DataReplicationMode": "CRDR",
            "DataReplicationPrimaryBrokerArn": "arn:aws:mq:us-east-1:123456789012:broker:other-tenant:b-1",
        },
        {"DataReplicationPrimaryBrokerArn": "arn:aws:mq:us-east-1:123456789012:broker:other-tenant:b-1"},
        {"DataReplicationMode": "CRDR"},
    ],
)
def test_a_broker_replicates_no_other_brokers_data_from_config(replication):
    # A replica broker takes the named primary's data, whoever owns it (#2087).
    client = _client()
    driver = AmazonMQRabbitMQDriver(config=_config(), client=client, secrets_client=_secrets())

    result = driver.provision(_spec(config={"broker": replication}))

    assert not result.ok
    assert "replicates no other broker's data" in result.message
    client.create_broker.assert_not_called()
