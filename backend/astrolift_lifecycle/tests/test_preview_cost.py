"""Tests for the preview cost / resource aggregator (#431).

Covers the pure helpers (parse / format / aggregate) and the
``estimate_daily_cost`` integration with a fake driver — including
the absolute prohibition on fabricating a cost when the driver can't
return a live number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.preview_cost import (
    PreviewAggregateResources,
    aggregate_pod_resources,
    estimate_daily_cost,
    format_cpu_cores,
    format_memory_bytes,
    parse_cpu_cores,
    parse_memory_bytes,
)

# ---- unit parsing ---------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", 0.0),
        ("100m", 0.1),
        ("500m", 0.5),
        ("1", 1.0),
        ("2.5", 2.5),
        (" 250m ", 0.25),
    ],
)
def test_parse_cpu_cores(raw, expected):
    assert parse_cpu_cores(raw) == pytest.approx(expected)


def test_parse_cpu_cores_rejects_garbage():
    with pytest.raises(ValueError):
        parse_cpu_cores("nope")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", 0.0),
        ("512Mi", 512 * 1024 * 1024),
        ("1Gi", 1024**3),
        ("100M", 100 * 1000 * 1000),
        ("2K", 2000),
    ],
)
def test_parse_memory_bytes(raw, expected):
    assert parse_memory_bytes(raw) == pytest.approx(expected)


def test_parse_memory_bytes_rejects_garbage():
    with pytest.raises(ValueError):
        parse_memory_bytes("nope")


# ---- formatting -----------------------------------------------------


@pytest.mark.parametrize(
    "cores,expected",
    [
        (0.0, "0 CPU"),
        (0.1, "100m CPU"),
        (0.25, "250m CPU"),
        (1.0, "1 CPU"),
        (1.5, "1.5 CPU"),
        (2.0, "2 CPU"),
    ],
)
def test_format_cpu_cores(cores, expected):
    assert format_cpu_cores(cores) == expected


@pytest.mark.parametrize(
    "byte_count,expected",
    [
        (0, "0 Mi"),
        (1024, "1.0 Ki"),
        (512 * 1024**2, "512 Mi"),
        (int(1.5 * 1024**3), "1.5 Gi"),
    ],
)
def test_format_memory_bytes(byte_count, expected):
    assert format_memory_bytes(byte_count) == expected


# ---- aggregation ----------------------------------------------------


def _fake_pod(*, phase: str = "Running", containers=None):
    containers = containers or []
    statuses = [
        SimpleNamespace(
            name=c.get("name", "main"),
            resources=SimpleNamespace(
                cpu_request=c.get("cpu_request", ""),
                cpu_limit=c.get("cpu_limit", ""),
                memory_request=c.get("memory_request", ""),
                memory_limit=c.get("memory_limit", ""),
            ),
        )
        for c in containers
    ]
    return SimpleNamespace(
        name=f"pod-{id(containers)}",
        phase=phase,
        container_statuses=statuses,
    )


def test_aggregate_sums_across_pods_and_containers():
    pods = [
        _fake_pod(
            containers=[
                {"cpu_request": "100m", "memory_request": "256Mi"},
                {"cpu_request": "50m", "memory_request": "128Mi"},
            ],
        ),
        _fake_pod(
            containers=[
                {"cpu_request": "200m", "memory_request": "512Mi"},
            ],
        ),
    ]
    agg = aggregate_pod_resources(pods)
    assert agg.pod_count == 2
    assert agg.cpu_cores == pytest.approx(0.35)
    assert agg.memory_bytes == pytest.approx((256 + 128 + 512) * 1024**2)


def test_aggregate_skips_terminal_pods():
    pods = [
        _fake_pod(containers=[{"cpu_request": "100m", "memory_request": "256Mi"}]),
        _fake_pod(
            phase="Succeeded",
            containers=[{"cpu_request": "999", "memory_request": "999Gi"}],
        ),
        _fake_pod(
            phase="Failed",
            containers=[{"cpu_request": "999", "memory_request": "999Gi"}],
        ),
    ]
    agg = aggregate_pod_resources(pods)
    assert agg.pod_count == 1
    assert agg.cpu_cores == pytest.approx(0.1)


def test_aggregate_tolerates_bad_resource_strings():
    """One broken pod shouldn't wipe the rest of the aggregate."""
    pods = [
        _fake_pod(containers=[{"cpu_request": "100m", "memory_request": "256Mi"}]),
        _fake_pod(containers=[{"cpu_request": "not-a-number", "memory_request": "256Mi"}]),
    ]
    agg = aggregate_pod_resources(pods)
    # Both pods count toward pod_count (they're both running) — only
    # the parseable resource numbers contribute to the sum.
    assert agg.pod_count == 2
    assert agg.cpu_cores == pytest.approx(0.1)


def test_aggregate_handles_no_pods():
    agg = aggregate_pod_resources([])
    assert agg == PreviewAggregateResources(cpu_cores=0.0, memory_bytes=0.0, pod_count=0)


# ---- cost integration ------------------------------------------------


