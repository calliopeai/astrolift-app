"""Tests for AWS CostEstimator (#78 part of #68)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from _sdk.cost import CostEstimate, CostEstimateRequest, CostEstimateUnavailable
from aws.cost import (
    PRICING_ENDPOINT,
    SERVICE_CODE_BY_VARIANT,
    AWSCostConfig,
    AWSCostEstimator,
)


def _s3_pricelist_record() -> str:
    return json.dumps(
        {
            "product": {
                "sku": "ABCDEF1234",
                "attributes": {
                    "usagetype": "USE1-TimedStorage-ByteHrs",
                    "location": "US East (N. Virginia)",
                    "storageClass": "General Purpose",
                },
            },
            "terms": {
                "OnDemand": {
                    "term1": {
                        "priceDimensions": {
                            "rate1": {
                                "unit": "GB-Mo",
                                "description": "0.023 per GB - first 50 TB",
                                "pricePerUnit": {"USD": "0.0230000000"},
                            },
                        },
                    },
                },
            },
        }
    )


@pytest.fixture
def fake_pricing() -> MagicMock:
    return MagicMock()


@pytest.fixture
def estimator(fake_pricing: MagicMock) -> AWSCostEstimator:
    return AWSCostEstimator(
        config=AWSCostConfig(pricing_client=fake_pricing),
    )


def test_supported_recognizes_known_variants(
    estimator: AWSCostEstimator,
) -> None:
    assert estimator.supported(kind="object_store", variant="s3")
    assert estimator.supported(kind="postgres", variant="rds")
    assert not estimator.supported(kind="object_store", variant="never")


def test_unsupported_variant_returns_unavailable(
    estimator: AWSCostEstimator,
) -> None:
    request = CostEstimateRequest(
        kind="postgres",
        variant="never_added",
        region="us-east-1",
        size="small",
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "unsupported"


def test_api_error_returns_unavailable(
    estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.side_effect = RuntimeError(
        "AWS API throttled",
    )
    request = CostEstimateRequest(
        kind="object_store",
        variant="s3",
        region="us-east-1",
        size="custom",
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_no_skus_returns_unavailable(
    estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {"PriceList": []}
    request = CostEstimateRequest(
        kind="object_store",
        variant="s3",
        region="us-east-1",
        size="custom",
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_s3_estimate_uses_storage_gb_month(
    estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {
        "PriceList": [_s3_pricelist_record()],
    }
    request = CostEstimateRequest(
        kind="object_store",
        variant="s3",
        region="us-east-1",
        size="custom",
        expected_usage={"storage_gb_month": 1000.0},
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    # 0.023 * 1000 = 23.00
    assert result.monthly_total == 23.0
    assert result.line_items[0].sku == "ABCDEF1234"


def test_estimate_carries_pricing_provenance(
    estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {
        "PriceList": [_s3_pricelist_record()],
    }
    request = CostEstimateRequest(
        kind="object_store",
        variant="s3",
        region="us-east-1",
        size="custom",
        expected_usage={"storage_gb_month": 100.0},
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    assert PRICING_ENDPOINT in result.pricing_source_url
    assert "AmazonS3" in result.pricing_source_url
    assert result.pricing_fetched_at  # set to current ISO time


def test_filters_include_region_location(
    estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {"PriceList": []}
    request = CostEstimateRequest(
        kind="object_store",
        variant="s3",
        region="us-east-1",
        size="custom",
    )
    estimator.estimate(request)
    _args, kwargs = fake_pricing.get_products.call_args
    filters = kwargs["Filters"]
    location_filter = next(f for f in filters if f["Field"] == "location")
    assert location_filter["Value"] == "US East (N. Virginia)"


def test_service_code_table_covers_core_kinds() -> None:
    """Core data kinds must have service-code mappings."""
    assert ("object_store", "s3") in SERVICE_CODE_BY_VARIANT
    assert ("queue", "sqs") in SERVICE_CODE_BY_VARIANT
    assert ("postgres", "rds") in SERVICE_CODE_BY_VARIANT


# ---- compute / node_hour (#440) -----------------------------------


def _ec2_product(
    *,
    instance_type: str,
    vcpu: int,
    memory_gib: float,
    hourly_usd: float,
    sku: str | None = None,
) -> str:
    sku_id = sku or f"SKU-{instance_type.upper()}"
    return json.dumps(
        {
            "product": {
                "sku": sku_id,
                "attributes": {
                    "instanceType": instance_type,
                    "vcpu": str(vcpu),
                    "memory": f"{memory_gib} GiB",
                    "operatingSystem": "Linux",
                    "tenancy": "Shared",
                    "preInstalledSw": "NA",
                    "capacitystatus": "Used",
                    "location": "US West (Oregon)",
                },
            },
            "terms": {
                "OnDemand": {
                    "term1": {
                        "priceDimensions": {
                            "rate1": {
                                "unit": "Hrs",
                                "description": f"{instance_type} on-demand",
                                "pricePerUnit": {"USD": f"{hourly_usd:.10f}"},
                            },
                        },
                    },
                },
            },
        }
    )


@pytest.fixture
def compute_estimator(fake_pricing: MagicMock) -> AWSCostEstimator:
    # Cache disabled so each test owns its fake response.
    return AWSCostEstimator(
        config=AWSCostConfig(
            pricing_client=fake_pricing,
            cache_ttl_seconds=0,
        ),
    )


def test_compute_node_hour_is_supported(
    compute_estimator: AWSCostEstimator,
) -> None:
    assert compute_estimator.supported(
        kind="compute",
        variant="node_hour",
    )


def test_compute_picks_cheapest_fitting_sku(
    compute_estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    # Three candidates; the cheapest that fits 2 vCPU + 4 GiB is the
    # m5.large at $0.096/hr. The c5.large is cheaper but only has 4 GiB
    # of memory which still fits — so c5.large should win.
    fake_pricing.get_products.return_value = {
        "PriceList": [
            _ec2_product(
                instance_type="t3.medium",
                vcpu=2,
                memory_gib=4.0,
                hourly_usd=0.0416,
            ),
            _ec2_product(
                instance_type="m5.large",
                vcpu=2,
                memory_gib=8.0,
                hourly_usd=0.096,
            ),
            _ec2_product(
                instance_type="c5.large",
                vcpu=2,
                memory_gib=4.0,
                hourly_usd=0.085,
            ),
        ],
        "NextToken": None,
    }
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-west-2",
        size="custom",
        expected_usage={
            "cpu_cores": 2.0,
            "memory_gib": 4.0,
            "hours_per_month": 720.0,
        },
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    # t3.medium fits, cheapest at $0.0416/hr * 720h = $29.952
    assert result.line_items[0].sku == "SKU-T3.MEDIUM"
    assert result.monthly_total == pytest.approx(29.95, rel=1e-3)


def test_compute_skips_skus_that_dont_fit(
    compute_estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    # Caller asks for 8 vCPU; the cheap t3.medium (2 vCPU) must be
    # rejected even though it's the cheapest in absolute terms.
    fake_pricing.get_products.return_value = {
        "PriceList": [
            _ec2_product(
                instance_type="t3.medium",
                vcpu=2,
                memory_gib=4.0,
                hourly_usd=0.0416,
            ),
            _ec2_product(
                instance_type="m5.2xlarge",
                vcpu=8,
                memory_gib=32.0,
                hourly_usd=0.384,
            ),
        ],
        "NextToken": None,
    }
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-west-2",
        size="custom",
        expected_usage={
            "cpu_cores": 8.0,
            "memory_gib": 16.0,
            "hours_per_month": 720.0,
        },
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    assert "m5.2xlarge" in result.line_items[0].label


def test_compute_no_fitting_sku_returns_unavailable(
    compute_estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {
        "PriceList": [
            _ec2_product(
                instance_type="t3.nano",
                vcpu=2,
                memory_gib=0.5,
                hourly_usd=0.0052,
            ),
        ],
        "NextToken": None,
    }
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-west-2",
        size="custom",
        expected_usage={
            "cpu_cores": 16.0,
            "memory_gib": 64.0,
            "hours_per_month": 720.0,
        },
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_compute_unmapped_region_returns_unavailable(
    compute_estimator: AWSCostEstimator,
) -> None:
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="mars-north-1",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"
    assert "mars-north-1" in result.message


def test_compute_api_error_returns_unavailable(
    compute_estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.side_effect = RuntimeError("throttled")
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-west-2",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_compute_empty_usage_returns_unavailable(
    compute_estimator: AWSCostEstimator,
) -> None:
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-west-2",
        size="custom",
        expected_usage={},
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"
    assert "cpu_cores" in result.message


def test_compute_filters_pin_linux_shared_tenancy(
    compute_estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {
        "PriceList": [],
        "NextToken": None,
    }
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-west-2",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    compute_estimator.estimate(request)
    _, kwargs = fake_pricing.get_products.call_args
    fields = {f["Field"]: f["Value"] for f in kwargs["Filters"]}
    assert fields["tenancy"] == "Shared"
    assert fields["operatingSystem"] == "Linux"
    assert fields["preInstalledSw"] == "NA"
    assert fields["capacitystatus"] == "Used"
    assert fields["location"] == "US West (Oregon)"


def test_compute_pricing_source_url_is_live_api(
    compute_estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {
        "PriceList": [
            _ec2_product(
                instance_type="m5.large",
                vcpu=2,
                memory_gib=8.0,
                hourly_usd=0.096,
            ),
        ],
        "NextToken": None,
    }
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-west-2",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    assert PRICING_ENDPOINT in result.pricing_source_url
    assert "AmazonEC2" in result.pricing_source_url
    assert "US West (Oregon)" in result.pricing_source_url
    assert result.pricing_fetched_at  # set


def test_compute_cache_reuses_sweep(
    fake_pricing: MagicMock,
) -> None:
    # With caching ON (default 6h), back-to-back estimates for the
    # same region hit the Pricing API once.
    cached = AWSCostEstimator(
        config=AWSCostConfig(pricing_client=fake_pricing),
    )
    fake_pricing.get_products.return_value = {
        "PriceList": [
            _ec2_product(
                instance_type="m5.large",
                vcpu=2,
                memory_gib=8.0,
                hourly_usd=0.096,
            ),
        ],
        "NextToken": None,
    }
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-west-2",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    cached.estimate(request)
    cached.estimate(request)
    assert fake_pricing.get_products.call_count == 1


def test_compute_pages_through_results(
    compute_estimator: AWSCostEstimator,
    fake_pricing: MagicMock,
) -> None:
    # Two pages — second one carries the cheapest fitting SKU.
    fake_pricing.get_products.side_effect = [
        {
            "PriceList": [
                _ec2_product(
                    instance_type="m5.large",
                    vcpu=2,
                    memory_gib=8.0,
                    hourly_usd=0.096,
                ),
            ],
            "NextToken": "page-2",
        },
        {
            "PriceList": [
                _ec2_product(
                    instance_type="t3.medium",
                    vcpu=2,
                    memory_gib=4.0,
                    hourly_usd=0.0416,
                ),
            ],
            "NextToken": None,
        },
    ]
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="us-west-2",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    result = compute_estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    assert result.line_items[0].sku == "SKU-T3.MEDIUM"
    assert fake_pricing.get_products.call_count == 2
