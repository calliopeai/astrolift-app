"""Authoring a github_checks stage through the GraphQL API (#2489)."""

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
from config.schema import schema
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from workflows.models import WorkflowDefinition, WorkflowStage

pytestmark = pytest.mark.django_db

CONFIG = {
    "repo": "ConflictHQ/fossilrepo",
    "sha_source": "pull_request_head",
    "pull_request_path": "build.pr",
    "checks": ["lint", "test"],
}
CREATE = """
mutation($slug: String!, $iteration: JSON) {
  createWorkflowStage(workflowSlug: $slug, kind: "github_checks", outputKey: "ci",
                      timeoutSeconds: 3600, iteration: $iteration) {
    ok
    errors { field messages }
    stage { guid kind iteration timeoutSeconds }
  }
}
"""
UPDATE = """
mutation($guid: ID!, $iteration: JSON) {
  updateWorkflowStage(stageGuid: $guid, iteration: $iteration) { ok errors { field messages } }
}
"""


@pytest.fixture
def author():
    org = Organization.objects.create(name="Gate authors", slug="gate-authors-2489")
    user = get_user_model().objects.create_user(
        username="gate-author", email="gate@example.test", password="pw"
    )
    definition = WorkflowDefinition.objects.create(
        organization=org, name="Story", slug="story-2489", model_label="", pattern_kind="chained"
    )
    return SimpleNamespace(org=org, user=user, definition=definition)


def _execute(author, query, variables):
    with tenant_context(TenantContext(organization_id=author.org.id, actor_user_id=author.user.id)):
        return schema.execute_sync(
            query, variable_values=variables, context_value=SimpleNamespace(user=author.user, request=None)
        )


def test_a_gate_is_created_with_its_validated_configuration(author, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)

    reply = _execute(author, CREATE, {"slug": "story-2489", "iteration": CONFIG})

    assert reply.errors is None
    result = reply.data["createWorkflowStage"]
    assert result["ok"], result["errors"]
    assert result["stage"]["kind"] == "github_checks"
    assert result["stage"]["iteration"] == CONFIG
    stage = author.definition.stages.get()
    assert (stage.kind, stage.iteration, stage.timeout_seconds) == ("github_checks", CONFIG, 3600)


@pytest.mark.parametrize(
    "iteration,message",
    [
        (None, "exactly repo, sha_source, pull_request_path and checks"),
        ({**CONFIG, "checks": []}, "checks must list"),
        ({**CONFIG, "repo": "fossilrepo"}, "owner/name"),
    ],
)
def test_an_incomplete_gate_is_refused_and_nothing_is_written(
    author, permission_resolver, iteration, message
):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)

    reply = _execute(author, CREATE, {"slug": "story-2489", "iteration": iteration})

    result = reply.data["createWorkflowStage"]
    assert not result["ok"]
    assert any(message in text for error in result["errors"] for text in error["messages"])
    assert not author.definition.stages.exists()


def test_creating_a_gate_requires_workflow_update(author, permission_resolver):
    permission_resolver.deny(Permission.WORKFLOW_UPDATE)

    reply = _execute(author, CREATE, {"slug": "story-2489", "iteration": CONFIG})

    assert reply.data is None
    assert [error.extensions["code"] for error in reply.errors] == ["PERMISSION_DENIED"]
    assert not author.definition.stages.exists()


def test_updating_a_gate_validates_its_new_configuration(author, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    stage = WorkflowStage.objects.create(
        definition=author.definition,
        slug="story-2489-ci",
        order=0,
        kind="github_checks",
        output_key="ci",
        iteration=CONFIG,
    )

    changed = {**CONFIG, "checks": ["lint", "test", "conformance"]}
    reply = _execute(author, UPDATE, {"guid": str(stage.guid), "iteration": changed})
    assert reply.data["updateWorkflowStage"] == {"ok": True, "errors": []}
    stage.refresh_from_db()
    assert stage.iteration == changed

    reply = _execute(
        author, UPDATE, {"guid": str(stage.guid), "iteration": {**CONFIG, "sha_source": "branch_head"}}
    )
    result = reply.data["updateWorkflowStage"]
    assert not result["ok"] and result["errors"][0]["field"] == "iteration"
    stage.refresh_from_db()
    assert stage.iteration == changed
