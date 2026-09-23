"""Same-site pages cannot drive session-authenticated surfaces (#1926).

App and preview hosts can share the control plane's registrable domain,
so the session cookie rides their requests. These tests pin the three
guards: credentialed CORS only for the frontend origin, a UI header on
session GraphQL POSTs, and an Origin check on cookie WebSocket handshakes.
"""

import asyncio
import json

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

GQL = f"/{settings.BASE_URL}gql/config/"
FOREIGN = "https://preview.attacker.example"
QUERY = json.dumps({"query": "{ __typename }"})


@pytest.fixture
def session_client(db):
    user = get_user_model().objects.create_user(username="guard-1926", password="x")
    client = Client()
    client.force_login(user)
    return client


def test_credentialed_preflight_from_foreign_origin_is_not_approved(client):
    response = client.options(
        GQL,
        HTTP_ORIGIN=FOREIGN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-platform",
    )
    assert response.headers.get("Access-Control-Allow-Origin") is None
    assert response.headers.get("Access-Control-Allow-Credentials") is None


def test_credentialed_preflight_from_frontend_origin_is_approved(client):
    origin = settings.CORS_ALLOWED_ORIGINS[0]
    response = client.options(
        GQL,
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-platform",
    )
    assert response.headers.get("Access-Control-Allow-Origin") == origin
    assert response.headers.get("Access-Control-Allow-Credentials") == "true"


@pytest.mark.parametrize("path", ["gql/config/", "gql/config/auth/", "gql/config/ws/"])
def test_session_post_without_ui_header_is_refused(session_client, path):
    response = session_client.post(f"/{settings.BASE_URL}{path}", data=QUERY, content_type="text/plain")
    assert response.status_code == 403


def test_session_post_with_ui_header_reaches_graphql(session_client):
    response = session_client.post(GQL, data=QUERY, content_type="application/json", HTTP_X_PLATFORM="web")
    assert response.status_code == 200, response.content
    assert response.json()["data"] == {"__typename": "Query"}


def test_bearer_post_is_not_asked_for_the_ui_header(session_client):
    # A bad bearer fails authentication later; the guard must not be what stops it.
    response = session_client.post(
        GQL, data=QUERY, content_type="application/json", HTTP_AUTHORIZATION="Bearer alft_at_nope"
    )
    assert b"X-Platform" not in response.content


def test_session_post_to_wayfinding_without_ui_header_is_refused(session_client):
    response = session_client.post(
        "/api/agents/v1/wayfinding/ask/", data=json.dumps({"question": "x"}), content_type="text/plain"
    )
    assert response.status_code == 403
    assert b"X-Platform" in response.content


def _handshake(path: str, headers: dict[str, str]) -> list[dict]:
    from config import asgi

    scope = {
        "type": "websocket",
        "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    asyncio.run(asgi.application(scope, receive, send))
    return sent


# An unknown path closes 4404 only after the origin check passes, so it
# tells "refused for origin" (4403) apart from "let through" (4404).
@pytest.mark.parametrize(
    "headers,code",
    [
        ({"Host": "astro.example.com", "Origin": FOREIGN, "Cookie": "sessionid=s"}, 4403),
        (
            {"Host": "astro.example.com", "Origin": "https://app.astro.example.com", "Cookie": "sessionid=s"},
            4403,
        ),
        ({"Host": "astro.example.com", "Cookie": "sessionid=s"}, 4403),
        ({"Host": "astro.example.com", "Origin": "https://astro.example.com", "Cookie": "sessionid=s"}, 4404),
        ({"Host": "localhost:8000", "Origin": "http://localhost:3000", "Cookie": "sessionid=s"}, 4404),
        ({"Host": "astro.example.com", "Authorization": "Bearer alft_at_x"}, 4404),
        ({"Host": "astro.example.com", "Origin": FOREIGN}, 4404),
        ({"Host": "astro.example.com"}, 4404),
    ],
)
def test_websocket_handshake_origin(headers, code):
    assert _handshake("/app/unknown/", headers) == [{"type": "websocket.close", "code": code}]
