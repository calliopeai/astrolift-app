"""Phase 4 tests for ``openCiWorkflowReconcilePr`` + the forced-PR reconcile
path (#1212).

The mutation reuses Phase 1's side-branch PR machinery (``force_pr=True``) to
propose overwriting a drifted repo file with the CURRENT template, so an
operator diffs their hand-edits before merging. Coverage:

* a ``conflict`` / ``repo_drift`` app opens a PR through the REUSED machinery —
  the real ``_github_create_ref`` / ``_github_open_pull_request`` are driven,
  not a second bespoke PR path — and the protection probe is short-circuited
  (the PR is opened because it was forced, not because the branch is protected);
* idempotency: re-calling refreshes the same deterministic side-branch PR
  instead of opening a duplicate (the GitHub helper's 422 "already exists" is
  covered directly);
* precondition-fails (never 500) for ``in_sync`` / ``absent`` / no-source-repo;
* ``app.update`` gating + tenant scoping;
* Bitbucket (no side-branch PR flow) fails cleanly rather than direct-writing.
"""

from __future__ import annotations

import io
import urllib.error
from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.ci_templates import TEMPLATE_VERSION, content_hash
from astrolift_scm.models import SourceConnection
from astrolift_scm.schema.mutations import CiWorkflowSyncActionInput, ScmMutation
from astrolift_scm.services import workflow_sync
from astrolift_scm.services.workflow_sync import (
    _github_open_pull_request,
    render_astrolift_bitbucket_pipeline,
    render_astrolift_ci_workflow,
)
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _put_result(sha="side-commit"):
    return SimpleNamespace(
        commit_sha=sha, file_path=".github/workflows/astrolift-ci.yml", web_url="https://x"
    )


def _scaffold(org, *, source_kind: str = "github", source_repo: str = "acme/api", slug: str = "hello-app"):
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{slug}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{slug}")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug=slug,
        source_kind=source_kind,
        source_repo=source_repo,
        default_branch="main",
        deploy_branch="main",
        manifest_path="astrolift.toml",
        k8s_namespace=f"acme-{slug}",
        subdomain=slug,
        registry_repo_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/hello-app",
        push_role_ref="arn:aws:iam::123456789012:role/astrolift-push-hello-app",
    )


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


def _seed(app, *, synced_hash: str, version: int, state: str = "repo_drift") -> None:
    """Give ``app`` a Phase-1 baseline so the drift recompute lands on a
    determinate state."""
    app.ci_workflow_template_version = version
    app.ci_workflow_state = {
        "synced_hash": synced_hash,
        "synced_at": "2026-01-01T00:00:00+00:00",
        "path": ".github/workflows/astrolift-ci.yml",
        "state": state,
    }
    app.save(update_fields=["ci_workflow_template_version", "ci_workflow_state", "updated_at", "version"])


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-reconcile")


def _stub_github_pr_path(monkeypatch, *, repo_body: str, pr_url: str):
    """Patch the reconcile network surface: the drift-recompute fetch, the
    sync's existing-file fetch, and every github PR helper. Returns a record of
    the reused-machinery calls so tests can assert the PR path ran."""
    rec = {"create_ref": [], "put_branch": [], "open_pr": [], "protection_probe": 0}

    # Drift recompute (fetch_repo_ci_workflow -> providers.fetch_file) and the
    # sync's own existing-file check both see the drifted body.
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: repo_body)
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: repo_body)

    def _protect(*a, **kw):
        rec["protection_probe"] += 1
        return False

    monkeypatch.setattr("astrolift_scm.services.workflow_sync._is_github_branch_protected", _protect)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._github_get_branch_sha", lambda *a, **kw: "headsha"
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._github_create_ref",
        lambda *a, **kw: rec["create_ref"].append(kw.get("new_branch")),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: rec["put_branch"].append(kw.get("branch")) or _put_result(),
    )

    def _open_pr(*a, **kw):
        rec["open_pr"].append((kw.get("head"), kw.get("base_branch")))
        return pr_url

    monkeypatch.setattr("astrolift_scm.services.workflow_sync._github_open_pull_request", _open_pr)
    return rec


# ---------------------------------------------------------------------------
# Happy path: conflict / repo_drift -> reviewable PR via the reused machinery
# ---------------------------------------------------------------------------


