"""EKS exec_plugin token-mint + kubeconfig synthesis (#309).

The EKS driver's runtime-observability path (``list_pods`` /
``stream_logs``) used to raise ``ClusterAuthError`` whenever the
TenantCluster row carried ``auth_method="exec_plugin"`` — the IRSA
pattern. These tests pin the driver's resolution of that auth blob:

1. DescribeCluster is called once for the endpoint + CA; subsequent
   exec_plugin lookups reuse the cache.
2. The bearer token is minted via ``mint_eks_token`` and the result
   is cached for ``exec_plugin_token_ttl_seconds`` against the
   driver's monotonic-clock injection point.
3. The synthesized auth payload is a real kubeconfig blob the
   shared ``build_api_client`` ``kubeconfig`` branch can consume.
4. DescribeCluster failure surfaces as a typed ProviderError so the
   resolver can swallow + log instead of leaking the boto3 stack.

The tests use pure mocks — moto's EKS surface doesn't model token
mint at all, and we don't want to depend on its describe_cluster
response shape staying stable across versions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
import yaml
from botocore.exceptions import ClientError

from _sdk.cluster import ClusterAuth, ClusterContext
from aws._errors import NotFoundError, ProviderError
from aws.cluster_eks import EKSClusterDriver, EKSConfig

if TYPE_CHECKING:
    from collections.abc import Callable


# ---- fixtures -----------------------------------------------------


def _eks_client_mock(*, endpoint: str = "https://example.eks.amazonaws.com", ca: str = "Zm9v") -> MagicMock:
    """Build an EKS boto3 client mock with a canned DescribeCluster.

    ``ca`` is the base64 form EKS returns from the real API. We don't
    decode it — the kubeconfig branch passes the base64 through to
    the kubernetes client which does its own decode.
    """
    client = MagicMock()
    client.describe_cluster.return_value = {
        "cluster": {
            "endpoint": endpoint,
            "certificateAuthority": {"data": ca},
        },
    }
    return client


class _Clock:
    """Test clock that advances explicitly. Mirrors ``time.monotonic``
    semantics (returns float seconds, never decreases) so the cache's
    TTL math behaves exactly as in production."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _RecordingMinter:
    """Token-mint double that returns a unique token per call and
    records its arguments. The unique token lets cache-hit tests
    distinguish a cached return from a re-mint without time tricks."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._counter = 0

    def __call__(self, cluster_name: str, region: str) -> str:
        self.calls.append((cluster_name, region))
        self._counter += 1
        return f"k8s-aws-v1.token-{self._counter}"


def _driver(
    *,
    eks_client: MagicMock,
    minter: Callable[[str, str], str],
    clock: Callable[[], float],
    ttl: int = 13 * 60,
) -> EKSClusterDriver:
    return EKSClusterDriver(
        config=EKSConfig(
            region="us-east-1",
            cluster_name="prod-eks",
            exec_plugin_token_ttl_seconds=ttl,
        ),
        eks_client=eks_client,
        sts_client=MagicMock(),
        # Test factory short-circuits the k8s client wiring so
        # constructing the driver doesn't try to import kubernetes.
        k8s_client_factory=lambda **_kw: MagicMock(),
        token_minter=minter,
        monotonic_clock=clock,
    )


def _exec_plugin_auth(slug: str = "prod-eks-row") -> ClusterAuth:
    return ClusterAuth(
        slug=slug,
        auth_method="exec_plugin",
        auth_config={
            "cluster_name": "prod-eks",
            "region": "us-east-1",
        },
    )


def _exec_plugin_context(slug: str = "prod-eks-row") -> ClusterContext:
    return ClusterContext(
        slug=slug,
        auth_method="exec_plugin",
        auth_config={
            "cluster_name": "prod-eks",
            "region": "us-east-1",
        },
    )


# ---- describe + kubeconfig shape ----------------------------------


def test_synthesizes_kubeconfig_from_describe_cluster() -> None:
    """First exec_plugin call materializes a kubeconfig YAML blob
    that carries endpoint, base64 CA, and the freshly-minted token."""
    eks = _eks_client_mock(endpoint="https://abc.eks.amazonaws.com", ca="Y2EtZGF0YQ==")
    minter = _RecordingMinter()
    clock = _Clock()
    driver = _driver(eks_client=eks, minter=minter, clock=clock)

    resolved = driver._resolve_eks_auth(_exec_plugin_auth())

    assert resolved.auth_method == "kubeconfig"
    blob = resolved.auth_config["kubeconfig"]
    parsed = yaml.safe_load(blob)
    assert parsed["apiVersion"] == "v1"
    assert parsed["kind"] == "Config"
    [cluster_entry] = parsed["clusters"]
    assert cluster_entry["name"] == "prod-eks"
    assert cluster_entry["cluster"]["server"] == "https://abc.eks.amazonaws.com"
    # CA stays base64 — kubernetes client decodes downstream.
    assert cluster_entry["cluster"]["certificate-authority-data"] == "Y2EtZGF0YQ=="
    [user_entry] = parsed["users"]
    assert user_entry["user"]["token"].startswith("k8s-aws-v1.")
    [context_entry] = parsed["contexts"]
    assert context_entry["context"]["cluster"] == "prod-eks"
    assert context_entry["context"]["user"] == user_entry["name"]
    assert parsed["current-context"] == "prod-eks-row"
    assert eks.describe_cluster.call_count == 1
    assert minter.calls == [("prod-eks", "us-east-1")]


def test_materialize_context_uses_same_synthesis() -> None:
    """ClusterContext path returns a kubeconfig blob too — bring/probe
    workflows need it on the same shape as the resolver path."""
    eks = _eks_client_mock()
    minter = _RecordingMinter()
    driver = _driver(eks_client=eks, minter=minter, clock=_Clock())

    resolved = driver._resolve_eks_auth_context(_exec_plugin_context())

    assert resolved.auth_method == "kubeconfig"
    parsed = yaml.safe_load(resolved.auth_config["kubeconfig"])
    # The cluster row's slug is the context name so the kubernetes
    # client picks the right context on load.
    assert parsed["current-context"] == "prod-eks-row"


def test_non_exec_plugin_auth_passes_through_untouched() -> None:
    """kubeconfig + service_account_token rows skip the EKS shim."""
    eks = _eks_client_mock()
    driver = _driver(eks_client=eks, minter=_RecordingMinter(), clock=_Clock())

    kc_auth = ClusterAuth(
        slug="x",
        auth_method="kubeconfig",
        auth_config={"kubeconfig": "yaml: here"},
    )
    assert driver._resolve_eks_auth(kc_auth) is kc_auth
    assert eks.describe_cluster.call_count == 0


def test_legacy_row_without_cluster_name_falls_back_to_config() -> None:
    """``auth_config`` from a pre-auto-discovery row may omit the
    cluster_name / region keys; the driver fills them from EKSConfig."""
    eks = _eks_client_mock()
    minter = _RecordingMinter()
    driver = _driver(eks_client=eks, minter=minter, clock=_Clock())

    auth = ClusterAuth(slug="legacy", auth_method="exec_plugin", auth_config={})
    driver._resolve_eks_auth(auth)

    assert minter.calls == [("prod-eks", "us-east-1")]
    eks.describe_cluster.assert_called_once_with(name="prod-eks")


# ---- token cache behavior -----------------------------------------


def test_token_cached_within_ttl_window() -> None:
    """Back-to-back calls inside the TTL reuse the cached token."""
    eks = _eks_client_mock()
    minter = _RecordingMinter()
    clock = _Clock()
    driver = _driver(eks_client=eks, minter=minter, clock=clock, ttl=13 * 60)

    first = driver._resolve_eks_auth(_exec_plugin_auth())
    clock.advance(60.0)  # 1 minute later — well inside TTL
    second = driver._resolve_eks_auth(_exec_plugin_auth())

    first_token = yaml.safe_load(first.auth_config["kubeconfig"])["users"][0]["user"]["token"]
    second_token = yaml.safe_load(second.auth_config["kubeconfig"])["users"][0]["user"]["token"]

    assert first_token == second_token
    assert len(minter.calls) == 1
    # DescribeCluster is also cached — same answer.
    assert eks.describe_cluster.call_count == 1


def test_token_re_minted_after_ttl_expiry() -> None:
    """When the cached token's deadline passes, the next call mints
    fresh and seeds a new expiry."""
    eks = _eks_client_mock()
    minter = _RecordingMinter()
    clock = _Clock()
    driver = _driver(eks_client=eks, minter=minter, clock=clock, ttl=13 * 60)

    first = driver._resolve_eks_auth(_exec_plugin_auth())
    # Step past the TTL boundary.
    clock.advance(13 * 60 + 1.0)
    second = driver._resolve_eks_auth(_exec_plugin_auth())

    first_token = yaml.safe_load(first.auth_config["kubeconfig"])["users"][0]["user"]["token"]
    second_token = yaml.safe_load(second.auth_config["kubeconfig"])["users"][0]["user"]["token"]

    assert first_token != second_token
    assert len(minter.calls) == 2


def test_token_at_exact_expiry_boundary_re_mints() -> None:
    """Cache check is strictly ``expires_at > now``; landing exactly
    on expiry must re-mint so we never serve a token at its expiry
    instant (clock jitter would push it just past on the apiserver)."""
    eks = _eks_client_mock()
    minter = _RecordingMinter()
    clock = _Clock()
    driver = _driver(eks_client=eks, minter=minter, clock=clock, ttl=60)

    driver._resolve_eks_auth(_exec_plugin_auth())
    clock.advance(60.0)  # Exactly at expiry.
    driver._resolve_eks_auth(_exec_plugin_auth())

    assert len(minter.calls) == 2


def test_tokens_cached_per_cluster_region_pair() -> None:
    """A driver pointed at multiple TenantCluster rows mints once
    per (cluster_name, region) — distinct keys never collide."""
    eks = _eks_client_mock()
    minter = _RecordingMinter()
    driver = _driver(eks_client=eks, minter=minter, clock=_Clock())

    a = ClusterAuth(
        slug="row-a",
        auth_method="exec_plugin",
        auth_config={"cluster_name": "cluster-a", "region": "us-east-1"},
    )
    b = ClusterAuth(
        slug="row-b",
        auth_method="exec_plugin",
        auth_config={"cluster_name": "cluster-b", "region": "us-east-1"},
    )
    driver._resolve_eks_auth(a)
    driver._resolve_eks_auth(b)
    driver._resolve_eks_auth(a)  # cache hit

    assert minter.calls == [
        ("cluster-a", "us-east-1"),
        ("cluster-b", "us-east-1"),
    ]


# ---- error handling -----------------------------------------------


def test_describe_cluster_not_found_raises_typed_error() -> None:
    """ClusterNotFoundException becomes a ProviderError subclass so
    the resolver can pattern-match on type, not error code."""
    eks = MagicMock()
    eks.describe_cluster.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "no such cluster"}},
        "DescribeCluster",
    )
    driver = _driver(eks_client=eks, minter=_RecordingMinter(), clock=_Clock())

    with pytest.raises(NotFoundError):
        driver._resolve_eks_auth(_exec_plugin_auth())


def test_describe_cluster_other_failure_raises_provider_error() -> None:
    """Throttling / access-denied / generic ClientError still maps
    through to a ProviderError so the call site has a typed catch."""
    eks = MagicMock()
    eks.describe_cluster.side_effect = ClientError(
        {"Error": {"Code": "ServiceUnavailable", "Message": "try again"}},
        "DescribeCluster",
    )
    driver = _driver(eks_client=eks, minter=_RecordingMinter(), clock=_Clock())

    with pytest.raises(ProviderError):
        driver._resolve_eks_auth(_exec_plugin_auth())


def test_token_mint_failure_raises_provider_error_and_skips_cache() -> None:
    """A failure inside the minter propagates as a typed
    ProviderError AND must not seed a cache entry — otherwise a
    subsequent call would happily return the half-built state."""
    eks = _eks_client_mock()

    def broken_minter(cluster_name: str, region: str) -> str:
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "iam denied"}},
            "GetCallerIdentity",
        )

    driver = _driver(eks_client=eks, minter=broken_minter, clock=_Clock())

    with pytest.raises(ProviderError):
        driver._resolve_eks_auth(_exec_plugin_auth())

    assert driver._token_cache == {}


# ---- describe-cache invalidation ----------------------------------


def test_invalidate_describe_cache_forces_redescribe() -> None:
    """Operators rotating cluster CA can clear the describe cache
    without throwing the bearer-token cache away."""
    eks = _eks_client_mock(endpoint="https://old.example", ca="Y2E=")
    minter = _RecordingMinter()
    driver = _driver(eks_client=eks, minter=minter, clock=_Clock())

    driver._resolve_eks_auth(_exec_plugin_auth())
    assert eks.describe_cluster.call_count == 1

    # Pretend the operator rotated the CA — next DescribeCluster
    # returns new endpoint + CA, but the token (still inside TTL)
    # stays cached.
    eks.describe_cluster.return_value = {
        "cluster": {
            "endpoint": "https://new.example",
            "certificateAuthority": {"data": "bmV3LWNh"},
        },
    }
    driver.invalidate_describe_cache("prod-eks")
    resolved = driver._resolve_eks_auth(_exec_plugin_auth())

    parsed = yaml.safe_load(resolved.auth_config["kubeconfig"])
    assert parsed["clusters"][0]["cluster"]["server"] == "https://new.example"
    assert parsed["clusters"][0]["cluster"]["certificate-authority-data"] == "bmV3LWNh"
    assert eks.describe_cluster.call_count == 2
    # Token came from the still-valid cache: minter called exactly once.
    assert len(minter.calls) == 1


# ---- list_pods / stream_logs integration --------------------------


def test_list_pods_materializes_auth_before_dispatch() -> None:
    """The driver's list_pods passes a synthesized kubeconfig auth
    to the pod backend — not the raw exec_plugin row."""
    captured: dict[str, Any] = {}

    class _Backend:
        def list_pods(self, *, auth: ClusterAuth, namespace: str, app_slug: str) -> list:
            captured["auth"] = auth
            captured["namespace"] = namespace
            captured["app_slug"] = app_slug
            return []

    eks = _eks_client_mock()
    minter = _RecordingMinter()
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-east-1", cluster_name="prod-eks"),
        eks_client=eks,
        sts_client=MagicMock(),
        k8s_client_factory=lambda **_kw: MagicMock(),
        pod_backend=_Backend(),
        token_minter=minter,
        monotonic_clock=_Clock(),
    )

    driver.list_pods(auth=_exec_plugin_auth(), namespace="ns", app_slug="api")

    auth = captured["auth"]
    assert auth.auth_method == "kubeconfig"
    assert "kubeconfig" in auth.auth_config
    assert len(minter.calls) == 1


@pytest.mark.asyncio
async def test_stream_logs_materializes_auth_before_dispatch() -> None:
    """Same shape as list_pods but for the async log stream path."""
    captured: dict[str, Any] = {}

    class _LogBackend:
        async def stream(
            self,
            *,
            auth: ClusterAuth,
            namespace: str,
            pod_name: str,
            container: str | None,
            tail_lines: int,
            follow: bool,
        ):
            captured["auth"] = auth
            if False:  # pragma: no cover — making this an async generator
                yield None

    eks = _eks_client_mock()
    minter = _RecordingMinter()
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-east-1", cluster_name="prod-eks"),
        eks_client=eks,
        sts_client=MagicMock(),
        k8s_client_factory=lambda **_kw: MagicMock(),
        log_backend=_LogBackend(),
        token_minter=minter,
        monotonic_clock=_Clock(),
    )

    gen = driver.stream_logs(
        auth=_exec_plugin_auth(),
        namespace="ns",
        pod_name="p",
        container=None,
        tail_lines=10,
        follow=False,
    )
    async for _ in gen:  # pragma: no cover — empty stream
        pass

    auth = captured["auth"]
    assert auth.auth_method == "kubeconfig"
    assert "kubeconfig" in auth.auth_config