@dataclass
class _FakeCostDriver:
    """Stand-in for the cluster's provider plugin cost capability.

    Behaviour:
      - ``mode="estimate"`` — return a CostEstimate matching the
        SDK shape so the aggregator picks it up.
      - ``mode="unavailable"`` — return CostEstimateUnavailable to
        prove the aggregator surfaces ``None``.
      - ``mode="raise"`` — raise to prove a transient API failure
        also surfaces as ``None``, not a fabricated price.
    """

    mode: str = "estimate"
    monthly_total: float = 30.0
    notes: list = field(default_factory=list)
    approximate: bool = False
    requests: list = field(default_factory=list)

    def estimate(self, request):
        self.requests.append(request)
        from _sdk.cost import CostEstimate, CostEstimateUnavailable, CostLineItem

        if self.mode == "unavailable":
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message="no SKU",
            )
        if self.mode == "raise":
            raise RuntimeError("pricing API offline")
        if self.mode == "not_implemented":
            raise NotImplementedError("driver does not implement compute pricing")
        return CostEstimate(
            request=request,
            line_items=[
                CostLineItem(label="compute", sku="t3.micro", monthly_amount=self.monthly_total),
            ],
            monthly_total=self.monthly_total,
            pricing_source_url="https://example.invalid/pricing",
            pricing_fetched_at="2026-05-16T00:00:00Z",
            notes=list(self.notes),
            approximate=self.approximate,
        )


@pytest.fixture
def patch_driver(monkeypatch):
    """Patch the cost-estimator resolver to return ``driver``.

    The aggregator resolves an estimator via
    :func:`astrolift_lifecycle.preview_cost.cost_estimator_for_cluster`
    (which instantiates the right `*CostEstimator` per plugin slug).
    Tests bypass the per-plugin lookup so they don't need to model
    boto3 / google-cloud / azure SDK shapes."""

    def _install(driver):
        from astrolift_lifecycle import preview_cost

        def _fake(_cluster):
            return driver

        monkeypatch.setattr(preview_cost, "cost_estimator_for_cluster", _fake)

    return _install


def test_estimate_daily_cost_returns_rounded_daily(patch_driver):
    """A driver that returns $30/month should resolve to $1/day."""
    driver = _FakeCostDriver(mode="estimate", monthly_total=30.0)
    patch_driver(driver)
    agg = PreviewAggregateResources(cpu_cores=0.5, memory_bytes=1024**3, pod_count=2)
    daily = estimate_daily_cost(
        cluster=SimpleNamespace(region="us-east-1"),
        aggregate=agg,
    )
    assert daily is not None
    assert daily.daily_usd == pytest.approx(1.0)


def test_the_drivers_caveats_travel_with_the_number(patch_driver):
    """#1509: every note a driver attached used to be dropped here.

    The list-price caveat is on all three clouds, so an operator reading
    any figure at all was reading one without it.
    """
    driver = _FakeCostDriver(
        mode="estimate",
        monthly_total=30.0,
        notes=[
            "AWS Pricing API returns on-demand list price; savings plans are not applied.",
            "",
        ],
    )
    patch_driver(driver)
    agg = PreviewAggregateResources(cpu_cores=0.5, memory_bytes=1024**3, pod_count=2)

    estimate = estimate_daily_cost(cluster=SimpleNamespace(region="us-east-1"), aggregate=agg)

    assert estimate is not None
    assert estimate.notes == ("AWS Pricing API returns on-demand list price; savings plans are not applied.",)
    assert estimate.approximate is False


def test_an_approximate_total_says_so(patch_driver):
    """The GCP estimator sums every region-matching SKU in a service for
    the fourteen variants with no SKU plan, which over-counts by
    construction. It labels the result; that label reached nobody."""
    driver = _FakeCostDriver(
        mode="estimate",
        monthly_total=30.0,
        notes=["APPROXIMATE: sums all 41 region-matching SKUs in service ABCD."],
        approximate=True,
    )
    patch_driver(driver)
    agg = PreviewAggregateResources(cpu_cores=0.5, memory_bytes=1024**3, pod_count=2)

    estimate = estimate_daily_cost(cluster=SimpleNamespace(region="us-east-1"), aggregate=agg)

    assert estimate is not None
    assert estimate.approximate is True
    assert estimate.notes[0].startswith("APPROXIMATE:")
    # The flag is structural, not sniffed out of the prose: a surface
    # branching on the string would break the first time the wording
    # changed.
    assert estimate.daily_usd == pytest.approx(1.0)


def test_estimate_daily_cost_returns_none_when_driver_unavailable(patch_driver):
    driver = _FakeCostDriver(mode="unavailable")
    patch_driver(driver)
    agg = PreviewAggregateResources(cpu_cores=0.5, memory_bytes=1024**3, pod_count=2)
    daily = estimate_daily_cost(
        cluster=SimpleNamespace(region="us-east-1"),
        aggregate=agg,
    )
    assert daily is None


