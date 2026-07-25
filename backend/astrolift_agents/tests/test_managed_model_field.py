"""``managed_model`` on AgentEnvironmentSpec — model + mutation threading.

Against a real database, pins that the new switch defaults off, that the
create / update mutations thread it (input → row → read type), that an
omitted update leaves it unchanged (partial-update contract), and that the
read converter surfaces it. Resolvers are exercised by direct invocation
with the controllable permission resolver + a bound tenant context (the
agents-test convention, mirrors ``test_agent_secret_value_mutations``).
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
from astrolift_agents.schema.types import agent_env_spec_to_type
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org():
    return Organization.objects.create(name="Model Org", slug="model-org")


@pytest.fixture
def with_tenant_org():
    def _enter(org):
        return _tenant_ctx(TenantContext(organization_id=org.id))

    return _enter


def _make(org, **kwargs):
    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Spec",
        slug="a",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        **kwargs,
    )


def test_model_defaults_managed_model_false(org):
    spec = _make(org)
    spec.refresh_from_db()
    assert spec.managed_model is False


def test_converter_surfaces_managed_model(org):
    assert agent_env_spec_to_type(_make(org, managed_model=True)).managed_model is True


def test_create_mutation_threads_managed_model(permission_resolver, info, org, with_tenant_org):
    permission_resolver.grant(Permission.APP_CREATE)
    with with_tenant_org(org):
        result = AgentsMutation().create_agent_environment_spec(
            info,
            input=CreateAgentEnvironmentSpecInput(
                name="Claude Managed",
                slug="claude-managed",
                agent_type="claude",
                managed_model=True,
            ),
            org_id=str(org.guid),
        )
    assert result.ok is True, result.errors
    assert result.data.managed_model is True
    row = AgentEnvironmentSpec.objects.get(organization=org, slug="claude-managed")
    assert row.managed_model is True


def test_create_mutation_defaults_managed_model_off(permission_resolver, info, org, with_tenant_org):
    permission_resolver.grant(Permission.APP_CREATE)
    with with_tenant_org(org):
        result = AgentsMutation().create_agent_environment_spec(
            info,
            input=CreateAgentEnvironmentSpecInput(name="Plain", slug="plain", agent_type="claude"),
            org_id=str(org.guid),
        )
    assert result.ok is True, result.errors
    assert result.data.managed_model is False


def test_update_mutation_toggles_managed_model(permission_resolver, info, org, with_tenant_org):
    permission_resolver.grant(Permission.APP_UPDATE)
    spec = _make(org, managed_model=False)
    with with_tenant_org(org):
        result = AgentsMutation().update_agent_environment_spec(
            info,
            slug="a",
            input=UpdateAgentEnvironmentSpecInput(managed_model=True),
        )
    assert result.ok is True, result.errors
    assert result.data.managed_model is True
    spec.refresh_from_db()
    assert spec.managed_model is True


def test_update_mutation_leaves_managed_model_unchanged_when_omitted(
    permission_resolver, info, org, with_tenant_org
):
    permission_resolver.grant(Permission.APP_UPDATE)
    spec = _make(org, managed_model=True)
    with with_tenant_org(org):
        result = AgentsMutation().update_agent_environment_spec(
            info,
            slug="a",
            input=UpdateAgentEnvironmentSpecInput(name="Renamed"),
        )
    assert result.ok is True, result.errors
    spec.refresh_from_db()
    # Omitted (None) → unchanged, still on.
    assert spec.managed_model is True
