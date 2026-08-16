"""Tests for the preview operator pin — ``set_preview_pinned`` (#1399).

The GC policy has modelled a pin since #88 (``is_eligible_for_gc``
short-circuits on it, and max-active eviction filters pinned rows out of
the candidate list), but no column existed to project it from, so both
branches were unreachable. These tests cover the column, the setter, the
mutation, and — the point of the whole change — that a pinned preview
actually survives a sweep that would otherwise evict it.

Covers:
  - set/clear semantics on the model helper, including stamp refresh
    on re-pin and full clear on unpin
  - reason truncation at the storage ceiling
  - a pinned preview surviving BOTH GC axes (TTL expiry, max-active)
  - cross-org isolation: another org's guid is NOT_FOUND and writes nothing
  - permission enforcement
  - PRECONDITION when pinning a torn-down preview, while unpin still works
  - idempotent unpin not bumping ``version``
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_lifecycle.models import PreviewEnvironment
from astrolift_lifecycle.models.preview_environment import PREVIEW_PIN_REASON_MAX_CHARS
from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    SetPreviewPinnedInput,
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


def test_set_pinned_stamps_the_audit_trail(app, env, actor):
    preview = _make_preview(app, env)
    preview.set_pinned(pinned=True, by=actor, reason="  holding for the demo  ")

    assert preview.is_pinned is True
    assert preview.pinned_by == actor
    assert preview.pinned_at is not None
    # Reason is stripped, so accidental whitespace doesn't render oddly.
    assert preview.pin_reason == "holding for the demo"


def test_unpin_clears_the_whole_trail(app, env, actor):
    """A stale "pinned by X because Y" next to an unpinned preview would
    read as currently-in-force, so unpin clears all three fields."""
    preview = _make_preview(app, env)
    preview.set_pinned(pinned=True, by=actor, reason="demo")
    preview.set_pinned(pinned=False)

    assert preview.is_pinned is False
    assert preview.pinned_by is None
    assert preview.pinned_at is None
    assert preview.pin_reason == ""


def test_repin_refreshes_the_stamp(app, env, actor):
    """Re-pinning is an operator renewing an open-ended decision, so the
    *current* justification is what gets recorded — unlike an incident
    pause, where the original start time is the interesting fact."""
    preview = _make_preview(app, env)
    preview.set_pinned(pinned=True, by=actor, reason="first")
    first_at = preview.pinned_at

    preview.set_pinned(pinned=True, by=actor, reason="second")

    assert preview.pin_reason == "second"
    assert preview.pinned_at >= first_at


def test_reason_is_truncated_to_the_storage_ceiling(app, env, actor):
    """The column is a CharField; an over-long reason must be cut before
    it reaches the DB rather than raising on save."""
    preview = _make_preview(app, env)
    preview.set_pinned(pinned=True, by=actor, reason="x" * (PREVIEW_PIN_REASON_MAX_CHARS + 50))

    assert len(preview.pin_reason) == PREVIEW_PIN_REASON_MAX_CHARS
    preview.save(update_fields=[*PreviewEnvironment.PIN_UPDATE_FIELDS, "updated_at", "version"])


# ---- the policy actually honours a persisted pin ---------------------


def test_pinned_preview_survives_both_gc_axes(app, env, actor):
    """The reason #1399 exists.

    Before this change ``PreviewSnapshot.is_pinned`` was never fed from a
    row, so both pin branches in the policy were dead code. Project a
    pinned row and an unpinned one through the same projection the
    scheduled sweep uses, and assert the pinned row is evicted by
    neither TTL expiry nor max-active pressure.
    """
    from astrolift_workflows.activities.scheduled import _preview_gc_snapshot
    from astrolift_workflows.preview_gc import evaluate_app_evictions, is_eligible_for_gc

    stale = timezone.now() - timedelta(days=90)

    pinned = _make_preview(app, env, pr_number=1)
    pinned.last_deployed_at = stale
    pinned.set_pinned(pinned=True, by=actor, reason="long-running demo")
    pinned.save()

    unpinned = _make_preview(app, env, pr_number=2)
    unpinned.last_deployed_at = stale
    unpinned.save()

    snapshots = [_preview_gc_snapshot(pinned), _preview_gc_snapshot(unpinned)]

    # Axis 1: the eligibility gate itself.
    by_id = {s.preview_id: s for s in snapshots}
    assert is_eligible_for_gc(preview=by_id[pinned.pk]) is False
    assert is_eligible_for_gc(preview=by_id[unpinned.pk]) is True

    # Axis 2 + 3: run the real policy with a cap of 1, so both TTL expiry
    # and max-active pressure apply at once. Only the unpinned row goes.
    evicted = {
        d.preview_id
        for d in evaluate_app_evictions(
            previews=snapshots,
            now_unix=int(timezone.now().timestamp()),
            ttl_days=7,
            max_active=1,
        )
    }
    assert pinned.pk not in evicted
    assert unpinned.pk in evicted


def test_snapshot_projects_the_pin_from_the_row(app, env, actor):
    """Guards the projection specifically: a True column must arrive as
    a True snapshot field. A silent False here would restore the old
    dead-code behaviour with every other test still passing."""
    from astrolift_workflows.activities.scheduled import _preview_gc_snapshot

    preview = _make_preview(app, env)
    assert _preview_gc_snapshot(preview).is_pinned is False

    preview.set_pinned(pinned=True, by=actor, reason="hold")
    preview.save()
    assert _preview_gc_snapshot(preview).is_pinned is True


# ---- mutation resolver -----------------------------------------------


def test_pin_mutation_happy_path(org, app, env, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _make_preview(app, env)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.set_preview_pinned(
            fake_info,
            input=SetPreviewPinnedInput(id=GUID(str(preview.guid)), pinned=True, reason="demo"),
        )

    assert result.ok, result.errors
    preview.refresh_from_db()
    assert preview.is_pinned is True
    assert preview.pin_reason == "demo"


def test_unpin_mutation_clears_the_pin(org, app, env, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _make_preview(app, env)
    preview.set_pinned(pinned=True, by=actor, reason="demo")
    preview.save()
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.set_preview_pinned(
            fake_info,
            input=SetPreviewPinnedInput(id=GUID(str(preview.guid)), pinned=False),
        )

    assert result.ok, result.errors
    preview.refresh_from_db()
    assert preview.is_pinned is False
    assert preview.pin_reason == ""
    assert preview.pinned_by is None


def test_idempotent_unpin_does_not_bump_version(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """Unpinning an already-unpinned preview has nothing to clear. Writing
    anyway would bump ``version`` and lose a concurrent update."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _make_preview(app, env)
    before = preview.version
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.set_preview_pinned(
            fake_info,
            input=SetPreviewPinnedInput(id=GUID(str(preview.guid)), pinned=False),
        )

    assert result.ok, result.errors
    preview.refresh_from_db()
    assert preview.version == before


