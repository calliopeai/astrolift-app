"""Tests for AKS ``exec_plugin`` auth resolution (#311).

The AKS ClusterDriver materializes a TenantCluster row carrying
``auth_method=exec_plugin`` into a ``kubeconfig`` blob fetched from
``managed_clusters.list_cluster_admin_credentials`` before delegating
to the shared k8s_native pod / log backends. These tests pin the
resolver shape, the per-(resource_group, cluster_name) TTL cache,
the cache invalidation after the window expires, and the
``ClusterAuthError`` mapping when the SDK call fails.

We mock ``ContainerServiceClient`` end-to-end: the AKS SDK shape is
``CredentialResults.kubeconfigs: list[CredentialResult]`` where each
entry has ``name`` + ``value`` (bytes); the fakes mirror that shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.cluster import ClusterAuth, ClusterContext
from azure.cluster_aks import AKSClusterDriver, AKSConfig
from k8s_native.observability import ClusterAuthError

# ---- Test doubles -------------------------------------------------


@dataclass
class FakeCredentialResult:
    name: str
    value: bytes


@dataclass
class FakeCredentialResults:
    kubeconfigs: list[FakeCredentialResult] = field(default_factory=list)


@dataclass
class FakeManagedClusters:
    """Records call counts so cache + TTL behavior is observable."""

    kubeconfig_blob: bytes = b"apiVersion: v1\nkind: Config\nclusters: []\n"
    cluster_name_seen: list[str] = field(default_factory=list)
    raise_exc: Exception | None = None
    empty_kubeconfigs: bool = False
    no_value: bool = False

    def list_cluster_admin_credentials(
        self,
        *,
        resource_group_name: str,
        resource_name: str,
    ) -> FakeCredentialResults:
        self.cluster_name_seen.append(resource_name)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.empty_kubeconfigs:
            return FakeCredentialResults(kubeconfigs=[])
        if self.no_value:
            return FakeCredentialResults(
                kubeconfigs=[FakeCredentialResult(name="clusterAdmin", value=None)],
            )
        return FakeCredentialResults(
            kubeconfigs=[
                FakeCredentialResult(
                    name="clusterAdmin",
                    value=self.kubeconfig_blob,
                ),
            ],
        )


@dataclass
class FakeAKS:
    managed_clusters: FakeManagedClusters = field(default_factory=FakeManagedClusters)


@dataclass
class RecordingPodBackend:
    seen: list[ClusterAuth] = field(default_factory=list)

    def list_pods(
        self,
        *,
        auth: ClusterAuth,
        namespace: str,
        app_slug: str,
    ) -> list[Any]:
        self.seen.append(auth)
        return []


class _Clock:
    """Monotonic-style fake clock; advanceable in test bodies."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---- Fixtures -----------------------------------------------------


def _driver(
    *,
    fake_aks: FakeAKS,
    clock: _Clock,
    pod_backend: RecordingPodBackend | None = None,
) -> tuple[AKSClusterDriver, RecordingPodBackend]:
    backend = pod_backend or RecordingPodBackend()
    drv = AKSClusterDriver(
        config=AKSConfig(
            subscription_id="sub-1",
            resource_group="rg-prod",
            cluster_name="aks-prod",
            container_service_client=fake_aks,
        ),
        pod_backend=backend,
        clock=clock,
    )
    return drv, backend


# ---- Tests --------------------------------------------------------


def test_exec_plugin_materializes_kubeconfig_from_admin_credentials() -> None:
    """An ``exec_plugin`` row gets resolved into a ``kubeconfig`` blob
    propagated verbatim into the pod backend."""
    fake = FakeAKS()
    drv, backend = _driver(fake_aks=fake, clock=_Clock())

    auth = ClusterAuth(
        slug="azure-prod",
        auth_method="exec_plugin",
        auth_config={},
    )
    drv.list_pods(auth=auth, namespace="tenant-x", app_slug="api")

    assert len(backend.seen) == 1
    resolved = backend.seen[0]
    assert resolved.auth_method == "kubeconfig"
    blob = resolved.auth_config["kubeconfig"]
    assert "apiVersion: v1" in blob
    assert fake.managed_clusters.cluster_name_seen == ["aks-prod"]


def test_exec_plugin_uses_row_overrides_over_driver_config() -> None:
    """When the row's auth_config carries ``resource_group`` /
    ``cluster_name``, they take precedence over the driver's
    AKSConfig defaults (one driver, many tenant clusters)."""
    fake = FakeAKS()
    drv, _ = _driver(fake_aks=fake, clock=_Clock())

    auth = ClusterAuth(
        slug="other",
        auth_method="exec_plugin",
        auth_config={
            "resource_group": "rg-other",
            "cluster_name": "aks-other",
            "context": "ctx-other",
        },
    )
    drv.list_pods(auth=auth, namespace="ns", app_slug="api")

    assert fake.managed_clusters.cluster_name_seen == ["aks-other"]


