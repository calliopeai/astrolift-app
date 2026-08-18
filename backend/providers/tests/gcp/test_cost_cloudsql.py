"""SKU-aware Cloud SQL cost estimates (#1318).

The fixtures below mirror the shape of a real
``services/9662-B51E-5089/skus`` response: one flat list carrying all
three Cloud SQL engines, both editions, zonal and regional rates,
several storage types, licences, backups, egress, other regions and a
committed-use rate plan. Every test asserts against that same mixed
list, because the bug being fixed is precisely that unrelated rows
used to be summed into the total.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.cost import CostEstimate, CostEstimateRequest, CostEstimateUnavailable
from gcp.cost import GCPCostConfig, GCPCostEstimator

CLOUD_SQL_SERVICE_ID = "9662-B51E-5089"
REGION = "us-central1"


@dataclass
class FakeUnitPrice:
    nanos: int
    units: int = 0
    currency_code: str = "USD"


@dataclass
class FakeTier:
    unit_price: FakeUnitPrice


@dataclass
class FakeExpression:
    usage_unit: str
    tiered_rates: list[FakeTier]


@dataclass
class FakePricingInfo:
    pricing_expression: FakeExpression


@dataclass
class FakeCategory:
    resource_family: str = "ApplicationServices"
    resource_group: str = "SQLGen2Instances"
    usage_type: str = "OnDemand"


@dataclass
class FakeSku:
    sku_id: str
    description: str
    pricing_info: list[FakePricingInfo]
    service_regions: list[str] = field(default_factory=lambda: [REGION])
    category: FakeCategory = field(default_factory=FakeCategory)
    name: str = ""


class FakeCatalogClient:
    """Stands in for ``billing_v1.CloudCatalogClient``.

    Records the ``parent`` every call asks for so a test can prove the
    estimate went through the live Catalog endpoint for the Cloud SQL
    service rather than through some local table.
    """

    def __init__(self, skus: list[FakeSku]) -> None:
        self.skus = skus
        self.parents: list[str] = []

    def list_services(self) -> list[Any]:
        return []

    def list_skus(self, *, parent: str) -> list[FakeSku]:
        self.parents.append(parent)
        return list(self.skus)


def _sku(
    sku_id: str,
    description: str,
    nanos: int,
    *,
    usage_unit: str = "h",
    regions: list[str] | None = None,
    usage_type: str = "OnDemand",
) -> FakeSku:
    return FakeSku(
        sku_id=sku_id,
        description=description,
        service_regions=regions if regions is not None else [REGION],
        category=FakeCategory(usage_type=usage_type),
        pricing_info=[
            FakePricingInfo(
                pricing_expression=FakeExpression(
                    usage_unit=usage_unit,
                    tiered_rates=[FakeTier(unit_price=FakeUnitPrice(nanos=nanos))],
                ),
            ),
        ],
    )


def catalog() -> list[FakeSku]:
    return [
        # --- Cloud SQL for SQL Server, Enterprise edition ---
        _sku("SQLSRV-CPU-ZONAL", "Cloud SQL for SQL Server: Zonal - vCPU in Americas", 59_000_000),
        _sku("SQLSRV-RAM-ZONAL", "Cloud SQL for SQL Server: Zonal - RAM in Americas", 10_000_000, usage_unit="GiBy.h"),
        _sku("SQLSRV-CPU-REGIONAL", "Cloud SQL for SQL Server: Regional - vCPU in Americas", 118_000_000),
        _sku(
            "SQLSRV-RAM-REGIONAL",
            "Cloud SQL for SQL Server: Regional - RAM in Americas",
            20_000_000,
            usage_unit="GiBy.h",
        ),
        # --- Cloud SQL for SQL Server, Enterprise Plus edition ---
        _sku(
            "SQLSRV-CPU-ZONAL-EP",
            "Cloud SQL for SQL Server: Enterprise Plus - Zonal - vCPU in Americas",
            80_000_000,
        ),
        _sku(
            "SQLSRV-RAM-ZONAL-EP",
            "Cloud SQL for SQL Server: Enterprise Plus - Zonal - RAM in Americas",
            15_000_000,
            usage_unit="GiBy.h",
        ),
        # --- SQL Server storage + backups ---
        _sku(
            "SQLSRV-SSD-ZONAL",
            "Cloud SQL for SQL Server: Zonal - SSD storage in Americas",
            170_000_000,
            usage_unit="GiBy.mo",
        ),
        _sku(
            "SQLSRV-HDD-ZONAL",
            "Cloud SQL for SQL Server: Zonal - HDD storage in Americas",
            90_000_000,
            usage_unit="GiBy.mo",
        ),
        _sku(
            "SQLSRV-SSD-REGIONAL",
            "Cloud SQL for SQL Server: Regional - SSD storage in Americas",
            340_000_000,
            usage_unit="GiBy.mo",
        ),
        _sku(
            "SQLSRV-BACKUP",
            "Cloud SQL for SQL Server: Backups in Americas",
            80_000_000,
            usage_unit="GiBy.mo",
        ),
        # --- SQL Server licences, one per server edition ---
        _sku("SQLSRV-LIC-STD", "Cloud SQL for SQL Server: Standard edition License in Americas", 50_000_000),
        _sku("SQLSRV-LIC-ENT", "Cloud SQL for SQL Server: Enterprise edition License in Americas", 150_000_000),
        _sku("SQLSRV-LIC-WEB", "Cloud SQL for SQL Server: Web edition License in Americas", 20_000_000),
        # --- Other engines sharing the same billing service ---
        _sku("PG-CPU-ZONAL", "Cloud SQL for PostgreSQL: Zonal - vCPU in Americas", 41_300_000),
        _sku("PG-RAM-ZONAL", "Cloud SQL for PostgreSQL: Zonal - RAM in Americas", 7_000_000, usage_unit="GiBy.h"),
        _sku(
            "PG-SSD-ZONAL",
            "Cloud SQL for PostgreSQL: Zonal - SSD storage in Americas",
            170_000_000,
            usage_unit="GiBy.mo",
        ),
        _sku("PG-BACKUP", "Cloud SQL for PostgreSQL: Backups in Americas", 80_000_000, usage_unit="GiBy.mo"),
        _sku("MYSQL-CPU-ZONAL", "Cloud SQL for MySQL: Zonal - vCPU in Americas", 41_300_000),
        _sku("MYSQL-RAM-ZONAL", "Cloud SQL for MySQL: Zonal - RAM in Americas", 7_000_000, usage_unit="GiBy.h"),
        _sku(
            "MYSQL-SSD-ZONAL",
            "Cloud SQL for MySQL: Zonal - SSD storage in Americas",
            170_000_000,
            usage_unit="GiBy.mo",
        ),
        # --- Service-wide network egress ---
        _sku(
            "NET-EGRESS",
            "Cloud SQL: Network Egress from Americas to Americas",
            10_000_000,
            usage_unit="GiBy",
        ),
        # --- Rows that must never be reachable for a us-central1
        # on-demand estimate ---
        _sku(
            "SQLSRV-CPU-ZONAL-EMEA",
            "Cloud SQL for SQL Server: Zonal - vCPU in EMEA",
            67_000_000,
            regions=["europe-west1"],
        ),
        _sku(
            "SQLSRV-CPU-ZONAL-CUD",
            "Commitment v1: Cloud SQL for SQL Server: Zonal - vCPU in Americas",
            30_000_000,
            usage_type="Commit1Yr",
        ),
    ]


def _estimator(skus: list[FakeSku] | None = None) -> tuple[GCPCostEstimator, FakeCatalogClient]:
    client = FakeCatalogClient(catalog() if skus is None else skus)
    return (
        GCPCostEstimator(config=GCPCostConfig(billing_client=client, cache_ttl_seconds=0)),
        client,
    )


def _sqlserver_request(**config: str) -> CostEstimateRequest:
    merged = {
        "tier": "db-custom-4-15360",
        "storage_gb": "100",
        "storage_type": "PD_SSD",
        "cloudsql_edition": "ENTERPRISE",
        "engine_version": "SQLSERVER_2022_STANDARD",
        "high_availability": "false",
    }
    merged.update(config)
    return CostEstimateRequest(
        kind="mssql",
        variant="cloudsql_sqlserver",
        region=REGION,
        size="custom",
        config=merged,
        expected_usage={
            "hours_per_month": 720.0,
            "backup_storage_gb_month": 50.0,
        },
    )


def _by_key(result: CostEstimate) -> dict[str, float]:
    return {item.sku: item.monthly_amount for item in result.line_items}


def test_sqlserver_estimate_prices_only_its_own_skus() -> None:
    estimator, _ = _estimator()
    result = estimator.estimate(_sqlserver_request())

    assert isinstance(result, CostEstimate), getattr(result, "message", "")
    amounts = _by_key(result)
    assert amounts == {
        "SQLSRV-CPU-ZONAL": pytest.approx(169.92),  # 4 vCPU * 720h * 0.059
        "SQLSRV-RAM-ZONAL": pytest.approx(108.0),  # 15 GiB * 720h * 0.010
        "SQLSRV-SSD-ZONAL": pytest.approx(17.0),  # 100 GiB * 0.170
        "SQLSRV-BACKUP": pytest.approx(4.0),  # 50 GiB * 0.080
        "SQLSRV-LIC-STD": pytest.approx(144.0),  # 4 vCPU * 720h * 0.050
    }
    assert result.monthly_total == pytest.approx(442.92)


def test_sqlserver_estimate_excludes_every_unrelated_regional_sku() -> None:
    estimator, _ = _estimator()
    result = estimator.estimate(_sqlserver_request())

    assert isinstance(result, CostEstimate)
    priced = set(_by_key(result))
    for unrelated in (
        "MYSQL-CPU-ZONAL",
        "MYSQL-RAM-ZONAL",
        "MYSQL-SSD-ZONAL",
        "PG-CPU-ZONAL",
        "PG-RAM-ZONAL",
        "PG-SSD-ZONAL",
        "PG-BACKUP",
        "SQLSRV-CPU-ZONAL-EP",
        "SQLSRV-RAM-ZONAL-EP",
        "SQLSRV-CPU-REGIONAL",
        "SQLSRV-RAM-REGIONAL",
        "SQLSRV-SSD-REGIONAL",
        "SQLSRV-HDD-ZONAL",
        "SQLSRV-LIC-ENT",
        "SQLSRV-LIC-WEB",
        "NET-EGRESS",
        "SQLSRV-CPU-ZONAL-EMEA",
        "SQLSRV-CPU-ZONAL-CUD",
    ):
        assert unrelated not in priced


def test_estimate_tracks_the_requested_tier() -> None:
    estimator, _ = _estimator()
    small = estimator.estimate(_sqlserver_request(tier="db-custom-4-15360"))
    large = estimator.estimate(_sqlserver_request(tier="db-custom-8-30720"))

    assert isinstance(small, CostEstimate)
    assert isinstance(large, CostEstimate)
    # Compute + licence double with the vCPU/RAM count; storage and
    # backup are unchanged.
    assert _by_key(large)["SQLSRV-CPU-ZONAL"] == pytest.approx(2 * _by_key(small)["SQLSRV-CPU-ZONAL"])
    assert _by_key(large)["SQLSRV-LIC-STD"] == pytest.approx(2 * _by_key(small)["SQLSRV-LIC-STD"])
    assert _by_key(large)["SQLSRV-SSD-ZONAL"] == _by_key(small)["SQLSRV-SSD-ZONAL"]
    assert large.monthly_total > small.monthly_total


def test_enterprise_plus_prices_from_its_own_compute_skus() -> None:
    estimator, _ = _estimator()
    result = estimator.estimate(_sqlserver_request(cloudsql_edition="ENTERPRISE_PLUS"))

    assert isinstance(result, CostEstimate), getattr(result, "message", "")
    priced = set(_by_key(result))
    assert {"SQLSRV-CPU-ZONAL-EP", "SQLSRV-RAM-ZONAL-EP"} <= priced
    assert "SQLSRV-CPU-ZONAL" not in priced
    assert "SQLSRV-RAM-ZONAL" not in priced


def test_high_availability_prices_from_the_regional_skus() -> None:
    """HA is not a multiplier the platform applies; Google prices the
    regional SKUs separately and the plan selects them."""
    estimator, _ = _estimator()
    zonal = estimator.estimate(_sqlserver_request(high_availability="false"))
    regional = estimator.estimate(_sqlserver_request(high_availability="true"))

    assert isinstance(zonal, CostEstimate)
    assert isinstance(regional, CostEstimate)
    assert {"SQLSRV-CPU-REGIONAL", "SQLSRV-RAM-REGIONAL", "SQLSRV-SSD-REGIONAL"} <= set(_by_key(regional))
    assert regional.monthly_total > zonal.monthly_total


def test_storage_type_selects_its_own_sku() -> None:
    estimator, _ = _estimator()
    result = estimator.estimate(_sqlserver_request(storage_type="PD_HDD"))

    assert isinstance(result, CostEstimate), getattr(result, "message", "")
    priced = set(_by_key(result))
    assert "SQLSRV-HDD-ZONAL" in priced
    assert "SQLSRV-SSD-ZONAL" not in priced


def test_postgres_request_never_reaches_the_other_engines() -> None:
    estimator, _ = _estimator()
    result = estimator.estimate(
        CostEstimateRequest(
            kind="postgres",
            variant="cloudsql",
            region=REGION,
            size="custom",
            config={"tier": "db-custom-2-7680", "storage_gb": "50"},
            expected_usage={"hours_per_month": 720.0},
        )
    )

    assert isinstance(result, CostEstimate), getattr(result, "message", "")
    assert set(_by_key(result)) == {"PG-CPU-ZONAL", "PG-RAM-ZONAL", "PG-SSD-ZONAL"}


def test_backup_and_egress_are_priced_only_when_requested() -> None:
    estimator, _ = _estimator()
    request = _sqlserver_request()
    without_extras = estimator.estimate(
        CostEstimateRequest(
            kind=request.kind,
            variant=request.variant,
            region=request.region,
            size=request.size,
            config=request.config,
            expected_usage={"hours_per_month": 720.0},
        )
    )
    with_egress = estimator.estimate(
        CostEstimateRequest(
            kind=request.kind,
            variant=request.variant,
            region=request.region,
            size=request.size,
            config=request.config,
            expected_usage={"hours_per_month": 720.0, "network_egress_gb": 200.0},
        )
    )

    assert isinstance(without_extras, CostEstimate), getattr(without_extras, "message", "")
    assert "SQLSRV-BACKUP" not in _by_key(without_extras)
    assert "NET-EGRESS" not in _by_key(without_extras)
    assert isinstance(with_egress, CostEstimate), getattr(with_egress, "message", "")
    assert _by_key(with_egress)["NET-EGRESS"] == pytest.approx(2.0)  # 200 GiB * 0.010


def test_express_edition_has_no_licence_line() -> None:
    estimator, _ = _estimator()
    result = estimator.estimate(_sqlserver_request(engine_version="SQLSERVER_2022_EXPRESS"))

    assert isinstance(result, CostEstimate), getattr(result, "message", "")
    assert not any(item.sku.startswith("SQLSRV-LIC") for item in result.line_items)
    assert any("Express" in note for note in result.notes)


def test_line_items_carry_the_catalog_sku_and_description() -> None:
    estimator, _ = _estimator()
    result = estimator.estimate(_sqlserver_request())

    assert isinstance(result, CostEstimate)
    vcpu = next(item for item in result.line_items if item.sku == "SQLSRV-CPU-ZONAL")
    assert "Cloud SQL for SQL Server: Zonal - vCPU in Americas" in vcpu.notes
    assert "SQLSRV-CPU-ZONAL" in vcpu.notes
    assert "0.059000" in vcpu.label


def test_estimate_reads_the_live_catalog_for_the_cloud_sql_service() -> None:
    estimator, client = _estimator()
    result = estimator.estimate(_sqlserver_request())

    assert client.parents == [f"services/{CLOUD_SQL_SERVICE_ID}"]
    assert isinstance(result, CostEstimate)
    assert result.pricing_source_url.endswith(f"/{CLOUD_SQL_SERVICE_ID}/skus")
    assert result.pricing_fetched_at


def test_catalog_failure_is_refused_not_guessed() -> None:
    class ExplodingClient(FakeCatalogClient):
        def list_skus(self, *, parent: str) -> list[FakeSku]:
            raise RuntimeError("403 quota exceeded")

    client = ExplodingClient(catalog())
    estimator = GCPCostEstimator(config=GCPCostConfig(billing_client=client, cache_ttl_seconds=0))
    result = estimator.estimate(_sqlserver_request())

    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "api_error"


def test_unknown_tier_is_refused() -> None:
    """Shared-core tiers bill as a single per-instance SKU, so the
    vCPU + RAM decomposition does not apply."""
    estimator, _ = _estimator()
    result = estimator.estimate(_sqlserver_request(tier="db-f1-micro"))

    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"
    assert "db-f1-micro" in result.message


def test_missing_licence_sku_refuses_the_whole_estimate() -> None:
    """A SQL Server instance without a resolvable licence rate is not
    a cheaper instance, it is an unknown one."""
    skus = [sku for sku in catalog() if not sku.sku_id.startswith("SQLSRV-LIC")]
    estimator, _ = _estimator(skus)
    result = estimator.estimate(_sqlserver_request())

    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"
    assert "license" in result.message


def test_unpriced_sku_refuses_rather_than_pricing_at_zero() -> None:
    skus = [sku for sku in catalog() if sku.sku_id != "SQLSRV-CPU-ZONAL"]
    skus.append(_sku("SQLSRV-CPU-ZONAL", "Cloud SQL for SQL Server: Zonal - vCPU in Americas", 0))
    estimator, _ = _estimator(skus)
    result = estimator.estimate(_sqlserver_request())

    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"
    assert "vcpu" in result.message


def test_ambiguous_machine_series_is_refused_until_narrowed() -> None:
    skus = [
        *catalog(),
        _sku("SQLSRV-CPU-ZONAL-N2", "Cloud SQL for SQL Server: Zonal - N2 vCPU in Americas", 65_000_000),
        _sku(
            "SQLSRV-RAM-ZONAL-N2",
            "Cloud SQL for SQL Server: Zonal - N2 RAM in Americas",
            12_000_000,
            usage_unit="GiBy.h",
        ),
    ]
    estimator, _ = _estimator(skus)
    ambiguous = estimator.estimate(_sqlserver_request())

    assert isinstance(ambiguous, CostEstimateUnavailable)
    assert ambiguous.reason == "ambiguous_sku"
    assert "SQLSRV-CPU-ZONAL-N2" in ambiguous.message

    estimator, _ = _estimator(skus)
    narrowed = estimator.estimate(_sqlserver_request(machine_series="N2"))
    assert isinstance(narrowed, CostEstimate), getattr(narrowed, "message", "")
    assert "SQLSRV-CPU-ZONAL-N2" in _by_key(narrowed)


def test_missing_storage_capacity_is_refused() -> None:
    estimator, _ = _estimator()
    result = estimator.estimate(_sqlserver_request(storage_gb="0"))

    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"
    assert "storage" in result.message


def test_unknown_storage_type_is_refused() -> None:
    estimator, _ = _estimator()
    result = estimator.estimate(_sqlserver_request(storage_type="PD_EXTREME"))

    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "unsupported"


def test_sqlserver_without_engine_version_is_refused() -> None:
    """The licence rate is edition-specific; guessing one would be a
    fabricated price."""
    estimator, _ = _estimator()
    request = _sqlserver_request()
    config = dict(request.config)
    config.pop("engine_version")
    result = estimator.estimate(
        CostEstimateRequest(
            kind=request.kind,
            variant=request.variant,
            region=request.region,
            size=request.size,
            config=config,
            expected_usage=request.expected_usage,
        )
    )

    assert isinstance(result, CostEstimateUnavailable)
    assert result.reason == "sku_not_found"
    assert "engine_version" in result.message
