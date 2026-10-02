from types import SimpleNamespace

import pytest

from astrolift_pipelines.models import Job, JobRun, Step, StepRun
from astrolift_pipelines.tests import test_run_contracts_2204 as fixtures
from config.schema import schema
from core.permissions import Permission
from core.tenancy import tenant_context

world = fixtures.world
local_cache = fixtures.local_cache

pytestmark = pytest.mark.django_db

START = "mutation($input:StartPipelineRunInput!){startPipelineRun(input:$input){ok errors{code message} data{id version pipelineId organizationId appId pipelineVersion requestId temporalWorkflowId temporalRunId dispatchStatus cancellationStatus cleanupStatus}}}"


def execute(world, query, variables=None):
    with tenant_context(world.tenant):
        return schema.execute_sync(
            query, variable_values=variables, context_value=SimpleNamespace(user=world.user, request=None)
        )


def test_pipeline_review_and_uncertain_reconciliation_keep_exact_identity(
    world, permission_resolver, settings
):
    permission_resolver.grant(Permission.APP_READ)
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    read = execute(
        world,
        "query($id:String!){astroliftPipeline(id:$id){id version organizationId}}",
        {"id": str(world.pipeline.guid)},
    )
    assert read.errors is None
    assert read.data["astroliftPipeline"]["version"] == world.pipeline.version
    data = {
        "pipelineId": str(world.pipeline.guid),
        "expectedVersion": world.pipeline.version,
        "requestId": "api-pipeline-stable",
        "confirmed": True,
    }
    result = execute(world, START, {"input": data})
    assert result.errors is None
    start = result.data["startPipelineRun"]
    assert start["ok"] is False
    assert start["data"]["dispatchStatus"] == "uncertain"
    assert start["data"]["organizationId"] == str(world.org.guid)
    assert start["data"]["temporalRunId"] is None
    second = execute(world, START, {"input": data}).data["startPipelineRun"]
    assert second["data"]["id"] == start["data"]["id"]
    recovered = execute(
        world,
        "query($id:GUID!,$key:String!){pipelineStartRequest(pipelineId:$id,requestId:$key){id dispatchStatus}}",
        {"id": str(world.pipeline.guid), "key": data["requestId"]},
    )
    assert recovered.errors is None
    assert recovered.data["pipelineStartRequest"]["id"] == start["data"]["id"]


def test_legacy_clients_receive_review_prerequisite_without_side_effects(world):
    result = execute(
        world,
        "mutation($id:GUID!){triggerPipelineRun(pipelineId:$id){ok errors{code message}}}",
        {"id": str(world.pipeline.guid)},
    )
    assert result.errors is None
    assert result.data["triggerPipelineRun"]["errors"][0]["code"] == "PRECONDITION"
    assert world.pipeline.runs.count() == 0
    result = execute(
        world,
        "mutation($id:GUID!){cancelPipelineRun(runId:$id){ok errors{code message}}}",
        {"id": str(world.pipeline.guid)},
    )
    assert result.errors is None
    assert result.data["cancelPipelineRun"]["errors"][0]["code"] == "PRECONDITION"


def test_nested_projection_is_bounded_with_cursor_access_to_every_job_and_step(world, permission_resolver):
    from astrolift_pipelines.tests.test_run_contracts_2204 import reserve

    permission_resolver.grant(Permission.APP_READ)
    run = reserve(world)
    jobs = []
    for index in range(21):
        job = Job.objects.create(
            pipeline=world.pipeline, pipeline_run=run, job_id=f"job-{index:02}", name=f"Job{index}"
        )
        jobs.append(JobRun.objects.create(pipeline_run=run, job=job, log_excerpt="x" * 65000))
    for index in range(53):
        step = Step.objects.create(job=jobs[0].job, position=index, step_id=f"step-{index}", run="true")
        StepRun.objects.create(job_run=jobs[0], step=step)
    query = "query($id:String!){astroliftPipelineRun(id:$id){jobsTruncated jobRuns{id stepsTruncated logKind logTruncated logExcerpt stepRuns{id}}}}"
    result = execute(world, query, {"id": str(run.guid)})
    assert result.errors is None
    detail = result.data["astroliftPipelineRun"]
    assert detail["jobsTruncated"] is True
    assert len(detail["jobRuns"]) == 20
    assert detail["jobRuns"][0]["stepsTruncated"] is True
    assert len(detail["jobRuns"][0]["stepRuns"]) == 50
    assert detail["jobRuns"][0]["logKind"] == "recorded"
    assert len(detail["jobRuns"][0]["logExcerpt"]) == 64000
    job_query = "query($id:GUID!,$after:String){pipelineJobRunsPage(runId:$id,limit:20,after:$after){items{id} nextCursor}}"
    first = execute(world, job_query, {"id": str(run.guid)}).data["pipelineJobRunsPage"]
    second = execute(world, job_query, {"id": str(run.guid), "after": first["nextCursor"]}).data[
        "pipelineJobRunsPage"
    ]
    assert len(first["items"]) == 20 and len(second["items"]) == 1
    step_query = "query($id:GUID!,$job:GUID!,$after:String){pipelineStepRunsPage(runId:$id,jobRunId:$job,limit:50,after:$after){items{id} nextCursor}}"
    first = execute(world, step_query, {"id": str(run.guid), "job": str(jobs[0].guid)}).data[
        "pipelineStepRunsPage"
    ]
    second = execute(
        world, step_query, {"id": str(run.guid), "job": str(jobs[0].guid), "after": first["nextCursor"]}
    ).data["pipelineStepRunsPage"]
    assert len(first["items"]) == 50 and len(second["items"]) == 3
