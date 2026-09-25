"""IAM role ARNs in a managed-service config must be the owning org's (#1960).

Tenants share the install's AWS account. A role a config names (a Firehose
destination's RoleARN, an EventBridge target's RoleArn, an RDS Proxy role_arn)
is passed to AWS with the platform's credentials, and the app's role is
granted iam:PassRole on it, so a tenant could name the platform's or another
org's role.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_services.secret_ref_config import config_role_arns, org_role_path, unscoped_config_secret_refs


def _owner():
    return SimpleNamespace(organization=SimpleNamespace(guid=uuid.uuid4()), guid=uuid.uuid4())


def _reasons(config, owner):
    return [reason for _path, _ref, reason in unscoped_config_secret_refs(config, owner=owner, cluster=None)]


@pytest.mark.parametrize(
    "config",
    [
        {"destination": {"configuration": {"RoleARN": "{arn}"}}},
        {"targets": [{"request": {"RoleArn": "{arn}"}}]},
        {"role_arn": "{arn}"},
        {"destination_update": {"configuration": {"role_arn": "{arn}"}}},
    ],
)
def test_a_role_outside_the_orgs_path_is_refused_in_any_spelling(config):
    owner = _owner()
    foreign = "arn:aws:iam::123456789012:role/astrolift-platform-admin"
    filled = eval(repr(config).replace("{arn}", foreign))  # noqa: S307 - test fixture templating

    assert config_role_arns(filled)
    assert any("IAM role path" in reason for reason in _reasons(filled, owner))


def test_a_role_under_the_orgs_path_is_allowed():
    owner = _owner()
    own = f"arn:aws:iam::123456789012:role{org_role_path(owner.organization)}firehose-delivery"
    assert _reasons({"destination": {"configuration": {"RoleARN": own}}}, owner) == []


def test_another_orgs_path_is_refused():
    owner, other = _owner(), _owner()
    theirs = f"arn:aws:iam::123456789012:role{org_role_path(other.organization)}firehose-delivery"
    assert _reasons({"role_arn": theirs}, owner)
