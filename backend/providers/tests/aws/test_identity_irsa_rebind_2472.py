"""A role's OIDC trust follows its cluster row to a new cluster (#2472).

The installer's region change re-registers the target region's cluster under
the old region's slug, so the tenant cluster row now names a cluster with
another OIDC issuer while every per-app role Astrolift minted still trusts the
old one. ``rebind_trust_issuer`` moves that trust and nothing else.
"""

from __future__ import annotations

import json
from urllib.parse import unquote

import pytest

from aws.identity_irsa import IRSAConfig, IRSADriver, _rebind_trust_document

ACCOUNT = "123456789012"
OLD = "oidc.eks.us-east-1.amazonaws.com/id/OLD"
NEW = "oidc.eks.us-west-2.amazonaws.com/id/NEW"
OTHER = "oidc.eks.us-east-1.amazonaws.com/id/OTHER"
ROLE = "astrolift-acme-web"
OWNER = {
    "organization": "0192f0a0-0000-7000-8000-000000000001",
    "app": "0192f0a0-0000-7000-8000-000000000002",
    "cluster": "0192f0a0-0000-7000-8000-000000000003",
}
GRANT = [{"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::acme-web/*"}]


def _driver(iam_client, issuer: str) -> IRSADriver:
    return IRSADriver(
        config=IRSAConfig(region="us-west-2", account_id=ACCOUNT, cluster_oidc_issuer=issuer),
        iam_client=iam_client,
    )


def _mint(iam_client, issuer: str, *namespaces: str) -> None:
    """What ``ensure_workload_identity`` leaves behind on a cluster with ``issuer``."""
    driver = _driver(iam_client, issuer)
    driver.create_identity_role(ROLE, GRANT)
    for namespace in namespaces:
        driver.bind_service_account("acme", namespace, ROLE, ROLE)


def _document(value):
    return json.loads(unquote(value)) if isinstance(value, str) else value


def _trust(iam_client, role: str = ROLE) -> dict:
    return _document(iam_client.get_role(RoleName=role)["Role"]["AssumeRolePolicyDocument"])


def _subject(namespace: str) -> str:
    return f"system:serviceaccount:{namespace}:{ROLE}"


def test_a_role_minted_on_the_old_cluster_trusts_the_new_one(iam_client) -> None:
    _mint(iam_client, OLD, "acme-web", "acme-web-staging")

    outcome = _driver(iam_client, NEW).rebind_trust_issuer(ROLE, previous_issuers=[OLD], owner=OWNER)

    assert outcome == "rebound"
    [statement] = _trust(iam_client)["Statement"]
    assert statement["Principal"] == {"Federated": f"arn:aws:iam::{ACCOUNT}:oidc-provider/{NEW}"}
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert statement["Condition"] == {
        "StringEquals": {
            f"{NEW}:aud": "sts.amazonaws.com",
            f"{NEW}:sub": [_subject("acme-web"), _subject("acme-web-staging")],
        }
    }
    policy = _document(
        iam_client.get_role_policy(RoleName=ROLE, PolicyName="astrolift-workload-policy")["PolicyDocument"]
    )
    assert policy["Statement"] == GRANT


def test_a_second_rebind_changes_nothing(iam_client) -> None:
    _mint(iam_client, OLD, "acme-web")
    driver = _driver(iam_client, NEW)
    driver.rebind_trust_issuer(ROLE, previous_issuers=[OLD], owner=OWNER)
    rebound = _trust(iam_client)

    assert driver.rebind_trust_issuer(ROLE, previous_issuers=[OLD], owner=OWNER) == "current"
    assert _trust(iam_client) == rebound


def test_a_role_another_cluster_bound_last_is_left_alone(iam_client) -> None:
    """One role per app: an app with environments on two clusters holds the
    trust of whichever bound it last. Re-pointing this cluster must not take
    the role from the other one."""
    _mint(iam_client, OTHER, "acme-web")
    before = _trust(iam_client)

    outcome = _driver(iam_client, NEW).rebind_trust_issuer(ROLE, previous_issuers=[OLD], owner=OWNER)

    assert outcome == "unrelated"
    assert _trust(iam_client) == before


def test_an_absent_role_is_reported_and_not_created(iam_client) -> None:
    outcome = _driver(iam_client, NEW).rebind_trust_issuer(ROLE, previous_issuers=[OLD], owner=OWNER)

    assert outcome == "absent"
    assert iam_client.list_roles()["Roles"] == []


def test_every_earlier_issuer_of_the_row_is_moved(iam_client) -> None:
    """A row re-pointed twice before its roles were moved: they trust the first issuer."""
    _mint(iam_client, OLD, "acme-web")

    outcome = _driver(iam_client, NEW).rebind_trust_issuer(ROLE, previous_issuers=[OTHER, OLD], owner=OWNER)

    assert outcome == "rebound"
    [statement] = _trust(iam_client)["Statement"]
    assert statement["Principal"]["Federated"].endswith(f"oidc-provider/{NEW}")


