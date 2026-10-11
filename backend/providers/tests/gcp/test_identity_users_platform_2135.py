"""Google Cloud Identity Platform users through the Identity Toolkit (#2135)."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from _sdk.identity_users import IdentityUsersError
from gcp.identity_users_platform import (
    IdentityPlatformUsersConfig,
    IdentityPlatformUsersDriver,
    identity_platform_project_from_issuer,
)

PROJECT = "acme-prod"
LOCAL_ID = "uid-ann-0001"


class Toolkit:
    """A recorded Identity Toolkit: answers by method and keeps every call."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def open(self, request, timeout=None):
        method = request.full_url.split(f"/projects/{PROJECT}/", 1)[1]
        body = json.loads(request.data)
        self.calls.append(SimpleNamespace(method=method, body=body, auth=request.get_header("Authorization")))
        answer = self.answers.get(method, {})
        if callable(answer):
            answer = answer(body)
        if isinstance(answer, HTTPError):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def driver(answers=None):
    toolkit = Toolkit(answers)
    d = IdentityPlatformUsersDriver(
        IdentityPlatformUsersConfig(project_id=PROJECT), token=lambda: "adc-token", opener=toolkit
    )
    return d, toolkit


def toolkit_error(message):
    body = json.dumps({"error": {"code": 400, "message": message}}).encode()
    return HTTPError("https://identitytoolkit.googleapis.com", 400, "Bad Request", {}, io.BytesIO(body))


ANN = {"localId": LOCAL_ID, "email": "ann@acme.example", "emailVerified": True, "createdAt": "1760000000000"}


@pytest.mark.parametrize(
    "url",
    [
        f"https://securetoken.google.com/{PROJECT}",
        f"https://securetoken.google.com/{PROJECT}/.well-known/openid-configuration",
    ],
)
def test_the_project_comes_from_the_issuer(url):
    assert identity_platform_project_from_issuer(url) == PROJECT


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://accounts.google.com",
        f"https://securetoken.google.com.evil.example/{PROJECT}",
        "https://login.example.net/oidc",
    ],
)
def test_another_provider_is_not_identity_platform(url):
    assert identity_platform_project_from_issuer(url) is None


def test_a_user_with_a_password_is_created_verified_and_scrubbed_on_error():
    d, toolkit = driver({"accounts": {"localId": LOCAL_ID}})
    user = d.create_user(email="ann@acme.example", password="S3cret!pass", permanent=True)
    [call] = toolkit.calls
    assert call.method == "accounts" and call.body == {
        "email": "ann@acme.example",
        "emailVerified": True,
        "password": "S3cret!pass",
    }
    assert call.auth == "Bearer adc-token" and user.provider_user_id == LOCAL_ID and d.pool_id == PROJECT

    d, _ = driver({"accounts": toolkit_error("WEAK_PASSWORD : S3cret!pass is too weak")})
    with pytest.raises(IdentityUsersError) as exc:
        d.create_user(email="ann@acme.example", password="S3cret!pass", permanent=False)
    assert "S3cret!pass" not in str(exc.value) and "WEAK_PASSWORD" in str(exc.value)


def test_a_user_without_a_password_gets_a_provider_sent_set_password_email():
    d, toolkit = driver({"accounts": {"localId": LOCAL_ID}})
    d.create_user(email="ann@acme.example", password=None, permanent=False)
    assert [c.method for c in toolkit.calls] == ["accounts", "accounts:sendOobCode"]
    assert toolkit.calls[1].body == {"requestType": "PASSWORD_RESET", "email": "ann@acme.example"}
    assert "password" not in toolkit.calls[0].body


def test_groups_are_empty_and_every_group_write_is_refused_before_the_provider():
    d, toolkit = driver()
    assert d.list_groups() == []
    for call in (
        lambda: d.create_group(name="ops"),
        lambda: d.add_to_group(username="ann@acme.example", group="ops"),
        lambda: d.remove_from_group(username="ann@acme.example", group="ops"),
        lambda: d.create_user(email="ann@acme.example", password="x", permanent=True, groups=("ops",)),
    ):
        with pytest.raises(IdentityUsersError, match="no groups"):
            call()
    assert toolkit.calls == []


def test_writes_resolve_the_exact_local_id_and_reset_sends_the_provider_email():
    d, toolkit = driver({"accounts:lookup": {"users": [ANN]}})
    d.set_enabled(username="ann@acme.example", enabled=False)
    d.set_password(username="ann@acme.example", password="N3w!pass", permanent=False)
    d.delete_user(username="ann@acme.example")
    d.reset_password(username="ann@acme.example")
    writes = [(c.method, c.body) for c in toolkit.calls if c.method != "accounts:lookup"]
    assert writes == [
        ("accounts:update", {"localId": LOCAL_ID, "disableUser": True}),
        ("accounts:update", {"localId": LOCAL_ID, "password": "N3w!pass"}),
        ("accounts:delete", {"localId": LOCAL_ID}),
        ("accounts:sendOobCode", {"requestType": "PASSWORD_RESET", "email": "ann@acme.example"}),
    ]


def test_an_unknown_user_is_refused_without_a_write():
    d, toolkit = driver({"accounts:lookup": {"users": []}})
    with pytest.raises(IdentityUsersError, match="No Identity Platform user"):
        d.delete_user(username="nobody@acme.example")
    assert [c.method for c in toolkit.calls] == ["accounts:lookup"]


def test_listing_caps_filters_by_prefix_and_maps_state():
    rows = [ANN, {"localId": "uid-bob", "email": "bob@acme.example", "disabled": True}]
    d, toolkit = driver({"accounts:query": {"userInfo": rows}})
    [ann] = d.list_users(search="ANN", limit=500)
    assert toolkit.calls[0].body["returnUserInfo"] is True
    assert ann.username == "ann@acme.example" and ann.status == "VERIFIED" and ann.created_at.year == 2025
    assert [u.enabled for u in d.list_users()] == [True, False]


def test_verification_uses_the_immutable_local_id():
    d, _ = driver({"accounts:lookup": {"users": [{**ANN, "email": "someone@else.example"}]}})
    with pytest.raises(IdentityUsersError, match="changed"):
        d.verify_user(username="ann@acme.example", expected_user_id=LOCAL_ID)
    with pytest.raises(IdentityUsersError, match="unknown"):
        d.verify_user(username="ann@acme.example", expected_user_id="")


def test_the_guard_runs_before_every_write_and_never_before_a_read():
    d, toolkit = driver({"accounts:lookup": {"users": [ANN]}, "accounts": {"localId": LOCAL_ID}})
    order = []
    original = toolkit.open

    def recording(request, timeout=None):
        order.append(request.full_url.rsplit("/", 1)[1])
        return original(request, timeout)

    toolkit.open = recording
    with d.guard_writes(lambda: order.append("guard")):
        d.set_enabled(username="ann@acme.example", enabled=True)
        d.create_user(email="ann@acme.example", password=None, permanent=False)
    writes = [i for i, step in enumerate(order) if step in ("accounts:update", "accounts", "accounts:sendOobCode")]
    assert writes and all(order[i - 1] == "guard" for i in writes)
    assert order.count("guard") == len(writes)
