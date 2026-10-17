"""Managing semantic layers end to end (#2424): create, status, share,
rebuild and delete, over GraphQL and MCP, against a fake source host."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager

import pytest

from astrolift_agents.tests.test_mcp_gateway import _request
from astrolift_agents.views.mcp import mcp_gateway
from astrolift_identity.api_tokens import (
    SCOPE_MCP_READ,
    SCOPE_MCP_WRITE,
    SCOPE_READ_APPS,
    SCOPE_WRITE_APP_ACCESS,
    SCOPE_WRITE_APPS,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ActionSafetyRule, ApiToken
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry import brain_manage, brain_template
from astrolift_registry.brains import visible_brains
from astrolift_registry.models import AppTeamAccess, BrainAppBinding, BrainCreation, RegisteredApp
from astrolift_registry.schema.brain_types import BrainShareAccess
from astrolift_registry.schema.mutations import RegistryMutation
from astrolift_registry.schema.mutations.brains import (
    ArchiveBrainInput,
    CreateBrainInput,
    RebuildBrainInput,
    ShareBrainInput,
    UnshareBrainInput,
)
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.tests.test_brain_scaffold_2424 import edition_zip
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import repo_writes
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db

ALL = [
    Permission.APP_READ,
    Permission.APP_CREATE,
    Permission.APP_UPDATE,
    Permission.APP_DEPLOY,
    Permission.APP_DELETE,
]


class FakeHost:
    """A source host with repositories in memory."""

    def __init__(self):
        self.repos: dict[str, repo_writes.RepoState] = {}
        self.commits: list[dict] = []
        self.created: list[tuple[str, str, str]] = []

    def state(self, connection, repo):
        return self.repos.get(repo, repo_writes.RepoState(exists=False))

    def create(self, connection, owner, name, visibility):
        if connection.kind == "github_app_install":
            raise repo_writes._error("PRECONDITION", "the GitHub App installation cannot create repositories")
        self.created.append((owner, name, visibility))
        self.repos[f"{owner}/{name}"] = repo_writes.RepoState(exists=True, empty=True)
        return self.repos[f"{owner}/{name}"]

    def commit(self, connection, repo, branch, files, message, *, state, author=None):
        sha = hashlib.sha1(repr(sorted(f.path for f in files)).encode()).hexdigest()
        self.commits.append(
            {"repo": repo, "branch": branch, "files": files, "message": message, "author": author}
        )
        self.repos[repo] = repo_writes.RepoState(exists=True, empty=False, head_sha=sha, commit_count=1)
        return sha


@pytest.fixture
def host(monkeypatch):
    fake = FakeHost()
    monkeypatch.setattr(repo_writes, "repository_state", fake.state)
    monkeypatch.setattr(repo_writes, "create_repository", fake.create)
    monkeypatch.setattr(repo_writes, "commit_tree", fake.commit)
    monkeypatch.setattr(brain_template, "check_ref", lambda ref: None)
    monkeypatch.setattr(brain_template, "download", lambda ref: edition_zip())
    return fake


@pytest.fixture
def started(monkeypatch):
    calls: list[tuple[str, list, str]] = []

    def start(name, args, *, workflow_id, task_queue=None, replace_running=True):
        from astrolift_workflows.client import WorkflowHandle

        calls.append((name, args, workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id="r", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", start)
    monkeypatch.setattr("astrolift_scm.services.autowire.run_autowire", lambda app, actor=None: None)
    return calls


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def audit(monkeypatch):
    rows = []
    monkeypatch.setattr("core.mutations._audit_writer", rows.append)
    return rows


def _world(suffix):
    from astrolift_clusters.models import TenantCluster

    world = ScopeWorld(suffix)
    world.user = make_user(suffix)
    world.cluster = make_cluster(world, suffix)
    TenantCluster.objects.filter(pk=world.cluster.pk).update(lifecycle=TenantCluster.Lifecycle.MANAGED.value)
    world.connection = SourceConnection.objects.create(
        organization=world.org, kind="github_pat", display_name="acme", account_login="acme"
    )
    return world


@pytest.fixture
def world():
    world = _world("bm2424")
    bind_role(world.user, permissions=ALL, kind="ORG", scope_id=world.org.pk, slug="bm-admin")
    return world


@pytest.fixture
def other():
    world = _world("bmx2424")
    bind_role(world.user, permissions=ALL, kind="ORG", scope_id=world.org.pk, slug="bmx-admin")
    return world


@contextmanager
def _as(world, user=None, token=None):
    user = user or world.user
    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=user.pk)
    ):
        marker = set_current_api_token(token) if token is not None else None
        try:
            yield make_info(user)
        finally:
            if marker is not None:
                reset_current_api_token(marker)


def _create(world, info=None, **extra):
    fields = {
        "project_id": str(world.medops_project.guid),
        "slug": "acme-brain",
        "source_connection_id": str(world.connection.guid),
        "repo": "acme/kb",
        "name": "Acme Knowledge",
        **extra,
    }
    with _as(world) as default_info:
        return RegistryMutation().create_brain(info or default_info, input=CreateBrainInput(**fields))


def _run_steps(row_id):
    for step in (brain_manage.ensure_repo, brain_manage.scaffold, brain_manage.register):
        assert step(row_id)["state"] != "failed", BrainCreation.objects.get(pk=row_id).error_message


def _brain(world, slug="acme-brain", **fields):
    app = RegisteredApp.objects.create(
        organization=world.org,
        team=world.medops,
        project=world.medops_project,
        name="KB",
        slug=slug,
        app_kind="mimwell",
        k8s_namespace=f"acme-{slug}",
        provisioning_status="ready",
        source_repo="acme/kb",
        source_kind="github",
        **fields,
    )
    AppTeamAccess.objects.create(registered_app=app, team=world.medops, access_level="owner")
    AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=world.cluster, name="production", url="https://kb.acme.test"
    )
    return app


# ---------------------------------------------------------------------------
# createBrain
# ---------------------------------------------------------------------------


def test_create_scaffolds_one_commit_registers_a_brain_and_deploys_it(world, host, started, audit):
    result = _create(world)
    assert result.ok, result.errors
    creation = result.data
    assert creation.state.value == "pending"
    [(name, args, workflow_id)] = started
    assert name == "BrainCreateWorkflow"
    assert workflow_id == f"BrainCreateWorkflow-{world.org.guid}-acme-brain"
    row = BrainCreation.objects.get(guid=str(creation.id))
    assert [a.action for a in audit] == ["brain.create"]
    assert audit[0].extra["repo"] == "acme/kb" and audit[0].extra["template_ref"] == "main"

    _run_steps(row.pk)
    assert host.created == [("acme", "kb", "private")]
    [commit] = host.commits
    assert commit["author"] == {"name": world.user.username, "email": world.user.email}
    paths = {f.path for f in commit["files"]}
    assert {"astrolift.toml", "client.config.json", "edition.json", brain_template.DOCKERFILE} <= paths

    row.refresh_from_db()
    assert row.state == "registered"
    app = row.registered_app
    assert app.app_kind == "mimwell" and app.build_mode == "platform_build" and app.source_repo == "acme/kb"
    assert "astrolift.toml" in {f.path for f in commit["files"]}
    with _as(world):
        assert [b.slug for b in visible_brains()] == ["acme-brain"]

    # Provisioning first; then the scaffold commit deploys.
    assert brain_manage.deploy(row.pk)["state"] == "registered"
    RegisteredApp.objects.filter(pk=app.pk).update(provisioning_status="ready")
    assert brain_manage.deploy(row.pk)["state"] == "deploying"
    row.refresh_from_db()
    assert row.deployment.commit_sha == row.commit_sha
    assert row.deployment.triggered_by_user_id == world.user.pk
    assert started[-1][0] == "DeployAppWorkflow"
    assert brain_manage.observe(row.pk)["state"] == "deploying"
    Deployment.objects.filter(pk=row.deployment_id).update(status="running")
    assert brain_manage.observe(row.pk)["state"] == "ready"

    with _as(world) as info:
        polled = RegistryQuery().astrolift_brain_creation(info, id=str(row.guid))
    assert polled.state.value == "ready" and polled.app.slug == "acme-brain"


def test_steps_are_idempotent_when_retried(world, host, started):
    row = BrainCreation.objects.get(guid=str(_create(world).data.id))
    _run_steps(row.pk)
    _run_steps(row.pk)  # every step sees its work done
    assert len(host.commits) == 1 and len(host.created) == 1
    assert RegisteredApp.objects.filter(organization=world.org, slug="acme-brain").count() == 1


def test_a_retry_joins_the_running_creation(world, host, started):
    first = _create(world).data
    second = _create(world).data
    assert first.id == second.id
    assert len(started) == 1
    someone = make_user("bm2424-b")
    bind_role(someone, permissions=ALL, kind="ORG", scope_id=world.org.pk, slug="bm-admin-b")
    with _as(world, user=someone) as info:
        clash = RegistryMutation().create_brain(
            info,
            input=CreateBrainInput(
                project_id=str(world.medops_project.guid),
                slug="acme-brain",
                source_connection_id=str(world.connection.guid),
                repo="acme/other",
            ),
        )
    assert clash.errors[0].code == "CONFLICT"


@pytest.mark.parametrize(
    "extra, code, field",
    [
        ({"slug": "Bad_Slug"}, "VALIDATION", "slug"),
        ({"slug": "1acme"}, "VALIDATION", "slug"),
        ({"slug": "acme-"}, "VALIDATION", "slug"),
        ({"repo": "nope"}, "VALIDATION", "repo"),
        ({"template_ref": "../x"}, "VALIDATION", "templateRef"),
        ({"environment_name": "Prod!"}, "VALIDATION", "environmentName"),
    ],
)
def test_create_validates_its_input(world, host, started, extra, code, field):
    result = _create(world, **extra)
    assert not result.ok
    assert (result.errors[0].code, result.errors[0].field) == (code, field)
    assert not started


def test_a_repository_with_history_or_an_app_install_without_one_is_refused(world, host, started):
    host.repos["acme/kb"] = repo_writes.RepoState(exists=True, empty=False, head_sha="h", commit_count=2)
    assert _create(world).errors[0].code == "CONFLICT"
    SourceConnection.objects.filter(pk=world.connection.pk).update(kind="github_app_install")
    refused = _create(world, repo="acme/new")
    assert refused.errors[0].code == "PRECONDITION"
    assert "create the empty repository first" in refused.errors[0].message
    assert not started


def test_another_orgs_project_or_connection_is_not_found(world, other, host, started):
    foreign_project = _create(world, project_id=str(other.medops_project.guid))
    assert foreign_project.errors[0].code in ("NOT_FOUND", "PERMISSION_DENIED")
    foreign_connection = _create(world, source_connection_id=str(other.connection.guid))
    assert (foreign_connection.errors[0].code, foreign_connection.errors[0].field) == (
        "NOT_FOUND",
        "sourceConnectionId",
    )
    someone = make_user("bm2424-c")
    mine = SourceConnection.objects.create(organization=world.org, kind="github_pat", user=someone)
    assert _create(world, source_connection_id=str(mine.guid)).errors[0].code == "NOT_FOUND"
    assert not started


def test_a_failed_step_records_why_and_stops(world, host, started, monkeypatch):
    row = BrainCreation.objects.get(guid=str(_create(world).data.id))
    # The ref moved after createBrain checked it.
    monkeypatch.setattr(brain_template, "download", lambda ref: edition_zip(tamper="README.md"))
    assert brain_manage.ensure_repo(row.pk)["state"] == "repo_ready"
    assert brain_manage.scaffold(row.pk)["state"] == "failed"
    row.refresh_from_db()
    assert row.error_code == "PRECONDITION" and "README.md (hash differs)" in row.error_message
    assert brain_manage.register(row.pk)["state"] == "failed"
    assert not host.commits


def test_a_creation_is_visible_to_its_creator_only_until_the_brain_is_readable(world, host, started):
    row = BrainCreation.objects.get(guid=str(_create(world).data.id))
    reader = make_user("bm2424-r")
    bind_role(reader, permissions=[Permission.APP_READ], kind="ORG", scope_id=world.org.pk, slug="bm-reader")
    with _as(world, user=reader) as info:
        assert RegistryQuery().astrolift_brain_creation(info, id=str(row.guid)) is None
    _run_steps(row.pk)
    with _as(world, user=reader) as info:
        assert RegistryQuery().astrolift_brain_creation(info, id=str(row.guid)).app.slug == "acme-brain"


# ---------------------------------------------------------------------------
# Status, share, rebuild, delete
# ---------------------------------------------------------------------------


def test_status_reports_deployed_against_head_and_compile_freshness(world, monkeypatch):
    app = _brain(world)
    env = AppEnvironment.objects.get(registered_app=app)
    Deployment.objects.create(
        registered_app=app, app_environment=env, trigger_kind="push", status="running", commit_sha="old"
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflows._pick_source_connection", lambda a: world.connection
    )
    monkeypatch.setattr(repo_writes, "branch_head_detail", lambda c, r, b: ("new", "2026-10-16T00:00:00Z"))
    monkeypatch.setattr(
        repo_writes,
        "latest_workflow_run",
        lambda c, r, f: {"status": "completed", "conclusion": "success", "url": "u", "created_at": "t"},
    )
    reads = []

    def fetch(connection, *, repo_full_name, path, ref):
        reads.append((path, ref))
        return json.dumps({"generated": "2026-10-15"})

    monkeypatch.setattr("astrolift_scm.providers.fetch_file", fetch)
    with _as(world) as info:
        status = RegistryQuery().astrolift_brain_status(info, slug=app.slug)
    assert (
        status.deployed_commit == "old" and status.source_head.commit_sha == "new" and status.behind is True
    )
    assert status.compiled_at == "2026-10-15" and reads == [
        (brain_manage.BRAIN_MANIFEST_FILE, "old"),
        ("client.config.json", "old"),
    ]
    assert status.last_ingest.conclusion == "success" and status.served


def test_share_and_unshare_change_who_can_read_the_brain(world, audit):
    app = _brain(world)
    member = make_user("bm2424-m")
    bind_role(member, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.platform.pk, slug="bm-p")

    def member_sees():
        with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=member.pk)):
            return [b.slug for b in visible_brains()]

    assert member_sees() == []
    with _as(world) as info:
        shared = RegistryMutation().share_brain(
            info,
            input=ShareBrainInput(
                slug=app.slug, team_slug=world.platform.slug, access=BrainShareAccess.VIEWER
            ),
        )
    assert shared.ok, shared.errors
    assert {(s.team, s.access) for s in shared.data.shared_with} >= {(world.platform.slug, "viewer")}
    assert member_sees() == [app.slug]
    with _as(world) as info:
        assert (
            RegistryMutation()
            .unshare_brain(info, input=UnshareBrainInput(slug=app.slug, team_slug=world.platform.slug))
            .ok
        )
        home = RegistryMutation().unshare_brain(
            info, input=UnshareBrainInput(slug=app.slug, team_slug=world.medops.slug)
        )
    assert member_sees() == []
    assert home.errors[0].code == "VALIDATION"
    assert [a.action for a in audit] == ["brain.share", "brain.unshare", "brain.unshare"]
    assert audit[0].extra["surface"] == "graphql"


def test_share_never_touches_an_owner(world):
    app = _brain(world)
    AppTeamAccess.objects.create(registered_app=app, team=world.platform, access_level="owner")
    with _as(world) as info:
        result = RegistryMutation().share_brain(
            info,
            input=ShareBrainInput(
                slug=app.slug, team_slug=world.platform.slug, access=BrainShareAccess.VIEWER
            ),
        )
        removed = RegistryMutation().unshare_brain(
            info, input=UnshareBrainInput(slug=app.slug, team_slug=world.platform.slug)
        )
    for refused in (result, removed):
        assert refused.errors[0].code == "PRECONDITION"
        # Owners change on the app's Teams card; astro app access is the edge sign-in list.
        assert "web UI, on the app's Settings page (Teams)" in refused.errors[0].message
        assert "astro app access" not in refused.errors[0].message
    assert AppTeamAccess.objects.get(registered_app=app, team=world.platform).access_level == "owner"


def test_share_asks_for_a_fresh_verification_in_the_browser(world, monkeypatch):
    monkeypatch.setattr("astrolift_identity.action_safety.DEFAULT_ON", True)
    app = _brain(world)
    from django.contrib.sessions.backends.db import SessionStore

    with _as(world) as info:
        info.context.request.session = SessionStore()  # a browser session that never stepped up
        result = RegistryMutation().share_brain(
            info,
            input=ShareBrainInput(
                slug=app.slug, team_slug=world.platform.slug, access=BrainShareAccess.VIEWER
            ),
        )
    assert result.errors[0].code == "STEP_UP_REQUIRED"
    assert not AppTeamAccess.objects.filter(registered_app=app, team=world.platform).exists()


def test_another_orgs_brain_is_not_found(world, other):
    foreign = _brain(other, slug="their-brain")
    with _as(world) as info:
        assert RegistryQuery().astrolift_brain(info, slug=foreign.slug) is None
        assert RegistryQuery().astrolift_brain_status(info, slug=foreign.slug) is None
        for call in (
            lambda: RegistryMutation().share_brain(
                info,
                input=ShareBrainInput(
                    slug=foreign.slug, team_slug=world.platform.slug, access=BrainShareAccess.VIEWER
                ),
            ),
            lambda: RegistryMutation().unshare_brain(
                info, input=UnshareBrainInput(slug=foreign.slug, team_slug=world.platform.slug)
            ),
            lambda: RegistryMutation().rebuild_brain(info, input=RebuildBrainInput(slug=foreign.slug)),
            lambda: RegistryMutation().archive_brain(info, input=ArchiveBrainInput(slug=foreign.slug)),
        ):
            assert call().errors[0].code == "NOT_FOUND"


def test_a_generic_app_is_not_a_brain(world):
    with _as(world) as info:
        assert RegistryQuery().astrolift_brain(info, slug=world.medops_app.slug) is None
        result = RegistryMutation().archive_brain(info, input=ArchiveBrainInput(slug=world.medops_app.slug))
    assert result.errors[0].code == "NOT_FOUND"
    world.medops_app.refresh_from_db()
    assert world.medops_app.archived_at is None


def test_rebuild_with_ingest_dispatches_the_refresh_workflow_only_when_it_exists(world, monkeypatch, audit):
    from astrolift_scm.services.workflows import WorkflowDispatchResult

    app = _brain(world, build_mode="platform_build")
    dispatched = []
    monkeypatch.setattr(
        "astrolift_scm.services.workflows._pick_source_connection", lambda a: world.connection
    )
    monkeypatch.setattr(
        "astrolift_scm.services.workflows.dispatch_astrolift_ci_workflow",
        lambda a, branch=None, workflow_path=None: dispatched.append(workflow_path)
        or WorkflowDispatchResult(ok=True, run_url="https://gh/runs"),
    )
    present = {"value": None}
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", lambda c, **k: present["value"])
    with _as(world) as info:
        missing = RegistryMutation().rebuild_brain(info, input=RebuildBrainInput(slug=app.slug, ingest=True))
        present["value"] = "on: workflow_dispatch"
        ran = RegistryMutation().rebuild_brain(info, input=RebuildBrainInput(slug=app.slug, ingest=True))
    assert missing.errors[0].code == "PRECONDITION" and "knowledge-refresh.yml" in missing.errors[0].message
    assert ran.ok and ran.data.mode == "ingest" and ran.data.ingest_run_url == "https://gh/runs"
    assert dispatched == [brain_manage.INGEST_WORKFLOW]
    assert audit[-1].action == "brain.rebuild" and audit[-1].extra["mode"] == "ingest"


def test_rebuild_redeploys_a_platform_built_brain_through_start_deployment(world, monkeypatch):
    from astrolift_graphql import success
    from astrolift_lifecycle.schema.mutations.deployments import DeploymentMutations

    app = _brain(world, build_mode="platform_build", deploy_branch="main")
    seen = []

    def start(self, info, input):
        seen.append((input.app_slug, input.environment_name, input.branch))
        return success(type("D", (), {"id": "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"})())

    monkeypatch.setattr(DeploymentMutations, "start_deployment", start)
    with _as(world) as info:
        result = RegistryMutation().rebuild_brain(info, input=RebuildBrainInput(slug=app.slug))
    assert result.ok and result.data.mode == "deploy" and seen == [(app.slug, "production", "main")]


def test_delete_archives_the_brain_and_revokes_hosted_apps(world, audit):
    brain = _brain(world)
    hosted = RegisteredApp.objects.create(
        organization=world.org, team=world.medops, project=world.medops_project, name="Chat", slug="kb-chat"
    )
    binding = BrainAppBinding.objects.create(
        organization=world.org, registered_app=hosted, brain=brain, granted_by=world.user
    )
    with _as(world) as info:
        hosted_rows = RegistryQuery().astrolift_brain(info, slug=brain.slug).hosted_apps
        assert [(h.slug, h.environment) for h in hosted_rows] == [("kb-chat", "")]
        result = RegistryMutation().archive_brain(info, input=ArchiveBrainInput(slug=brain.slug))
        assert result.ok, result.errors
        assert [b.slug for b in visible_brains()] == []
    brain.refresh_from_db()
    binding.refresh_from_db()
    assert brain.archived_at is not None and brain.deleted_at is None
    assert binding.deleted_at is not None
    assert audit[-1].action == "brain.delete"


def test_delete_waits_for_approval_when_a_rule_asks_for_it(world):
    brain = _brain(world)
    ActionSafetyRule.objects.create(
        organization=world.org,
        scope_kind="ORG",
        action="brain.delete",
        require_approval=True,
        minimum_approvals=1,
    )
    with _as(world) as info:
        result = RegistryMutation().archive_brain(info, input=ArchiveBrainInput(slug=brain.slug))
    assert result.errors[0].code == "APPROVAL_REQUIRED"
    brain.refresh_from_db()
    assert brain.archived_at is None


# ---------------------------------------------------------------------------
# MCP: agents
# ---------------------------------------------------------------------------


def _token(world, scopes, suffix="t"):
    plaintext = f"alft_at_brain-mgmt-{suffix}"
    return ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="brains",
        token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        token_last_4="2424",
        scopes=scopes,
    )


def _tool(world, token, name, **arguments):
    marker = set_current_api_token(token)
    try:
        with tenant_context(
            TenantContext(organization_id=world.org.pk, team_id=token.team_id, actor_user_id=world.user.pk)
        ):
            response = mcp_gateway(
                _request(world.user, token, "tools/call", {"name": name, "arguments": arguments})
            )
    finally:
        reset_current_api_token(marker)
    return json.loads(response.content)["result"]


WRITE = [SCOPE_MCP_READ, SCOPE_MCP_WRITE, SCOPE_READ_APPS, SCOPE_WRITE_APPS, SCOPE_WRITE_APP_ACCESS]


def test_agents_cannot_share_or_delete_until_the_org_allows_it(world, audit):
    brain = _brain(world)
    token = _token(world, WRITE)
    share = {"slug": brain.slug, "team_slug": world.platform.slug, "access": "viewer"}
    refused = _tool(world, token, "astrolift_share_brain", **share)
    assert refused["isError"] is True and "needs a person" in json.dumps(refused)
    assert _tool(world, token, "astrolift_delete_brain", slug=brain.slug)["isError"] is True
    brain.refresh_from_db()
    assert brain.archived_at is None
    assert not AppTeamAccess.objects.filter(registered_app=brain, team=world.platform).exists()

    for action in ("brain.share", "brain.delete"):
        ActionSafetyRule.objects.create(
            organization=world.org, scope_kind="ORG", action=action, allow_agents=True
        )
    # A narrower scope can turn it back off.
    ActionSafetyRule.objects.create(
        organization=world.org, scope_kind="APP", scope_id=brain.pk, action="brain.delete", allow_agents=False
    )
    allowed = _tool(world, token, "astrolift_share_brain", **share)
    assert allowed["isError"] is False, allowed
    assert AppTeamAccess.objects.get(registered_app=brain, team=world.platform).access_level == "viewer"
    assert _tool(world, token, "astrolift_delete_brain", slug=brain.slug)["isError"] is True
    shared = [a for a in audit if a.action == "brain.share"]
    assert shared and shared[-1].extra["surface"] == "mcp" and shared[-1].extra["token"] == str(token.guid)


def test_unshare_is_open_to_agents_because_it_only_narrows(world):
    brain = _brain(world)
    AppTeamAccess.objects.create(registered_app=brain, team=world.platform, access_level="viewer")
    result = _tool(
        world, _token(world, WRITE), "astrolift_unshare_brain", slug=brain.slug, team_slug=world.platform.slug
    )
    assert result["isError"] is False, result
    assert not AppTeamAccess.objects.filter(registered_app=brain, team=world.platform).exists()


def test_no_brain_tool_outruns_the_tokens_scopes(world):
    brain = _brain(world)
    reader = _token(world, [SCOPE_MCP_READ, SCOPE_READ_APPS], suffix="r")
    # Status reads nothing from the host here: no connection can read the repository.
    SourceConnection.objects.filter(pk=world.connection.pk).update(is_active=False)
    assert _tool(world, reader, "astrolift_get_brain_status", slug=brain.slug)["isError"] is False
    for name, args in (
        ("astrolift_unshare_brain", {"slug": brain.slug, "team_slug": world.platform.slug}),
        ("astrolift_rebuild_brain", {"slug": brain.slug}),
        ("astrolift_delete_brain", {"slug": brain.slug}),
    ):
        result = _tool(world, reader, name, **args)
        assert result["isError"] is True and "missing scope" in json.dumps(result), name
    # write:apps without write:app-access cannot touch shares.
    writer = _token(world, [SCOPE_MCP_READ, SCOPE_MCP_WRITE, SCOPE_READ_APPS, SCOPE_WRITE_APPS], suffix="w")
    result = _tool(world, writer, "astrolift_unshare_brain", slug=brain.slug, team_slug=world.platform.slug)
    assert result["isError"] is True and "write:app-access" in json.dumps(result)


def test_an_agent_creates_and_polls_a_brain(world, host, started):
    token = _token(world, WRITE)
    created = _tool(
        world,
        token,
        "astrolift_create_brain",
        project_id=str(world.medops_project.guid),
        slug="agent-brain",
        source_connection_id=str(world.connection.guid),
        repo="acme/agent-kb",
    )
    assert created["isError"] is False, created
    body = created["structuredContent"]
    assert body["state"] == "pending" and body["repo"] == "acme/agent-kb"
    polled = _tool(world, token, "astrolift_get_brain_creation", id=body["id"])
    assert polled["structuredContent"]["id"] == body["id"]


def test_the_org_rule_lets_agents_on_only_at_the_org(world):
    from astrolift_identity.schema.action_safety import ActionSafetyMutations, SetActionSafetyRuleInput

    bind_role(
        world.user, permissions=[Permission.ORG_UPDATE], kind="ORG", scope_id=world.org.pk, slug="bm-org"
    )
    with _as(world) as info:
        team_on = ActionSafetyMutations().set_action_safety_rule(
            info,
            input=SetActionSafetyRuleInput(
                action="brain.share", scope_kind="TEAM", scope_id=str(world.medops.guid), allow_agents=True
            ),
        )
        not_gated = ActionSafetyMutations().set_action_safety_rule(
            info, input=SetActionSafetyRuleInput(action="app.deregister", scope_kind="ORG", allow_agents=True)
        )
        org_on = ActionSafetyMutations().set_action_safety_rule(
            info, input=SetActionSafetyRuleInput(action="brain.share", scope_kind="ORG", allow_agents=True)
        )
    assert team_on.errors[0].field == "allowAgents"
    assert not_gated.errors[0].field == "allowAgents"
    assert org_on.ok and org_on.data.allow_agents is True


# ---------------------------------------------------------------------------
# Substrate (Mimwell's cell)
# ---------------------------------------------------------------------------


def _substrate(**fields):
    from astrolift_registry.schema import brain_types as bt

    return bt.BrainSubstrateInput(**fields)


def test_create_hosts_the_cell_it_was_asked_for(world, host, started):
    from astrolift_registry.models import BrainSubstrate
    from astrolift_registry.schema import brain_types as bt

    result = _create(
        world,
        substrate=_substrate(
            authority=bt.BrainAuthority.POSTGRES,
            vectors=bt.BrainVectors.IN_DB,
            persistence=[bt.BrainPersistence.POSTGRES, bt.BrainPersistence.SQLITE_VOLUME],
            replicas=2,
        ),
    )
    assert result.ok, result.errors
    row = BrainCreation.objects.get(guid=str(result.data.id))
    _run_steps(row.pk)
    files = {f.path: f.content for f in host.commits[0]["files"]}
    config = json.loads(files["client.config.json"])
    assert (
        config["deploy"]["persistence"] == ["postgres", "sqlite-volume"] and config["deploy"]["replicas"] == 2
    )
    app = RegisteredApp.objects.get(organization=world.org, slug="acme-brain")
    assert "statefulset" in app.manifest_raw and 'kind = "postgres"' in app.manifest_raw
    recorded = BrainSubstrate.objects.get(brain=app)
    assert (recorded.authority, recorded.vectors, recorded.persistence) == (
        "postgres",
        "in-db",
        ["postgres", "sqlite-volume"],
    )
    with _as(world) as info:
        substrate = RegistryQuery().astrolift_brain(info, slug=app.slug).substrate
    assert substrate.cell == "authority=postgres, blobs=fs, vectors=in-db, graph=edges-table"
    assert {r.kind for r in substrate.resources} == {"postgres", "sqlite-volume"}


@pytest.mark.parametrize(
    "fields, says",
    [
        ({"authority": "D1"}, "unsupported cell: authority=d1"),
        ({"authority": "POSTGRES"}, "needs persistence 'postgres'"),
        ({"vectors": "IN_DB"}, "files has none"),
    ],
)
def test_create_refuses_a_cell_by_name(world, host, started, fields, says):
    from astrolift_registry.schema import brain_types as bt

    enums = {"authority": bt.BrainAuthority, "vectors": bt.BrainVectors}
    result = _create(world, substrate=_substrate(**{k: enums[k][v] for k, v in fields.items()}))
    assert result.errors[0].code == "VALIDATION" and says in result.errors[0].message
    assert not started


def test_any_slug_is_a_brain_that_keeps_its_name(world, host, started):
    from astrolift_registry.brain_substrate import app_slug

    result = _create(world, slug="acme-kb")
    assert result.ok, result.errors
    row = BrainCreation.objects.get(guid=str(result.data.id))
    _run_steps(row.pk)
    files = {f.path: f.content for f in host.commits[0]["files"]}
    config = json.loads(files["client.config.json"])
    assert (config["client"]["slug"], config["deploy"]["app_slug"]) == ("acme-kb", "acme-kb")
    assert app_slug(config) == "acme-kb"  # what the brain's make astrolift-manifest renders
    app = RegisteredApp.objects.get(organization=world.org, slug="acme-kb")
    assert app.app_kind == "mimwell" and 'name = "acme-kb"' in app.manifest_raw
    assert 'name = "acme-kb"' in files["astrolift.toml"].decode()


def _edition_without_app_slug():
    from astrolift_registry.tests.test_brain_scaffold_2424 import CONFIG, OLD_DEPLOY, config_schema

    return edition_zip(
        files={
            "client.config.json": json.dumps(CONFIG).encode(),
            brain_template.CONFIG_SCHEMA: config_schema(*OLD_DEPLOY),
            brain_template.DOCKERFILE: b"FROM nginx\n",
        }
    )


def test_a_slug_the_edition_cannot_name_is_refused_before_anything_is_made(world, host, started, monkeypatch):
    monkeypatch.setattr(brain_template, "download", lambda ref: _edition_without_app_slug())

    result = _create(world, slug="acme")

    assert not result.ok
    error = result.errors[0]
    assert (error.code, error.field) == ("PRECONDITION", "slug")
    assert "has no deploy.app_slug" in error.message and "(acme-brain)" in error.message
    assert not BrainCreation.objects.exists()
    assert not started and not host.created and not host.commits
    assert _create(world, slug="acme-brain").ok  # the slug the refusal names


def test_the_scaffold_step_still_refuses_an_edition_that_changed_since(world, host, started, monkeypatch):
    row = BrainCreation.objects.get(guid=str(_create(world, slug="acme").data.id))
    monkeypatch.setattr(brain_template, "download", lambda ref: _edition_without_app_slug())
    assert brain_manage.ensure_repo(row.pk)["state"] == "repo_ready"
    assert brain_manage.scaffold(row.pk)["state"] == "failed"
    row.refresh_from_db()
    assert row.error_code == "PRECONDITION" and "has no deploy.app_slug" in row.error_message
    assert not host.commits


@pytest.mark.parametrize(
    ("case", "code", "field", "says"),
    [
        ("tampered edition", "PRECONDITION", "templateRef", "README.md (hash differs)"),
        ("no portal Dockerfile", "PRECONDITION", "templateRef", brain_template.DOCKERFILE),
        ("namespace taken", "VALIDATION", "slug", "is already used by another app"),
        ("no managed cluster", "PRECONDITION", None, "No managed cluster connected"),
    ],
)
def test_create_refuses_what_the_workflow_would_before_it_makes_anything(
    world, host, started, monkeypatch, case, code, field, says
):
    from _sdk.k8s_naming import app_namespace

    from astrolift_clusters.models import TenantCluster
    from astrolift_registry.tests.test_brain_scaffold_2424 import CONFIG

    if case == "tampered edition":
        monkeypatch.setattr(brain_template, "download", lambda ref: edition_zip(tamper="README.md"))
    elif case == "no portal Dockerfile":
        files = {"client.config.json": json.dumps(CONFIG).encode(), "README.md": b"# Mimwell\n"}
        monkeypatch.setattr(brain_template, "download", lambda ref: edition_zip(files=files))
    elif case == "namespace taken":
        RegisteredApp.objects.create(
            organization=world.org,
            team=world.medops,
            project=world.medops_project,
            name="Squatter",
            slug="squatter",
            k8s_namespace=app_namespace(organization_slug=world.org.slug, app_slug="acme-brain"),
        )
    else:
        TenantCluster.objects.filter(pk=world.cluster.pk).update(
            lifecycle=TenantCluster.Lifecycle.REGISTERED.value
        )

    result = _create(world)

    assert not result.ok
    error = result.errors[0]
    assert (error.code, error.field) == (code, field)
    assert says in error.message
    assert not BrainCreation.objects.exists()
    assert not started and not host.created and not host.commits


class FakeRepo:
    def __init__(self, config, manifest):
        self.files = {"client.config.json": json.dumps(config), "astrolift.toml": manifest}
        self.commits = []

    def fetch(self, connection, *, repo_full_name, path, ref):
        return self.files.get(path)

    def update(self, connection, repo, branch, files, message, *, author=None):
        self.commits.append(
            {"branch": branch, "files": {f.path: f.content.decode() for f in files}, "message": message}
        )
        self.files.update(self.commits[-1]["files"])
        return "f00d"


@pytest.fixture
def repo(world, monkeypatch):
    from astrolift_registry.brain_substrate import Selection, render_manifest

    fake = FakeRepo(
        {
            "client": {"slug": "acme"},
            "brain": {"store": "json"},
            "deploy": {"target": "astrolift", "persistence": []},
        },
        render_manifest(Selection(), slug="acme-brain", display_name="KB"),
    )
    monkeypatch.setattr("astrolift_scm.providers.fetch_file", fake.fetch)
    monkeypatch.setattr(repo_writes, "update_files", fake.update)
    monkeypatch.setattr(
        "astrolift_scm.services.workflows._pick_source_connection", lambda a: world.connection
    )
    return fake


def _graduate(world, slug, **fields):
    from astrolift_registry.schema.mutations.brains import UpdateBrainSubstrateInput

    with _as(world) as info:
        return RegistryMutation().update_brain_substrate(
            info, input=UpdateBrainSubstrateInput(slug=slug, substrate=_substrate(**fields))
        )


def test_graduating_commits_config_and_manifest_once_and_provisions_postgres(world, repo, started, audit):
    from astrolift_registry.schema import brain_types as bt
    from astrolift_services.models import ManagedService

    app = _brain(world, build_mode="platform_build")
    result = _graduate(
        world,
        app.slug,
        authority=bt.BrainAuthority.POSTGRES,
        graph=bt.BrainGraph.AGE,
        persistence=[bt.BrainPersistence.POSTGRES],
    )
    assert result.ok, result.errors
    [commit] = repo.commits
    assert set(commit["files"]) == {"client.config.json", "astrolift.toml"}
    assert json.loads(commit["files"]["client.config.json"])["deploy"]["persistence"] == ["postgres"]
    assert "age = true" in commit["files"]["astrolift.toml"]
    app.refresh_from_db()
    assert app.manifest_raw == commit["files"]["astrolift.toml"]
    assert ManagedService.objects.filter(registered_app=app, kind="postgres", name="brain").exists()
    assert result.data.substrate.cell == "authority=postgres, blobs=fs, vectors=local, graph=age"
    assert audit[-1].action == "brain.substrate.update" and audit[-1].extra["persistence"] == ["postgres"]


def test_graduation_never_drops_persistence_that_holds_data(world, repo):
    from astrolift_registry import brain_manage as bm
    from astrolift_registry.brain_substrate import Selection
    from astrolift_registry.schema import brain_types as bt

    app = _brain(world, build_mode="platform_build")
    bm.record_substrate(app, Selection(authority="sqlite", persistence=("sqlite-volume",)))
    result = _graduate(world, app.slug, persistence=[bt.BrainPersistence.S3], blobs=bt.BrainBlobs.S3)
    assert result.errors[0].code == "PRECONDITION" and "sqlite-volume" in result.errors[0].message
    assert not repo.commits


def test_an_unsupported_graduation_is_refused_by_cell(world, repo):
    from astrolift_registry.schema import brain_types as bt

    app = _brain(world, build_mode="platform_build")
    result = _graduate(world, app.slug, blobs=bt.BrainBlobs.DB_BLOB)
    assert (
        result.errors[0].code == "VALIDATION"
        and "unsupported cell: authority=files, blobs=db-blob" in result.errors[0].message
    )
    assert not repo.commits


def test_graduating_a_brain_named_without_brain_writes_app_slug_only_where_mimwell_has_it(world, repo):
    from astrolift_registry.schema import brain_types as bt
    from astrolift_registry.tests.test_brain_scaffold_2424 import OLD_DEPLOY, config_schema

    app = _brain(world, slug="kb", build_mode="platform_build")
    refused = _graduate(world, app.slug, persistence=[bt.BrainPersistence.SQLITE_VOLUME])
    assert (refused.errors[0].code, refused.errors[0].field) == ("PRECONDITION", "slug")
    assert "needs deploy.app_slug" in refused.errors[0].message and not repo.commits
    repo.files[brain_template.CONFIG_SCHEMA] = config_schema(*OLD_DEPLOY).decode()
    assert _graduate(world, app.slug, persistence=[]).errors[0].code == "PRECONDITION"
    assert not repo.commits

    repo.files[brain_template.CONFIG_SCHEMA] = config_schema(*OLD_DEPLOY, "app_slug").decode()
    result = _graduate(world, app.slug, persistence=[bt.BrainPersistence.SQLITE_VOLUME])
    assert result.ok, result.errors
    [commit] = repo.commits
    config = json.loads(commit["files"]["client.config.json"])
    assert (config["client"]["slug"], config["deploy"]["app_slug"]) == ("kb", "kb")
    assert 'name = "kb"' in commit["files"]["astrolift.toml"]


def test_graduating_keeps_the_client_slug_of_a_config_that_names_the_app(world, repo):
    from astrolift_registry.schema import brain_types as bt

    app = _brain(world, slug="kb", build_mode="platform_build")
    repo.files["client.config.json"] = json.dumps(
        {"client": {"slug": "acme"}, "deploy": {"target": "astrolift", "app_slug": "kb", "persistence": []}}
    )
    result = _graduate(world, app.slug, persistence=[bt.BrainPersistence.SQLITE_VOLUME])
    assert result.ok, result.errors
    [commit] = repo.commits
    config = json.loads(commit["files"]["client.config.json"])
    assert config["client"] == {"slug": "acme"} and config["deploy"]["app_slug"] == "kb"


def test_archive_keeps_the_brains_database(world):
    from astrolift_services.models import ManagedService

    app = _brain(world)
    env = AppEnvironment.objects.get(registered_app=app)
    db = ManagedService.objects.create(
        registered_app=app, app_environment=env, kind="postgres", name="brain", status="active"
    )
    with _as(world) as info:
        assert RegistryMutation().archive_brain(info, input=ArchiveBrainInput(slug=app.slug)).ok
    db.refresh_from_db()
    assert db.deleted_at is None and db.status == "active"


def test_an_agent_graduates_a_brain_over_mcp(world, repo):
    app = _brain(world, build_mode="platform_build")
    result = _tool(
        world,
        _token(world, WRITE),
        "astrolift_update_brain_substrate",
        slug=app.slug,
        substrate={"authority": "sqlite", "persistence": ["sqlite-volume"], "replicas": 2},
    )
    assert result["isError"] is False, result
    body = result["structuredContent"]["substrate"]
    assert body["cell"] == "authority=sqlite, blobs=fs, vectors=local, graph=edges-table"
    assert {r["kind"] for r in body["resources"]} == {"sqlite-volume"}
    bad = _tool(
        world,
        _token(world, WRITE, suffix="b"),
        "astrolift_update_brain_substrate",
        slug=app.slug,
        substrate={"authority": "d1"},
    )
    assert bad["isError"] is True and "unsupported cell" in json.dumps(bad)


# ---------------------------------------------------------------------------
# Review: no brain call reaches further than the caller's own grants
# ---------------------------------------------------------------------------

NO_DEPLOY = [Permission.APP_READ, Permission.APP_CREATE, Permission.APP_UPDATE]


def _limited(world, suffix):
    user = make_user(f"bm2424-{suffix}")
    bind_role(user, permissions=NO_DEPLOY, kind="ORG", scope_id=world.org.pk, slug=f"bm-{suffix}")
    return user


def test_create_deploys_only_for_a_caller_who_may_deploy(world, host, started):
    user = _limited(world, "nodeploy")
    fields = {
        "project_id": str(world.medops_project.guid),
        "slug": "acme-brain",
        "source_connection_id": str(world.connection.guid),
        "repo": "acme/kb",
    }
    with _as(world, user=user) as info:
        refused = RegistryMutation().create_brain(info, input=CreateBrainInput(**fields))
        allowed = RegistryMutation().create_brain(info, input=CreateBrainInput(**fields, deploy=False))
    assert refused.errors[0].code == "PERMISSION_DENIED"
    assert allowed.ok, allowed.errors


def test_share_as_deployer_needs_deploy(world):
    app = _brain(world)
    user = _limited(world, "noshare")
    with _as(world, user=user) as info:
        refused = RegistryMutation().share_brain(
            info,
            input=ShareBrainInput(
                slug=app.slug, team_slug=world.platform.slug, access=BrainShareAccess.DEPLOYER
            ),
        )
        viewer = RegistryMutation().share_brain(
            info,
            input=ShareBrainInput(
                slug=app.slug, team_slug=world.platform.slug, access=BrainShareAccess.VIEWER
            ),
        )
    assert refused.errors[0].code == "PERMISSION_DENIED"
    assert viewer.ok, viewer.errors
    assert AppTeamAccess.objects.get(registered_app=app, team=world.platform).access_level == "viewer"


def test_rebuild_of_a_ci_built_brain_keeps_the_approval_policy(world, monkeypatch):
    from astrolift_scm.services.workflows import WorkflowDispatchResult

    app = _brain(world, build_mode="ci_pushed")
    AppEnvironment.objects.filter(registered_app=app).update(required_approvals=1)
    dispatched = []
    monkeypatch.setattr(
        "astrolift_scm.services.workflows.dispatch_astrolift_ci_workflow",
        lambda a, branch=None, workflow_path=None: dispatched.append(workflow_path)
        or WorkflowDispatchResult(ok=True, run_url="https://gh/runs"),
    )
    with _as(world) as info:
        result = RegistryMutation().rebuild_brain(info, input=RebuildBrainInput(slug=app.slug))
    assert result.errors[0].code == "PRECONDITION" and "approval" in result.errors[0].message
    assert dispatched == []


def test_graduating_needs_deploy_because_the_push_deploys(world, repo):
    app = _brain(world)
    user = _limited(world, "nograd")
    from astrolift_registry.schema.mutations.brains import UpdateBrainSubstrateInput

    with _as(world, user=user) as info:
        result = RegistryMutation().update_brain_substrate(
            info, input=UpdateBrainSubstrateInput(slug=app.slug, substrate=_substrate(persistence=[]))
        )
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert repo.commits == []
