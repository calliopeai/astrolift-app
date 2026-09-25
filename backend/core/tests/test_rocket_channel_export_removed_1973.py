"""``/app/export/?file=rocket-channel-history`` let any logged-in user pull
any Rocket.Chat channel's history through the platform's service account
(#1973). ``download_file`` (``core/views.py``) was ``@login_required`` only:
no org check, no channel-membership check, just a ``chat_identifier`` the
caller could set to anything.

Nothing in the frontend, CLI or mobile app calls this route (grepped every
astrolift-* repo for ``rocket-channel-history`` / ``chat_identifier`` /
``download_file``), and ``rocket-channel-history`` was the only file
exporter ever registered anywhere in the backend, so the export path is
removed rather than gated.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core.utils.file_export_registry import get_file_exporter
from core.utils.request_handler import RequestHandler

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_rocket_channel_history_exporter_is_no_longer_registered():
    assert get_file_exporter("rocket-channel-history") is None


def test_download_file_refuses_the_removed_exporter_without_touching_rocketchat(monkeypatch):
    """A plain, non-operator, non-member user hitting the old URL gets a
    plain 400 and the view never reaches the Rocket.Chat service account:
    patching ``make_request`` to record calls and asserting it is never
    called is what proves the service-account credentials are not used,
    not just that the HTTP status looks right."""
    calls = []
    monkeypatch.setattr(RequestHandler, "make_request", lambda *args, **kwargs: calls.append((args, kwargs)))

    user = User.objects.create_user(username="plain-1973", email="plain-1973@t.test", password="x")
    client = Client()
    client.force_login(user)

    response = client.get("/app/export/", {"file": "rocket-channel-history", "chat_identifier": "general"})

    assert response.status_code == 400
    assert b"is not a valid choice" in response.content
    assert calls == []
