"""Webhook dispatch keeps a reserved identity even during engine outage (#2204)."""

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Pipeline, PipelineRun
from astrolift_pipelines.run_contracts import PipelineContractError, reserve_pipeline_run
from astrolift_pipelines.webhook_views import _dispatch_pipeline_run

pytestmark = pytest.mark.django_db


@pytest.fixture
def reserved(settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    org = Organization.objects.create(name="Webhook dispatch proof", slug="webhook-dispatch-proof")
    pipeline = Pipeline.objects.create(organization=org, name="ci", repo_url="https://example.test/ci")
    run = reserve_pipeline_run(
        pipeline_id=pipeline.pk,
        expected_version=pipeline.version,
        request_id="webhook-delivery-proof",
        actor_key="webhook:github",
        trusted_webhook=True,
    )
    return pipeline, run


def test_outage_keeps_exact_reserved_workflow_identity_and_retry_state(reserved):
    pipeline, run = reserved
    with pytest.raises(PipelineContractError, match="uncertain"):
        _dispatch_pipeline_run(run)
    run.refresh_from_db()
    assert run.temporal_workflow_id == f"pipeline-run-{run.guid}"
    assert run.dispatch_status == "uncertain" and run.temporal_run_id == ""
    repeated = reserve_pipeline_run(
        pipeline_id=pipeline.pk,
        expected_version=pipeline.version,
        request_id="webhook-delivery-proof",
        actor_key="webhook:github",
        trusted_webhook=True,
    )
    assert repeated.pk == run.pk and PipelineRun.objects.count() == 1


def test_delivery_recovers_its_existing_record_after_pipeline_version_changes(reserved):
    pipeline, run = reserved
    pipeline.default_branch = "changed"
    pipeline.save()
    repeated = reserve_pipeline_run(
        pipeline_id=pipeline.pk,
        expected_version=pipeline.version,
        request_id="webhook-delivery-proof",
        actor_key="webhook:github",
        trusted_webhook=True,
    )
    assert repeated.pk == run.pk
    with pytest.raises(PipelineContractError):
        reserve_pipeline_run(
            pipeline_id=pipeline.pk,
            expected_version=pipeline.version,
            request_id="webhook-delivery-proof",
            ref="another-branch",
            actor_key="webhook:github",
            trusted_webhook=True,
        )
