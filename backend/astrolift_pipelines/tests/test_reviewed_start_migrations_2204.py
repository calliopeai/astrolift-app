"""Real PostgreSQL historical-state backfill and collision refusal."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)
OLD = ("astrolift_pipelines", "0009_pipelinerun_definition_digest_and_more")
NEW = ("astrolift_pipelines", "0011_jobrun_cluster_jobrun_k8s_job_uid_and_more")


@pytest.mark.parametrize("collision", [False, True])
def test_historical_run_owners_are_backfilled_and_duplicate_numbers_are_not_rewritten(collision):
    from astrolift_identity.models import Organization

    org = Organization.objects.create(name="Historical migration proof", slug="historical-pipeline-proof")
    executor = MigrationExecutor(connection)
    executor.migrate([OLD])
    apps = executor.loader.project_state([OLD]).apps
    Pipeline = apps.get_model("astrolift_pipelines", "Pipeline")
    Run = apps.get_model("astrolift_pipelines", "PipelineRun")
    pipeline = Pipeline.objects.create(
        organization_id=org.pk, name="historical", repo_url="https://example.test/historical"
    )
    row = Run.objects.create(pipeline_id=pipeline.pk, run_number=7, temporal_workflow_id="legacy-engine-id")
    duplicate = None
    if collision:
        duplicate = Run.objects.create(pipeline_id=pipeline.pk, run_number=7, deleted_at=timezone.now())
    try:
        executor = MigrationExecutor(connection)
        if collision:
            with pytest.raises(RuntimeError, match="identities collide"):
                executor.migrate([NEW])
            assert list(
                Run._base_manager.filter(pipeline_id=pipeline.pk).values_list("run_number", flat=True)
            ) == [
                7,
                7,
            ]
            duplicate.delete()
            executor = MigrationExecutor(connection)
        executor.migrate([NEW])
        current = (
            executor.loader.project_state([NEW])
            .apps.get_model("astrolift_pipelines", "PipelineRun")
            .objects.get(pk=row.pk)
        )
        assert current.organization_id == org.pk
        assert current.run_number == 7 and current.pipeline_version == pipeline.version
        assert current.dispatch_status == "unknown"
        assert current.temporal_run_id == "" and current.cleanup_status == "unknown"
    finally:
        if duplicate is not None:
            Run.objects.filter(pk=duplicate.pk).delete()
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
