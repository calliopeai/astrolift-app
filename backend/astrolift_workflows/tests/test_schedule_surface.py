"""Native schedule authoring preserves identities and enforces current authority."""

import pytest
from django.utils import timezone

from astrolift_workflows.schema.mutations import WorkflowsMutation
from astrolift_workflows.schema.queries import WorkflowsQuery
from astrolift_workflows.tests.schedule_server import schedule_server as schedule_server_fixture
from astrolift_workflows.tests.test_configured_workflow_identity import recipe
from astrolift_workflows.tests.test_import_scopes_2114 import grant, tenant, token
from astrolift_workflows.tests.test_import_scopes_2114 import no_index as no_index
from astrolift_workflows.tests.test_import_scopes_2114 import world as world_fixture
from core.permissions import Permission, PermissionDenied
from workflows.models import Workflow

schedule_server = schedule_server_fixture
world = world_fixture

pytestmark = pytest.mark.django_db
WRITES = (Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE, Permission.WORKFLOW_DELETE)


def create(w, **kwargs):
    definition = recipe(w)
    definition.is_enabled = True
    definition.save()
    return WorkflowsMutation().create_workflow(
        w.info,
        definition_id=str(definition.guid),
        name="Scheduled",
        trigger_kind="schedule",
        schedule_cron="0 0 1 1 *",
        **kwargs,
    )


def test_disabled_engine_preserves_saved_id_for_recovery(world, settings, schedule_server):
    grant(world, *WRITES, Permission.WORKFLOW_READ, Permission.WORKFLOW_TRIGGER)
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    with tenant(world):
        result = create(world)
        assert not result.ok and result.configuration_saved
        row = Workflow.objects.get(guid=result.workflow.guid)
        schedule_server.track(row)
        assert result.schedule.workflow_id == str(row.guid)
        assert result.schedule.error_code == "engine_disabled" and row.schedule_managed
        settings.ASTROLIFT_TEMPORAL_ENABLED = True
        recovery = WorkflowsMutation().reconcile_workflow_schedule(
            world.info,
            workflow_id=str(row.guid),
            expected_version=result.schedule.configuration_version,
            expected_active=True,
        )
        assert recovery.ok and recovery.schedule.confirmed
        assert not recovery.configuration_saved
        assert WorkflowsQuery().workflow_schedule(world.info, workflow_id=str(row.guid)).confirmed


