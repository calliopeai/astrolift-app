"""``factoryRoom``: a factory project's runs, gates and outcomes, read-only (#2556).

Real RoleBindings: the MedOps project (Intake) is a factory because its app
declares ``[app] kind = "factory"``; the Platform project (Core) is generic.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.schema.queries import WorkflowsQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution

pytestmark = pytest.mark.django_db

READ = [Permission.WORKFLOW_READ, Permission.PROJECT_READ]
T0 = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)


def _definition(org, project, slug):
    definition = WorkflowDefinition.objects.create(
        organization=org,
        project=project,
        name=f"Toolchain {slug}",
        slug=slug,
        model_label="",
        is_enabled=True,
    )
    WorkflowStage.objects.create(
        slug=f"{slug}-code",
        definition=definition,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        role="engineer",
        output_key="code",
    )
    WorkflowStage.objects.create(
        slug=f"{slug}-approve",
        definition=definition,
        order=1,
        kind=WorkflowStage.StageKind.HUMAN_GATE,
        role="maintainer",
    )
    return definition


def _run(org, definition, name, *, status=WorkflowRun.Status.RUNNING, started=0, ended=None):
    return WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=f"WorkflowDefinitionRunWorkflow-{name}",
        run_id=f"temporal-{name}",
        status=status,
        started_at=T0 + dt.timedelta(minutes=started),
        ended_at=None if ended is None else T0 + dt.timedelta(minutes=ended),
    )


def _at(run, stage_slug, *, round_number=1, status=WorkflowStageExecution.Status.RUNNING):
    execution = WorkflowStageExecution.objects.create(
        slug=f"{run.workflow_id}-{stage_slug}-{round_number}",
        workflow_run=run,
        stage=WorkflowStage.objects.get(slug=stage_slug),
        status=status,
        round_number=round_number,
        started_at=timezone.now(),
    )
    run.current_stage_execution = execution
    run.save(update_fields=["current_stage_execution"])
    return execution


@pytest.fixture
def world():
    w = ScopeWorld("fr2556")
    w.medops_app.app_kind = "factory"
    w.medops_app.save(update_fields=["app_kind"])
    flow = _definition(w.org, w.medops_project, "security-2556")
    w.coding = _run(w.org, flow, "coding", started=1)
    _at(w.coding, "security-2556-code", round_number=2)
    w.gated = _run(w.org, flow, "gated", started=2)
    _at(w.gated, "security-2556-code", status=WorkflowStageExecution.Status.COMPLETED)
    _at(w.gated, "security-2556-approve")
    w.passed = _run(w.org, flow, "passed", status=WorkflowRun.Status.COMPLETED, started=-30, ended=-10)
    _at(w.passed, "security-2556-approve", status=WorkflowStageExecution.Status.COMPLETED)
    w.stopped = _run(w.org, flow, "stopped", status=WorkflowRun.Status.FAILED, started=-20, ended=-5)
    _at(w.stopped, "security-2556-code", status=WorkflowStageExecution.Status.FAILED)
    core_flow = _definition(w.org, w.platform_project, "core-2556")
    _at(_run(w.org, core_flow, "core-gated"), "core-2556-approve")
    w.beta = Organization.objects.create(name="Beta", slug="beta-fr2556")
    return w


def _room(world, user, project, *, org=None):
    with tenant_context(TenantContext(organization_id=(org or world.org).pk, actor_user_id=user.pk)):
        return WorkflowsQuery().factory_room(make_info(user), project_id=str(project.guid))


def _reader(world, name, *, permissions=READ, project=None):
    user = make_user(f"{name}-fr2556")
    bind_role(
        user,
        permissions=permissions,
        kind="PROJECT",
        scope_id=(project or world.medops_project).pk,
        slug=f"{name}-fr2556",
    )
    return user


def test_a_project_reader_sees_the_factory_room(world):
    room = _room(world, _reader(world, "reader"), world.medops_project)

    assert (room.project_id, room.project_slug, room.name) == (
        str(world.medops_project.guid),
        world.medops_project.slug,
        "Intake",
    )
    assert [r.id for r in room.active_runs] == [str(world.gated.guid), str(world.coding.guid)]
    coding = room.active_runs[1]
    assert (coding.flow, coding.flow_name, coding.stage, coding.round, coding.status) == (
        "security-2556",
        "Toolchain security-2556",
        "code",
        2,
        "running",
    )
    assert coding.waiting_on_person is False
    gated = room.active_runs[0]
    assert (gated.stage, gated.waiting_on_person) == ("maintainer", True)
    assert room.approvals_waiting == 1
    assert [(r.id, r.status) for r in room.finished_runs] == [
        (str(world.stopped.guid), "failed"),
        (str(world.passed.guid), "completed"),
    ]
    assert room.finished_runs[0].ended_at == T0 + dt.timedelta(minutes=-5)


def test_an_org_wide_reader_sees_only_this_factorys_runs(world):
    admin = make_user("admin-fr2556")
    bind_role(admin, permissions=READ, kind="ORG", scope_id=world.org.pk, slug="admin-fr2556")
    room = _room(world, admin, world.medops_project)
    assert {r.flow for r in room.active_runs + room.finished_runs} == {"security-2556"}
    assert room.approvals_waiting == 1


def test_a_finished_gate_is_not_waiting_on_anyone(world):
    WorkflowStageExecution.objects.filter(
        workflow_run=world.gated, stage__slug="security-2556-approve"
    ).update(status=WorkflowStageExecution.Status.COMPLETED)
    room = _room(world, _reader(world, "after-gate"), world.medops_project)
    assert room.approvals_waiting == 0
    assert all(run.waiting_on_person is False for run in room.active_runs)


def test_a_binding_on_a_sibling_project_is_refused(world):
    outsider = _reader(world, "sibling", project=world.platform_project)
    with pytest.raises(PermissionDenied):
        _room(world, outsider, world.medops_project)


def test_workflow_read_without_project_read_sees_no_room(world):
    runs_only = _reader(world, "runs-only", permissions=[Permission.WORKFLOW_READ])
    assert _room(world, runs_only, world.medops_project) is None


def test_a_generic_project_has_no_room(world):
    reader = _reader(world, "core", project=world.platform_project)
    assert _room(world, reader, world.platform_project) is None


def test_another_org_never_reaches_the_room(world):
    stranger = make_user("stranger-fr2556")
    bind_role(stranger, permissions=READ, kind="ORG", scope_id=world.beta.pk, slug="stranger-fr2556")
    assert _room(world, stranger, world.medops_project, org=world.beta) is None


def test_a_retired_factory_declaration_closes_the_room(world):
    world.medops_app.deleted_at = timezone.now()
    world.medops_app.save(update_fields=["deleted_at"])
    assert _room(world, _reader(world, "retired"), world.medops_project) is None


class _Request:
    def __init__(self, user):
        self.user = user
        self.session = {}
        self.headers = {}


def test_the_room_through_the_schema(world):
    from config.schema import schema
    from core.schema.context import StrawberryContext

    reader = _reader(world, "schema")
    document = """
      query($id: GUID!) {
        factoryRoom(projectId: $id) {
          projectSlug name approvalsWaiting
          activeRuns { id flow flowName stage round status waitingOnPerson startedAt endedAt }
          finishedRuns { id status endedAt }
        }
      }
    """
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=reader.pk)):
        result = schema.execute_sync(
            document,
            variable_values={"id": str(world.medops_project.guid)},
            context_value=StrawberryContext(_Request(reader)),
        )
    assert result.errors is None, result.errors
    room = result.data["factoryRoom"]
    assert room["approvalsWaiting"] == 1
    assert [r["status"] for r in room["activeRuns"]] == ["running", "running"]
    assert [r["waitingOnPerson"] for r in room["activeRuns"]] == [True, False]
    assert [r["status"] for r in room["finishedRuns"]] == ["failed", "completed"]
