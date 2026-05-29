"""Tests for AWS IRSA WorkloadIdentityDriver (#33)."""

from __future__ import annotations

import json

import pytest

from aws._errors import NotFoundError
from aws.identity_irsa import IRSAConfig, IRSADriver


@pytest.fixture
def driver(iam_client) -> IRSADriver:
    return IRSADriver(
        config=IRSAConfig(
            region="us-east-1",
            account_id="123456789012",
            cluster_oidc_issuer="oidc.eks.us-east-1.amazonaws.com/id/ABC123",
        ),
        iam_client=iam_client,
    )


# ---- create_identity_role ----------------------------------------


def test_create_role_basic(driver: IRSADriver) -> None:
    arn = driver.create_identity_role(
        name="acme-api",
        permissions=[{
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": "*",
        }],
    )
    assert arn.startswith("arn:aws:iam::123456789012:role/")
    assert "acme-api" in arn


def test_create_role_idempotent(driver: IRSADriver) -> None:
    """Re-create returns existing ARN, doesn't error."""
    a = driver.create_identity_role(name="acme-api", permissions=[])
    b = driver.create_identity_role(name="acme-api", permissions=[])
    assert a == b


def test_create_role_oidc_trust(driver: IRSADriver, iam_client) -> None:
    """Trust policy must reference the OIDC provider + audience."""
    driver.create_identity_role(name="acme-api", permissions=[])
    response = iam_client.get_role(RoleName="acme-api")
    trust = response["Role"]["AssumeRolePolicyDocument"]
    if isinstance(trust, str):
        from urllib.parse import unquote
        trust = json.loads(unquote(trust))

    statement = trust["Statement"][0]
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert "oidc-provider" in statement["Principal"]["Federated"]
    aud_key = (
        "oidc.eks.us-east-1.amazonaws.com/id/ABC123:aud"
    )
    assert (
        statement["Condition"]["StringEquals"][aud_key]
        == "sts.amazonaws.com"
    )


def test_create_role_attaches_inline_policy(
    driver: IRSADriver, iam_client,
) -> None:
    driver.create_identity_role(
        name="acme-api",
        permissions=[{
            "Effect": "Allow",
            "Action": "s3:*",
            "Resource": "*",
        }],
    )
    policies = iam_client.list_role_policies(RoleName="acme-api")
    assert "astrolift-workload-policy" in policies["PolicyNames"]


def test_create_role_no_inline_policy_when_empty(
    driver: IRSADriver, iam_client,
) -> None:
    driver.create_identity_role(name="acme-api", permissions=[])
    policies = iam_client.list_role_policies(RoleName="acme-api")
    assert policies["PolicyNames"] == []


# ---- bind_service_account ----------------------------------------


def test_bind_returns_irsa_annotation(driver: IRSADriver) -> None:
    driver.create_identity_role(name="acme-api", permissions=[])
    result = driver.bind_service_account(
        cluster="aws-prod",
        namespace="acme",
        sa_name="api",
        identity_role="acme-api",
    )
    assert "eks.amazonaws.com/role-arn" in result
    assert "acme-api" in result["eks.amazonaws.com/role-arn"]


def test_bind_adds_sub_claim_to_trust(
    driver: IRSADriver, iam_client,
) -> None:
    """Binding must update the trust policy to include the
    subject claim for this (namespace, sa)."""
    driver.create_identity_role(name="acme-api", permissions=[])
    driver.bind_service_account(
        cluster="x", namespace="acme", sa_name="api",
        identity_role="acme-api",
    )
    response = iam_client.get_role(RoleName="acme-api")
    trust = response["Role"]["AssumeRolePolicyDocument"]
    if isinstance(trust, str):
        from urllib.parse import unquote
        trust = json.loads(unquote(trust))

    sub_key = (
        "oidc.eks.us-east-1.amazonaws.com/id/ABC123:sub"
    )
    sub_value = trust["Statement"][0]["Condition"]["StringEquals"][sub_key]
    assert sub_value == "system:serviceaccount:acme:api"


def test_bind_role_not_found(driver: IRSADriver) -> None:
    with pytest.raises(NotFoundError):
        driver.bind_service_account(
            cluster="x", namespace="ns", sa_name="api",
            identity_role="missing",
        )


def test_bind_multiple_sas_keeps_all(
    driver: IRSADriver, iam_client,
) -> None:
    """Two SAs sharing one role: trust policy gets both subjects."""
    driver.create_identity_role(name="shared", permissions=[])
    driver.bind_service_account(
        cluster="x", namespace="acme", sa_name="api",
        identity_role="shared",
    )
    driver.bind_service_account(
        cluster="x", namespace="acme", sa_name="worker",
        identity_role="shared",
    )

    response = iam_client.get_role(RoleName="shared")
    trust = response["Role"]["AssumeRolePolicyDocument"]
    if isinstance(trust, str):
        from urllib.parse import unquote
        trust = json.loads(unquote(trust))

    sub_key = (
        "oidc.eks.us-east-1.amazonaws.com/id/ABC123:sub"
    )
    sub_value = trust["Statement"][0]["Condition"]["StringEquals"][sub_key]
    if isinstance(sub_value, list):
        assert "system:serviceaccount:acme:api" in sub_value
        assert "system:serviceaccount:acme:worker" in sub_value
    else:
        # Single subject — only worker present (last one wins is wrong)
        pytest.fail(
            f"expected list of subjects after second bind, got {sub_value!r}",
        )


# ---- delete_identity_role ----------------------------------------


def test_delete_role_with_inline_policies(
    driver: IRSADriver, iam_client,
) -> None:
    """Inline policies must be removed before role delete."""
    driver.create_identity_role(
        name="acme-api",
        permissions=[{"Effect": "Allow", "Action": "*", "Resource": "*"}],
    )
    driver.delete_identity_role("acme-api")
    with pytest.raises(Exception):
        iam_client.get_role(RoleName="acme-api")


def test_delete_role_with_managed_policy(
    driver: IRSADriver, iam_client,
) -> None:
    """Managed policies must be detached before role delete."""
    driver.create_identity_role(name="acme-api", permissions=[])
    iam_client.create_policy(
        PolicyName="extra",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": "*",
            }],
        }),
    )
    driver.attach_policy(
        role="acme-api",
        policy="arn:aws:iam::123456789012:policy/extra",
    )
    driver.delete_identity_role("acme-api")
    with pytest.raises(Exception):
        iam_client.get_role(RoleName="acme-api")


def test_delete_role_not_found(driver: IRSADriver) -> None:
    with pytest.raises(NotFoundError):
        driver.delete_identity_role("never-existed")


# ---- attach_policy -----------------------------------------------


def test_attach_policy_to_missing_role(driver: IRSADriver) -> None:
    with pytest.raises(NotFoundError):
        driver.attach_policy(
            role="missing",
            policy="arn:aws:iam::aws:policy/AdministratorAccess",
        )
