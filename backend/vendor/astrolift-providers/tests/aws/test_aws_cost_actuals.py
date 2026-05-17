"""Tests for AWS BillingActuals client (#502).

Stubs the boto3 Cost Explorer client to exercise the Cost Explorer
GroupBy TAG → BillingActualLineItem translation without hitting AWS.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from _sdk.cost import BillingActualsUnavailable
from aws.cost import (
    AWS_BINDING_TAG_KEY,
    AWSBillingActuals,
    AWSBillingActualsConfig,
)


class _FakeCE:
    """Minimal Cost Explorer fake.

    ``response`` is what ``get_cost_and_usage`` returns; ``raises`` is
    an Exception class to raise instead. Calls are captured on
    ``calls`` for argument assertions.
    """

    def __init__(self, *, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def get_cost_and_usage(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.response or {"ResultsByTime": []}


def _build_response(*, groups_by_tag):
    """Cost Explorer's wire shape is:
       ResultsByTime[].Groups[].{Keys: ['astrolift.io/binding$<value>'],
                                Metrics: {AmortizedCost: {Amount: '12.34'}}}.
    Helper builds a single-day bucket from a {tag_value: amount_str} dict."""
    return {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-05-15", "End": "2026-05-16"},
                "Groups": [
                    {
                        "Keys": [f"{AWS_BINDING_TAG_KEY}${tag_value}"],
                        "Metrics": {"AmortizedCost": {"Amount": amount_str, "Unit": "USD"}},
                    }
                    for tag_value, amount_str in groups_by_tag.items()
                ],
            }
        ],
    }


def test_tagged_groups_become_line_items():
    """One Cost Explorer group per binding tag value → one
    BillingActualLineItem each."""
    ce = _FakeCE(
        response=_build_response(
            groups_by_tag={
                "binding-guid-1": "12.34",
                "binding-guid-2": "0.99",
            }
        )
    )
    client = AWSBillingActuals(config=AWSBillingActualsConfig(ce_client=ce))

    result = client.query_actuals_by_binding(
        start=dt.date(2026, 5, 15),
        end=dt.date(2026, 5, 16),
    )

    assert isinstance(result, list)
    by_guid = {i.binding_guid: i for i in result}
    assert by_guid["binding-guid-1"].amount_cents == 1234
    assert by_guid["binding-guid-2"].amount_cents == 99
    assert all(i.provider == "aws" for i in result)
    assert all(i.currency == "USD" for i in result)


def test_untagged_group_emits_empty_binding_guid():
    """Resources missing the tag come back as
    ``astrolift.io/binding$`` (empty value) → the line item carries
    ``binding_guid=""`` so the caller buckets it as
    Shared / untagged."""
    ce = _FakeCE(response=_build_response(groups_by_tag={"": "5.00"}))
    client = AWSBillingActuals(config=AWSBillingActualsConfig(ce_client=ce))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].binding_guid == ""
    assert result[0].amount_cents == 500


def test_multi_day_window_sums_across_buckets():
    """Cost Explorer returns one bucket per day in the window — the
    client sums per-tag across buckets for the caller's window."""
    ce = _FakeCE(
        response={
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-05-15", "End": "2026-05-16"},
                    "Groups": [
                        {
                            "Keys": [f"{AWS_BINDING_TAG_KEY}$bg-1"],
                            "Metrics": {"AmortizedCost": {"Amount": "1.00"}},
                        },
                    ],
                },
                {
                    "TimePeriod": {"Start": "2026-05-16", "End": "2026-05-17"},
                    "Groups": [
                        {
                            "Keys": [f"{AWS_BINDING_TAG_KEY}$bg-1"],
                            "Metrics": {"AmortizedCost": {"Amount": "2.50"}},
                        },
                    ],
                },
            ]
        }
    )
    client = AWSBillingActuals(config=AWSBillingActualsConfig(ce_client=ce))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 17))

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].amount_cents == 350  # $3.50 = 350 cents


def test_zero_amount_rows_dropped():
    """Cost Explorer occasionally returns $0.00 metric rows; the
    client drops them so the caller's snapshot only carries
    meaningful rows."""
    ce = _FakeCE(response=_build_response(groups_by_tag={"bg-zero": "0.00", "bg-real": "1.23"}))
    client = AWSBillingActuals(config=AWSBillingActualsConfig(ce_client=ce))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, list)
    assert [i.binding_guid for i in result] == ["bg-real"]


def test_api_error_maps_to_unavailable():
    """A boto3 raise becomes BillingActualsUnavailable so the caller
    keeps sweeping."""
    ce = _FakeCE(raises=RuntimeError("Cost Explorer is having a bad day"))
    client = AWSBillingActuals(config=AWSBillingActualsConfig(ce_client=ce))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert result.reason == "api_error"
    assert "having a bad day" in result.message


def test_not_enabled_message_routed_to_not_enabled_reason():
    """When the tag isn't activated for cost allocation, AWS surfaces
    a 'not enabled' message; the client routes that to the
    `not_enabled` reason so the operator gets a clearer hint."""
    ce = _FakeCE(raises=RuntimeError("Tag 'astrolift.io/binding' is not enabled for cost allocation"))
    client = AWSBillingActuals(config=AWSBillingActualsConfig(ce_client=ce))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert result.reason == "not_enabled"
    assert AWS_BINDING_TAG_KEY in result.message


def test_invalid_window_rejected():
    """start >= end is operator error — refuse rather than calling
    Cost Explorer (which would 400 with a less useful message)."""
    ce = _FakeCE()
    client = AWSBillingActuals(config=AWSBillingActualsConfig(ce_client=ce))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 16), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert "before" in result.message
    assert ce.calls == []  # didn't call AWS


def test_request_carries_correct_group_by_and_tag_key():
    """Lock the wire shape: the API call must group by TAG on the
    canonical astrolift.io/binding key."""
    ce = _FakeCE(response={"ResultsByTime": []})
    client = AWSBillingActuals(config=AWSBillingActualsConfig(ce_client=ce))

    client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert len(ce.calls) == 1
    call = ce.calls[0]
    assert call["GroupBy"] == [{"Type": "TAG", "Key": AWS_BINDING_TAG_KEY}]
    assert call["TimePeriod"] == {"Start": "2026-05-15", "End": "2026-05-16"}
    assert call["Granularity"] == "DAILY"
    assert "AmortizedCost" in call["Metrics"]
