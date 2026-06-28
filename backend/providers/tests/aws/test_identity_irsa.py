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
        permissions=[
            {
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": "*",
            }
        ],
    )
    assert arn.startswith("arn:aws:iam::123456789012:role/")
    assert "acme-api" in arn


def test_create_role_idempotent(driver: IRSADriver) -> None:
    """Re-create returns existing ARN, doesn't error."""
    a = driver.create_identity_role(name="acme-api", permissions=[])
    b = driver.create_identity_role(name="acme-api", permissions=[])
    assert a == b


def test_create_role_reconciles_stale_trust_and_policy(iam_client) -> None:
    """Re-create self-heals a role whose trust was built with an empty/wrong
    issuer and whose grants have since changed (#1011).

    First create uses an empty issuer (the bug: yields a broken
    ``oidc-provider/`` principal). Second create — once the issuer is
    discovered — must rewrite the trust principal to the real issuer and
    replace the inline policy with the new grants, not silently no-op.
    """
    broken = IRSADriver(
        config=IRSAConfig(
            region="us-east-1",
            account_id="123456789012",
            cluster_oidc_issuer="",
        ),
        iam_client=iam_client,
    )
    broken.create_identity_role(
        name="acme-api",
        permissions=[{"Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::old/*"}],
    )

    healed = IRSADriver(
        config=IRSAConfig(
            region="us-east-1",
            account_id="123456789012",
            cluster_oidc_issuer="oidc.eks.us-east-1.amazonaws.com/id/REAL",
        ),
        iam_client=iam_client,
    )
    healed.create_identity_role(
        name="acme-api",
        permissions=[{"Effect": "Allow", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::new/*"}],
    )

    role = iam_client.get_role(RoleName="acme-api")["Role"]
    trust = role["AssumeRolePolicyDocument"]
    if isinstance(trust, str):
        from urllib.parse import unquote

        trust = json.loads(unquote(trust))
    principal = trust["Statement"][0]["Principal"]["Federated"]
    assert principal.endswith("oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/REAL")

    policy = iam_client.get_role_policy(
        RoleName="acme-api",
        PolicyName="astrolift-workload-policy",
    )["PolicyDocument"]
    if isinstance(policy, str):
        from urllib.parse import unquote

        policy = json.loads(unquote(policy))
    assert policy["Statement"] == [
        {"Effect": "Allow", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::new/*"},
    ]


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
    aud_key = "oidc.eks.us-east-1.amazonaws.com/id/ABC123:aud"
    assert statement["Condition"]["StringEquals"][aud_key] == "sts.amazonaws.com"


def test_create_role_attaches_inline_policy(
    driver: IRSADriver,
    iam_client,
) -> None:
    driver.create_identity_role(
        name="acme-api",
        permissions=[
            {
                "Effect": "Allow",
                "Action": "s3:*",
                "Resource": "*",
            }
        ],
    )
    policies = iam_client.list_role_policies(RoleName="acme-api")
    assert "astrolift-workload-policy" in policies["PolicyNames"]


def test_create_role_no_inline_policy_when_empty(
    driver: IRSADriver,
    iam_client,
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
    driver: IRSADriver,
    iam_client,
) -> None:
    """Binding must update the trust policy to include the
    subject claim for this (namespace, sa)."""
    driver.create_identity_role(name="acme-api", permissions=[])
    driver.bind_service_account(
        cluster="x",
        namespace="acme",
        sa_name="api",
        identity_role="acme-api",
    )
    response = iam_client.get_role(RoleName="acme-api")
    trust = response["Role"]["AssumeRolePolicyDocument"]
    if isinstance(trust, str):
        from urllib.parse import unquote

        trust = json.loads(unquote(trust))

    sub_key = "oidc.eks.us-east-1.amazonaws.com/id/ABC123:sub"
    sub_value = trust["Statement"][0]["Condition"]["StringEquals"][sub_key]
    assert sub_value == "system:serviceaccount:acme:api"


def test_bind_role_not_found(driver: IRSADriver) -> None:
    with pytest.raises(NotFoundError):
        driver.bind_service_account(
            cluster="x",
            namespace="ns",
            sa_name="api",
            identity_role="missing",
        )


def test_bind_multiple_sas_keeps_all(
    driver: IRSADriver,
    iam_client,
) -> None:
    """Two SAs sharing one role: trust policy gets both subjects."""
    driver.create_identity_role(name="shared", permissions=[])
    driver.bind_service_account(
        cluster="x",
        namespace="acme",
        sa_name="api",
        identity_role="shared",
    )
    driver.bind_service_account(
        cluster="x",
        namespace="acme",
        sa_name="worker",
        identity_role="shared",
    )

    response = iam_client.get_role(RoleName="shared")
    trust = response["Role"]["AssumeRolePolicyDocument"]
    if isinstance(trust, str):
        from urllib.parse import unquote

        trust = json.loads(unquote(trust))

    sub_key = "oidc.eks.us-east-1.amazonaws.com/id/ABC123:sub"
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
    driver: IRSADriver,
    iam_client,
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
    driver: IRSADriver,
    iam_client,
) -> None:
    """Managed policies must be detached before role delete."""
    driver.create_identity_role(name="acme-api", permissions=[])
    iam_client.create_policy(
        PolicyName="extra",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "s3:GetObject",
                        "Resource": "*",
                    }
                ],
            }
        ),
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


# ---- provision_ebs_csi_role (#1024) ------------------------------


class _RecordingIam:
    """Moto-free recording IAM double covering the EBS-CSI provision path.

    create_identity_role + _ensure_trust_includes + attach_policy is all the
    surface provision_ebs_csi_role touches; this records enough to assert the
    trust subject and the attached managed policy without moto installed.
    """

    # Names mirror boto3's IAM client.exceptions namespace exactly (the driver
    # catches self._iam.exceptions.{EntityAlreadyExists,NoSuchEntity}Exception),
    # so the casing can't follow ruff's class/exception conventions here.
    class exceptions:  # noqa: N801
        class EntityAlreadyExistsException(Exception):  # noqa: N818
            pass

        class NoSuchEntityException(Exception):  # noqa: N818
            pass

    def __init__(self) -> None:
        self.roles: dict[str, dict] = {}  # name -> {"trust": dict, "arn": str}
        self.attached: dict[str, list[str]] = {}

    # boto3's IAM API uses PascalCase kwargs; **kwargs keeps this fake faithful
    # to those call shapes without tripping ruff's snake_case arg lint (N803).
    def create_role(self, **kwargs) -> dict:
        name = kwargs["RoleName"]
        if name in self.roles:
            raise self.exceptions.EntityAlreadyExistsException(name)
        arn = f"arn:aws:iam::123456789012:role/{name}"
        self.roles[name] = {"trust": json.loads(kwargs["AssumeRolePolicyDocument"]), "arn": arn}
        return {"Role": {"Arn": arn}}

    def get_role(self, **kwargs) -> dict:
        name = kwargs["RoleName"]
        if name not in self.roles:
            raise self.exceptions.NoSuchEntityException(name)
        return {
            "Role": {
                "AssumeRolePolicyDocument": self.roles[name]["trust"],
                "Arn": self.roles[name]["arn"],
            }
        }

    def update_assume_role_policy(self, **kwargs) -> None:
        self.roles[kwargs["RoleName"]]["trust"] = json.loads(kwargs["PolicyDocument"])

    def put_role_policy(self, **kwargs) -> None:
        pass

    def attach_role_policy(self, **kwargs) -> None:
        name = kwargs["RoleName"]
        if name not in self.roles:
            raise self.exceptions.NoSuchEntityException(name)
        self.attached.setdefault(name, []).append(kwargs["PolicyArn"])


_EBS_CSI_ROLE = "astrolift-eks-aws-ebs-csi-driver"
_ISSUER = "oidc.eks.us-west-2.amazonaws.com/id/ABC"


def _ebs_driver(iam: _RecordingIam) -> IRSADriver:
    return IRSADriver(
        config=IRSAConfig(
            region="us-west-2",
            account_id="123456789012",
            cluster_oidc_issuer=_ISSUER,
        ),
        iam_client=iam,
    )


def test_provision_ebs_csi_role_trust_and_managed_policy() -> None:
    """Self-provisions a role whose OIDC trust is scoped to
    astrolift-system:ebs-csi-controller-sa and attaches AmazonEBSCSIDriverPolicy."""
    iam = _RecordingIam()
    arn = _ebs_driver(iam).provision_ebs_csi_role(_EBS_CSI_ROLE)

    assert arn == f"arn:aws:iam::123456789012:role/{_EBS_CSI_ROLE}"

    cond = iam.roles[_EBS_CSI_ROLE]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:ebs-csi-controller-sa"
    assert cond[f"{_ISSUER}:aud"] == "sts.amazonaws.com"

    assert "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy" in iam.attached[_EBS_CSI_ROLE]


def test_provision_ebs_csi_role_idempotent() -> None:
    """Re-running reconciles to the same single trust subject (not a
    duplicated list) and returns the same ARN."""
    iam = _RecordingIam()
    driver = _ebs_driver(iam)
    a = driver.provision_ebs_csi_role(_EBS_CSI_ROLE)
    b = driver.provision_ebs_csi_role(_EBS_CSI_ROLE)

    assert a == b
    cond = iam.roles[_EBS_CSI_ROLE]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:ebs-csi-controller-sa"
