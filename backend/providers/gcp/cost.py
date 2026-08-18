"""GCP CostEstimator (#78 part of #69).

Pulls SKU prices from the Cloud Billing Catalog API
(cloudbilling.googleapis.com/v1/services/{service}/skus). The
driver is read-only.

CRITICAL: never substitute hard-coded prices. The Catalog API call
is the source of truth; failures map to CostEstimateUnavailable.

Compute (#440) — the `(kind=compute, variant=node_hour)` pair asks
the driver for Compute Engine pricing. GCP bills cores + memory
separately (one SKU per "N2 Instance Core" hour, one per "N2
Instance Ram" GiB hour); the driver sums the two for the requested
vCPU + memory and multiplies by `hours_per_month`. Region scoping
follows the same SKU `service_regions` filter the managed-service
path already uses.
"""

from __future__ import annotations

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
from _sdk.cost_skus import CatalogPrice, SkuSelectionError, select_line_items
from gcp.cost_plans import PlanUnavailable, has_plan, plan_for

log = logging.getLogger(__name__)

# Label key on GCP resources is GCP-normalized — slashes / dots become
# underscores per ``core.cloud_tags.to_gcp``
# ("astrolift.io/managed_service_id" → "astrolift_io_managed_service_id").
# The actuals client filters on this normalized form when reading from
# the BigQuery billing export.
#
# WARNING (#1419): GCP managed-service drivers do not agree on this
# spelling. Four variants are in the tree and only a handful of drivers
# write this one, so today's group-by attributes those and drops the
# rest into the untagged bucket. Converging the drivers is tracked
# separately; this constant is the target they converge on, because it
# is what ``to_gcp`` produces from the canonical key.
GCP_MANAGED_SERVICE_LABEL_KEY = "astrolift_io_managed_service_id"


# GCP Cloud Billing service IDs.
SERVICE_ID_BY_VARIANT: dict[tuple[str, str], str] = {
    ("object_store", "gcs"): "95FF-2EF5-5EA1",  # Cloud Storage
    ("queue", "pubsub"): "A1E8-BE35-7EBC",  # Pub/Sub
    ("topic", "pubsub_topic"): "A1E8-BE35-7EBC",  # Pub/Sub
    ("warehouse", "bigquery"): "24E6-581D-38E5",  # BigQuery
    ("postgres", "cloudsql"): "9662-B51E-5089",  # Cloud SQL
    ("mysql", "cloudsql"): "9662-B51E-5089",  # Cloud SQL
    ("mssql", "cloudsql_sqlserver"): "9662-B51E-5089",  # Cloud SQL
    ("postgres", "alloydb"): "70A4-7A89-3F8F",  # AlloyDB
    ("redis", "memorystore"): "F25A-3A0D-5DDB",  # Memorystore
    ("nosql", "firestore"): "F17B-412E-CB64",  # Firestore
    ("document_db", "firestore_native"): "F17B-412E-CB64",  # Firestore (portable kind)
    ("nosql", "bigtable"): "FD83-CFB8-A3CB",  # Bigtable
    ("kv_store", "bigtable"): "FD83-CFB8-A3CB",  # Bigtable (portable kind)
    ("encryption_key", "cloud_kms"): "EE2F-D110-890C",  # Cloud KMS
    ("filesystem", "filestore"): "D97E-AB26-5D95",  # Cloud Filestore
    ("event_bus", "eventarc"): "BA71-906B-95A8",  # Eventarc
    # Cluster compute capacity (#440). The Compute Engine service ID;
    # the driver walks N2 SKUs (core + ram line items) and sums them
    # for the cluster's region.
    ("compute", "node_hour"): "6F81-5844-456A",  # Compute Engine
}

# Newer Google services can ship before their immutable Cloud Billing service
# ID is published in product documentation. Resolve those ids from the same
# authoritative Catalog API instead of hard-coding a guessed identifier.
SERVICE_DISPLAY_NAME_BY_VARIANT: dict[tuple[str, str], str] = {
    ("event_stream", "managed_kafka"): "Managed Service for Apache Kafka",
}

