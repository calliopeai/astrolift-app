"""Persistence + read-status tests for the CI-workflow sync record (#1209).

Exercises the DB-only half of Phase 1:

* ``sync_workflow_file_to_repo`` persists ``ci_workflow_template_version`` +
  ``ci_workflow_state`` after a successful reconcile (created / updated /
  in_sync / pr_opened) — and does NOT persist on fetch_failed.
* ``build_ci_workflow_sync_status`` reads that record back without touching
  the host.

Provider HTTP is stubbed at the ``workflow_sync`` module boundary (same shape
as ``test_workflow_sync_534``), so no network is issued and the push behaviour
itself is unchanged — we only assert the bookkeeping it now leaves behind.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import build_ci_workflow_sync_status
from astrolift_scm.ci_templates import TEMPLATE_VERSION, content_hash, git_blob_sha
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError
from astrolift_scm.services.workflow_sync import (
    render_astrolift_bitbucket_pipeline,
    render_astrolift_gitea_ci_workflow,
    sync_workflow_file_to_repo,
)
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


def _scaffold(*, source_kind: str = "bitbucket", source_repo: str = "acme/api"):
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        source_kind=source_kind,
        source_repo=source_repo,
        default_branch="main",
        deploy_branch="main",
        manifest_path="astrolift.toml",
        k8s_namespace="acme-hello-app",
        subdomain="hello-app",
        registry_repo_uri="123456.dkr.ecr.us-east-1.amazonaws.com/hello-app",
    )
    return org, app


def _bb_conn(org):
    encrypted = encrypt_at_rest(b"bb-token-never-hits-network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.BITBUCKET_OAUTH_USER,
        display_name="Bitbucket: acme",
        account_login="acme-workspace",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _gitea_conn(org):
    encrypted = encrypt_at_rest(b"gitea-token-never-hits-network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITEA_PAT,
        display_name="Gitea: acme",
        account_login="acme",
        api_base_url="https://gitea.example.com",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _put_result(sha="commit-sha"):
    return SimpleNamespace(commit_sha=sha, file_path="x", web_url="https://example.com/x")


def _pr_result(url="https://gitea.example.com/acme/api/pulls/1"):
    return SimpleNamespace(
        url=url,
        number="1",
        head_branch="astrolift/ci-workflow-hello-app",
        base_branch="main",
    )


# ---------------------------------------------------------------------------
# Persistence: created / in_sync / pr_opened / fetch_failed
# ---------------------------------------------------------------------------


def test_persist_on_created_records_version_and_digests(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: _put_result("bb-sha"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = sync_workflow_file_to_repo(app)

    assert result.status == "created"

    app.refresh_from_db()
    assert app.ci_workflow_template_version == TEMPLATE_VERSION

    rendered = render_astrolift_bitbucket_pipeline(app)
    state = app.ci_workflow_state
    assert state["state"] == "in_sync"
    assert state["path"] == "bitbucket-pipelines.yml"
    assert state["synced_hash"] == content_hash(rendered)
    assert state["synced_blob_sha"] == git_blob_sha(rendered.encode("utf-8"))
    assert state["last_commit_sha"] == "bb-sha"
    assert "pr_url" not in state
    # synced_at is a parseable ISO-8601 instant.
    assert dt.datetime.fromisoformat(state["synced_at"])


def test_persist_on_in_sync_omits_commit_sha(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    rendered = render_astrolift_bitbucket_pipeline(app)
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: rendered)
    put_called: list[int] = []
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: put_called.append(1) or _put_result(),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = sync_workflow_file_to_repo(app)

    assert result.status == "in_sync"
    assert not put_called  # push behaviour unchanged — nothing written

    app.refresh_from_db()
    assert app.ci_workflow_template_version == TEMPLATE_VERSION
    state = app.ci_workflow_state
    assert state["state"] == "in_sync"
    assert state["synced_hash"] == content_hash(rendered)
    # No base-branch commit landed on an in_sync, so no last_commit_sha.
    assert "last_commit_sha" not in state
    assert "pr_url" not in state


def test_persist_on_pr_opened_records_pr_url(monkeypatch):
    org, app = _scaffold(source_kind="gitea")
    _gitea_conn(org)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._is_gitea_branch_protected", lambda *a, **kw: True
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._gitea_create_branch_direct", lambda *a, **kw: None
    )
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.put_file", lambda *a, **kw: _put_result())
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.open_pull_request",
        lambda *a, **kw: _pr_result("https://gitea.example.com/acme/api/pulls/7"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = sync_workflow_file_to_repo(app)

    assert result.status == "pr_opened"

    app.refresh_from_db()
    assert app.ci_workflow_template_version == TEMPLATE_VERSION
    rendered = render_astrolift_gitea_ci_workflow(app)
    state = app.ci_workflow_state
    assert state["state"] == "in_sync"
    assert state["path"] == ".gitea/workflows/astrolift-ci.yml"
    assert state["synced_hash"] == content_hash(rendered)
    assert state["pr_url"] == "https://gitea.example.com/acme/api/pulls/7"
    # The change is parked in a review PR — nothing landed on the base branch.
    assert "last_commit_sha" not in state


def test_no_persist_on_fetch_failed(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    def _raise(*a, **kw):
        raise ProviderError("AUTH_FAILED", "bad token", recoverable=True)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", _raise)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = sync_workflow_file_to_repo(app)

    assert result.status == "fetch_failed"

    app.refresh_from_db()
    # Nothing landed → the sync record is untouched (model defaults).
    assert app.ci_workflow_template_version is None
    assert app.ci_workflow_state == {}


# ---------------------------------------------------------------------------
# Read status: build_ci_workflow_sync_status
# ---------------------------------------------------------------------------


def test_status_absent_when_never_synced():
    _org, app = _scaffold(source_kind="bitbucket")

    status = build_ci_workflow_sync_status(app)

    assert status.state == "absent"
    assert status.synced_template_version is None
    assert status.current_template_version == TEMPLATE_VERSION
    assert status.synced_at is None
    assert status.checked_at is None
    assert status.path == ""
    assert status.pr_url == ""
    assert status.detail == ""


def test_status_reflects_persisted_sync(monkeypatch):
    org, app = _scaffold(source_kind="bitbucket")
    _bb_conn(org)

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file", lambda *a, **kw: _put_result("bb-sha")
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        sync_workflow_file_to_repo(app)

    app.refresh_from_db()
    status = build_ci_workflow_sync_status(app)

    assert status.state == "in_sync"
    assert status.synced_template_version == TEMPLATE_VERSION
    assert status.current_template_version == TEMPLATE_VERSION
    assert status.path == "bitbucket-pipelines.yml"
    assert isinstance(status.synced_at, dt.datetime)
    assert status.checked_at is None
