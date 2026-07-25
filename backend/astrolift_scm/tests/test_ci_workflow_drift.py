"""Phase 2 drift tests for the managed CI workflow versioned sync (#1210).

Three concerns, kept separate:

* The PURE :func:`compute_sync_state` truth-table across all six states —
  no network, no ORM, just the classification logic.
* The provider rate-limit split: a GitHub 403 that is a rate limit must
  surface as ``RATE_LIMITED``, never ``AUTH_FAILED``.
* The network-facing layer against real Postgres rows with the wire
  stubbed at the ``providers``/``workflow_sync`` boundary (same shape as
  ``test_ci_workflow_sync_persistence``): the observe-only webhook
  reconcile flags state and never writes to the repo, and the three
  manual mutations resync / adopt / refresh behave.
"""

from __future__ import annotations

import io
import urllib.error
from email.message import Message
from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.ci_templates import (
    TEMPLATE_VERSION,
    content_hash,
    git_blob_sha,
    stamp_workflow,
)
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError
from astrolift_scm.providers.github import GithubProviderError, fetch_github_file
from astrolift_scm.schema.mutations import CiWorkflowSyncActionInput, ScmMutation
from astrolift_scm.services.ci_workflow_drift import (
    CiWorkflowFetchError,
    SyncState,
    compute_sync_state,
    fetch_repo_ci_workflow,
    reconcile_ci_workflow_on_push,
)
from astrolift_scm.services.workflow_sync import render_astrolift_bitbucket_pipeline
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# PURE truth-table: compute_sync_state across all six states
# ---------------------------------------------------------------------------


_BASE_BODY = "# Managed by Astrolift — do not edit by hand.\nname: Astrolift CI\njobs: {}\n"
_EDITED_BODY = "# Managed by Astrolift — do not edit by hand.\nname: Astrolift CI\njobs: {edited: true}\n"


def _stamped(body: str, *, version: int = TEMPLATE_VERSION) -> str:
    """Stamp a body the way the renderers do — stamp line inserted after the
    header, digest = content_hash of the (stamp-stripped) body."""
    return stamp_workflow(body, version=version, digest=content_hash(body))


def _baseline(*, version: int | None = TEMPLATE_VERSION) -> dict:
    """A persisted sync record whose baseline hash matches ``_BASE_BODY``."""
    return {"synced_hash": content_hash(_BASE_BODY), "synced_template_version": version}


def test_state_in_sync():
    """Repo matches what we synced and the template hasn't moved on."""
    state = compute_sync_state(
        repo_file_text=_stamped(_BASE_BODY),
        persisted_state=_baseline(),
        current_template_version=TEMPLATE_VERSION,
    )
    assert state is SyncState.IN_SYNC


def test_state_template_stale_via_synced_version():
    """Repo untouched, but our synced version is behind the current template."""
    state = compute_sync_state(
        repo_file_text=_stamped(_BASE_BODY),
        persisted_state=_baseline(version=TEMPLATE_VERSION),
        current_template_version=TEMPLATE_VERSION + 1,
    )
    assert state is SyncState.TEMPLATE_STALE


def test_state_template_stale_via_repo_stamp_version():
    """Repo body untouched (hash matches) but its own stamp is older than the
    current template — the ``repo_version < current`` disjunct."""
    state = compute_sync_state(
        repo_file_text=_stamped(_BASE_BODY, version=TEMPLATE_VERSION - 1),
        persisted_state=_baseline(version=TEMPLATE_VERSION),
        current_template_version=TEMPLATE_VERSION,
    )
    assert state is SyncState.TEMPLATE_STALE


def test_state_repo_drift():
    """Repo body edited under a still-current template — pure repo-side drift."""
    state = compute_sync_state(
        repo_file_text=_stamped(_EDITED_BODY),
        persisted_state=_baseline(version=TEMPLATE_VERSION),
        current_template_version=TEMPLATE_VERSION,
    )
    assert state is SyncState.REPO_DRIFT


def test_state_conflict():
    """Repo body edited AND the template advanced — both sides moved."""
    state = compute_sync_state(
        repo_file_text=_stamped(_EDITED_BODY),
        persisted_state=_baseline(version=TEMPLATE_VERSION),
        current_template_version=TEMPLATE_VERSION + 1,
    )
    assert state is SyncState.CONFLICT


