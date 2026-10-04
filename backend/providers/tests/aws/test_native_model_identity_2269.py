"""Actual SDK wire validation; GUID ownership refuses before native IAM effects."""

import json
from copy import deepcopy
from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import Stubber

from _sdk.cloud_credentials import CloudCredential
from aws.identity_irsa import IRSAConfig
from aws.identity_native import NativeIRSADriver

ACCOUNT = "123456789012"
REGION = "us-east-1"
NAME = "astrolift-tenant-example"
OWNER = {
    "organization": "11111111-1111-4111-8111-111111111111",
    "app": "22222222-2222-4222-8222-222222222222",
    "cluster": "33333333-3333-4333-8333-333333333333",
}
ISSUER = f"oidc.eks.{REGION}.amazonaws.com/id/ABC123"
SUBJECTS = [f"system:serviceaccount:org-example:{NAME}"]
PERMISSIONS = [
    {
        "Effect": "Allow",
        "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-text-express-v1",
    }
]


def config():
    return IRSAConfig(
        region=REGION,
        account_id=ACCOUNT,
        cluster_oidc_issuer=ISSUER,
        credential=CloudCredential(cloud="aws", declared_account=ACCOUNT),
    )


def client(service, **kwargs):
    return boto3.client(
        service,
        region_name=REGION,
        aws_access_key_id="synthetic-key",
        aws_secret_access_key="synthetic-secret",
        **kwargs,
    )


def trust():
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": f"arn:aws:iam::{ACCOUNT}:oidc-provider/{ISSUER}"},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {"StringEquals": {f"{ISSUER}:aud": "sts.amazonaws.com", f"{ISSUER}:sub": SUBJECTS}},
            }
        ],
    }


def wire_role(value=None):
    result = deepcopy(role() if value is None else value)
    result["AssumeRolePolicyDocument"] = json.dumps(result["AssumeRolePolicyDocument"])
    return {"Role": result}


def role():
    return {
        "RoleName": NAME,
        "Path": "/",
        "RoleId": "AROANATIVEIDENTITY1234",
        "Arn": f"arn:aws:iam::{ACCOUNT}:role/{NAME}",
        "CreateDate": datetime.now(UTC),
        "AssumeRolePolicyDocument": trust(),
        "Tags": [{"Key": "astrolift.io/managed-by", "Value": "platform"}]
        + [{"Key": f"astrolift.io/{k}-id", "Value": v} for k, v in OWNER.items()],
    }


@pytest.mark.parametrize(
    "corruption",
    [
        "foreign_app",
        "foreign_organization",
        "foreign_cluster",
        "missing_tags",
        "duplicate_tags",
        "path",
        "foreign_oidc",
        "foreign_subject",
        "wildcard_subject",
        "extra_statement",
    ],
)
def test_foreign_or_ambiguous_existing_role_no_write(corruption):
    observed = role()
    if corruption.startswith("foreign_") and corruption.split("_")[1] in OWNER:
        key = "astrolift.io/" + corruption.split("_")[1] + "-id"
        next(t for t in observed["Tags"] if t["Key"] == key)["Value"] = "44444444-4444-4444-8444-444444444444"
    elif corruption == "missing_tags":
        observed["Tags"] = observed["Tags"][:1]
    elif corruption == "duplicate_tags":
        observed["Tags"].append(observed["Tags"][0])
    elif corruption == "path":
        observed["Arn"] = f"arn:aws:iam::{ACCOUNT}:role/foreign/{NAME}"
    elif corruption == "foreign_oidc":
        observed["AssumeRolePolicyDocument"]["Statement"][0]["Principal"]["Federated"] += "FOREIGN"
    elif corruption in ("foreign_subject", "wildcard_subject"):
        observed["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]["StringEquals"][f"{ISSUER}:sub"] = [
            "system:serviceaccount:foreign:other" if corruption == "foreign_subject" else "*"
        ]
    else:
        observed["AssumeRolePolicyDocument"]["Statement"].append(
            deepcopy(observed["AssumeRolePolicyDocument"]["Statement"][0])
        )
    iam = client("iam")
    with Stubber(iam) as stub:
        stub.add_response("get_role", wire_role(observed), {"RoleName": NAME})
        driver = NativeIRSADriver(config=config(), iam_client=iam)
        with pytest.raises(ValueError, match=r"ownership|trust"):
            driver.reconcile_managed_identity(name=NAME, permissions=PERMISSIONS, owner=OWNER, subjects=SUBJECTS)
        stub.assert_no_pending_responses()


