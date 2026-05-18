"""Tests for ``core.systems.core_system.CoreSystem`` (#538).

The prior ``notify`` body called
``Notification.objects.get_or_create(created_by=created_by)`` —
creating one Notification row per actor, indexed nowhere useful, with
no recipient and no message body. The new body persists a real row
the inbox surface can read.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from core.models import Notification
from core.models.notification import NotificationStatus
from core.systems.core_system import CoreSystem

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_user(username: str):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="x",
    )


def test_notify_persists_notification_row():
    """CoreSystem.notify writes a Notification with the right user /
    creator / message / subject. UNREAD status (the model default)
    is what the inbox surface filters on, so we assert it explicitly
    here — a regression would make the row invisible to the inbox."""
    creator = _make_user("notify-creator-538")
    target = _make_user("notify-target-538")

    note = CoreSystem.notify(
        creator,
        target,
        "Your build finished.",
        subject="Build complete",
    )

    assert note is not None
    assert note.pk is not None
    assert note.user_id == target.id
    assert note.created_by_id == creator.id
    assert note.message == "Your build finished."
    assert note.subject == "Build complete"
    assert note.status == NotificationStatus.UNREAD


def test_notify_default_subject():
    """Callers that omit ``subject`` get a generic default so they
    don't have to thread the kwarg every time."""
    creator = _make_user("notify-creator-538-default")
    target = _make_user("notify-target-538-default")

    note = CoreSystem.notify(creator, target, "hi")
    assert note.subject == "Notification"


def test_notify_two_calls_create_two_rows():
    """Regression for the #538 bug-shape: the prior implementation
    used ``get_or_create(created_by=created_by)`` which silently
    deduplicated every subsequent call by the same creator. New
    implementation must produce a fresh row per call so each ping
    lands in the recipient's inbox independently."""
    creator = _make_user("notify-creator-538-multi")
    target = _make_user("notify-target-538-multi")

    first = CoreSystem.notify(creator, target, "first")
    second = CoreSystem.notify(creator, target, "second")

    assert first.pk != second.pk
    rows = Notification.objects.filter(user=target).order_by("pk")
    assert rows.count() == 2
    assert [r.message for r in rows] == ["first", "second"]


def test_notify_supports_anonymous_creator():
    """System-fired events have no human actor (``created_by=None``);
    persistence still has to work — the Notification model declares
    ``created_by`` nullable."""
    target = _make_user("notify-target-538-anon")

    note = CoreSystem.notify(None, target, "system event")
    assert note.created_by_id is None
    assert note.user_id == target.id
    assert note.message == "system event"


def test_notify_rejects_missing_user():
    """Recipient is required; ``user=None`` raises ValueError rather
    than persisting an orphaned row."""
    creator = _make_user("notify-creator-538-missing-user")
    with pytest.raises(ValueError, match="recipient"):
        CoreSystem.notify(creator, None, "hi")


def test_notify_rejects_empty_message():
    """Message body is required; empty string raises ValueError."""
    creator = _make_user("notify-creator-538-empty-msg")
    target = _make_user("notify-target-538-empty-msg")
    with pytest.raises(ValueError, match="non-empty message"):
        CoreSystem.notify(creator, target, "")


def test_notify_persisted_search_field_includes_subject_and_message():
    """``Notification.save`` builds a search field as
    ``f'{subject} {message}'``. Persisting through the model's full
    save() (not via objects.create's mid-save bypass) is what makes
    the inbox's text search work — assert the field is populated."""
    creator = _make_user("notify-creator-538-search")
    target = _make_user("notify-target-538-search")

    note = CoreSystem.notify(
        creator,
        target,
        "deploy failed at stage 2",
        subject="Deploy alert",
    )
    note.refresh_from_db()
    assert "Deploy alert" in (note.search or "")
    assert "deploy failed at stage 2" in (note.search or "")
