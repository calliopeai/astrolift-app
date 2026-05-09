"""Azure CostEstimator (#78 part of #70).

Pulls SKU prices from the Azure Retail Prices API
(https://prices.azure.com/api/retail/prices). The API is
authenticated-free + region-agnostic — the driver just calls HTTP
GET with OData $filter.

CRITICAL: never substitute hard-coded prices. The Retail Prices API
call is the source of truth; failures map to CostEstimateUnavailable.
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


# Azure service / product names per managed-service kind/variant.
SERVICE_NAME_BY_VARIANT: dict[tuple[str, str], str] = {
    ("object_store", "blob"): "Storage",
    ("queue", "servicebus"): "Service Bus",
    ("postgres", "flexible_server"): (
        "Azure Database for PostgreSQL"
    ),
    ("redis", "cache"): "Redis Cache",
    ("nosql", "cosmos"): "Azure Cosmos DB",
    ("filesystem", "files"): "Storage",
}

PRICES_ENDPOINT = "https://prices.azure.com/api/retail/prices"


@dataclass(frozen=True)
class AzureCostConfig:
    http_client: Any | None = None


class AzureCostEstimator(CostEstimator):
    def __init__(self, *, config: AzureCostConfig) -> None:
        self._config = config
        self._http = config.http_client or _DefaultHttp()

    def supported(self, *, kind: str, variant: str) -> bool:
        return (kind, variant) in SERVICE_NAME_BY_VARIANT

    def estimate(self, request: CostEstimateRequest) -> CostResult:
        service_name = SERVICE_NAME_BY_VARIANT.get(
            (request.kind, request.variant),
        )
        if service_name is None:
            return CostEstimateUnavailable(
                request=request,
                reason="unsupported",
                message=(
                    f"no Azure Retail Prices service name for "
                    f"({request.kind!r}, {request.variant!r})"
                ),
            )

        odata_filter = self._build_filter(
            service_name=service_name, request=request,
        )
        try:
            response = self._http.get(
                PRICES_ENDPOINT,
                params={
                    "$filter": odata_filter,
                    "currencyCode": request.currency,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=f"Retail Prices API call failed: {exc}",
            )

        body = response.json() if hasattr(response, "json") else response
        line_items = self._extract_line_items(
            body=body, request=request,
        )
        if not line_items:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(
                    f"no SKUs matched filter {odata_filter!r}"
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
                f"{PRICES_ENDPOINT}?$filter={odata_filter}"
            ),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "Azure Retail Prices returns list price; "
                "Reservations and Hybrid Benefit are not applied.",
            ],
        )

    def _build_filter(
        self,
        *,
        service_name: str,
        request: CostEstimateRequest,
    ) -> str:
        parts = [f"serviceName eq '{service_name}'"]
        if request.region:
            parts.append(f"armRegionName eq '{request.region}'")
        # Per-variant additional filters via request.config
        for key, value in request.config.items():
            parts.append(f"{key} eq '{value}'")
        return " and ".join(parts)

    def _extract_line_items(
        self,
        *,
        body: dict[str, Any],
        request: CostEstimateRequest,
    ) -> list[CostLineItem]:
        items: list[CostLineItem] = []
        for record in body.get("Items", []) or []:
            unit_price = float(record.get("unitPrice", 0.0) or 0.0)
            if unit_price <= 0.0:
                continue
            usage_unit = record.get("unitOfMeasure", "")
            monthly = self._monthly_amount(
                unit_price=unit_price,
                usage_unit=usage_unit,
                request=request,
            )
            items.append(CostLineItem(
                label=record.get("productName", "Azure line item"),
                sku=record.get("skuId", "") or record.get("meterId", ""),
                monthly_amount=round(monthly, 4),
                currency=request.currency,
                notes=usage_unit,
            ))
        return items

    def _monthly_amount(
        self,
        *,
        unit_price: float,
        usage_unit: str,
        request: CostEstimateRequest,
    ) -> float:
        usage = request.expected_usage
        unit_lower = usage_unit.lower()
        if "gb/month" in unit_lower or "gb-month" in unit_lower:
            return unit_price * usage.get("storage_gb_month", 0.0)
        if "request" in unit_lower or "10k" in unit_lower:
            divisor = 10_000.0 if "10k" in unit_lower else 1.0
            return unit_price * usage.get(
                "requests_per_month", 0.0,
            ) / divisor
        if "hour" in unit_lower:
            return unit_price * usage.get("hours_per_month", 720.0)
        return unit_price


class _DefaultHttp:
    def get(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        import json

        full = url
        if params:
            full = f"{url}?{urlencode(params)}"
        request = Request(full)
        with urlopen(request, timeout=30) as raw:
            payload = raw.read().decode("utf-8")
        return _Response(body=json.loads(payload))


@dataclass
class _Response:
    body: dict[str, Any]

    def json(self) -> Any:
        return self.body
