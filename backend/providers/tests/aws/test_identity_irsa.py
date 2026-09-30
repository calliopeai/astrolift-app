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
    with pytest.raises(Exception):  # noqa: B017 — moto raises a bare ClientError here
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
    with pytest.raises(Exception):  # noqa: B017 — moto raises a bare ClientError here
        iam_client.get_role(RoleName="acme-api")


def test_delete_role_not_found_is_idempotent(driver: IRSADriver) -> None:
    # #998: deleting an absent role is the desired end state — succeed
    # so teardown re-runs complete instead of halting.
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
        class EntityAlreadyExistsException(Exception):
            pass

        class NoSuchEntityException(Exception):
            pass

    def __init__(self) -> None:
        self.roles: dict[str, dict] = {}  # name -> {"trust": dict, "arn": str}
        self.inline_policies: dict[tuple[str, str], dict] = {}
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
        self.inline_policies[(kwargs["RoleName"], kwargs["PolicyName"])] = json.loads(kwargs["PolicyDocument"])

    def delete_role_policy(self, **kwargs) -> None:
        key = (kwargs["RoleName"], kwargs["PolicyName"])
        if key not in self.inline_policies:
            raise self.exceptions.NoSuchEntityException(kwargs["PolicyName"])
        del self.inline_policies[key]

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


# ---- provision_alb_controller_role + provision_external_dns_role (#1044) ----
#
# Same recording-IAM approach, but these controllers attach an *inline* policy
# (no AWS-managed ARN fits) so the double records put_role_policy to let the
# assertions reach the policy document.


class _InlineRecordingIam(_RecordingIam):
    """Adds inline-policy recording to the EBS-CSI double so the ALB /
    external-dns mint paths (which write an inline policy via
    create_identity_role) can be asserted without moto."""

    def __init__(self) -> None:
        super().__init__()
        self.inline: dict[str, dict] = {}  # role -> {policy_name: doc}

    def put_role_policy(self, **kwargs) -> None:
        self.inline.setdefault(kwargs["RoleName"], {})[kwargs["PolicyName"]] = json.loads(kwargs["PolicyDocument"])


_ALB_ROLE = "astrolift-eks-aws-load-balancer-controller"
_EXTERNAL_DNS_ROLE = "astrolift-eks-external-dns"


def _inline_driver(iam: _InlineRecordingIam) -> IRSADriver:
    return IRSADriver(
        config=IRSAConfig(
            region="us-west-2",
            account_id="123456789012",
            cluster_oidc_issuer=_ISSUER,
        ),
        iam_client=iam,
    )


def _flatten_actions(statements: list[dict]) -> set[str]:
    actions: set[str] = set()
    for stmt in statements:
        act = stmt.get("Action", [])
        actions.update([act] if isinstance(act, str) else act)
    return actions


def test_provision_alb_controller_role_trust_and_inline_policy() -> None:
    """Scopes the OIDC trust to astrolift-system:aws-load-balancer-controller
    and writes the canonical LB-controller permission set inline."""
    iam = _InlineRecordingIam()
    arn = _inline_driver(iam).provision_alb_controller_role(_ALB_ROLE)

    assert arn == f"arn:aws:iam::123456789012:role/{_ALB_ROLE}"

    cond = iam.roles[_ALB_ROLE]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:aws-load-balancer-controller"
    assert cond[f"{_ISSUER}:aud"] == "sts.amazonaws.com"

    doc = iam.inline[_ALB_ROLE]["astrolift-workload-policy"]
    actions = _flatten_actions(doc["Statement"])
    # Spot-check the load-bearing permissions across the policy's families.
    assert "elasticloadbalancing:CreateLoadBalancer" in actions
    assert "ec2:CreateSecurityGroup" in actions
    assert "wafv2:AssociateWebACL" in actions
    assert "acm:DescribeCertificate" in actions
    assert "cognito-idp:DescribeUserPoolClient" in actions


def test_provision_alb_controller_role_idempotent() -> None:
    """Re-running converges to a single trust subject + the same ARN."""
    iam = _InlineRecordingIam()
    driver = _inline_driver(iam)
    a = driver.provision_alb_controller_role(_ALB_ROLE)
    b = driver.provision_alb_controller_role(_ALB_ROLE)

    assert a == b
    cond = iam.roles[_ALB_ROLE]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:aws-load-balancer-controller"