# Variants still priced by summing every region-matching SKU in their
# Catalog service. That is over-counting by construction — each of
# these services bills on several dimensions the resource may not even
# use — and the #1318 audit froze the list here so it can only shrink:
# a variant leaves once ``gcp.cost_plans`` grows a plan for it, and a
# newly mapped variant without a plan is refused rather than summed.
LEGACY_SUM_VARIANTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("object_store", "gcs"),
        ("queue", "pubsub"),
        ("topic", "pubsub_topic"),
        ("warehouse", "bigquery"),
        ("postgres", "alloydb"),
        ("redis", "memorystore"),
        ("nosql", "firestore"),
        ("document_db", "firestore_native"),
        ("nosql", "bigtable"),
        ("kv_store", "bigtable"),
        ("encryption_key", "cloud_kms"),
        ("filesystem", "filestore"),
        ("event_bus", "eventarc"),
        ("event_stream", "managed_kafka"),
    }
)

CATALOG_BASE = "https://cloudbilling.googleapis.com/v1/services"


@dataclass(frozen=True)
class GCPCostConfig:
    billing_client: Any | None = None
    """Cloud Billing Catalog client. Inject for tests."""

    cache_ttl_seconds: int = 6 * 60 * 60
    """How long a Compute Engine SKU list is cached per service ID.
    Pricing tables don't churn often; 6h keeps the preview-cost
    column responsive without pinning a stale view across an SKU
    revision. Set to 0 to disable (tests do)."""