def test_state_absent():
    """No managed file on the branch tip."""
    state = compute_sync_state(
        repo_file_text=None,
        persisted_state=_baseline(),
        current_template_version=TEMPLATE_VERSION,
    )
    assert state is SyncState.ABSENT


def test_state_unknown_on_unusable_current_version():
    """Defensive: no usable current template version → UNKNOWN, not a guess."""
    state = compute_sync_state(
        repo_file_text=_stamped(_BASE_BODY),
        persisted_state=_baseline(),
        current_template_version=0,
    )
    assert state is SyncState.UNKNOWN


def test_state_no_baseline_but_file_present_is_repo_drift():
    """Never versioned-synced, yet a file exists at the managed path —
    someone else's file → REPO_DRIFT (adopt/resync resolves it)."""
    state = compute_sync_state(
        repo_file_text=_stamped(_BASE_BODY),
        persisted_state={},
        current_template_version=TEMPLATE_VERSION,
    )
    assert state is SyncState.REPO_DRIFT


def test_state_unstamped_foreign_file_is_repo_drift():
    """An unstamped file that doesn't match our baseline is repo drift."""
    state = compute_sync_state(
        repo_file_text="name: someone-elses-ci\n",
        persisted_state=_baseline(),
        current_template_version=TEMPLATE_VERSION,
    )
    assert state is SyncState.REPO_DRIFT


# ---------------------------------------------------------------------------
# Provider rate-limit split: 403 rate-limit → RATE_LIMITED (not AUTH_FAILED)
# ---------------------------------------------------------------------------


def _github_conn(org) -> SourceConnection:
    encrypted = encrypt_at_rest(b"gho_token_never_hits_the_network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: alice",
        account_login="alice",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _http_error(code: int, headers: Message | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.github.com", code, "err", headers or Message(), io.BytesIO(b"")
    )


def _rate_limit_headers() -> Message:
    h = Message()
    h["X-RateLimit-Remaining"] = "0"
    h["X-RateLimit-Limit"] = "5000"
    return h


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-drift")


def test_github_fetch_rate_limited_403_maps_to_rate_limited(monkeypatch, org):
    """A 403 with ``X-RateLimit-Remaining: 0`` is a rate limit, not auth."""
    conn = _github_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(403, _rate_limit_headers())),
    )

    with pytest.raises(GithubProviderError) as exc:
        fetch_github_file(conn, repo_full_name="acme/api", path=".github/x.yml", ref="main")
    assert exc.value.code == "RATE_LIMITED"


def test_github_fetch_plain_403_still_maps_to_auth_failed(monkeypatch, org):
    """A 403 WITHOUT rate-limit headers is a permission denial → AUTH_FAILED."""
    conn = _github_conn(org)

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(403)),
    )

    with pytest.raises(GithubProviderError) as exc:
        fetch_github_file(conn, repo_full_name="acme/api", path=".github/x.yml", ref="main")
    assert exc.value.code == "AUTH_FAILED"


def test_github_fetch_secondary_rate_limit_via_retry_after(monkeypatch, org):
    """A 429/403 with ``Retry-After`` (secondary limit) is also RATE_LIMITED."""
    conn = _github_conn(org)
    h = Message()
    h["Retry-After"] = "60"

    monkeypatch.setattr(
        "astrolift_scm.providers.github.urllib.request.urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(_http_error(429, h)),
    )

    with pytest.raises(GithubProviderError) as exc:
        fetch_github_file(conn, repo_full_name="acme/api", path=".github/x.yml", ref="main")
    assert exc.value.code == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Scaffolding for the ORM-backed network-layer tests
# ---------------------------------------------------------------------------


def _scaffold(org, *, source_kind: str = "bitbucket", source_repo: str = "acme/api"):
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
    return app


def _bb_conn(org) -> SourceConnection:
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


