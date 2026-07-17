"""GitHub reflection is wired into the deploy lifecycle sync hooks (#1124).

Exercises the real ``_mark_deploying_sync`` / ``_mark_running_sync`` /
``_mark_failed_sync`` activities end-to-end against Postgres: the GitHub
HTTP primitives + commit-status post are stubbed at their source modules
(so the real coordinator, connection resolver, owner/repo parse, and
url-resolution all run), and we assert both the reflection calls AND that
the deploy status transition always happens regardless.

The #1 invariant under test: a GitHub problem never fails the deploy.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_scm.models import SourceConnection
from astrolift_workflows.activities.app_lifecycle import (
    _mark_deploying_sync,
    _mark_failed_sync,
    _mark_running_sync,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def gh_conn(org):
    """An org GitHub-App installation so the connection resolver returns
    something for ORG_REPO_WRITE."""
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        account_login="acme",
        installation_id="1234",
    )


@pytest.fixture
def github_app(app):
    """The shared ``app`` fixture, pinned to a real GitHub owner/repo."""
    app.source_kind = "github"
    app.source_repo = "acme/hello"
    app.save(update_fields=["source_kind", "source_repo", "updated_at", "version"])
    return app


@pytest.fixture
def gh_calls(monkeypatch):
    """Record the three outbound GitHub operations and return canned
    success values, so tests assert the wiring without real HTTP."""
    calls: dict[str, list] = {"create": [], "status": [], "commit": []}

    def fake_create(connection, owner, repo, *, ref, environment, description=""):
        calls["create"].append({"owner": owner, "repo": repo, "ref": ref, "environment": environment})
        return 4242

    def fake_status(
        connection,
        owner,
        repo,
        deployment_id,
        *,
        state,
        environment="",
        environment_url="",
        log_url="",
        description="",
    ):
        calls["status"].append(
            {
                "deployment_id": deployment_id,
                "state": state,
                "environment": environment,
                "environment_url": environment_url,
                "log_url": log_url,
                "description": description,
            }
        )
        return True

    def fake_commit(*, connection, owner, repo, sha, payload):
        calls["commit"].append({"owner": owner, "repo": repo, "sha": sha, "payload": payload})
        return True

    monkeypatch.setattr("astrolift_scm.github_deployments.create_github_deployment", fake_create)
    monkeypatch.setattr("astrolift_scm.github_deployments.post_github_deployment_status", fake_status)
    monkeypatch.setattr("astrolift_scm.commit_status.post_commit_status", fake_commit)
    return calls


def _deploy(app, env, *, status, sha="abc123", **extra):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=status,
        image_tag="v1",
        commit_sha=sha,
        **extra,
    )


# ---- happy path: each hook reflects -----------------------------------


def test_mark_deploying_creates_github_deployment(github_app, env, gh_conn, gh_calls, settings):
    settings.APP_BASE_URL = "https://astrolift.test"
    d = _deploy(github_app, env, status=Deployment.Status.PENDING.value)

    _mark_deploying_sync(d.pk)

    d.refresh_from_db()
    # The core transition happened...
    assert d.status == Deployment.Status.DEPLOYING.value
    # ...and the GH deployment id was stored for idempotent updates.
    assert d.github_deployment_id == 4242

    assert len(gh_calls["create"]) == 1
    assert gh_calls["create"][0] == {
        "owner": "acme",
        "repo": "hello",
        "ref": "abc123",
        "environment": "prod",
    }
    assert len(gh_calls["status"]) == 1
    assert gh_calls["status"][0]["state"] == "in_progress"
    assert gh_calls["status"][0]["deployment_id"] == 4242
    assert gh_calls["status"][0]["log_url"] == f"https://astrolift.test/apps/hello-app/deployments/{d.guid}"
    # Dormant commit-status integration now fires: deploying → pending.
    assert len(gh_calls["commit"]) == 1
    assert gh_calls["commit"][0]["sha"] == "abc123"
    assert gh_calls["commit"][0]["payload"]["state"] == "pending"
    assert gh_calls["commit"][0]["payload"]["context"] == "astrolift/prod"


def test_mark_running_reflects_success_with_environment_url(github_app, env, gh_conn, gh_calls):
    # Simulate the create already having happened at deploy-start.
    d = _deploy(
        github_app,
        env,
        status=Deployment.Status.DEPLOYING.value,
        github_deployment_id=555,
    )

    _mark_running_sync(d.pk)

    d.refresh_from_db()
    assert d.status == Deployment.Status.RUNNING.value

    # Reuses the stored id — does NOT create a second GH deployment.
    assert gh_calls["create"] == []
    assert len(gh_calls["status"]) == 1
    status_call = gh_calls["status"][0]
    assert status_call["deployment_id"] == 555
    assert status_call["state"] == "success"
    # environment_url is the app's real resolved public URL (env fixture).
    assert status_call["environment_url"] == "https://hello.example.com/"

    assert gh_calls["commit"][0]["payload"]["state"] == "success"


def test_mark_failed_reflects_failure_with_reason(github_app, env, gh_conn, gh_calls):
    d = _deploy(
        github_app,
        env,
        status=Deployment.Status.DEPLOYING.value,
        github_deployment_id=777,
    )

    _mark_failed_sync(d.pk, "rollout timed out after 10m")

    d.refresh_from_db()
    assert d.status == Deployment.Status.FAILED.value

    assert gh_calls["create"] == []
    assert len(gh_calls["status"]) == 1
    assert gh_calls["status"][0]["deployment_id"] == 777
    assert gh_calls["status"][0]["state"] == "failure"
    assert "rollout timed out" in gh_calls["status"][0]["description"]
    assert gh_calls["commit"][0]["payload"]["state"] == "failure"


def test_mark_running_without_stored_id_creates_then_succeeds(github_app, env, gh_conn, gh_calls):
    """Deploy predates the integration (no stored id): the success hook
    best-effort creates the GH deployment, stores it, then marks success."""
    d = _deploy(github_app, env, status=Deployment.Status.DEPLOYING.value)

    _mark_running_sync(d.pk)

    d.refresh_from_db()
    assert d.status == Deployment.Status.RUNNING.value
    assert d.github_deployment_id == 4242  # created + persisted by the fallback
    assert len(gh_calls["create"]) == 1
    assert gh_calls["status"][0]["state"] == "success"
    assert gh_calls["status"][0]["deployment_id"] == 4242


# ---- graceful skips: deploy still transitions -------------------------


def test_skip_when_blank_source_repo(app, env, gh_conn, gh_calls):
    """app has no source_repo → clean skip, deploy still advances."""
    d = _deploy(app, env, status=Deployment.Status.PENDING.value)  # app.source_repo == ""

    _mark_deploying_sync(d.pk)

    d.refresh_from_db()
    assert d.status == Deployment.Status.DEPLOYING.value
    assert d.github_deployment_id is None
    assert gh_calls["create"] == []
    assert gh_calls["status"] == []
    assert gh_calls["commit"] == []


def test_skip_when_non_github_host(app, env, gh_conn, gh_calls):
    app.source_kind = "gitlab"
    app.source_repo = "acme/hello"
    app.save(update_fields=["source_kind", "source_repo", "updated_at", "version"])
    d = _deploy(app, env, status=Deployment.Status.PENDING.value)

    _mark_deploying_sync(d.pk)

    d.refresh_from_db()
    assert d.status == Deployment.Status.DEPLOYING.value
    assert gh_calls["create"] == []


def test_skip_when_no_commit_sha(github_app, env, gh_conn, gh_calls):
    d = _deploy(github_app, env, status=Deployment.Status.PENDING.value, sha="")

    _mark_deploying_sync(d.pk)

    d.refresh_from_db()
    assert d.status == Deployment.Status.DEPLOYING.value
    assert gh_calls["create"] == []


def test_skip_when_no_github_connection(github_app, env, gh_calls):
    """No SourceConnection for the org → resolver raises → clean skip."""
    d = _deploy(github_app, env, status=Deployment.Status.PENDING.value)

    _mark_deploying_sync(d.pk)

    d.refresh_from_db()
    assert d.status == Deployment.Status.DEPLOYING.value
    assert d.github_deployment_id is None
    assert gh_calls["create"] == []


# ---- best-effort: GitHub failure never fails the deploy ---------------


def test_deploy_unaffected_when_create_returns_none(github_app, env, gh_conn, monkeypatch):
    """create returns None (e.g. GitHub 500): no id stored, no status
    posted, deploy still transitions."""
    monkeypatch.setattr(
        "astrolift_scm.github_deployments.create_github_deployment",
        lambda *a, **k: None,
    )
    status_calls = []
    monkeypatch.setattr(
        "astrolift_scm.github_deployments.post_github_deployment_status",
        lambda *a, **k: status_calls.append(k) or False,
    )
    monkeypatch.setattr("astrolift_scm.commit_status.post_commit_status", lambda **k: False)

    d = _deploy(github_app, env, status=Deployment.Status.PENDING.value)
    _mark_deploying_sync(d.pk)

    d.refresh_from_db()
    assert d.status == Deployment.Status.DEPLOYING.value
    assert d.github_deployment_id is None
    # No deployment id → no deployment-status POST attempted.
    assert status_calls == []


def test_deploy_unaffected_when_reflection_raises(github_app, env, gh_conn, monkeypatch):
    """Even an unexpected exception in the reflection path (beyond the
    best-effort return contract) can never fail the deploy — the hook's
    belt-and-suspenders try/except swallows it."""

    def _boom(*a, **k):
        raise RuntimeError("github exploded")

    monkeypatch.setattr("astrolift_scm.github_deployments.create_github_deployment", _boom)

    d = _deploy(github_app, env, status=Deployment.Status.PENDING.value)
    # Must NOT raise.
    _mark_deploying_sync(d.pk)

    d.refresh_from_db()
    assert d.status == Deployment.Status.DEPLOYING.value
