"""Tests for the Cluster Status tab's backend surface (#362).

The umbrella ticket #362 covers four slices on the Status tab. Slices
1, 2, and the recent-workflows card landed previously and are
exercised through ``test_cluster_management`` /
``test_bring_cluster_into_management``. This module pins the
remaining slice — the per-Deployment workload-health rollup powering
the Status tab's Workload health card.

Boundaries covered:

* permission denied without ``cluster.register`` (the read-side
  permission shared with the existing health resolver)
* missing / soft-deleted cluster -> empty list (matches the
  cluster_health resolver's contract — UI renders the empty-state
  card rather than a router-level 404)
* driver-resolution failure -> empty list (no creds, plugin missing)
* happy path -> per-Deployment rows surfaced 1:1, sorted by
  ``(namespace, name)`` so the UI doesn't have to re-sort
* the ``_kube_health.workload_health_from_client`` helper computes
  the 24h restart window correctly (in-window contributes, out-of-
  window doesn't, missing timestamps fall back to running counter)
* the helper reads the ``NewReplicaSetAvailable`` Progressing
  condition for ``last_image_deployed_at`` and returns an empty
  string when the condition is absent
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture
def org():
    return Organization.objects.create(
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:6]}",
    )


@pytest.fixture
def plugin():
    # Skip BaseCoreModel.save (numeric version-increment vs CharField)
    # the same way the count-query test suite does.
    [row] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="local",
                slug=f"local-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
    )
    return row


@pytest.fixture
def cluster(org, plugin):
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


# ─── Resolver-level tests ────────────────────────────────────────────


def test_workload_health_denied_without_cluster_register(
    cluster,
    org,
    permission_resolver,
):
    """The resolver shares the read-side permission with
    ``astroliftClusterHealth``. Callers without it must be denied
    before the dispatch fires — defense in depth even though the
    Status tab itself only mounts behind the same gate."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cluster_workload_health(
                _info(),
                cluster_id=GUID(str(cluster.guid)),
            )


