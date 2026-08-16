from django.contrib.auth import get_user_model
from django.test import TestCase

from workflows.models import WorkflowDefinition

User = get_user_model()


class WorkflowDefinitionTypeTest(TestCase):

    def setUp(self):
        from organization.models import Organization, OrganizationMember
        self.org = Organization.objects.create(name='TestOrg')
        self.user = User.objects.create_superuser(
            username='workflowDefinition_test', email='workflowDefinition@test.com', password='testpass',
        )
        OrganizationMember.objects.create(
            organization=self.org, member=self.user, is_active=True,
        )
        self.user.profile.active_organization = self.org
        self.user.profile.save()

        self.instance = WorkflowDefinition.objects.create(
            name='Test WorkflowDefinition',
            created_by=self.user,
            updated_by=self.user,
        )

    def test_instance_created(self):
        obj = WorkflowDefinition.objects.get(pk=self.instance.pk)
        self.assertEqual(obj.name, 'Test WorkflowDefinition')
        self.assertIsNotNone(obj.guid)
        self.assertIsNotNone(obj.slug)

    def test_instance_has_tracking_fields(self):
        obj = WorkflowDefinition.objects.get(pk=self.instance.pk)
        self.assertIsNotNone(obj.created_at)
        self.assertIsNotNone(obj.updated_at)
        self.assertEqual(obj.created_by, self.user)


class WorkflowStageTypeGateFieldsTest(TestCase):
    """#1095: role/prompt/approvers must be queryable on WorkflowStageType —
    the stage builder reads the gate config back after save."""

    def setUp(self):
        from organization.models import Organization, OrganizationMember

        self.org = Organization.objects.create(name='StageFieldsOrg')
        self.user = User.objects.create_superuser(
            username='wf_stage_fields', email='wf_stage_fields@test.com', password='testpass',
        )
        OrganizationMember.objects.create(
            organization=self.org, member=self.user, is_active=True,
        )
        self.user.profile.active_organization = self.org
        self.user.profile.save()

    def test_stage_gate_fields_round_trip(self):
        from django.test import RequestFactory

        from config.schema import schema
        from core.schema.context import StrawberryContext
        from workflows.models import WorkflowStage

        definition = WorkflowDefinition.objects.create(
            name='Gate Def',
            slug='gate-def',
            created_by=self.user,
            updated_by=self.user,
        )
        WorkflowStage.objects.create(
            definition=definition,
            slug='gate-def-stage-0',
            order=0,
            kind=WorkflowStage.StageKind.HUMAN_GATE,
            role='reviewer',
            prompt='Ship it?',
            approvers=['team-leads'],
        )

        request = RequestFactory().get('/app/gql/config/')
        request.user = self.user
        request.session = {}
        result = schema.execute_sync(
            '{ workflowStages(workflowSlug: "gate-def") { order kind role prompt approvers } }',
            context_value=StrawberryContext(request),
        )
        self.assertIsNone(result.errors, f'unexpected errors: {result.errors}')
        stages = result.data['workflowStages']
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]['kind'], 'human_gate')
        self.assertEqual(stages[0]['role'], 'reviewer')
        self.assertEqual(stages[0]['prompt'], 'Ship it?')
        self.assertEqual(stages[0]['approvers'], ['team-leads'])


STAGE_EXECUTIONS_QUERY = '''
query($workflowId: String!, $runId: String!) {
  workflowStageExecutions(workflowId: $workflowId, runId: $runId) {
    stageOrder
    stageKind
    stageRole
    stageApprovers
    status
    humanGateState
    humanGateNote
  }
}
'''


