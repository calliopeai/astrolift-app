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
