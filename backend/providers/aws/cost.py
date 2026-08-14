"""AWS CostEstimator (#78 part of #68).

Pulls SKU prices from the AWS Pricing API (boto3 client('pricing'),
endpoint api.pricing.us-east-1.amazonaws.com). The driver is read-
only — never mutates state.

CRITICAL: never substitute hard-coded prices. The Pricing API call
is the source of truth; failures map to CostEstimateUnavailable.

Compute (#440) — the `(kind=compute, variant=node_hour)` pair asks
the driver for EC2 on-demand pricing for the cheapest instance type
that satisfies the caller's CPU + memory request. The driver pages
EC2 Pricing-API SKUs for the cluster's region, filters to a sane
subset (Linux, shared tenancy, no pre-installed software), and
picks the cheapest SKU whose attributes meet the requested
`expected_usage.cpu_cores` + `expected_usage.memory_gib`. Pricing
is the live hourly rate times the caller's `hours_per_month` (defaulting
to 720h ~= 30 days).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.cost import (
    BillingActualLineItem,
    BillingActualsResult,
    BillingActualsUnavailable,
    CostEstimate,
    CostEstimateRequest,
    CostEstimateUnavailable,
    CostEstimator,
    CostLineItem,
    CostResult,
)

log = logging.getLogger(__name__)

# Tag key the platform stamps on every provisioned cloud resource so
# Cost Explorer's GroupBy can attribute spend back to the binding.
# Mirrors ``core.cloud_tags.TAG_BINDING``. Defining it here keeps the
# vendor module self-contained (no app-side imports).
AWS_BINDING_TAG_KEY = "astrolift.io/binding"


# AWS Pricing API service codes per managed-service kind/variant.
# Any kind/variant not in this map returns CostEstimateUnavailable
# rather than guessing.
SERVICE_CODE_BY_VARIANT: dict[tuple[str, str], str] = {
    ("object_store", "s3"): "AmazonS3",
    ("queue", "sqs"): "AWSQueueService",
    ("postgres", "rds"): "AmazonRDS",
    ("postgres", "aurora"): "AmazonRDS",
    ("postgres", "aurora_postgres"): "AmazonRDS",
    ("postgres", "aurora_postgres_serverless_v2"): "AmazonRDS",
    ("mysql", "rds_mysql"): "AmazonRDS",
    ("mysql", "aurora_mysql"): "AmazonRDS",
    ("mysql", "aurora_mysql_serverless_v2"): "AmazonRDS",
    ("mssql", "rds_sqlserver_express"): "AmazonRDS",
    ("mssql", "rds_sqlserver_web"): "AmazonRDS",
    ("mssql", "rds_sqlserver_standard"): "AmazonRDS",
    ("mssql", "rds_sqlserver_enterprise"): "AmazonRDS",
    ("database_proxy", "rds_proxy"): "AmazonRDS",
    ("redis", "elasticache"): "AmazonElastiCache",
    ("redis", "elasticache_valkey"): "AmazonElastiCache",
    ("redis", "elasticache_serverless_valkey"): "AmazonElastiCache",
    ("redis", "elasticache_serverless_redis"): "AmazonElastiCache",
    ("redis", "memorydb"): "AmazonMemoryDB",
    ("cache", "elasticache_serverless_memcached"): "AmazonElastiCache",
    ("cache", "elasticache_memcached"): "AmazonElastiCache",
    ("nosql", "dynamodb"): "AmazonDynamoDB",
    ("kv_store", "dynamodb"): "AmazonDynamoDB",
    ("search", "opensearch"): "AmazonES",
    ("search", "opensearch_serverless"): "AmazonES",
    ("vector_index", "opensearch_vector"): "AmazonES",
    ("vector_index", "opensearch_serverless_vector"): "AmazonES",
    ("document_db", "documentdb"): "AmazonDocDB",
    ("document_db", "documentdb_serverless_v2"): "AmazonDocDB",
    ("wide_column", "keyspaces"): "AmazonMCS",
    ("filesystem", "efs"): "AmazonEFS",
    # Cluster compute capacity (#440). Translates a (cpu_cores,
    # memory_gib) request into the cheapest matching EC2 on-demand
    # SKU in the cluster's region.
    ("compute", "node_hour"): "AmazonEC2",
}

# Pricing API endpoint reference (for provenance + audit trail)
PRICING_ENDPOINT = "https://api.pricing.us-east-1.amazonaws.com"

# Pricing-API pagination cap. EC2 lists thousands of SKUs per region;
# 100 per page keeps the per-call payload bounded while letting the
# pager finish in a reasonable number of round trips. Production tuning
# can lift this when a region's full inventory is needed; tests inject
# a fake client and ignore the limit.
_EC2_PAGE_SIZE = 100

# How many EC2 SKU pages to walk per estimate. Each region has a few
# thousand price records (instance type * OS * tenancy * software);
# 50 pages * 100 = 5k records is enough headroom to find the cheapest
# fitting Linux/shared SKU without paying for unbounded paging on
# malformed responses.
_EC2_PAGE_LIMIT = 50


@dataclass(frozen=True)
class AWSCostConfig:
    pricing_client: Any | None = None
    """boto3 client('pricing'). Inject for tests."""

    cache_ttl_seconds: int = 6 * 60 * 60
    """How long an EC2 SKU page set is cached per (region, filters)
    tuple. Pricing tables don't churn often (AWS updates published
    rates on a roughly-monthly cadence); 6h keeps the preview-cost
    column responsive without pinning a stale view across an SKU
    revision. Set to 0 to disable caching (tests use this)."""


class AWSCostEstimator(CostEstimator):
    def __init__(self, *, config: AWSCostConfig) -> None:
        self._config = config
        if config.pricing_client is not None:
            self._client = config.pricing_client
        else:
            import boto3

            # Pricing API only lives in us-east-1 + ap-south-1.
            self._client = boto3.client(
                "pricing",
                region_name="us-east-1",
            )
        # In-process cache of EC2 SKU sweeps. Keyed on the immutable
        # filter tuple so concurrent estimates for the same region
        # share one Pricing-API call. Lock guards the dict; the cached
        # value itself is immutable.
        self._ec2_sku_cache: dict[
            tuple[tuple[str, str], ...],
            tuple[float, list[dict[str, Any]]],
        ] = {}
        self._ec2_sku_lock = threading.Lock()

    @driver_op(cloud="aws", driver="cost", heartbeat=False)
    def supported(self, *, kind: str, variant: str) -> bool:
        return (kind, variant) in SERVICE_CODE_BY_VARIANT

    @driver_op(cloud="aws", driver="cost")
    def estimate(self, request: CostEstimateRequest) -> CostResult:
        service_code = SERVICE_CODE_BY_VARIANT.get(
            (request.kind, request.variant),
        )
        if service_code is None:
            return CostEstimateUnavailable(
                request=request,
                reason="unsupported",
                message=(f"no AWS Pricing API service code mapped for ({request.kind!r}, {request.variant!r})"),
            )

        if request.kind == "compute" and request.variant == "node_hour":
            return self._estimate_compute_node_hour(
                request=request,
                service_code=service_code,
            )

        filters = self._filters_for(request=request)
        try:
            response = self._client.get_products(
                ServiceCode=service_code,
                Filters=filters,
                MaxResults=20,
            )
        except Exception as exc:
            # #616 -- log the API failure so the cost-estimate
            # dead-zone has a debuggable trace. The estimator returns a
            # structured Unavailable result so callers can still degrade,
            # but the log is the only signal an operator gets when the
            # Pricing API call started rejecting traffic.
            log.warning(
                "aws cost estimate failed",
                extra={
                    "service_code": service_code,
                    "kind": request.kind,
                    "variant": request.variant,
                    "exception_type": type(exc).__name__,
                },
                exc_info=True,
            )
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=f"pricing API call failed: {exc}",
            )

        line_items = self._extract_line_items(
            response=response,
            request=request,
        )
        if not line_items:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(f"no SKU matched filters {filters} on service {service_code}"),
            )

        total = sum(item.monthly_amount for item in line_items)
        return CostEstimate(
            request=request,
            line_items=line_items,
            monthly_total=round(total, 2),
            currency=request.currency,
            pricing_source_url=(f"{PRICING_ENDPOINT}/?ServiceCode={service_code}"),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "AWS Pricing API returns on-demand list price; "
                "savings plans / reserved instances are not "
                "applied — actual bill may be lower.",
            ],
        )

    # ---- compute / node_hour ------------------------------------------

    def _estimate_compute_node_hour(
        self,
        *,
        request: CostEstimateRequest,
        service_code: str,
    ) -> CostResult:
        """Pick the cheapest EC2 on-demand SKU whose vCPU + memory
        meet the caller's request, and return the hourly cost times
        the caller's `hours_per_month` (default 720h = ~30 days).

        Uses live Pricing-API responses only — no hard-coded SKU
        table. Region is mapped to the Pricing-API `location`
        long-form on the way out; tenancy + OS + pre-installed
        software are pinned so the SKU pool stays apples-to-apples.
        """
        location = request.config.get("location") or _region_to_location(
            request.region,
        )
        if not location:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(f"no Pricing-API location mapping for region {request.region!r}; pass config['location']"),
            )

        cpu_cores = float(
            request.expected_usage.get("cpu_cores", 0.0) or 0.0,
        )
        memory_gib = float(
            request.expected_usage.get("memory_gib", 0.0) or 0.0,
        )
        if cpu_cores <= 0.0 and memory_gib <= 0.0:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=("compute node_hour estimate needs cpu_cores or memory_gib in expected_usage"),
            )

        filters = self._compute_filters(
            location=location,
            request=request,
        )

        try:
            sku_pages = self._fetch_ec2_skus(
                service_code=service_code,
                filters=filters,
            )
        except Exception as exc:
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=f"pricing API call failed: {exc}",
            )

        cheapest = self._select_cheapest_ec2_sku(
            sku_pages=sku_pages,
            cpu_cores=cpu_cores,
            memory_gib=memory_gib,
            currency=request.currency,
        )
        if cheapest is None:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(f"no EC2 SKU in {location} fits cpu_cores={cpu_cores} memory_gib={memory_gib}"),
            )

        hourly_price, sku_id, instance_type, vcpu, memory = cheapest
        hours_per_month = float(
            request.expected_usage.get("hours_per_month", 720.0) or 720.0,
        )
        monthly = hourly_price * hours_per_month

        line_item = CostLineItem(
            label=(f"EC2 on-demand {instance_type} ({vcpu} vCPU, {memory} GiB) — ${hourly_price:.4f}/hr"),
            sku=sku_id,
            monthly_amount=round(monthly, 4),
            currency=request.currency,
            notes="Hrs",
        )
        return CostEstimate(
            request=request,
            line_items=[line_item],
            monthly_total=round(monthly, 2),
            currency=request.currency,
            pricing_source_url=(f"{PRICING_ENDPOINT}/?ServiceCode={service_code}&location={location}"),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "AWS Pricing API returns on-demand list price; "
                "savings plans / reserved instances are not applied — "
                "actual bill may be lower.",
                (f"selected cheapest Linux shared-tenancy SKU fitting cpu_cores={cpu_cores} memory_gib={memory_gib}"),
            ],
        )

    def _compute_filters(
        self,
        *,
        location: str,
        request: CostEstimateRequest,
    ) -> list[dict[str, str]]:
        """EC2 on-demand pricing filters that keep the SKU pool
        apples-to-apples across instance families.

        Linux + shared tenancy + no-pre-installed-software keeps us
        on the same base rate the EKS managed node groups
        consume. `capacitystatus=Used` excludes reserved-capacity
        and unused-capacity SKUs which aren't relevant to a runtime
        cost estimate. Caller can layer extra filters via
        ``request.config`` (e.g. ``instanceFamily=m6i``).
        """
        filters: list[dict[str, str]] = [
            {"Type": "TERM_MATCH", "Field": "location", "Value": location},
            {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
            {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
            {
                "Type": "TERM_MATCH",
                "Field": "preInstalledSw",
                "Value": "NA",
            },
            {
                "Type": "TERM_MATCH",
                "Field": "capacitystatus",
                "Value": "Used",
            },
            {
                "Type": "TERM_MATCH",
                "Field": "licenseModel",
                "Value": "No License required",
            },
        ]
        for key, value in request.config.items():
            if key in {"location"}:
                continue
            filters.append(
                {
                    "Type": "TERM_MATCH",
                    "Field": key,
                    "Value": value,
                }
            )
        return filters

    def _fetch_ec2_skus(
        self,
        *,
        service_code: str,
        filters: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Page through EC2 Pricing-API products with caching.

        Returns the flat list of product dicts (parsed JSON). Cache
        is keyed on the filter tuple so a re-estimate for the same
        region + restrictions reuses the previous sweep.
        """
        cache_key = tuple((f["Field"], f["Value"]) for f in filters)
        ttl = self._config.cache_ttl_seconds
        now = datetime.now(tz=UTC).timestamp()

        if ttl > 0:
            with self._ec2_sku_lock:
                cached = self._ec2_sku_cache.get(cache_key)
                if cached is not None:
                    fetched_at, products = cached
                    if (now - fetched_at) < ttl:
                        return products

        products: list[dict[str, Any]] = []
        next_token: str | None = None
        for _ in range(_EC2_PAGE_LIMIT):
            kwargs: dict[str, Any] = {
                "ServiceCode": service_code,
                "Filters": filters,
                "MaxResults": _EC2_PAGE_SIZE,
            }
            if next_token:
                kwargs["NextToken"] = next_token
            response = self._client.get_products(**kwargs)
            for raw in response.get("PriceList", []) or []:
                if isinstance(raw, str):
                    try:
                        products.append(json.loads(raw))
                    except ValueError:
                        # Skip malformed product entries; one
                        # bad SKU shouldn't blow the whole sweep.
                        continue
                else:
                    products.append(raw)
            next_token = response.get("NextToken")
            if not next_token:
                break

        if ttl > 0:
            with self._ec2_sku_lock:
                self._ec2_sku_cache[cache_key] = (now, products)
        return products

    def _select_cheapest_ec2_sku(
        self,
        *,
        sku_pages: list[dict[str, Any]],
        cpu_cores: float,
        memory_gib: float,
        currency: str,
    ) -> tuple[float, str, str, int, float] | None:
        """Scan the sweep for the cheapest SKU whose vCPU + memory
        clear the caller's request. Returns
        ``(hourly_price, sku_id, instance_type, vcpu, memory_gib)``
        or None when nothing matches.
        """
        best: tuple[float, str, str, int, float] | None = None
        for product in sku_pages:
            attrs = product.get("product", {}).get("attributes", {}) or {}
            instance_type = attrs.get("instanceType", "")
            if not instance_type:
                continue
            try:
                vcpu = int(attrs.get("vcpu", "0") or 0)
            except ValueError:
                continue
            try:
                memory_str = (attrs.get("memory", "0") or "0").split()[0]
                memory = float(memory_str.replace(",", ""))
            except (ValueError, IndexError):
                continue
            if vcpu < cpu_cores or memory < memory_gib:
                continue

            hourly_price = self._first_hourly_price(
                product=product,
                currency=currency,
            )
            if hourly_price is None or hourly_price <= 0.0:
                continue

            sku_id = product.get("product", {}).get("sku", "") or instance_type
            candidate = (hourly_price, sku_id, instance_type, vcpu, memory)
            if best is None or hourly_price < best[0]:
                best = candidate
        return best

    def _first_hourly_price(
        self,
        *,
        product: dict[str, Any],
        currency: str,
    ) -> float | None:
        """Pull the first on-demand per-hour rate off a product, or
        None when the price dimension isn't hourly / isn't priced
        in the requested currency."""
        terms = product.get("terms", {}).get("OnDemand", {}) or {}
        for term in terms.values():
            for dim in (term.get("priceDimensions", {}) or {}).values():
                unit = (dim.get("unit", "") or "").lower()
                if "hr" not in unit and "hour" not in unit:
                    continue
                price_str = (dim.get("pricePerUnit", {}) or {}).get(
                    currency,
                    "0",
                )
                try:
                    return float(price_str)
                except ValueError:
                    continue
        return None

    # ---- managed-service path (existing) ------------------------------

    def _filters_for(
        self,
        *,
        request: CostEstimateRequest,
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
            filters.append(
                {
                    "Type": "TERM_MATCH",
                    "Field": "location",
                    "Value": location,
                }
            )
        # Per-variant additional filters (instance class for RDS,
        # storage class for S3, etc.) come from request.config so
        # the caller drives.
        for key, value in request.config.items():
            if key in {"location"}:
                continue
            filters.append(
                {
                    "Type": "TERM_MATCH",
                    "Field": key,
                    "Value": value,
                }
            )
        return filters

    def _extract_line_items(
        self,
        *,
        response: dict[str, Any],
        request: CostEstimateRequest,
    ) -> list[CostLineItem]:
        items: list[CostLineItem] = []
        for raw in response.get("PriceList", []):
            product = json.loads(raw) if isinstance(raw, str) else raw
            terms = product.get("terms", {}).get("OnDemand", {})
            attrs = product.get("product", {}).get("attributes", {})
            sku = product.get("product", {}).get("sku", "")
            for term in terms.values():
                for dim in term.get("priceDimensions", {}).values():
                    price_str = dim.get("pricePerUnit", {}).get(
                        request.currency,
                        "0",
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
                    items.append(
                        CostLineItem(
                            label=dim.get("description", attrs.get("usagetype", "AWS line item")),
                            sku=sku,
                            monthly_amount=round(monthly, 4),
                            currency=request.currency,
                            notes=dim.get("unit", ""),
                        )
                    )
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


# ---- billing actuals (#502) ---------------------------------------


@dataclass(frozen=True)
class AWSBillingActualsConfig:
    """Per-cloud config for the actuals client. ``ce_client`` is an
    injectable boto3 ``client('ce')`` so tests can stub the Cost
    Explorer responses without hitting AWS."""

    ce_client: Any | None = None
    """boto3 client('ce'). Inject for tests."""


class AWSBillingActuals:
    """Reads actual AWS spend grouped by the ``astrolift.io/binding``
    tag via Cost Explorer's ``GetCostAndUsage`` API.

    Cost Explorer accepts ``GroupBy=[{Type:'TAG', Key:'astrolift.io/binding'}]``
    which returns one ``Group`` per distinct tag value plus a
    ``$untagged`` group for resources missing the tag. The client
    converts each group's amortized cost into integer cents and emits
    one :class:`BillingActualLineItem` per (tag value, currency) pair.

    Cost Explorer requires the account to be opted in to cost
    allocation tags AND the tag to be activated for cost allocation;
    a fresh AWS account will return :class:`BillingActualsUnavailable`
    with ``reason='not_enabled'`` until the operator activates the
    tag. The error message points at the AWS console path.
    """

    def __init__(self, *, config: AWSBillingActualsConfig | None = None) -> None:
        cfg = config or AWSBillingActualsConfig()
        if cfg.ce_client is not None:
            self._client = cfg.ce_client
        else:
            import boto3

            # Cost Explorer is global but the boto3 client expects a
            # region — us-east-1 is the canonical home.
            self._client = boto3.client("ce", region_name="us-east-1")

    @driver_op(cloud="aws", driver="cost")
    def query_actuals_by_binding(
        self,
        *,
        start: date,
        end: date,
        currency: str = "USD",
    ) -> BillingActualsResult:
        if start >= end:
            return BillingActualsUnavailable(
                reason="api_error",
                message=f"start={start} must be before end={end}",
            )
        try:
            response = self._client.get_cost_and_usage(
                TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                Granularity="DAILY",
                Metrics=["AmortizedCost"],
                GroupBy=[{"Type": "TAG", "Key": AWS_BINDING_TAG_KEY}],
            )
        except Exception as exc:
            msg = str(exc)
            # AWS surfaces "is not enabled as a cost allocation tag"
            # as a DataUnavailableException; distinguish so the caller
            # can show a clearer enablement hint.
            if "not enabled" in msg.lower() or "cost allocation" in msg.lower():
                return BillingActualsUnavailable(
                    reason="not_enabled",
                    message=(
                        f"Activate {AWS_BINDING_TAG_KEY!r} as a cost-allocation tag "
                        f"in Billing → Cost allocation tags. Underlying: {exc}"
                    ),
                )
            return BillingActualsUnavailable(
                reason="api_error",
                message=f"Cost Explorer GetCostAndUsage failed: {exc}",
            )

        # Sum across the daily buckets — the caller asks for total
        # spend in the window, not per-day. ``ResultsByTime`` is a
        # list of {TimePeriod, Total, Groups}; only Groups are
        # interesting for the tag breakdown.
        totals_cents: dict[str, int] = {}
        for bucket in response.get("ResultsByTime", []) or []:
            for group in bucket.get("Groups", []) or []:
                # Keys come back as ['astrolift.io/binding$<value>']
                # ('$' between tag key and value). Missing tags
                # surface as ['astrolift.io/binding$'] (no value)
                # which we map to the untagged bucket.
                raw_key = (group.get("Keys") or [""])[0]
                _, _, tag_value = raw_key.partition("$")
                amount_raw = group.get("Metrics", {}).get("AmortizedCost", {}).get("Amount", "0")
                try:
                    amount = float(amount_raw)
                except (TypeError, ValueError):
                    continue
                if amount <= 0:
                    continue
                cents = round(amount * 100)
                totals_cents[tag_value] = totals_cents.get(tag_value, 0) + cents

        if not totals_cents:
            return []
        return [
            BillingActualLineItem(
                binding_guid=tag_value,
                amount_cents=cents,
                currency=currency,
                provider="aws",
                service="",
            )
            for tag_value, cents in totals_cents.items()
        ]


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
