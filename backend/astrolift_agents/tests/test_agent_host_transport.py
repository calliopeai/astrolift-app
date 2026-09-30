"""Two independent clients use the real handshake, ORM, journal and RBAC."""

import json

import pytest
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator

from astrolift_agents.agent_host_ws import agent_host_ws_application
from astrolift_agents.models import AgentTaskInputMessage
from astrolift_agents.services.agent_host_projection import ROOT, chat_uri
from astrolift_agents.tests import test_agent_host as host_tests
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member

pytestmark = pytest.mark.django_db(transaction=True)
world = host_tests.world


def _token(world, user):
    Member.objects.create(user=user, scope_kind="ORG", scope_id=world.org.pk)
    issued = mint_token()
    row = ApiToken.objects.create(
        user=user,
        organization=world.org,
        name="AHP transport",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["admin"],
    )
    return issued.plaintext, row


async def connect(secret, org):
    comm = ApplicationCommunicator(
        agent_host_ws_application,
        {
            "type": "websocket",
            "path": "/app/ahp",
            "headers": [
                (b"authorization", f"Bearer {secret}".encode()),
                (b"x-astrolift-organization", str(org.guid).encode()),
            ],
        },
    )
    await comm.send_input({"type": "websocket.connect"})
    return comm, await comm.receive_output(timeout=10)


async def rpc(comm, method, params, request_id=1):
    await comm.send_input(
        {
            "type": "websocket.receive",
            "text": json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}),
        }
    )
    return json.loads((await comm.receive_output(timeout=10))["text"])


@pytest.mark.asyncio
async def test_two_clients_replay_steer_deny_and_detach_without_stopping(world):
    viewer_secret, _ = await sync_to_async(_token)(world, world.user)
    controller_secret, _ = await sync_to_async(_token)(world, world.other)
    viewer, accepted = await connect(viewer_secret, world.org)
    controller, other_accepted = await connect(controller_secret, world.org)
    assert accepted["type"] == other_accepted["type"] == "websocket.accept"
    try:
        for comm, name in [(viewer, "viewer"), (controller, "controller")]:
            reply = await rpc(
                comm,
                "initialize",
                {
                    "channel": ROOT,
                    "clientId": name,
                    "protocolVersions": ["1.0.0"],
                    "initialSubscriptions": [chat_uri(world.task)],
                },
            )
            assert reply["result"]["protocolVersion"] == "1.0.0"
        await sync_to_async(host_tests.add_event)(world.task, "Shared history")
        seen = []
        for comm in [viewer, controller]:
            actions = [json.loads((await comm.receive_output(timeout=10))["text"]) for _ in range(3)]
            assert [a["params"]["action"]["type"] for a in actions] == [
                "chat/turnStarted",
                "chat/responsePart",
                "chat/delta",
            ]
            seen.append(actions)
        assert seen[0] == seen[1]
        denied = await rpc(viewer, "dispatchAction", host_tests.steer(world.task))
        assert "rejectionReason" in denied["params"]
        accepted_action = await rpc(controller, "dispatchAction", host_tests.steer(world.task))
        assert "rejectionReason" not in accepted_action["params"]
        assert await sync_to_async(AgentTaskInputMessage.objects.count)() == 1
    finally:
        for comm in [viewer, controller]:
            await comm.send_input({"type": "websocket.disconnect"})
            await comm.wait(timeout=10)
    await sync_to_async(world.task.refresh_from_db)()
    assert world.task.status == "running"


@pytest.mark.asyncio
async def test_revoked_bearer_closes_an_open_connection(world):
    secret, row = await sync_to_async(_token)(world, world.user)
    comm, accepted = await connect(secret, world.org)
    assert accepted["type"] == "websocket.accept"
    await rpc(comm, "initialize", {"channel": ROOT, "clientId": "viewer", "protocolVersions": ["1.0.0"]})
    await sync_to_async(ApiToken.objects.filter(pk=row.pk).update)(is_revoked=True)
    closed = await comm.receive_output(timeout=10)
    assert closed == {"type": "websocket.close", "code": 4403}
    await comm.wait(timeout=10)


@pytest.mark.asyncio
async def test_anonymous_handshake_is_refused():
    comm = ApplicationCommunicator(
        agent_host_ws_application, {"type": "websocket", "path": "/app/ahp", "headers": []}
    )
    await comm.send_input({"type": "websocket.connect"})
    assert await comm.receive_output(timeout=10) == {"type": "websocket.close", "code": 4401}
    await comm.wait(timeout=10)
