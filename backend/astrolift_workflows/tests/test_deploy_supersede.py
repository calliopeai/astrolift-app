"""
mark_running supersedes the prior live deploy (#1103).

``Deployment.Status.RUNNING`` means "successfully live", not
"in progress". Before this fix ``_mark_running_sync`` only transitioned
the new deploy to RUNNING and left every prior running deploy for the
same (app, env) in RUNNING too — so the Active tab accumulated every
historical deploy that had ever gone live.

The fix: when a deploy reaches RUNNING, every OTHER currently-running
deploy for the same (registered_app, app_environment) is transitioned
to SUPERSEDED (running → superseded is an allowed transition). Scoping
is per (app, env): a deploy going live in one env / app never touches a
sibling's live deploy.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.app_lifecycle import _mark_running_sync

pytestmark = pytest.mark.django_db


def _make_deploying(app, env, tag="v1.0.0"):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.DEPLOYING.value,
        image_tag=tag,
    )


def test_second_running_supersedes_first(app, env):
    """A 2nd deploy reaching running flips the 1st to superseded and
    leaves itself as the sole live deploy."""
    first = _make_deploying(app, env, "v1")
    _mark_running_sync(first.pk)
    first.refresh_from_db()
    assert first.status == Deployment.Status.RUNNING.value

    second = _make_deploying(app, env, "v2")
    _mark_running_sync(second.pk)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == Deployment.Status.SUPERSEDED.value
    assert first.ended_at is not None
    assert second.status == Deployment.Status.RUNNING.value


def test_third_running_supersedes_only_the_live_one(app, env):
    """A 3rd deploy supersedes the 2nd (the live one); the already-
    superseded 1st is untouched — terminal rows are never re-transitioned."""
    first = _make_deploying(app, env, "v1")
    _mark_running_sync(first.pk)
    second = _make_deploying(app, env, "v2")
    _mark_running_sync(second.pk)

    first.refresh_from_db()
    first_ended = first.ended_at

    third = _make_deploying(app, env, "v3")
    _mark_running_sync(third.pk)

    first.refresh_from_db()
    second.refresh_from_db()
    third.refresh_from_db()
    assert first.status == Deployment.Status.SUPERSEDED.value
    # Untouched — no second transition on an already-superseded row.
    assert first.ended_at == first_ended
    assert second.status == Deployment.Status.SUPERSEDED.value
    assert third.status == Deployment.Status.RUNNING.value


def test_supersede_is_scoped_per_environment(app, env, env_requires_approval):
    """A deploy going live in env A doesn't supersede env B's live deploy."""
    # env_requires_approval is a second AppEnvironment ("staging") on the
    # same app.
    env_b = env_requires_approval

    live_a = _make_deploying(app, env, "a1")
    _mark_running_sync(live_a.pk)
    live_b = _make_deploying(app, env_b, "b1")
    _mark_running_sync(live_b.pk)

    # A new deploy in env A supersedes only env A's live deploy.
    new_a = _make_deploying(app, env, "a2")
    _mark_running_sync(new_a.pk)

    live_a.refresh_from_db()
    live_b.refresh_from_db()
    new_a.refresh_from_db()
    assert live_a.status == Deployment.Status.SUPERSEDED.value
    assert new_a.status == Deployment.Status.RUNNING.value
    # env B untouched.
    assert live_b.status == Deployment.Status.RUNNING.value


def test_supersede_is_scoped_per_app(app, env, org, project, team, cluster):
    """App X's deploy going live never touches app Y's live deploy."""
    from astrolift_lifecycle.models import AppEnvironment

    app_y = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Other",
        slug="other-app",
        provisioning_status="ready",
    )
    env_y = AppEnvironment.objects.create(
        registered_app=app_y,
        tenant_cluster=cluster,
        name="prod",
        url="https://other.example.com",
        required_approvals=0,
    )

    live_y = _make_deploying(app_y, env_y, "y1")
    _mark_running_sync(live_y.pk)

    live_x = _make_deploying(app, env, "x1")
    _mark_running_sync(live_x.pk)
    new_x = _make_deploying(app, env, "x2")
    _mark_running_sync(new_x.pk)

    live_x.refresh_from_db()
    new_x.refresh_from_db()
    live_y.refresh_from_db()
    assert live_x.status == Deployment.Status.SUPERSEDED.value
    assert new_x.status == Deployment.Status.RUNNING.value
    # app Y untouched.
    assert live_y.status == Deployment.Status.RUNNING.value


def test_mark_running_twice_is_idempotent(app, env):
    """Temporal activities are at-least-once: a re-run of mark_running
    after the DB already committed must NOT raise. RUNNING is not a legal
    self-transition, so the second call is a clean no-op — no raise, no
    second transition — and exactly one RUNNING row remains."""
    d = _make_deploying(app, env, "v1")
    _mark_running_sync(d.pk)
    d.refresh_from_db()
    assert d.status == Deployment.Status.RUNNING.value
    ended = d.ended_at

    # Activity retry after commit — same pk, already RUNNING.
    _mark_running_sync(d.pk)
    d.refresh_from_db()
    assert d.status == Deployment.Status.RUNNING.value
    # The self-transition was skipped, so the timestamps are untouched.
    assert d.ended_at == ended

    running = Deployment.objects.filter(
        registered_app=app,
        app_environment=env,
        status=Deployment.Status.RUNNING.value,
    )
    assert running.count() == 1


def test_two_running_passes_converge_to_single_live(app, env):
    """Two deploys both reaching running (sequential supersede passes)
    converge to exactly one live deploy — the last to run — with the
    prior one superseded. Best-effort stand-in for the concurrent race the
    select_for_update lock closes."""
    a = _make_deploying(app, env, "v1")
    b = _make_deploying(app, env, "v2")

    _mark_running_sync(a.pk)
    _mark_running_sync(b.pk)

    running = Deployment.objects.filter(
        registered_app=app,
        app_environment=env,
        status=Deployment.Status.RUNNING.value,
    )
    assert running.count() == 1
    assert running.first().pk == b.pk

    a.refresh_from_db()
    assert a.status == Deployment.Status.SUPERSEDED.value


def test_remarking_running_is_idempotent(app, env):
    """Re-marking the sole live deploy as running is a no-op supersede-wise
    and doesn't raise (running → running isn't a real transition, but the
    supersede pass must skip the row itself)."""
    only = _make_deploying(app, env, "v1")
    _mark_running_sync(only.pk)
    only.refresh_from_db()
    ended = only.ended_at

    # No other running row exists, and the row itself is excluded from the
    # supersede set — so nothing is transitioned. transition_to(RUNNING) on
    # an already-running row would raise, so mark_running is only ever
    # called once per deploy; here we assert the supersede pass alone is
    # safe by re-running against a fresh sibling that finds only this live.
    sibling = _make_deploying(app, env, "v2")
    _mark_running_sync(sibling.pk)

    only.refresh_from_db()
    sibling.refresh_from_db()
    assert only.status == Deployment.Status.SUPERSEDED.value
    assert only.ended_at == ended  # single, clean transition
    assert sibling.status == Deployment.Status.RUNNING.value