def test_kubeconfig_propagates_context_when_set() -> None:
    fake = FakeAKS()
    drv, backend = _driver(fake_aks=fake, clock=_Clock())

    drv.list_pods(
        auth=ClusterAuth(
            slug="azure-prod",
            auth_method="exec_plugin",
            auth_config={"context": "clusterAdmin"},
        ),
        namespace="ns",
        app_slug="api",
    )
    assert backend.seen[0].auth_config.get("context") == "clusterAdmin"


def test_kubeconfig_cached_within_ttl_window() -> None:
    """Two calls inside the TTL window only hit the Azure SDK once."""
    fake = FakeAKS()
    clock = _Clock()
    drv, _ = _driver(fake_aks=fake, clock=clock)

    auth = ClusterAuth(
        slug="azure-prod",
        auth_method="exec_plugin",
        auth_config={},
    )
    drv.list_pods(auth=auth, namespace="ns", app_slug="api")
    clock.advance(30 * 60)  # 30 min — well inside the 50 min TTL
    drv.list_pods(auth=auth, namespace="ns", app_slug="api")

    assert fake.managed_clusters.cluster_name_seen == ["aks-prod"]


def test_kubeconfig_refetched_after_ttl_expiry() -> None:
    """Past the TTL window the kubeconfig is re-fetched so a key
    rotation on the Azure side propagates without a process restart."""
    fake = FakeAKS()
    clock = _Clock()
    drv, _ = _driver(fake_aks=fake, clock=clock)

    auth = ClusterAuth(
        slug="azure-prod",
        auth_method="exec_plugin",
        auth_config={},
    )
    drv.list_pods(auth=auth, namespace="ns", app_slug="api")

    # Drop the cached blob's value so the second call sees a different
    # kubeconfig and we can verify the refetch end-to-end.
    fake.managed_clusters.kubeconfig_blob = (
        b"apiVersion: v1\nkind: Config\nclusters: [{name: rotated}]\n"
    )
    clock.advance(50 * 60 + 1)  # one second past the TTL
    backend = RecordingPodBackend()
    drv._pod_backend = backend  # tail-call into a fresh recorder
    drv.list_pods(auth=auth, namespace="ns", app_slug="api")

    assert len(fake.managed_clusters.cluster_name_seen) == 2
    assert "rotated" in backend.seen[0].auth_config["kubeconfig"]


def test_separate_cache_keys_per_cluster() -> None:
    """A driver serving two TenantClusters caches each independently."""
    fake = FakeAKS()
    clock = _Clock()
    drv, _ = _driver(fake_aks=fake, clock=clock)

    drv.list_pods(
        auth=ClusterAuth(
            slug="a",
            auth_method="exec_plugin",
            auth_config={"resource_group": "rg-a", "cluster_name": "aks-a"},
        ),
        namespace="ns",
        app_slug="api",
    )
    drv.list_pods(
        auth=ClusterAuth(
            slug="b",
            auth_method="exec_plugin",
            auth_config={"resource_group": "rg-b", "cluster_name": "aks-b"},
        ),
        namespace="ns",
        app_slug="api",
    )
    # Second call against each cluster should hit the cache.
    drv.list_pods(
        auth=ClusterAuth(
            slug="a",
            auth_method="exec_plugin",
            auth_config={"resource_group": "rg-a", "cluster_name": "aks-a"},
        ),
        namespace="ns",
        app_slug="api",
    )

    assert fake.managed_clusters.cluster_name_seen == ["aks-a", "aks-b"]


def test_clusterautherror_raised_when_sdk_call_fails() -> None:
    fake = FakeAKS(
        managed_clusters=FakeManagedClusters(
            raise_exc=RuntimeError("HTTP 403 Forbidden — managed identity lacks"
                                   " Microsoft.ContainerService/managedClusters/listClusterAdminCredential/action"),
        ),
    )
    drv, _ = _driver(fake_aks=fake, clock=_Clock())

    with pytest.raises(ClusterAuthError) as exc_info:
        drv.list_pods(
            auth=ClusterAuth(
                slug="azure-prod",
                auth_method="exec_plugin",
                auth_config={},
            ),
            namespace="ns",
            app_slug="api",
        )
    msg = str(exc_info.value)
    assert "rg-prod/aks-prod" in msg
    assert "list_cluster_admin_credentials failed" in msg


def test_clusterautherror_when_kubeconfigs_list_is_empty() -> None:
    fake = FakeAKS(managed_clusters=FakeManagedClusters(empty_kubeconfigs=True))
    drv, _ = _driver(fake_aks=fake, clock=_Clock())

    with pytest.raises(ClusterAuthError, match="returned no kubeconfigs"):
        drv.list_pods(
            auth=ClusterAuth(
                slug="azure-prod",
                auth_method="exec_plugin",
                auth_config={},
            ),
            namespace="ns",
            app_slug="api",
        )


