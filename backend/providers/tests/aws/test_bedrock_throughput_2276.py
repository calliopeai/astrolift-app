"""Durable paid throughput: fresh activity instances and actual SDK wire shapes."""

# ruff: noqa: N803
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError, ParamValidationError
from botocore.stub import Stubber
from botocore.validate import validate_parameters

from _sdk.cloud_credentials import CloudCredential
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.model_endpoint_bedrock import AmazonBedrockConfig, AmazonBedrockDriver

SID = "11111111-1111-4111-8111-111111111111"
ORG = "22222222-2222-4222-8222-222222222222"
ARN = "arn:aws:bedrock:us-east-1:123456789012:provisioned-model/abcdefgh1234"
MODEL = "anthropic.claude-3-haiku-20240307-v1:0"
MODEL_ARN = f"arn:aws:bedrock:us-east-1::foundation-model/{MODEL}"


def spec(**overrides: Any) -> ProvisionSpec:
    values = dict(
        organization_id=ORG,
        organization_slug="acme",
        app_id=SID,
        app_slug="api",
        environment_id=SID,
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="model",
        size="small",
        managed_service_id=SID,
        config={"model_id": MODEL, "provisioned_throughput": "OneMonth", "model_units": 1},
    )
    return ProvisionSpec(**(values | overrides))


class Logs:
    def create_log_group(self, **kwargs):
        pass

    def put_retention_policy(self, **kwargs):
        pass

    def delete_log_group(self, **kwargs):
        pass


class NativeBedrock:
    def __init__(self):
        self.row = None
        self.tags = []
        self.creates = []
        self.deletes = []
        self.tokens = {}
        self.read_error = None
        self.lose_reply = False

    def create_provisioned_model_throughput(self, **kwargs):
        assert isinstance(kwargs["tags"], list)
        assert all(set(tag) == {"key", "value"} for tag in kwargs["tags"])
        assert len(kwargs["clientRequestToken"]) >= 1
        self.creates.append(kwargs)
        token = kwargs["clientRequestToken"]
        if token not in self.tokens:
            self.tokens[token] = ARN
            self.tags = kwargs["tags"]
            self.row = dict(
                provisionedModelArn=ARN,
                provisionedModelName=kwargs["provisionedModelName"],
                modelArn=MODEL_ARN,
                desiredModelArn=MODEL_ARN,
                modelUnits=kwargs["modelUnits"],
                desiredModelUnits=kwargs["modelUnits"],
                status="Creating",
                commitmentDuration=kwargs["commitmentDuration"],
                commitmentExpirationTime=datetime.now(UTC) + timedelta(days=30),
            )
        if self.lose_reply:
            self.lose_reply = False
            raise TimeoutError("private canary response lost")
        return {"provisionedModelArn": self.tokens[token]}

    def get_provisioned_model_throughput(self, *, provisionedModelId):
        if self.read_error:
            raise self.read_error
        if self.row and provisionedModelId in (ARN, self.row["provisionedModelName"]):
            return dict(self.row)
        raise ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}}, "GetProvisionedModelThroughput"
        )

    def list_tags_for_resource(self, *, resourceARN):
        assert resourceARN == ARN
        return {"tags": list(self.tags)}

    def delete_provisioned_model_throughput(self, *, provisionedModelId):
        assert provisionedModelId == ARN
        self.deletes.append(provisionedModelId)
        self.row = None
        return {}


def fresh(native, *, region="us-east-1", account="123456789012"):
    return AmazonBedrockDriver(
        config=AmazonBedrockConfig(region=region, credential=CloudCredential(cloud="aws", declared_account=account)),
        bedrock_client=native,
        logs_client=Logs(),
    )


def selected(handle):
    return ServiceHandle(handle=handle, managed_service_id=SID, organization_id=ORG)