def test_estimate_daily_cost_returns_none_on_api_error(patch_driver):
    """Pricing API failures MUST surface as None, never as a fallback
    number. This is the hard workspace rule."""
    driver = _FakeCostDriver(mode="raise")
    patch_driver(driver)
    agg = PreviewAggregateResources(cpu_cores=0.5, memory_bytes=1024**3, pod_count=2)
    daily = estimate_daily_cost(
        cluster=SimpleNamespace(region="us-east-1"),
        aggregate=agg,
    )
    assert daily is None


def test_estimate_daily_cost_returns_none_on_not_implemented(patch_driver):
    driver = _FakeCostDriver(mode="not_implemented")
    patch_driver(driver)
    agg = PreviewAggregateResources(cpu_cores=0.5, memory_bytes=1024**3, pod_count=2)
    daily = estimate_daily_cost(
        cluster=SimpleNamespace(region="us-east-1"),
        aggregate=agg,
    )
    assert daily is None


def test_estimate_daily_cost_short_circuits_on_zero_usage():
    """No pods → no point asking the pricing API. The driver should
    not be invoked at all."""
    agg = PreviewAggregateResources(cpu_cores=0.0, memory_bytes=0.0, pod_count=0)
    daily = estimate_daily_cost(
        cluster=SimpleNamespace(region="us-east-1"),
        aggregate=agg,
    )
    assert daily is None


# ---- cost_estimator_for_cluster resolver (#440) ----------------------


def _fake_cluster(slug: str, *, provider_config: dict | None = None):
    """Build a cluster-like object with the attributes the resolver
    reads. The provider plugin attribute is a SimpleNamespace carrying
    a ``slug`` field, matching the TenantCluster.provider_plugin FK
    relationship shape the production code walks."""
    return SimpleNamespace(
        slug="some-cluster",
        region="us-east-1",
        provider_plugin=SimpleNamespace(slug=slug),
        provider_config=provider_config or {},
    )


def test_cost_estimator_for_cluster_returns_aws_estimator():
    """AWS path is wired through boto3, which ships in the backend
    image; the resolver returns an AWSCostEstimator instance."""
    from aws.cost import AWSCostEstimator

    from astrolift_lifecycle.preview_cost import cost_estimator_for_cluster

    driver = cost_estimator_for_cluster(_fake_cluster("aws"))
    assert isinstance(driver, AWSCostEstimator)


def test_cost_estimator_for_cluster_handles_gcp():
    """GCP path either returns a GCPCostEstimator (when
    `google-cloud-billing` ships in the image) or None (when the
    SDK isn't installed and the lazy `billing_v1` import fails
    inside ``GCPCostEstimator.__init__``). Either is correct;
    what's wrong is raising. The resolver guards the construction
    with try/except so the preview request always succeeds."""
    from gcp.cost import GCPCostEstimator

    from astrolift_lifecycle.preview_cost import cost_estimator_for_cluster

    driver = cost_estimator_for_cluster(_fake_cluster("gcp"))
    # Must not raise; either a real estimator or None.
    assert driver is None or isinstance(driver, GCPCostEstimator)


def test_cost_estimator_for_cluster_handles_azure():
    """Azure path returns an AzureCostEstimator without touching the
    SDK at construction — the default HTTP client + VM-size lookup
    are both lazy."""
    from azure.cost import AzureCostEstimator

    from astrolift_lifecycle.preview_cost import cost_estimator_for_cluster

    driver = cost_estimator_for_cluster(_fake_cluster("azure"))
    assert isinstance(driver, AzureCostEstimator)


def test_cost_estimator_for_cluster_returns_none_for_k8s_native():
    """Bare-metal clusters have no cloud-side SKU to ask."""
    from astrolift_lifecycle.preview_cost import cost_estimator_for_cluster

    assert cost_estimator_for_cluster(_fake_cluster("k8s_native")) is None


def test_cost_estimator_for_cluster_returns_none_for_unknown_plugin():
    from astrolift_lifecycle.preview_cost import cost_estimator_for_cluster

    assert cost_estimator_for_cluster(_fake_cluster("oracle")) is None


def test_cost_estimator_for_cluster_returns_none_when_no_plugin():
    from astrolift_lifecycle.preview_cost import cost_estimator_for_cluster

    cluster = SimpleNamespace(provider_plugin=None, provider_config={})
    assert cost_estimator_for_cluster(cluster) is None


def test_estimate_daily_cost_threads_subscription_id_for_azure(patch_driver):
    """Azure compute pricing needs the cluster's subscription_id so
    the default VM-size lookup can talk to ARM. The aggregator must
    pass it through via ``CostEstimateRequest.config``."""
    driver = _FakeCostDriver(mode="estimate", monthly_total=30.0)
    patch_driver(driver)
    cluster = SimpleNamespace(
        region="eastus",
        provider_plugin=SimpleNamespace(slug="azure"),
        provider_config={"subscription_id": "abc-123"},
    )
    agg = PreviewAggregateResources(
        cpu_cores=0.5,
        memory_bytes=1024**3,
        pod_count=2,
    )
    estimate_daily_cost(cluster=cluster, aggregate=agg)
    assert driver.requests[0].config["subscription_id"] == "abc-123"
