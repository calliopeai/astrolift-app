"""Tests for the ``updateAgentRunSpec`` run-spec write mutation (spec 33).

``update_agent_run_spec`` is the backend write surface the run-spec editors
target: the Once/Schedule/Service editor (PR-11) and the Loop/Trigger +
scaling editor (PR-12) both POST here. It writes the run-spec fields PR-1
added to ``Workload(kind=agent)`` (migration 0028) — no new migration. These
tests pin, against a real database:

  * a partial update writes ONLY the supplied fields (omitted fields are
    left unchanged);
  * a full Task-family spec (schedule + cron + loop cap + paused) persists;
  * a full Service-family spec (scale target + scale crons) persists, and a
    family switch in the same call as the scaling fields is coherent;
  * every cron field is shape-validated and stored normalized;
  * each incoherent combo is rejected with a structured field error (bad
    cron, schedule-without-cron, scaling-field-on-task-family, loop-cap-on-
    service-family, non-positive scale target, negative loop cap);
  * a non-agent workload is rejected (VALIDATION);
  * a cross-org agent slug is not resolvable (NOT_FOUND, no leak, no write);
  * the write is denied without ``app.update`` (PERMISSION_DENIED, no write);
  * the success envelope carries the updated run-spec in ``data``.

Resolvers are exercised by direct invocation (the agents-test convention)
with a controllable permission resolver + tenant context bound, mirroring
``test_run_agent_mutation.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_agents.schema.mutations import AgentRunSpecInput, AgentsMutation
from astrolift_agents.schema.types import AgentRunFamily, AgentRunMode, AgentRunSpecType
from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp, Workload
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org():
    return Organization.objects.create(name="RunSpec Org", slug="runspec-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="RunSpec Other", slug="runspec-other")


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _agent_workload(
    org,
    *,
    app_slug="triage-app",
    workload_slug="triage",
    kind=Workload.Kind.AGENT,
    **run_spec,
):
    """Create a ``kind=agent`` Workload under a fresh app in ``org``.

    ``run_spec`` overrides any run-spec field default (e.g.
    ``run_family=Workload.RunFamily.SERVICE``) so each test starts from a
    known spec. An explicit ``slug`` is always set (the model's NamedBase
    would otherwise slugify a blank name to a colliding value)."""
    team = Team.objects.create(organization=org, name=f"T-{app_slug}", slug=f"t-{app_slug}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name=app_slug.replace("-", " ").title(),
        slug=app_slug,
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name=workload_slug.replace("-", " ").title(),
        slug=workload_slug,
        kind=kind,
        **run_spec,
    )


def _call(org, with_tenant_org, slug, **input_kwargs):
    with with_tenant_org(org):
        return AgentsMutation().update_agent_run_spec(
            SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            agent_slug=slug,
            input=AgentRunSpecInput(**input_kwargs),
        )


# ---------------------------------------------------------------------------
# Partial update: only supplied fields are written
# ---------------------------------------------------------------------------


def test_partial_update_writes_only_supplied_fields(permission_resolver, org, with_tenant_org):
    """Supplying only ``run_paused`` flips it and leaves every other run-spec
    field at its stored value — the editor can save one toggle."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(
        org,
        run_family=Workload.RunFamily.TASK,
        run_mode=Workload.RunMode.LOOP,
        run_cron_expression="*/5 * * * *",
        run_max_parallel=3,
        run_paused=False,
    )
    prev_version = workload.version

    result = _call(org, with_tenant_org, workload.slug, run_paused=True)

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.run_paused is True
    # Untouched fields unchanged.
    assert workload.run_family == Workload.RunFamily.TASK.value
    assert workload.run_mode == Workload.RunMode.LOOP.value
    assert workload.run_cron_expression == "*/5 * * * *"
    assert workload.run_max_parallel == 3
    # The save bumped the optimistic-concurrency version.
    assert workload.version == prev_version + 1


def test_empty_input_is_a_noop_that_returns_current_spec(permission_resolver, org, with_tenant_org):
    """An input with no fields set writes nothing (no version bump) and
    returns the current run-spec — a benign save of an unchanged editor."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_max_parallel=2)
    prev_version = workload.version

    result = _call(org, with_tenant_org, workload.slug)

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.version == prev_version
    assert result.data.run_max_parallel == 2


# ---------------------------------------------------------------------------
# Full Task-family + Service-family writes (the two editors)
# ---------------------------------------------------------------------------


def test_full_task_schedule_spec(permission_resolver, org, with_tenant_org):
    """The PR-11 Schedule editor: family=task, mode=schedule, a cron, paused."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org)

    result = _call(
        org,
        with_tenant_org,
        workload.slug,
        run_family=AgentRunFamily.TASK,
        run_mode=AgentRunMode.SCHEDULE,
        run_cron_expression="0 9 * * 1-5",
        run_paused=True,
    )

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.run_family == "task"
    assert workload.run_mode == "schedule"
    assert workload.run_cron_expression == "0 9 * * 1-5"
    assert workload.run_paused is True


