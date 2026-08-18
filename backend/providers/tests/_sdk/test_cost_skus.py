"""Tests for the shared component-wise SKU selector (#1318)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from _sdk.cost_skus import (
    CatalogPrice,
    SkuComponent,
    SkuPlan,
    SkuSelectionError,
    select_line_items,
)

if TYPE_CHECKING:
    from _sdk.cost import CostLineItem


def _price(
    *,
    sku_id: str,
    description: str,
    unit_price: float = 0.05,
    currency: str = "USD",
    regions: tuple[str, ...] = ("us-central1",),
    usage_type: str = "OnDemand",
) -> CatalogPrice:
    return CatalogPrice(
        sku_id=sku_id,
        description=description,
        unit_price=unit_price,
        currency=currency,
        usage_unit="h",
        regions=regions,
        attributes={"usage_type": usage_type},
    )


# One vCPU component, 10 vCPU-hours, scoped to PostgreSQL.
_VCPU_PLAN = SkuPlan(
    components=(
        SkuComponent(
            key="vcpu",
            label="vCPU",
            quantity=10.0,
            quantity_unit="vCPU-hour",
            require=("for PostgreSQL", "vCPU"),
            exclude=("for MySQL",),
            attributes={"usage_type": "OnDemand"},
        ),
    ),
)


def _select(prices: list[CatalogPrice]) -> list[CostLineItem] | SkuSelectionError:
    return select_line_items(
        plan=_VCPU_PLAN,
        prices=prices,
        region="us-central1",
        currency="USD",
    )


def test_single_match_prices_the_component_with_provenance() -> None:
    result = _select(
        [
            _price(
                sku_id="ABCD-1234-EFGH",
                description="Cloud SQL for PostgreSQL: Zonal - vCPU in Americas",
                unit_price=0.0413,
            ),
        ]
    )
    assert isinstance(result, list)
    (item,) = result
    assert item.sku == "ABCD-1234-EFGH"
    # 0.0413 * 10 vCPU-hours
    assert item.monthly_amount == 0.413
    # The exact catalog description is the audit trail an operator
    # replays the lookup with.
    assert "Cloud SQL for PostgreSQL: Zonal - vCPU in Americas" in item.notes
    assert "ABCD-1234-EFGH" in item.notes


def test_unrelated_rows_never_join_the_total() -> None:
    """The bug this module exists for: a service's SKU list carries
    other engines, other regions and other rate plans."""
    result = _select(
        [
            _price(
                sku_id="RIGHT",
                description="Cloud SQL for PostgreSQL: Zonal - vCPU in Americas",
                unit_price=0.04,
            ),
            _price(
                sku_id="WRONG-ENGINE",
                description="Cloud SQL for MySQL: Zonal - vCPU in Americas",
            ),
            _price(
                sku_id="WRONG-REGION",
                description="Cloud SQL for PostgreSQL: Zonal - vCPU in EMEA",
                regions=("europe-west1",),
            ),
            _price(
                sku_id="WRONG-RATE-PLAN",
                description="Commitment v1: Cloud SQL for PostgreSQL: Zonal - vCPU in Americas",
                usage_type="Commit1Yr",
            ),
        ]
    )
    assert isinstance(result, list)
    assert [item.sku for item in result] == ["RIGHT"]
    assert result[0].monthly_amount == 0.4


def test_two_matching_skus_refuse_rather_than_sum() -> None:
    result = _select(
        [
            _price(
                sku_id="ENTERPRISE",
                description="Cloud SQL for PostgreSQL: Zonal - vCPU in Americas",
            ),
            _price(
                sku_id="ENTERPRISE-PLUS",
                description="Cloud SQL for PostgreSQL: Enterprise Plus - Zonal - vCPU in Americas",
            ),
        ]
    )
    assert isinstance(result, SkuSelectionError)
    assert result.reason == "ambiguous_sku"
    assert result.component_key == "vcpu"
    assert "ENTERPRISE-PLUS" in result.message


def test_missing_sku_refuses_rather_than_pricing_at_zero() -> None:
    result = _select(
        [
            _price(
                sku_id="ONLY-RAM",
                description="Cloud SQL for PostgreSQL: Zonal - RAM in Americas",
            ),
        ]
    )
    assert isinstance(result, SkuSelectionError)
    assert result.reason == "sku_not_found"
    assert result.component_key == "vcpu"


def test_zero_priced_row_is_not_a_price() -> None:
    """A promotional / free-tier row matches the same description as
    the paid one; taking it would show a paid database as free."""
    result = _select(
        [
            _price(
                sku_id="FREE-TIER",
                description="Cloud SQL for PostgreSQL: Zonal - vCPU in Americas",
                unit_price=0.0,
            ),
        ]
    )
    assert isinstance(result, SkuSelectionError)
    assert result.reason == "sku_not_found"


def test_other_currency_rows_are_not_priced() -> None:
    result = _select(
        [
            _price(
                sku_id="EUR-ROW",
                description="Cloud SQL for PostgreSQL: Zonal - vCPU in Americas",
                currency="EUR",
            ),
        ]
    )
    assert isinstance(result, SkuSelectionError)
    assert result.reason == "sku_not_found"


def test_global_and_unscoped_rows_stay_eligible() -> None:
    result = _select(
        [
            _price(
                sku_id="GLOBAL",
                description="Cloud SQL for PostgreSQL: Zonal - vCPU in Americas",
                regions=("global",),
                unit_price=0.01,
            ),
        ]
    )
    assert isinstance(result, list)
    assert result[0].sku == "GLOBAL"


def test_first_unresolved_component_abandons_the_whole_estimate() -> None:
    """A partial estimate reads as a total, so one missing component
    invalidates the rest."""
    plan = SkuPlan(
        components=(
            SkuComponent(
                key="vcpu",
                label="vCPU",
                quantity=10.0,
                quantity_unit="vCPU-hour",
                require=("vCPU",),
            ),
            SkuComponent(
                key="storage",
                label="Storage",
                quantity=100.0,
                quantity_unit="GiB-month",
                require=("SSD storage",),
            ),
        ),
    )
    result = select_line_items(
        plan=plan,
        prices=[
            _price(
                sku_id="CPU",
                description="Cloud SQL for PostgreSQL: Zonal - vCPU in Americas",
            ),
        ],
        region="us-central1",
        currency="USD",
    )
    assert isinstance(result, SkuSelectionError)
    assert result.component_key == "storage"
