"""Native exact definition activation shares revision, writer and permission rules."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import close_old_connections, connection, transaction

from astrolift_workflows.tests.test_configured_workflow_identity import recipe
from astrolift_workflows.tests.test_import_scopes_2114 import grant, tenant
from astrolift_workflows.tests.test_import_scopes_2114 import no_index as no_index
from astrolift_workflows.tests.test_import_scopes_2114 import world as world_fixture
from config.schema import schema
from core.permissions import Permission, PermissionDenied
from workflows.models import WorkflowDefinition
from workflows.reviewed_starts import definition_revision
from workflows.schema.mutations import Mutation

world = world_fixture
pytestmark = pytest.mark.django_db


def prepare(world):
    grant(world, Permission.WORKFLOW_UPDATE, Permission.WORKFLOW_TRIGGER)
    row = recipe(world)
    row.is_enabled = False
    row.save()
    return row


def test_public_graphql_activation_returns_native_review_receipt(world):
    row = prepare(world)
    before = definition_revision(row)
    with tenant(world):
        result = schema.execute_sync(
            "mutation($id:GUID!,$revision:String!){setWorkflowDefinitionEnabled(definitionId:$id,expectedRevision:$revision,isEnabled:true){ok definitionId revision isEnabled changed errors{field messages}}}",
            variable_values={"id": str(row.guid), "revision": before},
            context_value=world.info.context,
        )
    assert result.errors is None
    receipt = result.data["setWorkflowDefinitionEnabled"]
    row.refresh_from_db()
    assert receipt["ok"] and receipt["changed"] and receipt["isEnabled"]
    assert receipt["definitionId"] == str(row.guid)
    assert receipt["revision"] == definition_revision(row) != before
    assert row.updated_by == world.user


def test_enable_requires_trigger_permission_but_disable_only_needs_update(world):
    grant(world, Permission.WORKFLOW_UPDATE)
    row = recipe(world)
    row.is_enabled = False
    row.save()
    with tenant(world), pytest.raises(PermissionDenied):
        Mutation().set_workflow_definition_enabled(
            world.info,
            definition_id=str(row.guid),
            expected_revision=definition_revision(row),
            is_enabled=True,
        )
    row.refresh_from_db()
    assert not row.is_enabled
    row.is_enabled = True
    row.save()
    with tenant(world):
        result = Mutation().set_workflow_definition_enabled(
            world.info,
            definition_id=str(row.guid),
            expected_revision=definition_revision(row),
            is_enabled=False,
        )
    assert result.ok and result.changed and not result.is_enabled


def test_global_identity_never_substitutes_same_slug_owned_definition(world):
    grant(world, Permission.WORKFLOW_UPDATE, Permission.WORKFLOW_TRIGGER, kind="ORG")
    owned = recipe(world, slug="activation-collision")
    owned.is_enabled = False
    owned.save()
    global_row = WorkflowDefinition.objects.create(name="Global", slug=owned.slug, is_enabled=False)
    with tenant(world):
        result = Mutation().set_workflow_definition_enabled(
            world.info,
            definition_id=str(global_row.guid),
            expected_revision=definition_revision(global_row),
            is_enabled=True,
        )
    assert not result.ok and result.definition_id is None
    owned.refresh_from_db()
    global_row.refresh_from_db()
    assert not owned.is_enabled and not global_row.is_enabled


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("changed", ["definition", "stage"])
def test_revision_is_checked_after_waiting_for_editor_lock(world, changed):
    row = prepare(world)
    revision = definition_revision(row)
    connected = Event()
    pid = []

    def activate():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pid.append(cursor.fetchone()[0])
            connected.set()
            with tenant(world):
                return Mutation().set_workflow_definition_enabled(
                    world.info, definition_id=str(row.guid), expected_revision=revision, is_enabled=True
                )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            current = WorkflowDefinition.objects.select_for_update().get(pk=row.pk)
            pending = pool.submit(activate)
            assert connected.wait(5)
            deadline = time.monotonic() + 5
            while True:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pid[0]])
                    observed = cursor.fetchone()
                if observed and observed[0] == "Lock":
                    break
                assert time.monotonic() < deadline, "Activation did not reach the row lock"
                time.sleep(0.02)
            if changed == "definition":
                current.description = "Edited while activation waited"
                current.save()
            else:
                stage = current.stages.first()
                stage.prompt = "Edited while activation waited"
                stage.save()
        result = pending.result(timeout=10)
    assert not result.ok and result.errors[0].field == "expected_revision"
    row.refresh_from_db()
    assert not row.is_enabled
