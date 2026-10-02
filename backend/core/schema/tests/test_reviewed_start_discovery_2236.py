"""Anonymous metadata describes starts without granting execution authority."""

import json

import pytest
from django.conf import settings

from astrolift_pipelines.models import PipelineRun
from astrolift_pipelines.tests import test_run_contract_api_2204 as pipeline_api
from core.permissions import Permission
from workflows.models import WorkflowDefinitionStart
from workflows.tests import test_reviewed_start_api_2236 as workflow_api

pytestmark = pytest.mark.django_db

world = workflow_api.world
configuration = workflow_api.configuration
pipeline_world = pipeline_api.world

CAPABILITIES = {
    "workflows.reviewed_definition_starts",
    "workflows.definition_input_contracts",
    "workflows.definition_start_recovery",
    "pipelines.reviewed_starts",
    "pipelines.versioned_start_requests",
    "pipelines.start_request_recovery",
    "pipelines.exact_execution_cancellation",
    "pipelines.bounded_run_details",
}


def discover(client):
    response = client.post(
        f"/{settings.BASE_URL}gql/config/public/",
        data=json.dumps({"query": "{astroliftServerInfo{capabilities}}"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    result = response.json()
    assert not result.get("errors")
    capabilities = result["data"]["astroliftServerInfo"]["capabilities"]
    assert CAPABILITIES <= set(capabilities)
    assert len(capabilities) == len(set(capabilities)) < 128
    assert len(response.content) < 8192
    assert "sessionid" not in response.cookies
    return capabilities


def test_anonymous_discovery_cannot_invoke_any_reviewed_contract(client, settings):
    settings.DEBUG = False
    discover(client)
    for query in (
        '{workflowDefinitionStartRequest(requestId:"unknown"){id}}',
        'mutation{startWorkflowDefinition(input:{definitionId:"unknown",expectedRevision:"r",expectedInputSchemaDigest:"s",requestId:"key",confirmed:true}){ok}}',
        'mutation{startPipelineRun(input:{pipelineId:"unknown",expectedVersion:1,requestId:"key",confirmed:true}){ok}}',
        'mutation{cancelPipelineRun(runId:"unknown",confirmed:true){ok}}',
    ):
        response = client.post(
            f"/{settings.BASE_URL}gql/config/public/",
            data=json.dumps({"query": query}),
            content_type="application/json",
        )
        assert response.status_code in {200, 400}
        body = response.json()
        assert body.get("errors")
        assert not body.get("data")
    assert not WorkflowDefinitionStart.objects.exists()
    assert not PipelineRun.objects.exists()


def test_discovery_does_not_turn_workflow_read_into_trigger_grant(client, world, permission_resolver):
    discover(client)
    permission_resolver.deny(Permission.WORKFLOW_TRIGGER)
    review = workflow_api.execute(world, workflow_api.REVIEW, {"id": str(world.definition.guid)})
    assert review.errors is None
    result = workflow_api.execute(
        world,
        workflow_api.START,
        {
            "input": {
                "definitionId": str(world.definition.guid),
                "expectedRevision": review.data["workflowDefinitionById"]["revision"],
                "expectedInputSchemaDigest": review.data["workflowDefinitionById"]["inputContract"]["digest"],
                "requestId": "discovery-does-not-authorize",
                "confirmed": True,
            }
        },
    )
    assert result.errors is None
    assert result.data["startWorkflowDefinition"]["errors"][0]["code"] == "PERMISSION_DENIED"
    assert not WorkflowDefinitionStart.objects.exists()


def test_discovery_does_not_grant_pipeline_update(client, pipeline_world, permission_resolver):
    discover(client)
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.deny(Permission.APP_UPDATE)
    result = pipeline_api.execute(
        pipeline_world,
        pipeline_api.START,
        {
            "input": {
                "pipelineId": str(pipeline_world.pipeline.guid),
                "expectedVersion": pipeline_world.pipeline.version,
                "requestId": "discovery-does-not-authorize-pipeline",
                "confirmed": True,
            }
        },
    )
    assert result.errors is None
    assert result.data["startPipelineRun"]["errors"][0]["code"] == "PERMISSION_DENIED"
    assert not PipelineRun.objects.exists()
