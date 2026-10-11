"""Microsoft Entra ID users and groups through Microsoft Graph (#2134)."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from _sdk.identity_users import IdentityUsersError
from azure.identity_users_entra import EntraIdentityUsersDriver, EntraUsersConfig, entra_tenant_from_issuer

TENANT = "0f1e2d3c-4b5a-4968-8778-695a4b3c2d1e"
USER_ID = "11111111-2222-4333-8444-555555555555"
GROUP_ID = "99999999-8888-4777-8666-555555555555"


class Graph:
    """A recorded Microsoft Graph: answers by (method, path) and keeps every call."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def open(self, request, timeout=None):
        url = urlsplit(request.full_url)
        path = unquote(url.path.removeprefix("/v1.0"))
        body = json.loads(request.data) if request.data else None
        self.calls.append(
            SimpleNamespace(
                method=request.get_method(),
                path=path,
                query=parse_qs(url.query),
                body=body,
                auth=request.get_header("Authorization"),
            )
        )
        answer = self.answers.get((request.get_method(), path), {})
        if isinstance(answer, HTTPError):
            raise answer
        return io.BytesIO(json.dumps(answer).encode() if answer is not None else b"")


class Credential:
    def __init__(self):
        self.scopes = []

    def get_token(self, *scopes, **kwargs):
        self.scopes.append((scopes, kwargs.get("tenant_id")))
        return SimpleNamespace(token="graph-token")


def driver(answers=None):
    graph, credential = Graph(answers), Credential()
    return (
        EntraIdentityUsersDriver(EntraUsersConfig(tenant_id=TENANT), credential=credential, opener=graph),
        graph,
        credential,
    )


def graph_error(code, message):
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    return HTTPError("https://graph.microsoft.com", 400, "Bad Request", {}, io.BytesIO(body))


@pytest.mark.parametrize(
    "url",
    [
        f"https://login.microsoftonline.com/{TENANT}/v2.0",
        f"https://login.microsoftonline.com/{TENANT}/v2.0/.well-known/openid-configuration",
        f"https://sts.windows.net/{TENANT}/",
        f"https://login.microsoftonline.com/{TENANT.upper()}/v2.0",
    ],
)
def test_the_tenant_comes_from_the_issuer(url):
    assert entra_tenant_from_issuer(url) == TENANT


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://login.microsoftonline.com/common/v2.0",
        "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc",
        f"https://evil.example/{TENANT}/v2.0",
        f"https://login.microsoftonline.com.evil.example/{TENANT}/v2.0",
    ],
)
def test_another_provider_or_a_multi_tenant_endpoint_is_not_the_installs_tenant(url):
    assert entra_tenant_from_issuer(url) is None


def test_every_call_uses_a_graph_token_for_the_installs_tenant():
    d, graph, credential = driver({("GET", "/groups"): {"value": [{"displayName": "ops"}]}})
    assert d.list_groups() == ["ops"]
    assert credential.scopes == [(("https://graph.microsoft.com/.default",), TENANT)]
    assert graph.calls[0].auth == "Bearer graph-token" and d.pool_id == TENANT


def test_a_user_with_a_password_is_created_directly_and_permanent_means_no_forced_change():
    d, graph, _ = driver({("POST", "/users"): {"id": USER_ID, "userPrincipalName": "ann@acme.example"}})
    user = d.create_user(email="ann@acme.example", password="S3cret!pass", permanent=True)
    [call] = graph.calls
    assert call.method == "POST" and call.path == "/users"
    assert call.body["userPrincipalName"] == "ann@acme.example"
    assert call.body["passwordProfile"] == {"password": "S3cret!pass", "forceChangePasswordNextSignIn": False}
    assert user.provider_user_id == USER_ID and user.username == "ann@acme.example"


def test_a_user_without_a_password_is_invited_by_entra():
    d, graph, _ = driver({("POST", "/invitations"): {"invitedUser": {"id": USER_ID}}})
    user = d.create_user(email="guest@other.example", password=None, permanent=False)
    [call] = graph.calls
    assert call.path == "/invitations" and call.body["sendInvitationMessage"] is True
    assert "passwordProfile" not in call.body
    assert user.status == "PendingAcceptance" and user.provider_user_id == USER_ID


