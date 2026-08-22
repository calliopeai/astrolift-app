"""The two activities that join the teardown policy to the database (#87).

``preview_teardown`` is Django-free, so the workflow needs a projection
activity to decide anything, and the ``preview_env.torn_down`` step of
its sequence needs an activity that actually emits the event. Neither
existed.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from astrolift_lifecycle.models import PreviewEnvironment
from astrolift_workflows.activities.app_lifecycle import (
    _emit_preview_torn_down_event_sync,
    _load_preview_teardown_state_sync,
)
from astrolift_workflows.preview_teardown import (
    PreviewTeardownState,
    already_torn_down,
    teardown_steps_to_run,
)

pytestmark = pytest.mark.django_db


def _make_preview(app, env, *, status: str, torn_down_at=None):
    return PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=env,
        pr_number=11,
        branch="feat-11",
        commit_sha="abc123",
        status=status,
        hostname="pr-11-hello.pr.acme.example.com",
        namespace="preview-11",
        torn_down_at=torn_down_at,
    )


def test_projection_feeds_the_policy_short_circuit(app, env):
    """A torn-down row projects into a state the policy recognises, so
    the workflow skips the destructive steps."""
    preview = _make_preview(
        app,
        env,
        status=PreviewEnvironment.Status.TORN_DOWN.value,
        torn_down_at=timezone.now(),
    )

    state = _load_preview_teardown_state_sync(preview.pk)

    assert state["preview_id"] == preview.pk
    assert state["status"] == "torn_down"
    assert isinstance(state["torn_down_at_unix"], int)
    projected = PreviewTeardownState(**state)
    assert already_torn_down(state=projected) is True
    assert teardown_steps_to_run(state=projected) == ()


def test_projection_of_a_live_preview_runs_every_step(app, env):
    preview = _make_preview(app, env, status=PreviewEnvironment.Status.RUNNING.value)

    state = _load_preview_teardown_state_sync(preview.pk)

    assert state["torn_down_at_unix"] is None
    assert len(teardown_steps_to_run(state=PreviewTeardownState(**state))) == 6


def test_projection_reads_soft_deleted_previews(app, env):
    """A closed PR's preview can already be soft-deleted while its
    namespace is still up, so the projection must not filter it out."""
    preview = _make_preview(app, env, status=PreviewEnvironment.Status.RUNNING.value)
    preview.deleted_at = timezone.now()
    preview.save(update_fields=["deleted_at", "updated_at", "version"])

    assert _load_preview_teardown_state_sync(preview.pk)["status"] == "running"


def test_teardown_event_is_emitted(app, env, org):
    from astrolift_operations.models import Event

    preview = _make_preview(app, env, status=PreviewEnvironment.Status.TORN_DOWN.value)

    _emit_preview_torn_down_event_sync(preview.pk)

    row = Event.objects.get(event_type="preview_env.torn_down")
    assert row.organization_id == org.pk
    assert row.payload["preview_environment_guid"] == str(preview.guid)
    assert row.payload["pr_number"] == 11
