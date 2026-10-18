"""The operator Cognito client tool (backend/scripts/ops/cognito_client.py, #2055, #1727).

UpdateUserPoolClient resets every field it is not sent, so these pin that a
trim sends the whole config back, refuses what it cannot round-trip, and
reports any drift after the write.
"""

from __future__ import annotations

import datetime
import json

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.stub import Stubber
from scripts.ops import cognito_client as tool

POOL = "us-west-2_TESTPOOL1"
CLIENT = "1e3f2egf7ibju4c9rnldk98cvv"
KEEP = "https://astrolift.example.test/app/auth1/callback"
KEEP_LOGOUT = "https://astrolift.example.test/"
STALE = [f"https://app{i}.example.test/oauth2/idpresponse" for i in range(3)]

# Every writable field Describe can return, so equality proves the round trip.
CONFIG = {
    "UserPoolId": POOL,
    "ClientId": CLIENT,
    "ClientName": "astrolift-conflict",
    "ClientSecret": "s3cret-0123456789abcdefghij",
    "CreationDate": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    "LastModifiedDate": datetime.datetime(2026, 9, 25, tzinfo=datetime.UTC),
    "RefreshTokenValidity": 30,
    "AccessTokenValidity": 60,
    "IdTokenValidity": 60,
    "TokenValidityUnits": {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "days"},
    "ReadAttributes": ["email", "name"],
    "WriteAttributes": ["name"],
    "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH"],
    "SupportedIdentityProviders": ["COGNITO", "Google"],
    "CallbackURLs": [KEEP, *STALE],
    "LogoutURLs": [KEEP_LOGOUT, *(url.replace("oauth2/idpresponse", "") for url in STALE)],
    "DefaultRedirectURI": KEEP,
    "AllowedOAuthFlows": ["code"],
    "AllowedOAuthScopes": ["email", "openid", "profile"],
    "AllowedOAuthFlowsUserPoolClient": True,
    "AnalyticsConfiguration": {
        "ApplicationId": "app",
        "RoleArn": "arn:aws:iam::123456789012:role/pinpoint",
        "ExternalId": "x",
        "UserDataShared": False,
    },
    "PreventUserExistenceErrors": "ENABLED",
    "EnableTokenRevocation": True,
    "EnablePropagateAdditionalUserContextData": False,
    "AuthSessionValidity": 3,
    "RefreshTokenRotation": {"Feature": "DISABLED"},
}
TRIMMED = {"CallbackURLs": [KEEP], "LogoutURLs": [KEEP_LOGOUT]}


@pytest.fixture
def client():
    c = boto3.client("cognito-idp", region_name="us-west-2", aws_access_key_id="x", aws_secret_access_key="x")
    tool.watch_raw_describe(c)
    return c


def describe_params():
    return {"UserPoolId": POOL, "ClientId": CLIENT}


def run_trim(client, apply, out):
    return tool.trim(client, POOL, CLIENT, [KEEP], [KEEP_LOGOUT], apply, out=out.append)


def test_dry_run_prints_the_diff_and_never_updates(client):
    out: list[str] = []
    with Stubber(client) as stub:
        stub.add_response("describe_user_pool_client", {"UserPoolClient": CONFIG}, describe_params())
        assert run_trim(client, False, out) == 0
        stub.assert_no_pending_responses()  # an update call would raise UnStubbedResponseError
    assert "CallbackURLs: 4 -> 1" in out
    assert f"  - {STALE[0]}" in out
    assert f"    {KEEP}" in out
    assert out[-1].startswith("Dry run")


def test_apply_sends_every_writable_field_back_and_checks_the_result(client):
    expected = {k: v for k, v in CONFIG.items() if k not in tool.READ_ONLY} | TRIMMED
    assert set(expected) <= tool.writable_fields(client)
    out: list[str] = []
    with Stubber(client) as stub:
        stub.add_response("describe_user_pool_client", {"UserPoolClient": CONFIG}, describe_params())
        stub.add_response("update_user_pool_client", {"UserPoolClient": {}}, expected)
        stub.add_response("describe_user_pool_client", {"UserPoolClient": CONFIG | TRIMMED}, describe_params())
        assert run_trim(client, True, out) == 0
        stub.assert_no_pending_responses()
    assert out[-1] == "Updated: only CallbackURLs and LogoutURLs changed."


def test_apply_fails_when_another_field_changed(client):
    reset = CONFIG | TRIMMED | {"EnableTokenRevocation": False, "AllowedOAuthScopes": ["openid"]}
    out: list[str] = []
    with Stubber(client) as stub:
        stub.add_response("describe_user_pool_client", {"UserPoolClient": CONFIG}, describe_params())
        stub.add_response("update_user_pool_client", {"UserPoolClient": {}})
        stub.add_response("describe_user_pool_client", {"UserPoolClient": reset}, describe_params())
        assert run_trim(client, True, out) == 1
    assert out[-1].startswith("FAILED")
    assert "AllowedOAuthScopes" in out[-1]
    assert "EnableTokenRevocation" in out[-1]


