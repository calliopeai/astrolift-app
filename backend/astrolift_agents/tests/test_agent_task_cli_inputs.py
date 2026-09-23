"""CLI bearers can steer and answer tasks only within their real role grants."""

import json

import pytest
from django.conf import settings
from django.test import Client

from astrolift_agents.models import AgentTaskEvent, AgentTaskInputReply
from astrolift_agents.tests.test_fleet_scopes_1745 import bind, running
from astrolift_agents.tests.test_fleet_scopes_1745 import fleet as fleet_fixture
from astrolift_agents.tests.test_fleet_scopes_1745 import no_opensearch as no_opensearch
from astrolift_identity.api_tokens import CLI_DEVICE_SCOPES, DEFAULT_SCOPES, mint_token
from astrolift_identity.models import ApiToken, Member
from core.permissions import Permission

pytestmark = pytest.mark.django_db
fleet = fleet_fixture


@pytest.mark.parametrize("surface", ["steering", "reply"])
@pytest.mark.parametrize("access", ["cli", "read_only", "missing_role", "sibling_task"])
def test_cli_input_requires_both_bearer_scope_and_task_role(fleet, surface, access):
    permissions = [Permission.AGENT_READ]
    if access != "missing_role":
        permissions.append(Permission.AGENT_TASK_SEND_INPUT)
    bind(fleet, "APP", permissions)
    task = fleet.tasks[1 if access == "sibling_task" else 0]
    running(task)
    AgentTaskEvent.objects.create(
        organization=fleet.world.org,
        agent_task=task,
        sequence=1,
        turn_id="turn-1",
        message_id="permission-1",
        kind=AgentTaskEvent.Kind.APPROVAL_REQUIRED,
        request={"version": 1, "kind": "approval", "tool": {"name": "Bash", "input": {"command": "pwd"}}},
    )
    # The bearer authenticates only while its owner is an active member
    # of its org (#1910).
    Member.objects.create(user=fleet.user, scope_kind=Member.ScopeKind.ORG, scope_id=fleet.world.org.pk)
    minted = mint_token()
    ApiToken.objects.create(
        user=fleet.user,
        organization=fleet.world.org,
        name="CLI input verification",
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=list(DEFAULT_SCOPES if access == "read_only" else CLI_DEVICE_SCOPES),
    )
    if surface == "reply":
        mutation = "replyAgentTaskInput"
        arguments = 'requestSequence: 1, response: {decision: "allow"}'
    else:
        mutation = "sendAgentTaskInput"
        arguments = 'message: "continue with the next check"'
    query = f'mutation {{{mutation}(taskId: "{task.guid}", {arguments}) {{ok errors {{code message}}}}}}'
    response = Client().post(
        f"/{settings.BASE_URL}gql/config/",
        data=json.dumps({"query": query, "variables": {}}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {minted.plaintext}",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(fleet.world.org.guid),
    )
    assert response.status_code == 200, response.content
    result = response.json()
    accepted = not result.get("errors") and (result.get("data") or {}).get(mutation, {}).get("ok")
    assert bool(accepted) == (access == "cli"), result
    assert AgentTaskInputReply.objects.count() == int(access == "cli" and surface == "reply")
    assert task.input_messages.count() == int(access == "cli" and surface == "steering")
