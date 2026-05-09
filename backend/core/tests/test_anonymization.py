"""Tests for PII anonymization (#169 part 2, spec 04 §11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.anonymization import (
    DEFAULT_GRACE_DAYS,
    EVENT_PAYLOAD_PII_KEYS,
    USER_PII_FIELDS,
    deterministic_placeholders,
    schedule_for_deletion,
    scrub_event_payload,
)

UTC = UTC


# ---- placeholders ----------------------------------------------------


def test_email_field_gets_email_shaped_placeholder():
    out = deterministic_placeholders(user_id=42)
    assert out.placeholders["email"] == "anon-42@deleted.local"


def test_name_fields_get_human_readable_placeholder():
    """Operator browsing the DB can tell at a glance this is anonymized,
    not real data."""
    out = deterministic_placeholders(user_id=42)
    assert out.placeholders["first_name"] == "deleted-user-42"
    assert out.placeholders["last_name"] == "deleted-user-42"


def test_avatar_url_blanked():
    out = deterministic_placeholders(user_id=42)
    assert out.placeholders["avatar_url"] == ""


def test_account_marked_inactive():
    out = deterministic_placeholders(user_id=42)
    assert out.is_active is False


def test_deterministic_for_same_user_id():
    """Idempotent: re-running the anonymizer produces the same row."""
    a = deterministic_placeholders(user_id=42)
    b = deterministic_placeholders(user_id=42)
    assert a.placeholders == b.placeholders


def test_different_user_ids_get_different_placeholders():
    a = deterministic_placeholders(user_id=42)
    b = deterministic_placeholders(user_id=43)
    assert a.placeholders["email"] != b.placeholders["email"]


def test_field_set_covers_known_pii():
    """If a new PII column lands on the User model, the anonymizer
    must scrub it. Lock the field set so omissions trip this test."""
    assert "email" in USER_PII_FIELDS
    assert "first_name" in USER_PII_FIELDS
    assert "phone" in USER_PII_FIELDS
    assert "avatar_url" in USER_PII_FIELDS


# ---- payload scrubbing ----------------------------------------------


def test_scrubs_known_pii_keys():
    payload = {
        "ip_address": "192.168.1.1",
        "user_agent": "curl/8",
        "deployment_id": 42,
    }
    scrubbed = scrub_event_payload(payload)
    assert scrubbed["ip_address"] == "[anonymized]"
    assert scrubbed["user_agent"] == "[anonymized]"
    # Non-PII fields preserved
    assert scrubbed["deployment_id"] == 42


def test_scrubbing_preserves_original():
    """Returns a new dict; doesn't mutate the input. The audit-log
    rewrite path needs the old payload available for the diff log."""
    payload = {"ip_address": "192.168.1.1"}
    scrubbed = scrub_event_payload(payload)
    assert payload["ip_address"] == "192.168.1.1"
    assert scrubbed["ip_address"] == "[anonymized]"


def test_scrub_handles_missing_keys():
    """Payload without any PII keys passes through unchanged."""
    payload = {"app_id": 5, "deployment_id": 10}
    scrubbed = scrub_event_payload(payload)
    assert scrubbed == payload


def test_scrub_non_dict_passes_through():
    assert scrub_event_payload("not a dict") == "not a dict"  # type: ignore[arg-type]


def test_event_pii_keys_lock():
    """If new PII can land in event payloads, this set must grow.
    Locking it surfaces missed additions in code review."""
    expected = {"ip_address", "user_agent", "email", "request_email", "actor_email"}
    assert set(EVENT_PAYLOAD_PII_KEYS) == expected


# ---- grace period ---------------------------------------------------


def test_default_grace_is_30_days():
    assert DEFAULT_GRACE_DAYS == 30


def test_schedule_eligible_after_grace_window():
    requested = datetime(2026, 5, 1, tzinfo=UTC)
    sched = schedule_for_deletion(requested_at=requested, grace_days=10)
    assert sched.eligible_at == requested + timedelta(days=10)
    assert sched.is_due(now=requested + timedelta(days=10)) is True
    assert sched.is_due(now=requested + timedelta(days=9)) is False


def test_schedule_rejects_naive_request_time():
    with pytest.raises(ValueError):
        schedule_for_deletion(requested_at=datetime(2026, 5, 1))


def test_schedule_rejects_negative_grace():
    with pytest.raises(ValueError):
        schedule_for_deletion(
            requested_at=datetime(2026, 5, 1, tzinfo=UTC), grace_days=-1
        )


def test_is_due_rejects_naive_now():
    sched = schedule_for_deletion(
        requested_at=datetime(2026, 5, 1, tzinfo=UTC), grace_days=30,
    )
    with pytest.raises(ValueError):
        sched.is_due(now=datetime(2026, 6, 5))


def test_zero_grace_means_immediately_due():
    """Some installs (high-security, pre-existing OFAC sanction
    unwind) want immediate anonymization. 0-day grace must be valid."""
    requested = datetime(2026, 5, 1, tzinfo=UTC)
    sched = schedule_for_deletion(requested_at=requested, grace_days=0)
    assert sched.is_due(now=requested) is True
