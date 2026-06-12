"""Tests for the skill AI assist endpoint (#884).

POST /api/agents/v1/skills/ai-assist/

Anthropic SDK calls are always mocked — no network hits.
Django test Client is used so the full URL routing, CSRF-exempt decorator,
and middleware chain are exercised.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

User = get_user_model()

_URL = "/api/agents/v1/skills/ai-assist/"


@pytest.fixture(autouse=True)
def _no_debug_toolbar(settings):
    """Strip DjDT middleware so response codes reach assertions cleanly."""
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if "DebugToolbar" not in m]
    settings.DEBUG = False


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """Silence User -> Profile -> OpenSearch indexing on user creation."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def auth_client(db):
    """Authenticated Django test client (session login)."""
    user = User.objects.create_user(
        username="assist-tester",
        email="assist-tester@astrolift.dev",
        password="testpass123",
    )
    client = Client()
    client.force_login(user)
    return client


def _fake_message(text: str):
    """Build a minimal Anthropic-shaped response object."""
    content_block = SimpleNamespace(text=text)
    return SimpleNamespace(content=[content_block])


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_returns_output_on_valid_description(auth_client):
    generated = "You are a code-review agent. Review pull requests for correctness."

    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message(generated)

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        with patch("astrolift_agents.views.skill_ai_assist.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = fake_client
            resp = auth_client.post(
                _URL,
                data=json.dumps({"description": "review pull requests"}),
                content_type="application/json",
            )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload == {"output": generated}


@pytest.mark.django_db
def test_anthropic_called_with_correct_args(auth_client):
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message("some prompt")

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        with patch("astrolift_agents.views.skill_ai_assist.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = fake_client
            auth_client.post(
                _URL,
                data=json.dumps({"description": "monitor infrastructure"}),
                content_type="application/json",
            )

    create_call = fake_client.messages.create.call_args
    assert create_call is not None
    kwargs = create_call.kwargs if create_call.kwargs else create_call[1]
    assert kwargs.get("model") == "claude-sonnet-4-6"
    messages = kwargs.get("messages", [])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "monitor infrastructure"
    assert "system" in kwargs


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_missing_description_returns_400(auth_client):
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        resp = auth_client.post(
            _URL,
            data=json.dumps({}),
            content_type="application/json",
        )
    assert resp.status_code == 400
    assert resp.json() == {"error": "description is required"}


@pytest.mark.django_db
def test_empty_description_returns_400(auth_client):
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        resp = auth_client.post(
            _URL,
            data=json.dumps({"description": "   "}),
            content_type="application/json",
        )
    assert resp.status_code == 400
    assert resp.json() == {"error": "description is required"}


@pytest.mark.django_db
def test_invalid_json_returns_400(auth_client):
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        resp = auth_client.post(
            _URL,
            data=b"not json {{{",
            content_type="application/json",
        )
    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid JSON"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_unauthenticated_request_returns_401():
    client = Client()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        resp = client.post(
            _URL,
            data=json.dumps({"description": "something"}),
            content_type="application/json",
        )
    assert resp.status_code == 401
    assert resp.json() == {"error": "authentication required"}


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_missing_api_key_returns_503(auth_client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = auth_client.post(
        _URL,
        data=json.dumps({"description": "something"}),
        content_type="application/json",
    )
    assert resp.status_code == 503
    assert resp.json() == {"error": "AI assist not configured"}
