"""Azure CostEstimator (#78 part of #70).

Pulls SKU prices from the Azure Retail Prices API
(https://prices.azure.com/api/retail/prices). The API is
authenticated-free + region-agnostic — the driver just calls HTTP
GET with OData $filter.

CRITICAL: never substitute hard-coded prices. The Retail Prices API
call is the source of truth; failures map to CostEstimateUnavailable.

Compute (#440) — the `(kind=compute, variant=node_hour)` pair asks
the driver for Virtual Machines pricing. Azure's Retail Prices
response doesn't carry vCPU / memory attributes on each price row,
only the SKU name (``D4s_v3`` etc.), so the driver needs a second
data source for VM capacity. It calls `azure-mgmt-compute`'s
``VirtualMachineSizes.list(location)`` (auth required for that one)
to join SKU -> (vCPU, memory_gib), then picks the cheapest D-series
Consumption price whose capacity meets the caller's request.

Tests inject a fake ``vm_sizes`` lookup so they don't have to model
the SDK.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
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

# Tag key on Azure resources. Azure keeps the platform tag verbatim
# (mixed case + slash allowed) per ``core.cloud_tags.to_azure``.
AZURE_BINDING_TAG_KEY = "astrolift.io/binding"


# Azure service / product names per managed-service kind/variant.
SERVICE_NAME_BY_VARIANT: dict[tuple[str, str], str] = {
    ("object_store", "blob"): "Storage",
    ("queue", "servicebus"): "Service Bus",
    ("postgres", "flexible_server"): ("Azure Database for PostgreSQL"),
    ("redis", "cache"): "Redis Cache",
    ("nosql", "cosmos"): "Azure Cosmos DB",
    ("filesystem", "files"): "Storage",
    # Cluster compute capacity (#440). Retail Prices service name
    # `Virtual Machines` covers all VM SKUs across families; the
    # driver narrows to D-series consumption and joins with a
    # VM-size catalog for the vCPU + memory attributes.
    ("compute", "node_hour"): "Virtual Machines",
}

PRICES_ENDPOINT = "https://prices.azure.com/api/retail/prices"


# Type for the VM-size lookup. A callable that takes the cluster's
# armRegionName and returns a dict mapping armSkuName (e.g.
# ``Standard_D4s_v3``) to ``(vcpu, memory_gib)``. The default impl
# pulls from `azure-mgmt-compute`'s VirtualMachineSizesOperations;
# tests inject a fake.
VmSizeLookup = Callable[[str], dict[str, tuple[int, float]]]


@dataclass(frozen=True)
class AzureCostConfig:
    http_client: Any | None = None
    vm_sizes_lookup: VmSizeLookup | None = None
    """Resolver for armSkuName -> (vcpu, memory_gib). Required for
    compute pricing. The default implementation lazy-loads
    `azure-mgmt-compute` and asks per-region; injectable for tests."""

    cache_ttl_seconds: int = 6 * 60 * 60
    """How long a Retail Prices response is cached per
    (region, filters) tuple. Pricing tables don't churn often;
    6h keeps preview-cost responsive without pinning a stale
    snapshot. Set to 0 to disable (tests use this)."""


class AzureCostEstimator(CostEstimator):
    def __init__(self, *, config: AzureCostConfig) -> None:
        self._config = config
        self._http = config.http_client or _DefaultHttp()
        self._prices_cache: dict[
            tuple[str, str],
            tuple[float, dict[str, Any]],
        ] = {}
        self._prices_lock = threading.Lock()
        self._vm_size_cache: dict[
            str,
            tuple[float, dict[str, tuple[int, float]]],
        ] = {}
        self._vm_size_lock = threading.Lock()

    @driver_op(cloud="azure", driver="cost", heartbeat=False)
    def supported(self, *, kind: str, variant: str) -> bool:
        return (kind, variant) in SERVICE_NAME_BY_VARIANT

    @driver_op(cloud="azure", driver="cost")
    def estimate(self, request: CostEstimateRequest) -> CostResult:
        service_name = SERVICE_NAME_BY_VARIANT.get(
            (request.kind, request.variant),
        )
        if service_name is None:
            return CostEstimateUnavailable(
                request=request,
                reason="unsupported",
                message=(f"no Azure Retail Prices service name for ({request.kind!r}, {request.variant!r})"),
            )

        if request.kind == "compute" and request.variant == "node_hour":
            return self._estimate_compute_node_hour(
                request=request,
                service_name=service_name,
            )

        odata_filter = self._build_filter(
            service_name=service_name,
            request=request,
        )
        try:
            response = self._http.get(
                PRICES_ENDPOINT,
                params={
                    "$filter": odata_filter,
                    "currencyCode": request.currency,
                },
            )
        except Exception as exc:
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=f"Retail Prices API call failed: {exc}",
            )

        body = response.json() if hasattr(response, "json") else response
        line_items = self._extract_line_items(
            body=body,
            request=request,
        )
        if not line_items:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(f"no SKUs matched filter {odata_filter!r}"),
            )

        total = sum(item.monthly_amount for item in line_items)
        return CostEstimate(
            request=request,
            line_items=line_items,
            monthly_total=round(total, 2),
            currency=request.currency,
            pricing_source_url=(f"{PRICES_ENDPOINT}?$filter={odata_filter}"),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "Azure Retail Prices returns list price; Reservations and Hybrid Benefit are not applied.",
            ],
        )

    # ---- compute / node_hour ------------------------------------------

    def _estimate_compute_node_hour(
        self,
        *,
        request: CostEstimateRequest,
        service_name: str,
    ) -> CostResult:
        """Pick the cheapest VM SKU whose vCPU + memory meet the
        caller's request in the cluster's region, then bill at
        the live hourly rate * `expected_usage.hours_per_month`.

        Steps:
          1. Fetch the VM-size catalog (vCPU/memory per armSkuName)
             via the injected lookup; cached per region.
          2. Fetch Retail Prices for the requested region + family
             (default D-series) + Consumption priceType + Linux OS
             (no Windows surcharge).
          3. Join SKU -> price -> capacity; keep the cheapest
             that fits.
        """
        if not request.region:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message="compute node_hour estimate needs region",
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

        # 1. VM size catalog.
        vm_sizes = self._lookup_vm_sizes(request=request)
        if vm_sizes is None:
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=("VM size lookup unavailable — pass AzureCostConfig.vm_sizes_lookup to enable compute pricing"),
            )
        if not vm_sizes:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(f"no VM sizes returned for region {request.region!r}"),
            )

        # 2. Retail prices.
        family_prefix = request.config.get("instance_family", "D")
        odata_filter = self._compute_filter(
            service_name=service_name,
            request=request,
        )
        try:
            body = self._fetch_prices(
                odata_filter=odata_filter,
                currency=request.currency,
            )
        except Exception as exc:
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=f"Retail Prices API call failed: {exc}",
            )

        # 3. Match + pick cheapest.
        cheapest = self._select_cheapest_vm_sku(
            body=body,
            vm_sizes=vm_sizes,
            cpu_cores=cpu_cores,
            memory_gib=memory_gib,
            family_prefix=family_prefix,
        )
        if cheapest is None:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(
                    f"no Azure VM SKU in {request.region!r} fits "
                    f"cpu_cores={cpu_cores} memory_gib={memory_gib} "
                    f"(family prefix {family_prefix!r})"
                ),
            )

        hourly_price, arm_sku_name, sku_id, vcpu, memory = cheapest
        hours_per_month = float(
            request.expected_usage.get("hours_per_month", 720.0) or 720.0,
        )
        monthly = hourly_price * hours_per_month

        line_item = CostLineItem(
            label=(f"Azure {arm_sku_name} ({vcpu} vCPU, {memory:g} GiB) — ${hourly_price:.4f}/hr"),
            sku=sku_id,
            monthly_amount=round(monthly, 4),
            currency=request.currency,
            notes="1 Hour",
        )
        return CostEstimate(
            request=request,
            line_items=[line_item],
            monthly_total=round(monthly, 2),
            currency=request.currency,
            pricing_source_url=(f"{PRICES_ENDPOINT}?$filter={odata_filter}"),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "Azure Retail Prices returns list price; "
                "Reservations and Hybrid Benefit are not applied — "
                "actual bill may be lower.",
                (
                    f"selected cheapest {family_prefix}-series "
                    f"Consumption Linux SKU fitting cpu_cores="
                    f"{cpu_cores} memory_gib={memory_gib}"
                ),
            ],
        )

    def _compute_filter(
        self,
        *,
        service_name: str,
        request: CostEstimateRequest,
    ) -> str:
        """OData filter pinning the price set to consumption VMs in
        the cluster's region. We don't filter by SKU family in OData
        (Retail Prices' name filter doesn't accept ``startswith`` on
        all fields); the post-filter step does that based on the VM
        catalog. We do pin `priceType eq 'Consumption'` so reserved
        and spot rows are excluded."""
        parts = [
            f"serviceName eq '{service_name}'",
            f"armRegionName eq '{request.region}'",
            "priceType eq 'Consumption'",
        ]
        for key, value in request.config.items():
            if key in {"instance_family"}:
                continue
            parts.append(f"{key} eq '{value}'")
        return " and ".join(parts)

    def _select_cheapest_vm_sku(
        self,
        *,
        body: dict[str, Any],
        vm_sizes: dict[str, tuple[int, float]],
        cpu_cores: float,
        memory_gib: float,
        family_prefix: str,
    ) -> tuple[float, str, str, int, float] | None:
        """Walk Retail Prices items + join with the VM-size catalog
        and pick the cheapest fitting SKU. Skips:

          - Windows / Spot / Low-Priority SKUs (product/sku names
            carry these as suffixes)
          - Any SKU not in the requested family prefix
          - Any SKU whose vCPU/memory don't meet the request
        """
        family_upper = family_prefix.upper()
        best: tuple[float, str, str, int, float] | None = None
        for record in body.get("Items", []) or []:
            unit_price = float(record.get("unitPrice", 0.0) or 0.0)
            if unit_price <= 0.0:
                continue
            unit_of_measure = (record.get("unitOfMeasure", "") or "").lower()
            if "hour" not in unit_of_measure:
                continue
            arm_sku_name = record.get("armSkuName", "") or record.get("skuName", "")
            if not arm_sku_name:
                continue
            arm_sku_upper = arm_sku_name.upper()
            # Skip spot / low-priority — operator-facing preview
            # should reflect the on-demand bill.
            product_name = (record.get("productName", "") or "").lower()
            sku_name = (record.get("skuName", "") or "").lower()
            if any(token in sku_name or token in product_name for token in ("spot", "low priority", "windows")):
                continue
            # Family prefix check. Azure ARM SKU names look like
            # ``Standard_D4s_v3`` / ``D4s_v3`` / ``Standard_D2as_v5``.
            # Match after the optional ``Standard_`` prefix.
            sku_body = arm_sku_upper.removeprefix("STANDARD_")
            if not sku_body.startswith(family_upper):
                continue

            capacity = vm_sizes.get(arm_sku_name)
            if capacity is None:
                # Try without `Standard_` prefix as the lookup key.
                capacity = vm_sizes.get(sku_body)
            if capacity is None:
                continue
            vcpu, memory = capacity
            if vcpu < cpu_cores or memory < memory_gib:
                continue

            sku_id = record.get("skuId", "") or record.get("meterId", "") or arm_sku_name
            candidate = (unit_price, arm_sku_name, sku_id, vcpu, memory)
            if best is None or unit_price < best[0]:
                best = candidate
        return best

    def _lookup_vm_sizes(
        self,
        *,
        request: CostEstimateRequest,
    ) -> dict[str, tuple[int, float]] | None:
        """Return the VM size catalog for the request's region.

        Returns None when no lookup is wired and the default
        SDK-backed one can't be initialized — the caller surfaces
        a clear CostEstimateUnavailable with the wiring instruction.
        Returns an empty dict when the lookup runs but yields zero
        SKUs (genuinely empty region).
        """
        ttl = self._config.cache_ttl_seconds
        now = datetime.now(tz=UTC).timestamp()
        region = request.region

        if ttl > 0:
            with self._vm_size_lock:
                cached = self._vm_size_cache.get(region)
                if cached is not None:
                    fetched_at, sizes = cached
                    if (now - fetched_at) < ttl:
                        return sizes

        lookup = self._config.vm_sizes_lookup
        if lookup is None:
            lookup = _default_vm_size_lookup(config=request.config)
        if lookup is None:
            return None
        try:
            sizes = lookup(region)
        except Exception:
            return None

        if ttl > 0:
            with self._vm_size_lock:
                self._vm_size_cache[region] = (now, sizes)
        return sizes

    def _fetch_prices(
        self,
        *,
        odata_filter: str,
        currency: str,
    ) -> dict[str, Any]:
        """Cached GET against the Retail Prices API. Keyed on
        (filter, currency)."""
        ttl = self._config.cache_ttl_seconds
        now = datetime.now(tz=UTC).timestamp()
        cache_key = (odata_filter, currency)

        if ttl > 0:
            with self._prices_lock:
                cached = self._prices_cache.get(cache_key)
                if cached is not None:
                    fetched_at, body = cached
                    if (now - fetched_at) < ttl:
                        return body

        response = self._http.get(
            PRICES_ENDPOINT,
            params={"$filter": odata_filter, "currencyCode": currency},
        )
        body = response.json() if hasattr(response, "json") else response

        if ttl > 0:
            with self._prices_lock:
                self._prices_cache[cache_key] = (now, body)
        return body

    # ---- managed-service path (existing) ------------------------------

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
            items.append(
                CostLineItem(
                    label=record.get("productName", "Azure line item"),
                    sku=record.get("skuId", "") or record.get("meterId", ""),
                    monthly_amount=round(monthly, 4),
                    currency=request.currency,
                    notes=usage_unit,
                )
            )
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
            return (
                unit_price
                * usage.get(
                    "requests_per_month",
                    0.0,
                )
                / divisor
            )
        if "hour" in unit_lower:
            return unit_price * usage.get("hours_per_month", 720.0)
        return unit_price


def _default_vm_size_lookup(
    *,
    config: dict[str, str],
) -> VmSizeLookup | None:
    """Build the default `azure-mgmt-compute`-backed lookup if a
    subscription id is in the request config.

    Caller wires the subscription id via ``CostEstimateRequest.config
    ['subscription_id']``; without it we can't construct the
    ComputeManagementClient, and the driver surfaces
    CostEstimateUnavailable so the operator knows what to add.
    """
    subscription_id = config.get("subscription_id", "")
    if not subscription_id:
        return None

    def _lookup(region: str) -> dict[str, tuple[int, float]]:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient

        credential = DefaultAzureCredential()
        client = ComputeManagementClient(
            credential=credential,
            subscription_id=subscription_id,
        )
        result: dict[str, tuple[int, float]] = {}
        for vm_size in client.virtual_machine_sizes.list(location=region):
            name = getattr(vm_size, "name", "") or ""
            vcpu = int(getattr(vm_size, "number_of_cores", 0) or 0)
            # number_of_cores * memory_in_mb -> GiB
            memory_mb = float(getattr(vm_size, "memory_in_mb", 0) or 0)
            memory_gib = memory_mb / 1024.0
            if name:
                result[name] = (vcpu, memory_gib)
                # Also index with the ``Standard_`` prefix that
                # Retail Prices uses on `armSkuName`.
                result[f"Standard_{name}"] = (vcpu, memory_gib)
        return result

    return _lookup


class _DefaultHttp:
    def get(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        import json
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

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


# ---- billing actuals (#502) ---------------------------------------


@dataclass(frozen=True)
class AzureBillingActualsConfig:
    """Per-cloud config for the actuals client. ``cost_mgmt_client``
    is an injectable ``azure.mgmt.costmanagement.CostManagementClient``
    wrapper — tests substitute a fake. ``scope`` is the Cost
    Management API scope string (e.g.
    ``subscriptions/{sub_id}`` or
    ``providers/Microsoft.Billing/billingAccounts/{billing_account_id}``).
    """

    cost_mgmt_client: Any | None = None
    scope: str = ""


class AzureBillingActuals:
    """Reads actual Azure spend grouped by the
    ``astrolift.io/binding`` tag via the Cost Management Query API.

    Cost Management's ``Query`` endpoint accepts a ``Dimensions``
    grouping on tag names; the query returns one row per tag value
    with the summed cost in the account's currency. Resources missing
    the tag are emitted under an empty-string binding bucket.

    Returns :class:`BillingActualsUnavailable` when the scope isn't
    configured (operator hasn't supplied a subscription id) or when
    the Cost Management API rejects the request — the collector
    logs and moves on so one misconfigured tenant doesn't stall the
    whole snapshot.
    """

    def __init__(self, *, config: AzureBillingActualsConfig) -> None:
        self._config = config
        if not config.scope:
            self._client: Any | None = None
            self._unavailable_reason: tuple[str, str] | None = (
                "not_enabled",
                (
                    "Azure cost actuals not configured — set "
                    "AzureBillingActualsConfig.scope to a Cost Management "
                    "scope string (e.g. 'subscriptions/<sub-id>')."
                ),
            )
            return
        self._unavailable_reason = None
        if config.cost_mgmt_client is not None:
            self._client = config.cost_mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.costmanagement import CostManagementClient

            credential = DefaultAzureCredential()
            self._client = CostManagementClient(credential=credential)

    @driver_op(cloud="azure", driver="cost")
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
        if self._unavailable_reason is not None:
            reason, message = self._unavailable_reason
            return BillingActualsUnavailable(reason=reason, message=message)
        # Cost Management Query body. The shape matches the REST API
        # docs; the SDK accepts the same dict on `parameters=`.
        query_definition = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {
                "from": f"{start.isoformat()}T00:00:00+00:00",
                "to": f"{end.isoformat()}T00:00:00+00:00",
            },
            "dataset": {
                "granularity": "None",
                "aggregation": {
                    "totalCost": {"name": "Cost", "function": "Sum"},
                },
                "grouping": [
                    {"type": "TagKey", "name": AZURE_BINDING_TAG_KEY},
                ],
            },
        }
        try:
            response = self._client.query.usage(
                scope=self._config.scope,
                parameters=query_definition,
            )
        except Exception as exc:
            msg = str(exc)
            if "not authorized" in msg.lower() or "unauthorized" in msg.lower() or "forbidden" in msg.lower():
                return BillingActualsUnavailable(
                    reason="unauthenticated",
                    message=(f"Cost Management Query denied for scope {self._config.scope!r}. Underlying: {exc}"),
                )
            return BillingActualsUnavailable(
                reason="api_error",
                message=f"Cost Management Query failed: {exc}",
            )

        rows = _azure_query_rows(response)
        columns = _azure_query_columns(response)
        cost_idx, tag_idx, currency_idx = _azure_column_indices(columns)
        if cost_idx is None or tag_idx is None:
            return BillingActualsUnavailable(
                reason="api_error",
                message=(f"Cost Management Query response shape unexpected — columns={columns!r}"),
            )

        totals: dict[tuple[str, str], int] = {}
        for row in rows:
            try:
                amount = float(row[cost_idx] or 0.0)
            except (TypeError, ValueError, IndexError):
                continue
            if amount <= 0:
                continue
            try:
                raw_tag = row[tag_idx]
            except IndexError:
                raw_tag = ""
            binding_guid = _azure_normalize_tag_value(raw_tag)
            row_currency = currency
            if currency_idx is not None:
                with contextlib.suppress(IndexError):
                    row_currency = row[currency_idx] or currency
            if currency and row_currency and row_currency != currency:
                continue
            cents = round(amount * 100)
            key = (binding_guid, row_currency)
            totals[key] = totals.get(key, 0) + cents

        return [
            BillingActualLineItem(
                binding_guid=binding,
                amount_cents=cents,
                currency=row_currency,
                provider="azure",
                service="",
            )
            for (binding, row_currency), cents in totals.items()
        ]


def _azure_query_rows(response: Any) -> list[list[Any]]:
    if response is None:
        return []
    rows = getattr(response, "rows", None)
    if rows is None and isinstance(response, dict):
        rows = (response.get("properties") or {}).get("rows")
    return list(rows or [])


def _azure_query_columns(response: Any) -> list[dict[str, str]]:
    if response is None:
        return []
    columns = getattr(response, "columns", None)
    if columns is None and isinstance(response, dict):
        columns = (response.get("properties") or {}).get("columns")
    out: list[dict[str, str]] = []
    for col in columns or []:
        if isinstance(col, dict):
            out.append({"name": str(col.get("name", "")), "type": str(col.get("type", ""))})
        else:
            out.append(
                {
                    "name": str(getattr(col, "name", "") or ""),
                    "type": str(getattr(col, "type", "") or ""),
                }
            )
    return out


def _azure_column_indices(
    columns: list[dict[str, str]],
) -> tuple[int | None, int | None, int | None]:
    """Return (cost_idx, tag_value_idx, currency_idx). Cost Management
    response columns vary by API version — the names we look for are
    ``Cost`` / the requested tag name / ``Currency``. Tag values can
    be exposed under ``TagValue`` or the literal tag name."""
    cost_idx = tag_idx = currency_idx = None
    for i, col in enumerate(columns):
        name_lower = col["name"].lower()
        if cost_idx is None and name_lower in {"cost", "costusd", "totalcost"}:
            cost_idx = i
        elif tag_idx is None and (
            name_lower == AZURE_BINDING_TAG_KEY.lower()
            or name_lower == "tagvalue"
            or AZURE_BINDING_TAG_KEY.lower() in name_lower
        ):
            tag_idx = i
        elif currency_idx is None and name_lower == "currency":
            currency_idx = i
    return cost_idx, tag_idx, currency_idx


def _azure_normalize_tag_value(raw: Any) -> str:
    """Cost Management quotes tag values with extra ``"`` and may
    return ``None`` for the untagged bucket. Normalize."""
    if raw is None:
        return ""
    value = str(raw)
    return value.strip().strip('"')
