"""Phase 3 tests for the outbound CI-workflow resync sweep (#1211).

Four concerns, kept separate:

* The per-app reconcile pushes ONLY on ``template_stale`` / ``absent`` and
  never on ``repo_drift`` / ``conflict`` / ``in_sync`` — asserted by spying on
  Phase 1's ``sync_workflow_file_to_repo`` (is / isn't called).
* A fetch error (rate limit) SKIPS the app harmlessly, leaving prior state.
* The fleet sweep fans out over the managed apps (excluding unmanaged /
  unstamped ones), tallies counts by resulting state, and one app's fetch error
  doesn't fail the whole sweep.
* The ``resyncAllAstroliftCiWorkflows`` mutation is the platform operator's
  alone (#1978): anyone else is denied before the sweep runs.

Plus a guard that ``ScheduleKind.CI_WORKFLOW_RESYNC`` ships HELD (present in the
catalog + enum, but absent from ``PHASE_3A_ACTIVE_KINDS``).

The repo wire is never touched: ``fetch_repo_ci_workflow`` is stubbed at the
drift-service boundary and ``sync_workflow_file_to_repo`` is replaced with a spy
(no real ``fetch_file`` / ``put_file`` call is reachable).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.ci_templates import TEMPLATE_VERSION, content_hash
from astrolift_scm.schema.mutations import ScmMutation
from astrolift_scm.services import ci_workflow_drift as drift
from astrolift_scm.services.ci_workflow_drift import (
    CiWorkflowFetchError,
    ReconcileOutcome,
    reconcile_one_ci_workflow,
    sweep_ci_workflows,
)
from astrolift_scm.services.workflow_sync import render_astrolift_bitbucket_pipeline
from core.permissions import Permission

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-resync")


def _scaffold(org, *, slug: str, source_repo: str) -> RegisteredApp:
    team, _ = Team.objects.get_or_create(organization=org, slug="eng", defaults={"name": "Eng"})
    project, _ = Project.objects.get_or_create(
        organization=org, team=team, slug="demo", defaults={"name": "Demo"}
    )
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=slug,
        slug=slug,
        source_kind="bitbucket",
        source_repo=source_repo,
        default_branch="main",
        deploy_branch="main",
        manifest_path="astrolift.toml",
        k8s_namespace=f"acme-{slug}",
        subdomain=slug,
        registry_repo_uri="123456.dkr.ecr.us-east-1.amazonaws.com/hello-app",
    )


def _seed(app, *, version: int, synced_hash: str) -> None:
    """Give ``app`` a Phase-1 baseline (version column + synced_hash). No
    ``checked_at`` yet, so a later skip is visible by its absence."""
    app.ci_workflow_template_version = version
    app.ci_workflow_state = {
        "synced_hash": synced_hash,
        "synced_blob_sha": "seed",
        "synced_at": "2026-01-01T00:00:00+00:00",
        "path": "bitbucket-pipelines.yml",
        "state": "in_sync",
    }
    app.save(update_fields=["ci_workflow_template_version", "ci_workflow_state", "updated_at", "version"])


def _spy_push(monkeypatch) -> list:
    """Replace Phase 1's push with a recording spy; returns the list of pushed
    app pks. A real push (fetch_file/put_file) is thereby never reachable."""
    pushed: list = []

    def _spy(app, viewer_user=None):
        pushed.append(app.pk)
        return SimpleNamespace(status="updated", commit_sha="spy", pr_url="", error="")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo", _spy)
    return pushed


def _no_push(monkeypatch) -> None:
    """Make any push explode — a push in a no-push state is a bug."""

    def _boom(*a, **kw):
        raise AssertionError("reconcile must NOT push in this state")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.sync_workflow_file_to_repo", _boom)


# ---------------------------------------------------------------------------
# Per-app reconcile: pushes only on template_stale / absent
# ---------------------------------------------------------------------------


def test_reconcile_pushes_on_template_stale(monkeypatch, org):
    app = _scaffold(org, slug="stale-app", source_repo="acme/stale")
    body = render_astrolift_bitbucket_pipeline(app)
    # synced version behind current + repo body unchanged → TEMPLATE_STALE.
    _seed(app, version=TEMPLATE_VERSION - 1, synced_hash=content_hash(body))
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: body)
    pushed = _spy_push(monkeypatch)

    assert reconcile_one_ci_workflow(app) is ReconcileOutcome.PUSHED
    assert pushed == [app.pk]


def test_reconcile_pushes_on_absent(monkeypatch, org):
    app = _scaffold(org, slug="absent-app", source_repo="acme/absent")
    body = render_astrolift_bitbucket_pipeline(app)
    _seed(app, version=TEMPLATE_VERSION, synced_hash=content_hash(body))
    # File gone from the repo → ABSENT → re-push.
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: None)
    pushed = _spy_push(monkeypatch)

    assert reconcile_one_ci_workflow(app) is ReconcileOutcome.PUSHED
    assert pushed == [app.pk]


def test_reconcile_does_not_push_on_repo_drift(monkeypatch, org):
    app = _scaffold(org, slug="drift-app", source_repo="acme/drift")
    body = render_astrolift_bitbucket_pipeline(app)
    _seed(app, version=TEMPLATE_VERSION, synced_hash=content_hash(body))
    # Repo body edited under a still-current template → REPO_DRIFT.
    edited = body.replace("Astrolift", "Astrolift EDITED", 1)
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: edited)
    _no_push(monkeypatch)

    assert reconcile_one_ci_workflow(app) is ReconcileOutcome.REPO_DRIFT
    app.refresh_from_db()
    assert app.ci_workflow_state["state"] == "repo_drift"
    # Baseline digest untouched — observe-only never re-baselines.
    assert app.ci_workflow_state["synced_hash"] == content_hash(body)


def test_reconcile_does_not_push_on_conflict(monkeypatch, org):
    app = _scaffold(org, slug="conflict-app", source_repo="acme/conflict")
    body = render_astrolift_bitbucket_pipeline(app)
    # Template advanced AND repo edited → CONFLICT (both sides moved).
    _seed(app, version=TEMPLATE_VERSION - 1, synced_hash=content_hash(body))
    edited = body.replace("Astrolift", "Astrolift EDITED", 1)
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: edited)
    _no_push(monkeypatch)

    assert reconcile_one_ci_workflow(app) is ReconcileOutcome.CONFLICT
    app.refresh_from_db()
    assert app.ci_workflow_state["state"] == "conflict"


def test_reconcile_does_not_push_on_in_sync(monkeypatch, org):
    app = _scaffold(org, slug="insync-app", source_repo="acme/insync")
    body = render_astrolift_bitbucket_pipeline(app)
    _seed(app, version=TEMPLATE_VERSION, synced_hash=content_hash(body))
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: body)
    _no_push(monkeypatch)

    assert reconcile_one_ci_workflow(app) is ReconcileOutcome.IN_SYNC
    app.refresh_from_db()
    assert app.ci_workflow_state["state"] == "in_sync"


def test_reconcile_skips_on_fetch_error_leaving_prior_state(monkeypatch, org):
    app = _scaffold(org, slug="rl-app", source_repo="acme/rl")
    body = render_astrolift_bitbucket_pipeline(app)
    _seed(app, version=TEMPLATE_VERSION, synced_hash=content_hash(body))

    def _raise(a, **kw):
        raise CiWorkflowFetchError("RATE_LIMITED", "slow down")

    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", _raise)
    _no_push(monkeypatch)  # a skip must not push either

    assert reconcile_one_ci_workflow(app) is ReconcileOutcome.SKIPPED
    app.refresh_from_db()
    # Prior state untouched — no checked_at written, no state change.
    assert app.ci_workflow_state["state"] == "in_sync"
    assert "checked_at" not in app.ci_workflow_state


# ---------------------------------------------------------------------------
# Fleet sweep: fans out over managed apps; a fetch error skips one, not all
# ---------------------------------------------------------------------------


def test_sweep_fans_out_and_pushes_only_safe_states(monkeypatch, org):
    stale = _scaffold(org, slug="s-stale", source_repo="acme/s-stale")
    insync = _scaffold(org, slug="s-insync", source_repo="acme/s-insync")
    drift_app = _scaffold(org, slug="s-drift", source_repo="acme/s-drift")
    # Unmanaged: no baseline → ci_workflow_template_version stays NULL → excluded.
    _scaffold(org, slug="s-unmanaged", source_repo="acme/s-unmanaged")

    stale_body = render_astrolift_bitbucket_pipeline(stale)
    insync_body = render_astrolift_bitbucket_pipeline(insync)
    drift_body = render_astrolift_bitbucket_pipeline(drift_app)
    _seed(stale, version=TEMPLATE_VERSION - 1, synced_hash=content_hash(stale_body))
    _seed(insync, version=TEMPLATE_VERSION, synced_hash=content_hash(insync_body))
    _seed(drift_app, version=TEMPLATE_VERSION, synced_hash=content_hash(drift_body))

    fetch_map = {
        "acme/s-stale": stale_body,
        "acme/s-insync": insync_body,
        "acme/s-drift": drift_body.replace("Astrolift", "Astrolift EDITED", 1),
    }
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: fetch_map[a.source_repo])
    pushed = _spy_push(monkeypatch)

    summary = sweep_ci_workflows()

    # Only the 3 managed apps are scanned (the unstamped one is excluded).
    assert summary.scanned == 3
    assert summary.pushed == 1
    assert summary.in_sync == 1
    assert summary.repo_drift == 1
    assert summary.conflict == 0
    assert summary.skipped == 0
    assert summary.failed == 0
    # Only the stale app was pushed — the drift/in-sync apps were left alone.
    assert pushed == [stale.pk]


def test_sweep_fetch_error_skips_one_app_without_failing(monkeypatch, org):
    ok = _scaffold(org, slug="s-ok", source_repo="acme/s-ok")
    rl = _scaffold(org, slug="s-rl", source_repo="acme/s-rl")
    ok_body = render_astrolift_bitbucket_pipeline(ok)
    rl_body = render_astrolift_bitbucket_pipeline(rl)
    _seed(ok, version=TEMPLATE_VERSION, synced_hash=content_hash(ok_body))
    _seed(rl, version=TEMPLATE_VERSION, synced_hash=content_hash(rl_body))

    def _fetch(a, **kw):
        if a.source_repo == "acme/s-rl":
            raise CiWorkflowFetchError("RATE_LIMITED", "slow down")
        return ok_body

    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", _fetch)
    _no_push(monkeypatch)

    summary = sweep_ci_workflows()

    assert summary.scanned == 2
    assert summary.skipped == 1  # the rate-limited app
    assert summary.in_sync == 1  # the ok app still processed
    assert summary.failed == 0


def test_sweep_limit_bounds_fan_out(monkeypatch, org):
    for i in range(3):
        app = _scaffold(org, slug=f"lim-{i}", source_repo=f"acme/lim-{i}")
        body = render_astrolift_bitbucket_pipeline(app)
        _seed(app, version=TEMPLATE_VERSION, synced_hash=content_hash(body))

    monkeypatch.setattr(
        drift, "fetch_repo_ci_workflow", lambda a, **kw: render_astrolift_bitbucket_pipeline(a)
    )
    _no_push(monkeypatch)

    summary = sweep_ci_workflows(limit=2)
    assert summary.scanned == 2  # capped, not all 3


# ---------------------------------------------------------------------------
# Mutation: platform operator only, fleet-wide
# ---------------------------------------------------------------------------


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


def test_resync_all_mutation_denied_for_non_admin(monkeypatch):
    """No admin.elevate grant → PERMISSION_DENIED, and the sweep never runs
    (the gate fires before the resolver body)."""

    def _boom(*a, **kw):
        raise AssertionError("permission gate must fire before the sweep runs")

    monkeypatch.setattr(drift, "sweep_ci_workflows", _boom)

    result = ScmMutation().resync_all_astrolift_ci_workflows(_info())

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_resync_all_mutation_admin_runs_the_sweep(monkeypatch, org, permission_resolver):
    permission_resolver.grant(Permission.ADMIN_ELEVATE)

    app = _scaffold(org, slug="m-stale", source_repo="acme/m-stale")
    body = render_astrolift_bitbucket_pipeline(app)
    _seed(app, version=TEMPLATE_VERSION - 1, synced_hash=content_hash(body))
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: body)
    pushed = _spy_push(monkeypatch)
    # The fleet-wide sweep is the platform operator's alone (#1978).
    operator = User.objects.create(username="resync-operator", is_superuser=True, is_active=True)

    result = ScmMutation().resync_all_astrolift_ci_workflows(_info(operator))

    assert result.ok, result.errors
    assert result.data.scanned == 1
    assert result.data.pushed == 1
    assert pushed == [app.pk]


# ---------------------------------------------------------------------------
# Guard: the schedule kind ships HELD (opt-in), not in the active set
# ---------------------------------------------------------------------------


def test_ci_workflow_resync_kind_is_held_not_active():
    from astrolift_workflows.schedule_boot import PHASE_3A_ACTIVE_KINDS
    from astrolift_workflows.schedule_registry import (
        DEFAULT_SCHEDULES,
        ScheduleKind,
        get_schedule,
    )

    # Present in the enum + catalog, mapped to the tick workflow …
    assert ScheduleKind.CI_WORKFLOW_RESYNC in {s.kind for s in DEFAULT_SCHEDULES}
    assert get_schedule(kind=ScheduleKind.CI_WORKFLOW_RESYNC).workflow_name == "CiWorkflowResyncTickWorkflow"
    # … but deliberately NOT auto-activated (ships inert, opt-in only).
    assert ScheduleKind.CI_WORKFLOW_RESYNC not in PHASE_3A_ACTIVE_KINDS


def test_managed_apps_query_excludes_unstamped_and_unpushable(org):
    """The sweep's managed set = pushable host + non-null template version."""
    managed = _scaffold(org, slug="q-managed", source_repo="acme/q-managed")
    body = render_astrolift_bitbucket_pipeline(managed)
    _seed(managed, version=TEMPLATE_VERSION, synced_hash=content_hash(body))
    # Unstamped (never synced) — excluded.
    _scaffold(org, slug="q-unstamped", source_repo="acme/q-unstamped")
    # Stamped but a non-pushable host — excluded.
    up = _scaffold(org, slug="q-git-url", source_repo="acme/q-git-url")
    up.source_kind = "git_url"
    up.ci_workflow_template_version = TEMPLATE_VERSION
    up.save(update_fields=["source_kind", "ci_workflow_template_version", "updated_at", "version"])

    slugs = {a.slug for a in drift.managed_ci_workflow_apps()}
    assert slugs == {"q-managed"}
