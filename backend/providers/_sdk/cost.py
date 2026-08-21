"""Cost estimation + billing-actuals protocols (#78 + #502).

Two distinct capabilities live here:

* :class:`CostEstimator` — pre-provision price preview, queries the
  cloud's pricing API for SKU rates. Required: live API only.
* :class:`BillingActualsClient` — *post*-provision actual spend,
  queries the cloud's billing / cost-management API for line items
  grouped by the platform's ``astrolift.io/binding`` tag. Powers the
  per-binding cost snapshot collection job (#502).

CostEstimator is an OPTIONAL driver-side capability: a managed-service
or cluster driver may implement it to surface a pre-provision price
preview. The estimator MUST query the live cloud pricing API for
the relevant SKU; estimates are NEVER computed from hard-coded prices
or rule-of-thumb formulas.

Why live-API only:
- Cloud pricing changes frequently (region, currency, commitment
  discounts, savings plans). A cached estimate goes stale silently
  and gives operators wrong numbers at the worst moment (right
  before a deploy decision).
- Each cloud's pricing API is the source of truth maintained by
  the cloud itself. Pulling from it preserves the audit trail —
  if Astrolift surfaces $X and the bill shows $Y, the discrepancy
  is the cloud's, not ours.
- Hard-coded tables drift across PRs and reviewers can't easily
  spot a stale entry.

Concrete pricing-API endpoints per cloud (driver authors wire these
in their estimate() implementation):
- AWS Pricing API — https://api.pricing.us-east-1.amazonaws.com
  (also boto3 client('pricing'))
- GCP Cloud Billing Catalog API — billingbudgets.googleapis.com
  (also services.skus list endpoint)
- Azure Retail Prices API —
  https://prices.azure.com/api/retail/prices
- k8s_native: not a cloud — return EstimateResult(unsupported=True)

The platform aggregates per-driver estimates into a per-deploy
preview. Aggregation lives in astrolift-app, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import date


@dataclass(frozen=True)
class CostEstimateRequest:
    """Description of the resource to estimate.

    Drivers project their config_schema fields into this shape so
    aggregators can drive the estimate without driver-internal
    knowledge."""

    kind: str
    """ManagedServiceKind name or 'cluster' for ClusterDriver."""

    variant: str
    """e.g. 's3', 'cnpg', 'eks', 'aks_standard_d4s_v3'."""

    region: str
    size: str
    """small | medium | large | xlarge | custom"""

    config: dict[str, str] = field(default_factory=dict)
    expected_usage: dict[str, float] = field(default_factory=dict)
    """Per-driver usage metric estimates. e.g.
    {'storage_gb_month': 100, 'requests_per_month': 1_000_000}.
    Drivers translate these to billable units."""

    currency: str = "USD"


@dataclass(frozen=True)
class CostLineItem:
    label: str
    """Human-readable line, e.g. 'Storage @ $0.023 / GB-month'."""

    sku: str
    """Cloud-side SKU identifier the price was pulled from. This
    MUST match the cloud's pricing-catalog id; auditors can replay
    the lookup."""

    monthly_amount: float
    currency: str = "USD"
    notes: str = ""


@dataclass(frozen=True)
class CostEstimate:
    request: CostEstimateRequest
    line_items: list[CostLineItem]
    monthly_total: float
    currency: str = "USD"
    pricing_source_url: str = ""
    """Direct link to the cloud's pricing record(s) used. Drivers
    MUST populate — operators need to verify."""

    pricing_fetched_at: str = ""
    """ISO-8601 UTC timestamp of the live API call."""

    notes: list[str] = field(default_factory=list)
    """Qualifications on the number, for the operator reading it.

    Every driver populates this — list-price caveats on all three clouds,
    and on GCP the ``APPROXIMATE`` label on variants with no SKU plan. A
    caller that renders ``monthly_total`` and drops these is showing a
    figure without the sentence that says how much to trust it (#1509)."""

    approximate: bool = False
    """True when the total is known to be wrong in a stated direction.

    Structural rather than left for a caller to sniff out of ``notes``:
    the GCP estimator sums every region-matching SKU in a service for
    variants that have no SKU plan yet, which over-counts by
    construction. A surface that renders an approximate figure the same
    way it renders a considered one is the thing this flag exists to
    stop."""


@dataclass(frozen=True)
class CostEstimateUnavailable:
    """Returned when the driver can't estimate (k8s_native, unknown
    SKU, pricing API unreachable). Distinct type so callers can
    branch on absence-of-estimate vs zero-cost."""

    request: CostEstimateRequest
    reason: str
    """One of: 'no_pricing_api' | 'sku_not_found' | 'api_error'
    | 'unsupported'. Free-text detail in `message`."""

    message: str = ""


CostResult = CostEstimate | CostEstimateUnavailable


class CostEstimator(Protocol):
    """Optional driver capability — surface a pre-provision price.

    IMPORTANT: implementations MUST hit the live cloud pricing
    API. They MUST NOT compute from hard-coded SKU prices or
    rule-of-thumb formulas. Stale or hand-maintained price tables
    drift silently and erode operator trust in the preview.

    A service's catalog listing covers far more than the resource
    being estimated (other engines, editions, redundancy modes,
    storage classes), so step 1 is a per-variant selection, not a
    filter-and-sum. ``_sdk.cost_skus`` carries the cloud-agnostic
    machinery for it: declare one component per billable dimension
    and it resolves each to exactly one catalog row or refuses.

    Implementations should:
    1. Translate (kind, variant, size, expected_usage) into the
       relevant cloud pricing-catalog SKU(s).
    2. Call the pricing API at request time (cache the response
       for a SHORT TTL — minutes, not days).
    3. Return CostEstimate with pricing_source_url + pricing_fetched_at
       so operators can verify.
    4. Return CostEstimateUnavailable on any failure mode rather
       than guessing.
    """

    def estimate(
        self,
        request: CostEstimateRequest,
    ) -> CostResult: ...

    def supported(self, *, kind: str, variant: str) -> bool:
        """Quick check: does this driver have a pricing-API path
        for (kind, variant)? Cheap call — must not hit the network."""
        ...


# ---- billing actuals (#502) ----------------------------------------


@dataclass(frozen=True)
class BillingActualLineItem:
    """One billing-API row keyed back to a platform managed service.

    ``managed_service_guid`` is the value of the
    ``astrolift.io/managed_service_id`` tag on the cloud resource that
    incurred the cost. Empty string when the resource carries no such
    tag (operator-managed or shared resources) — the cost collector
    writes those rows with a NULL ``CostSnapshot.managed_service_id``
    so they roll up under the "Shared / untagged" bucket.

    This used to key on ``astrolift.io/binding``, which could never
    work (#1418): ``ManagedServiceBinding`` is one row per injected
    env var, its GUIDs are deleted and recreated on every envelope
    sync, and the rows do not exist yet when ``provision()`` runs. A
    cloud resource belongs to exactly one managed service, so the
    service GUID is the only identifier that is both singular and
    stable enough to stamp at provision time.

    ``amount_cents`` is integer cents in ``currency``. Drivers MUST
    convert from the billing-API's float USD to integer cents before
    handing back — the snapshot model stores cents and silently
    truncating mid-pipeline loses sub-cent precision.

    ``provider`` and ``service`` are best-effort provenance strings
    (``"aws-rds"`` / ``"PostgreSQL"`` etc.) so the cost panel can
    surface "which cloud / which service" alongside the service name.
    Drivers leave them empty when the billing API doesn't carry the
    detail.
    """

    managed_service_guid: str
    amount_cents: int
    currency: str = "USD"
    provider: str = ""
    service: str = ""


@dataclass(frozen=True)
class BillingActualsUnavailable:
    """Returned when the actuals query can't run (credentials missing,
    billing API unreachable, account not enabled for cost export). The
    collector logs + skips the provider rather than failing the whole
    snapshot — partial data is more useful than no data on a multi-
    cloud install where one cloud's billing is unreachable.
    """

    reason: str
    """One of: 'unauthenticated' | 'api_error' | 'not_enabled' |
    'unsupported'. Free-text detail in ``message``."""

    message: str = ""


BillingActualsResult = list[BillingActualLineItem] | BillingActualsUnavailable


class BillingActualsClient(Protocol):
    """Optional driver capability — surface *actual* spend grouped by
    the platform's ``astrolift.io/managed_service_id`` tag.

    Drivers MUST query the live cloud billing / cost-management API.
    No hard-coded amounts, no caching beyond the per-call response.

    Implementations should:
    1. Issue a single API call covering the requested ``[start, end]``
       window with a tag/label group-by on
       ``astrolift.io/managed_service_id`` (per cloud's serialization).
    2. Sum line items per tag value and return one
       :class:`BillingActualLineItem` per
       (managed_service_guid, currency) pair. Rows whose tag is
       missing or empty are emitted with ``managed_service_guid=""``
       — the collector buckets those under "Shared / untagged".
    3. Return :class:`BillingActualsUnavailable` on any failure mode
       rather than raising — the collector logs and moves on so one
       broken cloud doesn't fail the snapshot for the others.

    The group-by key must be the spelling this cloud's managed-service
    drivers actually stamp. Drivers within one cloud disagreeing on
    that spelling is the same bug as not stamping it at all: the
    group-by names one key, and every resource tagged under a
    different one silently lands in the untagged bucket.

    Date semantics: ``start`` is inclusive, ``end`` is exclusive. The
    typical caller asks for "yesterday's spend" with
    ``start=today-1, end=today``.
    """

    def query_actuals_by_service(
        self,
        *,
        start: date,
        end: date,
        currency: str = "USD",
    ) -> BillingActualsResult: ...
