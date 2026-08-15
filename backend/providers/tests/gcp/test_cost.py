"""Tests for GCP CostEstimator (#78 part of #69)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.cost import CostEstimate, CostEstimateRequest, CostEstimateUnavailable
from gcp.cost import (
    CATALOG_BASE,
    SERVICE_DISPLAY_NAME_BY_VARIANT,
    SERVICE_ID_BY_VARIANT,
    GCPCostConfig,
    GCPCostEstimator,
)


@dataclass
class FakeUnitPrice:
    units: int = 0
    nanos: int = 0
    currency_code: str = "USD"


@dataclass
class FakeTier:
    unit_price: FakeUnitPrice


@dataclass
class FakeExpression:
    usage_unit: str
    tiered_rates: list[FakeTier] = field(default_factory=list)


@dataclass
class FakePricingInfo:
    pricing_expression: FakeExpression


@dataclass
class FakeSku:
    sku_id: str
    description: str
    service_regions: list[str]
    pricing_info: list[FakePricingInfo]
    name: str = "services/X/skus/Y"


class FakeBillingClient:
    def __init__(self) -> None:
        self.skus: list[FakeSku] = []
        self.raise_on_call: Exception | None = None
        self.services: list[Any] = []

    def list_services(self) -> list[Any]:
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.services

    def list_skus(self, *, parent: str) -> list[FakeSku]:
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.skus


@pytest.fixture
def fake_billing() -> FakeBillingClient:
    return FakeBillingClient()


@pytest.fixture
def estimator(fake_billing: FakeBillingClient) -> GCPCostEstimator:
    return GCPCostEstimator(
        config=GCPCostConfig(billing_client=fake_billing),
    )


def test_supported_known_variants(estimator: GCPCostEstimator) -> None:
    assert estimator.supported(kind="object_store", variant="gcs")
    assert estimator.supported(kind="queue", variant="pubsub")
    assert estimator.supported(kind="topic", variant="pubsub_topic")
    assert estimator.supported(kind="warehouse", variant="bigquery")
    assert estimator.supported(kind="postgres", variant="alloydb")
    assert estimator.supported(kind="document_db", variant="firestore_native")
    assert estimator.supported(kind="event_stream", variant="managed_kafka")


def test_unsupported_returns_unavailable(
    estimator: GCPCostEstimator,
) -> None:
    result = estimator.estimate(
        CostEstimateRequest(
            kind="postgres",
            variant="never",
            region="us-central1",
            size="small",
        )
    )
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "unsupported"


def test_api_error_returns_unavailable(
    estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    fake_billing.raise_on_call = RuntimeError("403 quota")
    result = estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="gcs",
            region="us-central1",
            size="custom",
        )
    )
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_no_skus_returns_unavailable(
    estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    fake_billing.skus = []
    result = estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="gcs",
            region="us-central1",
            size="custom",
        )
    )
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_gcs_estimate_uses_storage_gb_month(
    estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    # GCP returns 0.020 USD/GiB-month as units=0, nanos=20_000_000
    fake_billing.skus = [
        FakeSku(
            sku_id="SKU-001",
            description="Standard Storage US",
            service_regions=["us-central1", "us"],
            pricing_info=[
                FakePricingInfo(
                    pricing_expression=FakeExpression(
                        usage_unit="gibibyte month",
                        tiered_rates=[
                            FakeTier(
                                unit_price=FakeUnitPrice(
                                    units=0,
                                    nanos=20_000_000,
                                    currency_code="USD",
                                ),
                            )
                        ],
                    ),
                )
            ],
        ),
    ]
    result = estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="gcs",
            region="us-central1",
            size="custom",
            expected_usage={"storage_gb_month": 1000.0},
        )
    )
    assert isinstance(result, CostEstimate)
    assert result.line_items[0].sku == "SKU-001"
    # 0.02 * 1000 = 20.00
    assert result.monthly_total == 20.0


def test_region_filtering(
    estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    """SKUs scoped to a different region must be filtered out."""
    fake_billing.skus = [
        FakeSku(
            sku_id="SKU-EU",
            description="EU Storage",
            service_regions=["europe-west1"],
            pricing_info=[
                FakePricingInfo(
                    pricing_expression=FakeExpression(
                        usage_unit="gibibyte month",
                        tiered_rates=[
                            FakeTier(
                                unit_price=FakeUnitPrice(
                                    nanos=20_000_000,
                                    currency_code="USD",
                                ),
                            )
                        ],
                    ),
                )
            ],
        ),
    ]
    result = estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="gcs",
            region="us-central1",
            size="custom",
        )
    )
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_provenance_url(
    estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    fake_billing.skus = [
        FakeSku(
            sku_id="X",
            description="d",
            service_regions=["us-central1"],
            pricing_info=[
                FakePricingInfo(
                    pricing_expression=FakeExpression(
                        usage_unit="hour",
                        tiered_rates=[
                            FakeTier(
                                unit_price=FakeUnitPrice(
                                    units=1,
                                    currency_code="USD",
                                ),
                            )
                        ],
                    ),
                )
            ],
        ),
    ]
    result = estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="gcs",
            region="us-central1",
            size="custom",
            expected_usage={"hours_per_month": 720},
        )
    )
    assert isinstance(result, CostEstimate)
    assert CATALOG_BASE in result.pricing_source_url
    assert "95FF-2EF5-5EA1" in result.pricing_source_url  # GCS service id


def test_service_id_table_covers_core_kinds() -> None:
    assert ("object_store", "gcs") in SERVICE_ID_BY_VARIANT
    assert ("queue", "pubsub") in SERVICE_ID_BY_VARIANT
    assert ("topic", "pubsub_topic") in SERVICE_ID_BY_VARIANT
    assert ("warehouse", "bigquery") in SERVICE_ID_BY_VARIANT
    assert ("document_db", "firestore_native") in SERVICE_ID_BY_VARIANT
    assert ("event_bus", "eventarc") in SERVICE_ID_BY_VARIANT
    assert ("postgres", "cloudsql") in SERVICE_ID_BY_VARIANT
    assert ("filesystem", "filestore") in SERVICE_ID_BY_VARIANT
    assert SERVICE_DISPLAY_NAME_BY_VARIANT[("event_stream", "managed_kafka")] == ("Managed Service for Apache Kafka")


def test_managed_kafka_resolves_current_billing_service_id(
    estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    fake_billing.services = [
        type(
            "Service",
            (),
            {
                "name": "services/CURRENT-KAFKA-ID",
                "display_name": "Managed Service for Apache Kafka",
            },
        )(),
    ]
    fake_billing.skus = [
        FakeSku(
            sku_id="KAFKA-DCU",
            description="Managed Service for Apache Kafka Data Compute Units",
            service_regions=["us-central1"],
            pricing_info=[
                FakePricingInfo(
                    pricing_expression=FakeExpression(
                        usage_unit="hour",
                        tiered_rates=[
                            FakeTier(
                                unit_price=FakeUnitPrice(
                                    nanos=90_000_000,
                                    currency_code="USD",
                                ),
                            ),
                        ],
                    ),
                ),
            ],
        ),
    ]
    result = estimator.estimate(
        CostEstimateRequest(
            kind="event_stream",
            variant="managed_kafka",
            region="us-central1",
            size="custom",
            expected_usage={"hours_per_month": 1},
        ),
    )
    assert isinstance(result, CostEstimate)
    assert "CURRENT-KAFKA-ID" in result.pricing_source_url


# ---- compute / node_hour (#440) -----------------------------------


@dataclass
class FakeCategory:
    resource_group: str = ""


@dataclass
class FakeComputeSku:
    sku_id: str
    description: str
    service_regions: list[str]
    pricing_info: list[FakePricingInfo]
    category: FakeCategory | None = None
    name: str = "services/6F81-5844-456A/skus/Y"


def _core_sku(
    *,
    sku_id: str,
    description: str,
    region: str,
    units: int = 0,
    nanos: int = 0,
) -> FakeComputeSku:
    return FakeComputeSku(
        sku_id=sku_id,
        description=description,
        service_regions=[region],
        pricing_info=[
            FakePricingInfo(
                pricing_expression=FakeExpression(
                    usage_unit="h",
                    tiered_rates=[
                        FakeTier(
                            unit_price=FakeUnitPrice(
                                units=units,
                                nanos=nanos,
                                currency_code="USD",
                            ),
                        ),
                    ],
                ),
            ),
        ],
        category=FakeCategory(resource_group="CPU"),
    )


def _ram_sku(
    *,
    sku_id: str,
    description: str,
    region: str,
    units: int = 0,
    nanos: int = 0,
) -> FakeComputeSku:
    return FakeComputeSku(
        sku_id=sku_id,
        description=description,
        service_regions=[region],
        pricing_info=[
            FakePricingInfo(
                pricing_expression=FakeExpression(
                    usage_unit="GiBy.h",
                    tiered_rates=[
                        FakeTier(
                            unit_price=FakeUnitPrice(
                                units=units,
                                nanos=nanos,
                                currency_code="USD",
                            ),
                        ),
                    ],
                ),
            ),
        ],
        category=FakeCategory(resource_group="RAM"),
    )


@pytest.fixture
def compute_estimator(fake_billing: FakeBillingClient) -> GCPCostEstimator:
    return GCPCostEstimator(
        config=GCPCostConfig(
            billing_client=fake_billing,
            cache_ttl_seconds=0,
        ),
    )


def test_compute_node_hour_is_supported(
    compute_estimator: GCPCostEstimator,
) -> None:
    assert compute_estimator.supported(
        kind="compute",
        variant="node_hour",
    )


def test_compute_sums_core_and_ram_skus(
    compute_estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    # N2 Instance Core @ $0.031611/hr/vCPU, N2 Instance Ram @
    # $0.004237/hr/GiB (rough live numbers).
    fake_billing.skus = [
        _core_sku(
            sku_id="N2-CORE-USC1",
            description="N2 Instance Core running in Americas",
            region="us-central1",
            nanos=31_611_000,
        ),
        _ram_sku(
            sku_id="N2-RAM-USC1",
            description="N2 Instance Ram running in Americas",
            region="us-central1",
            nanos=4_237_000,
        ),
    ]
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-central1",
        size="custom",
        expected_usage={
            "cpu_cores": 4.0,
            "memory_gib": 16.0,
            "hours_per_month": 720.0,
        },
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    # 0.031611 * 4 * 720 = 91.038 + 0.004237 * 16 * 720 = 48.81
    expected = (0.031611 * 4 * 720.0) + (0.004237 * 16 * 720.0)
    assert result.monthly_total == pytest.approx(round(expected, 2), rel=1e-3)
    assert len(result.line_items) == 2
    assert {li.sku for li in result.line_items} == {
        "N2-CORE-USC1",
        "N2-RAM-USC1",
    }


def test_compute_picks_cheapest_in_family(
    compute_estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    # Two N2 core SKUs in the same region — the cheaper one wins.
    fake_billing.skus = [
        _core_sku(
            sku_id="N2-CORE-EXPENSIVE",
            description="N2 Instance Core running in Americas",
            region="us-central1",
            nanos=40_000_000,
        ),
        _core_sku(
            sku_id="N2-CORE-CHEAP",
            description="N2 Instance Core running in Americas",
            region="us-central1",
            nanos=25_000_000,
        ),
        _ram_sku(
            sku_id="N2-RAM-USC1",
            description="N2 Instance Ram running in Americas",
            region="us-central1",
            nanos=4_237_000,
        ),
    ]
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-central1",
        size="custom",
        expected_usage={
            "cpu_cores": 2.0,
            "memory_gib": 8.0,
            "hours_per_month": 720.0,
        },
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    cpu_line = next(li for li in result.line_items if li.sku.endswith("CHEAP"))
    assert cpu_line.sku == "N2-CORE-CHEAP"


def test_compute_skips_preemptible_and_commitment_skus(
    compute_estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    # Preemptible and committed-use SKUs are cheaper but we must
    # ignore them — they don't reflect a fresh node-pool's bill.
    fake_billing.skus = [
        _core_sku(
            sku_id="N2-CORE-PREEMPT",
            description="Preemptible N2 Instance Core in Americas",
            region="us-central1",
            nanos=10_000_000,
        ),
        _core_sku(
            sku_id="N2-CORE-CUD",
            description="Commitment v1: N2 Cpu in Americas",
            region="us-central1",
            nanos=15_000_000,
        ),
        _core_sku(
            sku_id="N2-CORE-ONDEMAND",
            description="N2 Instance Core running in Americas",
            region="us-central1",
            nanos=31_611_000,
        ),
        _ram_sku(
            sku_id="N2-RAM-USC1",
            description="N2 Instance Ram running in Americas",
            region="us-central1",
            nanos=4_237_000,
        ),
    ]
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-central1",
        size="custom",
        expected_usage={
            "cpu_cores": 2.0,
            "memory_gib": 8.0,
            "hours_per_month": 720.0,
        },
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    core_line = next(li for li in result.line_items if "Core" in li.label)
    assert core_line.sku == "N2-CORE-ONDEMAND"


def test_compute_missing_family_returns_unavailable(
    compute_estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    # Only RAM SKUs available — no CPU match.
    fake_billing.skus = [
        _ram_sku(
            sku_id="N2-RAM-USC1",
            description="N2 Instance Ram running in Americas",
            region="us-central1",
            nanos=4_237_000,
        ),
    ]
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-central1",
        size="custom",
        expected_usage={
            "cpu_cores": 2.0,
            "memory_gib": 8.0,
            "hours_per_month": 720.0,
        },
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_compute_region_scope_excludes_other_regions(
    compute_estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    fake_billing.skus = [
        _core_sku(
            sku_id="N2-CORE-EU",
            description="N2 Instance Core running in EMEA",
            region="europe-west1",
            nanos=31_611_000,
        ),
        _ram_sku(
            sku_id="N2-RAM-EU",
            description="N2 Instance Ram running in EMEA",
            region="europe-west1",
            nanos=4_237_000,
        ),
    ]
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-central1",
        size="custom",
        expected_usage={
            "cpu_cores": 2.0,
            "memory_gib": 8.0,
            "hours_per_month": 720.0,
        },
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_compute_api_error_returns_unavailable(
    compute_estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    fake_billing.raise_on_call = RuntimeError("403 quota")
    result = compute_estimator.estimate(
        CostEstimateRequest(
            kind="compute",
            variant="node_hour",
            region="us-central1",
            size="custom",
            expected_usage={"cpu_cores": 2.0, "memory_gib": 8.0},
        )
    )
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_compute_empty_usage_returns_unavailable(
    compute_estimator: GCPCostEstimator,
) -> None:
    result = compute_estimator.estimate(
        CostEstimateRequest(
            kind="compute",
            variant="node_hour",
            region="us-central1",
            size="custom",
            expected_usage={},
        )
    )
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_compute_provenance_url(
    compute_estimator: GCPCostEstimator,
    fake_billing: FakeBillingClient,
) -> None:
    fake_billing.skus = [
        _core_sku(
            sku_id="N2-CORE",
            description="N2 Instance Core in Americas",
            region="us-central1",
            nanos=31_611_000,
        ),
        _ram_sku(
            sku_id="N2-RAM",
            description="N2 Instance Ram in Americas",
            region="us-central1",
            nanos=4_237_000,
        ),
    ]
    result = compute_estimator.estimate(
        CostEstimateRequest(
            kind="compute",
            variant="node_hour",
            region="us-central1",
            size="custom",
            expected_usage={"cpu_cores": 2.0, "memory_gib": 8.0},
        )
    )
    assert isinstance(result, CostEstimate)
    assert CATALOG_BASE in result.pricing_source_url
    assert "6F81-5844-456A" in result.pricing_source_url
    assert "us-central1" in result.pricing_source_url
    assert result.pricing_fetched_at


def test_compute_cache_reuses_sweep(
    fake_billing: FakeBillingClient,
) -> None:
    cached = GCPCostEstimator(
        config=GCPCostConfig(billing_client=fake_billing),
    )
    fake_billing.skus = [
        _core_sku(
            sku_id="N2-CORE",
            description="N2 Instance Core in Americas",
            region="us-central1",
            nanos=31_611_000,
        ),
        _ram_sku(
            sku_id="N2-RAM",
            description="N2 Instance Ram in Americas",
            region="us-central1",
            nanos=4_237_000,
        ),
    ]
    call_count_before = 0

    original = fake_billing.list_skus

    def counting_list_skus(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count_before
        call_count_before += 1
        return original(*args, **kwargs)

    fake_billing.list_skus = counting_list_skus  # type: ignore[assignment]
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-central1",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 8.0},
    )
    cached.estimate(request)
    cached.estimate(request)
    assert call_count_before == 1