def test_clusterautherror_when_kubeconfig_value_is_missing() -> None:
    fake = FakeAKS(managed_clusters=FakeManagedClusters(no_value=True))
    drv, _ = _driver(fake_aks=fake, clock=_Clock())

    with pytest.raises(ClusterAuthError, match=r"kubeconfig\.value is empty"):
        drv.list_pods(
            auth=ClusterAuth(
                slug="azure-prod",
                auth_method="exec_plugin",
                auth_config={},
            ),
            namespace="ns",
            app_slug="api",
        )


def test_failed_fetch_is_not_cached() -> None:
    """A failure must not poison the cache — a fixed SDK on the next
    call should succeed."""
    fake_managed = FakeManagedClusters(raise_exc=RuntimeError("transient 503"))
    fake = FakeAKS(managed_clusters=fake_managed)
    drv, backend = _driver(fake_aks=fake, clock=_Clock())

    with pytest.raises(ClusterAuthError):
        drv.list_pods(
            auth=ClusterAuth(slug="azure-prod", auth_method="exec_plugin"),
            namespace="ns",
            app_slug="api",
        )

    fake_managed.raise_exc = None
    drv.list_pods(
        auth=ClusterAuth(slug="azure-prod", auth_method="exec_plugin"),
        namespace="ns",
        app_slug="api",
    )
    assert len(backend.seen) == 1
    assert backend.seen[0].auth_method == "kubeconfig"


def test_kubeconfig_auth_method_passes_through_unchanged() -> None:
    """Non-``exec_plugin`` rows must not trigger the AKS SDK call."""
    fake = FakeAKS()
    drv, backend = _driver(fake_aks=fake, clock=_Clock())

    raw = ClusterAuth(
        slug="azure-prod",
        auth_method="kubeconfig",
        auth_config={"kubeconfig": "apiVersion: v1\n"},
    )
    drv.list_pods(auth=raw, namespace="ns", app_slug="api")

    assert fake.managed_clusters.cluster_name_seen == []
    assert backend.seen[0] is raw


def test_stream_logs_routes_through_same_resolver() -> None:
    """``stream_logs`` materializes the same way so the log
    subscription path benefits from the cache too."""

    class RecordingLogBackend:
        def __init__(self) -> None:
            self.seen: list[ClusterAuth] = []

        def stream(
            self,
            *,
            auth: ClusterAuth,
            namespace: str,
            pod_name: str,
            container: str | None,
            tail_lines: int,
            follow: bool,
        ) -> Any:
            self.seen.append(auth)

            async def _empty():
                if False:  # pragma: no cover
                    yield None

            return _empty()

    fake = FakeAKS()
    log_backend = RecordingLogBackend()
    drv = AKSClusterDriver(
        config=AKSConfig(
            subscription_id="sub-1",
            resource_group="rg-prod",
            cluster_name="aks-prod",
            container_service_client=fake,
        ),
        log_backend=log_backend,
        clock=_Clock(),
    )
    drv.stream_logs(
        auth=ClusterAuth(slug="azure-prod", auth_method="exec_plugin"),
        namespace="ns",
        pod_name="p",
        container=None,
        tail_lines=10,
        follow=False,
    )
    assert log_backend.seen[0].auth_method == "kubeconfig"


def test_cluster_context_resolver_used_for_probe_path() -> None:
    """``probe_capabilities`` resolves the context the same way so
    ``bring_into_management`` works for ``exec_plugin`` rows too.

    We don't exercise the full probe — the management backend
    protocol is wide and exercised in its own tests. Instead the
    fake's first read raises a sentinel and we capture the
    ClusterContext the helper saw at that point.
    """

    captured: dict[str, ClusterContext] = {}

    class _SentinelError(Exception):
        pass

    class StubManagementBackend:
        def list_cluster_crds(self, *, auth: ClusterAuth) -> Any:
            captured["auth"] = auth  # type: ignore[assignment]
            raise _SentinelError("stop after capture")

    fake = FakeAKS()
    drv = AKSClusterDriver(
        config=AKSConfig(
            subscription_id="sub-1",
            resource_group="rg-prod",
            cluster_name="aks-prod",
            container_service_client=fake,
        ),
        management_backend=StubManagementBackend(),
        clock=_Clock(),
    )

    with pytest.raises(_SentinelError):
        drv.probe_capabilities(
            ClusterContext(slug="azure-prod", auth_method="exec_plugin"),
        )
    resolved_auth = captured["auth"]
    assert resolved_auth.auth_method == "kubeconfig"
    assert "apiVersion: v1" in resolved_auth.auth_config["kubeconfig"]
