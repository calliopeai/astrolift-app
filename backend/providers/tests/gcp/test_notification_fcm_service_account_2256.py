"""FCM HTTP v1 outside GCP (#2256): tokens from the operator's service-account key.

google-auth runs for real; only its HTTP transport is a recorder standing in
for Google's token endpoint, so the tests see the exact JWT-bearer grant the
driver's credentials send and verify its signature with the key.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from _sdk.notification import DevicePlatform, NotificationPayload, PushTarget
from gcp import notification_fcm
from gcp.notification_fcm import (
    FCMConfig,
    FCMCredentialsError,
    FCMNotificationDriver,
    _FCMHttpError,
    service_account_token_provider,
)

TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass
class TokenResponse:
    status: int
    data: bytes
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class TokenEndpoint:
    """Google's token endpoint as google-auth's transport sees it."""

    answers: list[tuple[int, dict[str, Any]]] = field(
        default_factory=lambda: [(200, {"access_token": "ya29.first", "expires_in": 3600, "token_type": "Bearer"})]
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, url: str, method: str = "GET", body: Any = None, headers: Any = None, **kwargs: Any):
        self.calls.append({"url": url, "method": method, "body": body, "headers": dict(headers or {})})
        status, payload = self.answers[min(len(self.calls), len(self.answers)) - 1]
        return TokenResponse(status=status, data=json.dumps(payload).encode("utf-8"))


@dataclass
class FakeFCMHttp:
    posts: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list)
    error: _FCMHttpError | None = None

    def post(self, *, url: str, headers: dict[str, str], body: str) -> dict[str, Any]:
        self.posts.append((url, dict(headers), json.loads(body)))
        if self.error is not None:
            raise self.error
        return {"name": "projects/astrolift-mobile/messages/1"}


@pytest.fixture(autouse=True)
def _fresh_credentials():
    notification_fcm._SERVICE_ACCOUNTS.clear()
    yield
    notification_fcm._SERVICE_ACCOUNTS.clear()


@pytest.fixture
def account_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _service_account(key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "astrolift-mobile",
            "private_key_id": "0123456789abcdef",
            "private_key": pem,
            "client_email": "push@astrolift-mobile.iam.gserviceaccount.com",
            "client_id": "1234567890",
            "token_uri": TOKEN_URI,
            **overrides,
        }
    )


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _send(driver: FCMNotificationDriver):
    return driver.send(
        target=PushTarget(registration_id="fcm-token-1", platform=DevicePlatform.ANDROID),
        payload=NotificationPayload(title="t", body="b"),
    )


def test_the_token_comes_from_a_signed_jwt_bearer_grant_for_the_firebase_scope(account_key) -> None:
    endpoint = TokenEndpoint()
    provider = service_account_token_provider(_service_account(account_key), request=endpoint)

    assert provider() == "ya29.first"
    [call] = endpoint.calls
    assert (call["url"], call["method"]) == (TOKEN_URI, "POST")
    form = urllib.parse.parse_qs(call["body"].decode("utf-8"))
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    header, claims, signature = form["assertion"][0].split(".")
    account_key.public_key().verify(
        _b64decode(signature), f"{header}.{claims}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
    )
    assert json.loads(_b64decode(header))["alg"] == "RS256"
    claim_set = json.loads(_b64decode(claims))
    assert claim_set["iss"] == "push@astrolift-mobile.iam.gserviceaccount.com"
    # The scope FCM HTTP v1 documents for send requests.
    assert claim_set["scope"] == "https://www.googleapis.com/auth/firebase.messaging"
    assert claim_set["aud"] == TOKEN_URI


def test_one_key_shares_one_token_until_it_goes_stale(account_key) -> None:
    endpoint = TokenEndpoint()
    secret = _service_account(account_key)
    first = service_account_token_provider(secret, request=endpoint)
    second = service_account_token_provider(secret, request=endpoint)
    assert first() == second() == "ya29.first"
    assert len(endpoint.calls) == 1

    # google-auth calls a token stale 3 m 45 s before it expires.
    stale = TokenEndpoint(answers=[(200, {"access_token": "ya29.short", "expires_in": 120})])
    other = service_account_token_provider(
        _service_account(account_key, private_key_id="fedcba9876543210"), request=stale
    )
    other()
    other()
    assert len(stale.calls) == 2


def test_the_driver_sends_with_the_minted_token(account_key) -> None:
    http = FakeFCMHttp()
    provider = service_account_token_provider(_service_account(account_key), request=TokenEndpoint())
    driver = FCMNotificationDriver(
        config=FCMConfig(project_id="astrolift-mobile", token_provider=provider, http_client=http)
    )
    assert _send(driver).status == "delivered"
    [(url, headers, _body)] = http.posts
    assert url == "https://fcm.googleapis.com/v1/projects/astrolift-mobile/messages:send"
    assert headers["Authorization"] == "Bearer ya29.first"
    assert driver.healthcheck().ok is True


def test_a_refused_grant_fails_the_send_as_retriable_and_the_health_check(account_key) -> None:
    endpoint = TokenEndpoint(answers=[(400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."})])
    provider = service_account_token_provider(_service_account(account_key), request=endpoint)
    http = FakeFCMHttp()
    driver = FCMNotificationDriver(config=FCMConfig(project_id="p", token_provider=provider, http_client=http))

    result = _send(driver)
    assert (result.status, result.retriable) == ("failed", True)
    assert "token mint failed" in result.error
    assert "invalid_grant" in result.error
    assert http.posts == []
    assert driver.healthcheck().ok is False


@pytest.mark.parametrize(
    ("secret", "message"),
    [
        ("not json", "not JSON"),
        (json.dumps(["service_account"]), "JSON object"),
        (json.dumps({"type": "authorized_user", "client_email": "a", "private_key": "k", "token_uri": "u"}), "type"),
        (json.dumps({"type": "service_account", "private_key": "k"}), "client_email, token_uri"),
    ],
)
def test_keys_that_are_not_service_accounts_are_refused(secret, message) -> None:
    with pytest.raises(FCMCredentialsError, match=message):
        service_account_token_provider(secret, request=TokenEndpoint())


def test_an_unusable_private_key_is_refused_without_echoing_it() -> None:
    secret = json.dumps(
        {
            "type": "service_account",
            "client_email": "push@example.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----\n",
            "token_uri": TOKEN_URI,
        }
    )
    with pytest.raises(FCMCredentialsError, match="not a usable RSA key") as raised:
        service_account_token_provider(secret, request=TokenEndpoint())
    assert "not-a-key" not in str(raised.value)


def test_not_found_without_the_unregistered_detail_fails_without_pruning() -> None:
    # Without FCM's UNREGISTERED code a 404 says nothing about the token.
    http = FakeFCMHttp(
        error=_FCMHttpError(
            message="HTTP Error 404: Not Found",
            status_code=404,
            body={"error": {"code": 404, "status": "NOT_FOUND", "message": "Requested entity was not found."}},
        )
    )
    driver = FCMNotificationDriver(config=FCMConfig(project_id="astrolift-mobile", access_token="t", http_client=http))
    result = _send(driver)
    assert (result.status, result.retriable) == ("failed", False)


def test_a_token_from_another_firebase_project_fails_naming_fcms_code() -> None:
    http = FakeFCMHttp(
        error=_FCMHttpError(
            message="HTTP Error 403: Forbidden",
            status_code=403,
            body={
                "error": {
                    "status": "PERMISSION_DENIED",
                    "details": [
                        {"errorCode": "SENDER_ID_MISMATCH"},
                        {"errorCode": "fcm-token-1 <not a code>"},
                    ],
                }
            },
        )
    )
    driver = FCMNotificationDriver(config=FCMConfig(project_id="p", access_token="t", http_client=http))
    result = _send(driver)
    assert (result.status, result.retriable) == ("failed", False)
    assert result.error == "HTTP Error 403: Forbidden (PERMISSION_DENIED SENDER_ID_MISMATCH)"


def test_the_token_endpoint_wait_is_bounded() -> None:
    from google.auth.transport.requests import Request

    request = notification_fcm._google_request()
    assert isinstance(request.func, Request)
    assert request.keywords == {"timeout": 10}


def test_a_legacy_unregistered_status_still_prunes() -> None:
    http = FakeFCMHttp(error=_FCMHttpError(message="404", status_code=404, body={"error": {"status": "UNREGISTERED"}}))
    driver = FCMNotificationDriver(config=FCMConfig(project_id="p", access_token="t", http_client=http))
    assert _send(driver).status == "invalid_token"
