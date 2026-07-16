"""Tests for ``astroliftDeploymentReleaseNotes`` resolver (#738).

The GitHub compare call is stubbed at the ``urlopen`` boundary so the
resolver end-to-end coverage (SHA lookup, connection picker, cache,
None fallbacks) runs against real Postgres rows without hitting the
network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _make_github_compare_response(commits: list[dict]) -> bytes:
    payload = {
        "commits": commits,
        "permalink_url": "https://github.com/acme/demo/compare/abc123...def456",
        "html_url": "https://github.com/acme/demo/compare/abc123...def456",
    }
    return json.dumps(payload).encode("utf-8")


def _fake_urlopen(payload_bytes: bytes):
    class FakeResp:
        def read(self):
            return payload_bytes

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def _open(req, timeout=None):
        return FakeResp()

    return _open


@pytest.fixture
def gh_connection(org):
    secret = encrypt_at_rest(b"fake-pem-key")
    return SourceConnection.objects.create(
        organization=org,
        display_name="gh-pat",
        kind="github_pat",
        account_login="acme",
        is_active=True,
        is_orphaned=False,
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
    )


@pytest.fixture
def app_with_repo(org, project, team):
    return RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Demo",
        slug="demo-app",
        provisioning_status="ready",
        source_kind="github",
        source_repo="acme/demo",
        source_url="https://github.com/acme/demo",
    )


@pytest.fixture
def env(app_with_repo, cluster):
    return AppEnvironment.objects.create(
        registered_app=app_with_repo,
        tenant_cluster=cluster,
        name="prod",
        url="https://demo.example.com",
        required_approvals=0,
    )


@pytest.fixture
def prior_deployment(app_with_repo, env, actor):
    d = Deployment.objects.create(
        registered_app=app_with_repo,
        app_environment=env,
        trigger_kind="push",
        commit_sha="abc123sha",
        status=Deployment.Status.RUNNING,
    )
    d.succeeded_at = d.created_at
    d.save(update_fields=["succeeded_at"])
    return d


@pytest.fixture
def head_deployment(app_with_repo, env, prior_deployment):
    return Deployment.objects.create(
        registered_app=app_with_repo,
        app_environment=env,
        trigger_kind="push",
        commit_sha="def456sha",
        status=Deployment.Status.RUNNING,
    )


@pytest.fixture
def fake_info(actor):
    request = SimpleNamespace(user=actor)
    context = SimpleNamespace(request=request)
    return SimpleNamespace(context=context)


@pytest.fixture
def allow_all(permission_resolver):
    """Grant every permission so resolver decoration doesn't block tests."""
    from core.permissions import Permission

    for p in Permission:
        permission_resolver.grant(p)
    return permission_resolver


def test_returns_release_notes_when_prior_deploy_exists(
    org,
    gh_connection,
    app_with_repo,
    env,
    prior_deployment,
    head_deployment,
    fake_info,
    allow_all,
    monkeypatch,
):
    commits_payload = [
        {
            "sha": "def456sha",
            "commit": {
                "message": "feat: add widget\n\nThis adds the widget.",
                "author": {"name": "Alice"},
            },
            "author": {"login": "alice"},
            "pull_requests": [],
        },
        {
            "sha": "bbb222sha",
            "commit": {
                "message": "Merge pull request #42 from acme/feat-widget",
                "author": {"name": "Alice"},
            },
            "author": {"login": "alice"},
            "pull_requests": [
                {
                    "number": 42,
                    "title": "Add widget",
                    "url": "https://github.com/acme/demo/pull/42",
                }
            ],
        },
    ]
    fake_resp = _make_github_compare_response(commits_payload)
    monkeypatch.setattr(
        "astrolift_scm.providers.release_notes.urllib.request.urlopen",
        _fake_urlopen(fake_resp),
    )

    tc = TenantContext(organization_id=org.pk)
    with tenant_context(tc):
        q = LifecycleQuery()
        result = q.astrolift_deployment_release_notes(fake_info, deployment_id=str(head_deployment.guid))

    assert result is not None
    assert result.base_sha == "abc123sha"
    assert result.head_sha == "def456sha"
    assert len(result.commits) == 2
    assert result.commits[0].subject == "feat: add widget"
    assert result.commits[0].is_merge is False
    assert result.commits[1].is_merge is True
    assert len(result.pull_requests) == 1
    assert result.pull_requests[0].number == 42
    assert result.pull_requests[0].title == "Add widget"
    assert "compare" in result.compare_url


def test_returns_none_when_no_prior_deploy(
    org,
    gh_connection,
    app_with_repo,
    env,
    head_deployment,
    fake_info,
    allow_all,
):
    tc = TenantContext(organization_id=org.pk)
    with tenant_context(tc):
        q = LifecycleQuery()
        result = q.astrolift_deployment_release_notes(fake_info, deployment_id=str(head_deployment.guid))
    assert result is None


def test_returns_none_when_no_commit_sha(
    org,
    gh_connection,
    app_with_repo,
    env,
    fake_info,
    allow_all,
):
    deploy = Deployment.objects.create(
        registered_app=app_with_repo,
        app_environment=env,
        trigger_kind="manual",
        commit_sha="",
        status=Deployment.Status.RUNNING,
    )
    tc = TenantContext(organization_id=org.pk)
    with tenant_context(tc):
        q = LifecycleQuery()
        result = q.astrolift_deployment_release_notes(fake_info, deployment_id=str(deploy.guid))
    assert result is None


def test_returns_none_when_no_source_connection(
    org,
    app_with_repo,
    env,
    prior_deployment,
    head_deployment,
    fake_info,
    allow_all,
):
    tc = TenantContext(organization_id=org.pk)
    with tenant_context(tc):
        q = LifecycleQuery()
        result = q.astrolift_deployment_release_notes(fake_info, deployment_id=str(head_deployment.guid))
    assert result is None


def test_caches_result(
    org,
    gh_connection,
    app_with_repo,
    env,
    prior_deployment,
    head_deployment,
    fake_info,
    allow_all,
    monkeypatch,
):
    call_count = 0
    commits_payload = [
        {
            "sha": "def456sha",
            "commit": {"message": "feat: thing\n", "author": {"name": "Bob"}},
            "author": {"login": "bob"},
            "pull_requests": [],
        }
    ]
    fake_resp = _make_github_compare_response(commits_payload)

    def _counting_urlopen(req, timeout=None):
        nonlocal call_count
        call_count += 1
        return _fake_urlopen(fake_resp)(req, timeout=timeout)

    monkeypatch.setattr(
        "astrolift_scm.providers.release_notes.urllib.request.urlopen",
        _counting_urlopen,
    )
    from django.core.cache import cache as django_cache

    django_cache.clear()

    tc = TenantContext(organization_id=org.pk)
    with tenant_context(tc):
        q = LifecycleQuery()
        q.astrolift_deployment_release_notes(fake_info, deployment_id=str(head_deployment.guid))
        q.astrolift_deployment_release_notes(fake_info, deployment_id=str(head_deployment.guid))

    assert call_count == 1
