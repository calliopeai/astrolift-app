from __future__ import annotations

import asyncio

from temporalio import workflow as temporalio_workflow

from astrolift_workflows.activities import (
    check_managed_service_ready,
    finalize_managed_service_provision,
    finalize_managed_service_update,
    mark_managed_service_failed,
    mark_managed_service_provisioning,
    provision_managed_service,
    update_managed_service,
)
from astrolift_workflows.inputs import (
    Actor,
    ProvisionManagedServiceInput,
    UpdateManagedServiceInput,
)
from astrolift_workflows.workflows.provision_managed_service import (
    ProvisionManagedServiceWorkflow,
)
from astrolift_workflows.workflows.update_managed_service import (
    UpdateManagedServiceWorkflow,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _no_sleep(*args, **kwargs) -> None:
    return None


def test_update_timeout_marks_failed_instead_of_finalizing(monkeypatch) -> None:
    failed_messages: list[str] = []

    async def execute(activity_fn, *args, **kwargs):
        if activity_fn is update_managed_service:
            return {"handle": "search/example"}
        if activity_fn is check_managed_service_ready:
            return "updating"
        if activity_fn is mark_managed_service_failed:
            failed_messages.append(kwargs["args"][1])
            return None
        if activity_fn is finalize_managed_service_update:
            raise AssertionError("timed-out update must not finalize")
        raise AssertionError(f"unexpected activity {activity_fn}")

    monkeypatch.setattr(temporalio_workflow, "execute_activity", execute)
    monkeypatch.setattr(temporalio_workflow, "sleep", _no_sleep)
    result = _run(
        UpdateManagedServiceWorkflow().run(
            UpdateManagedServiceInput(
                managed_service_id=1,
                actor=Actor(kind="system"),
            )
        )
    )

    assert result.ok is False
    assert "timed out" in result.message
    assert failed_messages == [result.message]


def test_provision_timeout_marks_failed_instead_of_finalizing(monkeypatch) -> None:
    failed_messages: list[str] = []

    async def execute(activity_fn, *args, **kwargs):
        if activity_fn is mark_managed_service_provisioning:
            return None
        if activity_fn is provision_managed_service:
            return {"handle": "postgres/example", "ready": False}
        if activity_fn is check_managed_service_ready:
            return "provisioning"
        if activity_fn is mark_managed_service_failed:
            failed_messages.append(kwargs["args"][1])
            return None
        if activity_fn is finalize_managed_service_provision:
            raise AssertionError("timed-out provision must not finalize")
        raise AssertionError(f"unexpected activity {activity_fn}")

    monkeypatch.setattr(temporalio_workflow, "execute_activity", execute)
    monkeypatch.setattr(temporalio_workflow, "sleep", _no_sleep)
    result = _run(
        ProvisionManagedServiceWorkflow().run(
            ProvisionManagedServiceInput(
                managed_service_id=1,
                actor=Actor(kind="system"),
            )
        )
    )

    assert result.ok is False
    assert "timed out" in result.message
    assert failed_messages == [result.message]