def test_offline_draft_needs_no_trigger_but_activation_does(world, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    grant(world, *WRITES)
    with tenant(world):
        result = create(world, is_enabled=False)
        assert result.ok and result.configuration_saved
        assert result.schedule.observed_state == "not_requested"
        with pytest.raises(PermissionDenied):
            WorkflowsMutation().update_workflow(world.info, workflow_id=result.workflow.guid, is_enabled=True)
    row = Workflow.objects.get(guid=result.workflow.guid)
    assert not row.is_enabled and not row.schedule_managed


def test_deletion_during_outage_keeps_cleanup_recoverable(world, settings, schedule_server):
    grant(world, *WRITES, Permission.WORKFLOW_READ, Permission.WORKFLOW_TRIGGER)
    with tenant(world):
        created = create(world)
        assert created.ok
        row = Workflow.objects.get(guid=created.workflow.guid)
        schedule_server.track(row)
        settings.ASTROLIFT_TEMPORAL_ENABLED = False
        deleted = WorkflowsMutation().delete_workflow(world.info, workflow_id=str(row.guid))
        assert not deleted.ok and deleted.configuration_saved
        assert deleted.schedule.workflow_id == str(row.guid)
        row.refresh_from_db()
        assert row.deleted_at and row.schedule_managed
        assert (
            WorkflowsQuery().workflow_schedule(world.info, workflow_id=str(row.guid)).error_code
            == "engine_disabled"
        )
        settings.ASTROLIFT_TEMPORAL_ENABLED = True
        recovered = WorkflowsMutation().reconcile_workflow_schedule(
            world.info,
            workflow_id=str(row.guid),
            expected_version=row.version,
            expected_active=False,
        )
        assert recovered.ok and recovered.schedule.observed_state == "missing"
    assert schedule_server.describe(row) is None


@pytest.mark.parametrize("owner", ["own", "sibling", "foreign"])
@pytest.mark.parametrize("operator", [False, True])
def test_schedule_inspection_respects_token_ceiling(world, owner, operator, settings):
    grant(world, *WRITES, Permission.WORKFLOW_READ, kind="ORG")
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    with tenant(world):
        created = create(world, is_enabled=False)
    with tenant(world), token(world, owner=owner, operator=operator):
        if owner == "own":
            assert WorkflowsQuery().workflow_schedule(world.info, workflow_id=created.workflow.guid)
        else:
            with pytest.raises(PermissionDenied):
                WorkflowsQuery().workflow_schedule(world.info, workflow_id=created.workflow.guid)


def test_retired_owner_cleanup_requires_org_authority(world, settings):
    grant(world, *WRITES, Permission.WORKFLOW_READ)
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    with tenant(world):
        created = create(world, is_enabled=False)
        world.medops_project.deleted_at = timezone.now()
        world.medops_project.save()
        with pytest.raises(PermissionDenied):
            WorkflowsQuery().workflow_schedule(world.info, workflow_id=created.workflow.guid)
        grant(world, Permission.WORKFLOW_READ, kind="ORG")
        assert WorkflowsQuery().workflow_schedule(world.info, workflow_id=created.workflow.guid)


def test_graphql_schedule_recovery_and_cleanup(world, settings, schedule_server):
    from django.test import Client

    from astrolift_identity.models import Member

    grant(world, *WRITES, Permission.WORKFLOW_READ, Permission.WORKFLOW_TRIGGER)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    definition = recipe(world)
    definition.is_enabled = True
    definition.save()
    client = Client()
    client.force_login(world.user)

    def graphql(query, variables):
        response = client.post(
            f"/{settings.BASE_URL}gql/config/",
            data={"query": query, "variables": variables},
            content_type="application/json",
            HTTP_X_PLATFORM="WEB",
            HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        )
        assert response.status_code == 200
        payload = response.json()
        assert not payload.get("errors"), payload
        return payload["data"]

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    created = graphql(
        """mutation($definition: GUID!) {
        createWorkflow(name: "Recover me", definitionId: $definition,
            triggerKind: "schedule", scheduleCron: "0 0 1 1 *") {
            ok configurationSaved workflow { guid }
            schedule { workflowId configurationVersion desiredActive errorCode }
        }
    }""",
        {"definition": str(definition.guid)},
    )["createWorkflow"]
    assert not created["ok"] and created["configurationSaved"]
    row = Workflow.objects.get(guid=created["workflow"]["guid"])
    schedule_server.track(row)
    assert created["schedule"]["errorCode"] == "engine_disabled"
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    recovered = graphql(
        """mutation($id: GUID!, $version: Int!) {
        reconcileWorkflowSchedule(workflowId: $id, expectedVersion: $version, expectedActive: true) {
            ok schedule { confirmed observedState }
        }
    }""",
        {"id": str(row.guid), "version": created["schedule"]["configurationVersion"]},
    )["reconcileWorkflowSchedule"]
    assert recovered["ok"] and recovered["schedule"]["confirmed"]
    deleted = graphql(
        """mutation($id: GUID!) {
        deleteWorkflow(workflowId: $id) { ok configurationSaved schedule { confirmed observedState } }
    }""",
        {"id": str(row.guid)},
    )["deleteWorkflow"]
    assert deleted["ok"] and deleted["configurationSaved"] and deleted["schedule"]["confirmed"]
    observed = graphql(
        """query($id: GUID!) {
        workflowSchedule(workflowId: $id) { confirmed desiredActive observedState }
    }""",
        {"id": str(row.guid)},
    )["workflowSchedule"]
    assert observed == {"confirmed": True, "desiredActive": False, "observedState": "missing"}


def test_owner_change_after_save_retains_identity_without_engine_effect(world, schedule_server, monkeypatch):
    from workflows import schedule_sync
    from workflows.models import WorkflowDefinition

    grant(world, *WRITES, Permission.WORKFLOW_TRIGGER)
    original = schedule_sync.sync_workflow_schedule

    def moved(workflow, **kwargs):
        schedule_server.track(workflow)
        WorkflowDefinition.objects.filter(pk=workflow.definition_id).update(project=world.platform_project)
        return original(workflow, **kwargs)

    monkeypatch.setattr(schedule_sync, "sync_workflow_schedule", moved)
    with tenant(world):
        result = create(world)
    assert not result.ok and result.configuration_saved and result.workflow.guid
    assert result.schedule.error_code == "permission_denied"
    row = Workflow.objects.get(guid=result.workflow.guid)
    assert row.schedule_managed and schedule_server.describe(row) is None