class GCPCostEstimator(CostEstimator):
    def __init__(self, *, config: GCPCostConfig) -> None:
        self._config = config
        if config.billing_client is not None:
            self._client = config.billing_client
        else:
            from google.cloud import billing_v1

            self._client = billing_v1.CloudCatalogClient()
        # In-process cache of Compute Engine SKU sweeps keyed on
        # service ID. Compute SKU pages are big (thousands per
        # service); back-to-back estimates for the same cluster
        # should share one fetch.
        self._sku_cache: dict[str, tuple[float, list[Any]]] = {}
        self._sku_lock = threading.Lock()
        self._service_id_cache: dict[tuple[str, str], str] = {}

    @driver_op(cloud="gcp", driver="cost", heartbeat=False)
    def supported(self, *, kind: str, variant: str) -> bool:
        pair = (kind, variant)
        if pair not in SERVICE_ID_BY_VARIANT and pair not in SERVICE_DISPLAY_NAME_BY_VARIANT:
            return False
        # A mapped service id is not a pricing path on its own (#1318):
        # the variant needs a SKU plan, or a place on the frozen legacy
        # list. Compute walks its own CPU/RAM SKUs.
        if pair == ("compute", "node_hour"):
            return True
        return has_plan(kind=kind, variant=variant) or pair in LEGACY_SUM_VARIANTS

    @driver_op(cloud="gcp", driver="cost")
    def estimate(self, request: CostEstimateRequest) -> CostResult:
        pair = (request.kind, request.variant)
        if pair == ("compute", "node_hour"):
            return self._estimate_compute_node_hour(
                request=request,
                service_id=SERVICE_ID_BY_VARIANT[pair],
            )

        # Resolve the SKU plan before touching the network: a request
        # the plan refuses is refused either way, so the Catalog
        # lookup would be a wasted round trip.
        plan = plan_for(request) if has_plan(kind=request.kind, variant=request.variant) else None
        if isinstance(plan, PlanUnavailable):
            return CostEstimateUnavailable(
                request=request,
                reason=plan.reason,
                message=plan.message,
            )
        if plan is None and pair not in LEGACY_SUM_VARIANTS:
            return CostEstimateUnavailable(
                request=request,
                reason="unsupported",
                message=(
                    f"({request.kind!r}, {request.variant!r}) has no Cloud Billing Catalog "
                    "SKU plan; add one in gcp.cost_plans rather than summing every SKU in "
                    "the service (#1318)"
                ),
            )

        service_id = SERVICE_ID_BY_VARIANT.get(pair) or self._service_id_cache.get(pair)
        if service_id is None and pair in SERVICE_DISPLAY_NAME_BY_VARIANT:
            display_name = SERVICE_DISPLAY_NAME_BY_VARIANT[pair]
            try:
                service_id = next(
                    (
                        str(getattr(service, "name", "") or "").rsplit("/", 1)[-1]
                        for service in self._client.list_services()
                        if str(getattr(service, "display_name", "") or "") == display_name
                    ),
                    "",
                )
            except Exception as exc:
                return CostEstimateUnavailable(
                    request=request,
                    reason="api_error",
                    message=f"Catalog service lookup failed: {exc}",
                )
            if not service_id:
                return CostEstimateUnavailable(
                    request=request,
                    reason="service_not_found",
                    message=f"Cloud Billing Catalog has no service named {display_name!r}",
                )
            self._service_id_cache[pair] = service_id
        if service_id is None:
            return CostEstimateUnavailable(
                request=request,
                reason="unsupported",
                message=(f"no GCP Catalog service ID for ({request.kind!r}, {request.variant!r})"),
            )

        try:
            skus = self._fetch_skus(service_id=service_id)
        except Exception as exc:
            # #616 -- log the Catalog API failure so the cost-estimate
            # dead-zone has a debuggable trace. Without this the
            # operator only sees "estimate unavailable" on the UI.
            log.warning(
                "gcp cost estimate failed",
                extra={
                    "service_id": service_id,
                    "kind": request.kind,
                    "variant": request.variant,
                    "exception_type": type(exc).__name__,
                },
                exc_info=True,
            )
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=f"Catalog API call failed: {exc}",
            )

        if plan is None:
            return self._estimate_by_summing_service(
                request=request,
                service_id=service_id,
                skus=skus,
            )

        selection = select_line_items(
            plan=plan,
            prices=self._catalog_prices(skus=skus, currency=request.currency),
            region=request.region,
            currency=request.currency,
        )
        if isinstance(selection, SkuSelectionError):
            return CostEstimateUnavailable(
                request=request,
                reason=selection.reason,
                message=(f"{selection.message} [service {service_id}]"),
            )

        total = sum(item.monthly_amount for item in selection)
        return CostEstimate(
            request=request,
            line_items=selection,
            monthly_total=round(total, 2),
            currency=request.currency,
            pricing_source_url=(f"{CATALOG_BASE}/{service_id}/skus"),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "GCP Cloud Catalog returns list price; CUDs and sustained-use discounts are not applied.",
                *plan.notes,
            ],
        )

    # ---- compute / node_hour ------------------------------------------

    def _estimate_compute_node_hour(
        self,
        *,
        request: CostEstimateRequest,
        service_id: str,
    ) -> CostResult:
        """Sum the cheapest matching N2 core + ram SKUs for the
        requested vCPU + memory in the cluster's region, then
        multiply by `expected_usage.hours_per_month`.

        GCP bills Compute Engine on two axes: per-vCPU-hour and
        per-GiB-RAM-hour. So an N-vCPU, M-GiB workload costs
        N * vcpu_hour_rate + M * ram_gib_hour_rate. The driver picks
        the cheapest core SKU + cheapest ram SKU in the cluster's
        region that match the N2 instance family (the EKS-equivalent
        general-purpose family the Astrolift bootstrap recipe targets
        on GKE).
        """
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

        try:
            skus = self._fetch_skus(service_id=service_id)
        except Exception as exc:
            return CostEstimateUnavailable(
                request=request,
                reason="api_error",
                message=f"Catalog API call failed: {exc}",
            )

        family_preference = request.config.get(
            "instance_family",
            "N2",
        )
        core_match = self._find_cheapest_sku(
            skus=skus,
            region=request.region,
            currency=request.currency,
            resource_group="CPU",
            family=family_preference,
        )
        ram_match = self._find_cheapest_sku(
            skus=skus,
            region=request.region,
            currency=request.currency,
            resource_group="RAM",
            family=family_preference,
        )
        if core_match is None or ram_match is None:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(f"no {family_preference} CPU/RAM SKUs in region {request.region!r} in service {service_id}"),
            )

        hours_per_month = float(
            request.expected_usage.get("hours_per_month", 720.0) or 720.0,
        )
        core_price, core_sku_id, core_label = core_match
        ram_price, ram_sku_id, ram_label = ram_match
        core_monthly = core_price * cpu_cores * hours_per_month
        ram_monthly = ram_price * memory_gib * hours_per_month
        total_monthly = core_monthly + ram_monthly

        line_items = [
            CostLineItem(
                label=(f"{core_label} — {cpu_cores} vCPU @ ${core_price:.6f}/vCPU-hr"),
                sku=core_sku_id,
                monthly_amount=round(core_monthly, 4),
                currency=request.currency,
                notes="vCPU hour",
            ),
            CostLineItem(
                label=(f"{ram_label} — {memory_gib:.2f} GiB @ ${ram_price:.6f}/GiB-hr"),
                sku=ram_sku_id,
                monthly_amount=round(ram_monthly, 4),
                currency=request.currency,
                notes="GiB hour",
            ),
        ]
        return CostEstimate(
            request=request,
            line_items=line_items,
            monthly_total=round(total_monthly, 2),
            currency=request.currency,
            pricing_source_url=(f"{CATALOG_BASE}/{service_id}/skus?region={request.region}&family={family_preference}"),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "GCP Cloud Catalog returns list price; CUDs and "
                "sustained-use discounts are not applied — actual "
                "bill may be lower.",
                (f"vCPU + GiB priced from cheapest {family_preference} SKU in {request.region}"),
            ],
        )

    def _fetch_skus(
        self,
        *,
        service_id: str,
    ) -> list[Any]:
        """Return the full SKU list for a Catalog service, cached by
        service ID. The Catalog client paginates internally; we
        materialize into a list once so the matcher can walk it
        several times (CPU, RAM, storage, licence, ...)."""
        ttl = self._config.cache_ttl_seconds
        now = datetime.now(tz=UTC).timestamp()

        if ttl > 0:
            with self._sku_lock:
                cached = self._sku_cache.get(service_id)
                if cached is not None:
                    fetched_at, skus = cached
                    if (now - fetched_at) < ttl:
                        return skus

        skus = list(
            self._client.list_skus(
                parent=f"services/{service_id}",
            ),
        )
        if ttl > 0:
            with self._sku_lock:
                self._sku_cache[service_id] = (now, skus)
        return skus

    def _find_cheapest_sku(
        self,
        *,
        skus: list[Any],
        region: str,
        currency: str,
        resource_group: str,
        family: str,
    ) -> tuple[float, str, str] | None:
        """Scan SKUs for the cheapest entry in the cluster's region
        whose `category.resource_group` matches (`CPU` or `RAM`) and
        whose description / SKU name contains the family token (e.g.
        ``N2``).

        Returns ``(unit_price, sku_id, description)`` or None.
        Skips preemptible / spot / commitment / sole-tenant SKUs —
        those carry the family name too but bill on a discount rate
        we can't apply at preview time.
        """
        best: tuple[float, str, str] | None = None
        family_upper = family.upper()
        for sku in skus:
            description = getattr(sku, "description", "") or ""
            description_upper = description.upper()
            if family_upper not in description_upper:
                continue
            # Skip discount variants — the preview should reflect
            # what a fresh node-pool will be billed at on day one.
            if any(
                token in description_upper
                for token in (
                    "PREEMPTIBLE",
                    "SPOT",
                    "COMMITMENT",
                    "SOLE TENANCY",
                )
            ):
                continue
            category = getattr(sku, "category", None)
            if category is not None:
                resource = (getattr(category, "resource_group", "") or "").upper()
                if resource and resource != resource_group.upper():
                    continue
            else:
                # Fall back to description sniff when the test
                # fake doesn't model category.
                if resource_group == "CPU" and "RAM" in description_upper:
                    continue
                if resource_group == "RAM" and "CORE" in description_upper:
                    continue

            regions = list(getattr(sku, "service_regions", []) or [])
            if regions and region not in regions and "global" not in regions:
                continue
            pricing_info = list(
                getattr(sku, "pricing_info", []) or [],
            )
            if not pricing_info:
                continue
            tiered = pricing_info[0].pricing_expression
            unit_price = self._first_tier_price(
                tiered=tiered,
                currency=currency,
            )
            if unit_price is None or unit_price <= 0.0:
                continue

            sku_id = getattr(sku, "sku_id", "") or getattr(sku, "name", "")
            candidate = (unit_price, sku_id, description)
            if best is None or unit_price < best[0]:
                best = candidate
        return best

    # ---- legacy sum-the-service path (#1318 audit) ----------------------

    def _estimate_by_summing_service(
        self,
        *,
        request: CostEstimateRequest,
        service_id: str,
        skus: Any,
    ) -> CostResult:
        """Sum every region-matching SKU in the service.

        This is what every GCP managed-service estimate used to do,
        and it is wrong for any service that bills on more than one
        dimension — which, per the #1318 audit, is all of them: a
        Cloud Storage estimate lands on storage classes plus
        operations plus retrieval, a Bigtable estimate on nodes plus
        storage. The variants still on this path are frozen in
        ``LEGACY_SUM_VARIANTS``; each needs its own plan in
        ``gcp.cost_plans``, and no new variant may join them.

        Until then the estimate carries the caveat in its notes rather
        than presenting the sum as a considered figure.
        """
        line_items = self._legacy_line_items(skus=skus, request=request)
        if not line_items:
            return CostEstimateUnavailable(
                request=request,
                reason="sku_not_found",
                message=(f"no SKUs matched region {request.region!r} in service {service_id}"),
            )

        total = sum(item.monthly_amount for item in line_items)
        return CostEstimate(
            request=request,
            line_items=line_items,
            monthly_total=round(total, 2),
            currency=request.currency,
            pricing_source_url=(f"{CATALOG_BASE}/{service_id}/skus"),
            pricing_fetched_at=datetime.now(tz=UTC).isoformat(),
            notes=[
                "GCP Cloud Catalog returns list price; CUDs and sustained-use discounts are not applied.",
                (
                    f"APPROXIMATE: sums all {len(line_items)} region-matching SKUs in service "
                    f"{service_id}, including ones this resource does not use. "
                    f"({request.kind}, {request.variant}) has no SKU plan yet (#1318)."
                ),
            ],
        )

    def _legacy_line_items(
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
                tiered=tiered,
                currency=request.currency,
            )
            if unit_price is None:
                continue
            usage_unit = getattr(tiered, "usage_unit", "")
            monthly = self._monthly_amount(
                unit_price=unit_price,
                usage_unit=usage_unit,
                request=request,
            )
            items.append(
                CostLineItem(
                    label=getattr(sku, "description", "GCP line item"),
                    sku=getattr(sku, "sku_id", "") or getattr(sku, "name", ""),
                    monthly_amount=round(monthly, 4),
                    currency=request.currency,
                    notes=usage_unit,
                )
            )
        return items

    def _first_tier_price(
        self,
        *,
        tiered: Any,
        currency: str,
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

    # ---- managed-service path ------------------------------------------

    def _catalog_prices(
        self,
        *,
        skus: Any,
        currency: str,
    ) -> list[CatalogPrice]:
        """Project Catalog SKUs into the cloud-agnostic row the shared
        selector matches on.

        ``category`` carries the structured facets (``usage_type`` in
        particular) that keep committed-use and preemptible rates out
        of an on-demand preview; rows without a price in the requested
        currency are dropped here rather than defaulting to zero.
        """
        prices: list[CatalogPrice] = []
        for sku in skus:
            pricing_info_list = list(getattr(sku, "pricing_info", []) or [])
            if not pricing_info_list:
                continue
            tiered = pricing_info_list[0].pricing_expression
            unit_price = self._first_tier_price(tiered=tiered, currency=currency)
            if unit_price is None:
                continue
            category = getattr(sku, "category", None)
            prices.append(
                CatalogPrice(
                    sku_id=getattr(sku, "sku_id", "") or getattr(sku, "name", ""),
                    description=getattr(sku, "description", "") or "",
                    unit_price=unit_price,
                    currency=currency,
                    usage_unit=getattr(tiered, "usage_unit", "") or "",
                    regions=tuple(getattr(sku, "service_regions", []) or []),
                    attributes={
                        "resource_family": str(getattr(category, "resource_family", "") or ""),
                        "resource_group": str(getattr(category, "resource_group", "") or ""),
                        "usage_type": str(getattr(category, "usage_type", "") or ""),
                    },
                )
            )
        return prices


# ---- billing actuals (#502) ---------------------------------------


@dataclass(frozen=True)
class GCPBillingActualsConfig:
    """Per-cloud config for the actuals client.

    GCP serves cost actuals via a BigQuery billing export — the
    operator points the platform at the dataset / table they
    configured in the GCP console (Billing → Billing export → BigQuery
    export). ``bq_client`` is the injectable
    ``google.cloud.bigquery.Client``; ``project`` + ``dataset`` +
    ``table`` identify the export.
    """

    bq_client: Any | None = None
    project: str = ""
    dataset: str = ""
    table: str = "gcp_billing_export_resource_v1"


class GCPBillingActuals:
    """Reads actual GCP spend grouped by the
    ``astrolift_io_managed_service_id`` label via BigQuery against the
    billing export table.

    The exported schema carries a repeated ``labels`` STRUCT (key,
    value); the query unnests it, filters to our label key, and SUMs
    ``cost`` per ``value`` over the requested window. Resources with
    no matching label fall into the ``managed_service_guid=""`` bucket.

    The query template is parameterised on (project, dataset, table)
    so a multi-project operator can run several actuals clients
    against the same source-of-truth dataset.

    Returns :class:`BillingActualsUnavailable` when the dataset isn't
    configured (operator hasn't enabled BigQuery export yet) or when
    the query fails — the collector logs and moves on so one
    misconfigured cloud doesn't stall the whole snapshot.
    """

    _SQL_TEMPLATE = (
        "SELECT lbl.value AS managed_service_guid, "
        "SUM(cost) AS amount, currency "
        "FROM `{project}.{dataset}.{table}` "
        "LEFT JOIN UNNEST(labels) lbl ON lbl.key = @label_key "
        "WHERE usage_start_time >= @start AND usage_start_time < @end "
        "GROUP BY managed_service_guid, currency"
    )

    def __init__(self, *, config: GCPBillingActualsConfig) -> None:
        self._config = config
        if not config.project or not config.dataset:
            self._client = None
            self._unavailable_reason: tuple[str, str] | None = (
                "not_enabled",
                (
                    "GCP billing export not configured — set "
                    "GCPBillingActualsConfig.project + dataset to the "
                    "BigQuery dataset receiving your billing export "
                    "(Billing → Billing export → BigQuery export)."
                ),
            )
            return
        self._unavailable_reason = None
        if config.bq_client is not None:
            self._client = config.bq_client
        else:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=config.project)

    @driver_op(cloud="gcp", driver="cost")
    def query_actuals_by_service(
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
        sql = self._SQL_TEMPLATE.format(
            project=self._config.project,
            dataset=self._config.dataset,
            table=self._config.table,
        )
        try:
            job = self._client.query(
                sql,
                job_config=_bq_job_config(
                    label_key=GCP_MANAGED_SERVICE_LABEL_KEY,
                    start=start,
                    end=end,
                ),
            )
            rows = list(job.result())
        except Exception as exc:
            msg = str(exc)
            if "not found" in msg.lower() or "does not exist" in msg.lower():
                return BillingActualsUnavailable(
                    reason="not_enabled",
                    message=(
                        f"Billing export table not found "
                        f"({self._config.project}.{self._config.dataset}.{self._config.table}). "
                        f"Enable BigQuery billing export in the GCP console. Underlying: {exc}"
                    ),
                )
            return BillingActualsUnavailable(
                reason="api_error",
                message=f"BigQuery billing export query failed: {exc}",
            )

        items: list[BillingActualLineItem] = []
        for row in rows:
            managed_service_guid = _row_get(row, "managed_service_guid") or ""
            amount = float(_row_get(row, "amount") or 0.0)
            row_currency = _row_get(row, "currency") or currency
            if currency and row_currency and row_currency != currency:
                continue
            if amount <= 0:
                continue
            cents = round(amount * 100)
            items.append(
                BillingActualLineItem(
                    managed_service_guid=managed_service_guid,
                    amount_cents=cents,
                    currency=row_currency or currency,
                    provider="gcp",
                    service="",
                )
            )
        return items


def _bq_job_config(*, label_key: str, start: date, end: date) -> Any:
    """Build a parameterised BigQuery job config. Imported lazily so
    the module loads cleanly in environments without the BQ SDK
    (tests inject a fake client that ignores the job config)."""
    try:
        from google.cloud import bigquery
    except ImportError:
        return None
    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("label_key", "STRING", label_key),
            bigquery.ScalarQueryParameter("start", "DATE", start.isoformat()),
            bigquery.ScalarQueryParameter("end", "DATE", end.isoformat()),
        ],
    )


def _row_get(row: Any, key: str) -> Any:
    """BigQuery Row objects support both ``row['k']`` and ``row.k``;
    fake rows in tests are often dicts. Normalize."""
    if isinstance(row, dict):
        return row.get(key)
    if hasattr(row, "get"):
        try:
            return row.get(key)
        except TypeError:
            pass
    return getattr(row, key, None)
