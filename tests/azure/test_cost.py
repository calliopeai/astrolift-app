"""Tests for Azure CostEstimator (#78 part of #70)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from _sdk.cost import CostEstimate, CostEstimateRequest, CostEstimateUnavailable
from azure.cost import (
    PRICES_ENDPOINT,
    SERVICE_NAME_BY_VARIANT,
    AzureCostConfig,
    AzureCostEstimator,
)


@dataclass
class FakeResponse:
    body: dict[str, Any]

    def json(self) -> Any:
        return self.body


@dataclass
class FakeHttp:
    response: FakeResponse | None = None
    last_url: str | None = None
    last_params: dict[str, Any] = field(default_factory=dict)

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        self.last_url = url
        self.last_params = dict(params or {})
        return self.response


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def estimator(fake_http: FakeHttp) -> AzureCostEstimator:
    return AzureCostEstimator(
        config=AzureCostConfig(http_client=fake_http),
    )


def test_supported_recognizes_known_variants(
    estimator: AzureCostEstimator,
) -> None:
    assert estimator.supported(kind="object_store", variant="blob")
    assert estimator.supported(kind="queue", variant="servicebus")


def test_unsupported_variant_returns_unavailable(
    estimator: AzureCostEstimator,
) -> None:
    result = estimator.estimate(CostEstimateRequest(
        kind="postgres", variant="never",
        region="eastus", size="small",
    ))
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "unsupported"


def test_api_error_returns_unavailable(
    estimator: AzureCostEstimator, fake_http: FakeHttp,
) -> None:
    def _raise(*a, **kw):
        raise RuntimeError("Retail API down")
    fake_http.get = _raise  # type: ignore[method-assign]
    result = estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="blob",
        region="eastus", size="custom",
    ))
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_no_items_returns_unavailable(
    estimator: AzureCostEstimator, fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={"Items": []})
    result = estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="blob",
        region="eastus", size="custom",
    ))
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_blob_storage_estimate_uses_gb_month(
    estimator: AzureCostEstimator, fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={
        "Items": [{
            "skuId": "DZH318Z0BPYS/00CK",
            "productName": "Standard Storage",
            "unitPrice": 0.0184,
            "unitOfMeasure": "1 GB/Month",
        }],
    })
    result = estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="blob",
        region="eastus", size="custom",
        expected_usage={"storage_gb_month": 1000.0},
    ))
    assert isinstance(result, CostEstimate)
    assert result.monthly_total == 18.4


def test_filter_includes_region(
    estimator: AzureCostEstimator, fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={"Items": []})
    estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="blob",
        region="eastus", size="custom",
    ))
    odata = fake_http.last_params["$filter"]
    assert "armRegionName eq 'eastus'" in odata
    assert "serviceName eq 'Storage'" in odata


def test_provenance_url_carries_endpoint(
    estimator: AzureCostEstimator, fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={
        "Items": [{
            "skuId": "X", "productName": "P",
            "unitPrice": 1.0, "unitOfMeasure": "1 Hour",
        }],
    })
    result = estimator.estimate(CostEstimateRequest(
        kind="object_store", variant="blob",
        region="eastus", size="custom",
        expected_usage={"hours_per_month": 720},
    ))
    assert isinstance(result, CostEstimate)
    assert PRICES_ENDPOINT in result.pricing_source_url


def test_service_name_table_covers_core_kinds() -> None:
    assert ("object_store", "blob") in SERVICE_NAME_BY_VARIANT
    assert ("queue", "servicebus") in SERVICE_NAME_BY_VARIANT