class _Raw:
    def __init__(self, body: bytes):
        self.body = body

    def stream(self, **_):
        yield self.body


def test_refuses_a_field_this_botocore_cannot_send_back(client):
    """A newer Cognito field is invisible after parsing; the raw body still shows it."""
    body = {"UserPoolClient": {**json.loads(json.dumps(CONFIG, default=str)), "NewCognitoField": 1}}
    sent: list[str] = []

    def fake_send(request, **_):
        target = request.headers["X-Amz-Target"]
        sent.append(target.decode() if isinstance(target, bytes) else target)
        return AWSResponse(request.url, 200, {}, _Raw(json.dumps(body).encode()))

    client.meta.events.register("before-send.*.*", fake_send)
    with pytest.raises(tool.Refusal, match="NewCognitoField"):
        run_trim(client, True, [])
    assert sent == ["AWSCognitoIdentityProviderService.DescribeUserPoolClient"]


def test_refuses_dropping_the_default_redirect_or_adding_a_url():
    with pytest.raises(tool.Refusal, match="DefaultRedirectURI"):
        tool.plan_trim(CONFIG, [STALE[0]], [KEEP_LOGOUT])
    with pytest.raises(tool.Refusal, match="trim only removes"):
        tool.plan_trim(CONFIG, [KEEP, "https://new.example.test/cb"], [KEEP_LOGOUT])


def test_retire_refuses_while_a_callback_is_left(client):
    with Stubber(client) as stub:
        stub.add_response("describe_user_pool_client", {"UserPoolClient": CONFIG}, describe_params())
        with pytest.raises(tool.Refusal, match="callback URL"):
            tool.retire(client, POOL, CLIENT, True, out=[].append)
        stub.assert_no_pending_responses()


def test_retire_deletes_an_emptied_client_only_with_apply(client):
    empty = CONFIG | {"CallbackURLs": [], "LogoutURLs": []}
    empty.pop("DefaultRedirectURI")
    with Stubber(client) as stub:
        stub.add_response("describe_user_pool_client", {"UserPoolClient": empty}, describe_params())
        assert tool.retire(client, POOL, CLIENT, False, out=[].append) == 0
        stub.add_response("describe_user_pool_client", {"UserPoolClient": empty}, describe_params())
        stub.add_response("delete_user_pool_client", {}, describe_params())
        assert tool.retire(client, POOL, CLIENT, True, out=[].append) == 0
        stub.assert_no_pending_responses()


def test_show_redacts_the_secret(client):
    out: list[str] = []
    with Stubber(client) as stub:
        stub.add_response("describe_user_pool_client", {"UserPoolClient": CONFIG}, describe_params())
        tool.show(client, POOL, CLIENT, out=out.append)
    assert "s3cret" not in out[0]
    assert json.loads(out[0])["ClientSecret"] == "[redacted]"


def test_clients_counts_urls_and_branding(client):
    other = "2abc"
    out: list[str] = []
    with Stubber(client) as stub:
        stub.add_response(
            "list_user_pool_clients",
            {
                "UserPoolClients": [
                    {"ClientId": CLIENT, "UserPoolId": POOL, "ClientName": "astrolift-conflict"},
                    {"ClientId": other, "UserPoolId": POOL, "ClientName": "central"},
                ]
            },
        )
        stub.add_response("describe_user_pool_client", {"UserPoolClient": CONFIG})
        stub.add_client_error("describe_managed_login_branding_by_client", "ResourceNotFoundException")
        stub.add_response(
            "describe_user_pool_client",
            {"UserPoolClient": {"ClientId": other, "CallbackURLs": [KEEP]}},
        )
        stub.add_response("describe_managed_login_branding_by_client", {"ManagedLoginBranding": {}})
        tool.clients(client, POOL, out=out.append)
    assert out[1].split()[:4] == [CLIENT, "4", "4", "NO"]
    assert out[2].split()[:4] == [other, "1", "0", "yes"]
    assert out[-1] == "2 clients; 1 of 20 managed login brandings in use."


def test_main_reports_a_refusal_with_exit_two(client, capsys):
    with Stubber(client) as stub:
        stub.add_response("describe_user_pool_client", {"UserPoolClient": CONFIG}, describe_params())
        code = tool.main(["retire", "--pool", POOL, "--client", CLIENT, "--apply"], client=client)
    assert code == 2
    assert "Refused:" in capsys.readouterr().err