def test_provision_external_dns_role_trust_and_inline_policy() -> None:
    """Scopes the OIDC trust to astrolift-system:external-dns and writes the
    Route53 change/list permissions inline."""
    iam = _InlineRecordingIam()
    arn = _inline_driver(iam).provision_external_dns_role(_EXTERNAL_DNS_ROLE)

    assert arn == f"arn:aws:iam::123456789012:role/{_EXTERNAL_DNS_ROLE}"

    cond = iam.roles[_EXTERNAL_DNS_ROLE]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:external-dns"
    assert cond[f"{_ISSUER}:aud"] == "sts.amazonaws.com"

    doc = iam.inline[_EXTERNAL_DNS_ROLE]["astrolift-workload-policy"]
    actions = _flatten_actions(doc["Statement"])
    assert "route53:ChangeResourceRecordSets" in actions
    assert "route53:ListHostedZones" in actions
    assert "route53:ListResourceRecordSets" in actions
    # ChangeResourceRecordSets is scoped to hosted zones, not "*".
    change_stmt = next(s for s in doc["Statement"] if "route53:ChangeResourceRecordSets" in s["Action"])
    assert change_stmt["Resource"] == ["arn:aws:route53:::hostedzone/*"]


def test_provision_external_dns_role_idempotent() -> None:
    """Re-running converges to a single trust subject + the same ARN."""
    iam = _InlineRecordingIam()
    driver = _inline_driver(iam)
    a = driver.provision_external_dns_role(_EXTERNAL_DNS_ROLE)
    b = driver.provision_external_dns_role(_EXTERNAL_DNS_ROLE)

    assert a == b
    cond = iam.roles[_EXTERNAL_DNS_ROLE]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:external-dns"


# ---- permissions boundary (#1678) --------------------------------


def _boundary_driver(iam_client, boundary: str) -> IRSADriver:
    return IRSADriver(
        config=IRSAConfig(
            region="us-east-1",
            account_id="123456789012",
            cluster_oidc_issuer="oidc.eks.us-east-1.amazonaws.com/id/ABC123",
            permissions_boundary_arn=boundary,
        ),
        iam_client=iam_client,
    )


def test_create_role_attaches_the_configured_permissions_boundary(iam_client) -> None:
    """On an agent-installed (pull mode) cluster the agent's own boundary
    carries ``DenyRoleCreationWithoutThisBoundary``, refusing any CreateRole
    that does not attach that same boundary. Without this the platform can
    create no workload-identity role at all, so every managed-service binding
    and the in-cluster build role are unreachable."""
    boundary = "arn:aws:iam::123456789012:policy/calliope-agent-boundary"
    driver = _boundary_driver(iam_client, boundary)

    driver.create_identity_role(name="astrolift-acme-api", permissions=[])

    role = iam_client.get_role(RoleName="astrolift-acme-api")["Role"]
    assert role.get("PermissionsBoundary", {}).get("PermissionsBoundaryArn") == boundary


def test_create_role_omits_the_boundary_when_there_is_none(iam_client) -> None:
    """Admin-provisioned (push mode) installs have no boundary. IAM rejects an
    empty PermissionsBoundary, so it has to be omitted rather than passed
    through as an empty string."""
    driver = _boundary_driver(iam_client, "")

    driver.create_identity_role(name="astrolift-acme-api", permissions=[])

    role = iam_client.get_role(RoleName="astrolift-acme-api")["Role"]
    assert "PermissionsBoundary" not in role


def test_boundary_defaults_to_absent(iam_client) -> None:
    """The field is opt-in: a config that never mentions it behaves exactly as
    it did before this existed."""
    assert (
        IRSAConfig(
            region="us-east-1",
            account_id="123456789012",
            cluster_oidc_issuer="oidc.eks.us-east-1.amazonaws.com/id/ABC123",
        ).permissions_boundary_arn
        == ""
    )


def test_empty_reconcile_removes_only_the_platform_inline_policy(driver, iam_client):
    driver.create_identity_role(
        name="empty-union",
        permissions=[{"Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::retired/*"}],
    )
    iam_client.put_role_policy(
        RoleName="empty-union",
        PolicyName="external-policy",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::external"}],
            }
        ),
    )
    for _ in range(2):
        driver.create_identity_role(name="empty-union", permissions=[])
        assert iam_client.list_role_policies(RoleName="empty-union")["PolicyNames"] == ["external-policy"]