class WorkflowStageExecutionGateStateTest(TestCase):
    """astrolift-cli#69 / vscode#341 inc. 4: a run's per-stage rows must carry
    role, declared approvers, and a derived gate state, so a remote client can
    render "waiting on approval" as read-only platform truth instead of
    reverse-engineering the ``output`` JSON.
    """

    def setUp(self):
        from astrolift_identity.models import Organization

        self.org = Organization.objects.create(name='Gate State Org', slug='gate-state-org')
        self.user = User.objects.create_user(
            username='wf_gate_state', email='wf_gate_state@test.com', password='testpass',
        )
        self.definition = WorkflowDefinition.objects.create(
            name='Gate State Def',
            slug='gate-state-def',
            organization=self.org,
            created_by=self.user,
            updated_by=self.user,
        )

    def _stage(self, order, kind, **kwargs):
        from workflows.models import WorkflowStage

        return WorkflowStage.objects.create(
            definition=self.definition,
            slug=f'gate-state-def-stage-{order}',
            order=order,
            kind=kind,
            **kwargs,
        )

    def _execution(self, run, stage, status, output=None):
        from workflows.models import WorkflowStageExecution

        # ``WorkflowStageExecution.slug`` is globally unique and the model has
        # no name to derive one from, so an unset slug collides on the second
        # row in a test (the worker mints "wfse-<run>-<stage>-a<attempt>").
        return WorkflowStageExecution.objects.create(
            slug=f'wfse-{run.pk}-{stage.pk}-a1',
            workflow_run=run,
            stage=stage,
            status=status,
            output=output,
        )

    def _run(self, workflow_id='wfid-gate', run_id='rid-gate', organization=None):
        from astrolift_operations.models import WorkflowRun

        return WorkflowRun.objects.create(
            workflow_kind='WorkflowDefinitionRunWorkflow',
            workflow_definition=self.definition,
            workflow_id=workflow_id,
            run_id=run_id,
            organization=self.org if organization is None else organization,
        )

    def _execute(self, workflow_id='wfid-gate', run_id='rid-gate'):
        """Run the query the way a client does — through ``schema.execute_sync``
        rather than by invoking the resolver — so the derived fields are
        exercised over the real Strawberry binding."""
        from django.test import RequestFactory

        from config.schema import schema
        from core.schema.context import StrawberryContext
        from core.tenancy import TenantContext, tenant_context

        request = RequestFactory().get('/app/gql/config/')
        request.user = self.user
        request.session = {}
        tenant = TenantContext(organization_id=self.org.pk, actor_user_id=self.user.pk)
        with tenant_context(tenant):
            result = schema.execute_sync(
                STAGE_EXECUTIONS_QUERY,
                variable_values={'workflowId': workflow_id, 'runId': run_id},
                context_value=StrawberryContext(request),
            )
        self.assertIsNone(result.errors, f'unexpected errors: {result.errors}')
        return result.data['workflowStageExecutions']

    def test_open_gate_reports_pending_with_its_approvers(self):
        """The signal #341 inc. 4 renders: an open gate is ``pending`` and
        names who it waits on, while a sibling agent stage carries no gate
        state at all."""
        from workflows.models import WorkflowStage, WorkflowStageExecution

        build = self._stage(0, WorkflowStage.StageKind.AGENT_DISPATCH, role='builder')
        gate = self._stage(
            1,
            WorkflowStage.StageKind.HUMAN_GATE,
            role='reviewer',
            approvers=['team-leads', 'sre'],
        )
        run = self._run()
        self._execution(run, build, WorkflowStageExecution.Status.COMPLETED, output={'summary': 'built'})
        self._execution(run, gate, WorkflowStageExecution.Status.RUNNING)

        rows = self._execute()
        self.assertEqual(len(rows), 2)

        agent_row, gate_row = rows
        self.assertEqual(agent_row['stageKind'], 'agent_dispatch')
        self.assertEqual(agent_row['stageRole'], 'builder')
        self.assertEqual(agent_row['stageApprovers'], [])
        # A non-gate stage must not claim a gate state, even mid-run.
        self.assertEqual(agent_row['humanGateState'], '')
        self.assertEqual(agent_row['humanGateNote'], '')

        self.assertEqual(gate_row['stageKind'], 'human_gate')
        self.assertEqual(gate_row['stageRole'], 'reviewer')
        self.assertEqual(gate_row['stageApprovers'], ['team-leads', 'sre'])
        self.assertEqual(gate_row['status'], 'running')
        self.assertEqual(gate_row['humanGateState'], 'pending')
        self.assertEqual(gate_row['humanGateNote'], '')

    def test_decided_gates_report_the_recorded_decision_and_note(self):
        """``record_human_gate_decision`` is the only writer of gate outcome;
        an approval, a rejection, and the timeout (recorded as a rejection
        noted ``gate timed out``) must each read back distinctly."""
        from workflows.models import WorkflowStage, WorkflowStageExecution

        approved_stage = self._stage(0, WorkflowStage.StageKind.HUMAN_GATE, role='reviewer')
        rejected_stage = self._stage(1, WorkflowStage.StageKind.HUMAN_GATE, role='reviewer')
        expired_stage = self._stage(2, WorkflowStage.StageKind.HUMAN_GATE, role='reviewer')
        run = self._run()

        self._execution(
            run,
            approved_stage,
            WorkflowStageExecution.Status.COMPLETED,
            output={'human_gate': {'decision': 'approved', 'decided_by_user_id': 7, 'note': 'ship it'}},
        )
        self._execution(
            run,
            rejected_stage,
            WorkflowStageExecution.Status.FAILED,
            output={'human_gate': {'decision': 'rejected', 'decided_by_user_id': 7, 'note': 'not yet'}},
        )
        self._execution(
            run,
            expired_stage,
            WorkflowStageExecution.Status.FAILED,
            output={
                'human_gate': {'decision': 'rejected', 'decided_by_user_id': None, 'note': 'gate timed out'},
            },
        )

        approved, rejected, expired = self._execute()
        self.assertEqual(approved['humanGateState'], 'approved')
        self.assertEqual(approved['humanGateNote'], 'ship it')
        self.assertEqual(rejected['humanGateState'], 'rejected')
        self.assertEqual(rejected['humanGateNote'], 'not yet')
        self.assertEqual(expired['humanGateState'], 'rejected')
        self.assertEqual(expired['humanGateNote'], 'gate timed out')

    def test_gate_state_tolerates_a_non_dict_output(self):
        """``output`` is free-form JSON — a gate row carrying a chained
        scalar/list payload rather than the decision dict must read as
        ``closed`` (terminal, undecided), not raise."""
        from workflows.models import WorkflowStage, WorkflowStageExecution

        gate = self._stage(0, WorkflowStage.StageKind.HUMAN_GATE, role='reviewer')
        run = self._run()
        self._execution(run, gate, WorkflowStageExecution.Status.SKIPPED, output=['not', 'a', 'dict'])

        (row,) = self._execute()
        self.assertEqual(row['humanGateState'], 'closed')
        self.assertEqual(row['humanGateNote'], '')
