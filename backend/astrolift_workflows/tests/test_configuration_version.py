"""Configuration review versions are native API assertions checked under the row lock."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import close_old_connections, connection, transaction

from astrolift_workflows.schema.mutations import WorkflowsMutation
from astrolift_workflows.tests.test_configured_workflow_identity import PERMISSIONS, configured
from astrolift_workflows.tests.test_import_scopes_2114 import grant, tenant
from astrolift_workflows.tests.test_import_scopes_2114 import no_index as no_index
from astrolift_workflows.tests.test_import_scopes_2114 import world as world_fixture
from config.schema import schema
from workflows.models import Workflow

world = world_fixture
pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_graphql_version_is_exposed_and_refuses_stale_writes(world, operation):
    grant(world, *PERMISSIONS)
    row = configured(world)
    old_version = row.version
    row.name = "Other editor"
    row.save()
    with tenant(world):
        result = schema.execute_sync(
            "query($id:GUID!){workflow(workflowId:$id){guid version name}}",
            variable_values={"id": str(row.guid)},
            context_value=world.info.context,
        )
        assert result.errors is None
        assert result.data["workflow"]["version"] == row.version
        result = schema.execute_sync(
            f"mutation($id:GUID!,$v:Int!){{{operation}Workflow(workflowId:$id,expectedVersion:$v){{ok configurationSaved errors{{field messages}}}}}}",
            variable_values={"id": str(row.guid), "v": old_version},
            context_value=world.info.context,
        )
        assert result.errors is None
        receipt = result.data[f"{operation}Workflow"]
        assert not receipt["ok"] and not receipt["configurationSaved"]
        assert receipt["errors"][0]["field"] == "expected_version"
    row.refresh_from_db()
    assert row.name == "Other editor" and row.deleted_at is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_version_is_checked_after_waiting_for_current_writer(world, operation):
    grant(world, *PERMISSIONS)
    row = configured(world)
    before = row.version
    connected = Event()
    pid = []

    def stale_write():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pid.append(cursor.fetchone()[0])
            connected.set()
            with tenant(world):
                mutate = getattr(WorkflowsMutation(), f"{operation}_workflow")
                return mutate(world.info, workflow_id=str(row.guid), expected_version=before)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            current = Workflow.objects.select_for_update().get(pk=row.pk)
            pending = pool.submit(stale_write)
            assert connected.wait(5)
            deadline = time.monotonic() + 5
            while True:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pid[0]])
                    observed = cursor.fetchone()
                if observed and observed[0] == "Lock":
                    break
                assert time.monotonic() < deadline, "Second writer never reached the row lock"
                time.sleep(0.02)
            current.name = "Committed while other editor waited"
            current.save()
        result = pending.result(timeout=10)
    assert not result.ok and not result.configuration_saved
    assert result.errors[0].field == "expected_version"
    row.refresh_from_db()
    assert row.name == "Committed while other editor waited" and row.deleted_at is None
