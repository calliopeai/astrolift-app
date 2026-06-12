"""Schema tests for astrolift_agents — Skill CRUD, AgentTask dispatch,
AgentEnvironmentSpec CRUD, and Brief queries (#864).

Coverage:
* skills query returns only skills for the org (plus global ones).
* skill query returns None for a missing slug.
* createSkill creates and returns a skill; duplicate slug returns CONFLICT.
* updateSkill changes only supplied fields; missing id returns NOT_FOUND.
* deleteSkill soft-deletes; missing id returns NOT_FOUND.
* agentTasks returns only tasks for the current org.
* agentTask returns None for a task in another org.
* enqueueAgentTask creates a task in QUEUED status.
* cancelAgentTask transitions a queued task to CANCELLED.
* cancelAgentTask returns PRECONDITION for a terminal task.
* agentEnvironmentSpecs returns only specs for the current org.
* agentEnvironmentSpec returns None for a spec in another org.
* createAgentEnvironmentSpec creates a spec.
* updateAgentEnvironmentSpec changes only supplied fields.
* deleteAgentEnvironmentSpec soft-deletes.
* agentBriefs returns only briefs for the current org.
* agentBrief returns None for a brief in another org.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Brief, Skill
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    CreateAgentEnvironmentSpecInput,
    CreateSkillInput,
    EnqueueAgentTaskInput,
    UpdateAgentEnvironmentSpecInput,
    UpdateSkillInput,
)
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Agent Org", slug="agent-org-schema")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Other Org", slug="other-org-schema")


@pytest.fixture
def user(org):
    return User.objects.create_superuser(
        username="agent-admin",
        email="admin@agents.test",
        password="x",
    )


@pytest.fixture
def skill(org):
    return Skill.objects.create(
        organization=org,
        name="Deploy Helper",
        slug="deploy-helper",
        content="deploy everything",
        agent_type="claude",
    )


@pytest.fixture
def other_skill(other_org):
    return Skill.objects.create(
        organization=other_org,
        name="Rival Skill",
        slug="rival-skill",
        agent_type="claude",
    )


@pytest.fixture
def global_skill():
    return Skill.objects.create(
        organization=None,
        name="Global Helper",
        slug="global-helper",
        is_global=True,
    )


@pytest.fixture
def draft_task(org):
    return AgentTask.objects.create(organization=org)


@pytest.fixture
def other_task(other_org):
    return AgentTask.objects.create(organization=other_org)


@pytest.fixture
def env_spec(org):
    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Claude Dev",
        slug="claude-dev",
        image_tag="123456789.dkr.ecr.us-west-2.amazonaws.com/agents:latest",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
    )


@pytest.fixture
def other_spec(other_org):
    return AgentEnvironmentSpec.objects.create(
        organization=other_org,
        name="Other Spec",
        slug="other-spec",
        image_tag="123456789.dkr.ecr.us-west-2.amazonaws.com/agents:other",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
    )


@pytest.fixture
def brief(org):
    return Brief.objects.create(
        organization=org,
        content_hash="a" * 64,
        status=Brief.Status.READY,
        assembled_at=None,
    )


@pytest.fixture
def other_brief(other_org):
    return Brief.objects.create(
        organization=other_org,
        content_hash="b" * 64,
        status=Brief.Status.READY,
    )


def _info(user):
    from types import SimpleNamespace

    return SimpleNamespace(
        context=SimpleNamespace(request=SimpleNamespace(user=user, auth=None))
    )


def _ctx(user, org):
    """Tenant context with the user as actor (superuser bypasses RBAC)."""
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


# ---------------------------------------------------------------------------
# skills query
# ---------------------------------------------------------------------------


def test_skills_returns_own_org_skills(user, org, skill, other_skill):
    query = AgentsQuery()
    with _ctx(user, org):
        results = query.skills(_info(user))
    slugs = {r.slug for r in results}
    assert skill.slug in slugs
    assert other_skill.slug not in slugs


def test_skills_includes_global_skills(user, org, skill, global_skill):
    query = AgentsQuery()
    with _ctx(user, org):
        results = query.skills(_info(user))
    slugs = {r.slug for r in results}
    assert global_skill.slug in slugs


def test_skills_excludes_global_when_flag_false(user, org, global_skill):
    query = AgentsQuery()
    with _ctx(user, org):
        results = query.skills(_info(user), include_global=False)
    slugs = {r.slug for r in results}
    assert global_skill.slug not in slugs


# ---------------------------------------------------------------------------
# skill query
# ---------------------------------------------------------------------------


def test_skill_returns_none_for_missing_slug(user, org):
    query = AgentsQuery()
    with _ctx(user, org):
        result = query.skill(_info(user), slug="nonexistent-slug")
    assert result is None


def test_skill_returns_own_org_skill(user, org, skill):
    query = AgentsQuery()
    with _ctx(user, org):
        result = query.skill(_info(user), slug=skill.slug)
    assert result is not None
    assert result.slug == skill.slug


def test_skill_does_not_return_other_org_skill(user, org, other_skill):
    query = AgentsQuery()
    with _ctx(user, org):
        result = query.skill(_info(user), slug=other_skill.slug)
    assert result is None


# ---------------------------------------------------------------------------
# createSkill
# ---------------------------------------------------------------------------


def test_create_skill_happy_path(user, org):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.create_skill(
            _info(user),
            input=CreateSkillInput(
                name="Code Review",
                slug="code-review",
                content="review the code",
                agent_type="claude",
            ),
        )
    assert result.ok, result.errors
    assert result.data is not None
    assert result.data.slug == "code-review"
    assert result.data.name == "Code Review"
    assert Skill.objects.filter(organization=org, slug="code-review").exists()


def test_create_skill_missing_name_returns_validation_error(user, org):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.create_skill(
            _info(user),
            input=CreateSkillInput(name="", slug="some-slug"),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "name"


def test_create_skill_duplicate_slug_returns_conflict(user, org, skill):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.create_skill(
            _info(user),
            input=CreateSkillInput(name="Another Skill", slug=skill.slug),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"


# ---------------------------------------------------------------------------
# updateSkill
# ---------------------------------------------------------------------------


def test_update_skill_changes_fields(user, org, skill):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.update_skill(
            _info(user),
            id=str(skill.guid),
            input=UpdateSkillInput(name="Updated Name", content="updated content"),
        )
    assert result.ok, result.errors
    assert result.data.name == "Updated Name"
    skill.refresh_from_db()
    assert skill.name == "Updated Name"
    assert skill.content == "updated content"


def test_update_skill_not_found_for_other_org(user, org, other_skill):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.update_skill(
            _info(user),
            id=str(other_skill.guid),
            input=UpdateSkillInput(name="Hacked"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# deleteSkill
# ---------------------------------------------------------------------------


def test_delete_skill_soft_deletes(user, org, skill):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.delete_skill(_info(user), id=str(skill.guid))
    assert result.ok, result.errors
    skill.refresh_from_db()
    assert skill.deleted_at is not None


def test_delete_skill_not_found_for_other_org(user, org, other_skill):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.delete_skill(_info(user), id=str(other_skill.guid))
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# enqueueAgentTask
# ---------------------------------------------------------------------------


def test_enqueue_agent_task_creates_queued_task(user, org):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.enqueue_agent_task(
            _info(user),
            input=EnqueueAgentTaskInput(timeout_seconds=600, source_ref="acme/app@abc123"),
        )
    assert result.ok, result.errors
    assert result.data is not None
    assert result.data.status == "queued"
    task = AgentTask.objects.filter(organization=org, status=AgentTask.Status.QUEUED).first()
    assert task is not None
    assert task.queued_at is not None
    assert task.source_ref == "acme/app@abc123"


# ---------------------------------------------------------------------------
# cancelAgentTask
# ---------------------------------------------------------------------------


def test_cancel_agent_task_transitions_to_cancelled(user, org, draft_task):
    draft_task.transition_to(AgentTask.Status.QUEUED)
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.cancel_agent_task(_info(user), id=str(draft_task.guid))
    assert result.ok, result.errors
    assert result.data.status == "cancelled"
    draft_task.refresh_from_db()
    assert draft_task.status == AgentTask.Status.CANCELLED
    assert draft_task.ended_at is not None


def test_cancel_agent_task_terminal_task_returns_precondition(user, org, draft_task):
    draft_task.transition_to(AgentTask.Status.QUEUED)
    draft_task.transition_to(AgentTask.Status.PROVISIONING)
    draft_task.transition_to(AgentTask.Status.RUNNING)
    draft_task.transition_to(AgentTask.Status.COMPLETED)
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.cancel_agent_task(_info(user), id=str(draft_task.guid))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_cancel_agent_task_not_found_for_other_org(user, org, other_task):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.cancel_agent_task(_info(user), id=str(other_task.guid))
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# agentTasks query — tenant isolation
# ---------------------------------------------------------------------------


def test_agent_tasks_returns_only_own_org(user, org, draft_task, other_task):
    query = AgentsQuery()
    with _ctx(user, org):
        results = query.agent_tasks(_info(user))
    ids = {r.id for r in results}
    assert str(draft_task.guid) in ids
    assert str(other_task.guid) not in ids


def test_agent_task_single_is_tenant_scoped(user, org, other_task):
    query = AgentsQuery()
    with _ctx(user, org):
        result = query.agent_task(_info(user), id=str(other_task.guid))
    assert result is None


# ---------------------------------------------------------------------------
# AgentEnvironmentSpec queries
# ---------------------------------------------------------------------------


def test_agent_environment_specs_returns_only_own_org(user, org, env_spec, other_spec):
    query = AgentsQuery()
    with _ctx(user, org):
        results = query.agent_environment_specs(_info(user))
    slugs = {r.slug for r in results}
    assert env_spec.slug in slugs
    assert other_spec.slug not in slugs


def test_agent_environment_spec_single_is_tenant_scoped(user, org, other_spec):
    query = AgentsQuery()
    with _ctx(user, org):
        result = query.agent_environment_spec(_info(user), slug=other_spec.slug)
    assert result is None


# ---------------------------------------------------------------------------
# createAgentEnvironmentSpec
# ---------------------------------------------------------------------------


def test_create_agent_environment_spec_happy_path(user, org):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.create_agent_environment_spec(
            _info(user),
            input=CreateAgentEnvironmentSpecInput(
                name="Test Spec",
                slug="test-spec",
                image_tag="123.dkr.ecr.us-west-2.amazonaws.com/agents:v1",
                agent_type="claude",
                tool_preset="dev",
            ),
        )
    assert result.ok, result.errors
    assert result.data is not None
    assert result.data.slug == "test-spec"
    assert result.data.agent_type == "claude"
    assert AgentEnvironmentSpec.objects.filter(organization=org, slug="test-spec").exists()


def test_create_agent_environment_spec_invalid_agent_type(user, org):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.create_agent_environment_spec(
            _info(user),
            input=CreateAgentEnvironmentSpecInput(
                name="Bad Spec",
                slug="bad-spec",
                image_tag="123.dkr.ecr.us-west-2.amazonaws.com/agents:v1",
                agent_type="gpt-99",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "agentType"


def test_agent_environment_spec_type_exposes_count_not_refs():
    """Regression guard: the GraphQL type must surface only the COUNT of
    secret references, never the references (ARNs / vault URIs) themselves.
    Leaking the pointer helps an attacker locate the secret even though the
    value never lives on the row. See the agent-platform foundation work.
    """
    from astrolift_agents.schema.types import (
        AgentEnvironmentSpecType,
        agent_environment_spec_to_type,
    )

    # The type's GraphQL fields must include the count and must NOT include
    # a raw secret_refs / secretRefs field.
    field_names = {f.name for f in AgentEnvironmentSpecType.__strawberry_definition__.fields}
    assert "secret_ref_count" in field_names
    assert "secret_refs" not in field_names
    assert "secretRefs" not in field_names

    # The shaper returns the count, and the shaped object carries no
    # attribute exposing the references.
    spec = AgentEnvironmentSpec(
        name="Has Secrets",
        slug="has-secrets",
        image_tag="img:v1",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        secret_refs=[
            {"uri": "arn:aws:secretsmanager:us-west-2:1:secret:gh", "env_var": "GH_TOKEN"},
            {"uri": "vault://secret/db", "env_var": "DB_PASSWORD"},
        ],
    )
    shaped = agent_environment_spec_to_type(spec)
    assert shaped.secret_ref_count == 2
    assert not hasattr(shaped, "secret_refs")


def test_create_agent_environment_spec_returns_secret_ref_count(user, org):
    """End-to-end: creating a spec with secret refs returns the count, and
    the refs are persisted on the row for the dispatcher (never surfaced)."""
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.create_agent_environment_spec(
            _info(user),
            input=CreateAgentEnvironmentSpecInput(
                name="Secretful",
                slug="secretful",
                image_tag="123.dkr.ecr.us-west-2.amazonaws.com/agents:v1",
                agent_type="claude",
                secret_refs=[
                    {"uri": "arn:aws:secretsmanager:us-west-2:1:secret:gh", "env_var": "GH_TOKEN"},
                ],
            ),
        )
    assert result.ok, result.errors
    assert result.data.secret_ref_count == 1
    # The references are stored on the row (the dispatcher needs them) but
    # the GraphQL surface only ever returns the count.
    spec = AgentEnvironmentSpec.objects.get(organization=org, slug="secretful")
    assert len(spec.secret_refs) == 1


def test_create_agent_environment_spec_duplicate_slug_returns_conflict(user, org, env_spec):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.create_agent_environment_spec(
            _info(user),
            input=CreateAgentEnvironmentSpecInput(
                name="Duplicate",
                slug=env_spec.slug,
                image_tag="123.dkr.ecr.us-west-2.amazonaws.com/agents:v2",
                agent_type="claude",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"


# ---------------------------------------------------------------------------
# updateAgentEnvironmentSpec
# ---------------------------------------------------------------------------


def test_update_agent_environment_spec_changes_fields(user, org, env_spec):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.update_agent_environment_spec(
            _info(user),
            slug=env_spec.slug,
            input=UpdateAgentEnvironmentSpecInput(
                name="Claude Dev v2",
                allow_install=True,
            ),
        )
    assert result.ok, result.errors
    assert result.data.name == "Claude Dev v2"
    assert result.data.allow_install is True
    env_spec.refresh_from_db()
    assert env_spec.name == "Claude Dev v2"
    assert env_spec.allow_install is True


def test_update_agent_environment_spec_not_found_for_other_org(user, org, other_spec):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.update_agent_environment_spec(
            _info(user),
            slug=other_spec.slug,
            input=UpdateAgentEnvironmentSpecInput(name="Hacked"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# deleteAgentEnvironmentSpec
# ---------------------------------------------------------------------------


def test_delete_agent_environment_spec_soft_deletes(user, org, env_spec):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.delete_agent_environment_spec(_info(user), slug=env_spec.slug)
    assert result.ok, result.errors
    env_spec.refresh_from_db()
    assert env_spec.deleted_at is not None


def test_delete_agent_environment_spec_not_found_for_other_org(user, org, other_spec):
    mutation = AgentsMutation()
    with _ctx(user, org):
        result = mutation.delete_agent_environment_spec(_info(user), slug=other_spec.slug)
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# agentBriefs query — tenant isolation
# ---------------------------------------------------------------------------


def test_agent_briefs_returns_only_own_org(user, org, brief, other_brief):
    query = AgentsQuery()
    with _ctx(user, org):
        results = query.agent_briefs(_info(user))
    hashes = {r.content_hash for r in results}
    assert brief.content_hash in hashes
    assert other_brief.content_hash not in hashes


def test_agent_brief_single_is_tenant_scoped(user, org, other_brief):
    query = AgentsQuery()
    with _ctx(user, org):
        result = query.agent_brief(_info(user), id=str(other_brief.guid))
    assert result is None