def test_full_task_loop_spec_with_cap(permission_resolver, org, with_tenant_org):
    """The PR-12 Loop editor: family=task, mode=loop, an explicit cap."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org)

    result = _call(
        org,
        with_tenant_org,
        workload.slug,
        run_family=AgentRunFamily.TASK,
        run_mode=AgentRunMode.LOOP,
        run_max_parallel=5,
    )

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.run_mode == "loop"
    assert workload.run_max_parallel == 5


def test_loop_cap_zero_is_a_soft_pause(permission_resolver, org, with_tenant_org):
    """An explicit ``run_max_parallel=0`` is honoured (Loop soft-pause), and
    is distinct from ``None`` (leave unchanged)."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_mode=Workload.RunMode.LOOP, run_max_parallel=4)

    result = _call(org, with_tenant_org, workload.slug, run_max_parallel=0)

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.run_max_parallel == 0


def test_full_service_scaling_spec_with_family_switch(permission_resolver, org, with_tenant_org):
    """The PR-11 Service editor: switch family→service AND set the scaling
    fields in the same call. Coherence is judged against the EFFECTIVE family
    (service), so this is accepted even though the row started task-family."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.TASK)

    result = _call(
        org,
        with_tenant_org,
        workload.slug,
        run_family=AgentRunFamily.SERVICE,
        scheduled_scale_to=4,
        scale_up_cron="0 8 * * 1-5",
        scale_down_cron="0 18 * * 1-5",
    )

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.run_family == "service"
    assert workload.scheduled_scale_to == 4
    assert workload.scale_up_cron == "0 8 * * 1-5"
    assert workload.scale_down_cron == "0 18 * * 1-5"


def test_replicas_on_service_writes(permission_resolver, org, with_tenant_org):
    """The PR-11 Service editor sets the baseline replica count through the
    same mutation; it writes to ``workload.replicas`` (the field the
    Deployment renderer reads) on a service-family agent."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.SERVICE, replicas=1)

    result = _call(org, with_tenant_org, workload.slug, replicas=3)

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.replicas == 3


def test_replicas_writes_on_same_call_family_switch(permission_resolver, org, with_tenant_org):
    """Switching family→service AND setting replicas in one call is coherent
    (replicas is judged against the EFFECTIVE family, service)."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.TASK, replicas=1)

    result = _call(
        org,
        with_tenant_org,
        workload.slug,
        run_family=AgentRunFamily.SERVICE,
        replicas=5,
    )

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.run_family == "service"
    assert workload.replicas == 5


def test_cron_is_stored_normalized(permission_resolver, org, with_tenant_org):
    """A cron with irregular whitespace is stored single-space-normalized
    (the validator's canonical form), so the scheduler reads a clean value."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org)

    result = _call(
        org,
        with_tenant_org,
        workload.slug,
        run_mode=AgentRunMode.SCHEDULE,
        run_cron_expression="0   9 *  * 1-5",
    )

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.run_cron_expression == "0 9 * * 1-5"


def test_empty_cron_string_clears_the_field(permission_resolver, org, with_tenant_org):
    """An explicit empty-string cron clears a previously-set cron (the editor
    blanking the field), distinct from None which leaves it unchanged. Mode is
    moved off schedule in the same call so the schedule-needs-cron rule holds."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_mode=Workload.RunMode.SCHEDULE, run_cron_expression="0 9 * * *")

    result = _call(
        org,
        with_tenant_org,
        workload.slug,
        run_mode=AgentRunMode.ONCE,
        run_cron_expression="",
    )

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.run_cron_expression == ""
    assert workload.run_mode == "once"


# ---------------------------------------------------------------------------
# Clear scheduled scaling (#952): the dedicated "remove the schedule" seam
# ---------------------------------------------------------------------------


def test_clear_scheduled_scaling_clears_the_triple(permission_resolver, org, with_tenant_org):
    """``clearScheduledScaling=True`` turns the whole scheduled-scaling triple
    off — both crons to "" and the scale target to NULL — which a normal update
    can't express (None on ``scheduled_scale_to`` means leave-unchanged).
    ``replicas`` (the Service baseline) is deliberately left untouched."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(
        org,
        run_family=Workload.RunFamily.SERVICE,
        replicas=2,
        scheduled_scale_to=5,
        scale_up_cron="0 8 * * 1-5",
        scale_down_cron="0 18 * * 1-5",
    )

    result = _call(org, with_tenant_org, workload.slug, clear_scheduled_scaling=True)

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.scheduled_scale_to is None
    assert workload.scale_up_cron == ""
    assert workload.scale_down_cron == ""
    # Baseline replica count is NOT part of the schedule — left as-is.
    assert workload.replicas == 2
    # The success envelope reads back the cleared state in one round-trip.
    assert result.data.scheduled_scale_to is None
    assert result.data.scale_up_cron == ""
    assert result.data.scale_down_cron == ""


