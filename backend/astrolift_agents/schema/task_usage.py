"""Exact-task usage provenance, never a reconciliation or billing estimate (#2458)."""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import replace
from decimal import Decimal

import strawberry
from django.db.models import Q
from django.utils import timezone
from strawberry.types import Info

from astrolift_agents.scopes import agent_task_scope
from astrolift_agents.visibility import agent_tasks
from astrolift_graphql import GUID
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    session_may_act_in,
    set_current_api_token,
    with_active_org_member,
)
from astrolift_identity.operation_context import agent_task_operation
from core.decorators import tenant_scoped
from core.permissions import Permission, PermissionDenied, require_permission
from core.tenancy import get_current_tenant


@strawberry.enum
class TaskUsageCompleteness(enum.Enum):
    UNKNOWN = "unknown"
    PARTIAL = "partial"
    REPORTED = "reported"
    OBSERVED = "observed"


@strawberry.enum
class TaskUsageUnit(enum.Enum):
    TOKENS = "tokens"
    MILLISECONDS = "milliseconds"


@strawberry.enum
class TaskUsageSource(enum.Enum):
    COMPLETION_REPORT = "completion_report"
    PLATFORM_TIMESTAMPS = "platform_timestamps"
    K8S_METRICS = "k8s_metrics"
    ECS_METADATA = "ecs_metadata"
    WALL_TIME_ESTIMATE = "wall_time_estimate"
    AGENT_REPORTED = "agent_reported"


@strawberry.enum
class TaskUsageReason(enum.Enum):
    NOT_RECORDED = "not_recorded"
    INVALID_VALUE = "invalid_value"
    NONTERMINAL_REPORT = "nonterminal_report"
    INCONSISTENT_TIMESTAMPS = "inconsistent_timestamps"
    UNVERIFIED_RUN_BINDING = "unverified_run_binding"
    PREMATURE_METER = "premature_meter"
    NO_TASK_BILLING_SOURCE = "no_task_billing_source"


@strawberry.enum
class TaskCostReconciliation(enum.Enum):
    NOT_RECONCILED = "not_reconciled"
    NO_SOURCE = "no_source"


@strawberry.type(name="AgentTaskUsageQuantity")
class TaskUsageQuantity:
    value: Decimal | None
    unit: TaskUsageUnit
    source: TaskUsageSource | None
    source_recorded_at: dt.datetime | None
    completeness: TaskUsageCompleteness
    reason: TaskUsageReason | None


@strawberry.type(name="AgentTaskUsageCost")
class TaskUsageCost:
    amount: Decimal | None
    currency: str | None
    source: TaskUsageSource | None
    source_recorded_at: dt.datetime | None
    completeness: TaskUsageCompleteness
    reconciliation: TaskCostReconciliation
    reason: TaskUsageReason | None


@strawberry.type(name="AgentTaskUsageSnapshot")
class TaskUsageSnapshot:
    """One exact task. Completion and linked-run meter values are never summed.

    Retry dispatch creates a new task GUID, excluded from this snapshot.
    A linked run's retry count does not identify separate billable attempts.
    """

    task_id: GUID
    task_version: int
    task_status: str
    as_of: dt.datetime
    aggregation_scope: str
    linked_run_id: GUID | None
    linked_run_retry_count: int | None
    input_tokens: TaskUsageQuantity
    output_tokens: TaskUsageQuantity
    duration: TaskUsageQuantity
    reported_cost: TaskUsageCost
    actual_cost: TaskUsageCost
    model: str | None
    model_source: TaskUsageSource | None
    model_source_recorded_at: dt.datetime | None


def _unknown(unit, reason=TaskUsageReason.NOT_RECORDED):
    return TaskUsageQuantity(
        value=None,
        unit=unit,
        source=None,
        source_recorded_at=None,
        completeness=TaskUsageCompleteness.UNKNOWN,
        reason=reason,
    )


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1


def _cost(value):
    # Match dispatcher normalization; Decimal preserves transport precision only,
    # not precision already lost when a runner's JSON number became a float.
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    decimal = Decimal(str(value))
    return decimal if decimal.is_finite() and 0 <= decimal <= Decimal("1e15") else None


