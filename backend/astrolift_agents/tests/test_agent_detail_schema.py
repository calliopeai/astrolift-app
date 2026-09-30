"""Schema tests for the per-agent detail bundle (spec 38 Phase 4).

Covers the read side the per-agent Build tab consumes:

* ``agent(orgId:, slug:)`` — one ``kind: agent`` Workload's full bundle:
  identity + run-spec basics, primary-container image, definitional
  Brief (nullable), and attached Skills (ordered by AgentSkillRef
  position) with each skill's ToolDefs nested.

Every case binds a real tenant context + a controllable permission
resolver and invokes the resolver directly (the agents-test
convention). The database is real. Cross-tenant isolation is asserted
explicitly because the agent slug is only unique *within* an org.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from astrolift_agents.models import AgentSkillRef, Brief, Skill, ToolDef
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import Container, RegisteredApp, Workload
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def info():
    def _make(request=None):
        return SimpleNamespace(context=SimpleNamespace(user=None, request=request))

    return _make


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _org(slug):
    return Organization.objects.create(name=slug.replace("-", " ").title(), slug=slug)


def _project(org, slug):
    team = Team.objects.create(organization=org, name=f"{slug} team", slug=f"{slug}-team")
    return Project.objects.create(organization=org, team=team, name=slug.title(), slug=slug), team


def _app(org, project, team, slug, **kwargs):
    return RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name=slug.title(),
        slug=slug,
        provisioning_status="ready",
        **kwargs,
    )


def _agent_workload(app, slug, **kwargs):
    return Workload.objects.create(
        registered_app=app,
        name=slug.title(),
        slug=slug,
        kind=Workload.Kind.AGENT,
        **kwargs,
    )


def _skill(org, slug, **kwargs):
    return Skill.objects.create(
        organization=org,
        name=slug.title(),
        slug=slug,
        **kwargs,
    )


def _brief(org, content_hash, **kwargs):
    return Brief.objects.create(
        organization=org,
        content_hash=content_hash,
        **kwargs,
    )


def _attach_skill(workload, skill, *, position):
    return AgentSkillRef.objects.create(
        workload=workload,
        skill=skill,
        position=position,
    )


def _container(workload, name, *, is_primary, image_ref="", dockerfile_path="Dockerfile"):
    return Container.objects.create(
        workload=workload,
        name=name,
        is_primary=is_primary,
        image_ref=image_ref,
        dockerfile_path=dockerfile_path,
    )


def _seed_full_agent(org, app):
    """An agent Workload with a brief, two ordered skills (one carrying
    tool defs), and a primary + sidecar container. Returns the workload.

    Skills are attached out of position order to prove the resolver
    orders by ``AgentSkillRef.position`` (not insertion / pk order).
    """
    brief = _brief(
        org,
        "a" * 64,
        storage_key="briefs/acme/full.json",
        manifest_snapshot={"env": {"LOG_LEVEL": "info"}},
    )
    agent = _agent_workload(
        app,
        "triage-bot",
        brief=brief,
        run_mode=Workload.RunMode.SCHEDULE,
        run_cron_expression="0 9 * * *",
        run_paused=True,
    )

    # Skill with two ToolDefs — attach at position 1 (the SECOND slot).
    skill_tools = _skill(org, "code-review")
    ToolDef.objects.create(skill=skill_tools, name="Run Gh", slug="gh", adapter=ToolDef.Adapter.PYTHON_FN)
    ToolDef.objects.create(skill=skill_tools, name="Run Git", slug="git", adapter=ToolDef.Adapter.PYTHON_FN)
    # Toolless skill — attach at position 0 (the FIRST slot).
    skill_plain = _skill(org, "infra-ops")

    # Insert refs in REVERSE position order so insertion order != position.
    _attach_skill(agent, skill_tools, position=1)
    _attach_skill(agent, skill_plain, position=0)

    # Primary container carries the image; a sidecar must be ignored.
    _container(agent, "sidecar", is_primary=False, image_ref="sidecar:latest")
    _container(
        agent,
        "app",
        is_primary=True,
        image_ref="ghcr.io/acme/triage:1.2.3",
        dockerfile_path="agent.Dockerfile",
    )
    return agent


# ---------------------------------------------------------------------------
# agent(slug) — happy path
# ---------------------------------------------------------------------------


def test_agent_returns_full_bundle(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello", source_repo="acme/agents")
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _seed_full_agent(org, app)

    with with_tenant_org(org):
        detail = AgentsQuery().agent(info(), org_id=str(org.guid), slug="triage-bot")

    assert detail is not None
    # Identity + run-spec basics.
    assert str(detail.id) == str(agent.guid)
    assert detail.name == "Triage-Bot"
    assert detail.slug == "triage-bot"
    assert detail.app_slug == "hello"
    assert detail.source_repo == "acme/agents"
    assert detail.run_family == "task"
    assert detail.run_mode == "schedule"
    assert detail.run_paused is True
    assert detail.run_cron_expression == "0 9 * * *"

    # Primary-container image (sidecar ignored).
    assert detail.image_ref == "ghcr.io/acme/triage:1.2.3"
    assert detail.dockerfile_path == "agent.Dockerfile"

    # Brief.
    assert detail.brief is not None
    assert detail.brief.content_hash == "a" * 64
    assert detail.brief.config == {"env": {"LOG_LEVEL": "info"}}


def test_agent_skills_ordered_by_position(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    _seed_full_agent(org, app)

    with with_tenant_org(org):
        detail = AgentsQuery().agent(info(), org_id=str(org.guid), slug="triage-bot")

    # Two skills, ordered by AgentSkillRef.position (0 then 1) — NOT by
    # insertion order (the tools skill was inserted first at position 1).
    assert [s.position for s in detail.skills] == [0, 1]
    assert [s.skill.slug for s in detail.skills] == ["infra-ops", "code-review"]


def test_agent_tool_defs_nested_under_skill(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    _seed_full_agent(org, app)

    with with_tenant_org(org):
        detail = AgentsQuery().agent(info(), org_id=str(org.guid), slug="triage-bot")

    by_slug = {s.skill.slug: s for s in detail.skills}
    # Toolless skill → empty tool list.
    assert by_slug["infra-ops"].tool_defs == []
    # Tool-bearing skill → both tools, ordered by slug.
    review_tools = [t.slug for t in by_slug["code-review"].tool_defs]
    assert review_tools == ["gh", "git"]


def test_agent_graceful_when_no_brief_no_skills(permission_resolver, info, with_tenant_org):
    """An agent registered before a Brief is assembled and with no skills
    attached still resolves — with null brief + empty skills + empty
    image, not an error."""
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    _agent_workload(app, "bare-bot")

    with with_tenant_org(org):
        detail = AgentsQuery().agent(info(), org_id=str(org.guid), slug="bare-bot")

    assert detail is not None
    assert detail.slug == "bare-bot"
    assert detail.brief is None
    assert detail.skills == []
    # No container row yet → empty image coordinates (not a crash).
    assert detail.image_ref == ""
    assert detail.dockerfile_path == ""


def test_agent_unknown_slug_returns_null(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)

    with with_tenant_org(org):
        detail = AgentsQuery().agent(info(), org_id=str(org.guid), slug="does-not-exist")

    assert detail is None


def test_agent_task_non_uuid_id_returns_null_not_500(permission_resolver, info, with_tenant_org):
    """A non-UUID ``[task]`` route param (e.g. ``/agents/runs/overview``) must
    resolve to None, not raise a UUIDField ValidationError -> HTTP 500
    ("'overview' is not a valid UUID"). Regression for the broken run-detail page."""
    org = _org("acme")
    permission_resolver.grant(Permission.APP_READ)

    with with_tenant_org(org):
        result = AgentsQuery().agent_task(info(), id="overview")

    assert result is None


def test_agent_ignores_non_agent_workload(permission_resolver, info, with_tenant_org):
    """A deployment workload sharing the slug must not resolve as an
    agent — the query is scoped to ``kind: agent``."""
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    Workload.objects.create(registered_app=app, name="Web", slug="web-svc", kind=Workload.Kind.DEPLOYMENT)

    with with_tenant_org(org):
        detail = AgentsQuery().agent(info(), org_id=str(org.guid), slug="web-svc")

    assert detail is None


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


def test_agent_cross_tenant_returns_null(permission_resolver, info, with_tenant_org):
    """A caller in org A asking for org B's agent slug gets null — never
    another tenant's agent — even though both orgs reuse the same slug."""
    org_a = _org("org-a")
    org_b = _org("org-b")
    proj_a, team_a = _project(org_a, "shared-slug")
    proj_b, team_b = _project(org_b, "shared-slug")
    app_a = _app(org_a, proj_a, team_a, "shared-app")
    app_b = _app(org_b, proj_b, team_b, "shared-app")
    permission_resolver.grant(Permission.AGENT_READ)
    # Both orgs have an agent with the SAME slug.
    _agent_workload(app_a, "shared-bot")
    _seed_full_agent(org_b, app_b)  # org B's "triage-bot" is fully populated
    _agent_workload(app_b, "shared-bot")

    with with_tenant_org(org_a):
        # org A has its own "shared-bot" — that one resolves.
        own = AgentsQuery().agent(info(), org_id=str(org_a.guid), slug="shared-bot")
        # org B's "triage-bot" is invisible to org A.
        foreign = AgentsQuery().agent(info(), org_id=str(org_a.guid), slug="triage-bot")

    assert own is not None
    assert own.slug == "shared-bot"
    assert foreign is None