def test_pin_refused_on_torn_down_preview(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """The namespace is already gone, so a pin would protect nothing
    while reading as an active cost decision on the previews page."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _make_preview(app, env, status=PreviewEnvironment.Status.TORN_DOWN.value)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.set_preview_pinned(
            fake_info,
            input=SetPreviewPinnedInput(id=GUID(str(preview.guid)), pinned=True),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    preview.refresh_from_db()
    assert preview.is_pinned is False


def test_unpin_allowed_on_torn_down_preview(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """Operators must always be able to clear stale state, even after
    the preview is gone."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _make_preview(app, env, status=PreviewEnvironment.Status.TORN_DOWN.value)
    preview.set_pinned(pinned=True, by=actor, reason="stale")
    preview.save()
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.set_preview_pinned(
            fake_info,
            input=SetPreviewPinnedInput(id=GUID(str(preview.guid)), pinned=False),
        )

    assert result.ok, result.errors
    preview.refresh_from_db()
    assert preview.is_pinned is False


def test_pin_mutation_not_found(org, actor, fake_info, permission_resolver, no_temporal):
    permission_resolver.grant(Permission.APP_DEPLOY)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.set_preview_pinned(
            fake_info,
            input=SetPreviewPinnedInput(
                id=GUID("00000000-0000-0000-0000-000000000000"), pinned=True
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_pin_requires_permission(org, app, env, actor, fake_info, permission_resolver, no_temporal):
    preview = _make_preview(app, env)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.set_preview_pinned(
            fake_info,
            input=SetPreviewPinnedInput(id=GUID(str(preview.guid)), pinned=True),
        )

    assert not result.ok
    preview.refresh_from_db()
    assert preview.is_pinned is False


def test_pin_rejects_another_orgs_preview_and_writes_nothing(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """Cross-org isolation (#1183).

    ``@tenant_scoped`` only asserts; it does not filter. The resolver
    carries an explicit ``registered_app__organization_id`` filter, and
    this is what proves it: acting as org B against org A's preview guid
    must read as not-found and leave the row untouched. Dropping that
    filter lets org B pin — and later unpin — org A's environments.
    """
    from astrolift_identity.models import Organization

    permission_resolver.grant(Permission.APP_DEPLOY)
    victim = _make_preview(app, env)
    other_org = Organization.objects.create(name="Other", slug="other-test")
    mut = LifecycleMutation()

    with tenant_context(
        TenantContext(organization_id=other_org.id, actor_user_id=actor.id)
    ):
        result = mut.set_preview_pinned(
            fake_info,
            input=SetPreviewPinnedInput(id=GUID(str(victim.guid)), pinned=True, reason="theirs"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    victim.refresh_from_db()
    assert victim.is_pinned is False
    assert victim.pin_reason == ""
