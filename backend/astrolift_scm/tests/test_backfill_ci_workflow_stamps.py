"""Tests for the ``backfill_ci_workflow_stamps`` management command (#1211).

The command baselines the versioned-sync record for apps whose managed CI
workflow file predates Phase 0/1 versioning: it fetches the repo file (Phase 2)
and, when present, persists a baseline via Phase 1's ``adopt_repo_ci_workflow``.
Covered here: a baseline is persisted for an unstamped app with a repo file; the
command is idempotent (a re-run finds nothing and never re-fetches); ``--dry-run``
writes nothing; an absent file is skipped; and ``--org`` scopes the sweep.

The repo wire is stubbed at the drift-service boundary (``fetch_repo_ci_workflow``
is what both the command and ``adopt_repo_ci_workflow`` call), so no real
``fetch_file`` is reached.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.ci_templates import TEMPLATE_VERSION, content_hash
from astrolift_scm.services import ci_workflow_drift as drift
from astrolift_scm.services.ci_workflow_drift import CiWorkflowFetchError
from astrolift_scm.services.workflow_sync import render_astrolift_bitbucket_pipeline

pytestmark = pytest.mark.django_db


def _org(slug: str) -> Organization:
    return Organization.objects.create(name=slug, slug=slug)


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
        k8s_namespace=f"ns-{slug}",
        subdomain=slug,
        registry_repo_uri="123456.dkr.ecr.us-east-1.amazonaws.com/hello-app",
    )


def test_backfill_persists_baseline_for_unstamped_app(monkeypatch):
    org = _org("bf-one")
    app = _scaffold(org, slug="bf-app", source_repo="acme/bf")
    assert app.ci_workflow_template_version is None  # unstamped precondition

    body = render_astrolift_bitbucket_pipeline(app)
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: body)

    call_command("backfill_ci_workflow_stamps")

    app.refresh_from_db()
    assert app.ci_workflow_template_version == TEMPLATE_VERSION
    assert app.ci_workflow_state["state"] == "in_sync"
    assert app.ci_workflow_state["synced_hash"] == content_hash(body)


def test_backfill_is_idempotent(monkeypatch):
    org = _org("bf-idem")
    app = _scaffold(org, slug="bf-idem-app", source_repo="acme/bf-idem")
    body = render_astrolift_bitbucket_pipeline(app)
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: body)

    call_command("backfill_ci_workflow_stamps")
    app.refresh_from_db()
    first_version = app.ci_workflow_template_version
    assert first_version == TEMPLATE_VERSION

    # Second run: the app is no longer a candidate (version is non-null), so the
    # command must not even fetch it. A booby-trapped fetch proves that.
    def _boom(*a, **kw):
        raise AssertionError("idempotent re-run must not re-fetch a baselined app")

    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", _boom)
    call_command("backfill_ci_workflow_stamps")

    app.refresh_from_db()
    assert app.ci_workflow_template_version == first_version


def test_backfill_dry_run_writes_nothing(monkeypatch):
    org = _org("bf-dry")
    app = _scaffold(org, slug="bf-dry-app", source_repo="acme/bf-dry")
    body = render_astrolift_bitbucket_pipeline(app)
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: body)

    call_command("backfill_ci_workflow_stamps", "--dry-run")

    app.refresh_from_db()
    assert app.ci_workflow_template_version is None  # unchanged
    assert app.ci_workflow_state == {}  # unchanged


def test_backfill_skips_app_with_no_repo_file(monkeypatch):
    org = _org("bf-absent")
    app = _scaffold(org, slug="bf-absent-app", source_repo="acme/bf-absent")
    # No managed file in the repo → nothing to baseline.
    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", lambda a, **kw: None)

    call_command("backfill_ci_workflow_stamps")

    app.refresh_from_db()
    assert app.ci_workflow_template_version is None


def test_backfill_skips_app_on_fetch_error(monkeypatch):
    org = _org("bf-err")
    app = _scaffold(org, slug="bf-err-app", source_repo="acme/bf-err")

    def _raise(a, **kw):
        raise CiWorkflowFetchError("RATE_LIMITED", "slow down")

    monkeypatch.setattr(drift, "fetch_repo_ci_workflow", _raise)

    # Must not raise out of the command; the app is simply left unstamped.
    call_command("backfill_ci_workflow_stamps")

    app.refresh_from_db()
    assert app.ci_workflow_template_version is None


def test_backfill_org_scope_limits_sweep(monkeypatch):
    org_a = _org("bf-a")
    org_b = _org("bf-b")
    app_a = _scaffold(org_a, slug="a-app", source_repo="acme/a")
    app_b = _scaffold(org_b, slug="b-app", source_repo="acme/b")
    monkeypatch.setattr(
        drift, "fetch_repo_ci_workflow", lambda a, **kw: render_astrolift_bitbucket_pipeline(a)
    )

    call_command("backfill_ci_workflow_stamps", "--org", "bf-a")

    app_a.refresh_from_db()
    app_b.refresh_from_db()
    assert app_a.ci_workflow_template_version == TEMPLATE_VERSION  # in scope → baselined
    assert app_b.ci_workflow_template_version is None  # out of scope → untouched
