"""Resolver tests for the operator-facing ``agentTaskLogs`` query.

``agentTaskLogs(id, tail)`` resolves an AgentTask (tenant-scoped like
``agentTask``), walks ``dispatcher.tenant_cluster`` + the per-org agent
namespace, discovers the task's pod, and reads up to ``tail`` of its most
recent log message lines through the same driver plumbing the app-log
surface uses.

The pod/log backends are faked through the existing
``core.cluster_observability`` test hooks
(``set_pod_backend_for_tests`` / ``set_log_backend_for_tests``) so the
resolver runs without a live cluster.

Covered cases:
* happy path — pod discovered, lines collected (capped at ``tail``)
* foreign-org task id resolves to ``[]`` (no cross-tenant leak)
* unknown task id resolves to ``[]``
* dispatcher with no ``tenant_cluster`` resolves to ``[]``
* an inactive cluster resolves to ``[]``
* ``app.read_logs`` is required (denial raises)
* a driver error degrades to ``[]`` (no 500)
* ``tail`` caps the returned line count
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from types import SimpleNamespace

import pytest
from _sdk.cluster import PodInfo, PodLogLine

from astrolift_agents.models import AgentTask, DispatcherInstance
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from core.cluster_observability import (
    ClusterObservabilityError,
    reset_log_backend_for_tests,
    reset_pod_backend_for_tests,
    set_log_backend_for_tests,
    set_pod_backend_for_tests,
)
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _tenant(org, actor_user_id=None):
    return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))


def _provider_plugin(slug: str) -> ProviderPlugin:
    # bulk_create bypasses BaseCoreModel.save(), which would try to
    # increment the model's ``version`` field — but ProviderPlugin shadows
    # that with a semver *string* field, so save() raises TypeError. This
    # mirrors the workaround in astrolift_observability/tests/test_log_queries.
    plugin = ProviderPlugin(
        name="K8s",
        slug=slug,
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return ProviderPlugin.objects.get(slug=slug)


def _cluster(org, *, slug: str, is_active: bool = True) -> TenantCluster:
    plugin = _provider_plugin(f"k8s-{slug}")
    return TenantCluster.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        is_active=is_active,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _dispatcher(org, cluster, *, slug: str) -> DispatcherInstance:
    return DispatcherInstance.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        endpoint="https://dispatch.invalid",
        cloud=DispatcherInstance.Cloud.AWS,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
        tenant_cluster=cluster,
    )


def _task(org, dispatcher, *, pod_name: str = "") -> AgentTask:
    return AgentTask.objects.create(
        organization=org,
        dispatcher=dispatcher,
        status=AgentTask.Status.RUNNING,
        pod_name=pod_name,
    )


def _fake_pod(name: str) -> PodInfo:
    return PodInfo(
        name=name,
        workload="agent",
        status="Running",
        phase="Running",
        ready=True,
        restarts=0,
        age=dt.datetime.now(UTC),
        node="node-a",
        container_statuses=[],
    )


class _FixedPodBackend:
    def __init__(self, pods):
        self._pods = list(pods)
        self.calls: list[dict] = []

    def list_pods(self, *, auth, namespace, app_slug):
        self.calls.append({"namespace": namespace, "app_slug": app_slug})
        return list(self._pods)


class _ScriptedLogBackend:
    def __init__(self, lines: list[str]):
        self._lines = lines
        self.calls: list[dict] = []

    async def stream(self, *, auth, namespace, pod_name, container, tail_lines, follow):
        self.calls.append(
            {
                "namespace": namespace,
                "pod_name": pod_name,
                "tail_lines": tail_lines,
                "follow": follow,
            }
        )
        for msg in self._lines:
            yield PodLogLine(
                pod_name=pod_name,
                container=container or "agent",
                timestamp=dt.datetime.now(UTC),
                message=msg,
                stream="stdout",
            )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_task_logs_happy_path(permission_resolver):
    """Pod discovered + lines streamed back as plain message strings,
    in the per-org agent namespace, with follow disabled."""
    import asyncio

    org = await asyncio.to_thread(Organization.objects.create, name="Logs Org", slug="logs-org")
    cluster = await asyncio.to_thread(_cluster, org, slug="logs-cluster")
    dispatcher = await asyncio.to_thread(_dispatcher, org, cluster, slug="logs-dispatcher")
    task = await asyncio.to_thread(_task, org, dispatcher)

    permission_resolver.grant(Permission.APP_READ_LOGS)
    pod_backend = _FixedPodBackend([_fake_pod("agent-task-abc-xyz12")])
    log_backend = _ScriptedLogBackend(["line one", "line two", "line three"])
    set_pod_backend_for_tests(pod_backend)
    set_log_backend_for_tests(log_backend)
    try:
        with _tenant(org):
            lines = await AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid), tail=200)
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == ["line one", "line two", "line three"]
    # Discovery + stream both targeted the per-org agent namespace.
    assert pod_backend.calls[0]["namespace"] == "astrolift-agents-logs-org"
    assert pod_backend.calls[0]["app_slug"] == str(task.guid)
    assert log_backend.calls[0]["pod_name"] == "agent-task-abc-xyz12"
    # One-shot read — never a live follow.
    assert log_backend.calls[0]["follow"] is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_task_logs_tail_caps_line_count(permission_resolver):
    import asyncio

    org = await asyncio.to_thread(Organization.objects.create, name="Tail Org", slug="tail-org")
    cluster = await asyncio.to_thread(_cluster, org, slug="tail-cluster")
    dispatcher = await asyncio.to_thread(_dispatcher, org, cluster, slug="tail-dispatcher")
    task = await asyncio.to_thread(_task, org, dispatcher)

    permission_resolver.grant(Permission.APP_READ_LOGS)
    set_pod_backend_for_tests(_FixedPodBackend([_fake_pod("pod-1")]))
    set_log_backend_for_tests(_ScriptedLogBackend([f"line {i}" for i in range(50)]))
    try:
        with _tenant(org):
            lines = await AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid), tail=5)
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == ["line 0", "line 1", "line 2", "line 3", "line 4"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_task_logs_falls_back_to_pod_name_hint(permission_resolver):
    """When discovery returns no pods (the live backend can't select the
    agent's task-id label), the resolver falls back to the recorded Job
    name on ``AgentTask.pod_name``."""
    import asyncio

    org = await asyncio.to_thread(Organization.objects.create, name="Hint Org", slug="hint-org")
    cluster = await asyncio.to_thread(_cluster, org, slug="hint-cluster")
    dispatcher = await asyncio.to_thread(_dispatcher, org, cluster, slug="hint-dispatcher")
    task = await asyncio.to_thread(_task, org, dispatcher, pod_name="agent-task-deadbeef")

    permission_resolver.grant(Permission.APP_READ_LOGS)
    set_pod_backend_for_tests(_FixedPodBackend([]))  # discovery finds nothing
    log_backend = _ScriptedLogBackend(["hi"])
    set_log_backend_for_tests(log_backend)
    try:
        with _tenant(org):
            lines = await AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == ["hi"]
    assert log_backend.calls[0]["pod_name"] == "agent-task-deadbeef"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_task_logs_foreign_org_returns_empty(permission_resolver):
    """A task owned by another org resolves to [] (not an error) so the
    surface doesn't leak task existence across tenants."""
    import asyncio

    org = await asyncio.to_thread(Organization.objects.create, name="Mine", slug="mine-org")
    other = await asyncio.to_thread(Organization.objects.create, name="Theirs", slug="their-org")
    cluster = await asyncio.to_thread(_cluster, other, slug="their-cluster")
    dispatcher = await asyncio.to_thread(_dispatcher, other, cluster, slug="their-dispatcher")
    foreign_task = await asyncio.to_thread(_task, other, dispatcher)

    permission_resolver.grant(Permission.APP_READ_LOGS)
    # Backends would yield lines if ever reached — they must not be.
    set_pod_backend_for_tests(_FixedPodBackend([_fake_pod("pod-1")]))
    set_log_backend_for_tests(_ScriptedLogBackend(["should not appear"]))
    try:
        with _tenant(org):
            lines = await AgentsQuery().agent_task_logs(info=_info(), id=str(foreign_task.guid))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_task_logs_unknown_id_returns_empty(permission_resolver):
    import asyncio
    import uuid

    org = await asyncio.to_thread(Organization.objects.create, name="Empty", slug="empty-org")

    permission_resolver.grant(Permission.APP_READ_LOGS)
    set_pod_backend_for_tests(_FixedPodBackend([_fake_pod("pod-1")]))
    set_log_backend_for_tests(_ScriptedLogBackend(["x"]))
    try:
        with _tenant(org):
            lines = await AgentsQuery().agent_task_logs(info=_info(), id=str(uuid.uuid4()))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_task_logs_no_cluster_returns_empty(permission_resolver):
    """A dispatcher without a tenant_cluster bound has no pod to read —
    the resolver returns [] rather than 500ing."""
    import asyncio

    org = await asyncio.to_thread(Organization.objects.create, name="NoClust", slug="noclust-org")
    dispatcher = await asyncio.to_thread(
        DispatcherInstance.objects.create,
        organization=org,
        name="d",
        slug="noclust-dispatcher",
        endpoint="https://dispatch.invalid",
        cloud=DispatcherInstance.Cloud.AWS,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
        tenant_cluster=None,
    )
    task = await asyncio.to_thread(_task, org, dispatcher)

    permission_resolver.grant(Permission.APP_READ_LOGS)
    try:
        with _tenant(org):
            lines = await AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))
    finally:
        pass

    assert lines == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_task_logs_inactive_cluster_returns_empty(permission_resolver):
    import asyncio

    org = await asyncio.to_thread(Organization.objects.create, name="Inact", slug="inact-org")
    cluster = await asyncio.to_thread(_cluster, org, slug="inact-cluster", is_active=False)
    dispatcher = await asyncio.to_thread(_dispatcher, org, cluster, slug="inact-dispatcher")
    task = await asyncio.to_thread(_task, org, dispatcher)

    permission_resolver.grant(Permission.APP_READ_LOGS)
    set_pod_backend_for_tests(_FixedPodBackend([_fake_pod("pod-1")]))
    set_log_backend_for_tests(_ScriptedLogBackend(["x"]))
    try:
        with _tenant(org):
            lines = await AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_task_logs_requires_read_logs_permission():
    """Without ``app.read_logs`` the resolver-entry gate raises before any
    task lookup (queries surface permission failures via raise)."""
    import asyncio

    org = await asyncio.to_thread(Organization.objects.create, name="Deny", slug="deny-org")
    cluster = await asyncio.to_thread(_cluster, org, slug="deny-cluster")
    dispatcher = await asyncio.to_thread(_dispatcher, org, cluster, slug="deny-dispatcher")
    task = await asyncio.to_thread(_task, org, dispatcher)

    # NOTE: no permission granted.
    with _tenant(org), pytest.raises(PermissionDenied):
        await AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_task_logs_driver_error_degrades_to_empty(permission_resolver):
    """A ClusterObservabilityError during discovery degrades to [] —
    the operator read never 500s on a cluster outage."""
    import asyncio

    org = await asyncio.to_thread(Organization.objects.create, name="Err", slug="err-org")
    cluster = await asyncio.to_thread(_cluster, org, slug="err-cluster")
    dispatcher = await asyncio.to_thread(_dispatcher, org, cluster, slug="err-dispatcher")
    task = await asyncio.to_thread(_task, org, dispatcher, pod_name="")

    class _ExplodingPodBackend:
        def list_pods(self, *, auth, namespace, app_slug):
            raise ClusterObservabilityError("cluster unreachable")

    permission_resolver.grant(Permission.APP_READ_LOGS)
    set_pod_backend_for_tests(_ExplodingPodBackend())
    set_log_backend_for_tests(_ScriptedLogBackend(["x"]))
    try:
        with _tenant(org):
            lines = await AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    # No pod found (discovery raised) and no pod_name hint -> [].
    assert lines == []