@pytest.mark.parametrize(
    "native_state,expected",
    [
        ("Creating", "provisioning"),
        ("Updating", "updating"),
        ("Failed", "error"),
        ("InService", "available"),
        ("Unknown", "error"),
    ],
)
def test_fresh_activity_observes_native_readiness(native_state, expected):
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    assert result.ok and not result.ready
    assert ARN in result.handle and len(result.handle) <= 512
    native.row["status"] = native_state
    assert fresh(native).status(selected(result.handle)).state == expected


def test_read_failure_cannot_report_available_or_create_again():
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    native.read_error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "private canary"}}, "GetProvisionedModelThroughput"
    )
    assert fresh(native).status(selected(result.handle)).state == "error"
    retry = fresh(native).provision(spec(recorded_handle=result.handle))
    assert not retry.ok and "private canary" not in str(retry)
    assert len(native.creates) == 1


def test_lost_create_reply_recovers_exact_resource_without_second_purchase():
    native = NativeBedrock()
    native.lose_reply = True
    assert not fresh(native).provision(spec()).ok
    retry = fresh(native).provision(spec())
    assert retry.ok and ARN in retry.handle and len(native.creates) == 1
    wrong_source = fresh(native).provision(
        spec(config={"model_id": "other.model", "provisioned_throughput": "OneMonth"})
    )
    wrong_org = fresh(native).provision(spec(organization_id=SID))
    assert not wrong_source.ok and not wrong_org.ok and len(native.creates) == 1


def test_binding_uses_provisioned_arn_and_refuses_foreign_owner_or_region():
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    native.row["status"] = "InService"
    binding = fresh(native).binding(selected(result.handle), spec().config)
    assert binding.env_vars["BEDROCK_MODEL_ID"].literal == ARN
    assert [grant.resource for grant in binding.iam_grants] == [ARN]
    with pytest.raises(ManagedServiceError):
        fresh(native).binding(replace(selected(result.handle), managed_service_id=ORG), spec().config)
    with pytest.raises(ManagedServiceError):
        fresh(native, region="us-west-2").binding(selected(result.handle), spec().config)
    with pytest.raises(ManagedServiceError):
        fresh(native, account="999999999999").binding(selected(result.handle), spec().config)


def test_paid_update_and_commitment_force_do_not_claim_false_effects():
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    native.row["status"] = "InService"
    change = fresh(native).update(
        UpdateSpec(
            handle=result.handle,
            managed_service_id=SID,
            organization_id=ORG,
            config={"provisioned_throughput": "SixMonths"},
        )
    )
    assert not change.ok and not change.retryable
    removal = DeprovisionSpec(handle=result.handle, managed_service_id=SID, organization_id=ORG)
    assert not fresh(native).deprovision(removal, force_destroy=True).ok
    assert not native.deletes
    native.row["commitmentExpirationTime"] = datetime.now(UTC) - timedelta(seconds=1)
    assert fresh(native).deprovision(removal).ok and native.deletes == [ARN]
    assert fresh(native).deprovision(removal).ok and native.deletes == [ARN]


