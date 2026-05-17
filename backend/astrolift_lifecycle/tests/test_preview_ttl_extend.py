"""Tests for the ``extend_preview_ttl`` mutation + TTL model helper (#431).

Covers:
  - allowed days values (1, 7, 30)
  - rejection of out-of-set values (0, 2, 31, negative)
  - +30d ceiling from now, even on chained extensions
  - re-anchor on expired previews (anchors at now, not the stale past)
  - PRECONDITION on torn-down previews
  - NOT_FOUND on unknown id
  - permission enforcement (no permission → denied)
  - the ``ttl_until`` echoed back on the success payload
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_lifecycle.models import PreviewEnvironment
from astrolift_lifecycle.models.preview_environment import (
    PREVIEW_TTL_EXTEND_DAYS,
    PREVIEW_TTL_MAX_DAYS,
)
from astrolift_lifecycle.schema.mutations import (
    ExtendPreviewTtlInputGql,
    LifecycleMutation,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _make_preview(app, env, *, pr_number: int = 1, status: str | None = None):
    return PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=env,
        pr_number=pr_number,
        branch=f"feat-{pr_number}",
        commit_sha="abc123",
        status=status or PreviewEnvironment.Status.RUNNING.value,
        hostname=f"pr-{pr_number}-hello.pr.acme.example.com",
        namespace=f"preview-{pr_number}",
    )


# ---- model helper ----------------------------------------------------


def test_extend_ttl_pushes_forward_when_not_expired(app, env):
    """Anchoring on max(now, ttl_until) means a not-yet-expired preview
    gets its window pushed out by the full ``days`` argument."""
    preview = _make_preview(app, env)
    initial = preview.ttl_until
    preview.extend_ttl(days=7)
    assert preview.ttl_until > initial
    # Roughly +7d from the original anchor (default ttl was now+7d, so
    # the new one should be ~now+14d, but capped at +30d so stays <= cap).
    assert preview.ttl_until - initial == timedelta(days=7)


def test_extend_ttl_caps_at_max_days_from_now(app, env):
    """Even a giant chain of extensions can't push past +30d from
    *now* — the ceiling is anchored on now, not on the original
    ``ttl_until``."""
    preview = _make_preview(app, env)
    for _ in range(10):
        preview.extend_ttl(days=30)
    now = timezone.now()
    assert preview.ttl_until <= now + timedelta(days=PREVIEW_TTL_MAX_DAYS, seconds=5)
    assert preview.ttl_until >= now + timedelta(days=PREVIEW_TTL_MAX_DAYS - 1)


def test_extend_ttl_reanchors_on_expired_preview(app, env):
    """An already-expired ttl_until shouldn't keep the new value in
    the past — anchor at now and add ``days``."""
    preview = _make_preview(app, env)
    preview.ttl_until = timezone.now() - timedelta(days=14)
    preview.extend_ttl(days=7)
    now = timezone.now()
    assert preview.ttl_until > now
    assert preview.ttl_until <= now + timedelta(days=7, seconds=5)


def test_extend_ttl_rejects_unknown_days(app, env):
    preview = _make_preview(app, env)
    for bad in (0, 2, 14, 31, -1, 365):
        with pytest.raises(ValueError, match="extend days"):
            preview.extend_ttl(days=bad)


def test_extend_ttl_allowed_values_are_locked():
    """Sanity-check the public constant — the mutation resolver
    surfaces it in error messages, so the contract should be stable."""
    assert PREVIEW_TTL_EXTEND_DAYS == (1, 7, 30)


# ---- mutation resolver -----------------------------------------------


def test_extend_mutation_happy_path(org, app, env, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _make_preview(app, env)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.extend_preview_ttl(
            fake_info,
            input=ExtendPreviewTtlInputGql(id=GUID(str(preview.guid)), days=7),
        )

    assert result.ok, result.errors
    preview.refresh_from_db()
    assert result.data.ttl_until == preview.ttl_until
    # Should have advanced past the original default ttl_until.
    assert preview.ttl_until > timezone.now() + timedelta(days=6)


def test_extend_mutation_rejects_bad_days(org, app, env, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _make_preview(app, env)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.extend_preview_ttl(
            fake_info,
            input=ExtendPreviewTtlInputGql(id=GUID(str(preview.guid)), days=14),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    preview.refresh_from_db()


def test_extend_mutation_not_found(org, app, env, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_DEPLOY)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.extend_preview_ttl(
            fake_info,
            input=ExtendPreviewTtlInputGql(
                id=GUID("00000000-0000-0000-0000-000000000000"),
                days=7,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_extend_mutation_rejects_torn_down(org, app, env, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _make_preview(
        app,
        env,
        status=PreviewEnvironment.Status.TORN_DOWN.value,
    )
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.extend_preview_ttl(
            fake_info,
            input=ExtendPreviewTtlInputGql(id=GUID(str(preview.guid)), days=7),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_extend_mutation_requires_permission(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """Without ``app.deploy`` the request must be denied — the
    workspace deny-by-default rule applies to every mutation."""
    preview = _make_preview(app, env)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.extend_preview_ttl(
            fake_info,
            input=ExtendPreviewTtlInputGql(id=GUID(str(preview.guid)), days=7),
        )
    assert not result.ok
    # Permission decorator emits a PERMISSION_DENIED-style failure;
    # the exact code is the project's own enum value — we assert the
    # mutation didn't actually run by re-checking the row.
    preview.refresh_from_db()
    # Default TTL ≈ now + 7d; rejection means it didn't move further.
    assert preview.ttl_until <= timezone.now() + timedelta(days=8)


def test_extend_mutation_cap_respected_end_to_end(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """The model layer enforces the cap, so a 30-day extend on a
    preview already near the ceiling still lands at the ceiling."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _make_preview(app, env)
    # Park the row near the ceiling already.
    preview.ttl_until = timezone.now() + timedelta(days=29)
    preview.save(update_fields=["ttl_until"])
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.extend_preview_ttl(
            fake_info,
            input=ExtendPreviewTtlInputGql(id=GUID(str(preview.guid)), days=30),
        )
    assert result.ok
    preview.refresh_from_db()
    now = timezone.now()
    assert preview.ttl_until <= now + timedelta(days=PREVIEW_TTL_MAX_DAYS, seconds=5)