def test_reconcile_opens_pr_for_conflict(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _github_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    rendered = render_astrolift_ci_workflow(app)
    drifted = rendered.replace("Astrolift", "Astrolift HAND-EDIT", 1)
    # Baseline synced at the CURRENT hash but an OLDER version -> repo edited
    # AND template advanced -> CONFLICT.
    _seed(app, synced_hash=content_hash(rendered), version=TEMPLATE_VERSION - 1, state="conflict")
    rec = _stub_github_pr_path(monkeypatch, repo_body=drifted, pr_url="https://github.com/acme/api/pull/7")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert result.ok, result.errors
    # The reused PR machinery ran: a side branch was created + committed and a
    # PR was opened back into the deploy branch.
    assert rec["create_ref"] == [f"astrolift/ci-workflow-{app.slug}"]
    assert rec["put_branch"] == [f"astrolift/ci-workflow-{app.slug}"]
    assert rec["open_pr"] == [(f"astrolift/ci-workflow-{app.slug}", "main")]
    # PR opened because it was FORCED, not because the branch is protected.
    assert rec["protection_probe"] == 0
    # The review link is persisted onto the sync record and returned.
    assert result.data.pr_url == "https://github.com/acme/api/pull/7"
    app.refresh_from_db()
    assert app.ci_workflow_state["pr_url"] == "https://github.com/acme/api/pull/7"


def test_reconcile_opens_pr_for_repo_drift(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _github_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    rendered = render_astrolift_ci_workflow(app)
    drifted = rendered.replace("Astrolift", "Astrolift HAND-EDIT", 1)
    # Baseline synced at the current hash AND version -> only the repo moved.
    _seed(app, synced_hash=content_hash(rendered), version=TEMPLATE_VERSION, state="repo_drift")
    rec = _stub_github_pr_path(monkeypatch, repo_body=drifted, pr_url="https://github.com/acme/api/pull/9")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert result.ok, result.errors
    assert rec["open_pr"] == [(f"astrolift/ci-workflow-{app.slug}", "main")]
    assert result.data.pr_url == "https://github.com/acme/api/pull/9"


def test_reconcile_is_idempotent_no_duplicate_pr(monkeypatch, org, permission_resolver):
    """Re-calling drives the SAME deterministic side branch + PR — the machinery
    treats a re-open as the existing PR, never a duplicate."""
    app = _scaffold(org)
    _github_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    rendered = render_astrolift_ci_workflow(app)
    drifted = rendered.replace("Astrolift", "Astrolift HAND-EDIT", 1)
    _seed(app, synced_hash=content_hash(rendered), version=TEMPLATE_VERSION, state="repo_drift")
    rec = _stub_github_pr_path(monkeypatch, repo_body=drifted, pr_url="https://github.com/acme/api/pull/7")

    with tenant_context(TenantContext(organization_id=org.id)):
        first = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )
        second = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert first.ok and second.ok, (first.errors, second.errors)
    assert first.data.pr_url == second.data.pr_url == "https://github.com/acme/api/pull/7"
    # Both calls target the identical deterministic side branch -> the host
    # dedupes to one PR; no second branch/PR is invented.
    assert rec["create_ref"] == [f"astrolift/ci-workflow-{app.slug}"] * 2
    assert rec["open_pr"] == [(f"astrolift/ci-workflow-{app.slug}", "main")] * 2


def test_github_open_pr_helper_is_idempotent_on_already_exists(monkeypatch, org):
    """The reused ``_github_open_pull_request`` returns the existing PR's URL on
    a 422 'already exists' rather than erroring or duplicating — the guarantee
    the mutation's idempotency rests on."""
    conn = _github_conn(org)

    def fake_urlopen(req, timeout=10):
        if req.get_method() == "POST":
            raise urllib.error.HTTPError(
                req.full_url,
                422,
                "Unprocessable Entity",
                {},
                io.BytesIO(b'{"message":"A pull request already exists for acme:branch."}'),
            )
        # GET find-existing -> the open PR.
        return _FakeResp(b'[{"html_url":"https://github.com/acme/api/pull/5"}]')

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.urllib.request.urlopen", fake_urlopen)

    url = _github_open_pull_request(
        conn,
        repo_full_name="acme/api",
        head=f"astrolift/ci-workflow-{'x'}",
        base_branch="main",
        title="Astrolift: reconcile",
        body="body",
    )
    assert url == "https://github.com/acme/api/pull/5"


class _FakeResp:
    def __init__(self, payload: bytes):
        self._p = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._p


# ---------------------------------------------------------------------------
# Preconditions: nothing to reconcile / no repo -> clean field error, never 500
# ---------------------------------------------------------------------------


def _boom_pr(monkeypatch):
    """Make every write/PR helper explode so a precondition-reject test proves
    the reject happens BEFORE any push, not after a stray write."""

    def _boom(*a, **kw):
        raise AssertionError("must not push/PR when there's nothing to reconcile")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync._github_open_pull_request", _boom)
    monkeypatch.setattr("astrolift_scm.services.workflow_sync._github_create_ref", _boom)
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.put_file", _boom)


def test_reconcile_rejects_in_sync(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _github_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    rendered = render_astrolift_ci_workflow(app)
    _seed(app, synced_hash=content_hash(rendered), version=TEMPLATE_VERSION, state="in_sync")
    # Repo matches the template exactly -> IN_SYNC, nothing to reconcile.
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: rendered)
    _boom_pr(monkeypatch)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "in_sync" in result.errors[0].message


def test_reconcile_rejects_absent(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _github_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    rendered = render_astrolift_ci_workflow(app)
    _seed(app, synced_hash=content_hash(rendered), version=TEMPLATE_VERSION, state="in_sync")
    # No file on the branch tip -> ABSENT.
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: None)
    _boom_pr(monkeypatch)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "absent" in result.errors[0].message


def test_reconcile_rejects_no_source_repo(monkeypatch, org, permission_resolver):
    app = _scaffold(org, source_repo="")
    _github_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)
    _boom_pr(monkeypatch)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert result.errors[0].field == "appId"
    assert "NO_SOURCE_REPO" in result.errors[0].message