def test_actual_sdk_create_wire_accepts_tags_token_and_native_identity():
    client = boto3.client(
        "bedrock", region_name="us-east-1", aws_access_key_id="synthetic", aws_secret_access_key="synthetic"
    )
    with Stubber(client) as stub:
        stub.add_client_error("get_provisioned_model_throughput", service_error_code="ResourceNotFoundException")
        stub.add_client_error("get_provisioned_model_throughput", service_error_code="ResourceNotFoundException")
        _expected, tags = fresh(client)._throughput.expected(spec(), MODEL)
        from aws.managed._bedrock_throughput import digest

        stub.add_response(
            "create_provisioned_model_throughput",
            {"provisionedModelArn": ARN},
            expected_params={
                "modelUnits": 1,
                "provisionedModelName": "astrolift-" + SID.replace("-", ""),
                "modelId": MODEL,
                "commitmentDuration": "OneMonth",
                "tags": tags,
                "clientRequestToken": digest(["astrolift-bedrock-pt1", SID]),
            },
        )
        # The real SDK validates its response model too; a native observation
        # is required because an idempotency reply may refer to an older intent.
        stamp = datetime.now(UTC)
        stub.add_response(
            "get_provisioned_model_throughput",
            {
                "provisionedModelArn": ARN,
                "provisionedModelName": "astrolift-" + SID.replace("-", ""),
                "modelArn": MODEL_ARN,
                "desiredModelArn": MODEL_ARN,
                "foundationModelArn": MODEL_ARN,
                "modelUnits": 1,
                "desiredModelUnits": 1,
                "status": "Creating",
                "commitmentDuration": "OneMonth",
                "creationTime": stamp,
                "lastModifiedTime": stamp,
            },
        )
        stub.add_response("list_tags_for_resource", {"tags": tags})
        result = fresh(client).provision(spec())
        assert result.ok and ARN in result.handle
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "field,value",
    [
        ("desiredModelArn", "arn:aws:bedrock:us-east-1::foundation-model/other.model"),
        ("modelArn", "arn:aws:bedrock:us-east-1::foundation-model/other.model"),
        ("desiredModelUnits", 2),
        ("modelUnits", True),
        ("commitmentDuration", "SixMonths"),
        ("provisionedModelArn", ARN.replace("123456789012", "999999999999")),
    ],
)
def test_native_target_withdrawal_refuses_all_paid_operations(field, value):
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    native.row["status"] = "InService"
    native.row[field] = value
    assert fresh(native).status(selected(result.handle)).state == "error"
    with pytest.raises(ManagedServiceError):
        fresh(native).binding(selected(result.handle), spec().config)
    assert not fresh(native).update(UpdateSpec(handle=result.handle, managed_service_id=SID, organization_id=ORG)).ok
    assert (
        not fresh(native)
        .deprovision(
            DeprovisionSpec(handle=result.handle, managed_service_id=SID, organization_id=ORG), force_destroy=True
        )
        .ok
    )
    assert not native.deletes and len(native.creates) == 1


@pytest.mark.parametrize(
    "tag,value",
    [
        ("astrolift.io/managed-by", "foreign"),
        ("astrolift.io/managed_service_id", ORG),
        ("astrolift.io/bedrock-organization-id", SID),
        ("astrolift.io/bedrock-intent", "0" * 64),
        ("astrolift.io/bedrock-model", "0" * 64),
        ("astrolift.io/bedrock-units", "2"),
    ],
)
def test_live_ownership_and_intent_withdrawal_refuses_binding(tag, value):
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    native.row["status"] = "InService"
    native.tags = [{"key": row["key"], "value": value if row["key"] == tag else row["value"]} for row in native.tags]
    with pytest.raises(ManagedServiceError):
        fresh(native).binding(selected(result.handle), spec().config)
    assert not fresh(native).provision(spec(recorded_handle=result.handle)).ok
    assert len(native.creates) == 1


@pytest.mark.parametrize("kind", ["duplicate", "uppercase", "missing", "not_list", "oversized"])
def test_malformed_ownership_cannot_be_adopted(kind):
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    native.row["status"] = "InService"
    if kind == "duplicate":
        native.tags.append(native.tags[0])
    elif kind == "uppercase":
        native.tags = [{"Key": row["key"], "Value": row["value"]} for row in native.tags]
    elif kind == "missing":
        native.tags = []
    elif kind == "not_list":
        native.list_tags_for_resource = lambda **_: {"tags": None}
    else:
        native.tags = [{"key": str(i), "value": "v"} for i in range(201)]
    assert fresh(native).status(selected(result.handle)).state == "error"
    assert not fresh(native).provision(spec()).ok and len(native.creates) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"managed_service_id": "1"},
        {"organization_id": "1"},
        {"config": {"provisioned_throughput": "OneMonth", "model_units": True}},
        {"config": {"provisioned_throughput": "OneMonth", "model_units": 0}},
        {"config": {"provisioned_throughput": "OneMonth", "model_units": 2147483648}},
        {"config": {"provisioned_throughput": "Forever"}},
        {"config": {"provisioned_throughput": "OneMonth", "model_id": "x" * 1100}},
        {"config": {"provisioned_throughput": "OneMonth", "model_id": MODEL_ARN.replace("us-east-1", "us-west-2")}},
        {"tags": {"oversize": "x" * 257}},
    ],
)
def test_invalid_paid_inputs_fail_before_any_provider_effect(override):
    native = NativeBedrock()
    assert not fresh(native).provision(spec(**override)).ok
    assert not native.creates and not native.deletes


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("private canary"),
        RuntimeError("ResourceNotFoundException private canary"),
        ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "private canary"}}, "GetProvisionedModelThroughput"
        ),
    ],
)
def test_only_real_sdk_not_found_can_start_allocation(failure):
    native = NativeBedrock()
    native.read_error = failure
    result = fresh(native).provision(spec())
    assert not result.ok and not native.creates and "private canary" not in str(result)
    assert (
        fresh(native)
        .status(ServiceHandle(handle="model_endpoint/legacy", managed_service_id=SID, organization_id=ORG))
        .state
        == "error"
    )


