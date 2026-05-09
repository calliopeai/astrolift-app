"""Tests for AWS CostEstimator (#78 part of #68)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
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
    return json.dumps({
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
    })


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
        kind="postgres", variant="never_added",
        region="us-east-1", size="small",
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "unsupported"


def test_api_error_returns_unavailable(
    estimator: AWSCostEstimator, fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.side_effect = RuntimeError(
        "AWS API throttled",
    )
    request = CostEstimateRequest(
        kind="object_store", variant="s3",
        region="us-east-1", size="custom",
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_no_skus_returns_unavailable(
    estimator: AWSCostEstimator, fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {"PriceList": []}
    request = CostEstimateRequest(
        kind="object_store", variant="s3",
        region="us-east-1", size="custom",
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_s3_estimate_uses_storage_gb_month(
    estimator: AWSCostEstimator, fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {
        "PriceList": [_s3_pricelist_record()],
    }
    request = CostEstimateRequest(
        kind="object_store", variant="s3",
        region="us-east-1", size="custom",
        expected_usage={"storage_gb_month": 1000.0},
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    # 0.023 * 1000 = 23.00
    assert result.monthly_total == 23.0
    assert result.line_items[0].sku == "ABCDEF1234"


def test_estimate_carries_pricing_provenance(
    estimator: AWSCostEstimator, fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {
        "PriceList": [_s3_pricelist_record()],
    }
    request = CostEstimateRequest(
        kind="object_store", variant="s3",
        region="us-east-1", size="custom",
        expected_usage={"storage_gb_month": 100.0},
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimate)
    assert PRICING_ENDPOINT in result.pricing_source_url
    assert "AmazonS3" in result.pricing_source_url
    assert result.pricing_fetched_at  # set to current ISO time


def test_filters_include_region_location(
    estimator: AWSCostEstimator, fake_pricing: MagicMock,
) -> None:
    fake_pricing.get_products.return_value = {"PriceList": []}
    request = CostEstimateRequest(
        kind="object_store", variant="s3",
        region="us-east-1", size="custom",
    )
    estimator.estimate(request)
    args, kwargs = fake_pricing.get_products.call_args
    filters = kwargs["Filters"]
    location_filter = next(
        f for f in filters if f["Field"] == "location"
    )
    assert location_filter["Value"] == "US East (N. Virginia)"


def test_service_code_table_covers_core_kinds() -> None:
    """Core data kinds must have service-code mappings."""
    assert ("object_store", "s3") in SERVICE_CODE_BY_VARIANT
    assert ("queue", "sqs") in SERVICE_CODE_BY_VARIANT
    assert ("postgres", "rds") in SERVICE_CODE_BY_VARIANT