def test_a_provider_error_never_echoes_the_password():
    d, _, _ = driver(
        {("POST", "/users"): graph_error("Request_BadRequest", "password S3cret!pass does not meet complexity")}
    )
    with pytest.raises(IdentityUsersError) as exc:
        d.create_user(email="ann@acme.example", password="S3cret!pass", permanent=False)
    assert "S3cret!pass" not in str(exc.value) and "Request_BadRequest" in str(exc.value)


def test_entra_has_no_provider_sent_reset_and_says_so_without_calling_graph():
    d, graph, _ = driver()
    with pytest.raises(IdentityUsersError, match="no provider-sent password reset"):
        d.reset_password(username="ann@acme.example")
    assert graph.calls == []


def test_listing_filters_safely_and_carries_each_users_groups():
    d, graph, _ = driver(
        {
            ("GET", "/users"): {
                "value": [
                    {
                        "id": USER_ID,
                        "userPrincipalName": "ann@acme.example",
                        "mail": "ann@acme.example",
                        "accountEnabled": False,
                        "createdDateTime": "2026-10-01T12:00:00Z",
                    }
                ]
            },
            ("GET", f"/users/{USER_ID}/memberOf/microsoft.graph.group"): {
                "value": [{"displayName": "ops"}, {"displayName": "admins"}]
            },
        }
    )
    [user] = d.list_users(search="o'brien", limit=500)
    listing = graph.calls[0]
    assert listing.query["$top"] == ["60"]
    assert "startswith(mail,'o''brien')" in listing.query["$filter"][0]
    assert user.groups == ("admins", "ops") and user.enabled is False and user.created_at.year == 2026


def test_membership_resolves_exact_ids_and_refuses_an_ambiguous_group_name():
    answers = {
        ("GET", "/users/ann@acme.example"): {"id": USER_ID},
        ("GET", "/groups"): {"value": [{"id": GROUP_ID}]},
    }
    d, graph, _ = driver(answers)
    d.add_to_group(username="ann@acme.example", group="ops")
    add = graph.calls[-1]
    assert add.method == "POST" and add.path == f"/groups/{GROUP_ID}/members/$ref"
    assert add.body == {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{USER_ID}"}
    d.remove_from_group(username="ann@acme.example", group="ops")
    assert graph.calls[-1].method == "DELETE" and graph.calls[-1].path == f"/groups/{GROUP_ID}/members/{USER_ID}/$ref"

    answers[("GET", "/groups")] = {"value": [{"id": GROUP_ID}, {"id": USER_ID}]}
    with pytest.raises(IdentityUsersError, match="More than one"):
        d.add_to_group(username="ann@acme.example", group="ops")


def test_verification_uses_the_immutable_object_id_not_the_name():
    d, _, _ = driver(
        {("GET", "/users/ann@acme.example"): {"id": "someone-else", "userPrincipalName": "ann@acme.example"}}
    )
    with pytest.raises(IdentityUsersError, match="changed"):
        d.verify_user(username="ann@acme.example", expected_user_id=USER_ID)
    with pytest.raises(IdentityUsersError, match="unknown"):
        d.verify_user(username="ann@acme.example", expected_user_id="")


def test_the_guard_runs_before_every_write_and_never_before_a_read():
    d, graph, _ = driver(
        {
            ("POST", "/users"): {"id": USER_ID, "userPrincipalName": "ann@acme.example"},
            ("GET", "/users/ann@acme.example"): {"id": USER_ID, "userPrincipalName": "ann@acme.example"},
            ("GET", "/groups"): {"value": [{"id": GROUP_ID}]},
        }
    )
    order = []
    original = graph.open

    def recording(request, timeout=None):
        order.append(request.get_method())
        return original(request, timeout)

    graph.open = recording
    with d.guard_writes(lambda: order.append("guard")):
        d.create_user(email="ann@acme.example", password="S3cret!pass", permanent=True, groups=("ops",))
        d.set_enabled(username="ann@acme.example", enabled=False)
    writes = [i for i, step in enumerate(order) if step in ("POST", "PATCH", "DELETE")]
    assert writes and all(order[i - 1] == "guard" for i in writes)
    assert order.count("guard") == len(writes)