def test_recorded_missing_native_is_not_replaced_or_available():
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    native.row = None
    assert fresh(native).status(selected(result.handle)).state == "deprovisioned"
    assert not fresh(native).provision(spec(recorded_handle=result.handle)).ok
    assert len(native.creates) == 1


@pytest.mark.parametrize("malformed", ["model_endpoint/pt1|bad", "other/legacy", "model_endpoint/" + "x" * 513])
def test_malformed_handles_fail_before_native_reads(malformed):
    native = NativeBedrock()
    native.get_provisioned_model_throughput = lambda **_: pytest.fail("must validate before native read")
    assert fresh(native).status(selected(malformed)).state == "error"
    with pytest.raises(ManagedServiceError):
        fresh(native).binding(selected(malformed), spec().config)
    assert (
        not fresh(native).deprovision(DeprovisionSpec(handle=malformed, managed_service_id=SID, organization_id=ORG)).ok
    )


def test_paid_noop_is_live_and_snapshot_is_not_a_copy_primitive():
    native = NativeBedrock()
    driver = fresh(native)
    result = driver.provision(spec())
    native.row["status"] = "InService"
    updated = fresh(native).update(
        UpdateSpec(handle=result.handle, managed_service_id=SID, organization_id=ORG, config=spec().config)
    )
    assert updated.ok and updated.handle == result.handle
    with pytest.raises(ManagedServiceError, match="no snapshot primitive"):
        fresh(native).snapshot(selected(result.handle))
    assert (
        not fresh(native)
        .update(UpdateSpec(handle=result.handle, managed_service_id=SID, organization_id=ORG, size="large"))
        .ok
    )
    assert len(native.creates) == 1


def test_actual_sdk_runtime_invocation_uses_only_provisioned_arn():
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    native.row["status"] = "InService"
    binding = fresh(native).binding(selected(result.handle), spec().config)
    runtime = boto3.client(
        "bedrock-runtime", region_name="us-east-1", aws_access_key_id="synthetic", aws_secret_access_key="synthetic"
    )
    body = b'{"synthetic":true}'
    with Stubber(runtime) as stub:
        stub.add_response(
            "invoke_model",
            {"body": b"{}", "contentType": "application/json"},
            expected_params={"modelId": ARN, "body": body, "contentType": "application/json"},
        )
        runtime.invoke_model(
            modelId=binding.env_vars["BEDROCK_MODEL_ID"].literal, body=body, contentType="application/json"
        )
        stub.assert_no_pending_responses()


def legacy(native):
    from aws.managed._base import tags_for

    original = spec()
    original_handle = "model_endpoint/astrolift-acme-api-prod-model"
    native.row = dict(
        provisionedModelArn=ARN,
        provisionedModelName="astrolift-acme-api-prod-model",
        modelArn=MODEL_ARN,
        desiredModelArn=MODEL_ARN,
        modelUnits=1,
        desiredModelUnits=1,
        status="Creating",
        commitmentDuration="OneMonth",
        commitmentExpirationTime=datetime.now(UTC) + timedelta(days=30),
    )
    native.tags = [{"key": tag["Key"], "value": tag["Value"]} for tag in tags_for(original)]
    return original_handle


