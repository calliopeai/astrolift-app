from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections
from django.db.models.signals import post_save

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from workflows.models import WorkflowDefinition
from workflows.run_service import build_workflow_definition_run_input


@pytest.mark.django_db(transaction=True)
def test_overlapping_initial_inserts_keep_distinct_execution_handles():
    org = Organization.objects.create(name="Concurrent starts", slug="concurrent-starts")
    definition = WorkflowDefinition.objects.create(
        name="Concurrent starts", slug="concurrent-starts", organization=org
    )
    inserted = Barrier(2, timeout=10)

    def overlap_initial_inserts(sender, instance, created, **kwargs):
        if created and instance.workflow_definition_id == definition.pk:
            inserted.wait()

    def build():
        try:
            return build_workflow_definition_run_input(definition, organization_id=org.pk)
        finally:
            connections.close_all()

    post_save.connect(overlap_initial_inserts, sender=WorkflowRun)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(build) for _ in range(2)]
            errors = [future.exception(timeout=20) for future in futures]
            assert not any(errors), errors
            results = [future.result(timeout=20) for future in futures]
    finally:
        post_save.disconnect(overlap_initial_inserts, sender=WorkflowRun)

    assert len({run.pk for run, _, _ in results}) == 2
    assert len({workflow_id for _, _, workflow_id in results}) == 2
    for run, run_input, workflow_id in results:
        run.refresh_from_db()
        assert run.workflow_id == workflow_id == f"WorkflowDefinitionRunWorkflow-{run.pk}"
        assert run.run_id == ""
        assert run.organization_id == org.pk
        assert run_input.workflow_run_id == str(run.pk)
        assert run_input.workflow_definition_id == str(definition.pk)
