"""Schema tests for the AgentEnvironmentSpec GraphQL surface.

AgentEnvironmentSpec is a reusable, org-scoped recipe for the container
environment an agent task runs in (image/runtime/tool preset/capability
toggles + secret *references*). This module covers its read + CRUD
resolvers; Skill/ToolDef coverage lives in ``test_skill_schema.py`` /
``test_tool_registry_schema.py``, Task coverage in
``test_task_state_machine.py``, and Brief coverage in
``test_brief_assembler.py``.

Resolvers are exercised by direct invocation (the agents-test
convention) with a controllable permission resolver + tenant context
bound. The database is real.

Coverage:
* agentEnvironmentSpecs returns only specs for the caller's org.
* agentEnvironmentSpecs rejects an org_id that isn't the active tenant.
* agentEnvironmentSpec returns None for a spec in another org.
* createAgentEnvironmentSpec creates a spec (happy path).
* createAgentEnvironmentSpec rejects an unknown agent_type (VALIDATION).
* createAgentEnvironmentSpec rejects a duplicate slug (CONFLICT).
* createAgentEnvironmentSpec stores secret_refs as references, not values.
* createAgentEnvironmentSpec requires agent_env_spec.create.
* updateAgentEnvironmentSpec changes only supplied fields.
* updateAgentEnvironmentSpec is NOT_FOUND for another org's spec.
* deleteAgentEnvironmentSpec soft-deletes.
* deleteAgentEnvironmentSpec is NOT_FOUND for another org's spec.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    CreateAgentEnvironmentSpecInput,
    UpdateAgentEnvironmentSpecInput,
)
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.api_tokens import (
    SCOPE_AGENT_ENV_SPEC_WRITE,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import Organization
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
def org():
    return Organization.objects.create(name="Env Spec Org", slug="env-spec-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Env Spec Other", slug="env-spec-other")


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _grant_crud(resolver):
    for p in (
        Permission.AGENT_ENV_SPEC_READ,
        Permission.AGENT_ENV_SPEC_CREATE,
        Permission.AGENT_ENV_SPEC_UPDATE,
        Permission.AGENT_ENV_SPEC_DELETE,
    ):
        resolver.grant(p)


def _mk_spec(org, slug, *, agent_type=AgentEnvironmentSpec.AgentType.CLAUDE, **kwargs):
    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name=slug.replace("-", " ").title(),
        slug=slug,
        agent_type=agent_type,
        image_tag=kwargs.pop("image_tag", "123.dkr.ecr.us-west-2.amazonaws.com/agents:latest"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# agentEnvironmentSpecs / agentEnvironmentSpec queries
# ---------------------------------------------------------------------------


def test_specs_returns_only_own_org(permission_resolver, info, org, other_org, with_tenant_org):
    _grant_crud(permission_resolver)
    _mk_spec(org, "claude-dev")
    _mk_spec(other_org, "their-spec")

    with with_tenant_org(org):
        rows = AgentsQuery().agent_environment_specs(info(), org_id=str(org.guid))

    slugs = {r.slug for r in rows}
    assert "claude-dev" in slugs
    assert "their-spec" not in slugs


def test_specs_rejects_foreign_org_id(permission_resolver, info, org, other_org, with_tenant_org):
    """Passing another org's GUID while scoped to ``org`` must be
    rejected — a caller can't read a foreign tenant's specs by GUID."""
    from graphql import GraphQLError

    _grant_crud(permission_resolver)
    with with_tenant_org(org):
        with pytest.raises(GraphQLError):
            AgentsQuery().agent_environment_specs(info(), org_id=str(other_org.guid))


def test_spec_detail_cross_tenant_returns_null(permission_resolver, info, org, other_org, with_tenant_org):
    _grant_crud(permission_resolver)
    _mk_spec(other_org, "secret-spec")

    with with_tenant_org(org):
        result = AgentsQuery().agent_environment_spec(info(), slug="secret-spec")

    assert result is None


def test_spec_detail_returns_own(permission_resolver, info, org, with_tenant_org):
    _grant_crud(permission_resolver)
    spec = _mk_spec(org, "claude-dev")

    with with_tenant_org(org):
        result = AgentsQuery().agent_environment_spec(info(), slug="claude-dev")

    assert result is not None
    assert str(result.id) == str(spec.guid)
    assert result.slug == "claude-dev"


def test_specs_requires_env_spec_read(info, org, with_tenant_org):
    with with_tenant_org(org):
        with pytest.raises(PermissionDenied) as exc_info:
            AgentsQuery().agent_environment_specs(info(), org_id=str(org.guid))
    assert exc_info.value.permission == Permission.AGENT_ENV_SPEC_READ


# ---------------------------------------------------------------------------
# createAgentEnvironmentSpec
# ---------------------------------------------------------------------------


def test_create_spec_happy_path(permission_resolver, info, org, with_tenant_org):
    _grant_crud(permission_resolver)
    with with_tenant_org(org):
        result = AgentsMutation().create_agent_environment_spec(
            info(),
            input=CreateAgentEnvironmentSpecInput(
                name="Test Spec",
                slug="test-spec",
                agent_type="claude",
                image_tag="123.dkr.ecr.us-west-2.amazonaws.com/agents:v1",
                tool_preset="dev",
                vnc_enabled=True,
            ),
            org_id=str(org.guid),
        )
    assert result.ok, result.errors
    assert result.data is not None
    assert result.data.slug == "test-spec"
    assert result.data.agent_type == "claude"
    assert result.data.tool_preset == "dev"
    assert result.data.vnc_enabled is True
    spec = AgentEnvironmentSpec.objects.get(organization=org, slug="test-spec")
    assert spec.config_branch == "main"  # default applied


def test_create_spec_invalid_agent_type(permission_resolver, info, org, with_tenant_org):
    _grant_crud(permission_resolver)
    with with_tenant_org(org):
        result = AgentsMutation().create_agent_environment_spec(
            info(),
            input=CreateAgentEnvironmentSpecInput(
                name="Bad Spec",
                slug="bad-spec",
                agent_type="gpt-99",
            ),
            org_id=str(org.guid),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "agentType"


def test_create_spec_duplicate_slug_returns_conflict(permission_resolver, info, org, with_tenant_org):
    _grant_crud(permission_resolver)
    _mk_spec(org, "claude-dev")
    with with_tenant_org(org):
        result = AgentsMutation().create_agent_environment_spec(
            info(),
            input=CreateAgentEnvironmentSpecInput(
                name="Duplicate",
                slug="claude-dev",
                agent_type="claude",
            ),
            org_id=str(org.guid),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"


def test_create_spec_missing_name_returns_validation(permission_resolver, info, org, with_tenant_org):
    _grant_crud(permission_resolver)
    with with_tenant_org(org):
        result = AgentsMutation().create_agent_environment_spec(
            info(),
            input=CreateAgentEnvironmentSpecInput(name="", slug="x", agent_type="claude"),
            org_id=str(org.guid),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "name"


def test_create_spec_stores_secret_refs_not_values(permission_resolver, info, org, with_tenant_org):
    """The spec must persist secret *references* (uri + env_var), never
    secret values — the dispatcher resolves them at launch time."""
    _grant_crud(permission_resolver)
    refs = [{"uri": "arn:aws:secretsmanager:us-west-2:1:secret:gh", "env_var": "GITHUB_TOKEN"}]
    with with_tenant_org(org):
        result = AgentsMutation().create_agent_environment_spec(
            info(),
            input=CreateAgentEnvironmentSpecInput(
                name="With Secret",
                slug="with-secret",
                agent_type="claude",
                secret_refs=refs,
            ),
            org_id=str(org.guid),
        )
    assert result.ok, result.errors
    spec = AgentEnvironmentSpec.objects.get(organization=org, slug="with-secret")
    assert spec.secret_refs == refs


@pytest.mark.parametrize(
    ("input_kwargs", "message"),
    [
        ({"env_vars": []}, "envVars must be an object"),
        ({"env_vars": {"BAD-NAME": "x"}}, "invalid environment variable name"),
        ({"env_vars": {"AGENT_CALLBACK_URL": "https://attacker.invalid"}}, "dispatcher-owned"),
        ({"env_vars": {"NESTED": {"x": 1}}}, "must be a scalar or null"),
        ({"secret_refs": {}}, "secretRefs must be an array"),
        (
            {"secret_refs": [{"env_var": "TOKEN", "uri": "sm:x", "value": "plaintext"}]},
            "unsupported fields: value",
        ),
    ],
)
def test_create_spec_rejects_malformed_environment_json(
    permission_resolver, info, org, with_tenant_org, input_kwargs, message
):
    _grant_crud(permission_resolver)
    with with_tenant_org(org):
        result = AgentsMutation().create_agent_environment_spec(
            info(),
            input=CreateAgentEnvironmentSpecInput(
                name="Bad Environment",
                slug="bad-environment",
                agent_type="claude",
                **input_kwargs,
            ),
            org_id=str(org.guid),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert message in result.errors[0].message


def test_create_spec_requires_env_spec_create(info, org, with_tenant_org):
    # ``@mutation_audit`` wraps ``@require_permission`` and converts a
    # PermissionDenied into a PERMISSION_DENIED envelope (the mutation
    # contract) rather than letting it propagate — unlike the query path.
    with with_tenant_org(org):
        result = AgentsMutation().create_agent_environment_spec(
            info(),
            input=CreateAgentEnvironmentSpecInput(name="No Perm", slug="no-perm", agent_type="claude"),
            org_id=str(org.guid),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# updateAgentEnvironmentSpec
# ---------------------------------------------------------------------------


def test_update_spec_changes_only_supplied_fields(permission_resolver, info, org, with_tenant_org):
    _grant_crud(permission_resolver)
    spec = _mk_spec(org, "claude-dev", tool_preset="dev")
    with with_tenant_org(org):
        result = AgentsMutation().update_agent_environment_spec(
            info(),
            slug="claude-dev",
            input=UpdateAgentEnvironmentSpecInput(name="Claude Dev v2", allow_install=True),
        )
    assert result.ok, result.errors
    assert result.data.name == "Claude Dev v2"
    assert result.data.allow_install is True
    spec.refresh_from_db()
    assert spec.name == "Claude Dev v2"
    assert spec.allow_install is True
    # Untouched field preserved.
    assert spec.tool_preset == "dev"


def test_update_spec_invalid_agent_type(permission_resolver, info, org, with_tenant_org):
    _grant_crud(permission_resolver)
    _mk_spec(org, "claude-dev")
    with with_tenant_org(org):
        result = AgentsMutation().update_agent_environment_spec(
            info(),
            slug="claude-dev",
            input=UpdateAgentEnvironmentSpecInput(agent_type="gpt-99"),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "agentType"


def test_update_spec_rejects_malformed_environment_json(permission_resolver, info, org, with_tenant_org):
    _grant_crud(permission_resolver)
    spec = _mk_spec(org, "claude-dev", env_vars={"GOOD": "preserved"})
    with with_tenant_org(org):
        result = AgentsMutation().update_agent_environment_spec(
            info(),
            slug="claude-dev",
            input=UpdateAgentEnvironmentSpecInput(env_vars=["not", "an", "object"]),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    spec.refresh_from_db()
    assert spec.env_vars == {"GOOD": "preserved"}


def test_update_spec_not_found_for_other_org(permission_resolver, info, org, other_org, with_tenant_org):
    _grant_crud(permission_resolver)
    _mk_spec(other_org, "their-spec")
    with with_tenant_org(org):
        result = AgentsMutation().update_agent_environment_spec(
            info(),
            slug="their-spec",
            input=UpdateAgentEnvironmentSpecInput(name="Hacked"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# deleteAgentEnvironmentSpec
# ---------------------------------------------------------------------------


def test_delete_spec_soft_deletes(permission_resolver, info, org, with_tenant_org):
    _grant_crud(permission_resolver)
    spec = _mk_spec(org, "claude-dev")
    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_environment_spec(info(), slug="claude-dev")
    assert result.ok, result.errors
    spec.refresh_from_db()
    assert spec.deleted_at is not None


def test_cli_env_spec_scope_can_delete_without_app_delete(permission_resolver, info, org, with_tenant_org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_DELETE)
    spec = _mk_spec(org, "cli-delete")
    context_token = set_current_api_token(SimpleNamespace(scopes=[SCOPE_AGENT_ENV_SPEC_WRITE]))
    try:
        with with_tenant_org(org):
            result = AgentsMutation().delete_agent_environment_spec(info(), slug=spec.slug)
    finally:
        reset_current_api_token(context_token)

    assert result.ok, result.errors
    spec.refresh_from_db()
    assert spec.deleted_at is not None


def test_delete_spec_not_found_for_other_org(permission_resolver, info, org, other_org, with_tenant_org):
    _grant_crud(permission_resolver)
    _mk_spec(other_org, "their-spec")
    with with_tenant_org(org):
        result = AgentsMutation().delete_agent_environment_spec(info(), slug="their-spec")
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
