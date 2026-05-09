"""Tests for GCP CostEstimator (#78 part of #69)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from _sdk.cost import CostEstimate, CostEstimateRequest, CostEstimateUnavailable
from gcp.cost import (
    CATALOG_BASE,
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


def test_unsupported_returns_unavailable(
    estimator: GCPCostEstimator,
) -> None:
    result = estimator.estimate(CostEstimateRequest(
        kind="postgres", variant="never",
        region="us-central1", size="small",
    ))
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "unsupported"


def test_api_error_returns_unavailable(
    estimator: GCPCostEstimator, fake_billing: FakeBillingClient,
) -> None:
    fake_billing.raise_on_call = RuntimeError("403 quota")
    result = estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="gcs",
        region="us-central1", size="custom",
    ))
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_no_skus_returns_unavailable(
    estimator: GCPCostEstimator, fake_billing: FakeBillingClient,
) -> None:
    fake_billing.skus = []
    result = estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="gcs",
        region="us-central1", size="custom",
    ))
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_gcs_estimate_uses_storage_gb_month(
    estimator: GCPCostEstimator, fake_billing: FakeBillingClient,
) -> None:
    # GCP returns 0.020 USD/GiB-month as units=0, nanos=20_000_000
    fake_billing.skus = [
        FakeSku(
            sku_id="SKU-001",
            description="Standard Storage US",
            service_regions=["us-central1", "us"],
            pricing_info=[FakePricingInfo(
                pricing_expression=FakeExpression(
                    usage_unit="gibibyte month",
                    tiered_rates=[FakeTier(
                        unit_price=FakeUnitPrice(
                            units=0, nanos=20_000_000,
                            currency_code="USD",
                        ),
                    )],
                ),
            )],
        ),
    ]
    result = estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="gcs",
        region="us-central1", size="custom",
        expected_usage={"storage_gb_month": 1000.0},
    ))
    assert isinstance(result, CostEstimate)
    assert result.line_items[0].sku == "SKU-001"
    # 0.02 * 1000 = 20.00
    assert result.monthly_total == 20.0


def test_region_filtering(
    estimator: GCPCostEstimator, fake_billing: FakeBillingClient,
) -> None:
    """SKUs scoped to a different region must be filtered out."""
    fake_billing.skus = [
        FakeSku(
            sku_id="SKU-EU",
            description="EU Storage",
            service_regions=["europe-west1"],
            pricing_info=[FakePricingInfo(
                pricing_expression=FakeExpression(
                    usage_unit="gibibyte month",
                    tiered_rates=[FakeTier(
                        unit_price=FakeUnitPrice(
                            nanos=20_000_000, currency_code="USD",
                        ),
                    )],
                ),
            )],
        ),
    ]
    result = estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="gcs",
        region="us-central1", size="custom",
    ))
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_provenance_url(
    estimator: GCPCostEstimator, fake_billing: FakeBillingClient,
) -> None:
    fake_billing.skus = [
        FakeSku(
            sku_id="X", description="d",
            service_regions=["us-central1"],
            pricing_info=[FakePricingInfo(
                pricing_expression=FakeExpression(
                    usage_unit="hour",
                    tiered_rates=[FakeTier(
                        unit_price=FakeUnitPrice(
                            units=1, currency_code="USD",
                        ),
                    )],
                ),
            )],
        ),
    ]
    result = estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="gcs",
        region="us-central1", size="custom",
        expected_usage={"hours_per_month": 720},
    ))
    assert isinstance(result, CostEstimate)
    assert CATALOG_BASE in result.pricing_source_url
    assert "95FF-2EF5-5EA1" in result.pricing_source_url  # GCS service id


def test_service_id_table_covers_core_kinds() -> None:
    assert ("object_store", "gcs") in SERVICE_ID_BY_VARIANT
    assert ("queue", "pubsub") in SERVICE_ID_BY_VARIANT
    assert ("postgres", "cloudsql") in SERVICE_ID_BY_VARIANT
