"""Lifecycle, safety, binding, and native-shape tests for SNS SMS bindings."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.sms_sns import SNSSmsConfig, SNSSmsDriver


class NotFound(Exception):
    response: ClassVar[dict] = {"Error": {"Code": "NotFound"}}


class AuthorizationError(Exception):
    response: ClassVar[dict] = {"Error": {"Code": "AuthorizationError"}}


class FakeSNS:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.topics: dict[str, dict[str, str]] = {}
        self.tags: dict[str, dict[str, str]] = {}
        self.subscriptions: dict[str, list[dict]] = {}
        self.data_policies: dict[str, str] = {}
        self.sandbox: bool | None = False
        self.deny_sandbox_read = False
        self.subscription_counter = 0

    def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs_for(self, name: str, occurrence: int = -1) -> dict:
        return [kwargs for call, kwargs in self.calls if call == name][occurrence]

    @staticmethod
    def _tag_map(values: list[dict] | None) -> dict[str, str]:
        return {str(item["Key"]): str(item["Value"]) for item in values or []}

    def create_topic(self, **kwargs):
        self._call("create_topic", kwargs)
        arn = f"arn:aws:sns:us-east-1:123456789012:{kwargs['Name']}"
        self.topics[arn] = {"TopicArn": arn, **dict(kwargs.get("Attributes") or {})}
        self.tags[arn] = self._tag_map(kwargs.get("Tags"))
        self.subscriptions.setdefault(arn, [])
        return {"TopicArn": arn}

    def get_topic_attributes(self, **kwargs):
        self._call("get_topic_attributes", kwargs)
        if kwargs["TopicArn"] not in self.topics:
            raise NotFound(kwargs["TopicArn"])
        return {"Attributes": dict(self.topics[kwargs["TopicArn"]])}

    def list_tags_for_resource(self, **kwargs):
        self._call("list_tags_for_resource", kwargs)
        return {
            "Tags": [
                {"Key": key, "Value": value} for key, value in sorted(self.tags.get(kwargs["ResourceArn"], {}).items())
            ],
        }

    def tag_resource(self, **kwargs):
        self._call("tag_resource", kwargs)
        self.tags.setdefault(kwargs["ResourceArn"], {}).update(self._tag_map(kwargs["Tags"]))

    def set_topic_attributes(self, **kwargs):
        self._call("set_topic_attributes", kwargs)
        self.topics[kwargs["TopicArn"]][kwargs["AttributeName"]] = kwargs["AttributeValue"]

    def put_data_protection_policy(self, **kwargs):
        self._call("put_data_protection_policy", kwargs)
        self.data_policies[kwargs["ResourceArn"]] = kwargs["DataProtectionPolicy"]

    def subscribe(self, **kwargs):
        self._call("subscribe", kwargs)
        self.subscription_counter += 1
        arn = f"{kwargs['TopicArn']}:{self.subscription_counter}"
        self.subscriptions.setdefault(kwargs["TopicArn"], []).append(
            {
                "TopicArn": kwargs["TopicArn"],
                "Protocol": kwargs["Protocol"],
                "Endpoint": kwargs["Endpoint"],
                "SubscriptionArn": arn,
            },
        )
        return {"SubscriptionArn": arn}

    def list_subscriptions_by_topic(self, **kwargs):
        self._call("list_subscriptions_by_topic", kwargs)
        return {"Subscriptions": [dict(item) for item in self.subscriptions.get(kwargs["TopicArn"], [])]}

    def unsubscribe(self, **kwargs):
        self._call("unsubscribe", kwargs)
        for topic_arn, rows in self.subscriptions.items():
            self.subscriptions[topic_arn] = [
                item for item in rows if item["SubscriptionArn"] != kwargs["SubscriptionArn"]
            ]

    def delete_topic(self, **kwargs):
        self._call("delete_topic", kwargs)
        arn = kwargs["TopicArn"]
        if arn not in self.topics:
            raise NotFound(arn)
        self.topics.pop(arn)
        self.tags.pop(arn, None)
        self.subscriptions.pop(arn, None)

    def get_sms_sandbox_account_status(self, **kwargs):
        self._call("get_sms_sandbox_account_status", kwargs)
        if self.deny_sandbox_read:
            raise AuthorizationError("denied")
        return {} if self.sandbox is None else {"IsInSandbox": self.sandbox}

    def seed(self, name: str, *, owned: bool = False) -> str:
        arn = f"arn:aws:sns:us-east-1:123456789012:{name}"
        self.topics[arn] = {"TopicArn": arn}
        self.tags[arn] = {"astrolift.io/sms-binding": name} if owned else {"owner": "someone-else"}
        self.subscriptions[arn] = []
        self.calls.clear()
        return arn


def _config(**overrides) -> SNSSmsConfig:
    return SNSSmsConfig(
        region="us-east-1",
        account_id="123456789012",
        **overrides,
    )


def _driver(**config_overrides) -> tuple[SNSSmsDriver, FakeSNS]:
    sns = FakeSNS()
    return SNSSmsDriver(config=_config(**config_overrides), client=sns), sns


def _spec(config: dict | None = None) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="acme",
        app_id="app-id",
        app_slug="triage",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="alerts",
        size="small",
        config={} if config is None else config,
        tags={"owner": "support"},
        binding_id="binding-id",
        managed_service_id="11111111-1111-4111-8111-111111111111",
    )


def _update_spec(*args, **kwargs):
    kwargs.setdefault("managed_service_id", "11111111-1111-4111-8111-111111111111")
    return UpdateSpec(*args, **kwargs)


def _deprovision_spec(*args, **kwargs):
    kwargs.setdefault("managed_service_id", "11111111-1111-4111-8111-111111111111")
    return DeprovisionSpec(*args, **kwargs)


def _arn(result) -> str:
    return result.handle.split("/", 1)[1]


def test_provision_creates_owned_project_topic_and_portable_binding() -> None:
    driver, sns = _driver()
    result = driver.provision(
        _spec(
            {
                "sender_id": "Acme-Help",
                "sms_type": "Transactional",
                "max_price_usd": "0.08",
                "origination_number": "+12065550100",
                "entity_id": "entity-1",
                "template_id": "template-1",
            },
        ),
    )
    assert result.ok and result.ready and result.handle.startswith("sms/arn:aws:sns:")
    arn = _arn(result)
    assert sns.tags[arn]["astrolift.io/sms-binding"] == arn.rsplit(":", 1)[-1]
    assert sns.tags[arn]["astrolift.io/binding"] == "binding-id"

    binding = driver.binding(
        ServiceHandle(result.handle),
        _spec().config
        | {
            "sender_id": "Acme-Help",
            "origination_number": "+12065550100",
            "max_price_usd": "0.08",
            "entity_id": "entity-1",
            "template_id": "template-1",
        },
    )
    assert binding.env_vars["SMS_PROVIDER"].literal == "aws_sns"
    assert binding.env_vars["SMS_FROM"].literal == "+12065550100"
    assert binding.env_vars["SMS_TOPIC_ARN"].literal == arn
    assert binding.env_vars["SMS_SANDBOX"].literal == "false"
    assert [(grant.resource, grant.actions) for grant in binding.iam_grants] == [(arn, ["sns:Publish"])]


def test_direct_mode_is_explicit_and_discloses_wildcard_publish_scope() -> None:
    driver, _ = _driver()
    result = driver.provision(_spec({"delivery_mode": "direct"}))
    binding = driver.binding(ServiceHandle(result.handle), {"delivery_mode": "direct"})
    assert binding.iam_grants[0].resource == "*"
    assert "requires sns:Publish on all resources" in binding.notes


def test_topic_kms_grant_is_not_added_to_direct_only_mode() -> None:
    driver, _ = _driver(kms_key_id="arn:aws:kms:us-east-1:123456789012:key/sms")
    result = driver.provision(_spec())
    topic = driver.binding(ServiceHandle(result.handle), {"delivery_mode": "topic"})
    assert [grant.actions for grant in topic.iam_grants] == [
        ["sns:Publish"],
        ["kms:Decrypt", "kms:GenerateDataKey"],
    ]
    direct = driver.binding(ServiceHandle(result.handle), {"delivery_mode": "direct"})
    assert [grant.actions for grant in direct.iam_grants] == [["sns:Publish"]]


def test_explicit_empty_sender_and_kms_override_operator_defaults() -> None:
    driver, sns = _driver(
        kms_key_id="arn:aws:kms:us-east-1:123456789012:key/default",
        sender_id_default="Acme",
    )
    result = driver.provision(_spec({"kms_master_key_id": "", "sender_id": ""}))
    assert result.ok
    arn = _arn(result)
    assert sns.topics[arn]["KmsMasterKeyId"] == ""
    binding = driver.binding(
        ServiceHandle(result.handle),
        {"kms_master_key_id": "", "sender_id": ""},
    )
    assert binding.env_vars["SMS_SENDER_ID"].literal == ""
    assert [grant.actions for grant in binding.iam_grants] == [["sns:Publish"]]


def test_phone_subscriptions_are_idempotent_and_pruned_declaratively() -> None:
    driver, sns = _driver()
    first = driver.provision(_spec({"phone_numbers": ["+12065550101", "+12065550102"]}))
    second = driver.provision(_spec({"phone_numbers": ["+12065550101", "+12065550102"]}))
    assert first.ok and second.ok and first.handle == second.handle
    assert sns.names().count("create_topic") == 1
    assert sns.names().count("subscribe") == 2
    updated = driver.update(
        _update_spec(first.handle, config={"phone_numbers": ["+12065550102", "+12065550103"]}),
    )
    assert updated.ok
    endpoints = {item["Endpoint"] for item in sns.subscriptions[_arn(first)]}
    assert endpoints == {"+12065550102", "+12065550103"}
    assert "unsubscribe" in sns.names()


def test_phone_subscription_pruning_can_be_disabled() -> None:
    driver, sns = _driver()
    result = driver.provision(_spec({"phone_numbers": ["+12065550101"]}))
    updated = driver.update(
        _update_spec(result.handle, config={"phone_numbers": [], "prune_phone_numbers": False}),
    )
    assert updated.ok and sns.subscriptions[_arn(result)]
    assert "unsubscribe" not in sns.names()


def test_topic_name_is_immutable_on_update() -> None:
    driver, _ = _driver()
    result = driver.provision(_spec({"topic_name": "sms-alerts"}))
    updated = driver.update(_update_spec(result.handle, config={"topic_name": "renamed"}))
    assert not updated.ok and updated.errors == ["immutable_topic_name"]


def test_foreign_name_collision_is_rejected_before_mutation() -> None:
    driver, sns = _driver()
    sns.seed("sms-alerts")
    result = driver.provision(_spec({"topic_name": "sms-alerts"}))
    assert not result.ok and "foreign" in result.message
    assert "create_topic" not in sns.names()
    assert "set_topic_attributes" not in sns.names()


def test_foreign_handle_cannot_be_updated_bound_or_deleted() -> None:
    driver, sns = _driver()
    arn = sns.seed("foreign")
    handle = f"sms/{arn}"
    updated = driver.update(_update_spec(handle, config={}))
    assert not updated.ok and "foreign" in updated.message
    with pytest.raises(Exception, match="foreign"):
        driver.binding(ServiceHandle(handle))
    deleted = driver.deprovision(_deprovision_spec(handle, {}), force_destroy=True)
    assert not deleted.ok and arn in sns.topics


def test_status_surfaces_sandbox_without_treating_it_as_failure() -> None:
    driver, sns = _driver()
    sns.sandbox = True
    result = driver.provision(_spec({"phone_numbers": ["+12065550101"]}))
    assert result.ok and "sandbox" in result.message
    status = driver.status(ServiceHandle(result.handle))
    assert status.state == "available" and "verified recipients" in status.message


def test_sandbox_read_permission_is_optional_and_reported_unknown() -> None:
    driver, sns = _driver()
    sns.deny_sandbox_read = True
    result = driver.provision(_spec())
    assert result.ok
    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["SMS_SANDBOX"].literal == "unknown"


def test_deprovision_is_protected_idempotent_and_forceable() -> None:
    driver, sns = _driver()
    result = driver.provision(_spec())
    protected = driver.deprovision(_deprovision_spec(result.handle, {}))
    assert not protected.ok and protected.retryable is False
    deleted = driver.deprovision(_deprovision_spec(result.handle, {}), force_destroy=True)
    assert deleted.ok and sns.topics == {}
    again = driver.deprovision(_deprovision_spec(result.handle, {}), force_destroy=True)
    assert again.ok and "already gone" in again.message


def test_status_reports_deprovisioned_for_missing_topic() -> None:
    driver, _ = _driver()
    handle = "sms/arn:aws:sns:us-east-1:123456789012:missing"
    assert driver.status(ServiceHandle(handle)).state == "deprovisioned"


def test_explicit_null_policies_clear_to_safe_provider_defaults() -> None:
    driver, sns = _driver()
    result = driver.provision(
        _spec({"topic_policy": {"Version": "2012-10-17"}, "data_protection_policy": {"Name": "audit"}}),
    )
    updated = driver.update(
        _update_spec(result.handle, config={"topic_policy": None, "data_protection_policy": None}),
    )
    assert updated.ok
    arn = _arn(result)
    assert "AWS:SourceOwner" in sns.topics[arn]["Policy"]
    assert sns.data_policies[arn] == ""


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"unknown": True}, "unsupported"),
        ({"topic_name": "bad.name"}, "topic_name"),
        ({"delivery_mode": "magic"}, "delivery_mode"),
        ({"phone_numbers": "not-a-list"}, "array"),
        ({"phone_numbers": ["206-555-0100"]}, "E.164"),
        ({"phone_numbers": ["+12065550100", "+12065550100"]}, "unique"),
        ({"prune_phone_numbers": "yes"}, "boolean"),
        ({"sender_id": "1234"}, "sender_id"),
        ({"sender_id": "-Acme"}, "sender_id"),
        ({"origination_number": "12065550100"}, "E.164"),
        ({"sms_type": "Urgent"}, "sms_type"),
        ({"max_price_usd": 0}, "positive"),
        ({"max_price_usd": "nan"}, "positive"),
        ({"entity_id": "only-one"}, "supplied together"),
        ({"entity_id": "x" * 51, "template_id": "t"}, "1-50"),
        ({"display_name": "x" * 101}, "at most 100"),
        ({"kms_master_key_id": 123}, "must be a string"),
        ({"tracing_config": "Maximum"}, "tracing_config"),
        ({"deletion_protection": "yes"}, "deletion_protection"),
        ({"topic_policy": "not-json"}, "Expecting value"),
        ({"data_protection_policy": []}, "JSON object"),
    ],
)
def test_invalid_config_fails_before_cloud_mutation(config: dict, message: str) -> None:
    driver, sns = _driver()
    result = driver.provision(_spec(config))
    assert not result.ok and message in result.message
    assert sns.calls == []


def test_invalid_operator_identity_fails_before_cloud_mutation() -> None:
    sns = FakeSNS()
    driver = SNSSmsDriver(config=SNSSmsConfig(region="", account_id="account"), client=sns)
    result = driver.provision(_spec())
    assert not result.ok and "account_id" in result.message and sns.calls == []


def test_non_object_config_fails_before_cloud_mutation() -> None:
    driver, sns = _driver()
    result = driver.provision(_spec([]))  # type: ignore[arg-type]
    assert not result.ok and "must be an object" in result.message and sns.calls == []


def test_native_requests_match_current_botocore_shapes() -> None:
    driver, sns = _driver()
    config = {
        "display_name": "Alerts",
        "kms_master_key_id": "alias/aws/sns",
        "topic_policy": {"Version": "2012-10-17", "Statement": []},
        "data_protection_policy": {"Name": "audit"},
        "phone_numbers": ["+12065550101"],
    }
    result = driver.provision(_spec(config))
    assert result.ok
    assert driver.provision(_spec(config)).ok
    assert driver.update(_update_spec(result.handle, config={**config, "phone_numbers": []})).ok
    assert driver.deprovision(_deprovision_spec(result.handle, {}), force_destroy=True).ok
    model = Session().get_service_model("sns")
    for method, operation in {
        "create_topic": "CreateTopic",
        "tag_resource": "TagResource",
        "set_topic_attributes": "SetTopicAttributes",
        "put_data_protection_policy": "PutDataProtectionPolicy",
        "subscribe": "Subscribe",
        "list_subscriptions_by_topic": "ListSubscriptionsByTopic",
        "unsubscribe": "Unsubscribe",
        "delete_topic": "DeleteTopic",
        "get_sms_sandbox_account_status": "GetSMSSandboxAccountStatus",
    }.items():
        validate_parameters(sns.kwargs_for(method), model.operation_model(operation).input_shape)


def test_schema_registration_cost_availability_and_runtime_config() -> None:
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    driver, _ = _driver()
    assert driver.config_schema()["additionalProperties"] is False
    assert "SMS_PROVIDER" in driver.binding_schema().env_vars
    assert ("sms", "sns_sms") in PLUGIN.managed_service_drivers
    assert SERVICE_CODE_BY_VARIANT[("sms", "sns_sms")] == "AmazonSNS"
    entry = next(
        item
        for item in MATRIX.managed_services
        if item.plugin_id == "aws" and item.kind == "sms" and item.variant == "sns_sms"
    )
    assert entry.status == "preview" and "SMS_TOPIC_ARN" in entry.binding_envs
    cluster = SimpleNamespace(
        slug="cluster",
        region="us-west-2",
        provider_config={
            "account_id": "123456789012",
            "sns_sms_topic_name_prefix": "messages",
            "sns_sms_sender_id_default": "Acme",
            "sns_sms_type_default": "Promotional",
            "deletion_protection_default": False,
        },
        auth_config={},
    )
    config = managed_config_for("aws", cluster, kind="sms", variant="sns_sms")
    assert isinstance(config, SNSSmsConfig)
    assert config.region == "us-west-2" and config.topic_name_prefix == "messages"
    assert config.sender_id_default == "Acme" and config.sms_type_default == "Promotional"
    assert config.deletion_protection_default is False


def test_partition_is_derived_for_govcloud_and_china() -> None:
    sns = FakeSNS()
    gov = SNSSmsDriver(config=SNSSmsConfig(region="us-gov-west-1", account_id="123456789012"), client=sns)
    china = SNSSmsDriver(config=SNSSmsConfig(region="cn-north-1", account_id="123456789012"), client=sns)
    assert gov._topic_arn("sms").startswith("arn:aws-us-gov:")
    assert china._topic_arn("sms").startswith("arn:aws-cn:")


def test_another_tenants_sms_topic_cannot_be_named_and_retagged() -> None:
    """topic_name is tenant-set; the SMS marker alone is not ownership (#1961)."""
    driver, sns = _driver()
    arn = sns.seed("sms-alerts", owned=True)
    sns.tags[arn]["astrolift.io/organization"] = "globex"

    result = driver.provision(_spec({"topic_name": "sms-alerts"}))

    assert not result.ok and "refusing to adopt" in result.message
    assert "tag_resource" not in sns.names()
