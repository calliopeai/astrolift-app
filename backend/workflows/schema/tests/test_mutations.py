"""Workflow create-mutation regressions (#1016).

Two gaps the workflows grid surfaced:
* createWorkflowStage never set the unique BaseCoreModel.slug, so every stage
  defaulted to "none" and the 2nd stage created anywhere collided — making
  multi-stage workflows impossible via the API.
* create/update_workflow_definition had no pattern_kind arg, so every
  API-created definition was stuck on "single" and fan-out was undefinable.
"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.schema.mutations import Mutation

User = get_user_model()


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user))


class WorkflowCreateMutationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="wf_mut_test", email="wf_mut@test.com", password="pw",
        )
        self.m = Mutation()

    def _make_def(self, slug, pattern_kind=None):
        return self.m.create_workflow_definition(
            _info(self.user),
            name=f"Def {slug}",
            slug=slug,
            model_label="workflows.workflowdefinition",
            states=[{"name": "pending", "label": "Pending", "is_initial": True, "is_final": False}],
            transitions=[],
            pattern_kind=pattern_kind,
        )

    def test_create_definition_sets_pattern_kind(self):
        res = self._make_def("wf-fan", pattern_kind="fan_out")
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(WorkflowDefinition.objects.get(slug="wf-fan").pattern_kind, "fan_out")

    def test_create_definition_defaults_pattern_single(self):
        res = self._make_def("wf-plain")
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(WorkflowDefinition.objects.get(slug="wf-plain").pattern_kind, "single")

    def test_create_definition_rejects_bad_pattern(self):
        res = self._make_def("wf-bad", pattern_kind="nonsense")
        self.assertFalse(res.ok)

    def test_multiple_stages_get_unique_slugs(self):
        """The regression: two stages in one workflow must both persist with
        distinct slugs (not collide on the default 'none')."""
        self._make_def("wf-multi")
        r0 = self.m.create_workflow_stage(
            _info(self.user), workflow_slug="wf-multi", order=0, kind="agent_dispatch",
            on_failure="fail", timeout_seconds=300,
        )
        r1 = self.m.create_workflow_stage(
            _info(self.user), workflow_slug="wf-multi", order=1, kind="agent_dispatch",
            on_failure="fail", timeout_seconds=300,
        )
        self.assertTrue(r0.ok, r0.errors)
        self.assertTrue(r1.ok, r1.errors)
        slugs = set(
            WorkflowStage.objects.filter(definition__slug="wf-multi").values_list("slug", flat=True)
        )
        self.assertEqual(len(slugs), 2, f"stages must have distinct slugs, got {slugs}")
        self.assertNotIn("none", slugs)