def test_workload_health_missing_cluster_returns_empty(
    org,
    permission_resolver,
):
    """A non-existent cluster guid must return an empty list, not
    raise. Mirrors the cluster_health resolver's contract — the UI
    renders the empty-state card and the operator follows the
    cluster-list breadcrumb back."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_workload_health(
            _info(),
            cluster_id=GUID(str(uuid.uuid4())),
        )
    assert result == []


def test_workload_health_soft_deleted_cluster_returns_empty(
    cluster,
    org,
    permission_resolver,
):
    """Soft-deleted clusters must be excluded — the tab is a
    health-of-this-cluster view, and a deleted row by definition
    has nothing to report."""
    cluster.soft_delete()
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_workload_health(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result == []


def test_workload_health_driver_failure_returns_empty(
    cluster,
    org,
    permission_resolver,
    monkeypatch,
):
    """``ClusterManagementError`` from the dispatch path (no plugin
    loaded, no creds, unreachable apiserver) must surface as an
    empty list to the UI rather than tearing the tab down.

    Patched at the resolver-imported reference rather than the
    module-of-truth so the resolver's own ``from core...`` import
    sees the stub.
    """
    from core import cluster_management

    def _boom(**_kwargs):
        raise cluster_management.ClusterManagementError(
            "no plugin loaded for this cluster",
        )

    monkeypatch.setattr(
        cluster_management,
        "cluster_workload_health_dispatch",
        _boom,
    )
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_workload_health(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result == []


def test_workload_health_happy_path_surfaces_rows(
    cluster,
    org,
    permission_resolver,
    monkeypatch,
):
    """Dispatch returns plain dicts; the resolver maps them 1:1 onto
    ``ClusterWorkloadHealthType`` rows. The resolver MUST NOT re-
    aggregate — the dispatch layer is the source of truth for the
    shape and ordering. We assert the resolver carries the rows
    through verbatim, in the order dispatch returned them."""
    from core import cluster_management

    def _ok(**_kwargs):
        return [
            {
                "namespace": "astrolift-system",
                "name": "api",
                "desired_replicas": 3,
                "ready_replicas": 2,
                "restart_count_24h": 5,
                "last_image_deployed_at": "2026-05-15T12:00:00+00:00",
            },
            {
                "namespace": "astrolift-system",
                "name": "worker",
                "desired_replicas": 1,
                "ready_replicas": 1,
                "restart_count_24h": 0,
                "last_image_deployed_at": "",
            },
        ]

    monkeypatch.setattr(
        cluster_management,
        "cluster_workload_health_dispatch",
        _ok,
    )
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        rows = ClustersQuery().astrolift_cluster_workload_health(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert [(r.namespace, r.workload_name) for r in rows] == [
        ("astrolift-system", "api"),
        ("astrolift-system", "worker"),
    ]
    api = rows[0]
    assert api.desired_replicas == 3
    assert api.ready_replicas == 2
    assert api.restart_count_24h == 5
    assert api.last_image_deployed_at == "2026-05-15T12:00:00+00:00"
    worker = rows[1]
    assert worker.desired_replicas == 1
    assert worker.ready_replicas == 1
    assert worker.restart_count_24h == 0
    assert worker.last_image_deployed_at == ""


# ─── _kube_health helper tests ───────────────────────────────────────


def _ns(**kw):
    """Helper: build a SimpleNamespace shaped like a kubernetes-client
    object so the helper sees ``.metadata.name`` / ``.spec.replicas``
    / etc. exactly the way the live client surfaces it."""
    return SimpleNamespace(**kw)


def _deployment(
    *,
    name: str,
    replicas: int,
    ready_replicas: int,
    match_labels: dict[str, str] | None = None,
    progressing_at: dt.datetime | None = None,
):
    conditions: list = []
    if progressing_at is not None:
        conditions.append(
            _ns(
                type="Progressing",
                reason="NewReplicaSetAvailable",
                last_transition_time=progressing_at,
            ),
        )
    selector = _ns(match_labels=match_labels) if match_labels is not None else None
    return _ns(
        metadata=_ns(name=name),
        spec=_ns(replicas=replicas, selector=selector),
        status=_ns(ready_replicas=ready_replicas, conditions=conditions),
    )


def _pod(*, container_statuses):
    return _ns(status=_ns(container_statuses=container_statuses))


def _container_status(
    *,
    restart_count: int,
    finished_at: dt.datetime | None,
):
    last_state = _ns(
        terminated=(_ns(finished_at=finished_at) if finished_at is not None else None),
    )
    return _ns(restart_count=restart_count, last_state=last_state)


class _FakeK8sClient:
    """Minimal kube-client stand-in for the helper.

    Mirrors ``kubernetes.client.CoreV1Api`` /
    ``kubernetes.client.AppsV1Api`` surface narrowly: per-namespace
    Deployment lookup and per-namespace Pod lookup filtered by a
    ``key=value,key=value`` label selector.
    """

    def __init__(self, *, deployments, pods_by_selector):
        self._deployments = deployments
        self._pods_by_selector = pods_by_selector

    def list_namespaced_deployment(self, *, namespace):
        return _ns(items=self._deployments.get(namespace, []))

    def list_namespaced_pod(self, *, namespace, label_selector=""):
        key = (namespace, label_selector)
        return _ns(items=self._pods_by_selector.get(key, []))


def test_helper_rolls_up_desired_and_ready_replicas():
    """One Deployment with 3 desired / 2 ready surfaces those values
    verbatim — no aggregation, no rounding."""
    from _sdk._kube_health import workload_health_from_client

    client = _FakeK8sClient(
        deployments={
            "astrolift-system": [
                _deployment(
                    name="api",
                    replicas=3,
                    ready_replicas=2,
                    match_labels={"app": "api"},
                ),
            ],
        },
        pods_by_selector={("astrolift-system", "app=api"): []},
    )
    rows = workload_health_from_client(
        client,
        namespaces=["astrolift-system"],
    )
    assert len(rows) == 1
    assert rows[0].name == "api"
    assert rows[0].desired_replicas == 3
    assert rows[0].ready_replicas == 2
    assert rows[0].restart_count_24h == 0


def test_helper_counts_restarts_in_24h_window():
    """A container restart whose last termination is 1h ago counts;
    one from 48h ago does not."""
    from _sdk._kube_health import workload_health_from_client

    now = dt.datetime(2026, 5, 15, 12, 0, tzinfo=dt.UTC)
    one_hour_ago = now - dt.timedelta(hours=1)
    two_days_ago = now - dt.timedelta(days=2)
    pod_in_window = _pod(
        container_statuses=[
            _container_status(restart_count=3, finished_at=one_hour_ago),
        ],
    )
    pod_out_of_window = _pod(
        container_statuses=[
            _container_status(restart_count=7, finished_at=two_days_ago),
        ],
    )
    client = _FakeK8sClient(
        deployments={
            "astrolift-system": [
                _deployment(
                    name="api",
                    replicas=2,
                    ready_replicas=2,
                    match_labels={"app": "api"},
                ),
            ],
        },
        pods_by_selector={
            ("astrolift-system", "app=api"): [pod_in_window, pod_out_of_window],
        },
    )
    rows = workload_health_from_client(
        client,
        namespaces=["astrolift-system"],
        now_iso=now.isoformat(),
    )
    assert rows[0].restart_count_24h == 3


def test_helper_falls_back_to_running_counter_when_no_timestamp():
    """A container with restart_count>0 but no termination timestamp
    (older kubelet, partial status) contributes the full counter
    rather than dropping the signal."""
    from _sdk._kube_health import workload_health_from_client

    now = dt.datetime(2026, 5, 15, 12, 0, tzinfo=dt.UTC)
    pod = _pod(
        container_statuses=[
            _container_status(restart_count=4, finished_at=None),
        ],
    )
    client = _FakeK8sClient(
        deployments={
            "astrolift-system": [
                _deployment(
                    name="api",
                    replicas=1,
                    ready_replicas=1,
                    match_labels={"app": "api"},
                ),
            ],
        },
        pods_by_selector={("astrolift-system", "app=api"): [pod]},
    )
    rows = workload_health_from_client(
        client,
        namespaces=["astrolift-system"],
        now_iso=now.isoformat(),
    )
    assert rows[0].restart_count_24h == 4


def test_helper_reads_last_image_deployed_from_progressing_condition():
    """``last_image_deployed_at`` should pull from the Progressing
    condition with reason NewReplicaSetAvailable — the canonical
    'rollout completed' signal."""
    from _sdk._kube_health import workload_health_from_client

    rollout = dt.datetime(2026, 5, 14, 9, 30, tzinfo=dt.UTC)
    client = _FakeK8sClient(
        deployments={
            "astrolift-system": [
                _deployment(
                    name="api",
                    replicas=1,
                    ready_replicas=1,
                    match_labels={"app": "api"},
                    progressing_at=rollout,
                ),
            ],
        },
        pods_by_selector={("astrolift-system", "app=api"): []},
    )
    rows = workload_health_from_client(
        client,
        namespaces=["astrolift-system"],
    )
    assert str(rollout) == rows[0].last_image_deployed_at


def test_helper_returns_empty_last_deployed_when_condition_missing():
    """A freshly-created Deployment has no Progressing/NewReplicaSet-
    Available condition yet; the helper returns an empty string
    rather than fabricating a timestamp."""
    from _sdk._kube_health import workload_health_from_client

    client = _FakeK8sClient(
        deployments={
            "astrolift-system": [
                _deployment(
                    name="api",
                    replicas=1,
                    ready_replicas=0,
                    match_labels={"app": "api"},
                    progressing_at=None,
                ),
            ],
        },
        pods_by_selector={("astrolift-system", "app=api"): []},
    )
    rows = workload_health_from_client(
        client,
        namespaces=["astrolift-system"],
    )
    assert rows[0].last_image_deployed_at == ""


def test_helper_skips_namespace_on_list_failure():
    """A per-namespace failure (RBAC, missing namespace) must not
    take the whole rollup down — the helper silently skips the bad
    namespace and continues with the reachable ones."""
    from _sdk._kube_health import workload_health_from_client

    class _PartialClient:
        def list_namespaced_deployment(self, *, namespace):
            if namespace == "broken":
                raise RuntimeError("forbidden")
            return _ns(
                items=[
                    _deployment(
                        name="api",
                        replicas=1,
                        ready_replicas=1,
                        match_labels={"app": "api"},
                    ),
                ],
            )

        def list_namespaced_pod(self, *, namespace, label_selector=""):
            return _ns(items=[])

    rows = workload_health_from_client(
        _PartialClient(),
        namespaces=["broken", "astrolift-system"],
    )
    assert len(rows) == 1
    assert rows[0].namespace == "astrolift-system"


def test_helper_sorts_by_namespace_then_name():
    """The UI doesn't re-sort — the helper must emit rows in
    (namespace, name) order so the operator sees deterministic
    ordering across polls."""
    from _sdk._kube_health import workload_health_from_client

    client = _FakeK8sClient(
        deployments={
            "tenant-b": [
                _deployment(
                    name="zeta",
                    replicas=1,
                    ready_replicas=1,
                    match_labels={"app": "zeta"},
                ),
            ],
            "tenant-a": [
                _deployment(
                    name="bravo",
                    replicas=1,
                    ready_replicas=1,
                    match_labels={"app": "bravo"},
                ),
                _deployment(
                    name="alpha",
                    replicas=1,
                    ready_replicas=1,
                    match_labels={"app": "alpha"},
                ),
            ],
        },
        pods_by_selector={},
    )
    rows = workload_health_from_client(
        client,
        namespaces=["tenant-b", "tenant-a"],
    )
    assert [(r.namespace, r.name) for r in rows] == [
        ("tenant-a", "alpha"),
        ("tenant-a", "bravo"),
        ("tenant-b", "zeta"),
    ]


# ─── Lifecycle audit resolver tests (#770) ───────────────────────────


def _mkaudit(
    *,
    operation: str,
    variables: dict,
    success: bool = True,
    errors: list[str] | None = None,
) -> None:
    """Insert one ``MutationAuditLog`` row directly. The audit
    extension normally writes these inside ``on_operation``; tests
    bypass the extension and write rows that simulate what the
    extension would produce for a given mutation."""
    from core.schema.audit import MutationAuditLog

    MutationAuditLog.objects.create(
        operation=operation,
        variables=variables,
        success=success,
        errors=errors or [],
    )


def test_lifecycle_audit_denied_without_cluster_register(
    cluster,
    org,
    permission_resolver,
):
    """The lifecycle timeline shares the read-side permission with the
    rest of the Status tab. Callers without it must be denied before
    the table scan begins."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cluster_lifecycle_audit(
                _info(),
                cluster_id=GUID(str(cluster.guid)),
            )


