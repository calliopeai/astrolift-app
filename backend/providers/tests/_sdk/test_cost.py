"""Tests for the cost-estimation protocol (#78).

The protocol-level tests cover the dataclass shapes; per-cloud
estimators land in their respective test files when implemented.
The CRITICAL invariant is that estimates come from a live cloud
pricing API — these tests pin the protocol fields that enforce
that contract (pricing_source_url + pricing_fetched_at + sku)."""

from __future__ import annotations

import pytest

from _sdk.cost import (
    CostEstimate,
    CostEstimateRequest,
    CostEstimateUnavailable,
    CostLineItem,
)


def test_request_defaults() -> None:
    req = CostEstimateRequest(
        kind="postgres",
        variant="rds",
        region="us-east-1",
        size="small",
    )
    assert req.currency == "USD"
    assert req.config == {}
    assert req.expected_usage == {}


def test_estimate_carries_pricing_provenance() -> None:
    """Pricing-source-url + pricing_fetched_at + per-line SKU are
    the fields that let auditors verify the estimate against the
    cloud's own catalog. The dataclass shape pins them at the
    type level so a driver author can't ship without them."""
    req = CostEstimateRequest(
        kind="postgres",
        variant="rds",
        region="us-east-1",
        size="small",
    )
    estimate = CostEstimate(
        request=req,
        line_items=[
            CostLineItem(
                label="db.t4g.micro instance",
                sku="db.t4g.micro:us-east-1:on_demand",
                monthly_amount=12.34,
            ),
        ],
        monthly_total=12.34,
        pricing_source_url="https://api.pricing.us-east-1.amazonaws.com",
        pricing_fetched_at="2026-05-09T00:00:00Z",
    )
    assert estimate.pricing_source_url
    assert estimate.pricing_fetched_at
    assert estimate.line_items[0].sku.startswith("db.t4g.micro")


def test_unavailable_distinct_from_zero_cost() -> None:
    """Returning CostEstimateUnavailable is meaningfully different
    from returning CostEstimate(monthly_total=0). Callers branch
    on the type to avoid showing 'free!' for unsupported variants."""
    req = CostEstimateRequest(
        kind="postgres",
        variant="cnpg",
        region="local",
        size="small",
    )
    unavail = CostEstimateUnavailable(
        request=req,
        reason="unsupported",
        message="k8s_native runs in-cluster; no cloud pricing API",
    )
    assert unavail.reason == "unsupported"
    assert isinstance(unavail, CostEstimateUnavailable)
    assert not isinstance(unavail, CostEstimate)


def test_line_item_sku_is_required_field() -> None:
    """SKU has no default — driver authors must supply it from
    the live pricing-catalog response. Hard-coded prices would
    show up here as a synthesized SKU, but reviewer notes catch
    that pattern."""
    with pytest.raises(TypeError):
        # Missing SKU — should fail at construction
        CostLineItem(  # type: ignore[call-arg]
            label="x",
            monthly_amount=1.0,
        )


def test_unavailable_reasons_are_documented() -> None:
    """The doc-comment enumerates the legal reasons. New ones
    require updating consumers."""
    valid = {
        "no_pricing_api",
        "sku_not_found",
        "api_error",
        "unsupported",
    }
    # smoke test: each reason can be stored
    for reason in valid:
        unavail = CostEstimateUnavailable(
            request=CostEstimateRequest(
                kind="x",
                variant="y",
                region="r",
                size="small",
            ),
            reason=reason,
        )
        assert unavail.reason == reason


def test_expected_usage_metrics_passed_through() -> None:
    req = CostEstimateRequest(
        kind="object_store",
        variant="s3",
        region="us-east-1",
        size="custom",
        expected_usage={
            "storage_gb_month": 1000,
            "get_requests_per_month": 5_000_000,
            "put_requests_per_month": 100_000,
        },
    )
    assert req.expected_usage["storage_gb_month"] == 1000
