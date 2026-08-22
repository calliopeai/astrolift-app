"""The managed CI workflow gets a real inbound direction, and an outbound
one that stops overwriting hand edits.

What was wrong. Every action on this file wrote outward: the manual
``resyncAstroliftCiWorkflow`` rendered the template and put it, and the
fleet sweep did the same. The one "from repo" action, ``adoptRepoCiWorkflow``,
re-pointed the sync record's digests at the repo file and then discarded
its text -- so an operator's own workflow was never anywhere the platform
could show it, and ``repo_drift`` was a badge you could only act on by
overwriting a file you could not read.

Worse, resync direct-writes when the deploy branch is unprotected. Pressing
it on a drifted app clobbered the operator's edit in place. The safe path
(a side-branch PR) existed but was a separate, less prominent button.

So: ``pullCiWorkflowFromRepo`` stores the repo's text, and
``sync_workflow_file_to_repo`` -- the one service every push entry point
delegates to -- classifies before it writes and routes a hand-edited file
through the PR machinery instead of over it.

The push tests assert on WHICH route the write took, not merely that a
write happened: both routes end in a put, so only the route tells the safe
one from the destructive one.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import build_ci_workflow_sync_status
from astrolift_scm.ci_templates import TEMPLATE_VERSION, content_hash, git_blob_sha
from astrolift_scm.models import SourceConnection
from astrolift_scm.schema.mutations import CiWorkflowSyncActionInput, ScmMutation
from astrolift_scm.services.ci_workflow_drift import (
    MAX_STORED_REPO_TEXT_BYTES,
    CiWorkflowAdoptError,
    pull_repo_ci_workflow,
)
from astrolift_scm.services.workflow_sync import render_astrolift_bitbucket_pipeline
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_FOREIGN = "# hand-authored by the operator\nname: their own CI\njobs: {a: 1}\n"


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-ci-pull")


def _scaffold(org):
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        source_kind="bitbucket",
        source_repo="acme/api",
        default_branch="main",
        deploy_branch="main",
        manifest_path="astrolift.toml",
        k8s_namespace="acme-hello-app",
        subdomain="hello-app",
        registry_repo_uri="123456.dkr.ecr.us-east-1.amazonaws.com/hello-app",
    )


def _conn(org) -> SourceConnection:
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
    """Baseline the app against the current template, i.e. ``in_sync``."""
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


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture(autouse=True)
def _no_stray_writes(monkeypatch):
    """A pull must never write to a repo. Make the raw provider writes explode
    so a stray one fails loudly; the push tests stub the layer above this."""

    def _boom(*a, **kw):
        raise AssertionError("a pull must never write to the repo")

    monkeypatch.setattr("astrolift_scm.providers.put_file", _boom)
    monkeypatch.setattr("astrolift_scm.providers.open_pull_request", _boom)


# ---------------------------------------------------------------------------
# The pull: the repo's text ends up somewhere the platform can show it
# ---------------------------------------------------------------------------


def test_pull_stores_the_repo_text(monkeypatch, org):
    """The gap this closes. Adopt re-baselined and threw the file away."""
    app = _scaffold(org)
    _conn(org)
    _seed_baseline(app)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: _FOREIGN)

    pull_repo_ci_workflow(app)

    app.refresh_from_db()
    assert app.ci_workflow_state["repo_text"] == _FOREIGN
    assert app.ci_workflow_state["repo_text_bytes"] == len(_FOREIGN.encode("utf-8"))
    assert app.ci_workflow_state["repo_text_ref"] == "main"
    assert app.ci_workflow_state["repo_text_pulled_at"]


def test_pull_rebaselines_onto_the_repo_copy(monkeypatch, org):
    app = _scaffold(org)
    _conn(org)
    _seed_baseline(app)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: _FOREIGN)

    pull_repo_ci_workflow(app)

    app.refresh_from_db()
    assert app.ci_workflow_state["synced_hash"] == content_hash(_FOREIGN)
    assert app.ci_workflow_state["synced_blob_sha"] == git_blob_sha(_FOREIGN.encode("utf-8"))
    assert app.ci_workflow_state["state"] == "in_sync"


def test_pull_refuses_a_file_over_the_cap_rather_than_truncating(monkeypatch, org):
    """A half-file presented as "what your repo has" is worse than an error."""
    app = _scaffold(org)
    _conn(org)
    huge = "x" * (MAX_STORED_REPO_TEXT_BYTES + 1)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: huge)

    with pytest.raises(CiWorkflowAdoptError) as exc:
        pull_repo_ci_workflow(app)

    assert exc.value.code == "TOO_LARGE"
    app.refresh_from_db()
    assert "repo_text" not in (app.ci_workflow_state or {})


def test_pull_with_no_file_in_the_repo_is_a_precondition(monkeypatch, org):
    app = _scaffold(org)
    _conn(org)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: None)

    with pytest.raises(CiWorkflowAdoptError) as exc:
        pull_repo_ci_workflow(app)

    assert exc.value.code == "ABSENT"


def test_adopt_clears_a_previously_pulled_text(monkeypatch, org):
    """Adopt keeps no copy, so it must not leave a stale one behind claiming
    to be what the repo has."""
    app = _scaffold(org)
    _conn(org)
    _seed_baseline(app)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: _FOREIGN)
    pull_repo_ci_workflow(app)
    app.refresh_from_db()
    assert app.ci_workflow_state["repo_text"]

    from astrolift_scm.services.ci_workflow_drift import adopt_repo_ci_workflow

    other = "# a different hand-authored file\njobs: {b: 2}\n"
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: other)
    adopt_repo_ci_workflow(app)

    app.refresh_from_db()
    assert "repo_text" not in app.ci_workflow_state
    assert app.ci_workflow_state["synced_hash"] == content_hash(other)


def test_the_status_rollup_exposes_the_repo_copy_and_the_render(monkeypatch, org):
    """Storing the text is only worth anything if something reads it: the
    status rollup is what hands the UI both sides of the diff."""
    app = _scaffold(org)
    _conn(org)
    _seed_baseline(app)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: _FOREIGN)
    pull_repo_ci_workflow(app)
    app.refresh_from_db()

    status = build_ci_workflow_sync_status(app)

    assert status.repo_text == _FOREIGN
    assert status.rendered_text == render_astrolift_bitbucket_pipeline(app)
    assert status.rendered_text != status.repo_text
    assert status.repo_text_pulled_at is not None


def test_the_status_rollup_is_empty_strings_before_any_pull(org):
    app = _scaffold(org)
    _seed_baseline(app)

    status = build_ci_workflow_sync_status(app)

    assert status.repo_text == ""
    assert status.repo_text_pulled_at is None
    # The render is available without a pull -- it is a pure template render.
    assert status.rendered_text


def test_pull_mutation_reports_in_sync(monkeypatch, org, permission_resolver):
    app = _scaffold(org)
    _conn(org)
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_baseline(app)
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda *a, **kw: _FOREIGN)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().pull_ci_workflow_from_repo(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert result.ok, result.errors
    assert result.data.state == "in_sync"
    assert result.data.repo_text == _FOREIGN


def test_pull_mutation_requires_permission(monkeypatch, org):
    app = _scaffold(org)
    _conn(org)

    def _boom(*a, **kw):
        raise AssertionError("the permission gate must fire before any repo read")

    monkeypatch.setattr("astrolift_scm.providers.fetch_file", _boom)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmMutation().pull_ci_workflow_from_repo(
            _info(), input=CiWorkflowSyncActionInput(app_id=str(app.guid))
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# The push: a hand-edited file is reviewed, not overwritten
#
# The guard sits in ``sync_workflow_file_to_repo`` -- the one service every
# push entry point delegates to (``pushAstroliftCiWorkflowToRepo``,
# ``pushCiWorkflow``, ``resyncAstroliftCiWorkflow``, the fleet sweep) -- so
# fixing it there fixes all four rather than adding a fifth, safer field
# beside them. These go through the resync mutation, which is the one the
# UI button used to call straight into the destructive path.
#
# They assert on WHICH route was taken, not that a push happened: both
# routes end in a write, so only the route tells them apart.
# ---------------------------------------------------------------------------


def _github_app(org):
    app = _scaffold(org)
    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.save(update_fields=["source_kind", "source_repo", "updated_at", "version"])
    return app


def _github_conn(org) -> SourceConnection:
    encrypted = encrypt_at_rest(b"gh-token-never-hits-network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: acme",
        account_login="acme",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _seed_github_baseline(app) -> str:
    from astrolift_scm.services.workflow_sync import render_astrolift_ci_workflow

    body = render_astrolift_ci_workflow(app)
    app.ci_workflow_template_version = TEMPLATE_VERSION
    app.ci_workflow_state = {
        "synced_hash": content_hash(body),
        "synced_blob_sha": git_blob_sha(body.encode("utf-8")),
        "synced_at": "2026-01-01T00:00:00+00:00",
        "path": ".github/workflows/astrolift-ci.yml",
        "state": "in_sync",
    }
    app.save(update_fields=["ci_workflow_template_version", "ci_workflow_state", "updated_at", "version"])
    return body


@pytest.fixture
def route_spy(monkeypatch):
    """Record which route each push took: "direct" or "pr"."""
    routes: list[str] = []

    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync.put_file",
        lambda *a, **kw: routes.append("pr" if kw.get("branch", "").startswith("astrolift/") else "direct")
        or SimpleNamespace(commit_sha="sha", file_path="p", web_url="https://x"),
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._github_get_branch_sha", lambda *a, **kw: "head-sha"
    )
    monkeypatch.setattr("astrolift_scm.services.workflow_sync._github_create_ref", lambda *a, **kw: None)
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._github_open_pull_request",
        lambda *a, **kw: "https://github.com/acme/api/pull/7",
    )
    # An unprotected branch: the direct write is available, which is the
    # whole hazard -- a protected branch was never at risk.
    monkeypatch.setattr(
        "astrolift_scm.services.workflow_sync._is_github_branch_protected", lambda *a, **kw: False
    )
    return routes


def test_a_hand_edited_file_is_not_overwritten_in_place(monkeypatch, org, route_spy):
    """The headline bug: this direct-wrote over the operator's edit."""
    app = _github_app(org)
    _github_conn(org)
    _seed_github_baseline(app)
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: _FOREIGN)

    from astrolift_scm.services.workflow_sync import sync_workflow_file_to_repo

    result = sync_workflow_file_to_repo(app)

    assert route_spy == ["pr"], "an edited file must go through a reviewable PR"
    assert result.status == "pr_opened"


