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
* ``agent.read`` is required (denial raises) — re-gated from
  ``app.read_logs`` in the entity-module re-shell (spec 36 §0.4); the
  agents module has no separate read-logs perm
* a driver error degrades to ``[]`` (no 500)
* ``tail`` caps the returned line count
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from types import SimpleNamespace

import pytest
from _sdk.cluster import ClusterAuth, PodInfo, PodLogLine

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


def _task(org, dispatcher, *, pod_name: str = "", namespace: str = "") -> AgentTask:
    return AgentTask.objects.create(
        organization=org,
        dispatcher=dispatcher,
        status=AgentTask.Status.RUNNING,
        pod_name=pod_name,
        namespace=namespace,
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

    def list_pods(self, *, auth, namespace, app_slug, task_id=""):
        self.calls.append({"namespace": namespace, "app_slug": app_slug, "task_id": task_id})
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
def test_agent_task_logs_happy_path(permission_resolver):
    """Pod discovered + lines streamed back as plain message strings,
    in the per-org agent namespace, with follow disabled."""
    org = Organization.objects.create(name="Logs Org", slug="logs-org")
    cluster = _cluster(org, slug="logs-cluster")
    dispatcher = _dispatcher(org, cluster, slug="logs-dispatcher")
    task = _task(org, dispatcher)

    permission_resolver.grant(Permission.AGENT_READ)
    pod_backend = _FixedPodBackend([_fake_pod("agent-task-abc-xyz12")])
    log_backend = _ScriptedLogBackend(["line one", "line two", "line three"])
    set_pod_backend_for_tests(pod_backend)
    set_log_backend_for_tests(log_backend)
    try:
        with _tenant(org):
            lines = AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid), tail=200)
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == ["line one", "line two", "line three"]
    # Discovery + stream both targeted the per-org agent namespace (the
    # fallback when the task never stamped a namespace).
    assert pod_backend.calls[0]["namespace"] == "astrolift-agents-logs-org"
    assert pod_backend.calls[0]["app_slug"] == str(task.guid)
    # Discovery selects the agent pod by its task-id label (#891).
    assert pod_backend.calls[0]["task_id"] == str(task.guid)
    assert log_backend.calls[0]["pod_name"] == "agent-task-abc-xyz12"
    # One-shot read — never a live follow.
    assert log_backend.calls[0]["follow"] is False


@pytest.mark.django_db(transaction=True)
def test_agent_task_logs_tail_caps_line_count(permission_resolver):
    org = Organization.objects.create(name="Tail Org", slug="tail-org")
    cluster = _cluster(org, slug="tail-cluster")
    dispatcher = _dispatcher(org, cluster, slug="tail-dispatcher")
    task = _task(org, dispatcher)

    permission_resolver.grant(Permission.AGENT_READ)
    set_pod_backend_for_tests(_FixedPodBackend([_fake_pod("pod-1")]))
    set_log_backend_for_tests(_ScriptedLogBackend([f"line {i}" for i in range(50)]))
    try:
        with _tenant(org):
            lines = AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid), tail=5)
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == ["line 0", "line 1", "line 2", "line 3", "line 4"]


@pytest.mark.django_db(transaction=True)
def test_agent_task_logs_falls_back_to_pod_name_hint(permission_resolver):
    """When discovery returns no pods (the live backend can't select the
    agent's task-id label), the resolver falls back to the recorded Job
    name on ``AgentTask.pod_name``."""
    org = Organization.objects.create(name="Hint Org", slug="hint-org")
    cluster = _cluster(org, slug="hint-cluster")
    dispatcher = _dispatcher(org, cluster, slug="hint-dispatcher")
    task = _task(org, dispatcher, pod_name="agent-task-deadbeef")

    permission_resolver.grant(Permission.AGENT_READ)
    set_pod_backend_for_tests(_FixedPodBackend([]))  # discovery finds nothing
    log_backend = _ScriptedLogBackend(["hi"])
    set_log_backend_for_tests(log_backend)
    try:
        with _tenant(org):
            lines = AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == ["hi"]
    assert log_backend.calls[0]["pod_name"] == "agent-task-deadbeef"


@pytest.mark.django_db(transaction=True)
def test_agent_task_logs_foreign_org_returns_empty(permission_resolver):
    """A task owned by another org resolves to [] (not an error) so the
    surface doesn't leak task existence across tenants."""
    org = Organization.objects.create(name="Mine", slug="mine-org")
    other = Organization.objects.create(name="Theirs", slug="their-org")
    cluster = _cluster(other, slug="their-cluster")
    dispatcher = _dispatcher(other, cluster, slug="their-dispatcher")
    foreign_task = _task(other, dispatcher)

    permission_resolver.grant(Permission.AGENT_READ)
    # Backends would yield lines if ever reached — they must not be.
    set_pod_backend_for_tests(_FixedPodBackend([_fake_pod("pod-1")]))
    set_log_backend_for_tests(_ScriptedLogBackend(["should not appear"]))
    try:
        with _tenant(org):
            lines = AgentsQuery().agent_task_logs(info=_info(), id=str(foreign_task.guid))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == []


