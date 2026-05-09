"""Tests for webhook ingress policy (#93, spec 27 §14)."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from astrolift_scm.webhook_ingress import (
    WEBHOOK_RATE_LIMIT_PER_SECOND,
    DeliveryRef,
    WebhookRejected,
    WebhookSource,
    evaluate,
    extract_delivery_id,
    is_replay,
    verify_signature,
)

SECRET = b"super-secret-key"
BODY = b'{"event":"push"}'


def _gh_sig(body: bytes, secret: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


# ---- signature verification ----------------------------------------


def test_github_valid_signature_passes():
    headers = {
        "X-Hub-Signature-256": _gh_sig(BODY, SECRET),
        "X-GitHub-Delivery": "abc-123",
    }
    verify_signature(
        source=WebhookSource.GITHUB, secret=SECRET,
        raw_body=BODY, headers=headers,
    )


def test_github_invalid_signature_rejected():
    headers = {
        "X-Hub-Signature-256": "sha256=" + "0" * 64,
    }
    with pytest.raises(WebhookRejected, match="mismatch"):
        verify_signature(
            source=WebhookSource.GITHUB, secret=SECRET,
            raw_body=BODY, headers=headers,
        )


def test_missing_signature_rejected():
    with pytest.raises(WebhookRejected, match="missing"):
        verify_signature(
            source=WebhookSource.GITHUB, secret=SECRET,
            raw_body=BODY, headers={},
        )


def test_empty_secret_rejected():
    """Misconfigured source with no secret — fail closed."""
    headers = {"X-Hub-Signature-256": "sha256=anything"}
    with pytest.raises(WebhookRejected, match="no secret"):
        verify_signature(
            source=WebhookSource.GITHUB, secret=b"",
            raw_body=BODY, headers=headers,
        )


def test_gitlab_uses_opaque_token():
    """GitLab is the odd one out — token compared verbatim, no HMAC."""
    headers = {"X-Gitlab-Token": "super-secret-key"}
    verify_signature(
        source=WebhookSource.GITLAB, secret=SECRET,
        raw_body=BODY, headers=headers,
    )


def test_gitlab_token_mismatch_rejected():
    headers = {"X-Gitlab-Token": "wrong"}
    with pytest.raises(WebhookRejected, match="mismatch"):
        verify_signature(
            source=WebhookSource.GITLAB, secret=SECRET,
            raw_body=BODY, headers=headers,
        )


def test_bitbucket_signature():
    sig = "sha256=" + hmac.new(SECRET, BODY, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature": sig, "X-Hook-UUID": "uuid-1"}
    verify_signature(
        source=WebhookSource.BITBUCKET, secret=SECRET,
        raw_body=BODY, headers=headers,
    )


def test_gitea_bare_hex_signature():
    """Gitea sends bare hex, no 'sha256=' prefix."""
    sig = hmac.new(SECRET, BODY, hashlib.sha256).hexdigest()
    headers = {"X-Gitea-Signature": sig, "X-Gitea-Delivery": "g-1"}
    verify_signature(
        source=WebhookSource.GITEA, secret=SECRET,
        raw_body=BODY, headers=headers,
    )


def test_header_lookup_case_insensitive():
    """RFC 7230: HTTP headers are case-insensitive. Some
    receivers normalize, some don't."""
    headers = {"x-hub-signature-256": _gh_sig(BODY, SECRET)}
    verify_signature(
        source=WebhookSource.GITHUB, secret=SECRET,
        raw_body=BODY, headers=headers,
    )


def test_signature_check_uses_constant_time_compare():
    """Smoke: a length-mismatched signature still rejects without
    crashing. (compare_digest handles the timing.)"""
    headers = {"X-Hub-Signature-256": "sha256=short"}
    with pytest.raises(WebhookRejected):
        verify_signature(
            source=WebhookSource.GITHUB, secret=SECRET,
            raw_body=BODY, headers=headers,
        )


# ---- delivery id ---------------------------------------------------


def test_extract_delivery_id_per_source():
    cases = [
        (WebhookSource.GITHUB, {"X-GitHub-Delivery": "gh-1"}, "gh-1"),
        (WebhookSource.GITLAB, {"X-Gitlab-Event-UUID": "gl-1"}, "gl-1"),
        (WebhookSource.BITBUCKET, {"X-Hook-UUID": "bb-1"}, "bb-1"),
        (WebhookSource.GITEA, {"X-Gitea-Delivery": "gt-1"}, "gt-1"),
        (WebhookSource.GENERIC, {"X-Astrolift-Delivery-Id": "ax-1"}, "ax-1"),
    ]
    for source, headers, expected in cases:
        ref = extract_delivery_id(source=source, headers=headers)
        assert ref.delivery_id == expected
        assert ref.source == source


def test_missing_delivery_id_rejected():
    with pytest.raises(WebhookRejected, match="delivery id"):
        extract_delivery_id(
            source=WebhookSource.GITHUB, headers={},
        )


# ---- replay detection ----------------------------------------------


def test_is_replay_when_id_in_seen_set():
    ref = DeliveryRef(source=WebhookSource.GITHUB, delivery_id="abc-123")
    assert is_replay(ref=ref, seen_ids={"abc-123", "other"}) is True


def test_not_replay_when_unseen():
    ref = DeliveryRef(source=WebhookSource.GITHUB, delivery_id="abc-123")
    assert is_replay(ref=ref, seen_ids={"other"}) is False


# ---- evaluate end-to-end -------------------------------------------


def test_evaluate_accepts_valid_first_time():
    headers = {
        "X-Hub-Signature-256": _gh_sig(BODY, SECRET),
        "X-GitHub-Delivery": "first-delivery",
    }
    out = evaluate(
        source=WebhookSource.GITHUB, secret=SECRET,
        raw_body=BODY, headers=headers, seen_ids=set(),
    )
    assert out.accepted is True
    assert out.delivery_ref.delivery_id == "first-delivery"


def test_evaluate_rejects_bad_signature():
    headers = {
        "X-Hub-Signature-256": "sha256=" + "0" * 64,
        "X-GitHub-Delivery": "x",
    }
    out = evaluate(
        source=WebhookSource.GITHUB, secret=SECRET,
        raw_body=BODY, headers=headers, seen_ids=set(),
    )
    assert out.accepted is False
    assert "mismatch" in out.reason
    assert out.delivery_ref is None


def test_evaluate_replay_rejected_but_carries_ref():
    """Replays are 'rejected' (workflow doesn't re-fire) but the
    receiver still returns 200 — caller logs the replay decision
    against the delivery_ref."""
    headers = {
        "X-Hub-Signature-256": _gh_sig(BODY, SECRET),
        "X-GitHub-Delivery": "already-seen",
    }
    out = evaluate(
        source=WebhookSource.GITHUB, secret=SECRET,
        raw_body=BODY, headers=headers,
        seen_ids={"already-seen"},
    )
    assert out.accepted is False
    assert "replay" in out.reason
    assert out.delivery_ref is not None
    assert out.delivery_ref.delivery_id == "already-seen"


def test_rate_limit_locked():
    """Spec 27 §8: 60/sec/source. Caller enforces; module owns
    the constant for documentation + lock-test."""
    assert WEBHOOK_RATE_LIMIT_PER_SECOND == 60
