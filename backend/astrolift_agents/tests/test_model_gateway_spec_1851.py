"""The ``model_gateway`` switch on an agent environment spec (#1851).

What the switch does to a task or box lives with the spawner and box tests
(``astrolift_dispatch/tests/test_model_gateway_1851.py``,
``test_agent_box.py``). Here: the field on the GraphQL surface, and that it
cannot be combined with ``managed_model``.
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
from astrolift_dispatch.model_gateway import MODEL_GATEWAY_MANAGED_CONFLICT
from astrolift_identity.models import Organization
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Gateway Spec Org", slug="gateway-spec-org")


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="Gateway Other Org", slug="gateway-other-org")


def _create(info, org, **fields):
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        return AgentsMutation().create_agent_environment_spec(
            info,
            input=CreateAgentEnvironmentSpecInput(name="Spec", slug="spec", agent_type="claude", **fields),
            org_id=str(org.guid),
        )


def _update(info, org, **fields):
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        return AgentsMutation().update_agent_environment_spec(
            info, slug="spec", input=UpdateAgentEnvironmentSpecInput(**fields)
        )


def _stored(org):
    return AgentEnvironmentSpec.objects.get(organization=org, slug="spec")


def test_a_spec_keeps_provider_keys_unless_it_asks_for_the_gateway(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)

    result = _create(info, org)

    assert result.ok is True, result.errors
    assert result.data.model_gateway is False
    assert _stored(org).model_gateway is False


def test_create_threads_model_gateway(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)

    result = _create(info, org, model_gateway=True)

    assert result.ok is True, result.errors
    assert result.data.model_gateway is True
    assert _stored(org).model_gateway is True


def test_create_refuses_the_gateway_with_managed_model(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)

    result = _create(info, org, model_gateway=True, managed_model=True)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "modelGateway"
    assert result.errors[0].message == MODEL_GATEWAY_MANAGED_CONFLICT
    assert not AgentEnvironmentSpec.objects.filter(organization=org).exists()


def test_update_turns_it_on_and_off_without_touching_the_rest(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_UPDATE)
    AgentEnvironmentSpec.objects.create(
        organization=org, name="Spec", slug="spec", agent_type="claude", box_workspace=True
    )

    on = _update(info, org, model_gateway=True)
    assert on.ok is True, on.errors
    assert on.data.model_gateway is True
    assert on.data.box_workspace is True

    assert _update(info, org, name="Renamed").data.model_gateway is True

    off = _update(info, org, model_gateway=False)
    assert off.data.model_gateway is False
    assert _stored(org).model_gateway is False


@pytest.mark.parametrize(
    ("stored", "change"),
    [
        ({"model_gateway": True}, {"managed_model": True}),
        ({"managed_model": True}, {"model_gateway": True}),
    ],
)
def test_update_refuses_to_combine_them_in_either_order(permission_resolver, info, org, stored, change):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_UPDATE)
    AgentEnvironmentSpec.objects.create(
        organization=org, name="Spec", slug="spec", agent_type="claude", **stored
    )

    result = _update(info, org, **change)

    assert result.ok is False
    assert result.errors[0].field == "modelGateway"
    spec = _stored(org)
    assert (spec.model_gateway, spec.managed_model) == (
        stored.get("model_gateway", False),
        stored.get("managed_model", False),
    )


def test_update_is_denied_without_the_grant(permission_resolver, info, org):
    AgentEnvironmentSpec.objects.create(organization=org, name="Spec", slug="spec", agent_type="claude")

    result = _update(info, org, model_gateway=True)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert _stored(org).model_gateway is False


def test_another_organizations_spec_cannot_be_switched(permission_resolver, info, org, other_org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_UPDATE)
    AgentEnvironmentSpec.objects.create(organization=other_org, name="Spec", slug="spec", agent_type="claude")

    result = _update(info, org, model_gateway=True)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    assert _stored(other_org).model_gateway is False