def test_agent_rejects_foreign_org_id(permission_resolver, info, with_tenant_org):
    """Passing another org's GUID as ``org_id`` is rejected outright (the
    same _caller_org_id gate the sibling resolvers enforce)."""
    from graphql import GraphQLError

    org_a = _org("org-a")
    org_b = _org("org-b")
    permission_resolver.grant(Permission.AGENT_READ)
    with with_tenant_org(org_a):
        with pytest.raises(GraphQLError):
            AgentsQuery().agent(info(), org_id=str(org_b.guid), slug="anything")


# ---------------------------------------------------------------------------
# Permission gate
# ---------------------------------------------------------------------------


def test_agent_requires_agent_read(info, with_tenant_org):
    """Without agent.read the resolver denies (the agent readers demand
    the standalone agent.read perm — spec 36 §0.4)."""
    org = _org("acme")
    with with_tenant_org(org):
        with pytest.raises(PermissionDenied) as exc_info:
            AgentsQuery().agent(info(), org_id=str(org.guid), slug="anything")
    assert exc_info.value.permission.value == "agent.read"


# ---------------------------------------------------------------------------
# No N+1
# ---------------------------------------------------------------------------


def test_agent_query_count_is_bounded(permission_resolver, info, with_tenant_org):
    """The bundle resolves in a bounded number of queries regardless of
    how many skills/tools the agent carries — select_related the brief +
    app, prefetch the skill→tool join + containers.

    The query budget covers the authoritative organization/managed-cluster
    lookup, the owner-workload gate, org resolution (_caller_org_id), the
    workload+brief+app row and the four skill/ref/tool/container prefetches.
    Adding more skills/tools must NOT increase it (the proof against a
    per-skill or per-tool N+1)."""
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)

    # Small fixture: 1 skill, 1 tool.
    brief = _brief(org, "b" * 64)
    small = _agent_workload(app, "small-bot", brief=brief)
    s1 = _skill(org, "s-one")
    ToolDef.objects.create(skill=s1, name="T1", slug="t-one")
    _attach_skill(small, s1, position=0)
    _container(small, "app", is_primary=True, image_ref="img:small")

    # Large fixture: 5 skills, each with 3 tools.
    brief2 = _brief(org, "c" * 64)
    large = _agent_workload(app, "large-bot", brief=brief2)
    for i in range(5):
        sk = _skill(org, f"big-skill-{i}")
        for j in range(3):
            ToolDef.objects.create(skill=sk, name=f"T{i}{j}", slug=f"t-{i}-{j}")
        _attach_skill(large, sk, position=i)
    _container(large, "app", is_primary=True, image_ref="img:large")

    with with_tenant_org(org):
        with CaptureQueriesContext(connection) as small_ctx:
            AgentsQuery().agent(info(), org_id=str(org.guid), slug="small-bot")
        with CaptureQueriesContext(connection) as large_ctx:
            AgentsQuery().agent(info(), org_id=str(org.guid), slug="large-bot")

    # The whole point of the prefetch: the 5-skill/15-tool agent costs the
    # SAME number of queries as the 1-skill/1-tool agent. If a per-skill or
    # per-tool N+1 regressed in, the large count would jump.
    assert len(large_ctx) == len(small_ctx), (
        f"N+1 regression: small={len(small_ctx)} large={len(large_ctx)} "
        f"queries\nlarge:\n" + "\n".join(q["sql"] for q in large_ctx.captured_queries)
    )
    # And the absolute budget stays tight (sanity bound).
    assert len(small_ctx) <= 9