def _seed_baseline(app) -> str:
    """Give ``app`` a Phase-1 baseline synced to the current template. Returns
    the rendered (stamped) body that constitutes 'in sync'."""
    body = render_astrolift_bitbucket_pipeline(app)
    app.ci_workflow_template_version = TEMPLATE_VERSION
    app.ci_workflow_state = {
        "synced_hash": content_hash(body),
        "synced_blob_sha": git_blob_sha(body.encode("utf-8")),
        "synced_at": "2026-01-01T00:00:00+00:00",
        "path": "bitbucket-pipelines.yml",
        "state": "in_sync",
    }
    app.save(update_fields=["ci_workflow_template_version", "ci_workflow_state", "updated_at", "version"])
    return body


# ---------------------------------------------------------------------------
# fetch_repo_ci_workflow reuses the provider plumbing + preserves RATE_LIMITED
# ---------------------------------------------------------------------------


def test_fetch_repo_ci_workflow_maps_rate_limit(monkeypatch, org):
    app = _scaffold(org)
    _bb_conn(org)

    def _raise(*a, **kw):
        raise ProviderError("RATE_LIMITED", "slow down", recoverable=True)

    monkeypatch.setattr("astrolift_scm.providers.fetch_file", _raise)

    with pytest.raises(CiWorkflowFetchError) as exc:
        fetch_repo_ci_workflow(app)
    assert exc.value.code == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Observe-only webhook reconcile: flags state, NEVER writes to the repo
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_repo_writes(monkeypatch):
    """Any repo write from the reconcile path is a bug — make put/PR explode
    so a stray write fails loudly instead of silently mutating a repo."""

    def _boom(*a, **kw):
        raise AssertionError("observe-only reconcile must never write to the repo")

    monkeypatch.setattr("astrolift_scm.providers.put_file", _boom)
    monkeypatch.setattr("astrolift_scm.providers.open_pull_request", _boom)


def test_reconcile_on_deploy_branch_flags_repo_drift(monkeypatch, org):
    app = _scaffold(org)
    _bb_conn(org)
    _seed_baseline(app)

    # Repo now carries an edited body → REPO_DRIFT.
    edited = render_astrolift_bitbucket_pipeline(app).replace("Astrolift", "Astrolift EDITED", 1)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: edited)

    reconcile_ci_workflow_on_push(app, branch="main", pushed_sha="deadbeef")

    app.refresh_from_db()
    assert app.ci_workflow_state["state"] == "repo_drift"
    assert app.ci_workflow_state["checked_at"]  # stamped a check time
    # Baseline digests untouched — reconcile only flags, never re-baselines.
    assert app.ci_workflow_state["synced_hash"] == content_hash(render_astrolift_bitbucket_pipeline(app))


def test_reconcile_on_deploy_branch_flags_in_sync(monkeypatch, org):
    app = _scaffold(org)
    _bb_conn(org)
    body = _seed_baseline(app)

    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: body)

    reconcile_ci_workflow_on_push(app, branch="main", pushed_sha="cafe")

    app.refresh_from_db()
    assert app.ci_workflow_state["state"] == "in_sync"


def test_reconcile_ignores_non_deploy_branch(monkeypatch, org):
    app = _scaffold(org)
    _bb_conn(org)
    _seed_baseline(app)

    def _boom_fetch(*a, **kw):
        raise AssertionError("non-deploy-branch push must not fetch")

    monkeypatch.setattr("astrolift_scm.providers.fetch_file", _boom_fetch)

    reconcile_ci_workflow_on_push(app, branch="feature/x", pushed_sha="abc")

    app.refresh_from_db()
    # State left exactly as seeded (in_sync), no checked_at written.
    assert app.ci_workflow_state["state"] == "in_sync"
    assert "checked_at" not in app.ci_workflow_state


def test_reconcile_leaves_prior_state_on_fetch_error(monkeypatch, org):
    app = _scaffold(org)
    _bb_conn(org)
    _seed_baseline(app)

    def _raise(*a, **kw):
        raise ProviderError("NETWORK", "down", recoverable=True)

    monkeypatch.setattr("astrolift_scm.providers.fetch_file", _raise)

    # Best-effort: must not raise, and must leave prior state intact.
    reconcile_ci_workflow_on_push(app, branch="main", pushed_sha="abc")

    app.refresh_from_db()
    assert app.ci_workflow_state["state"] == "in_sync"
    assert "checked_at" not in app.ci_workflow_state


