"""ProvisionManagedServiceWorkflow resumes from the recorded failed step (#2606).

Activities are faked, so no Temporal server runs; the same harness as the
timeout tests.
"""

from __future__ import annotations

import asyncio

from temporalio import workflow as temporalio_workflow

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
from astrolift_workflows.workflows.provision_managed_service import ProvisionManagedServiceWorkflow


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _no_sleep(*args, **kwargs) -> None:
    return None


def _input() -> ProvisionManagedServiceInput:
    return ProvisionManagedServiceInput(managed_service_id=1, actor=Actor(kind="system"))


def test_a_resume_from_finalize_skips_the_driver_call(monkeypatch) -> None:
    calls: list[str] = []

    async def execute(activity_fn, *args, **kwargs):
        if activity_fn is mark_managed_service_provisioning:
            return {"failed_step": "finalize", "backend_ref": "postgres/records"}
        if activity_fn is provision_managed_service:
            raise AssertionError("a resume past the driver call must not provision again")
        if activity_fn is check_managed_service_ready:
            raise AssertionError("a resume from finalize must not wait again")
        if activity_fn is finalize_managed_service_provision:
            calls.append(kwargs["args"][1])
            return []
        if activity_fn is bounce_workloads_bound_to_managed_service:
            return None
        raise AssertionError(f"unexpected activity {activity_fn}")

    monkeypatch.setattr(temporalio_workflow, "execute_activity", execute)
    monkeypatch.setattr(temporalio_workflow, "sleep", _no_sleep)

    result = _run(ProvisionManagedServiceWorkflow().run(_input()))

    assert result.ok is True
    assert calls == ["postgres/records"]


def test_a_first_attempt_with_no_failed_step_runs_from_the_beginning(monkeypatch) -> None:
    seen: list[object] = []

    async def execute(activity_fn, *args, **kwargs):
        if activity_fn is mark_managed_service_provisioning:
            return {"failed_step": "", "backend_ref": ""}
        if activity_fn is provision_managed_service:
            seen.append("provision")
            return {"handle": "postgres/records", "ready": True}
        if activity_fn is finalize_managed_service_provision:
            return []
        if activity_fn is bounce_workloads_bound_to_managed_service:
            return None
        raise AssertionError(f"unexpected activity {activity_fn}")

    monkeypatch.setattr(temporalio_workflow, "execute_activity", execute)
    monkeypatch.setattr(temporalio_workflow, "sleep", _no_sleep)

    result = _run(ProvisionManagedServiceWorkflow().run(_input()))

    assert result.ok is True
    assert seen == ["provision"]


def test_a_finalize_failure_records_the_step_and_keeps_the_error(monkeypatch) -> None:
    recorded: list[str] = []
    failed: list[str] = []
    events: list[list] = []

    async def execute(activity_fn, *args, **kwargs):
        if activity_fn is mark_managed_service_provisioning:
            return {"failed_step": "", "backend_ref": ""}
        if activity_fn is provision_managed_service:
            return {"handle": "postgres/records", "ready": True}
        if activity_fn is finalize_managed_service_provision:
            raise RuntimeError("database went away")
        if activity_fn is mark_managed_service_failed:
            failed.append(kwargs["args"][1])
            return None
        if activity_fn is record_managed_service_failed_step:
            recorded.append(kwargs["args"][1])
            return None
        if activity_fn is emit_managed_service_provision_failed_event:
            events.append(kwargs["args"])
            return None
        raise AssertionError(f"unexpected activity {activity_fn}")

    monkeypatch.setattr(temporalio_workflow, "execute_activity", execute)
    monkeypatch.setattr(temporalio_workflow, "sleep", _no_sleep)
    monkeypatch.setattr(temporalio_workflow, "patched", lambda patch_id: True)

    result = _run(ProvisionManagedServiceWorkflow().run(_input()))

    assert result.ok is False
    assert recorded == ["finalize"]
    assert failed == [result.message]
    assert events == [[1, "finalize", result.message]]
    assert "finalize_managed_service_provision failed" in result.message