def test_lifecycle_audit_missing_cluster_returns_empty(
    org,
    permission_resolver,
):
    """A non-existent cluster guid must return an empty list rather
    than fall back to a global audit-log dump. Mirrors the cluster_
    health / workload_health contract."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_lifecycle_audit(
            _info(),
            cluster_id=GUID(str(uuid.uuid4())),
        )
    assert result == []


def test_lifecycle_audit_matches_dot_notation_operation(
    cluster,
    org,
    permission_resolver,
):
    """The canonical case: ``@mutation_audit`` populates
    ``_mutation_action_local.action`` and the extension records the
    dot-notation form (e.g. ``cluster.install_prereqs``). The resolver
    matches on the ``cluster.`` prefix."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _mkaudit(
        operation="cluster.install_prereqs",
        variables={"input": {"clusterId": str(cluster.guid), "selectedComponents": ["cert-manager"]}},
    )
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_lifecycle_audit(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert len(result) == 1
    assert result[0].operation == "cluster.install_prereqs"


def test_lifecycle_audit_matches_pascal_case_operation(
    cluster,
    org,
    permission_resolver,
):
    """Defensive fallback (#770): when the thread-local isn't set,
    ``MutationAuditExtension`` records ``request.operation_name`` —
    the PascalCase GraphQL operation name (e.g. ``InstallClusterPrereqs``).
    The resolver matches the known PascalCase names so a thread-local
    regression doesn't silently empty the Status tab card.

    This is the bug pattern the previous fix attempt addressed at the
    extension layer but couldn't fully resolve from the audit-write
    side; the resolver now tolerates both formats."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _mkaudit(
        operation="InstallClusterPrereqs",
        variables={"input": {"clusterId": str(cluster.guid), "selectedComponents": ["cert-manager"]}},
    )
    _mkaudit(
        operation="BringClusterIntoManagement",
        variables={"input": {"clusterId": str(cluster.guid)}},
    )
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_lifecycle_audit(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    ops = {r.operation for r in result}
    assert ops == {"InstallClusterPrereqs", "BringClusterIntoManagement"}


def test_lifecycle_audit_matches_register_by_slug_not_guid(
    cluster,
    org,
    permission_resolver,
):
    """RegisterTenantCluster's variables don't carry a cluster guid
    yet (the row is being created), only the slug + name. The resolver
    must match such rows via the slug check so the cluster's own
    registration appears in its lifecycle timeline."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _mkaudit(
        operation="cluster.register",
        variables={"input": {"slug": cluster.slug, "name": cluster.name}},
    )
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_lifecycle_audit(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert len(result) == 1
    assert result[0].operation == "cluster.register"


def test_lifecycle_audit_skips_other_clusters(
    cluster,
    org,
    permission_resolver,
):
    """A cluster's lifecycle timeline must contain only rows that
    reference its own guid or slug — rows for unrelated clusters must
    be excluded even when the operation prefix matches."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    other_guid = str(uuid.uuid4())
    _mkaudit(
        operation="cluster.install_prereqs",
        variables={"input": {"clusterId": other_guid}},
    )
    _mkaudit(
        operation="cluster.install_prereqs",
        variables={"input": {"clusterId": str(cluster.guid)}},
    )
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_lifecycle_audit(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert len(result) == 1


def test_lifecycle_audit_skips_unrelated_operations(
    cluster,
    org,
    permission_resolver,
):
    """Non-cluster mutations targeting other domains (app.deploy,
    secret.rotated, ...) must not bleed into the cluster timeline even
    when the cluster's guid happens to appear in their variables."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _mkaudit(
        operation="app.deploy",
        variables={"input": {"appId": "x", "clusterRef": str(cluster.guid)}},
    )
    _mkaudit(
        operation="cluster.register",
        variables={"input": {"slug": cluster.slug, "name": cluster.name}},
    )
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_lifecycle_audit(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert len(result) == 1
    assert result[0].operation == "cluster.register"


def test_lifecycle_audit_respects_limit_and_newest_first(
    cluster,
    org,
    permission_resolver,
):
    """Resolver returns newest-first (matches ``order_by('-timestamp')``)
    and stops after ``limit`` matches — operators see the most-recent
    activity on the Status tab even when the audit table is dense."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    for _ in range(5):
        _mkaudit(
            operation="cluster.install_prereqs",
            variables={"input": {"clusterId": str(cluster.guid)}},
        )
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_lifecycle_audit(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
            limit=3,
        )
    assert len(result) == 3
    timestamps = [r.timestamp for r in result]
    assert timestamps == sorted(timestamps, reverse=True)


# ─── Recent cluster workflows visibility-query tests (#770) ──────────


def test_recent_workflows_query_uses_exact_workflow_id_predicates():
    """The visibility query must use exact ``WorkflowId="<type>-<guid>"``
    predicates joined with OR — standard SQL visibility doesn't support
    ``LIKE "%...%"`` on WorkflowId, which silently returned no rows in
    prod even after Bring / Install / Decommission workflows ran.

    Tests assert the constructed query shape rather than going through
    a real Temporal server because the network layer is covered by the
    Temporal SDK's own integration tests; what matters here is that we
    don't reintroduce the LIKE-substring regression."""
    from astrolift_workflows.client import _CLUSTER_WORKFLOW_ID_PREFIXES

    # The prefixes must cover every workflow type that constructs its
    # workflow_id as "<prefix>-<cluster_guid>" in ClustersMutation.
    # Any new cluster workflow must add a prefix here.
    assert "BringClusterIntoManagement" in _CLUSTER_WORKFLOW_ID_PREFIXES
    assert "DecommissionClusterWorkflow" in _CLUSTER_WORKFLOW_ID_PREFIXES
    assert "InstallClusterPrereqsWorkflow" in _CLUSTER_WORKFLOW_ID_PREFIXES


def test_recent_workflows_disabled_returns_empty(settings):
    """With Temporal off (kill switch flipped), the resolver returns
    an empty list rather than raising — the Status tab card renders
    its 'no runs recorded yet' empty state."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    from astrolift_workflows.client import list_workflows_for_cluster

    assert list_workflows_for_cluster("any-guid", limit=10) == []


# ─── Prometheus metrics resolver tests (#771) ────────────────────────


def test_prometheus_metrics_denied_without_cluster_register(
    cluster,
    org,
    permission_resolver,
):
    """The Prometheus metrics resolver shares the read-side permission
    gate with every other Status tab resolver. Callers without
    CLUSTER_REGISTER must be denied before any Prometheus I/O fires."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cluster_prometheus_metrics(
                _info(),
                cluster_id=GUID(str(cluster.guid)),
            )


