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
                        "Attributes": [{"Name": "email", "Value": kwargs["Username"]}],
                    }
                }
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