def _snapshot(task):
    now = timezone.now()
    terminal = task.status in task.TERMINAL_STATUSES and task.ended_at is not None
    report_ready = bool(
        terminal
        and task.created_at <= task.ended_at <= now
        and (task.started_at is None or task.created_at <= task.started_at <= task.ended_at)
    )
    coherent = (
        terminal
        and task.started_at is not None
        and task.created_at <= task.started_at <= task.ended_at <= now
    )
    run = task.agent_run
    valid_run = bool(
        run
        and run.deleted_at is None
        and task.agent_definition_id
        and run.workload_id == task.agent_definition_id
        and run.workload.deleted_at is None
        and run.workload.registered_app.deleted_at is None
        and run.workload.registered_app.organization_id == task.organization_id
    )
    meter = getattr(run, "metering_record", None) if valid_run else None
    meter_source = None
    if meter:
        try:
            meter_source = TaskUsageSource(meter.metering_source)
        except ValueError:
            pass
    meter_final = bool(
        coherent
        and meter
        and meter_source
        and task.ended_at <= meter.created_at <= now
        and run.started_at is not None
        and run.ended_at is not None
        and task.created_at <= run.started_at <= run.ended_at <= meter.created_at
        and task.started_at <= run.ended_at
        and run.status in {"succeeded", "failed", "cancelled"}
    )
    usage = task.completion_usage if isinstance(task.completion_usage, dict) else {}

    def tokens(key, meter_key):
        raw = usage.get(key)
        if raw is not None and report_ready and _integer(raw):
            return TaskUsageQuantity(
                value=Decimal(raw),
                unit=TaskUsageUnit.TOKENS,
                source=TaskUsageSource.COMPLETION_REPORT,
                source_recorded_at=None,
                completeness=TaskUsageCompleteness.REPORTED,
                reason=None,
            )
        if meter and meter_source and _integer(getattr(meter, meter_key)):
            return TaskUsageQuantity(
                value=Decimal(getattr(meter, meter_key)),
                unit=TaskUsageUnit.TOKENS,
                source=meter_source,
                source_recorded_at=meter.created_at,
                completeness=TaskUsageCompleteness.REPORTED if meter_final else TaskUsageCompleteness.PARTIAL,
                reason=None if meter_final else TaskUsageReason.PREMATURE_METER,
            )
        reason = TaskUsageReason.NOT_RECORDED
        if raw is not None:
            reason = (
                TaskUsageReason.INVALID_VALUE
                if report_ready
                else TaskUsageReason.INCONSISTENT_TIMESTAMPS
                if terminal
                else TaskUsageReason.NONTERMINAL_REPORT
            )
        elif run and not valid_run:
            reason = TaskUsageReason.UNVERIFIED_RUN_BINDING
        elif meter and (getattr(meter, meter_key) is not None or meter_source is None):
            reason = TaskUsageReason.INVALID_VALUE
        return _unknown(TaskUsageUnit.TOKENS, reason)

    duration = _unknown(TaskUsageUnit.MILLISECONDS)
    if coherent:
        delta = task.ended_at - task.started_at
        millis = Decimal(delta.days * 86400 + delta.seconds) * 1000 + Decimal(delta.microseconds) / 1000
        duration = TaskUsageQuantity(
            value=millis,
            unit=TaskUsageUnit.MILLISECONDS,
            source=TaskUsageSource.PLATFORM_TIMESTAMPS,
            source_recorded_at=task.ended_at,
            completeness=TaskUsageCompleteness.OBSERVED,
            reason=None,
        )
    elif task.started_at is not None or task.ended_at is not None:
        duration.reason = (
            TaskUsageReason.INCONSISTENT_TIMESTAMPS if terminal else TaskUsageReason.NOT_RECORDED
        )
    raw_cost = usage.get("total_cost_usd")
    amount = _cost(raw_cost) if report_ready else None
    reported_cost = TaskUsageCost(
        amount=amount,
        currency="USD" if amount is not None else None,
        source=TaskUsageSource.COMPLETION_REPORT if amount is not None else None,
        source_recorded_at=None,
        completeness=TaskUsageCompleteness.REPORTED if amount is not None else TaskUsageCompleteness.UNKNOWN,
        reconciliation=TaskCostReconciliation.NOT_RECONCILED,
        reason=None
        if amount is not None
        else (
            TaskUsageReason.NOT_RECORDED
            if raw_cost is None
            else TaskUsageReason.INVALID_VALUE
            if report_ready
            else TaskUsageReason.INCONSISTENT_TIMESTAMPS
            if terminal
            else TaskUsageReason.NONTERMINAL_REPORT
        ),
    )
    model = usage.get("model") if report_ready else None
    if not isinstance(model, str) or not model or len(model) > 256 or any(ord(c) < 32 for c in model):
        model = None
    return TaskUsageSnapshot(
        task_id=GUID(str(task.guid)),
        task_version=task.version,
        task_status=task.status,
        as_of=now,
        aggregation_scope="EXACT_TASK_PREFER_COMPLETION_THEN_LINKED_RUN_NO_SUM",
        linked_run_id=GUID(str(run.guid)) if valid_run else None,
        linked_run_retry_count=run.retry_count if valid_run and 0 <= run.retry_count <= 2**31 - 1 else None,
        input_tokens=tokens("input_tokens", "token_input"),
        output_tokens=tokens("output_tokens", "token_output"),
        duration=duration,
        reported_cost=reported_cost,
        actual_cost=TaskUsageCost(
            amount=None,
            currency=None,
            source=None,
            source_recorded_at=None,
            completeness=TaskUsageCompleteness.UNKNOWN,
            reconciliation=TaskCostReconciliation.NO_SOURCE,
            reason=TaskUsageReason.NO_TASK_BILLING_SOURCE,
        ),
        model=model,
        model_source=TaskUsageSource.COMPLETION_REPORT if model else None,
        model_source_recorded_at=None,
    )


