"""Private AZURE infrastructure actual adapter; not exposed as product costing."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _sdk._telemetry import driver_op
from _sdk.cost import BillingActualLineItem, BillingActualsResult, BillingActualsUnavailable

if TYPE_CHECKING:
    from datetime import date

AZURE_MANAGED_SERVICE_TAG_KEY = "astrolift-managed-service-id"

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
    """Reads actual Azure spend grouped by ``AZURE_MANAGED_SERVICE_TAG_KEY``
    via the Cost Management Query API.

    That is ``astrolift-managed-service-id``. This text used to say
    ``astrolift-binding``, which the code has not grouped on since #1419
    moved attribution onto the managed-service id — and which no resource
    carries anyway, because ``ProvisionSpec.binding_id`` is set by nothing
    (#1470).

    Cost Management's ``Query`` endpoint accepts a ``Dimensions``
    grouping on tag names; the query returns one row per tag value
    with the summed cost in the account's currency. Resources missing
    the tag are emitted under an empty-string bucket.

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
                    {"type": "TagKey", "name": AZURE_MANAGED_SERVICE_TAG_KEY},
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
            managed_service_guid = _azure_normalize_tag_value(raw_tag)
            row_currency = currency
            if currency_idx is not None:
                with contextlib.suppress(IndexError):
                    row_currency = row[currency_idx] or currency
            if currency and row_currency and row_currency != currency:
                continue
            cents = round(amount * 100)
            key = (managed_service_guid, row_currency)
            totals[key] = totals.get(key, 0) + cents

        return [
            BillingActualLineItem(
                managed_service_guid=service_guid,
                amount_cents=cents,
                currency=row_currency,
                provider="azure",
                service="",
            )
            for (service_guid, row_currency), cents in totals.items()
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
            name_lower == AZURE_MANAGED_SERVICE_TAG_KEY.lower()
            or name_lower == "tagvalue"
            or AZURE_MANAGED_SERVICE_TAG_KEY.lower() in name_lower
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
