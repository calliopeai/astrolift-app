"""Managed-model (Bedrock auto-wire) tests for EKSClusterDriver.

Two surfaces:

* ``agent_model_env`` — pure compute; returns the Bedrock env a Claude
  Code runner reads (CLAUDE_CODE_USE_BEDROCK + region + Opus/Haiku ids),
  with per-cluster, worker-environment, and region fallbacks.
* ``ensure_agent_model_identity`` — mints (idempotently) the IRSA role the
  managed-model pod's ServiceAccount assumes to call bedrock:InvokeModel.
  Uses moto IAM for real role storage + a fake EKS/STS pair so the OIDC
  issuer + account id are deterministic (mirrors ``test_registry_ecr``'s
  IAM approach; moto EKS OIDC fields are version-fragile).
"""

from __future__ import annotations

import json
from urllib.parse import unquote

import pytest

from _sdk.cluster import ManagedModelNotSupportedError
from aws._naming import iam_role_name
from aws.cluster_eks import EKSClusterDriver, EKSConfig

_ISSUER_URL = "https://oidc.eks.us-west-2.amazonaws.com/id/0B2122D66B8C3E3BE35BA8187004A2CB"
_ISSUER = "oidc.eks.us-west-2.amazonaws.com/id/0B2122D66B8C3E3BE35BA8187004A2CB"

# moto's default account. In real AWS, STS get_caller_identity + the IAM
# client the role is created in share ONE account, so the fake STS reports
# moto's account: the minted role ARN + the OIDC-provider principal in the
# trust policy then both resolve to it (as they would in production).
_ACCOUNT = "123456789012"


class _FakeEks:
    """EKS stand-in serving DescribeCluster's OIDC issuer. ``issuer=None``
    models a cluster whose identity block is absent (no OIDC provider)."""

    def __init__(self, issuer: str | None = _ISSUER_URL) -> None:
        self._issuer = issuer

    def describe_cluster(self, name: str) -> dict:
        if not self._issuer:
            return {"cluster": {}}
        return {"cluster": {"identity": {"oidc": {"issuer": self._issuer}}}}


class _FakeSts:
    def get_caller_identity(self) -> dict:
        return {"Account": _ACCOUNT}


def _driver(iam_client=None, *, issuer: str | None = _ISSUER_URL, region="us-west-2", cluster_name="astrolift-eks"):
    return EKSClusterDriver(
        config=EKSConfig(region=region, cluster_name=cluster_name),
        eks_client=_FakeEks(issuer),
        sts_client=_FakeSts(),
        ec2_client=object(),
        iam_client=iam_client,
        k8s_client_factory=lambda **kw: object(),
    )


def _as_doc(doc):
    """Normalize an IAM policy document (moto may return dict or a
    url-encoded JSON string, matching real AWS)."""
    if isinstance(doc, str):
        return json.loads(unquote(doc))
    return doc


# ---- agent_model_env (pure) ---------------------------------------


def test_agent_model_env_defaults():
    env = _driver().agent_model_env(region="us-west-2", provider_config={})
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["AWS_REGION"] == "us-west-2"
    assert env["ANTHROPIC_MODEL"] == "us.anthropic.claude-opus-5"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_agent_model_env_region_falls_back_to_config():
    env = _driver(region="eu-west-1").agent_model_env(region="", provider_config={})
    assert env["AWS_REGION"] == "eu-west-1"


def test_agent_model_env_worker_model_ids_overridable(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "us.anthropic.claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_SMALL_FAST_MODEL", "us.anthropic.claude-haiku-4-5")

    env = _driver().agent_model_env(region="us-west-2", provider_config={})

    assert env["ANTHROPIC_MODEL"] == "us.anthropic.claude-sonnet-5"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "us.anthropic.claude-haiku-4-5"


