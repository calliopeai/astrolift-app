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