def test_normal_update_does_not_clear_scaling(permission_resolver, org, with_tenant_org):
    """Without the flag, a partial update that touches an unrelated field
    leaves the stored scaling triple intact (a normal save can't accidentally
    clear the schedule)."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(
        org,
        run_family=Workload.RunFamily.SERVICE,
        scheduled_scale_to=5,
        scale_up_cron="0 8 * * 1-5",
        scale_down_cron="0 18 * * 1-5",
    )

    result = _call(org, with_tenant_org, workload.slug, replicas=3)

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.replicas == 3
    # Scaling triple untouched.
    assert workload.scheduled_scale_to == 5
    assert workload.scale_up_cron == "0 8 * * 1-5"
    assert workload.scale_down_cron == "0 18 * * 1-5"


def test_clear_scheduled_scaling_with_scale_value_rejected(permission_resolver, org, with_tenant_org):
    """Setting the clear flag AND a scaling value in one call is contradictory
    (set vs clear) — rejected with a field error, nothing written."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(
        org,
        run_family=Workload.RunFamily.SERVICE,
        scheduled_scale_to=5,
    )

    result = _call(
        org,
        with_tenant_org,
        workload.slug,
        clear_scheduled_scaling=True,
        scheduled_scale_to=8,
    )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "clearScheduledScaling"
    workload.refresh_from_db()
    assert workload.scheduled_scale_to == 5


# ---------------------------------------------------------------------------
# Validation: each rule rejects with a field error (no 500, no write)
# ---------------------------------------------------------------------------


def test_bad_cron_rejected(permission_resolver, org, with_tenant_org):
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org)

    result = _call(
        org,
        with_tenant_org,
        workload.slug,
        run_mode=AgentRunMode.SCHEDULE,
        run_cron_expression="99 99 * * *",  # minute/hour out of range
    )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "runCronExpression"
    workload.refresh_from_db()
    # Nothing written.
    assert workload.run_mode == Workload.RunMode.ONCE.value
    assert workload.run_cron_expression == ""


def test_schedule_without_cron_rejected(permission_resolver, org, with_tenant_org):
    """run_mode=schedule with no cron (neither stored nor supplied) rejects."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org)  # no cron stored

    result = _call(org, with_tenant_org, workload.slug, run_mode=AgentRunMode.SCHEDULE)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "runCronExpression"
    workload.refresh_from_db()
    assert workload.run_mode == Workload.RunMode.ONCE.value


def test_schedule_uses_already_stored_cron(permission_resolver, org, with_tenant_org):
    """Switching to schedule mode is allowed when a cron is ALREADY stored,
    even though this input supplies none (effective-cron check)."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_cron_expression="0 0 * * *")

    result = _call(org, with_tenant_org, workload.slug, run_mode=AgentRunMode.SCHEDULE)

    assert result.ok is True
    workload.refresh_from_db()
    assert workload.run_mode == "schedule"
    assert workload.run_cron_expression == "0 0 * * *"