def test_prometheus_metrics_missing_cluster_returns_unavailable(
    org,
    permission_resolver,
):
    """A non-existent cluster guid returns available=False without
    raising. The UI degrades gracefully to the empty-state card
    (same contract as cluster_health / workload_health)."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_prometheus_metrics(
            _info(),
            cluster_id=GUID(str(uuid.uuid4())),
        )
    assert result.available is False


def test_prometheus_metrics_no_endpoint_returns_no_endpoint_reason(
    cluster,
    org,
    permission_resolver,
):
    """When neither provider_config nor capabilities carries a
    prometheus_endpoint, the resolver returns available=False with
    reason='no_endpoint' — no network I/O attempted."""
    cluster.provider_config = {}
    cluster.capabilities = {}
    cluster.save()

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_prometheus_metrics(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result.available is False
    assert result.reason == "no_endpoint"


def test_prometheus_metrics_endpoint_from_capabilities(
    cluster,
    org,
    permission_resolver,
    monkeypatch,
):
    """When provider_config has no endpoint but capabilities does
    (populated by the probe during bringClusterIntoManagement), the
    resolver falls back to capabilities and executes queries."""
    cluster.provider_config = {}
    cluster.capabilities = {"prometheus_endpoint": "http://prom:9090"}
    cluster.save()

    _instant_calls: list[str] = []

    def _fake_instant(*, endpoint: str, query: str, **_kw: object) -> float:
        _instant_calls.append(query)
        # count() returns int-like; ratios return 0–1.
        if "count(" in query:
            return 3.0
        return 0.55

    from astrolift_operations import prometheus_client as prom_mod

    monkeypatch.setattr(prom_mod, "query_instant", _fake_instant)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_prometheus_metrics(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )

    assert result.available is True
    assert result.node_count == 3
    assert len(_instant_calls) == 5


def test_prometheus_metrics_happy_path(
    cluster,
    org,
    permission_resolver,
    monkeypatch,
):
    """When Prometheus returns values for all five golden-signal
    queries, the resolver surfaces them 1:1 and sets available=True."""
    cluster.provider_config = {"prometheus_endpoint": "http://prom:9090"}
    cluster.save()

    counter = [0]

    def _fake_instant(*, endpoint: str, query: str, **_kw: object) -> float:
        counter[0] += 1
        if "count(" in query:
            return 5.0
        return 0.60

    from astrolift_operations import prometheus_client as prom_mod

    monkeypatch.setattr(prom_mod, "query_instant", _fake_instant)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_prometheus_metrics(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )

    assert result.available is True
    assert result.reason is None
    assert result.node_count == 5
    assert result.pod_running_ratio == pytest.approx(0.60)
    assert result.cpu_utilization == pytest.approx(0.60)
    assert result.memory_utilization == pytest.approx(0.60)
    assert result.deployment_ready_ratio == pytest.approx(0.60)
    assert counter[0] == 5  # exactly five queries fired


def test_prometheus_metrics_unreachable_returns_unreachable_reason(
    cluster,
    org,
    permission_resolver,
    monkeypatch,
):
    """A PrometheusError on any of the five queries causes the resolver
    to return available=False with reason='unreachable'. No partial
    values are exposed — the UI shows a uniform error card."""
    from astrolift_operations.prometheus_client import PrometheusUnavailable

    cluster.provider_config = {"prometheus_endpoint": "http://prom:9090"}
    cluster.save()

    def _raise(*_a: object, **_kw: object) -> float:
        raise PrometheusUnavailable("connection refused")

    from astrolift_operations import prometheus_client as prom_mod

    monkeypatch.setattr(prom_mod, "query_instant", _raise)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_prometheus_metrics(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )

    assert result.available is False
    assert result.reason == "unreachable"
    assert result.node_count is None


def test_prometheus_range_metrics_no_endpoint(
    cluster,
    org,
    permission_resolver,
):
    """Range-metrics resolver also returns available=False / reason=
    'no_endpoint' when the cluster has no prometheus_endpoint. The
    sparkline grid degrades gracefully to the empty-state card."""
    cluster.provider_config = {}
    cluster.capabilities = {}
    cluster.save()

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_prometheus_range_metrics(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result.available is False
    assert result.reason == "no_endpoint"
    assert result.series == []


def test_prometheus_range_metrics_happy_path(
    cluster,
    org,
    permission_resolver,
    monkeypatch,
):
    """When Prometheus returns data for all eight golden-signal range
    queries, the resolver returns available=True and one series per
    metric, each with at least one point."""
    cluster.provider_config = {"prometheus_endpoint": "http://prom:9090"}
    cluster.save()

    # Minimal fake: each query returns one row with two (ts, value) pairs.
    class _FakeRow:
        def __init__(self) -> None:
            import time

            self.values = [(time.time() - 30, 0.5), (time.time(), 0.6)]

    def _fake_range(*, endpoint: str, query: str, **_kw: object) -> list:
        return [_FakeRow()]

    from astrolift_operations import prometheus_client as prom_mod

    monkeypatch.setattr(prom_mod, "query_range", _fake_range)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_prometheus_range_metrics(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
            range_seconds=3600,
            step_seconds=60,
        )

    assert result.available is True
    assert result.reason is None
    assert len(result.series) == 8  # all eight golden signals
    for s in result.series:
        assert len(s.points) >= 1
        assert s.current is not None


def test_prometheus_range_metrics_all_queries_fail_returns_unreachable(
    cluster,
    org,
    permission_resolver,
    monkeypatch,
):
    """When all eight range queries fail with PrometheusError, the
    resolver returns available=False / reason='unreachable'. If only
    some fail, the resolver returns partial series (empty points for
    the failed ones) and available=True — this test targets the
    all-failed path."""
    from astrolift_operations.prometheus_client import PrometheusUnavailable

    cluster.provider_config = {"prometheus_endpoint": "http://prom:9090"}
    cluster.save()

    def _raise(*_a: object, **_kw: object) -> list:
        raise PrometheusUnavailable("connection refused")

    from astrolift_operations import prometheus_client as prom_mod

    monkeypatch.setattr(prom_mod, "query_range", _raise)

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cluster_prometheus_range_metrics(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )

    assert result.available is False
    assert result.reason == "unreachable"
    assert result.series == []
