"""Another organization's guid reads exactly like a missing one (#1866).

Four resolvers take an organization guid beside the tenant they act in. Each
fetched the guid across the whole install and compared it with the tenant
afterwards, so a foreign guid got a different answer from one that does not
exist: ``organization mismatch`` (``PERMISSION_DENIED`` or a GraphQL error) or
``PRECONDITION``, against ``organization not found``. The answer said which
guids exist. A foreign guid now gets the missing guid's answer, and nothing is
written. The agents queries' superuser bypass is unchanged.

The caller holds a real organization role binding, so the gates run the way
they do in production.
"""

from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from graphql import GraphQLError

# The whole schema first: ``workflows.schema`` imported on its own trips its
# import cycle with ``astrolift_workflows.schema``.
import config.schema  # noqa: F401
from astrolift_agents.models import Skill
from astrolift_agents.schema.mutations import AgentsMutation, SkillInput
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_identity.models import Member, Organization
from astrolift_operations.models import Notification
from astrolift_operations.schema.mutations import OperationsMutation, TestNotificationInput
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, as_tenant, bind_role, make_info, make_user
from workflows.models import WorkflowDefinition
from workflows.schema.mutations import Mutation as WorkflowsMutation

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    world = ScopeWorld("xorg-guid-1866")
    world.user = make_user("xorg-guid-1866")
    Member.objects.create(user=world.user, scope_kind=Member.ScopeKind.ORG, scope_id=world.org.pk)
    bind_role(
        world.user,
        permissions=(
            Permission.APP_CREATE,
            Permission.AGENT_ENV_SPEC_READ,
            Permission.ORG_UPDATE,
            Permission.WORKFLOW_CREATE,
        ),
        kind="ORG",
        scope_id=world.org.pk,
        slug="xorg-guid-1866-admin",
    )
    world.info = make_info(world.user)
    world.foreign_org = Organization.objects.create(name="Globex", slug="globex-xorg-guid-1866")
    return world


def _answer(call):
    """What the caller sees: the envelope, or the GraphQL error raised."""
    try:
        result = call()
    except GraphQLError as exc:
        return "raised", str(exc)
    return result.ok, getattr(result, "data", None), [dataclasses.asdict(error) for error in result.errors]


def _state():
    """Every row these routes write."""
    return (
        Skill.all_objects.count(),
        Notification.all_objects.count(),
        WorkflowDefinition.objects.count(),
    )


ROUTES = {
    # agents ``_resolve_org``, behind six mutations.
    "createSkill": lambda world, guid: AgentsMutation().create_skill(
        world.info, input=SkillInput(name="Lint", slug="lint", description="", content="lint"), org_id=guid
    ),
    # agents ``_caller_org_id``, behind the agent queries.
    "agentEnvironmentSpecs": lambda world, guid: AgentsQuery().agent_environment_specs(
        world.info, org_id=guid
    ),
    "testNotificationChannel": lambda world, guid: OperationsMutation().test_notification_channel(
        world.info, input=TestNotificationInput(id=guid)
    ),
    "cloneWorkflowDefinition": lambda world, guid: WorkflowsMutation().clone_workflow_definition(
        world.info, slug="any-definition", org_id=guid
    ),
}


@pytest.mark.parametrize("route", list(ROUTES))
def test_a_foreign_organization_guid_reads_like_a_missing_one(world, route):
    call = ROUTES[route]
    before = _state()

    with as_tenant(world, world.user):
        from_foreign = _answer(lambda: call(world, str(world.foreign_org.guid)))
        from_missing = _answer(lambda: call(world, str(uuid4())))

    assert from_foreign == from_missing
    assert "organization not found" in repr(from_foreign)
    assert _state() == before


def test_a_superuser_still_reads_another_organization_by_guid(world):
    operator = get_user_model().objects.create_superuser(
        username="xorg-guid-1866-operator", email="operator@xorg-guid-1866.test", password="x"
    )

    with as_tenant(world, operator):
        specs = AgentsQuery().agent_environment_specs(make_info(operator), org_id=str(world.foreign_org.guid))
        with pytest.raises(GraphQLError, match="organization not found"):
            AgentsQuery().agent_environment_specs(make_info(operator), org_id=str(uuid4()))

    assert specs == []
