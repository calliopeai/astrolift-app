"""The Client Cove support module over HTTP (#1809): gates, the install's own
deployment, ticket detail and replies, and upstream refusals."""

import json
from uuid import uuid4

import pytest
import requests
from constance.test import override_config
from django.test import Client

from astrolift_identity.models import Member
from config.schema import schema
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db

DEPLOYMENT = "0b6f7a6e-1c3a-4c56-9f2a-6f0e3c9d2a11"
TICKET = "5f0c2d4e-8a7b-4c1d-9e3f-2a1b0c9d8e7f"


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )


class _Reply:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


@pytest.fixture
def upstream(monkeypatch, settings):
    """Client Cove at the HTTP boundary: records each call, answers ``reply``."""
    monkeypatch.setenv("FEATURE_SUPPORT", "1")
    settings.ASTROLIFT_SUPPORT_ENABLED = True
    settings.CLIENT_COVE_SUPPORT_URL = "https://cove.example.test"
    settings.CLIENT_COVE_SUPPORT_API_KEY = "shared-secret"
    settings.CLIENT_COVE_DEPLOYMENT_ID = DEPLOYMENT
    state = {"calls": [], "reply": _Reply(200, {"tickets": []})}

    def record(method):
        def call(url, headers=None, json=None, timeout=None):
            state["calls"].append((method, url, json))
            return state["reply"]

        return call

    monkeypatch.setattr("core.services.client_cove_support.requests.get", record("GET"))
    monkeypatch.setattr("core.services.client_cove_support.requests.post", record("POST"))
    with override_config(SUPPORT_ENABLED=True):
        yield state


@pytest.fixture
def member():
    w = ScopeWorld("support-1809")
    w.user = make_user("support-1809")
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(w.user, permissions=[Permission.ORG_READ], kind="ORG", scope_id=w.org.pk, slug=uuid4().hex)
    client = Client()
    client.force_login(w.user)
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(w.org.guid), "HTTP_X_PLATFORM": "web"}
    return client, headers


def _post(client, path, body, headers):
    return client.post(path, json.dumps(body), content_type="application/json", **headers)


def test_the_module_is_off_until_the_install_switches_it_on(upstream, member):
    client, headers = member
    with override_config(SUPPORT_ENABLED=False):
        assert client.get("/api/support/v1/tickets/", **headers).status_code == 404
        assert client.get(f"/api/support/v1/tickets/{TICKET}/", **headers).status_code == 404
    assert upstream["calls"] == []


def test_a_session_post_without_the_ui_header_is_refused(upstream, member):
    client, headers = member
    del headers["HTTP_X_PLATFORM"]
    response = _post(client, "/api/support/v1/tickets/", {"title": "t", "body": "b"}, headers)
    assert response.status_code == 403
    assert upstream["calls"] == []


def test_a_case_is_filed_against_this_install_whatever_the_browser_sends(upstream, member):
    client, headers = member
    upstream["reply"] = _Reply(201, {"ticket": {"id": TICKET, "status": "new"}})
    response = _post(
        client,
        "/api/support/v1/tickets/",
        {
            "title": "Deploys stuck",
            "body": "Since 09:00",
            "severity": 2,
            "product": "zentinelle",
            "deployment_id": str(uuid4()),
        },
        headers,
    )
    assert response.status_code == 201, response.content
    assert response.json() == {"ticket": {"id": TICKET, "status": "new"}}
    [(method, url, sent)] = upstream["calls"]
    assert (method, url) == ("POST", "https://cove.example.test/app/api/support/tickets/")
    assert sent == {
        "product": "astrolift",
        "deployment_id": DEPLOYMENT,
        "title": "Deploys stuck",
        "body": "Since 09:00",
        "severity": 2,
    }


@pytest.mark.parametrize("severity", [0, 5, "1", True, None])
def test_an_invalid_severity_never_reaches_client_cove(upstream, member, severity):
    client, headers = member
    response = _post(
        client, "/api/support/v1/tickets/", {"title": "t", "body": "b", "severity": severity}, headers
    )
    assert response.status_code == 400
    assert response.json()["code"] == "SUPPORT_REQUEST_INVALID"
    assert upstream["calls"] == []


def test_without_a_configured_deployment_no_case_is_filed(upstream, member, settings):
    client, headers = member
    settings.CLIENT_COVE_DEPLOYMENT_ID = ""
    response = _post(client, "/api/support/v1/tickets/", {"title": "t", "body": "b"}, headers)
    assert response.status_code == 503
    assert response.json()["code"] == "SUPPORT_UNAVAILABLE"
    assert upstream["calls"] == []


@pytest.mark.parametrize(
    ("status", "expected", "code"),
    [
        (402, 402, "SUPPORT_COVERAGE_REQUIRED"),
        (404, 404, "NOT_FOUND"),
        (400, 400, "SUPPORT_REQUEST_INVALID"),
        (500, 502, "SUPPORT_UNAVAILABLE"),
    ],
)
def test_upstream_refusals_keep_their_meaning(upstream, member, status, expected, code):
    client, headers = member
    upstream["reply"] = _Reply(status, {"error": "upstream detail, not echoed"})
    response = _post(client, "/api/support/v1/tickets/", {"title": "t", "body": "b"}, headers)
    assert response.status_code == expected
    assert response.json()["code"] == code
    assert "upstream detail" not in response.content.decode()


def test_a_ticket_is_read_and_answered_by_its_id(upstream, member):
    client, headers = member
    upstream["reply"] = _Reply(200, {"ticket": {"id": TICKET, "events": []}})
    assert client.get(f"/api/support/v1/tickets/{TICKET}/", **headers).json()["ticket"]["id"] == TICKET
    replied = _post(client, f"/api/support/v1/tickets/{TICKET}/", {"message": "  Still failing  "}, headers)
    assert replied.status_code == 200, replied.content
    assert upstream["calls"] == [
        ("GET", f"https://cove.example.test/app/api/support/tickets/{TICKET}/", None),
        (
            "POST",
            f"https://cove.example.test/app/api/support/tickets/{TICKET}/",
            {"message": "Still failing"},
        ),
    ]
    empty = _post(client, f"/api/support/v1/tickets/{TICKET}/", {"message": "   "}, headers)
    assert empty.status_code == 400
    assert len(upstream["calls"]) == 2


def test_the_install_handshake_publishes_the_switch():
    query = "{astroliftServerInfo{featureFlags{key enabled}}}"
    for value in (False, True):
        with override_config(SUPPORT_ENABLED=value):
            flags = schema.execute_sync(query).data["astroliftServerInfo"]["featureFlags"]
        assert {row["key"]: row["enabled"] for row in flags}["support.enabled"] is value
