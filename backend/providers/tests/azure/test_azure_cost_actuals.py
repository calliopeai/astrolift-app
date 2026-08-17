"""Tests for Azure BillingActuals client (#502).

Stubs the Cost Management Query client to exercise the rows/columns
response shape → BillingActualLineItem translation without hitting
Azure.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any

from _sdk.cost import BillingActualsUnavailable
from azure.cost import (
    AZURE_MANAGED_SERVICE_TAG_KEY,
    AzureBillingActuals,
    AzureBillingActualsConfig,
)


class _FakeCostMgmt:
    """Minimal Cost Management Query fake.

    ``response`` is the SimpleNamespace (or dict) the real SDK
    returns; ``raises`` is an Exception to raise instead. The fake
    exposes the ``query.usage(scope=, parameters=)`` shape the
    Astrolift client calls.
    """

    def __init__(self, *, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

        outer = self

        class _Query:
            def usage(self, *, scope, parameters):
                outer.calls.append({"scope": scope, "parameters": parameters})
                if outer.raises is not None:
                    raise outer.raises
                return outer.response

        self.query = _Query()


def _build_response(*, rows, columns=None):
    """Cost Management's wire shape: properties.{rows, columns}. The
    columns default mirrors what the SDK returns for our query
    (Cost, TagValue, Currency)."""
    if columns is None:
        columns = [
            {"name": "Cost", "type": "Number"},
            {"name": AZURE_MANAGED_SERVICE_TAG_KEY, "type": "String"},
            {"name": "Currency", "type": "String"},
        ]
    # The real SDK returns a typed object with .columns / .rows
    # attrs; SimpleNamespace matches that for our purposes.
    return SimpleNamespace(rows=rows, columns=columns)


def test_returns_unavailable_when_scope_unconfigured():
    """An empty scope means the operator hasn't supplied the
    subscription id — surface the configuration hint."""
    client = AzureBillingActuals(config=AzureBillingActualsConfig())

    result = client.query_actuals_by_service(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert result.reason == "not_enabled"
    assert "scope" in result.message.lower()


def test_tagged_rows_become_line_items():
    """Each Cost Management row carries (cost, tag_value, currency)
    in column order — the client emits one BillingActualLineItem per
    distinct (managed_service_guid, currency) pair."""
    cm = _FakeCostMgmt(
        response=_build_response(
            rows=[
                [12.34, "bg-1", "USD"],
                [0.99, "bg-2", "USD"],
            ]
        )
    )
    client = AzureBillingActuals(config=AzureBillingActualsConfig(cost_mgmt_client=cm, scope="subscriptions/x"))

    result = client.query_actuals_by_service(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, list)
    by_guid = {i.managed_service_guid: i for i in result}
    assert by_guid["bg-1"].amount_cents == 1234
    assert by_guid["bg-2"].amount_cents == 99
    assert all(i.provider == "azure" for i in result)


def test_quoted_tag_value_normalized():
    """Cost Management returns tag values quoted (``"bg-1"`` not
    ``bg-1``) — the client strips the quotes so the binding lookup
    matches the platform's bare GUIDs."""
    cm = _FakeCostMgmt(response=_build_response(rows=[[1.00, '"bg-quoted"', "USD"]]))
    client = AzureBillingActuals(config=AzureBillingActualsConfig(cost_mgmt_client=cm, scope="subscriptions/x"))

    result = client.query_actuals_by_service(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, list)
    assert result[0].managed_service_guid == "bg-quoted"


def test_untagged_row_emits_empty_managed_service_guid():
    """Cost Management returns None for the tag value when the
    resource carries no matching tag — normalize to ``""`` for the
    Shared / untagged bucket."""
    cm = _FakeCostMgmt(response=_build_response(rows=[[3.21, None, "USD"]]))
    client = AzureBillingActuals(config=AzureBillingActualsConfig(cost_mgmt_client=cm, scope="subscriptions/x"))

    result = client.query_actuals_by_service(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, list)
    assert result[0].managed_service_guid == ""
    assert result[0].amount_cents == 321


