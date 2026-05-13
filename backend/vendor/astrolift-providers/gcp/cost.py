"""GCP CostEstimator (#78 part of #69).

Pulls SKU prices from the Cloud Billing Catalog API
(cloudbilling.googleapis.com/v1/services/{service}/skus). The
driver is read-only.

CRITICAL: never substitute hard-coded prices. The Catalog API call
is the source of truth; failures map to CostEstimateUnavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.cost import (
    CostEstimate,
    CostEstimateRequest,
    CostEstimateUnavailable,
    CostEstimator,
    CostLineItem,
    CostResult,
)


# GCP Cloud Billing service IDs.
SERVICE_ID_BY_VARIANT: dict[tuple[str, str], str] = {
    ("object_store", "gcs"): "95FF-2EF5-5EA1",  # Cloud Storage
    ("queue", "pubsub"): "A1E8-BE35-7EBC",      # Pub/Sub
    ("postgres", "cloudsql"): "9662-B51E-5089",  # Cloud SQL
    ("postgres", "alloydb"): "70A4-7A89-3F8F",   # AlloyDB
    ("redis", "memorystore"): "F25A-3A0D-5DDB",  # Memorystore
    ("nosql", "firestore"): "F17B-412E-CB64",    # Firestore
    ("nosql", "bigtable"): "FD83-CFB8-A3CB",     # Bigtable
}

CATALOG_BASE = (
    "https://cloudbilling.googleapis.com/v1/services"
)


@dataclass(frozen=True)
class GCPCostConfig:
    billing_client: Any | None = None
    """Cloud Billing Catalog client. Inject for tests."""


class GCPCostEstimator(CostEstimator):
    def __init__(self, *, config: GCPCostConfig) -> None:
        self._config = config
        if config.billing_client is not None:
            self._client = config.billing_client
        else:
            from google.cloud import billing_v1

            self._client = billing_v1.CloudCatalogClient()

    def supported(self, *, kind: str, variant: str) -> bool:
        return (kind, variant) in SERVICE_ID_BY_VARIANT

    def estimate(self, request: CostEstimateRequest) -> CostResult:
        service_id = SERVICE_ID_BY_VARIANT.get(
            (request.kind, request.variant),
        )
        if service_id is None:
            return CostEstimateUnavailable(
                request=request,
                reason="unsupported",
                message=(
                    f"no GCP Catalog service ID for "
                    f"({request.kind!r}, {request.variant!r})"
                ),
            )
        try:
            skus = self._client.list_skus(
                parent=f"services/{service_id}",
            )
        except Exception as exc:  # noqa: BLE001
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=f"Catalog API call failed: {exc}",
            )

        line_items = self._extract_line_items(
            skus=skus, request=request,
        )
        if not line_items:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(
                    f"no SKUs matched region {request.region!r} "
                    f"in service {service_id}"
                ),
            )

        from datetime import UTC, datetime

        total = sum(item.monthly_amount for item in line_items)
        return CostEstimate(
            request=request,
            line_items=line_items,
            monthly_total=round(total, 2),
            currency=request.currency,
            pricing_source_url=(
                f"{CATALOG_BASE}/{service_id}/skus"
            ),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "GCP Cloud Catalog returns list price; CUDs and "
                "sustained-use discounts are not applied.",
            ],
        )

    def _extract_line_items(
        self,
        *,
        skus: Any,
        request: CostEstimateRequest,
    ) -> list[CostLineItem]:
        items: list[CostLineItem] = []
        for sku in skus:
            regions = list(getattr(sku, "service_regions", []) or [])
            if regions and request.region not in regions and "global" not in regions:
                continue
            pricing_info_list = list(
                getattr(sku, "pricing_info", []) or [],
            )
            if not pricing_info_list:
                continue
            tiered = pricing_info_list[0].pricing_expression
            unit_price = self._first_tier_price(
                tiered=tiered, currency=request.currency,
            )
            if unit_price is None:
                continue
            usage_unit = getattr(tiered, "usage_unit", "")
            monthly = self._monthly_amount(
                unit_price=unit_price,
                usage_unit=usage_unit,
                request=request,
            )
            items.append(CostLineItem(
                label=getattr(sku, "description", "GCP line item"),
                sku=getattr(sku, "sku_id", "") or getattr(sku, "name", ""),
                monthly_amount=round(monthly, 4),
                currency=request.currency,
                notes=usage_unit,
            ))
        return items

    def _first_tier_price(
        self, *, tiered: Any, currency: str,
    ) -> float | None:
        for tier in getattr(tiered, "tiered_rates", []) or []:
            unit_price = getattr(tier, "unit_price", None)
            if unit_price is None:
                continue
            if getattr(unit_price, "currency_code", "") != currency:
                continue
            units = getattr(unit_price, "units", 0) or 0
            nanos = getattr(unit_price, "nanos", 0) or 0
            return float(units) + (float(nanos) / 1_000_000_000)
        return None

    def _monthly_amount(
        self,
        *,
        unit_price: float,
        usage_unit: str,
        request: CostEstimateRequest,
    ) -> float:
        usage = request.expected_usage
        unit_lower = usage_unit.lower()
        if "gibibyte month" in unit_lower or "gib mo" in unit_lower:
            return unit_price * usage.get("storage_gb_month", 0.0)
        if "request" in unit_lower:
            return unit_price * usage.get("requests_per_month", 0.0)
        if "hour" in unit_lower:
            return unit_price * usage.get("hours_per_month", 720.0)
        return unit_price