def test_explicit_original_spec_recovers_legacy_paid_resource_without_effects():
    native = NativeBedrock()
    old = legacy(native)
    # A bare old handle cannot establish its intended target.
    assert fresh(native).status(selected(old)).state == "error"
    with pytest.raises(ManagedServiceError):
        fresh(native).binding(selected(old), spec().config)
    result = fresh(native).provision(spec(recorded_handle=old))
    assert result.ok and not result.ready and ARN in result.handle
    assert len(result.handle) <= 512 and not native.creates
    native.row["status"] = "InService"
    assert fresh(native).status(selected(result.handle)).state == "available"
    binding = fresh(native).binding(selected(result.handle), spec().config)
    assert binding.env_vars["BEDROCK_MODEL_ID"].literal == ARN
    assert "unproved" in binding.notes and "persist at" not in binding.notes
    assert fresh(native).provision(spec(recorded_handle=result.handle)).handle == result.handle
    native.tags[0]["value"] = "withdrawn"
    assert fresh(native).status(selected(result.handle)).state == "error"


@pytest.mark.parametrize(
    "override",
    [
        {"organization_id": SID},
        {"app_id": ORG},
        {"environment_id": ORG},
        {"tenant_cluster_id": "other"},
        {"config": {"model_id": "other.model", "provisioned_throughput": "OneMonth", "model_units": 1}},
    ],
)
def test_recovered_legacy_intent_cannot_be_replayed_with_substituted_tuple(override):
    native = NativeBedrock()
    recovered = fresh(native).provision(spec(recorded_handle=legacy(native)))
    assert recovered.ok
    assert not fresh(native).provision(spec(recorded_handle=recovered.handle, **override)).ok
    assert not native.creates


@pytest.mark.parametrize(
    "dimension", ["managed-by", "managed_service_id", "organization", "app", "environment", "cluster", "isolation"]
)
def test_legacy_recovery_requires_every_original_owner_dimension(dimension):
    native = NativeBedrock()
    old = legacy(native)
    native.tags = [tag for tag in native.tags if tag["key"] != "astrolift.io/" + dimension]
    assert not fresh(native).provision(spec(recorded_handle=old)).ok
    assert not native.creates


@pytest.mark.parametrize(
    "config",
    [
        {"provisioned_throughput": "OneMonth"},
        {"model_id": MODEL, "provisioned_throughput": "OneMonth"},
        {"model_units": 1, "provisioned_throughput": "OneMonth"},
        {"model_id": MODEL, "model_units": 2, "provisioned_throughput": "OneMonth"},
        {"model_id": MODEL, "model_units": 1, "provisioned_throughput": "SixMonths"},
    ],
)
def test_legacy_recovery_never_guesses_source_or_commitment(config):
    native = NativeBedrock()
    old = legacy(native)
    assert not fresh(native).provision(spec(recorded_handle=old, config=config)).ok
    assert not native.creates


def test_changed_intent_idempotent_reply_is_verified_not_reported_matching():
    native = NativeBedrock()
    native.lose_reply = True
    assert not fresh(native).provision(spec()).ok
    original_get = native.get_provisioned_model_throughput

    def temporarily_missing_name(*, provisionedModelId):
        if not provisionedModelId.startswith("arn:"):
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "propagating"}},
                "GetProvisionedModelThroughput",
            )
        return original_get(provisionedModelId=provisionedModelId)

    native.get_provisioned_model_throughput = temporarily_missing_name
    result = fresh(native).provision(
        spec(config={"model_id": "other.model", "model_units": 1, "provisioned_throughput": "OneMonth"})
    )
    assert not result.ok and len(native.tokens) == 1
    assert native.creates[0]["clientRequestToken"] == native.creates[1]["clientRequestToken"]
    assert native.row["modelArn"] == MODEL_ARN


