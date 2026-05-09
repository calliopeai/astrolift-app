"""Tests for installation.deleted handler + commit-status mapping (#145)."""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_scm.commit_status import (
    deployment_status_to_github,
    post_commit_status,
)
from astrolift_scm.models import SourceConnection
from astrolift_scm.orphan import (
    handle_github_installation_deleted,
    mark_connection_orphaned,
    reconnect_connection,
)

pytestmark = pytest.mark.django_db


_org_counter = 0


def _conn(**overrides):
    """Create a SourceConnection. ``dict.pop(key, default)``
    evaluates ``default`` eagerly — if we wrote it inline as
    ``Organization.objects.create(...)``, the row would get inserted
    on every call. Build lazily, with a unique slug per call so the
    org_slug uniqueness constraint doesn't trip across parametrised
    runs."""
    global _org_counter
    org = overrides.pop("organization", None)
    if org is None:
        _org_counter += 1
        org = Organization.objects.create(
            name="ACME Corp", slug=f"acme-corp-{_org_counter}"
        )
    defaults = {
        "kind": SourceConnection.Kind.GITHUB_APP_INSTALL,
        "account_login": "acme-corp-org",
        "installation_id": "1234",
    }
    defaults.update(overrides)
    return SourceConnection.objects.create(organization=org, **defaults)


# ---- orphan flip ------------------------------------------------------


def test_mark_orphaned_stamps_reason_and_timestamp():
    c = _conn()
    mark_connection_orphaned(c, reason="github installation uninstalled by owner")
    c.refresh_from_db()
    assert c.is_orphaned is True
    assert c.orphaned_at is not None
    assert "uninstalled" in c.orphaned_reason


def test_mark_orphaned_is_idempotent():
    """Already-orphaned rows stay as they were — the original reason
    + timestamp are the audit trail."""
    c = _conn()
    mark_connection_orphaned(c, reason="first reason")
    c.refresh_from_db()
    first_reason = c.orphaned_reason
    first_at = c.orphaned_at

    mark_connection_orphaned(c, reason="second reason — should not replace")
    c.refresh_from_db()
    assert c.orphaned_reason == first_reason
    assert c.orphaned_at == first_at


def test_reconnect_clears_orphan_flags():
    c = _conn()
    mark_connection_orphaned(c, reason="x")
    reconnect_connection(c)
    c.refresh_from_db()
    assert c.is_orphaned is False
    assert c.orphaned_at is None
    assert c.orphaned_reason == ""


def test_reconnect_no_op_on_healthy_connection():
    c = _conn()
    version_before = c.version
    reconnect_connection(c)
    c.refresh_from_db()
    assert c.version == version_before


# ---- installation.deleted handler -------------------------------------


def test_installation_deleted_orphans_all_matching_connections():
    _org_counter_global = id(_conn)  # local unique slug for this test
    org = Organization.objects.create(
        name="Beta", slug=f"beta-{_org_counter_global}"
    )
    # Vary account_login so the (org, kind, account_login) unique
    # constraint doesn't trip — same install, multiple repo owners.
    a = _conn(
        organization=org, installation_id="42",
        account_login="acme-corp-prod", display_name="prod-install",
    )
    b = _conn(
        organization=org, installation_id="42",
        account_login="acme-corp-dr", display_name="dr-install",
    )
    other = _conn(
        organization=org, installation_id="999",
        account_login="some-other-org",
    )

    flipped = handle_github_installation_deleted("42")
    assert flipped == 2

    a.refresh_from_db()
    b.refresh_from_db()
    other.refresh_from_db()
    assert a.is_orphaned is True
    assert b.is_orphaned is True
    assert other.is_orphaned is False


def test_installation_deleted_skips_already_orphaned():
    """Re-firing the same delete (GitHub does retry webhooks) must
    not re-stamp the orphan timestamp."""
    c = _conn(installation_id="42")
    handle_github_installation_deleted("42")
    c.refresh_from_db()
    first_at = c.orphaned_at
    assert handle_github_installation_deleted("42") == 0
    c.refresh_from_db()
    assert c.orphaned_at == first_at


def test_installation_deleted_with_empty_id_is_noop():
    """Defensive — webhook payload variants might omit the id."""
    _conn(installation_id="42")
    assert handle_github_installation_deleted("") == 0


# ---- commit status mapping --------------------------------------------


@pytest.mark.parametrize(
    "deploy_status,gh_state",
    [
        ("pending", "pending"),
        ("pending_approval", "pending"),
        ("deploying", "pending"),
        ("redeploying", "pending"),
        ("running", "success"),
        ("superseded", "success"),
        ("failed", "failure"),
        ("rolled_back", "failure"),
        ("totally-unknown", "error"),
    ],
)
def test_deployment_status_maps_to_github_state(deploy_status, gh_state):
    body = deployment_status_to_github(deploy_status, env_name="prod")
    assert body["state"] == gh_state


def test_status_payload_carries_context_and_description():
    body = deployment_status_to_github(
        "running", env_name="prod", target_url="https://x"
    )
    assert body["context"] == "astrolift/prod"
    assert "succeeded" in body["description"]
    assert body["target_url"] == "https://x"


def test_status_payload_omits_target_url_when_empty():
    """The status API tolerates the field being absent — don't
    emit an empty string that would render as a broken link."""
    body = deployment_status_to_github("running", env_name="prod")
    assert "target_url" not in body


# ---- post short-circuits on orphaned connection -----------------------


def test_post_commit_status_short_circuits_on_orphaned_connection(monkeypatch):
    """Refuses to call GitHub when the connection is dead — the
    install was removed, so further calls would 404 + spam logs.
    Defensive on top of the mark_connection_orphaned policy."""
    c = _conn()
    mark_connection_orphaned(c, reason="x")

    called = {"count": 0}
    monkeypatch.setattr(
        "requests.post", lambda *a, **kw: called.__setitem__("count", called["count"] + 1)
    )
    ok = post_commit_status(
        connection=c,
        owner="acme",
        repo="hello",
        sha="abc",
        payload={"state": "success"},
    )
    assert ok is False
    assert called["count"] == 0
