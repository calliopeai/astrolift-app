"""Public workflow discovery advertises contracts, without execution authority."""

import json
from pathlib import Path

import pytest
from graphql import build_schema, parse, validate

from workflows.models import WorkflowDefinitionStart, WorkflowStageExecution

pytestmark = pytest.mark.django_db


def test_actual_anonymous_http_discovery_advertises_bounded_contracts_only(client, settings):
    settings.DEBUG = False
    response = client.post(
        f"/{settings.BASE_URL}gql/config/public/",
        data=json.dumps({"query": "{astroliftServerInfo{capabilities}}"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.json()
    assert not payload.get("errors")
    capabilities = payload["data"]["astroliftServerInfo"]["capabilities"]
    assert {"workflows.bounded_review_loops", "workflows.serial_collections"} <= set(capabilities)
    assert len(capabilities) == len(set(capabilities)) < 128
    assert len(response.content) < 8192
    assert "sessionid" not in response.cookies
    for query in (
        '{workflowStages(workflowSlug:"private-workflow"){guid maxAttempts backEdge iteration}}',
        'mutation{updateWorkflowStage(stageGuid:"private-stage",maxAttempts:2,iteration:{}){ok}}',
        'mutation{signalWorkflowInstance(workflowId:"private-run",signalName:"abort"){ok}}',
    ):
        denied = client.post(
            f"/{settings.BASE_URL}gql/config/public/",
            data=json.dumps({"query": query}),
            content_type="application/json",
        )
        assert denied.status_code in {200, 400}
        assert denied.json().get("errors")
        assert not denied.json().get("data")
    assert not WorkflowDefinitionStart.objects.exists()
    assert not WorkflowStageExecution.objects.exists()


def test_advertised_metadata_documents_validate_against_actual_paired_sdl():
    root = Path(__file__).resolve().parents[4]
    document = parse(
        'query{workflowStages(workflowSlug:"review"){guid kind maxAttempts backEdge iteration}'
        'workflowStageExecutions(workflowId:"run",runId:"exact-run"){guid status attemptNumber '
        "roundNumber causedBy{edge reason maxRounds edgeRound} collectionIndex collectionStageId "
        "collectionParentExecutionGuid fanoutIndex fanoutParentExecutionGuid startedAt endedAt}}"
    )
    for filename in ("backend/schema.graphql", "frontend/schema.graphql"):
        assert not validate(build_schema((root / filename).read_text()), document)
