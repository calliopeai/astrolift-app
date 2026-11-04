"""A failed provision is recorded in the app's event log (#2608).

``ProvisionManagedServiceWorkflow`` wrote a terminal failure only to the row's
``status_error``, which the console's managed-services card shows. The app's
event log, which ``astro app events`` reads through
``astroliftEventsPage(appSlug:)``, had no record of it. The workflow now emits
``managed_service.provision.failed`` for an app-owned service, scoped to the
app and its organization, with the same redacted reason the card shows.

Runs the real workflow and its real activities against a Temporal test server
and Postgres; only the provider driver is faked.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from _sdk.managed_service import ProvisionResult

from astrolift_operations.models import Event
from astrolift_services.models import ManagedService
from astrolift_workflows.activities import (
    bounce_workloads_bound_to_managed_service,
    check_managed_service_ready,
    emit_managed_service_provision_failed_event,
    finalize_managed_service_provision,
    mark_managed_service_failed,
    mark_managed_service_provisioning,
    provision_managed_service,
    record_managed_service_failed_step,
)
from astrolift_workflows.inputs import Actor, ProvisionManagedServiceInput
from astrolift_workflows.tests.test_provision_managed_service_temporal import _make_service
from astrolift_workflows.workflows.provision_managed_service import ProvisionManagedServiceWorkflow
from core.testing.temporal import temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)

EVENT_TYPE = "managed_service.provision.failed"

# The driver message from the #2592 report.
AURORA_REFUSAL = (
    "create_db_cluster: An error occurred (InvalidParameterCombination) when calling the "
    "CreateDBCluster operation: PubliclyAccessible isn't supported for DB engine aurora-postgresql."
)

ACTIVITIES = [
    mark_managed_service_provisioning,
    provision_managed_service,
    check_managed_service_ready,
    finalize_managed_service_provision,
    mark_managed_service_failed,
    record_managed_service_failed_step,
    emit_managed_service_provision_failed_event,
    bounce_workloads_bound_to_managed_service,
]


def _refusing_driver(message: str):
    class _RefusingDriver:
        def __init__(self, *, config):
            pass

        def provision(self, spec):
            return ProvisionResult(ok=False, handle="", message=message, errors=["refused"])

    return _RefusingDriver


async def _provision(temporal_env, svc: ManagedService, driver):
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=driver),
        patch("core.cluster_observability.managed_config_for", return_value={}),
    ):
        async with temporal_worker(
            temporal_env, workflows=[ProvisionManagedServiceWorkflow], activities=ACTIVITIES
        ):
            return await temporal_env.client.execute_workflow(
                ProvisionManagedServiceWorkflow.run,
                ProvisionManagedServiceInput(managed_service_id=svc.pk, actor=Actor(kind="system")),
                id=f"ProvisionManagedServiceWorkflow-{svc.guid}",
                task_queue="astrolift-test",
            )


def _failure_events(svc: ManagedService) -> list[Event]:
    return list(Event.objects.filter(event_type=EVENT_TYPE, resource_id=str(svc.guid)))


async def test_a_refused_provision_is_recorded_in_the_apps_event_log(temporal_env):
    from asgiref.sync import sync_to_async

    svc = await sync_to_async(_make_service)()
    organization_id = svc.registered_app.organization_id

    result = await _provision(temporal_env, svc, _refusing_driver(AURORA_REFUSAL))

    assert result.ok is False
    await sync_to_async(svc.refresh_from_db)()
    assert svc.status == ManagedService.Status.FAILED
    events = await sync_to_async(_failure_events)(svc)
    assert len(events) == 1
    event = events[0]
    # The per-app feed matches on the app FK inside the caller's organization.
    assert event.organization_id == organization_id
    assert event.registered_app_id == svc.registered_app_id
    assert event.severity == "error"
    assert event.resource_kind == "managed_service"
    assert event.payload == {
        "managed_service_guid": str(svc.guid),
        "name": "records",
        "kind": "postgres",
        "variant": "aurora_postgres_serverless_v2",
        "environment": "production",
        "step": "provision",
        "reason": svc.status_error,
        "severity": "error",
    }
    assert AURORA_REFUSAL in event.payload["reason"]


async def test_a_credential_in_the_drivers_message_stays_out_of_the_event(temporal_env):
    from asgiref.sync import sync_to_async

    svc = await sync_to_async(_make_service)()

    await _provision(
        temporal_env, svc, _refusing_driver("config rejected: password=hunter2 for host db.internal")
    )

    events = await sync_to_async(_failure_events)(svc)
    assert len(events) == 1
    assert "hunter2" not in json.dumps(events[0].payload)
    assert "password=***redacted***" in events[0].payload["reason"]


def test_a_project_owned_service_has_no_app_log_and_emits_nothing():
    from astrolift_workflows.activities.managed_service_lifecycle import _emit_provision_failed_event_sync
    from core.events import register_event_subscriber, unregister_event_subscriber

    svc = _make_service()
    svc.project = svc.registered_app.project
    svc.tenant_cluster = svc.app_environment.tenant_cluster
    svc.registered_app = None
    svc.app_environment = None
    svc.save()
    # The writer drops an event with no organization, so an empty table would
    # not show that nothing was emitted; a subscriber sees every emit.
    seen: list[str] = []

    def subscriber(envelope) -> None:
        seen.append(envelope.event_type)

    register_event_subscriber(subscriber)
    try:
        _emit_provision_failed_event_sync(svc.pk, "provision", "provision_managed_service failed: refused")
    finally:
        unregister_event_subscriber(subscriber)

    assert EVENT_TYPE not in seen
    assert not Event.objects.filter(event_type=EVENT_TYPE).exists()