def test_a_new_issuer_key_beside_the_old_principal_is_merged(iam_client) -> None:
    """The half-moved trust ``_ensure_trust_includes`` leaves when it runs
    against a role it did not reset: the old principal and keys, plus a
    ``<new>:sub`` key next to them."""
    _mint(iam_client, OLD, "acme-web")
    _driver(iam_client, NEW).bind_service_account("acme", "acme-web-staging", ROLE, ROLE)

    assert _driver(iam_client, NEW).rebind_trust_issuer(ROLE, previous_issuers=[OLD], owner=OWNER) == "rebound"

    [statement] = _trust(iam_client)["Statement"]
    assert statement["Principal"]["Federated"].endswith(f"oidc-provider/{NEW}")
    assert statement["Condition"]["StringEquals"] == {
        f"{NEW}:aud": "sts.amazonaws.com",
        f"{NEW}:sub": [_subject("acme-web-staging"), _subject("acme-web")],
    }


def test_a_role_the_platform_does_not_manage_is_refused(iam_client) -> None:
    trust = _driver(iam_client, OLD)._oidc_trust_policy()
    iam_client.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust))

    with pytest.raises(ValueError, match="not managed by the platform"):
        _driver(iam_client, NEW).rebind_trust_issuer(ROLE, previous_issuers=[OLD], owner=OWNER)

    assert _trust(iam_client) == trust


def test_a_role_tagged_for_another_cluster_is_refused(iam_client) -> None:
    trust = _driver(iam_client, OLD)._oidc_trust_policy()
    iam_client.create_role(
        RoleName=ROLE,
        AssumeRolePolicyDocument=json.dumps(trust),
        Tags=[
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/cluster-id", "Value": "0192f0a0-0000-7000-8000-0000000000ff"},
        ],
    )

    with pytest.raises(ValueError, match="belongs to another cluster"):
        _driver(iam_client, NEW).rebind_trust_issuer(ROLE, previous_issuers=[OLD], owner=OWNER)

    assert _trust(iam_client) == trust


def test_a_native_role_of_this_cluster_passes_the_native_trust_check_after(iam_client) -> None:
    """A native model connection role carries its owner GUIDs. Moved, its
    trust is exactly what the native reconcile accepts for the new issuer,
    so that reconcile can run on it again."""
    subjects = [_subject("acme-web")]
    native = _driver(iam_client, OLD)
    native.reconcile_managed_identity(name=ROLE, permissions=GRANT, owner=OWNER, subjects=subjects)
    with pytest.raises(ValueError, match="trust is unavailable"):
        _driver(iam_client, NEW)._managed_role(ROLE, OWNER, subjects)

    assert _driver(iam_client, NEW).rebind_trust_issuer(ROLE, previous_issuers=[OLD], owner=OWNER) == "rebound"

    assert _driver(iam_client, NEW)._managed_role(ROLE, OWNER, subjects) is not None


@pytest.mark.parametrize(("issuer", "previous"), [("", [OLD]), (NEW, [NEW]), (NEW, [""])])
def test_a_driver_without_a_distinct_issuer_pair_refuses(iam_client, issuer, previous) -> None:
    _mint(iam_client, OLD, "acme-web")
    before = _trust(iam_client)

    with pytest.raises(ValueError, match="needs the cluster's issuer"):
        _driver(iam_client, issuer).rebind_trust_issuer(ROLE, previous_issuers=previous, owner=OWNER)

    assert _trust(iam_client) == before


def test_the_document_rewrite_keeps_partition_shape_and_other_statements() -> None:
    document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Pods",
                "Effect": "Allow",
                "Principal": {"Federated": [f"arn:aws-cn:iam::{ACCOUNT}:oidc-provider/{OLD}"]},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {f"{OLD}:aud": "sts.amazonaws.com"},
                    "StringLike": {f"{OLD}:sub": "system:serviceaccount:acme-*:*"},
                },
            },
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            },
        ],
    }

    rebound, moved, current = _rebind_trust_document(document, {OLD}, NEW)

    assert (moved, current) == (True, False)
    assert rebound["Statement"][0] == {
        "Sid": "Pods",
        "Effect": "Allow",
        "Principal": {"Federated": [f"arn:aws-cn:iam::{ACCOUNT}:oidc-provider/{NEW}"]},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
            "StringEquals": {f"{NEW}:aud": "sts.amazonaws.com"},
            "StringLike": {f"{NEW}:sub": "system:serviceaccount:acme-*:*"},
        },
    }
    assert rebound["Statement"][1] == document["Statement"][1]
    assert document["Statement"][0]["Principal"]["Federated"] == [f"arn:aws-cn:iam::{ACCOUNT}:oidc-provider/{OLD}"]
