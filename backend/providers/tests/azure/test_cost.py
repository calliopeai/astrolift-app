"""Tests for Azure CostEstimator (#78 part of #70)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    result = estimator.estimate(
        CostEstimateRequest(
            kind="postgres",
            variant="never",
            region="eastus",
            size="small",
        )
    )
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "unsupported"


def test_api_error_returns_unavailable(
    estimator: AzureCostEstimator,
    fake_http: FakeHttp,
) -> None:
    def _raise(*a, **kw):
        raise RuntimeError("Retail API down")

    fake_http.get = _raise  # type: ignore[method-assign]
    result = estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="blob",
            region="eastus",
            size="custom",
        )
    )
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_no_items_returns_unavailable(
    estimator: AzureCostEstimator,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={"Items": []})
    result = estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="blob",
            region="eastus",
            size="custom",
        )
    )
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_blob_storage_estimate_uses_gb_month(
    estimator: AzureCostEstimator,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "Items": [
                {
                    "skuId": "DZH318Z0BPYS/00CK",
                    "productName": "Standard Storage",
                    "unitPrice": 0.0184,
                    "unitOfMeasure": "1 GB/Month",
                }
            ],
        }
    )
    result = estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="blob",
            region="eastus",
            size="custom",
            expected_usage={"storage_gb_month": 1000.0},
        )
    )
    assert isinstance(result, CostEstimate)
    assert result.monthly_total == 18.4


def test_filter_includes_region(
    estimator: AzureCostEstimator,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={"Items": []})
    estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="blob",
            region="eastus",
            size="custom",
        )
    )
    odata = fake_http.last_params["$filter"]
    assert "armRegionName eq 'eastus'" in odata
    assert "serviceName eq 'Storage'" in odata


def test_provenance_url_carries_endpoint(
    estimator: AzureCostEstimator,
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "Items": [
                {
                    "skuId": "X",
                    "productName": "P",
                    "unitPrice": 1.0,
                    "unitOfMeasure": "1 Hour",
                }
            ],
        }
    )
    result = estimator.estimate(
        CostEstimateRequest(
            kind="object_store",
            variant="blob",
            region="eastus",
            size="custom",
            expected_usage={"hours_per_month": 720},
        )
    )
    assert isinstance(result, CostEstimate)
    assert PRICES_ENDPOINT in result.pricing_source_url


def test_service_name_table_covers_core_kinds() -> None:
    assert ("object_store", "blob") in SERVICE_NAME_BY_VARIANT
    assert ("queue", "servicebus") in SERVICE_NAME_BY_VARIANT


# ---- compute / node_hour (#440) -----------------------------------


def _vm_size_lookup(
    table: dict[str, tuple[int, float]] | None = None,
    *,
    raise_on_call: Exception | None = None,
) -> Any:
    """Build a fake `vm_sizes_lookup` callable for Azure compute
    tests. The returned function accepts a region and returns the
    same table regardless of region — the SKUs that matter to a
    test are scoped via the test's `armRegionName` filter on the
    Retail Prices side."""
    table = table or {}

    def _call(region: str) -> dict[str, tuple[int, float]]:
        if raise_on_call is not None:
            raise raise_on_call
        return dict(table)

    return _call


def _price_record(
    *,
    arm_sku_name: str,
    sku_id: str,
    hourly_usd: float,
    product_name: str = "Virtual Machines D Series",
    sku_name: str | None = None,
) -> dict[str, Any]:
    return {
        "armSkuName": arm_sku_name,
        "skuId": sku_id,
        "skuName": sku_name or arm_sku_name.removeprefix("Standard_"),
        "productName": product_name,
        "unitPrice": hourly_usd,
        "unitOfMeasure": "1 Hour",
        "armRegionName": "eastus",
        "priceType": "Consumption",
    }


def test_compute_node_hour_is_supported(
    estimator: AzureCostEstimator,
) -> None:
    assert estimator.supported(kind="compute", variant="node_hour")


def test_compute_picks_cheapest_fitting_d_sku(
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "Items": [
                _price_record(
                    arm_sku_name="Standard_D2s_v3",
                    sku_id="D2S-V3-EASTUS",
                    hourly_usd=0.096,
                ),
                _price_record(
                    arm_sku_name="Standard_D4s_v3",
                    sku_id="D4S-V3-EASTUS",
                    hourly_usd=0.192,
                ),
                _price_record(
                    arm_sku_name="Standard_D2as_v5",
                    sku_id="D2AS-V5-EASTUS",
                    hourly_usd=0.086,
                ),
            ],
        }
    )
    sizes = {
        "Standard_D2s_v3": (2, 8.0),
        "Standard_D4s_v3": (4, 16.0),
        "Standard_D2as_v5": (2, 8.0),
    }
    est = AzureCostEstimator(
        config=AzureCostConfig(
            http_client=fake_http,
            vm_sizes_lookup=_vm_size_lookup(sizes),
            cache_ttl_seconds=0,
        ),
    )
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="eastus",
        size="custom",
        expected_usage={
            "cpu_cores": 2.0,
            "memory_gib": 4.0,
            "hours_per_month": 720.0,
        },
    )
    result = est.estimate(request)
    assert isinstance(result, CostEstimate)
    # D2as_v5 fits at $0.086/hr — cheapest of the three.
    assert result.line_items[0].sku == "D2AS-V5-EASTUS"
    # 0.086 * 720 = 61.92
    assert result.monthly_total == pytest.approx(61.92, rel=1e-3)


def test_compute_skips_skus_without_capacity_info(
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "Items": [
                _price_record(
                    arm_sku_name="Standard_D99_unknown",
                    sku_id="UNKNOWN",
                    hourly_usd=0.01,
                ),
                _price_record(
                    arm_sku_name="Standard_D2s_v3",
                    sku_id="D2S",
                    hourly_usd=0.096,
                ),
            ],
        }
    )
    est = AzureCostEstimator(
        config=AzureCostConfig(
            http_client=fake_http,
            vm_sizes_lookup=_vm_size_lookup(
                {
                    "Standard_D2s_v3": (2, 8.0),
                }
            ),
            cache_ttl_seconds=0,
        ),
    )
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="eastus",
        size="custom",
        expected_usage={
            "cpu_cores": 2.0,
            "memory_gib": 4.0,
            "hours_per_month": 720.0,
        },
    )
    result = est.estimate(request)
    assert isinstance(result, CostEstimate)
    # D99 is excluded for lack of capacity info -> D2s wins.
    assert result.line_items[0].sku == "D2S"


def test_compute_skips_windows_and_spot(
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "Items": [
                {
                    **_price_record(
                        arm_sku_name="Standard_D2s_v3",
                        sku_id="SPOT",
                        hourly_usd=0.02,
                    ),
                    "skuName": "D2s v3 Spot",
                },
                {
                    **_price_record(
                        arm_sku_name="Standard_D2s_v3",
                        sku_id="WIN",
                        hourly_usd=0.20,
                        product_name="Virtual Machines D Series Windows",
                    ),
                    "skuName": "D2s v3 Windows",
                },
                _price_record(
                    arm_sku_name="Standard_D2s_v3",
                    sku_id="LINUX-OD",
                    hourly_usd=0.096,
                ),
            ],
        }
    )
    est = AzureCostEstimator(
        config=AzureCostConfig(
            http_client=fake_http,
            vm_sizes_lookup=_vm_size_lookup(
                {
                    "Standard_D2s_v3": (2, 8.0),
                }
            ),
            cache_ttl_seconds=0,
        ),
    )
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="eastus",
        size="custom",
        expected_usage={
            "cpu_cores": 2.0,
            "memory_gib": 4.0,
            "hours_per_month": 720.0,
        },
    )
    result = est.estimate(request)
    assert isinstance(result, CostEstimate)
    assert result.line_items[0].sku == "LINUX-OD"


def test_compute_filters_pin_consumption_region(
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(body={"Items": []})
    est = AzureCostEstimator(
        config=AzureCostConfig(
            http_client=fake_http,
            vm_sizes_lookup=_vm_size_lookup(
                {
                    "Standard_D2s_v3": (2, 8.0),
                }
            ),
            cache_ttl_seconds=0,
        ),
    )
    est.estimate(
        CostEstimateRequest(
            kind="compute",
            variant="node_hour",
            region="westeurope",
            size="custom",
            expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
        )
    )
    odata = fake_http.last_params["$filter"]
    assert "serviceName eq 'Virtual Machines'" in odata
    assert "armRegionName eq 'westeurope'" in odata
    assert "priceType eq 'Consumption'" in odata


def test_compute_no_fitting_sku_returns_unavailable(
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "Items": [
                _price_record(
                    arm_sku_name="Standard_D2s_v3",
                    sku_id="D2S",
                    hourly_usd=0.096,
                ),
            ],
        }
    )
    est = AzureCostEstimator(
        config=AzureCostConfig(
            http_client=fake_http,
            vm_sizes_lookup=_vm_size_lookup(
                {
                    "Standard_D2s_v3": (2, 8.0),
                }
            ),
            cache_ttl_seconds=0,
        ),
    )
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="eastus",
        size="custom",
        expected_usage={
            "cpu_cores": 16.0,
            "memory_gib": 64.0,
            "hours_per_month": 720.0,
        },
    )
    result = est.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"


def test_compute_without_vm_size_lookup_returns_unavailable(
    fake_http: FakeHttp,
) -> None:
    # No vm_sizes_lookup in config + no subscription_id in request
    # config -> default lookup is None -> Unavailable.
    est = AzureCostEstimator(
        config=AzureCostConfig(
            http_client=fake_http,
            cache_ttl_seconds=0,
        ),
    )
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="eastus",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    result = est.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"
    assert "vm_sizes_lookup" in result.message


def test_compute_api_error_returns_unavailable(
    fake_http: FakeHttp,
) -> None:
    def _raise(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("Retail API down")

    fake_http.get = _raise  # type: ignore[method-assign]
    est = AzureCostEstimator(
        config=AzureCostConfig(
            http_client=fake_http,
            vm_sizes_lookup=_vm_size_lookup(
                {
                    "Standard_D2s_v3": (2, 8.0),
                }
            ),
            cache_ttl_seconds=0,
        ),
    )
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="eastus",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    result = est.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_compute_provenance_url(
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "Items": [
                _price_record(
                    arm_sku_name="Standard_D2s_v3",
                    sku_id="D2S",
                    hourly_usd=0.096,
                ),
            ],
        }
    )
    est = AzureCostEstimator(
        config=AzureCostConfig(
            http_client=fake_http,
            vm_sizes_lookup=_vm_size_lookup(
                {
                    "Standard_D2s_v3": (2, 8.0),
                }
            ),
            cache_ttl_seconds=0,
        ),
    )
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="eastus",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    result = est.estimate(request)
    assert isinstance(result, CostEstimate)
    assert PRICES_ENDPOINT in result.pricing_source_url
    assert "Virtual Machines" in result.pricing_source_url
    assert "eastus" in result.pricing_source_url
    assert result.pricing_fetched_at


def test_compute_cache_reuses_prices_and_sizes(
    fake_http: FakeHttp,
) -> None:
    fake_http.response = FakeResponse(
        body={
            "Items": [
                _price_record(
                    arm_sku_name="Standard_D2s_v3",
                    sku_id="D2S",
                    hourly_usd=0.096,
                ),
            ],
        }
    )
    size_call_count = 0

    def _counting_lookup(region: str) -> dict[str, tuple[int, float]]:
        nonlocal size_call_count
        size_call_count += 1
        return {"Standard_D2s_v3": (2, 8.0)}

    price_call_count = 0
    original_get = fake_http.get

    def _counting_get(url: str, *, params: dict[str, Any] | None = None) -> Any:
        nonlocal price_call_count
        price_call_count += 1
        return original_get(url, params=params)

    fake_http.get = _counting_get  # type: ignore[method-assign]
    est = AzureCostEstimator(
        config=AzureCostConfig(
            http_client=fake_http,
            vm_sizes_lookup=_counting_lookup,
        ),
    )
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="eastus",
        size="custom",
        expected_usage={"cpu_cores": 2.0, "memory_gib": 4.0},
    )
    est.estimate(request)
    est.estimate(request)
    assert size_call_count == 1
    assert price_call_count == 1


def test_compute_empty_usage_returns_unavailable(
    estimator: AzureCostEstimator,
) -> None:
    request = CostEstimateRequest(
        kind="compute",
        variant="node_hour",
        region="eastus",
        size="custom",
        expected_usage={},
    )
    result = estimator.estimate(request)
    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"
