"""Tests for the pipeline GitHub webhook receiver (#72).

Coverage:
- Valid push event with matching pipeline triggers PipelineRun creation
- Valid push event with no matching pipeline returns 200 with empty dispatched list
- HMAC signature mismatch returns 401
- Missing webhook secret returns 401
- Unknown org slug returns 404
- Non-POST request returns 405
- Unknown GitHub event returns 200 "not handled"
- pull_request event matches PR trigger
- Trigger branch filter works correctly
- run_number is monotonically increasing
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from django.test import RequestFactory

from astrolift_identity.models import Organization
from astrolift_pipelines.gitlab_webhook_views import pipeline_gitlab_webhook
from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger
from astrolift_pipelines.webhook_views import pipeline_github_webhook

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _make_push_payload(clone_url: str, ref: str = "refs/heads/main", actor: str = "octocat") -> dict:
    return {
        "ref": ref,
        "sender": {"login": actor},
        "repository": {
            "clone_url": clone_url,
            "html_url": clone_url.replace(".git", ""),
        },
    }


def _make_pr_payload(clone_url: str, branch: str = "feature/foo") -> dict:
    return {
        "pull_request": {"head": {"ref": branch}},
        "sender": {"login": "user"},
        "repository": {
            "clone_url": clone_url,
            "html_url": clone_url.replace(".git", ""),
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org(db, monkeypatch):
    org = Organization.objects.create(name="Acme", slug="acme-test-pipes")
    # ``Organization.extra_data`` was dropped, taking the dev/test
    # plaintext-secret fallback with it (and the prod path —
    # ``astrolift_lifecycle.services.secrets.read_org_secret`` — is still
    # an unimplemented stub). Patch the resolver seam so these tests
    # exercise the HMAC + dispatch receiver logic (#72) with a known
    # secret; secret *storage* is a separate WIP surface.
    from astrolift_pipelines import webhook_views

    monkeypatch.setattr(
        webhook_views,
        "_get_org_pipeline_secret",
        lambda o: b"test-secret-123" if o.slug == "acme-test-pipes" else None,
    )
    return org


@pytest.fixture
def pipeline(org):
    return Pipeline.objects.create(
        organization=org,
        name="ci",
        repo_url="https://github.com/acme/myapp.git",
        default_branch="main",
        toml_path=".astrolift/pipelines/ci.toml",
    )


@pytest.fixture
def push_trigger(pipeline):
    return Trigger.objects.create(
        pipeline=pipeline,
        kind="push",
        config={"branches": ["main"]},
    )


@pytest.fixture
def factory():
    return RequestFactory()


def _post(factory, org_slug, payload, event="push", secret=b"test-secret-123"):
    body = json.dumps(payload).encode()
    sig = _sign(body, secret)
    return factory.post(
        f"/webhooks/pipelines/github/{org_slug}/",
        data=body,
        content_type="application/json",
        HTTP_X_GITHUB_EVENT=event,
        HTTP_X_GITHUB_DELIVERY=str(uuid.uuid4()),
        HTTP_X_HUB_SIGNATURE_256=sig,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_push_triggers_pipeline_run(factory, org, pipeline, push_trigger):
    payload = _make_push_payload("https://github.com/acme/myapp.git")
    request = _post(factory, org.slug, payload)

    response = pipeline_github_webhook(request, org.slug)

    assert response.status_code == 503
    data = json.loads(response.content)
    assert data["status"] == "retry"
    assert data["dispatched"] == []

    run = PipelineRun.objects.get(pipeline=pipeline)
    assert run.trigger_kind == "push"
    assert run.trigger_ref == "refs/heads/main"
    assert run.trigger_actor == "octocat"
    assert run.run_number == 1


def test_run_number_increments_monotonically(factory, org, pipeline, push_trigger):
    payload = _make_push_payload("https://github.com/acme/myapp.git")
    for _ in range(3):
        request = _post(factory, org.slug, payload)
        pipeline_github_webhook(request, org.slug)

    runs = list(PipelineRun.objects.filter(pipeline=pipeline).order_by("run_number"))
    assert [r.run_number for r in runs] == [1, 2, 3]


def test_no_matching_pipeline_returns_empty_dispatched(factory, org):
    payload = _make_push_payload("https://github.com/acme/other-repo.git")
    request = _post(factory, org.slug, payload)

    response = pipeline_github_webhook(request, org.slug)

    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["status"] == "ok"
    assert data["dispatched"] == []
    assert PipelineRun.objects.count() == 0


def test_hmac_mismatch_returns_401(factory, org, pipeline, push_trigger):
    payload = _make_push_payload("https://github.com/acme/myapp.git")
    body = json.dumps(payload).encode()
    request = factory.post(
        f"/webhooks/pipelines/github/{org.slug}/",
        data=body,
        content_type="application/json",
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_HUB_SIGNATURE_256="sha256=bad",
    )

    response = pipeline_github_webhook(request, org.slug)
    assert response.status_code == 401
    assert PipelineRun.objects.count() == 0


def test_missing_secret_returns_401(factory, db):
    # Org with no webhook secret configured
    org_no_secret = Organization.objects.create(name="B", slug="b-no-secret")
    payload = _make_push_payload("https://github.com/b/app.git")
    request = _post(factory, org_no_secret.slug, payload, secret=b"unused")
    response = pipeline_github_webhook(request, org_no_secret.slug)
    assert response.status_code == 401


def test_unknown_org_slug_returns_404(factory, db):
    body = json.dumps({}).encode()
    request = factory.post(
        "/webhooks/pipelines/github/nonexistent-org/",
        data=body,
        content_type="application/json",
        HTTP_X_GITHUB_EVENT="push",
        HTTP_X_HUB_SIGNATURE_256="sha256=x",
    )
    response = pipeline_github_webhook(request, "nonexistent-org")
    assert response.status_code == 404


def test_unknown_event_returns_200_not_handled(factory, org, pipeline, push_trigger):
    payload = _make_push_payload("https://github.com/acme/myapp.git")
    request = _post(factory, org.slug, payload, event="ping")
    response = pipeline_github_webhook(request, org.slug)
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["status"] == "not handled"


def test_branch_filter_excludes_non_matching_refs(factory, org, pipeline):
    Trigger.objects.create(
        pipeline=pipeline,
        kind="push",
        config={"branches": ["main"]},
    )
    payload = _make_push_payload(
        "https://github.com/acme/myapp.git",
        ref="refs/heads/feature-x",
    )
    request = _post(factory, org.slug, payload)
    response = pipeline_github_webhook(request, org.slug)
    data = json.loads(response.content)
    assert data["dispatched"] == []
    assert PipelineRun.objects.count() == 0


def test_pull_request_event_matches_pr_trigger(factory, org, pipeline):
    Trigger.objects.create(
        pipeline=pipeline,
        kind="pull_request",
        config={},
    )
    payload = _make_pr_payload("https://github.com/acme/myapp.git", branch="fix/bug-42")
    request = _post(factory, org.slug, payload, event="pull_request")
    response = pipeline_github_webhook(request, org.slug)
    data = json.loads(response.content)
    assert response.status_code == 503
    assert data["status"] == "retry"
    assert data["dispatched"] == []
    run = PipelineRun.objects.get(pipeline=pipeline)
    assert run.trigger_kind == "pull_request"
    assert run.trigger_ref == "fix/bug-42"


# ---------------------------------------------------------------------------
# GitLab receiver — token auth boundary (#1060). GitLab doesn't HMAC the
# body; it presents a shared ``X-Gitlab-Token`` we compare in constant time
# against the per-org secret. A wrong / missing token must be rejected
# (403) before any PipelineRun is created.
# ---------------------------------------------------------------------------


@pytest.fixture
def gitlab_org(org, monkeypatch):
    """Reuse the github ``org`` but also patch the gitlab module's secret
    seam (it has its own ``_get_org_pipeline_secret`` copy)."""
    from astrolift_pipelines import gitlab_webhook_views

    monkeypatch.setattr(
        gitlab_webhook_views,
        "_get_org_pipeline_secret",
        lambda o: "gitlab-secret-123" if o.slug == org.slug else None,
    )
    return org


def _gitlab_push_payload(http_url: str = "https://gitlab.com/acme/myapp") -> dict:
    return {
        "object_kind": "push",
        "ref": "refs/heads/main",
        "user_username": "octocat",
        "project": {"git_ssh_url": f"{http_url}.git", "http_url": http_url},
    }


def test_gitlab_valid_token_dispatches(factory, gitlab_org, pipeline, push_trigger):
    pipeline.repo_url = "https://gitlab.com/acme/myapp"
    pipeline.save(update_fields=["repo_url"])
    body = json.dumps(_gitlab_push_payload()).encode()
    request = factory.post(
        f"/webhooks/pipelines/gitlab/{gitlab_org.slug}/",
        data=body,
        content_type="application/json",
        HTTP_X_GITLAB_EVENT="Push Hook",
        HTTP_X_GITLAB_TOKEN="gitlab-secret-123",
    )
    response = pipeline_gitlab_webhook(request, gitlab_org.slug)
    assert response.status_code == 503
    assert PipelineRun.objects.count() == 1
    assert PipelineRun.objects.get().dispatch_status == "uncertain"


def test_gitlab_bad_token_returns_403(factory, gitlab_org, pipeline, push_trigger):
    body = json.dumps(_gitlab_push_payload()).encode()
    request = factory.post(
        f"/webhooks/pipelines/gitlab/{gitlab_org.slug}/",
        data=body,
        content_type="application/json",
        HTTP_X_GITLAB_EVENT="Push Hook",
        HTTP_X_GITLAB_TOKEN="wrong-token",
    )
    response = pipeline_gitlab_webhook(request, gitlab_org.slug)
    assert response.status_code == 403
    assert PipelineRun.objects.count() == 0


def test_gitlab_missing_token_returns_403(factory, gitlab_org, pipeline, push_trigger):
    body = json.dumps(_gitlab_push_payload()).encode()
    request = factory.post(
        f"/webhooks/pipelines/gitlab/{gitlab_org.slug}/",
        data=body,
        content_type="application/json",
        HTTP_X_GITLAB_EVENT="Push Hook",
    )
    response = pipeline_gitlab_webhook(request, gitlab_org.slug)
    assert response.status_code == 403
    assert PipelineRun.objects.count() == 0
