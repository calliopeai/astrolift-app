"""Durable paid throughput: fresh activity instances and actual SDK wire shapes."""

# ruff: noqa: N803
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

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
        stub.add_response("create_provisioned_model_throughput", {"provisionedModelArn": ARN})
        # The real SDK validates its response model too; a native observation
        # is required because an idempotency reply may refer to an older intent.
        _expected, tags = fresh(client)._throughput.expected(spec(), MODEL)
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
