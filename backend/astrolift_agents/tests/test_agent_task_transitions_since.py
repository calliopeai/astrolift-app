"""Resolver tests for ``agentTaskTransitionsSince`` — the /fleet/map live
feed (#1091).

``agentTaskTransitionsSince(orgId, since, limit)`` returns the caller-org's
AgentTasks whose ``updated_at`` advanced past ``since``, oldest change
first, capped. The fleet map polls it with a moving high-water cursor and
merges each batch, so the map accumulates the live fleet and pulses an edge
per transition.

Covered cases:
* caller-org scoping — a task in another org is never returned (a
  tenancy regression control: the foreign task WOULD appear without the
  ``organization_id`` clause)
* the ``since`` cursor — only ``updated_at > since`` rows come back
* ascending ``updated_at`` order regardless of insertion order
* ``limit`` caps the page (oldest first)
* the extended ``AstroliftAgentTask`` node-layer fields — dispatcher
  (+ its cluster), pod_name, namespace, and the queued/provisioning/
  updated stamps the map's layers read
* deny-by-default: ``agent.read`` is required (denial raises)
* the explicit-arg gate: a foreign ``orgId`` raises rather than crossing
  tenants
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from graphql import GraphQLError

from astrolift_agents.models import AgentTask, DispatcherInstance
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _tenant(org):
    return _tenant_ctx(TenantContext(organization_id=org.id))


def _provider_plugin(slug: str) -> ProviderPlugin:
    # bulk_create bypasses BaseCoreModel.save() (which would try to bump the
    # model's integer ``version`` — ProviderPlugin shadows it with a semver
    # *string*, so save() raises). Mirrors the workaround in the sibling
    # agent-task-logs / observability tests.
    plugin = ProviderPlugin(
        name="K8s",
        slug=slug,
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return ProviderPlugin.objects.get(slug=slug)


def _cluster(org, *, slug: str) -> TenantCluster:
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
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _dispatcher(org, cluster, *, slug: str, region: str = "us-west-2") -> DispatcherInstance:
    return DispatcherInstance.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        endpoint="https://dispatch.invalid",
        cloud=DispatcherInstance.Cloud.AWS,
        region=region,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
        tenant_cluster=cluster,
    )


def _task(
    org,
    *,
    dispatcher=None,
    status: str = AgentTask.Status.QUEUED,
    pod_name: str = "",
    namespace: str = "",
    updated_at=None,
) -> AgentTask:
    task = AgentTask.objects.create(
        organization=org,
        dispatcher=dispatcher,
        status=status,
        pod_name=pod_name,
        namespace=namespace,
    )
    if updated_at is not None:
        # ``updated_at`` is stamped by save(); a raw UPDATE bypasses that so
        # the cursor is deterministic in the test.
        AgentTask.objects.filter(pk=task.pk).update(updated_at=updated_at)
        task.refresh_from_db()
    return task


@pytest.mark.django_db(transaction=True)
def test_transitions_scoped_to_caller_org(permission_resolver):
    """The caller sees only their own org's tasks — a task in another org
    is excluded. Meaningful because the foreign task WOULD be returned by a
    bare ``AgentTask.objects.filter(updated_at__gt=…)`` without the
    ``organization_id`` clause (a cross-tenant leak)."""
    mine = Organization.objects.create(name="Mine", slug="mine-org")
    theirs = Organization.objects.create(name="Theirs", slug="their-org")
    my_task = _task(mine, status=AgentTask.Status.RUNNING)
    _task(theirs, status=AgentTask.Status.RUNNING)  # foreign — must not leak

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(mine):
        rows = AgentsQuery().agent_task_transitions_since(info=_info(), org_id=str(mine.guid))

    assert [r.id for r in rows] == [str(my_task.guid)]


@pytest.mark.django_db(transaction=True)
def test_transitions_respects_since_cursor(permission_resolver):
    """``since`` returns only tasks whose ``updated_at`` is strictly after
    the cursor; omitting it returns the whole (capped) set."""
    org = Organization.objects.create(name="Cursor", slug="cursor-org")
    now = timezone.now()
    t1 = _task(org, updated_at=now - timedelta(minutes=3))
    t2 = _task(org, updated_at=now - timedelta(minutes=2))
    t3 = _task(org, updated_at=now - timedelta(minutes=1))

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        all_rows = AgentsQuery().agent_task_transitions_since(info=_info(), org_id=str(org.guid))
        after_t1 = AgentsQuery().agent_task_transitions_since(
            info=_info(), org_id=str(org.guid), since=t1.updated_at
        )
        after_t3 = AgentsQuery().agent_task_transitions_since(
            info=_info(), org_id=str(org.guid), since=t3.updated_at
        )

    assert [r.id for r in all_rows] == [str(t1.guid), str(t2.guid), str(t3.guid)]
    # Strictly greater than t1 — t1 itself is excluded.
    assert [r.id for r in after_t1] == [str(t2.guid), str(t3.guid)]
    # Nothing has advanced past the newest row.
    assert after_t3 == []


@pytest.mark.django_db(transaction=True)
def test_transitions_ordered_ascending_by_updated_at(permission_resolver):
    """Rows come back oldest-change-first regardless of insertion order, so
    the client can fold them and advance its cursor to the last row."""
    org = Organization.objects.create(name="Order", slug="order-org")
    now = timezone.now()
    # Insert out of order; updated_at is what orders the result.
    newest = _task(org, updated_at=now - timedelta(seconds=10))
    oldest = _task(org, updated_at=now - timedelta(seconds=90))
    middle = _task(org, updated_at=now - timedelta(seconds=50))

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        rows = AgentsQuery().agent_task_transitions_since(info=_info(), org_id=str(org.guid))

    assert [r.id for r in rows] == [str(oldest.guid), str(middle.guid), str(newest.guid)]


@pytest.mark.django_db(transaction=True)
def test_transitions_limit_caps_result(permission_resolver):
    """``limit`` bounds the page and keeps the oldest changes (so the
    cursor drains forward across polls); a limit above the cap is clamped
    but still returns every available row when the fleet is small."""
    org = Organization.objects.create(name="Cap", slug="cap-org")
    now = timezone.now()
    t1 = _task(org, updated_at=now - timedelta(minutes=3))
    t2 = _task(org, updated_at=now - timedelta(minutes=2))
    _task(org, updated_at=now - timedelta(minutes=1))  # newest — trimmed by limit=2

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        limited = AgentsQuery().agent_task_transitions_since(info=_info(), org_id=str(org.guid), limit=2)
        huge = AgentsQuery().agent_task_transitions_since(info=_info(), org_id=str(org.guid), limit=10_000)

    assert [r.id for r in limited] == [str(t1.guid), str(t2.guid)]
    # An over-cap limit clamps without error and returns all three.
    assert len(huge) == 3


@pytest.mark.django_db(transaction=True)
def test_transitions_exposes_node_layer_fields(permission_resolver):
    """The extended ``AstroliftAgentTask`` projects the fleet-map node layer:
    dispatcher (+ its co-located cluster), pod_name, namespace, and the
    queued/provisioning/updated stamps."""
    org = Organization.objects.create(name="Fields", slug="fields-org")
    cluster = _cluster(org, slug="fields-cluster")
    dispatcher = _dispatcher(org, cluster, slug="fields-dispatcher")
    queued_at = timezone.now() - timedelta(minutes=5)
    provisioning_at = timezone.now() - timedelta(minutes=4)
    task = _task(
        org,
        dispatcher=dispatcher,
        status=AgentTask.Status.RUNNING,
        pod_name="agent-task-abc123",
        namespace="astrolift-agents-fields-org",
    )
    AgentTask.objects.filter(pk=task.pk).update(queued_at=queued_at, provisioning_at=provisioning_at)

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        rows = AgentsQuery().agent_task_transitions_since(info=_info(), org_id=str(org.guid))

    assert len(rows) == 1
    row = rows[0]
    assert row.pod_name == "agent-task-abc123"
    assert row.namespace == "astrolift-agents-fields-org"
    assert row.queued_at == queued_at
    assert row.provisioning_at == provisioning_at
    assert row.updated_at is not None
    # The dispatcher reference carries the routing identity + the cluster the
    # map correlates with heartbeat liveness — never the endpoint/key.
    assert row.dispatcher is not None
    assert row.dispatcher.id == str(dispatcher.guid)
    assert row.dispatcher.name == "fields-dispatcher"
    assert row.dispatcher.slug == "fields-dispatcher"
    assert row.dispatcher.cloud == "aws"
    assert row.dispatcher.region == "us-west-2"
    assert row.dispatcher.cluster_id == str(cluster.guid)
    assert row.dispatcher.cluster_name == "fields-cluster"


@pytest.mark.django_db(transaction=True)
def test_transitions_dispatcher_null_before_routing(permission_resolver):
    """A draft/queued task has no dispatcher yet, so ``dispatcher`` is
    null (the Controller sets it at PROVISIONING)."""
    org = Organization.objects.create(name="Unrouted", slug="unrouted-org")
    _task(org, status=AgentTask.Status.QUEUED)

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(org):
        rows = AgentsQuery().agent_task_transitions_since(info=_info(), org_id=str(org.guid))

    assert len(rows) == 1
    assert rows[0].dispatcher is None


@pytest.mark.django_db(transaction=True)
def test_transitions_requires_agent_read():
    """Deny-by-default: without ``agent.read`` the resolver-entry gate
    raises before any query (queries surface permission failures by
    raising)."""
    org = Organization.objects.create(name="Deny", slug="deny-org")
    _task(org)

    with _tenant(org), pytest.raises(PermissionDenied):
        AgentsQuery().agent_task_transitions_since(info=_info(), org_id=str(org.guid))


@pytest.mark.django_db(transaction=True)
def test_transitions_foreign_org_id_raises(permission_resolver):
    """Passing another org's ``orgId`` while scoped to your own tenant is
    rejected by the explicit-arg gate — a caller can't read another org's
    feed by supplying its guid."""
    mine = Organization.objects.create(name="Mine2", slug="mine2-org")
    theirs = Organization.objects.create(name="Theirs2", slug="theirs2-org")
    _task(theirs, status=AgentTask.Status.RUNNING)

    permission_resolver.grant(Permission.AGENT_READ)
    with _tenant(mine), pytest.raises(GraphQLError):
        AgentsQuery().agent_task_transitions_since(info=_info(), org_id=str(theirs.guid))
