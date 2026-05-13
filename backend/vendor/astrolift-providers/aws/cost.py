"""AWS CostEstimator (#78 part of #68).

Pulls SKU prices from the AWS Pricing API (boto3 client('pricing'),
endpoint api.pricing.us-east-1.amazonaws.com). The driver is read-
only — never mutates state.

CRITICAL: never substitute hard-coded prices. The Pricing API call
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


# AWS Pricing API service codes per managed-service kind/variant.
# Any kind/variant not in this map returns CostEstimateUnavailable
# rather than guessing.
SERVICE_CODE_BY_VARIANT: dict[tuple[str, str], str] = {
    ("object_store", "s3"): "AmazonS3",
    ("queue", "sqs"): "AWSQueueService",
    ("postgres", "rds"): "AmazonRDS",
    ("postgres", "aurora"): "AmazonRDS",
    ("redis", "elasticache"): "AmazonElastiCache",
    ("nosql", "dynamodb"): "AmazonDynamoDB",
    ("filesystem", "efs"): "AmazonEFS",
}

# Pricing API endpoint reference (for provenance + audit trail)
PRICING_ENDPOINT = (
    "https://api.pricing.us-east-1.amazonaws.com"
)


@dataclass(frozen=True)
class AWSCostConfig:
    pricing_client: Any | None = None
    """boto3 client('pricing'). Inject for tests."""


class AWSCostEstimator(CostEstimator):
    def __init__(self, *, config: AWSCostConfig) -> None:
        self._config = config
        if config.pricing_client is not None:
            self._client = config.pricing_client
        else:
            import boto3

            # Pricing API only lives in us-east-1 + ap-south-1.
            self._client = boto3.client(
                "pricing", region_name="us-east-1",
            )

    def supported(self, *, kind: str, variant: str) -> bool:
        return (kind, variant) in SERVICE_CODE_BY_VARIANT

    def estimate(self, request: CostEstimateRequest) -> CostResult:
        service_code = SERVICE_CODE_BY_VARIANT.get(
            (request.kind, request.variant),
        )
        if service_code is None:
            return CostEstimateUnavailable(
                request=request,
                reason="unsupported",
                message=(
                    f"no AWS Pricing API service code mapped for "
                    f"({request.kind!r}, {request.variant!r})"
                ),
            )

        filters = self._filters_for(request=request)
        try:
            response = self._client.get_products(
                ServiceCode=service_code,
                Filters=filters,
                MaxResults=20,
            )
        except Exception as exc:  # noqa: BLE001
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=f"pricing API call failed: {exc}",
            )

        line_items = self._extract_line_items(
            response=response, request=request,
        )
        if not line_items:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(
                    f"no SKU matched filters {filters} on service "
                    f"{service_code}"
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
                f"{PRICING_ENDPOINT}/?ServiceCode={service_code}"
            ),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "AWS Pricing API returns on-demand list price; "
                "savings plans / reserved instances are not "
                "applied — actual bill may be lower.",
            ],
        )

    def _filters_for(
        self, *, request: CostEstimateRequest,
    ) -> list[dict[str, str]]:
        # Region maps via location attribute. The Pricing API's
        # 'location' is the long-form ('US East (N. Virginia)'),
        # not the short region code; production wires a lookup
        # table. For brevity here we let the caller pass the
        # long-form via config['location'] when needed.
        filters: list[dict[str, str]] = []
        location = request.config.get("location") or _region_to_location(
            request.region,
        )
        if location:
            filters.append({
                "Type": "TERM_MATCH",
                "Field": "location",
                "Value": location,
            })
        # Per-variant additional filters (instance class for RDS,
        # storage class for S3, etc.) come from request.config so
        # the caller drives.
        for key, value in request.config.items():
            if key in {"location"}:
                continue
            filters.append({
                "Type": "TERM_MATCH",
                "Field": key,
                "Value": value,
            })
        return filters

    def _extract_line_items(
        self,
        *,
        response: dict[str, Any],
        request: CostEstimateRequest,
    ) -> list[CostLineItem]:
        import json

        items: list[CostLineItem] = []
        for raw in response.get("PriceList", []):
            product = json.loads(raw) if isinstance(raw, str) else raw
            terms = product.get("terms", {}).get("OnDemand", {})
            attrs = product.get("product", {}).get("attributes", {})
            sku = product.get("product", {}).get("sku", "")
            for term in terms.values():
                for dim in term.get("priceDimensions", {}).values():
                    price_str = (
                        dim.get("pricePerUnit", {}).get(
                            request.currency, "0",
                        )
                    )
                    try:
                        unit_price = float(price_str)
                    except ValueError:
                        continue
                    monthly = self._monthly_amount(
                        unit_price=unit_price,
                        unit=dim.get("unit", ""),
                        request=request,
                    )
                    items.append(CostLineItem(
                        label=dim.get("description", attrs.get("usagetype", "AWS line item")),
                        sku=sku,
                        monthly_amount=round(monthly, 4),
                        currency=request.currency,
                        notes=dim.get("unit", ""),
                    ))
        return items

    def _monthly_amount(
        self,
        *,
        unit_price: float,
        unit: str,
        request: CostEstimateRequest,
    ) -> float:
        """Translate the API's per-unit price + the caller's
        expected_usage into a monthly amount. Drivers MUST pass
        usage estimates in the request — without them this returns
        the per-unit price as a placeholder."""
        usage = request.expected_usage
        unit_lower = unit.lower()
        if "gb-mo" in unit_lower or "gb-month" in unit_lower:
            return unit_price * usage.get("storage_gb_month", 0.0)
        if "request" in unit_lower:
            return unit_price * usage.get("requests_per_month", 0.0)
        if "hour" in unit_lower:
            return unit_price * usage.get("hours_per_month", 720.0)
        return unit_price


def _region_to_location(region: str) -> str:
    """Map AWS region code → Pricing API 'location' string.
    Production wiring uses the full ssm:GetParametersByPath
    /aws/service/global-infrastructure/regions table; the subset
    here covers common regions for tests / dev paths."""
    table = {
        "us-east-1": "US East (N. Virginia)",
        "us-east-2": "US East (Ohio)",
        "us-west-1": "US West (N. California)",
        "us-west-2": "US West (Oregon)",
        "eu-west-1": "EU (Ireland)",
        "eu-central-1": "EU (Frankfurt)",
        "ap-southeast-1": "Asia Pacific (Singapore)",
        "ap-northeast-1": "Asia Pacific (Tokyo)",
    }
    return table.get(region, "")