def test_zero_amount_rows_dropped():
    """Zero-cost rows clutter the snapshot without adding signal."""
    cm = _FakeCostMgmt(
        response=_build_response(
            rows=[
                [0.0, "bg-zero", "USD"],
                [4.20, "bg-real", "USD"],
            ]
        )
    )
    client = AzureBillingActuals(config=AzureBillingActualsConfig(cost_mgmt_client=cm, scope="subscriptions/x"))

    result = client.query_actuals_by_service(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, list)
    assert [i.managed_service_guid for i in result] == ["bg-real"]


def test_currency_mismatch_row_skipped():
    """A row in a non-matching currency is dropped — mixing currencies
    silently is wrong."""
    cm = _FakeCostMgmt(
        response=_build_response(
            rows=[
                [1.00, "bg-1", "USD"],
                [2.00, "bg-2", "EUR"],
            ]
        )
    )
    client = AzureBillingActuals(config=AzureBillingActualsConfig(cost_mgmt_client=cm, scope="subscriptions/x"))

    result = client.query_actuals_by_service(
        start=dt.date(2026, 5, 15),
        end=dt.date(2026, 5, 16),
        currency="USD",
    )

    assert isinstance(result, list)
    assert [i.managed_service_guid for i in result] == ["bg-1"]


def test_unauthorized_maps_to_unauthenticated():
    """A 401/403 from Cost Management should route to
    unauthenticated, not api_error — operator needs a clearer hint
    on which credential to fix."""
    cm = _FakeCostMgmt(raises=Exception("403 Forbidden: not authorized for scope"))
    client = AzureBillingActuals(config=AzureBillingActualsConfig(cost_mgmt_client=cm, scope="subscriptions/x"))

    result = client.query_actuals_by_service(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert result.reason == "unauthenticated"


def test_generic_failure_maps_to_api_error():
    cm = _FakeCostMgmt(raises=RuntimeError("Cost Mgmt was offline"))
    client = AzureBillingActuals(config=AzureBillingActualsConfig(cost_mgmt_client=cm, scope="subscriptions/x"))

    result = client.query_actuals_by_service(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert result.reason == "api_error"


def test_invalid_window_rejected():
    """start >= end never reaches Cost Management."""
    cm = _FakeCostMgmt()
    client = AzureBillingActuals(config=AzureBillingActualsConfig(cost_mgmt_client=cm, scope="subscriptions/x"))

    result = client.query_actuals_by_service(start=dt.date(2026, 5, 16), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert cm.calls == []


def test_unexpected_columns_maps_to_api_error():
    """If the SDK returns a column layout we don't recognize, fail
    open with api_error rather than guessing — better to surface a
    clear error than silently miss every row."""
    cm = _FakeCostMgmt(
        response=_build_response(
            rows=[[1, 2, 3]],
            columns=[
                {"name": "ColA", "type": "String"},
                {"name": "ColB", "type": "Number"},
                {"name": "ColC", "type": "String"},
            ],
        )
    )
    client = AzureBillingActuals(config=AzureBillingActualsConfig(cost_mgmt_client=cm, scope="subscriptions/x"))

    result = client.query_actuals_by_service(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert result.reason == "api_error"


def test_request_carries_correct_scope_and_grouping():
    """Lock the wire shape: scope flows through verbatim, grouping
    is TagKey on the Azure-safe astrolift-managed-service-id key."""
    cm = _FakeCostMgmt(response=_build_response(rows=[]))
    client = AzureBillingActuals(
        config=AzureBillingActualsConfig(
            cost_mgmt_client=cm,
            scope="subscriptions/abc-123",
        )
    )

    client.query_actuals_by_service(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert len(cm.calls) == 1
    call = cm.calls[0]
    assert call["scope"] == "subscriptions/abc-123"
    grouping = call["parameters"]["dataset"]["grouping"]
    assert grouping == [{"type": "TagKey", "name": AZURE_MANAGED_SERVICE_TAG_KEY}]
    assert call["parameters"]["type"] == "ActualCost"