@pytest.mark.django_db(transaction=True)
def test_agent_task_logs_unknown_id_returns_empty(permission_resolver):
    import uuid

    org = Organization.objects.create(name="Empty", slug="empty-org")

    permission_resolver.grant(Permission.AGENT_READ)
    set_pod_backend_for_tests(_FixedPodBackend([_fake_pod("pod-1")]))
    set_log_backend_for_tests(_ScriptedLogBackend(["x"]))
    try:
        with _tenant(org):
            lines = AgentsQuery().agent_task_logs(info=_info(), id=str(uuid.uuid4()))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == []


@pytest.mark.django_db(transaction=True)
def test_agent_task_logs_no_cluster_returns_empty(permission_resolver):
    """A dispatcher without a tenant_cluster bound has no pod to read —
    the resolver returns [] rather than 500ing."""
    org = Organization.objects.create(name="NoClust", slug="noclust-org")
    dispatcher = DispatcherInstance.objects.create(
        organization=org,
        name="d",
        slug="noclust-dispatcher",
        endpoint="https://dispatch.invalid",
        cloud=DispatcherInstance.Cloud.AWS,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
        tenant_cluster=None,
    )
    task = _task(org, dispatcher)

    permission_resolver.grant(Permission.AGENT_READ)
    try:
        with _tenant(org):
            lines = AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))
    finally:
        pass

    assert lines == []


@pytest.mark.django_db(transaction=True)
def test_agent_task_logs_inactive_cluster_returns_empty(permission_resolver):
    org = Organization.objects.create(name="Inact", slug="inact-org")
    cluster = _cluster(org, slug="inact-cluster", is_active=False)
    dispatcher = _dispatcher(org, cluster, slug="inact-dispatcher")
    task = _task(org, dispatcher)

    permission_resolver.grant(Permission.AGENT_READ)
    set_pod_backend_for_tests(_FixedPodBackend([_fake_pod("pod-1")]))
    set_log_backend_for_tests(_ScriptedLogBackend(["x"]))
    try:
        with _tenant(org):
            lines = AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == []


@pytest.mark.django_db(transaction=True)
def test_agent_task_logs_requires_read_logs_permission():
    """Without the read gate (``agent.read`` after the §0.4 re-gate) the
    resolver-entry gate raises before any task lookup (queries surface
    permission failures via raise)."""
    org = Organization.objects.create(name="Deny", slug="deny-org")
    cluster = _cluster(org, slug="deny-cluster")
    dispatcher = _dispatcher(org, cluster, slug="deny-dispatcher")
    task = _task(org, dispatcher)

    # NOTE: no permission granted.
    with _tenant(org), pytest.raises(PermissionDenied):
        AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))


@pytest.mark.django_db(transaction=True)
def test_agent_task_logs_driver_error_degrades_to_empty(permission_resolver):
    """A ClusterObservabilityError during discovery degrades to [] —
    the operator read never 500s on a cluster outage."""
    org = Organization.objects.create(name="Err", slug="err-org")
    cluster = _cluster(org, slug="err-cluster")
    dispatcher = _dispatcher(org, cluster, slug="err-dispatcher")
    task = _task(org, dispatcher, pod_name="")

    class _ExplodingPodBackend:
        def list_pods(self, *, auth, namespace, app_slug, task_id=""):
            raise ClusterObservabilityError("cluster unreachable")

    permission_resolver.grant(Permission.AGENT_READ)
    set_pod_backend_for_tests(_ExplodingPodBackend())
    set_log_backend_for_tests(_ScriptedLogBackend(["x"]))
    try:
        with _tenant(org):
            lines = AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    # No pod found (discovery raised) and no pod_name hint -> [].
    assert lines == []


@pytest.mark.django_db(transaction=True)
def test_agent_task_logs_reads_stamped_namespace(permission_resolver):
    """The resolver reads ``AgentTask.namespace`` (the namespace the spawn
    actually used) rather than recomputing it from the org slug (#891).

    Stage-dispatched agents can land in a namespace that doesn't match the
    recomputed ``astrolift-agents-<org>`` guess; reading the stamped value
    is what makes agentTaskLogs return lines on a real cluster.

    ``agent_task_logs`` is a sync resolver (it bridges the async log fetch
    with ``async_to_sync`` itself), so it is exercised here from a sync test
    rather than awaited.
    """
    org = Organization.objects.create(name="Stamp", slug="stamp-org")
    cluster = _cluster(org, slug="stamp-cluster")
    dispatcher = _dispatcher(org, cluster, slug="stamp-dispatcher")
    # Stamp a namespace that is NOT the recomputed astrolift-agents-stamp-org.
    task = _task(org, dispatcher, namespace="agents-elsewhere")

    permission_resolver.grant(Permission.AGENT_READ)
    pod_backend = _FixedPodBackend([_fake_pod("agent-task-stamped")])
    log_backend = _ScriptedLogBackend(["only line"])
    set_pod_backend_for_tests(pod_backend)
    set_log_backend_for_tests(log_backend)
    try:
        with _tenant(org):
            lines = AgentsQuery().agent_task_logs(info=_info(), id=str(task.guid))
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == ["only line"]
    # Both discovery and the stream read the stamped namespace, not the
    # recomputed astrolift-agents-stamp-org guess.
    assert pod_backend.calls[0]["namespace"] == "agents-elsewhere"
    assert log_backend.calls[0]["namespace"] == "agents-elsewhere"
    # And the pod is still discovered by the task-id selector.
    assert pod_backend.calls[0]["task_id"] == str(task.guid)


