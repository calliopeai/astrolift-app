"""Private AWS infrastructure actual adapter; not exposed as product costing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _sdk._telemetry import driver_op
from _sdk.cost import BillingActualLineItem, BillingActualsResult, BillingActualsUnavailable

if TYPE_CHECKING:
    from datetime import date

AWS_MANAGED_SERVICE_TAG_KEY = "astrolift.io/managed_service_id"

# ---- billing actuals (#502) ---------------------------------------


@dataclass(frozen=True)
class AWSBillingActualsConfig:
    """Per-cloud config for the actuals client. ``ce_client`` is an
    injectable boto3 ``client('ce')`` so tests can stub the Cost
    Explorer responses without hitting AWS."""

    ce_client: Any | None = None
    """boto3 client('ce'). Inject for tests."""


class AWSBillingActuals:
    """Reads actual AWS spend grouped by ``AWS_MANAGED_SERVICE_TAG_KEY``
    via Cost Explorer's ``GetCostAndUsage`` API.

    That is ``astrolift.io/managed_service_id``. This text used to say
    ``astrolift.io/binding``, which the code has not grouped on since
    #1419 moved attribution onto the managed-service id — and which no
    resource carries anyway, because ``ProvisionSpec.binding_id`` is set
    by nothing (#1470). Anyone debugging an attribution gap against the
    old sentence would have activated the wrong cost-allocation tag.

    Cost Explorer returns one ``Group`` per distinct tag value plus a
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
        try:
            response = self._client.get_cost_and_usage(
                TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                Granularity="DAILY",
                Metrics=["AmortizedCost"],
                GroupBy=[{"Type": "TAG", "Key": AWS_MANAGED_SERVICE_TAG_KEY}],
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
                        f"Activate {AWS_MANAGED_SERVICE_TAG_KEY!r} as a cost-allocation tag "
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
                # Keys come back as
                # ['astrolift.io/managed_service_id$<value>'] ('$'
                # between tag key and value). Missing tags surface as
                # ['astrolift.io/managed_service_id$'] (no value)
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
                managed_service_guid=tag_value,
                amount_cents=cents,
                currency=currency,
                provider="aws",
                service="",
            )
            for tag_value, cents in totals_cents.items()
        ]