@require_permission(
    Permission.APP_READ,
    Permission.AGENT_READ,
    scope=agent_task_scope("id"),
    operation=agent_task_operation("id"),
)
@tenant_scoped()
def _authorized_usage(info: Info, id: str) -> TaskUsageSnapshot | None:
    tenant = get_current_tenant()
    task = (
        agent_tasks(tenant.organization_id, Permission.AGENT_READ)
        .filter(
            guid=id,
            organization_id=tenant.organization_id,
            pk__in=agent_tasks(tenant.organization_id, Permission.APP_READ).values("pk"),
        )
        .select_related("agent_run__metering_record", "agent_run__workload__registered_app")
        .first()
    )
    return _snapshot(task) if task else None


def _fresh_authorized_usage(info: Info, id: str) -> TaskUsageSnapshot | None:
    from astrolift_identity import abac

    tenant = get_current_tenant()
    attributes = replace(abac.attributes_for(tenant.actor_user_id), cache={})
    with abac.request_attributes(attributes):
        return _authorized_usage(info, id)


def read_task_usage(info: Info, id: str) -> TaskUsageSnapshot | None:
    """Recheck revocation and current bearer ceilings, including stale contexts."""
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import ApiToken

    tenant = get_current_tenant()
    viewer = (
        get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first() if tenant else None
    )
    if viewer is None:
        raise PermissionDenied(Permission.AGENT_READ, None, "viewer is no longer active")
    token = get_current_api_token()
    if token is None:
        if not session_may_act_in(viewer, tenant.organization_id):
            raise PermissionDenied(Permission.AGENT_READ, None, "organization membership is no longer active")
        return _fresh_authorized_usage(info, id)
    fresh = (
        with_active_org_member(
            ApiToken.objects.filter(
                pk=token.pk,
                token_hash=token.token_hash,
                is_revoked=False,
                user_id=tenant.actor_user_id if tenant else None,
                user__is_active=True,
                organization_id=tenant.organization_id if tenant else None,
                organization__deleted_at__isnull=True,
            ),
            user="user",
            organization="organization",
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .first()
    )
    if fresh is None:
        raise PermissionDenied(Permission.AGENT_READ, None, "credential is no longer valid")
    marker = set_current_api_token(fresh)
    try:
        return _fresh_authorized_usage(info, id)
    finally:
        reset_current_api_token(marker)