def test_reconcile_app_not_found(monkeypatch, org, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    _boom_pr(monkeypatch)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(),
            input=CiWorkflowSyncActionInput(app_id="00000000-0000-0000-0000-000000000000"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Gating: permission + tenant scope
# ---------------------------------------------------------------------------


def test_reconcile_requires_permission(monkeypatch, org):
    """No APP_UPDATE grant -> the @mutation_audit wrapper converts the
    PermissionDenied into a PERMISSION_DENIED envelope, and nothing is pushed."""
    app = _scaffold(org)
    _github_conn(org)
    _boom_pr(monkeypatch)
    # Also guard the recompute fetch — the gate must fire before any network.
    monkeypatch.setattr(
        "astrolift_scm.providers.fetch_file",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("permission gate must fire first")),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_reconcile_is_tenant_scoped(monkeypatch, permission_resolver):
    """A caller in org A can't reconcile org B's app — the org-scoped lookup
    returns NOT_FOUND and no PR is opened, even with the permission granted."""
    org_a = Organization.objects.create(name="A", slug="org-a-reconcile")
    org_b = Organization.objects.create(name="B", slug="org-b-reconcile")
    app_b = _scaffold(org_b, slug="victim-app")
    _github_conn(org_b)
    permission_resolver.grant(Permission.APP_UPDATE)
    _boom_pr(monkeypatch)
    monkeypatch.setattr(
        "astrolift_scm.providers.fetch_file",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("cross-org must not fetch")),
    )

    with tenant_context(TenantContext(organization_id=org_a.id)):
        result = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app_b.guid))
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Bitbucket: no side-branch PR flow -> clean refusal, no direct-write
# ---------------------------------------------------------------------------


def test_reconcile_bitbucket_unsupported(monkeypatch, org, permission_resolver):
    app = _scaffold(org, source_kind="bitbucket")
    _bb_conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)

    rendered = render_astrolift_bitbucket_pipeline(app)
    drifted = rendered.replace("Astrolift", "Astrolift HAND-EDIT", 1)
    _seed(app, synced_hash=content_hash(rendered), version=TEMPLATE_VERSION, state="repo_drift")
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: drifted)
    # A direct write would clobber the operator's edits — must never happen.
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("must not direct-write on bitbucket reconcile")
        ),
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().open_ci_workflow_reconcile_pr(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "bitbucket" in result.errors[0].message.lower()


def test_sync_force_pr_flag_routes_github_via_pr(monkeypatch, org):
    """Unit-level: force_pr routes an UNPROTECTED github branch through the PR
    flow (the flag the mutation relies on), short-circuiting the probe."""
    app = _scaffold(org)
    _github_conn(org)
    rendered = render_astrolift_ci_workflow(app)
    drifted = rendered.replace("Astrolift", "Astrolift HAND-EDIT", 1)
    rec = _stub_github_pr_path(monkeypatch, repo_body=drifted, pr_url="https://github.com/acme/api/pull/3")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = workflow_sync.sync_workflow_file_to_repo(app, force_pr=True)

    assert result.status == "pr_opened"
    assert result.pr_url == "https://github.com/acme/api/pull/3"
    assert rec["protection_probe"] == 0