def test_agent_model_env_per_cluster_model_ids_override_worker(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "us.anthropic.claude-opus-5")
    monkeypatch.setenv("ANTHROPIC_SMALL_FAST_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

    env = _driver().agent_model_env(
        region="eu-west-1",
        provider_config={
            "bedrock_model_id": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
            "bedrock_small_fast_model_id": "eu.anthropic.claude-3-5-haiku-20241022-v1:0",
        },
    )
    assert env["ANTHROPIC_MODEL"] == "eu.anthropic.claude-sonnet-4-20250514-v1:0"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "eu.anthropic.claude-3-5-haiku-20241022-v1:0"


def test_agent_model_env_ignores_blank_overrides(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "  ")
    monkeypatch.setenv("ANTHROPIC_SMALL_FAST_MODEL", "")

    env = _driver().agent_model_env(
        region="us-west-2",
        provider_config={
            "bedrock_model_id": " ",
            "bedrock_small_fast_model_id": None,
        },
    )

    assert env["ANTHROPIC_MODEL"] == "us.anthropic.claude-opus-5"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


# ---- ensure_agent_model_identity (moto IAM) -----------------------


def test_ensure_identity_mints_role_with_oidc_trust(iam_client):
    driver = _driver(iam_client)
    arn = driver.ensure_agent_model_identity(
        namespace="astrolift-agents-acme",
        service_account="astrolift-agent-model",
        provider_config={},
    )
    assert arn.startswith(f"arn:aws:iam::{_ACCOUNT}:role/astrolift-agent-model")

    role_name = arn.rsplit("/", 1)[-1]
    trust = _as_doc(iam_client.get_role(RoleName=role_name)["Role"]["AssumeRolePolicyDocument"])
    stmt = trust["Statement"][0]
    assert stmt["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert stmt["Principal"]["Federated"] == f"arn:aws:iam::{_ACCOUNT}:oidc-provider/{_ISSUER}"
    cond = stmt["Condition"]["StringEquals"]
    assert cond[f"{_ISSUER}:aud"] == "sts.amazonaws.com"
    assert cond[f"{_ISSUER}:sub"] == "system:serviceaccount:astrolift-agents-acme:astrolift-agent-model"


def test_ensure_identity_grants_bedrock_invoke(iam_client):
    driver = _driver(iam_client)
    arn = driver.ensure_agent_model_identity(
        namespace="ns",
        service_account="astrolift-agent-model",
        provider_config={},
    )
    role_name = arn.rsplit("/", 1)[-1]
    policy = _as_doc(iam_client.get_role_policy(RoleName=role_name, PolicyName="bedrock-invoke")["PolicyDocument"])
    stmt = policy["Statement"][0]
    assert "bedrock:InvokeModel" in stmt["Action"]
    assert "bedrock:InvokeModelWithResponseStream" in stmt["Action"]
    assert stmt["Resource"] == "*"


def test_ensure_identity_idempotent(iam_client):
    driver = _driver(iam_client)
    a = driver.ensure_agent_model_identity(namespace="ns", service_account="astrolift-agent-model", provider_config={})
    # Second call takes the EntityAlreadyExists → update-trust → re-read path.
    b = driver.ensure_agent_model_identity(namespace="ns", service_account="astrolift-agent-model", provider_config={})
    assert a == b
    # Exactly one role (re-run reconciled in place, didn't duplicate).
    role_name = a.rsplit("/", 1)[-1]
    assert iam_client.get_role(RoleName=role_name)["Role"]["RoleName"] == role_name


def test_ensure_identity_account_id_from_provider_config_wins(iam_client):
    # An explicit provider_config account id is used for the OIDC-provider
    # principal in the trust policy (STS is never consulted). The minted role
    # ARN itself is assigned by IAM in the client's own account (moto's), as
    # in real AWS — so the override is asserted where it actually applies.
    driver = _driver(iam_client)
    arn = driver.ensure_agent_model_identity(
        namespace="ns",
        service_account="astrolift-agent-model",
        provider_config={"account_id": "111122223333"},
    )
    role_name = arn.rsplit("/", 1)[-1]
    trust = _as_doc(iam_client.get_role(RoleName=role_name)["Role"]["AssumeRolePolicyDocument"])
    principal = trust["Statement"][0]["Principal"]["Federated"]
    assert principal == f"arn:aws:iam::111122223333:oidc-provider/{_ISSUER}"


def test_ensure_identity_narrows_resource_when_model_arns_given(iam_client):
    arns = ["arn:aws:bedrock:us-west-2::inference-profile/us.anthropic.claude-sonnet-4-20250514-v1:0"]
    driver = _driver(iam_client)
    arn = driver.ensure_agent_model_identity(
        namespace="ns",
        service_account="astrolift-agent-model",
        provider_config={"bedrock_model_arns": arns},
    )
    role_name = arn.rsplit("/", 1)[-1]
    policy = _as_doc(iam_client.get_role_policy(RoleName=role_name, PolicyName="bedrock-invoke")["PolicyDocument"])
    assert policy["Statement"][0]["Resource"] == arns


def test_ensure_identity_operator_override_skips_minting(iam_client):
    override = "arn:aws:iam::999999999999:role/operator-provisioned-model"
    driver = _driver(iam_client)
    arn = driver.ensure_agent_model_identity(
        namespace="ns",
        service_account="astrolift-agent-model",
        provider_config={"agent_model_role_arn": override},
    )
    assert arn == override
    # Nothing was minted — the derived role name doesn't exist.
    derived = iam_role_name("astrolift", "agent-model", "astrolift-eks", "ns")
    with pytest.raises(iam_client.exceptions.NoSuchEntityException):
        iam_client.get_role(RoleName=derived)


def test_ensure_identity_raises_when_oidc_issuer_absent(iam_client):
    driver = _driver(iam_client, issuer=None)
    with pytest.raises(ManagedModelNotSupportedError):
        driver.ensure_agent_model_identity(
            namespace="ns",
            service_account="astrolift-agent-model",
            provider_config={},
        )