class StoredLogs(Logs):
    def __init__(self):
        self.groups = set()
        self.deleted = []
        self.failure = None

    def create_log_group(self, *, logGroupName):
        self.groups.add(logGroupName)

    def delete_log_group(self, *, logGroupName):
        self.deleted.append(logGroupName)
        if self.failure:
            raise self.failure
        self.groups.discard(logGroupName)


def test_lost_native_delete_reply_and_log_failure_retry_exact_original_group():
    native, logs = NativeBedrock(), StoredLogs()

    def driver():
        return AmazonBedrockDriver(
            config=AmazonBedrockConfig(region="us-east-1"), bedrock_client=native, logs_client=logs
        )

    result = driver().provision(spec())
    native.row["commitmentExpirationTime"] = datetime.now(UTC) - timedelta(seconds=1)
    original_delete = native.delete_provisioned_model_throughput

    def lost_reply(**kwargs):
        original_delete(**kwargs)
        raise TimeoutError("private canary")

    native.delete_provisioned_model_throughput = lost_reply
    removal = DeprovisionSpec(handle=result.handle, managed_service_id=SID, organization_id=ORG)
    assert not driver().deprovision(removal, delete_data=True).ok
    assert native.row is None and logs.groups
    logs.failure = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "private canary"}}, "DeleteLogGroup"
    )
    refused = driver().deprovision(removal, delete_data=True)
    assert not refused.ok and refused.errors == ["log_cleanup_unconfirmed"] and logs.groups
    assert "private canary" not in str(refused)
    logs.failure = None
    assert driver().deprovision(removal, delete_data=True).ok and not logs.groups
    assert set(logs.deleted) == {"/aws/astrolift/bedrock/astrolift-" + SID.replace("-", "")}
    assert native.deletes == [ARN]


def test_gone_recovered_legacy_refuses_unproved_log_cleanup():
    native, logs = NativeBedrock(), StoredLogs()
    old = legacy(native)
    recovered = fresh(native).provision(spec(recorded_handle=old))
    assert recovered.ok and len(recovered.handle) <= 512
    native.row = None
    logs.groups.add("/aws/astrolift/bedrock/astrolift-acme-api-prod-model")
    driver = AmazonBedrockDriver(
        config=AmazonBedrockConfig(region="us-east-1"), bedrock_client=native, logs_client=logs
    )
    refused = driver.deprovision(
        DeprovisionSpec(handle=recovered.handle, managed_service_id=SID, organization_id=ORG), delete_data=True
    )
    assert not refused.ok
    assert logs.groups == {"/aws/astrolift/bedrock/astrolift-acme-api-prod-model"} and not logs.deleted
    assert not native.deletes


def test_changed_log_prefix_after_native_gone_refuses_wrong_group_cleanup():
    native, logs = NativeBedrock(), StoredLogs()
    original = AmazonBedrockDriver(
        config=AmazonBedrockConfig(region="us-east-1"), bedrock_client=native, logs_client=logs
    )
    result = original.provision(spec())
    native.row = None
    changed = AmazonBedrockDriver(
        config=AmazonBedrockConfig(region="us-east-1", invocation_log_group_prefix="/different-prefix"),
        bedrock_client=native,
        logs_client=logs,
    )
    removal = DeprovisionSpec(handle=result.handle, managed_service_id=SID, organization_id=ORG)
    assert not changed.deprovision(removal, delete_data=True).ok
    assert logs.groups and not logs.deleted
    assert original.deprovision(removal, delete_data=True).ok and not logs.groups


def test_maximum_supported_arn_handle_preserves_full_hashes_under_storage_limit():
    from aws.managed._bedrock_throughput import PaidHandle

    longest_arn = "arn:aws-us-gov:bedrock:" + "r" * 20 + ":123456789012:provisioned-model/abcdefgh1234"
    saved = PaidHandle(
        arn=longest_arn,
        service=SID,
        organization=ORG,
        units=2147483647,
        term="SixMonths",
        model_hash="0" * 64,
        intent="1" * 64,
        legacy_tags_hash="2" * 64,
        legacy_name="n" * 63,
        log_prefix_hash="3" * 64,
    )
    assert len(saved.encode()) == 504
    assert PaidHandle.parse(saved.encode()) == saved
    with pytest.raises(ManagedServiceError):
        PaidHandle.parse(saved.encode() + "x" * 9)


