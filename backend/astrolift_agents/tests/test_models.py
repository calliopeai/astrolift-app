"""Model-level tests for astrolift_agents (#41, #42).

Runs against real Postgres (pytest-django + DATABASES from settings).
No mocks.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from astrolift_agents.models import AgentSkillRef, Brief, BriefSkillRef, Skill, ToolDef
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Silence the User -> Profile -> OpenSearch indexing chain."""
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
    return Organization.objects.create(name="TestOrg", slug="test-org-agents")


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_brief_can_be_created(org):
    brief = Brief.objects.create(
        organization=org,
        content_hash="a" * 64,
        manifest_snapshot={"env": {"FOO": "bar"}},
        secrets_refs=[{"name": "DB_PASSWORD", "ref": "vault://secret/db"}],
        context={"actor": "user-guid-123", "project": "my-project"},
    )
    assert brief.pk is not None
    assert brief.status == Brief.Status.ASSEMBLING
    assert brief.ttl_seconds == 3600


@pytest.mark.django_db(transaction=True)
def test_brief_content_hash_is_unique(org):
    hash_val = "b" * 64
    Brief.objects.create(organization=org, content_hash=hash_val)
    with pytest.raises(IntegrityError):
        Brief.objects.create(organization=org, content_hash=hash_val)


@pytest.mark.django_db(transaction=True)
def test_brief_str(org):
    brief = Brief.objects.create(organization=org, content_hash="c" * 64)
    assert "assembling" in str(brief)
    assert "c" * 12 in str(brief)


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_skill_can_be_created_with_org_scope(org):
    skill = Skill.objects.create(
        organization=org,
        name="My Skill",
        slug="my-skill",
        content="echo hello",
        dependencies=["requests==2.32.0"],
    )
    assert skill.pk is not None
    assert skill.skill_version == 1
    assert not skill.is_global


@pytest.mark.django_db(transaction=True)
def test_global_skill_has_no_org():
    skill = Skill.objects.create(
        organization=None,
        name="Global Skill",
        slug="global-skill",
        is_global=True,
    )
    assert skill.organization_id is None
    assert skill.is_global


@pytest.mark.django_db(transaction=True)
def test_skill_slug_unique_active_per_org(org):
    Skill.objects.create(organization=org, name="Skill A", slug="skill-a")
    with pytest.raises(IntegrityError):
        Skill.objects.create(organization=org, name="Skill A2", slug="skill-a")


# ---------------------------------------------------------------------------
# ToolDef
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_tooldef_belongs_to_skill(org):
    skill = Skill.objects.create(organization=org, name="Tool Skill", slug="tool-skill")
    tool = ToolDef.objects.create(
        skill=skill,
        name="Run Script",
        slug="run-script",
        input_schema={"type": "object", "properties": {"cmd": {"type": "string"}}},
        output_schema={"type": "object", "properties": {"exit_code": {"type": "integer"}}},
        adapter=ToolDef.Adapter.PYTHON_FN,
        handler_ref="astrolift_agents.handlers.run_script",
    )
    assert tool.skill == skill
    assert tool.input_schema["type"] == "object"
    assert tool.adapter == "python_fn"


@pytest.mark.django_db(transaction=True)
def test_tooldef_input_schema_stores_json_schema(org):
    skill = Skill.objects.create(organization=org, name="Schema Skill", slug="schema-skill")
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "timeout": {"type": "integer"},
        },
        "required": ["url"],
    }
    tool = ToolDef.objects.create(
        skill=skill,
        name="Fetch URL",
        slug="fetch-url",
        input_schema=schema,
        adapter=ToolDef.Adapter.HTTP_ENDPOINT,
        handler_ref="https://tools.internal/fetch",
    )
    tool.refresh_from_db()
    assert tool.input_schema == schema


# ---------------------------------------------------------------------------
# BriefSkillRef
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_briefskillref_links_brief_to_skill(org):
    brief = Brief.objects.create(organization=org, content_hash="d" * 64)
    skill = Skill.objects.create(organization=org, name="Linked Skill", slug="linked-skill")
    ref = BriefSkillRef.objects.create(brief=brief, skill=skill, skill_version=1)
    assert ref.brief == brief
    assert ref.skill == skill
    assert ref.skill_version == 1


# ---------------------------------------------------------------------------
# AgentSkillRef + Workload.brief (spec 38, Phase 2)
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_workload(org):
    """A ``Workload(kind=agent)`` plus its required app/team/project chain."""
    team = Team.objects.create(organization=org, name="Eng", slug="eng-skillref")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug="demo-skillref"
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Agent App",
        slug="agent-app-skillref",
        k8s_namespace="org-agent-app",
    )
    return Workload.objects.create(
        registered_app=app,
        name="my-agent",
        slug="my-agent",
        kind=Workload.Kind.AGENT,
    )


@pytest.mark.django_db(transaction=True)
def test_agentskillref_links_workload_to_skill(org, agent_workload):
    skill = Skill.objects.create(organization=org, name="Deploy Skill", slug="deploy-skill")
    ref = AgentSkillRef.objects.create(workload=agent_workload, skill=skill, position=2)

    assert ref.workload == agent_workload
    assert ref.skill == skill
    assert ref.position == 2
    # Reverse relations resolve in both directions.
    assert list(agent_workload.agent_skill_refs.all()) == [ref]
    assert list(skill.agent_refs.all()) == [ref]


@pytest.mark.django_db(transaction=True)
def test_agentskillref_position_defaults_to_zero(org, agent_workload):
    skill = Skill.objects.create(organization=org, name="Zero Skill", slug="zero-skill")
    ref = AgentSkillRef.objects.create(workload=agent_workload, skill=skill)
    assert ref.position == 0


@pytest.mark.django_db(transaction=True)
def test_agentskillref_unique_per_workload(org, agent_workload):
    skill = Skill.objects.create(organization=org, name="Dup Skill", slug="dup-skill")
    AgentSkillRef.objects.create(workload=agent_workload, skill=skill)
    # The same (workload, skill) pair is rejected regardless of position.
    with pytest.raises(IntegrityError):
        AgentSkillRef.objects.create(workload=agent_workload, skill=skill, position=9)


@pytest.mark.django_db(transaction=True)
def test_workload_brief_fk_is_settable_and_nullable(org, agent_workload):
    # Defaults to null (existing rows / non-agent workloads carry no brief).
    assert agent_workload.brief_id is None

    brief = Brief.objects.create(organization=org, content_hash="e" * 64)
    agent_workload.brief = brief
    agent_workload.save(update_fields=["brief", "updated_at", "version"])
    agent_workload.refresh_from_db()

    assert agent_workload.brief == brief
    # Reverse accessor resolves.
    assert list(brief.agent_workloads.all()) == [agent_workload]


@pytest.mark.django_db(transaction=True)
def test_workload_brief_set_null_on_brief_delete(org, agent_workload):
    """Hard-deleting the Brief row nulls the FK, leaving the workload registered."""
    brief = Brief.objects.create(organization=org, content_hash="f" * 64)
    agent_workload.brief = brief
    agent_workload.save(update_fields=["brief", "updated_at", "version"])

    # Hard delete (DB-level on_delete=SET_NULL behaviour, not the soft-delete manager).
    Brief.all_objects.filter(pk=brief.pk).delete()
    agent_workload.refresh_from_db()

    assert agent_workload.brief_id is None
