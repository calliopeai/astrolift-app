"""An unacknowledged cancel never fabricates local terminal state (#2204).

The production Temporal/Kubernetes tests cover acknowledged cancellation,
engine closure, owned resource cleanup and step settlement. These legacy
service checks now pin the prerequisite and audit boundary instead of the
old unsafe local cascade when no engine exists.
"""

import inspect

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Job, JobRun, Pipeline, PipelineRun, Step, StepRun
from astrolift_pipelines.run_contracts import PipelineContractError, request_pipeline_cancellation
from astrolift_workflows.activities.pipeline_job_spawn import _mark_pipeline_run_failed_sync

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("run_status", ["pending", "running", "success", "failure", "cancelled"])
@pytest.mark.parametrize("job_status", ["pending", "running"])
def test_missing_exact_engine_identity_never_cascades(run_status, job_status):
    org = Organization.objects.create(name="Cancellation contract proof", slug="cancel-contract-proof")
    pipeline = Pipeline.objects.create(
        organization=org, name="cancel", repo_url="https://example.test/cancel"
    )
    run = PipelineRun.objects.create(pipeline=pipeline, run_number=1, status=run_status)
    job = Job.objects.create(pipeline=pipeline, pipeline_run=run, job_id="hold", name="Hold")
    job_run = JobRun.objects.create(pipeline_run=run, job=job, status=job_status)
    step = Step.objects.create(job=job, step_id="hold", position=0, run="sleep 30")
    step_run = StepRun.objects.create(job_run=job_run, step=step, status="running")
    with pytest.raises(PipelineContractError, match="engine execution"):
        request_pipeline_cancellation(
            run,
            expected_version=run.version,
            workflow_id=run.temporal_workflow_id,
            temporal_run_id=run.temporal_run_id,
            trusted_internal=True,
        )
    run.refresh_from_db()
    job_run.refresh_from_db()
    step_run.refresh_from_db()
    assert (run.status, job_run.status, step_run.status) == (run_status, job_status, "running")
    assert run.finished_at is None and step_run.finished_at is None


def test_signal_requires_the_exact_temporal_run_id():
    from astrolift_workflows.client import signal_pipeline_execution

    signature = inspect.signature(signal_pipeline_execution)
    signature.bind("workflow-id", "exact-run-id")
    with pytest.raises(TypeError):
        signature.bind("workflow-id")


def test_actual_worker_terminal_cancellation_reaches_the_event_pipeline():
    from core.events import register_event_subscriber, unregister_event_subscriber

    org = Organization.objects.create(name="Cancellation audit proof", slug="cancel-audit-proof")
    pipeline = Pipeline.objects.create(
        organization=org, name="cancel", repo_url="https://example.test/cancel"
    )
    run = PipelineRun.objects.create(pipeline=pipeline, run_number=1, status="running")
    seen = []
    register_event_subscriber(seen.append)
    try:
        _mark_pipeline_run_failed_sync(run.pk, "cancelled by signal")
    finally:
        unregister_event_subscriber(seen.append)
    run.refresh_from_db()
    assert run.status == "cancelled"
    assert "pipeline_run.cancelled" in [event.event_type for event in seen]
