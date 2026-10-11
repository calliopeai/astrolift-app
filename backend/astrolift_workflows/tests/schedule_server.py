"""Synchronous schedule assertions backed by the disposable Temporal service."""

import os
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from astrolift_workflows import client as workflow_client
from workflows.schedule_sync import schedule_id_for


@pytest.fixture
def schedule_server(settings, monkeypatch):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.TEMPORAL_TASK_QUEUE = f"schedule-lifecycle-{uuid4().hex}"
    tracked = set()

    async def connect():
        return await Client.connect(
            os.environ.get("ASTROLIFT_TEST_TEMPORAL_ADDRESS") or os.environ["TEMPORAL_ADDRESS"],
            namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        )

    monkeypatch.setattr(workflow_client, "_get_client_async", connect)
    assert workflow_client._temporal_enabled()

    class Server:
        task_queue = settings.TEMPORAL_TASK_QUEUE

        @staticmethod
        async def connect():
            return await connect()

        @staticmethod
        def track(workflow):
            tracked.add(schedule_id_for(workflow))

        @staticmethod
        @async_to_sync
        async def describe(workflow):
            tracked.add(schedule_id_for(workflow))
            try:
                return await (await connect()).get_schedule_handle(schedule_id_for(workflow)).describe()
            except RPCError as exc:
                if exc.status == RPCStatusCode.NOT_FOUND:
                    return None
                raise

        @staticmethod
        @async_to_sync
        async def action_input(workflow):
            description = await (await connect()).get_schedule_handle(schedule_id_for(workflow)).describe()
            return (await (await connect()).data_converter.decode(description.schedule.action.args))[0]

    yield Server()

    @async_to_sync
    async def cleanup():
        client = await connect()
        for schedule_id in tracked:
            try:
                await client.get_schedule_handle(schedule_id).delete()
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise

    cleanup()
