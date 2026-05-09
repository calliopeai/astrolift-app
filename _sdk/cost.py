"""Cost estimation protocol (#78).

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
from typing import Protocol


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
        self, request: CostEstimateRequest,
    ) -> CostResult: ...

    def supported(self, *, kind: str, variant: str) -> bool:
        """Quick check: does this driver have a pricing-API path
        for (kind, variant)? Cheap call — must not hit the network."""
        ...