def test_pod_label_selector_prefers_task_id():
    """The pod discovery selector keys on the task-id label when a task_id
    is given and falls back to the app-slug label otherwise (#891)."""
    from k8s_native.observability import _pod_label_selector

    assert _pod_label_selector(app_slug="web", task_id="guid-1") == "astrolift.dev/task-id=guid-1"
    assert _pod_label_selector(app_slug="web", task_id="") == "astrolift.dev/app=web"


@pytest.mark.asyncio
async def test_fetch_task_pod_logs_discovers_by_task_id():
    """``fetch_task_pod_logs`` discovers the agent pod via the task-id
    selector and reads it in the namespace it is handed — not a recomputed
    one (#891). Exercises the discovery wiring end-to-end through the
    driver-override layer without a live cluster or kubernetes SDK."""
    from core.cluster_observability import fetch_task_pod_logs

    cluster = SimpleNamespace(
        slug="c",
        auth_method="kubeconfig",
        auth_config={"kubeconfig": "x"},
        endpoint="https://k8s.invalid",
        ca_cert="",
        default_namespace_prefix="",
        is_active=True,
    )
    pod_backend = _FixedPodBackend([_fake_pod("agent-task-xyz")])
    log_backend = _ScriptedLogBackend(["a", "b"])
    set_pod_backend_for_tests(pod_backend)
    set_log_backend_for_tests(log_backend)
    try:
        lines = await fetch_task_pod_logs(
            cluster=cluster,
            namespace="agents-elsewhere",
            task_guid="task-guid-9",
            pod_name_hint="",
            tail=200,
        )
    finally:
        reset_pod_backend_for_tests()
        reset_log_backend_for_tests()

    assert lines == ["a", "b"]
    # Discovery selected by the task-id label, in the supplied namespace.
    assert pod_backend.calls[0]["task_id"] == "task-guid-9"
    assert pod_backend.calls[0]["namespace"] == "agents-elsewhere"
    # The stream read the discovered pod in that same namespace.
    assert log_backend.calls[0]["namespace"] == "agents-elsewhere"
    assert log_backend.calls[0]["pod_name"] == "agent-task-xyz"


def test_live_pod_backend_uses_task_id_selector(monkeypatch):
    """``LivePodBackend.list_pods`` selects agent pods by the task-id label
    (``astrolift.dev/task-id=<guid>``) when ``task_id`` is set, and falls
    back to the app-slug label otherwise (#891).

    Recording fake: the kubernetes ``CoreV1Api`` is patched so the call
    records the ``label_selector`` it would have sent to the apiserver.
    """
    pytest.importorskip("kubernetes")
    import k8s_native.observability as obs

    monkeypatch.setattr(obs, "build_api_client", lambda auth: object())

    recorded: dict[str, str] = {}

    class _FakeResp:
        items: list = []

    class _RecordingCoreV1:
        def __init__(self, _api_client):
            pass

        def list_namespaced_pod(self, *, namespace, label_selector, timeout_seconds):
            recorded["namespace"] = namespace
            recorded["label_selector"] = label_selector
            return _FakeResp()

    from kubernetes import client as k8s_client

    monkeypatch.setattr(k8s_client, "CoreV1Api", _RecordingCoreV1)

    auth = ClusterAuth(slug="c", auth_method="kubeconfig", auth_config={"kubeconfig": "x"})

    # task_id set -> task-id label selector, app_slug ignored.
    pods = obs.LivePodBackend().list_pods(
        auth=auth,
        namespace="astrolift-agents-acme",
        app_slug="ignored-app",
        task_id="task-guid-123",
    )
    assert pods == []
    assert recorded["namespace"] == "astrolift-agents-acme"
    assert recorded["label_selector"] == "astrolift.dev/task-id=task-guid-123"

    # No task_id -> falls back to the app-slug label (app-log surface).
    obs.LivePodBackend().list_pods(auth=auth, namespace="acme-web", app_slug="web")
    assert recorded["label_selector"] == "astrolift.dev/app=web"
