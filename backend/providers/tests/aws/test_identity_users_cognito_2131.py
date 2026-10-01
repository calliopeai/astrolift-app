"""Cognito as the cluster's edge identity provider (#2131)."""

from __future__ import annotations

import pytest

from _sdk.identity_users import IdentityUsersError
from aws.identity_users_cognito import (
    CognitoIdentityUsersDriver,
    CognitoUsersConfig,
    cognito_pool_from_issuer,
)

POOL = "us-west-2_xU96Y7DAg"


class FakeIdp:
    def __init__(self, fail_with: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_with = fail_with

    def __getattr__(self, method):
        def call(**kwargs):
            self.calls.append((method, kwargs))
            if self.fail_with is not None:
                raise self.fail_with
            if method == "admin_create_user":
                return {
                    "User": {
                        "Username": "uuid-1",
                        "Enabled": True,
                        "UserStatus": "FORCE_CHANGE_PASSWORD",
                        "Attributes": [
                            {"Name": "sub", "Value": "subject-1"},
                            {"Name": "email", "Value": kwargs["Username"]},
                        ],
                    }
                }
            if method == "admin_get_user":
                return {"Username": "uuid-1", "UserAttributes": [{"Name": "sub", "Value": "subject-1"}]}
            if method == "list_users":
                return {
                    "Users": [
                        {
                            "Username": "uuid-1",
                            "Enabled": True,
                            "UserStatus": "CONFIRMED",
                            "Attributes": [{"Name": "email", "Value": "a@example.com"}],
                        }
                    ]
                }
            if method == "admin_list_groups_for_user":
                return {"Groups": [{"GroupName": "veruus"}]}
            if method == "list_groups":
                return {"Groups": [{"GroupName": "veruus"}, {"GroupName": "staff"}]}
            return {}

        return call


def _driver(idp):
    return CognitoIdentityUsersDriver(CognitoUsersConfig(region="us-west-2", pool_id=POOL), client=idp)


@pytest.mark.parametrize(
    "url",
    [
        f"https://cognito-idp.us-west-2.amazonaws.com/{POOL}",
        f"https://cognito-idp.us-west-2.amazonaws.com/{POOL}/.well-known/openid-configuration",
    ],
)
def test_the_pool_comes_from_the_issuer(url):
    assert cognito_pool_from_issuer(url) == ("us-west-2", POOL)


@pytest.mark.parametrize("url", ["https://dex.example.net/dex", "https://login.microsoftonline.com/x/v2.0", ""])
def test_another_provider_is_not_cognito(url):
    assert cognito_pool_from_issuer(url) is None


def test_every_call_is_on_the_installs_own_pool():
    idp = FakeIdp()
    _driver(idp).list_users()

    assert {kw["UserPoolId"] for _m, kw in idp.calls} == {POOL}


def test_a_user_with_a_password_gets_no_invitation_and_a_permanent_one_is_set():
    idp = FakeIdp()

    user = _driver(idp).create_user(
        email="veruus-admin@example.com", password="Pw-123456!", permanent=True, groups=("veruus",)
    )

    create = dict(idp.calls[0][1])
    assert create["TemporaryPassword"] == "Pw-123456!"
    assert create["MessageAction"] == "SUPPRESS"
    assert (
        "admin_set_user_password",
        {"UserPoolId": POOL, "Username": "uuid-1", "Password": "Pw-123456!", "Permanent": True},
    ) in idp.calls
    assert ("admin_add_user_to_group", {"UserPoolId": POOL, "Username": "uuid-1", "GroupName": "veruus"}) in idp.calls
    assert user.email == "veruus-admin@example.com"
    assert "Pw-123456!" not in repr(user)


def test_a_user_without_a_password_is_invited_by_cognito():
    idp = FakeIdp()

    _driver(idp).create_user(email="new@example.com", password=None, permanent=False)

    create = idp.calls[0][1]
    assert "TemporaryPassword" not in create
    assert "MessageAction" not in create
    assert [m for m, _ in idp.calls] == ["admin_create_user"]


def test_a_provider_error_never_echoes_the_password():
    class ClientError(Exception):
        def __init__(self):
            super().__init__("invalid password")
            self.response = {
                "Error": {"Code": "InvalidPasswordException", "Message": "Password Pw-123456! does not conform"}
            }

    with pytest.raises(IdentityUsersError) as exc:
        _driver(FakeIdp(ClientError())).set_password(username="u", password="Pw-123456!", permanent=True)

    assert "Pw-123456!" not in str(exc.value)
    assert "InvalidPasswordException" in str(exc.value)


def test_listing_carries_each_users_groups():
    users = _driver(FakeIdp()).list_users(search='a"')

    assert users[0].groups == ("veruus",)
    assert users[0].email == "a@example.com"


@pytest.mark.parametrize("subject", ["replacement-subject", ""])
def test_immutable_user_verification_uses_actual_subject_not_email(subject):
    import boto3
    from botocore.stub import Stubber

    client = boto3.client(
        "cognito-idp", region_name="us-west-2", aws_access_key_id="TEST_ONLY", aws_secret_access_key="TEST_ONLY"
    )
    with Stubber(client) as stub:
        stub.add_response(
            "admin_get_user",
            {
                "Username": "username-literal",
                "UserAttributes": [{"Name": "sub", "Value": subject}],
                "Enabled": True,
                "UserStatus": "CONFIRMED",
            },
            {"UserPoolId": POOL, "Username": "username-literal"},
        )
        with pytest.raises(IdentityUsersError):
            _driver(client).verify_user(username="username-literal", expected_user_id="reviewed-subject")
        stub.assert_no_pending_responses()


def test_sdk_subject_verification_and_write_have_no_conditional_compare_and_swap():
    import boto3
    from botocore.stub import Stubber

    client = boto3.client(
        "cognito-idp", region_name="us-west-2", aws_access_key_id="TEST_ONLY", aws_secret_access_key="TEST_ONLY"
    )
    with Stubber(client) as stub:
        stub.add_response(
            "admin_get_user",
            {
                "Username": "username-literal",
                "UserAttributes": [{"Name": "sub", "Value": "reviewed-subject"}],
                "Enabled": True,
                "UserStatus": "CONFIRMED",
            },
            {"UserPoolId": POOL, "Username": "username-literal"},
        )
        stub.add_response("admin_disable_user", {}, {"UserPoolId": POOL, "Username": "username-literal"})
        driver = _driver(client)
        driver.verify_user(username="username-literal", expected_user_id="reviewed-subject")
        driver.set_enabled(username="username-literal", enabled=False)
        stub.assert_no_pending_responses()


def test_user_recreated_after_creation_cannot_receive_permanent_password_or_groups():
    class Replaced(FakeIdp):
        def __getattr__(self, method):
            original = super().__getattr__(method)

            def call(**kwargs):
                result = original(**kwargs)
                if method == "admin_get_user":
                    result["UserAttributes"] = [{"Name": "sub", "Value": "replacement"}]
                return result

            return call

    idp = Replaced()
    with pytest.raises(IdentityUsersError):
        _driver(idp).create_user(email="new@example.test", password="WRITE_ONLY", permanent=True, groups=("group",))
    assert [name for name, _ in idp.calls] == ["admin_create_user", "admin_get_user"]


def test_guard_runs_before_each_actual_create_followup_and_membership_write():
    idp = FakeIdp()
    calls = []
    driver = _driver(idp)
    with driver.guard_writes(lambda: calls.append(len(idp.calls))):
        driver.create_user(email="new@example.test", password="WRITE_ONLY", permanent=True, groups=("a", "b"))
    assert calls == [0, 2, 4, 6]
    assert [name for name, _ in idp.calls] == [
        "admin_create_user",
        "admin_get_user",
        "admin_set_user_password",
        "admin_get_user",
        "admin_add_user_to_group",
        "admin_get_user",
        "admin_add_user_to_group",
    ]
    driver.set_enabled(username="uuid-1", enabled=False)
    assert len(calls) == 4
