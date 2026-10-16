"""Actual HTTP acceptance → registered Temporal workflow/activity → owned SDK HTTP."""

import asyncio
import threading
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio.client import WorkflowFailureError
from temporalio.testing import ActivityEnvironment

from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http
from astrolift_lifecycle.models import ManagedDomainAppBinding
from astrolift_workflows import client as workflow_client
from astrolift_workflows.activities.domain_app_route import reconcile_domain_app_route
from astrolift_workflows.client import start_workflow_once as real_start_workflow_once
from astrolift_workflows.workflows.domain_app_route import ManagedDomainAppRouteWorkflow
from core.testing.temporal import temporal_worker
from providers.tests.aws.test_domain_route_api_gateway_http import world as native_http_world  # noqa: F401

from .test_domain_app_http_api_native_2299 import (  # noqa: F401
    CONNECT,
    REVIEW,
    accept,
    dns_certificate_wire,
)
from .test_domain_app_http_api_native_2299 import (
    connected_source as source_fixture,
)
from .test_domain_app_route_store_2299 import admitted, world  # noqa: F401

connected_source = source_fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio, pytest.mark.slow]


async def bind_worker(temporal_env, monkeypatch):
    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
    monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(workflow_client, "_task_queue", lambda: "astrolift-test")
    monkeypatch.setattr(workflow_client, "start_workflow_once", real_start_workflow_once)


def accept_real(client, w):
    reply = graphql_http(client, w.headers, REVIEW, {"input": w.route_input})
    assert not reply.get("errors"), reply
    review = reply["data"]["managedDomainAppRouteReview"]
    assert review["canAttach"]
    payload = {**w.route_input, "reviewSha256": review["reviewSha256"], "requestId": str(uuid4())}
    reply = graphql_http(client, w.headers, CONNECT, {"input": payload})
    assert not reply.get("errors") and reply["data"]["connectManagedDomainAppRoute"]["ok"], reply
    row = ManagedDomainAppBinding.objects.get(
        guid=reply["data"]["connectManagedDomainAppRoute"]["data"]["id"]
    )
    assert w.starts == []  # Actual Temporal dispatch replaced the fixture interceptor.
    return row, payload


async def test_real_authenticated_start_worker_sdk_complete_and_original_replay(
    connected_source, client, temporal_env, monkeypatch
):
    w = connected_source
    await bind_worker(temporal_env, monkeypatch)
    with dns_certificate_wire(monkeypatch) as dns:
        async with temporal_worker(
            temporal_env, workflows=[ManagedDomainAppRouteWorkflow], activities=[reconcile_domain_app_route]
        ):
            row, payload = await sync_to_async(accept_real)(client, w)
            handle = temporal_env.client.get_workflow_handle(row.workflow_id)
            # Wait on the run's status rather than result()'s history long
            # poll, which can return without a close event on a slow runner.
            for _ in range(200):
                if (await handle.describe()).status.name != "RUNNING":
                    break
                await asyncio.sleep(0.5)
            result = await asyncio.wait_for(handle.result(), 30)
            assert result["status"] == "ready", result
            repeated = await sync_to_async(graphql_http)(client, w.headers, CONNECT, {"input": payload})
            assert repeated["data"]["connectManagedDomainAppRoute"]["data"]["id"] == str(row.guid)
            assert (await handle.describe()).status.name == "COMPLETED"
            assert len(w.native["writes"]) == 2 and len(dns["writes"]) == 1
            assert b"d-original.execute-api" in dns["writes"][0]
            history = await handle.fetch_history()
            assert any(event.HasField("activity_task_completed_event_attributes") for event in history.events)


async def test_real_worker_cancel_held_native_reply_retains_only_original_ack(
    connected_source, client, temporal_env, monkeypatch
):
    w = connected_source
    await bind_worker(temporal_env, monkeypatch)
    entered, released = threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert released.wait(4)

    w.native["before_domain_reply"] = hold
    with dns_certificate_wire(monkeypatch) as dns:
        async with temporal_worker(
            temporal_env, workflows=[ManagedDomainAppRouteWorkflow], activities=[reconcile_domain_app_route]
        ):
            row, _payload = await sync_to_async(accept_real)(client, w)
            handle = temporal_env.client.get_workflow_handle(row.workflow_id)
            assert await asyncio.to_thread(entered.wait, 60)
            await handle.cancel()
            try:
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), 3)
            finally:
                released.set()
            for _ in range(150):
                current = await sync_to_async(ManagedDomainAppBinding.objects.get)(pk=row.pk)
                if current.native_receipts.get("api-domain", {}).get("phase") == "ACK":
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("Original typed native ACK was not retained")
            assert "api-mapping" not in current.native_receipts
            assert len(w.native["writes"]) == 1 and dns["writes"] == []
            assert current.status != "ready"


async def test_actual_parent_writer_busy_preserves_reservation_then_observes_after_release(
    connected_source, client, monkeypatch
):
    from django.db import connection

    w = connected_source
    with dns_certificate_wire(monkeypatch) as dns:
        row = await sync_to_async(accept)(client, w)

        def hold_parent():
            owned = connection.Database.connect(**connection.get_connection_params())
            with owned.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM astrolift_identity_organization WHERE id=%s FOR UPDATE", [w.org.pk]
                )
            return owned

        owned = await sync_to_async(hold_parent)()
        try:
            result = await ActivityEnvironment().run(reconcile_domain_app_route, str(row.guid))
            assert result["status"] == "pending" and result["reason"] == "CURRENT_ROUTE_LOCK_BUSY"
            observed = await sync_to_async(ManagedDomainAppBinding.objects.get)(pk=row.pk)
            assert observed.status == "accepted" and "api-domain" not in observed.native_receipts
            assert w.native["writes"] == [] and dns["writes"] == []
        finally:
            await sync_to_async(owned.rollback)()
            await sync_to_async(owned.close)()
        result = await ActivityEnvironment().run(reconcile_domain_app_route, str(row.guid))
        assert result["status"] == "ready"
        assert len(w.native["writes"]) == 2 and len(dns["writes"]) == 1