def test_an_absent_file_is_written_directly(monkeypatch, org, route_spy):
    """Nothing to lose, so a PR would be pure friction."""
    app = _github_app(org)
    _github_conn(org)
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: None)

    from astrolift_scm.services.workflow_sync import sync_workflow_file_to_repo

    sync_workflow_file_to_repo(app)

    assert route_spy == ["direct"]


def test_a_platform_written_file_on_an_older_template_is_written_directly(monkeypatch, org, route_spy):
    """``template_stale``: the repo file is the platform's own work, just
    stamped at an older template version.

    The body has to differ from the current render or the push short-circuits
    as ``in_sync`` before any route is chosen -- so the difference here is the
    stamp line's version, which ``content_hash`` strips, keeping the body hash
    equal to the recorded baseline. That is exactly what a template bump
    looks like on a file nobody has touched.
    """
    import re

    app = _github_app(org)
    _github_conn(org)
    body = _seed_github_baseline(app)
    older = max(TEMPLATE_VERSION - 1, 0)
    repo_text = re.sub(r"template-version=\d+", f"template-version={older}", body, count=1)
    assert repo_text != body, "the stale stamp must actually differ"
    app.ci_workflow_template_version = older
    app.save(update_fields=["ci_workflow_template_version", "updated_at", "version"])
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: repo_text)

    from astrolift_scm.services.workflow_sync import sync_workflow_file_to_repo

    sync_workflow_file_to_repo(app)

    assert route_spy == ["direct"]


def test_bitbucket_refuses_rather_than_overwriting(monkeypatch, org):
    """Bitbucket has no side-branch PR flow, so there is no safe route --
    refusing beats losing the edit."""
    app = _scaffold(org)  # bitbucket
    _conn(org)
    _seed_baseline(app)
    monkeypatch.setattr("astrolift_scm.services.workflow_sync.fetch_file", lambda *a, **kw: _FOREIGN)

    def _boom(*a, **kw):
        raise AssertionError("must not write over a hand-edited Bitbucket file")

    monkeypatch.setattr("astrolift_scm.services.workflow_sync.put_file", _boom)

    from astrolift_scm.services.workflow_sync import WorkflowSyncError, sync_workflow_file_to_repo

    with pytest.raises(WorkflowSyncError) as exc:
        sync_workflow_file_to_repo(app)

    assert exc.value.code == "WOULD_OVERWRITE_EDITS"