def test_prior_version_readable_but_unknown_log_identity_cannot_claim_cleanup():
    from aws.managed._bedrock_throughput import PaidHandle

    native = NativeBedrock()
    result = fresh(native).provision(spec())
    parsed = PaidHandle.parse(result.handle)
    prior = replace(parsed, log_prefix_hash="").encode()
    assert prior.startswith("model_endpoint/pt1|")
    native.row["status"] = "InService"
    assert fresh(native).status(selected(prior)).state == "available"
    assert fresh(native).binding(selected(prior), spec().config).env_vars["BEDROCK_MODEL_ID"].literal == ARN
    removal = DeprovisionSpec(handle=prior, managed_service_id=SID, organization_id=ORG)
    assert not fresh(native).deprovision(removal, delete_data=True).ok and not native.deletes


@pytest.mark.parametrize(
    "bad",
    [{"tags": {"owner": "platform"}}, {"tags": [{"Key": "owner", "Value": "platform"}]}, {"clientRequestToken": 123}],
)
def test_installed_sdk_rejects_old_tag_shape_and_invalid_token(bad):
    client = boto3.client(
        "bedrock", region_name="us-east-1", aws_access_key_id="synthetic", aws_secret_access_key="synthetic"
    )
    payload = {
        "modelUnits": 1,
        "provisionedModelName": "synthetic",
        "modelId": MODEL,
        "commitmentDuration": "OneMonth",
        "tags": [{"key": "owner", "value": "platform"}],
        "clientRequestToken": "synthetic-idempotency",
    }
    shape = client.meta.service_model.operation_model("CreateProvisionedModelThroughput").input_shape
    validate_parameters(payload, shape)
    with pytest.raises(ParamValidationError):
        validate_parameters(payload | bad, shape)


@pytest.mark.parametrize(
    "profile",
    ["us.anthropic.claude-sonnet-4-6", "eu.anthropic.claude-sonnet-4-6", "global.anthropic.claude-sonnet-4-6"],
)
def test_inference_profile_cannot_be_a_paid_foundation_model_source(profile):
    native = NativeBedrock()
    result = fresh(native).provision(
        spec(config={"model_id": profile, "model_units": 1, "provisioned_throughput": "OneMonth"})
    )
    assert not result.ok and not native.creates


def test_legacy_recovery_never_certifies_current_prefix_as_original_log_identity():
    native, logs = NativeBedrock(), StoredLogs()
    old = legacy(native)
    original_group = "/aws/astrolift/bedrock/astrolift-acme-api-prod-model"
    other_group = "/different-prefix/astrolift-acme-api-prod-model"
    logs.groups.update((original_group, other_group))
    driver = AmazonBedrockDriver(
        config=AmazonBedrockConfig(region="us-east-1", invocation_log_group_prefix="/different-prefix"),
        bedrock_client=native,
        logs_client=logs,
    )
    recovered = driver.provision(spec(recorded_handle=old))
    assert recovered.ok
    native.row["commitmentExpirationTime"] = datetime.now(UTC) - timedelta(seconds=1)
    refused = driver.deprovision(
        DeprovisionSpec(handle=recovered.handle, managed_service_id=SID, organization_id=ORG), delete_data=True
    )
    assert not refused.ok
    assert native.row is not None and not native.deletes
    assert logs.groups == {original_group, other_group} and not logs.deleted


@pytest.mark.parametrize("config", [{}, {"model_id": MODEL}, {"provisioned_throughput": None}])
def test_paid_update_cannot_confirm_removed_commitment(config):
    native = NativeBedrock()
    result = fresh(native).provision(spec())
    native.row["status"] = "InService"
    refused = fresh(native).update(
        UpdateSpec(handle=result.handle, managed_service_id=SID, organization_id=ORG, config=config)
    )
    assert not refused.ok and not refused.retryable
    assert len(native.creates) == 1 and not native.deletes
