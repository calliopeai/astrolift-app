"""Tests for the GKE Workload Identity ``exec_plugin`` token mint (#310).

The driver materializes an ``exec_plugin`` ClusterAuth / ClusterContext
into a ``kubeconfig`` blob carrying:

  - cluster endpoint + CA from ``container.get_cluster``
  - a freshly-refreshed bearer from ``google.auth.default()``

so the shared k8s_native ``build_api_client`` never has to know about
GCP-specific auth. The materializer caches the synthesized blob for
~50 minutes; back-to-back resolver calls reuse it, and a re-mint
happens only after expiry.

Each test injects a fake ``ClusterManagerClient`` + a fake
``credentials_factory`` so nothing here talks to a real GCP project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
import yaml

from _sdk.cluster import ClusterAuth, ClusterContext
from gcp.cluster_gke import (
    _WI_KUBECONFIG_TTL_SECONDS,
    GKEClusterDriver,
    GKEConfig,
)
from k8s_native.observability import ClusterAuthError

# ---- fake GCP surface --------------------------------------------


@dataclass
class _FakeMasterAuth:
    cluster_ca_certificate: str = "BASE64-CA-PEM"


@dataclass
class _FakeCluster:
    endpoint: str = "10.20.30.40"
    master_auth: _FakeMasterAuth = field(default_factory=_FakeMasterAuth)


class _FakeContainerClient:
    """Records ``get_cluster`` calls; serves a successful response
    unless ``raise_on_get`` is set, in which case the next call
    raises that exception once."""

    def __init__(
        self,
        *,
        cluster: _FakeCluster | None = None,
        raise_on_get: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._cluster = cluster or _FakeCluster()
        self._raise_on_get = raise_on_get

    def get_cluster(self, *, name: str) -> _FakeCluster:
        self.calls.append(name)
        if self._raise_on_get is not None:
            exc = self._raise_on_get
            self._raise_on_get = None
            raise exc
        return self._cluster


class _FakeCredentials:
    """Stand-in for a ``google.auth.credentials.Credentials`` instance.

    ``refresh`` is what production calls with a Request transport; we
    populate ``token`` here so the materializer sees a non-empty
    bearer. ``refresh_count`` lets tests assert the cache hit / miss
    behavior without monkeypatching.
    """

    def __init__(self, token: str = "wi-bearer-token") -> None:
        self.token = ""
        self._next_token = token
        self.refresh_count = 0

    def refresh(self, _request: Any) -> None:
        self.refresh_count += 1
        self.token = f"{self._next_token}-{self.refresh_count}"


# ---- fixtures ----------------------------------------------------


@pytest.fixture
def fake_container() -> _FakeContainerClient:
    return _FakeContainerClient()


@pytest.fixture
def fake_creds() -> _FakeCredentials:
    return _FakeCredentials()


@pytest.fixture
def clock() -> dict[str, float]:
    return {"t": 1000.0}


@pytest.fixture
def driver(
    fake_container: _FakeContainerClient,
    fake_creds: _FakeCredentials,
    clock: dict[str, float],
) -> GKEClusterDriver:
    return GKEClusterDriver(
        config=GKEConfig(
            project_id="acme",
            location="us-central1",
            cluster_name="prod",
            container_client=fake_container,
        ),
        k8s_client_factory=lambda **kw: object(),
        credentials_factory=lambda: (fake_creds, "acme"),
        clock=lambda: clock["t"],
    )


# ---- happy path: kubeconfig synthesis ----------------------------


def test_materialize_auth_synthesizes_kubeconfig(
    driver: GKEClusterDriver,
    fake_container: _FakeContainerClient,
    fake_creds: _FakeCredentials,
) -> None:
    auth = ClusterAuth(
        slug="prod-gke",
        auth_method="exec_plugin",
        auth_config={
            "project_id": "acme",
            "location": "us-central1",
            "cluster_name": "prod",
        },
    )

    materialized = driver._materialize_gke_auth_auth(auth)

    assert materialized.auth_method == "kubeconfig"
    assert materialized.slug == "prod-gke"
    assert fake_container.calls == [
        "projects/acme/locations/us-central1/clusters/prod",
    ]
    assert fake_creds.refresh_count == 1

    blob = materialized.auth_config["kubeconfig"]
    assert isinstance(blob, str) and blob
    parsed = yaml.safe_load(blob)
    assert parsed["apiVersion"] == "v1"
    assert parsed["kind"] == "Config"
    assert parsed["clusters"][0]["cluster"]["server"] == "https://10.20.30.40"
    assert parsed["clusters"][0]["cluster"]["certificate-authority-data"] == "BASE64-CA-PEM"
    assert parsed["users"][0]["user"]["token"] == "wi-bearer-token-1"
    assert parsed["current-context"] == "gke_acme_us-central1_prod"


def test_materialize_context_synthesizes_kubeconfig(
    driver: GKEClusterDriver,
    fake_creds: _FakeCredentials,
) -> None:
    ctx = ClusterContext(
        slug="prod-gke",
        auth_method="exec_plugin",
        auth_config={
            "project_id": "acme",
            "location": "us-central1",
            "cluster_name": "prod",
        },
        ingress_class="nginx",
        provider_plugin_slug="gcp",
    )

    materialized = driver._materialize_gke_auth_context(ctx)

    assert materialized.auth_method == "kubeconfig"
    # ClusterContext fields outside the auth surface survive replace.
    assert materialized.ingress_class == "nginx"
    assert materialized.provider_plugin_slug == "gcp"
    parsed = yaml.safe_load(materialized.auth_config["kubeconfig"])
    assert parsed["users"][0]["user"]["token"] == "wi-bearer-token-1"


# ---- pass-through for non-exec_plugin methods --------------------


def test_materialize_passes_through_kubeconfig_auth_unchanged(
    driver: GKEClusterDriver,
    fake_container: _FakeContainerClient,
    fake_creds: _FakeCredentials,
) -> None:
    """``kubeconfig`` rows must NOT trigger a get_cluster + refresh —
    the row already carries every piece of auth the backend needs."""
    auth = ClusterAuth(
        slug="prod-gke",
        auth_method="kubeconfig",
        auth_config={"kubeconfig": "apiVersion: v1\nkind: Config\n"},
    )
    out = driver._materialize_gke_auth_auth(auth)
    assert out is auth
    assert fake_container.calls == []
    assert fake_creds.refresh_count == 0


def test_materialize_passes_through_token_auth_unchanged(
    driver: GKEClusterDriver,
    fake_container: _FakeContainerClient,
    fake_creds: _FakeCredentials,
) -> None:
    auth = ClusterAuth(
        slug="prod-gke",
        auth_method="service_account_token",
        auth_config={"token": "static-bearer"},
        endpoint="https://1.2.3.4",
    )
    out = driver._materialize_gke_auth_auth(auth)
    assert out is auth
    assert fake_container.calls == []
    assert fake_creds.refresh_count == 0


# ---- caching behaviour -------------------------------------------


def test_kubeconfig_cached_within_window(
    driver: GKEClusterDriver,
    fake_container: _FakeContainerClient,
    fake_creds: _FakeCredentials,
    clock: dict[str, float],
) -> None:
    auth = ClusterAuth(
        slug="prod-gke",
        auth_method="exec_plugin",
        auth_config={
            "project_id": "acme",
            "location": "us-central1",
            "cluster_name": "prod",
        },
    )

    first = driver._materialize_gke_auth_auth(auth)
    # Advance to just under the TTL — cache MUST still hit.
    clock["t"] += _WI_KUBECONFIG_TTL_SECONDS - 1
    second = driver._materialize_gke_auth_auth(auth)

    assert first.auth_config["kubeconfig"] == second.auth_config["kubeconfig"]
    assert fake_container.calls == [
        "projects/acme/locations/us-central1/clusters/prod",
    ]
    assert fake_creds.refresh_count == 1


def test_kubeconfig_remint_after_window_expiry(
    driver: GKEClusterDriver,
    fake_container: _FakeContainerClient,
    fake_creds: _FakeCredentials,
    clock: dict[str, float],
) -> None:
    auth = ClusterAuth(
        slug="prod-gke",
        auth_method="exec_plugin",
        auth_config={
            "project_id": "acme",
            "location": "us-central1",
            "cluster_name": "prod",
        },
    )

    first = driver._materialize_gke_auth_auth(auth)
    # Step just past the TTL — driver MUST re-mint.
    clock["t"] += _WI_KUBECONFIG_TTL_SECONDS + 1
    second = driver._materialize_gke_auth_auth(auth)

    assert fake_creds.refresh_count == 2
    assert fake_container.calls == [
        "projects/acme/locations/us-central1/clusters/prod",
        "projects/acme/locations/us-central1/clusters/prod",
    ]
    first_token = yaml.safe_load(first.auth_config["kubeconfig"])["users"][0]["user"]["token"]
    second_token = yaml.safe_load(second.auth_config["kubeconfig"])["users"][0]["user"]["token"]
    assert first_token == "wi-bearer-token-1"
    assert second_token == "wi-bearer-token-2"


def test_kubeconfig_cache_keyed_per_cluster(
    fake_creds: _FakeCredentials,
    clock: dict[str, float],
) -> None:
    """Two clusters in the same project share the driver but must
    each maintain their own cache entry."""
    container = _FakeContainerClient()
    drv = GKEClusterDriver(
        config=GKEConfig(
            project_id="acme",
            location="us-central1",
            cluster_name="prod",
            container_client=container,
        ),
        k8s_client_factory=lambda **kw: object(),
        credentials_factory=lambda: (fake_creds, "acme"),
        clock=lambda: clock["t"],
    )
    base_cfg = {"project_id": "acme", "location": "us-central1"}

    drv._materialize_gke_auth_auth(
        ClusterAuth(
            slug="a",
            auth_method="exec_plugin",
            auth_config={**base_cfg, "cluster_name": "prod"},
        )
    )
    drv._materialize_gke_auth_auth(
        ClusterAuth(
            slug="b",
            auth_method="exec_plugin",
            auth_config={**base_cfg, "cluster_name": "staging"},
        )
    )
    # Two distinct clusters → two get_cluster calls + two refreshes.
    assert fake_creds.refresh_count == 2
    assert len(container.calls) == 2


# ---- error path: get_cluster failure -----------------------------


def test_materialize_raises_cluster_auth_error_on_get_cluster_failure(
    fake_creds: _FakeCredentials,
    clock: dict[str, float],
) -> None:
    boom = RuntimeError("cluster not found")
    container = _FakeContainerClient(raise_on_get=boom)
    drv = GKEClusterDriver(
        config=GKEConfig(
            project_id="acme",
            location="us-central1",
            cluster_name="prod",
            container_client=container,
        ),
        k8s_client_factory=lambda **kw: object(),
        credentials_factory=lambda: (fake_creds, "acme"),
        clock=lambda: clock["t"],
    )

    with pytest.raises(ClusterAuthError) as exc_info:
        drv._materialize_gke_auth_auth(
            ClusterAuth(
                slug="prod-gke",
                auth_method="exec_plugin",
                auth_config={
                    "project_id": "acme",
                    "location": "us-central1",
                    "cluster_name": "prod",
                },
            )
        )
    # Credentials must NOT have been refreshed — the failure happens
    # before we ever resolve ADC.
    assert fake_creds.refresh_count == 0
    assert "GKE get_cluster failed" in str(exc_info.value)


def test_materialize_raises_cluster_auth_error_on_credentials_failure(
    fake_container: _FakeContainerClient,
    clock: dict[str, float],
) -> None:
    def _boom() -> tuple[Any, str | None]:
        raise RuntimeError("ADC not available")

    drv = GKEClusterDriver(
        config=GKEConfig(
            project_id="acme",
            location="us-central1",
            cluster_name="prod",
            container_client=fake_container,
        ),
        k8s_client_factory=lambda **kw: object(),
        credentials_factory=_boom,
        clock=lambda: clock["t"],
    )

    with pytest.raises(ClusterAuthError) as exc_info:
        drv._materialize_gke_auth_auth(
            ClusterAuth(
                slug="prod-gke",
                auth_method="exec_plugin",
                auth_config={
                    "project_id": "acme",
                    "location": "us-central1",
                    "cluster_name": "prod",
                },
            )
        )
    assert "google.auth.default()" in str(exc_info.value)


def test_materialize_raises_cluster_auth_error_on_empty_token(
    fake_container: _FakeContainerClient,
    clock: dict[str, float],
) -> None:
    class _EmptyTokenCreds:
        token = ""

        def refresh(self, _req: Any) -> None:
            # Refresh succeeds but the credential surface never
            # populated a bearer — e.g. a misconfigured WI binding
            # where the impersonation chain returned an empty body.
            return None

    drv = GKEClusterDriver(
        config=GKEConfig(
            project_id="acme",
            location="us-central1",
            cluster_name="prod",
            container_client=fake_container,
        ),
        k8s_client_factory=lambda **kw: object(),
        credentials_factory=lambda: (_EmptyTokenCreds(), "acme"),
        clock=lambda: clock["t"],
    )

    with pytest.raises(ClusterAuthError) as exc_info:
        drv._materialize_gke_auth_auth(
            ClusterAuth(
                slug="prod-gke",
                auth_method="exec_plugin",
                auth_config={
                    "project_id": "acme",
                    "location": "us-central1",
                    "cluster_name": "prod",
                },
            )
        )
    assert "empty token" in str(exc_info.value)


# ---- end-to-end via list_pods + bring_into_management ------------


def test_list_pods_routes_through_materializer(
    driver: GKEClusterDriver,
    fake_container: _FakeContainerClient,
    fake_creds: _FakeCredentials,
) -> None:
    """``list_pods`` MUST materialize exec_plugin before delegating
    so the pod backend sees a kubeconfig-shaped auth."""
    captured: dict[str, Any] = {}

    class _Backend:
        def list_pods(self, *, auth: Any, namespace: str, app_slug: str) -> list[Any]:
            captured["auth"] = auth
            captured["namespace"] = namespace
            captured["app_slug"] = app_slug
            return []

    driver._pod_backend = _Backend()

    auth = ClusterAuth(
        slug="prod-gke",
        auth_method="exec_plugin",
        auth_config={
            "project_id": "acme",
            "location": "us-central1",
            "cluster_name": "prod",
        },
    )
    out = driver.list_pods(auth=auth, namespace="tenant-a", app_slug="web")
    assert out == []
    assert captured["namespace"] == "tenant-a"
    assert captured["app_slug"] == "web"
    delivered = captured["auth"]
    assert delivered.auth_method == "kubeconfig"
    assert "kubeconfig" in delivered.auth_config
    assert fake_creds.refresh_count == 1
    assert fake_container.calls == [
        "projects/acme/locations/us-central1/clusters/prod",
    ]


def test_bring_into_management_routes_through_materializer(
    driver: GKEClusterDriver,
    fake_container: _FakeContainerClient,
    fake_creds: _FakeCredentials,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bring_into_management`` follows the same materialize-then-
    delegate path so the management backend never sees exec_plugin."""
    captured: dict[str, Any] = {}

    def _fake_run(*, backend: Any, cluster: ClusterContext, run_preflight: bool) -> Any:
        captured["cluster"] = cluster
        captured["run_preflight"] = run_preflight
        from _sdk.cluster import ManagementReport

        return ManagementReport(
            success=True,
            rbac_applied=True,
            capabilities={},
            preflight_status="skipped",
        )

    monkeypatch.setattr("gcp.cluster_gke.run_bring_into_management", _fake_run)

    ctx = ClusterContext(
        slug="prod-gke",
        auth_method="exec_plugin",
        auth_config={
            "project_id": "acme",
            "location": "us-central1",
            "cluster_name": "prod",
        },
    )
    report = driver.bring_into_management(ctx, run_preflight=False)
    assert report.success is True
    assert captured["run_preflight"] is False
    assert captured["cluster"].auth_method == "kubeconfig"
    assert "kubeconfig" in captured["cluster"].auth_config
    assert fake_creds.refresh_count == 1