def test_owned_union_update_readback_and_empty_union_removes_only_named_policy():
    iam = client("iam")
    driver = NativeIRSADriver(config=config(), iam_client=iam)
    with Stubber(iam) as stub:
        for permissions in (PERMISSIONS, []):
            stub.add_response("get_role", wire_role(), {"RoleName": NAME})
            stub.add_response(
                "update_assume_role_policy", {}, {"RoleName": NAME, "PolicyDocument": json.dumps(trust())}
            )
            if permissions:
                stub.add_response(
                    "put_role_policy",
                    {},
                    {
                        "RoleName": NAME,
                        "PolicyName": "astrolift-workload-policy",
                        "PolicyDocument": json.dumps({"Version": "2012-10-17", "Statement": permissions}),
                    },
                )
            else:
                stub.add_response(
                    "delete_role_policy", {}, {"RoleName": NAME, "PolicyName": "astrolift-workload-policy"}
                )
            stub.add_response("get_role", wire_role(), {"RoleName": NAME})
            if permissions:
                stub.add_response(
                    "get_role_policy",
                    {
                        "RoleName": NAME,
                        "PolicyName": "astrolift-workload-policy",
                        "PolicyDocument": json.dumps({"Version": "2012-10-17", "Statement": permissions}),
                    },
                    {"RoleName": NAME, "PolicyName": "astrolift-workload-policy"},
                )
            else:
                stub.add_client_error(
                    "get_role_policy",
                    "NoSuchEntity",
                    expected_params={"RoleName": NAME, "PolicyName": "astrolift-workload-policy"},
                )
            assert (
                driver.reconcile_managed_identity(name=NAME, permissions=permissions, owner=OWNER, subjects=SUBJECTS)
                == role()["Arn"]
            )
            assert driver.verify_managed_identity(name=NAME, permissions=permissions, owner=OWNER, subjects=SUBJECTS)
        stub.assert_no_pending_responses()


def test_create_collision_does_not_adopt_foreign_role():
    iam = client("iam")
    observed = role()
    observed["Tags"] = []
    with Stubber(iam) as stub:
        stub.add_client_error("get_role", "NoSuchEntity", expected_params={"RoleName": NAME})
        stub.add_client_error(
            "create_role",
            "EntityAlreadyExists",
            expected_params={
                "Path": "/",
                "RoleName": NAME,
                "AssumeRolePolicyDocument": json.dumps(trust()),
                "Tags": role()["Tags"],
            },
        )
        stub.add_response("get_role", wire_role(observed), {"RoleName": NAME})
        driver = NativeIRSADriver(config=config(), iam_client=iam)
        with pytest.raises(ValueError, match="ownership"):
            driver.reconcile_managed_identity(name=NAME, permissions=PERMISSIONS, owner=OWNER, subjects=SUBJECTS)
        stub.assert_no_pending_responses()


def test_new_role_wire_shape_exact_tags_subjects_policy():
    iam = client("iam")
    with Stubber(iam) as stub:
        stub.add_client_error("get_role", "NoSuchEntity", expected_params={"RoleName": NAME})
        stub.add_response(
            "create_role",
            wire_role(),
            {"Path": "/", "RoleName": NAME, "AssumeRolePolicyDocument": json.dumps(trust()), "Tags": role()["Tags"]},
        )
        stub.add_response(
            "put_role_policy",
            {},
            {
                "RoleName": NAME,
                "PolicyName": "astrolift-workload-policy",
                "PolicyDocument": json.dumps({"Version": "2012-10-17", "Statement": PERMISSIONS}),
            },
        )
        driver = NativeIRSADriver(config=config(), iam_client=iam)
        driver.reconcile_managed_identity(name=NAME, permissions=PERMISSIONS, owner=OWNER, subjects=SUBJECTS)
        stub.assert_no_pending_responses()


def test_absent_role_empty_cleanup_has_no_create():
    iam = client("iam")
    with Stubber(iam) as stub:
        stub.add_client_error("get_role", "NoSuchEntity", expected_params={"RoleName": NAME})
        driver = NativeIRSADriver(config=config(), iam_client=iam)
        driver.reconcile_managed_identity(name=NAME, permissions=[], owner=OWNER, subjects=SUBJECTS)
        stub.assert_no_pending_responses()


def test_native_private_clients_ignore_environment_endpoints_and_verify_account(monkeypatch):
    for key in ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_IAM", "AWS_ENDPOINT_URL_STS"):
        monkeypatch.setenv(key, "https://synthetic-untrusted-endpoint.invalid")
    session = boto3.Session(
        region_name=REGION, aws_access_key_id="synthetic-key", aws_secret_access_key="synthetic-secret"
    )
    original = session.client
    clients, stubs, constructed = {}, {}, {}

    def build(service, **kwargs):
        constructed[service] = kwargs["config"]
        actual = original(service, **kwargs)
        clients[service] = actual
        if service == "sts":
            stub = Stubber(actual)
            for _ in range(8):
                stub.add_response(
                    "get_caller_identity",
                    {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/synthetic", "UserId": "synthetic"},
                )
            stub.activate()
            stubs[service] = stub
        elif service == "iam":
            stub = Stubber(actual)
            stub.add_response("get_role", wire_role(), {"RoleName": NAME})
            stub.activate()
            stubs[service] = stub
        return actual

    monkeypatch.setattr(session, "client", build)
    driver = NativeIRSADriver(config=config(), session=session)
    try:
        assert driver._managed_role(NAME, OWNER, SUBJECTS)
        for service in ("sts", "iam"):
            actual = clients[service]
            assert ".invalid" not in actual.meta.endpoint_url
            bounds = actual.meta.config
            assert bounds.connect_timeout == 5 and bounds.read_timeout == 20
            assert bounds.retries["total_max_attempts"] == 1
            assert constructed[service].ignore_configured_endpoint_urls is True
        stubs["iam"].assert_no_pending_responses()
    finally:
        driver.close()
        for stub in stubs.values():
            stub.deactivate()