def test_reconcile_swallows_unexpected_read_error(monkeypatch, org):
    """A raw non-ProviderError on the read path (e.g. a credential that won't
    decrypt) must be swallowed too — flag-only must never disturb the ack."""
    app = _scaffold(org)
    _bb_conn(org)
    _seed_baseline(app)

    def _raise(*a, **kw):
        raise ValueError("undecryptable secret")

    monkeypatch.setattr("astrolift_scm.providers.fetch_file", _raise)

    # Must not raise out; prior state intact.
    reconcile_ci_workflow_on_push(app, branch="main", pushed_sha="abc")

    app.refresh_from_db()
    assert app.ci_workflow_state["state"] == "in_sync"
    assert "checked_at" not in app.ci_workflow_state


# ---------------------------------------------------------------------------
# Mutations: resync (pushes), adopt (re-baselines, no push), refresh (recompute)
# ---------------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _put_result(sha="commit-sha"):
    return SimpleNamespace(commit_sha=sha, file_path="bitbucket-pipelines.yml", web_url="https://x")


def test_resync_mutation_pushes_and_reports_in_sync(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _bb_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    # File missing on the repo → created path. Track the push.
    put_calls: list = []
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: put_calls.append(1) or _put_result("bb-sha"),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().resync_astrolift_ci_workflow(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert result.ok, result.errors
    assert put_calls, "resync must push the rendered template"
    assert result.data.state == "in_sync"
    assert result.data.synced_template_version == TEMPLATE_VERSION
    assert result.data.current_template_version == TEMPLATE_VERSION
    assert result.data.path == "bitbucket-pipelines.yml"


def test_resync_mutation_requires_permission(monkeypatch, org):
    app = _scaffold(org)
    _bb_conn(org)

    def _boom(*a, **kw):
        raise AssertionError("permission gate must fire before any push")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.put_file", _boom)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().resync_astrolift_ci_workflow(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_adopt_mutation_rebaselines_without_pushing(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _bb_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_baseline(app)

    # The repo has drifted to a foreign body; adopt should accept THAT as the
    # new baseline (and must not push — the autouse _no_repo_writes guards it).
    foreign = "# hand-authored\nname: their own CI\njobs: {a: 1}\n"
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: foreign)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().adopt_repo_ci_workflow(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert result.ok, result.errors
    assert result.data.state == "in_sync"

    app.refresh_from_db()
    # Baseline now tracks the repo's foreign file, not the rendered template.
    assert app.ci_workflow_state["synced_hash"] == content_hash(foreign)
    assert app.ci_workflow_state["synced_blob_sha"] == git_blob_sha(foreign.encode("utf-8"))
    # Unstamped adopted file is declared current so it reads in_sync, not stale.
    assert app.ci_workflow_template_version == TEMPLATE_VERSION


def test_adopt_mutation_absent_file_is_precondition(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _bb_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    # No file in the repo to adopt.
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: None)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().adopt_repo_ci_workflow(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_refresh_mutation_recomputes_repo_drift(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _bb_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_baseline(app)

    edited = render_astrolift_bitbucket_pipeline(app).replace("Astrolift", "Astrolift EDITED", 1)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: edited)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().refresh_ci_workflow_sync_status(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert result.ok, result.errors
    assert result.data.state == "repo_drift"
    assert result.data.checked_at is not None


def test_refresh_mutation_rate_limit_is_unknown_not_auth_failed(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _bb_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_baseline(app)

    def _raise(*a, **kw):
        raise ProviderError("RATE_LIMITED", "slow down", recoverable=True)

    monkeypatch.setattr("astrolift_scm.providers.fetch_file", _raise)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().refresh_ci_workflow_sync_status(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    # A rate limit must NOT poison the drift into a hard auth failure — it's a
    # clean, transient 'unknown' the operator can retry.
    assert result.ok, result.errors
    assert result.data.state == "unknown"
    app.refresh_from_db()
    assert app.ci_workflow_state["state"] == "unknown"


def test_refresh_mutation_app_not_found(monkeypatch, org, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().refresh_ci_workflow_sync_status(
            _info(),
            input=CiWorkflowSyncActionInput(app_id="00000000-0000-0000-0000-000000000000"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
