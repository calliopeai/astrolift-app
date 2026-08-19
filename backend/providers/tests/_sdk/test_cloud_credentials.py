"""Reading a cluster's declared cloud credential (#1422).

The properties worth pinning are the ones a mistake in would be expensive:
the default stays ambient so adopting the model changes nothing on its own,
a credential block can never carry material, and a malformed declaration
fails rather than degrading to the ambient identity.
"""

from __future__ import annotations

import pytest

from _sdk.cloud_credentials import (
    CloudCredentialError,
    CredentialMode,
    credential_from_config,
    session_name_for,
)

ROLE = "arn:aws:iam::210987654321:role/astrolift-control-plane"


def test_no_credential_block_is_ambient_and_keeps_the_declared_account():
    cred = credential_from_config(
        cloud="aws",
        provider_config={"region": "us-west-2", "account_id": "123456789012"},
    )

    assert cred.is_ambient
    assert cred.mode is CredentialMode.AMBIENT
    # Retained even when ambient: it is what verify_account compares against,
    # and today's clusters declare it without any credential block.
    assert cred.declared_account == "123456789012"


def test_assume_role_is_read_off_the_cluster_row():
    cred = credential_from_config(
        cloud="aws",
        provider_config={
            "region": "us-west-2",
            "account_id": "210987654321",
            "credential": {"mode": "aws_assume_role", "role_arn": ROLE, "external_id": "nonce-7"},
        },
        cluster_slug="prod-usw2",
    )

    assert cred.mode is CredentialMode.AWS_ASSUME_ROLE
    assert cred.role_arn == ROLE
    assert cred.external_id == "nonce-7"
    assert cred.session_name == "astrolift-prod-usw2"


def test_declared_account_defaults_to_the_role_arn_account():
    """A row that names a role but no account still has something to verify
    against — the account is already in the ARN."""
    cred = credential_from_config(
        cloud="aws",
        provider_config={"credential": {"mode": "aws_assume_role", "role_arn": ROLE}},
    )

    assert cred.declared_account == "210987654321"


def test_gcp_and_azure_read_their_own_container_key():
    gcp = credential_from_config(cloud="gcp", provider_config={"project_id": "calliopealpha"})
    azure = credential_from_config(cloud="azure", provider_config={"subscription_id": "sub-abc"})

    assert gcp.declared_account == "calliopealpha"
    assert azure.declared_account == "sub-abc"


@pytest.mark.parametrize(
    "key",
    [
        "aws_secret_access_key",
        "client_secret",
        "service_account_key",
        "password",
        "session_token",
    ],
)
def test_credential_material_is_refused_rather_than_stored(key):
    """provider_config is a plaintext JSON column. The binding rule already
    forbids a literal credential landing in one; this is the same rule applied
    to the cluster row, using the same classifier."""
    with pytest.raises(CloudCredentialError) as exc:
        credential_from_config(
            cloud="aws",
            provider_config={"credential": {"mode": "aws_assume_role", "role_arn": ROLE, key: "hunter2"}},
        )

    assert key in str(exc.value)


def test_pointer_fields_are_not_mistaken_for_material():
    """role_arn and external_id look credential-shaped and are not: one is an
    identifier, the other a confused-deputy nonce AWS documents as shareable.
    A rule that rejected them would reject the only mode that exists."""
    cred = credential_from_config(
        cloud="aws",
        provider_config={"credential": {"mode": "aws_assume_role", "role_arn": ROLE, "external_id": "n"}},
    )

    assert cred.role_arn == ROLE


def test_unknown_mode_fails_instead_of_falling_back_to_ambient():
    with pytest.raises(CloudCredentialError, match="unsupported credential mode"):
        credential_from_config(
            cloud="aws",
            provider_config={"credential": {"mode": "gcp_impersonate", "role_arn": ROLE}},
        )


def test_assume_role_is_rejected_for_a_non_aws_cluster():
    with pytest.raises(CloudCredentialError, match="AWS-only"):
        credential_from_config(
            cloud="gcp",
            provider_config={"credential": {"mode": "aws_assume_role", "role_arn": ROLE}},
        )


@pytest.mark.parametrize(
    "role_arn",
    ["", "astrolift-control-plane", "arn:aws:iam::210987654321:user/leo", "arn:aws:s3:::bucket"],
)
def test_a_role_arn_that_is_not_a_role_arn_is_rejected(role_arn):
    with pytest.raises(CloudCredentialError, match="IAM role ARN"):
        credential_from_config(
            cloud="aws",
            provider_config={"credential": {"mode": "aws_assume_role", "role_arn": role_arn}},
        )


def test_a_non_object_credential_block_is_rejected():
    with pytest.raises(CloudCredentialError, match="must be an object"):
        credential_from_config(cloud="aws", provider_config={"credential": ROLE})


def test_session_name_is_sts_legal_and_identifies_the_cluster():
    """The session name is the tenant's only handle on which of our clusters
    called them, so it must survive slugs STS would reject."""
    assert session_name_for("prod/us-west-2") == "astrolift-prod-us-west-2"
    assert len(session_name_for("x" * 200)) == 64
