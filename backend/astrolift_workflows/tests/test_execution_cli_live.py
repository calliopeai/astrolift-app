"""Opt-in compiled CLI → authenticated Django HTTP → real Temporal proof."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.tests.test_execution_surface import owned_execution  # noqa: F401
from astrolift_workflows.tests.test_workflow_stage_activities import (  # noqa: F401
    agent_workload,
    definition,
    org,
    run,
)
from core.permissions import Permission

CLI = os.environ.get("ASTROLIFT_TEST_CLI")
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not CLI, reason="set ASTROLIFT_TEST_CLI"),
]


async def test_cli_observes_controls_and_cleans_exact_execution_over_authenticated_http(
    owned_execution,  # noqa: F811
    permission_resolver,
    temporal_env,
    live_server,
    monkeypatch,
):
    from temporalio.client import WorkflowFailureError

    from astrolift_workflows import client
    from astrolift_workflows.tests.test_workflow_run_reconcile import HoldingDefinition
    from core.testing.temporal import temporal_worker

    permission_resolver.grant(Permission.WORKFLOW_READ)
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)

    @sync_to_async
    def authenticate():
        user = get_user_model().objects.create_user(
            username="execution-cli", email="execution-cli@example.test"
        )
        Member.objects.create(user=user, scope_kind="ORG", scope_id=owned_execution.organization_id)
        issued = mint_token()
        ApiToken.objects.create(
            user=user,
            organization_id=owned_execution.organization_id,
            name="disposable execution CLI verification",
            token_hash=issued.token_hash,
            token_last_4=issued.last4,
            scopes=["admin"],
        )
        return issued.plaintext, str(owned_execution.organization.guid)

    token, organization = await authenticate()

    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(client, "_get_client_async", get_client)
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)

    async def invoke(command, identifier, *args, expect_success=True):
        environment = dict(os.environ, ASTROLIFT_TOKEN=token, ASTROLIFT_NO_UPDATE_CHECK="1")
        process = await asyncio.create_subprocess_exec(
            CLI,
            "--api-url",
            live_server.url,
            "--org",
            organization,
            "--json",
            "--no-prompt",
            "workflow",
            command,
            str(identifier),
            *args,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        assert token.encode() not in stdout + stderr
        if not expect_success:
            assert process.returncode != 0
            return None
        assert process.returncode == 0, stderr.decode()
        return json.loads(stdout)

    async with temporal_worker(temporal_env, workflows=[HoldingDefinition]):
        original = await temporal_env.client.start_workflow(
            HoldingDefinition.run, id=owned_execution.workflow_id, task_queue="astrolift-test"
        )
        owned_execution.run_id = original.first_execution_run_id
        await sync_to_async(owned_execution.save)(update_fields=["run_id", "updated_at", "version"])
        neighbor = None
        try:
            row = await invoke("execution", owned_execution.pk)
            assert row["guid"] == str(owned_execution.guid)
            assert row["organizationGuid"] == organization
            assert row["status"] == "running" and row["observationError"] == ""
            await invoke(
                "execution-stop", owned_execution.pk, "--yes", "--run-id", "different", expect_success=False
            )
            assert (await original.describe()).status.name == "RUNNING"
            await original.signal(HoldingDefinition.finish)
            await asyncio.wait_for(original.result(), 10)
            neighbor = await temporal_env.client.start_workflow(
                HoldingDefinition.run, id=owned_execution.workflow_id, task_queue="astrolift-test"
            )
            other = await sync_to_async(WorkflowRun.objects.create)(
                organization_id=owned_execution.organization_id,
                workflow_kind="WorkflowDefinitionRunWorkflow",
                workflow_id=neighbor.id,
                run_id=neighbor.first_execution_run_id,
                workflow_definition_id=owned_execution.workflow_definition_id,
            )
            ack = await invoke("execution-stop", owned_execution.pk, "--yes")
            assert ack["requested"] and ack["execution"]["status"] == "completed"
            assert (await neighbor.describe()).status.name == "RUNNING"
            ack = await invoke("execution-stop", other.pk, "--yes")
            assert ack["requested"] and ack["execution"]["temporalRunId"] == other.run_id
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(neighbor.result(), 10)
            row = await invoke("execution", other.pk, "--watch")
            assert row["status"] == "cancelled"
            assert row["taskCleanup"]["remaining"] == 0
            ack = await invoke("execution-cleanup", other.guid, "--yes")
            assert ack["execution"]["taskCleanup"]["status"] in {"completed", "not_required"}
        finally:
            for handle in (original, neighbor):
                if handle is not None:
                    pinned = temporal_env.client.get_workflow_handle(
                        handle.id, run_id=handle.first_execution_run_id
                    )
                    if (await pinned.describe()).status.name == "RUNNING":
                        await pinned.terminate(reason="disposable CLI verification cleanup")