def test_scaling_field_on_task_family_rejected(permission_resolver, org, with_tenant_org):
    """Setting scaleUpCron on a task-family agent is rejected (scaling is
    service-only) — the editor's contract, not store-but-ignore."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.TASK)

    result = _call(org, with_tenant_org, workload.slug, scale_up_cron="0 8 * * *")

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "scaleUpCron"
    workload.refresh_from_db()
    assert workload.scale_up_cron == ""


def test_scheduled_scale_to_on_task_family_rejected(permission_resolver, org, with_tenant_org):
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.TASK)

    result = _call(org, with_tenant_org, workload.slug, scheduled_scale_to=3)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "scheduledScaleTo"


def test_replicas_on_task_family_rejected(permission_resolver, org, with_tenant_org):
    """Setting replicas on a task-family agent is rejected (replicas is the
    Service Deployment's baseline count; a Task agent runs as a Job) — the
    editor's contract, not store-but-ignore. No write to the row."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.TASK, replicas=1)

    result = _call(org, with_tenant_org, workload.slug, replicas=3)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "replicas"
    workload.refresh_from_db()
    assert workload.replicas == 1


def test_non_positive_replicas_rejected(permission_resolver, org, with_tenant_org):
    """replicas must be >= 1 (a Service has at least one desired pod);
    ``replicas=0`` is rejected with a field error, no write."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.SERVICE, replicas=2)

    result = _call(org, with_tenant_org, workload.slug, replicas=0)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "replicas"
    workload.refresh_from_db()
    assert workload.replicas == 2


def test_loop_cap_on_service_family_rejected(permission_resolver, org, with_tenant_org):
    """run_max_parallel (the Loop cap) on a service-family agent is rejected —
    a Service scales via replicas, not a per-task cap."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.SERVICE)

    result = _call(org, with_tenant_org, workload.slug, run_max_parallel=3)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "runMaxParallel"
    workload.refresh_from_db()
    assert workload.run_max_parallel is None


def test_non_positive_scale_target_rejected(permission_resolver, org, with_tenant_org):
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.SERVICE)

    result = _call(org, with_tenant_org, workload.slug, scheduled_scale_to=0)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "scheduledScaleTo"


def test_negative_loop_cap_rejected(permission_resolver, org, with_tenant_org):
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.TASK)

    result = _call(org, with_tenant_org, workload.slug, run_max_parallel=-1)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "runMaxParallel"


def test_bad_scale_up_cron_rejected_on_service(permission_resolver, org, with_tenant_org):
    """A malformed scale cron rejects even on the (valid) service family."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org, run_family=Workload.RunFamily.SERVICE)

    result = _call(org, with_tenant_org, workload.slug, scale_down_cron="not a cron")

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "scaleDownCron"


# ---------------------------------------------------------------------------
# Workload-kind + tenancy + permission scoping
# ---------------------------------------------------------------------------


def test_non_agent_workload_rejected(permission_resolver, org, with_tenant_org):
    """A workload in the caller's org but not kind=agent is rejected."""
    permission_resolver.grant(Permission.APP_UPDATE)
    deployment = _agent_workload(org, app_slug="web-app", workload_slug="web", kind=Workload.Kind.DEPLOYMENT)

    result = _call(org, with_tenant_org, deployment.slug, run_paused=True)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert result.errors[0].field == "agentSlug"
    deployment.refresh_from_db()
    assert deployment.run_paused is False


def test_cross_org_agent_not_found(permission_resolver, org, other_org, with_tenant_org):
    """An agent slug that exists only in another org is not resolvable for
    the caller's tenant — NOT_FOUND (no existence leak), and no write to the
    foreign row."""
    permission_resolver.grant(Permission.APP_UPDATE)
    foreign = _agent_workload(other_org, app_slug="foreign-app", workload_slug="foreign-agent")

    result = _call(org, with_tenant_org, foreign.slug, run_paused=True)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    foreign.refresh_from_db()
    assert foreign.run_paused is False


def test_denied_without_app_update(org, with_tenant_org):
    """No app.update grant -> PERMISSION_DENIED, no write.

    No ``permission_resolver`` fixture here, so the process-default deny-all
    resolver is in force."""
    workload = _agent_workload(org, run_paused=False)

    result = _call(org, with_tenant_org, workload.slug, run_paused=True)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    workload.refresh_from_db()
    assert workload.run_paused is False


# ---------------------------------------------------------------------------
# Return envelope shape
# ---------------------------------------------------------------------------


def test_success_envelope_carries_updated_run_spec(permission_resolver, org, with_tenant_org):
    """The success envelope's ``data`` is the full, updated run-spec so the
    editor reads back the persisted state in one round-trip."""
    permission_resolver.grant(Permission.APP_UPDATE)
    workload = _agent_workload(org)

    result = _call(
        org,
        with_tenant_org,
        workload.slug,
        run_family=AgentRunFamily.SERVICE,
        replicas=2,
        scheduled_scale_to=6,
        scale_up_cron="0 8 * * *",
    )

    assert result.ok is True
    assert isinstance(result.data, AgentRunSpecType)
    assert str(result.data.id) == str(workload.guid)
    assert result.data.slug == workload.slug
    assert result.data.kind == "agent"
    assert result.data.run_family == "service"
    assert result.data.replicas == 2
    assert result.data.scheduled_scale_to == 6
    assert result.data.scale_up_cron == "0 8 * * *"
    # Untouched fields surface their stored defaults.
    assert result.data.run_mode == "once"
    assert result.data.run_paused is False
