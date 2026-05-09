"""Tests for deploy token issue + rotate + verify (#143).

The rotation contract is security-critical: every property pinned
here protects either the token-at-rest invariant (always hashed) or
the grace-window semantics (CI runners keep working until updated).
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from django.utils import timezone

from astrolift_lifecycle.deploy_tokens import (
    DEFAULT_GRACE_PERIOD_SECONDS,
    PLAINTEXT_PREFIX,
    issue_token,
    rotate_token,
    verify_token,
)
from astrolift_lifecycle.models import DeployToken

pytestmark = pytest.mark.django_db


# ---- issue ------------------------------------------------------------


def test_issue_persists_only_hash(app):
    """Plaintext must NEVER hit the DB; only the SHA-256 hash."""
    row, plaintext = issue_token(app=app, name="ci-prod")
    assert plaintext.startswith(PLAINTEXT_PREFIX)
    assert row.token_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    assert row.token_hash != plaintext  # belt + suspenders
    assert row.token_last_4 == plaintext[-4:]
    assert row.is_revoked is False


def test_issue_default_scopes(app):
    row, _ = issue_token(app=app, name="ci")
    assert row.scopes == ["app.deploy"]


def test_issue_clamps_max_ttl(app):
    """Passing a huge TTL should clamp to MAX_TTL_DAYS rather than
    silently accept (which would produce surprisingly-long-lived
    tokens)."""
    row, _ = issue_token(app=app, name="ci", expires_in_days=10000)
    assert row.expires_at is not None
    delta = row.expires_at - timezone.now()
    # MAX_TTL_DAYS == 5 years; allow 1 day slack for clock drift.
    assert delta.days <= 5 * 365 + 1


# ---- rotate -----------------------------------------------------------


def test_rotate_issues_new_secret_and_parks_old(app):
    """The new hash must replace the current one; the old hash gets
    parked for the grace window. Both are accepted by verify until
    grace expires."""
    row, original_plaintext = issue_token(app=app, name="ci")
    original_hash = row.token_hash

    rotated, new_plaintext = rotate_token(row)
    assert new_plaintext != original_plaintext
    assert rotated.token_hash == hashlib.sha256(new_plaintext.encode()).hexdigest()
    assert rotated.token_hash != original_hash
    assert rotated.previous_token_hash == original_hash
    assert rotated.previous_token_expires_at is not None
    assert rotated.last_rotated_at is not None
    # Default grace period: 1h.
    grace = rotated.previous_token_expires_at - rotated.last_rotated_at
    assert (
        DEFAULT_GRACE_PERIOD_SECONDS - 1
        <= grace.total_seconds()
        <= DEFAULT_GRACE_PERIOD_SECONDS + 1
    )


def test_rotate_immediate_skips_grace_window(app):
    """``immediate=True`` is the compromised-token path: the old hash
    must be cleared so any in-flight CI runner using it gets a 401."""
    row, original_plaintext = issue_token(app=app, name="ci")
    rotated, new_plaintext = rotate_token(row, immediate=True)

    assert rotated.previous_token_hash == ""
    assert rotated.previous_token_expires_at is None
    assert verify_token(original_plaintext, app=app) is None
    assert verify_token(new_plaintext, app=app) == rotated


def test_rotate_un_revokes_token(app):
    """Rotating a previously-revoked token brings it back to active —
    the operator's intent is clear (re-issue secret + flip back on)."""
    row, _ = issue_token(app=app, name="ci")
    row.is_revoked = True
    row.save(update_fields=["is_revoked"])

    rotated, new_plaintext = rotate_token(row)
    assert rotated.is_revoked is False
    assert verify_token(new_plaintext, app=app) == rotated


# ---- verify -----------------------------------------------------------


def test_verify_accepts_current_token(app):
    row, plaintext = issue_token(app=app, name="ci")
    assert verify_token(plaintext, app=app) == row


def test_verify_accepts_previous_token_within_grace(app):
    row, original_plaintext = issue_token(app=app, name="ci")
    rotate_token(row)
    # Old token still works because we're within grace.
    found = verify_token(original_plaintext, app=app)
    assert found is not None
    assert found.pk == row.pk


def test_verify_rejects_previous_token_after_grace(app):
    row, original_plaintext = issue_token(app=app, name="ci")
    rotate_token(row)
    # Backdate the grace expiry.
    DeployToken.objects.filter(pk=row.pk).update(
        previous_token_expires_at=timezone.now() - dt.timedelta(seconds=1)
    )
    assert verify_token(original_plaintext, app=app) is None


def test_verify_rejects_revoked_token(app):
    row, plaintext = issue_token(app=app, name="ci")
    row.is_revoked = True
    row.save(update_fields=["is_revoked"])
    assert verify_token(plaintext, app=app) is None


def test_verify_rejects_expired_token(app):
    row, plaintext = issue_token(app=app, name="ci")
    DeployToken.objects.filter(pk=row.pk).update(
        expires_at=timezone.now() - dt.timedelta(seconds=1)
    )
    assert verify_token(plaintext, app=app) is None


def test_verify_rejects_token_against_wrong_app(app, org, project, team):
    """A leaked deploy token can't be used against a different app."""
    from astrolift_registry.models import RegisteredApp

    other = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Other",
        slug="other-app",
        provisioning_status="ready",
    )
    _, plaintext = issue_token(app=app, name="ci")
    assert verify_token(plaintext, app=other) is None
    # And without an app filter, finds the row (verify works for the
    # caller-doesn't-yet-know-which-app path too).
    assert verify_token(plaintext) is not None


def test_verify_unknown_token_returns_none(app):
    assert verify_token("alft_dt_garbage", app=app) is None
