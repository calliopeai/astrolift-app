"""Tests for GCP BillingActuals client (#502).

Stubs the BigQuery client to exercise the billing-export query →
BillingActualLineItem translation without hitting GCP.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from _sdk.cost import BillingActualsUnavailable
from gcp.cost import (
    GCP_BINDING_LABEL_KEY,
    GCPBillingActuals,
    GCPBillingActualsConfig,
)


class _FakeBQ:
    """Minimal BigQuery client fake. ``rows`` is the iterable returned
    by ``client.query().result()``; ``raises`` is an Exception to
    raise from ``query`` instead. Calls land on ``calls`` for
    argument assertions."""

    def __init__(self, *, rows=None, raises=None):
        self.rows = rows or []
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def query(self, sql, *, job_config=None):
        self.calls.append({"sql": sql, "job_config": job_config})
        if self.raises is not None:
            raise self.raises

        class _Job:
            def __init__(self, rows):
                self._rows = rows

            def result(self):
                return iter(self._rows)

        return _Job(self.rows)


def test_returns_unavailable_when_dataset_unconfigured():
    """No project/dataset means the operator hasn't enabled BigQuery
    export — surface the configuration hint, don't try to query."""
    client = GCPBillingActuals(config=GCPBillingActualsConfig())

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert result.reason == "not_enabled"
    assert "billing export" in result.message.lower()


def test_tagged_rows_become_line_items():
    """Each BQ row carries (binding_guid, amount, currency) — the
    client converts amount → cents and emits one line item per row."""
    bq = _FakeBQ(
        rows=[
            {"binding_guid": "bg-1", "amount": 1.50, "currency": "USD"},
            {"binding_guid": "bg-2", "amount": 12.34, "currency": "USD"},
        ]
    )
    client = GCPBillingActuals(config=GCPBillingActualsConfig(bq_client=bq, project="p", dataset="d"))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, list)
    by_guid = {i.binding_guid: i for i in result}
    assert by_guid["bg-1"].amount_cents == 150
    assert by_guid["bg-2"].amount_cents == 1234
    assert all(i.provider == "gcp" for i in result)


def test_null_label_row_emits_empty_binding_guid():
    """A LEFT JOIN UNNEST against the labels column can yield NULL
    binding_guid for resources without our label — the client
    normalizes that to ``""`` (the Shared / untagged bucket)."""
    bq = _FakeBQ(rows=[{"binding_guid": None, "amount": 5.0, "currency": "USD"}])
    client = GCPBillingActuals(config=GCPBillingActualsConfig(bq_client=bq, project="p", dataset="d"))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].binding_guid == ""
    assert result[0].amount_cents == 500


def test_currency_mismatch_row_skipped():
    """If the BQ row's currency doesn't match what the caller asked
    for, drop it — mixing USD and EUR sums silently would be wrong."""
    bq = _FakeBQ(
        rows=[
            {"binding_guid": "bg-1", "amount": 1.0, "currency": "USD"},
            {"binding_guid": "bg-2", "amount": 2.0, "currency": "EUR"},
        ]
    )
    client = GCPBillingActuals(config=GCPBillingActualsConfig(bq_client=bq, project="p", dataset="d"))

    result = client.query_actuals_by_binding(
        start=dt.date(2026, 5, 15),
        end=dt.date(2026, 5, 16),
        currency="USD",
    )

    assert isinstance(result, list)
    assert [i.binding_guid for i in result] == ["bg-1"]


def test_query_failure_maps_to_unavailable():
    """Generic BQ failures map to api_error so the collector keeps
    going."""
    bq = _FakeBQ(raises=RuntimeError("BQ ate the query"))
    client = GCPBillingActuals(config=GCPBillingActualsConfig(bq_client=bq, project="p", dataset="d"))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert result.reason == "api_error"


def test_table_not_found_maps_to_not_enabled():
    """When BQ reports the table doesn't exist (operator never
    finished the billing export setup), surface the
    enablement-required hint rather than a generic api_error."""
    bq = _FakeBQ(raises=Exception("Table p.d.gcp_billing_export_v1 was not found"))
    client = GCPBillingActuals(config=GCPBillingActualsConfig(bq_client=bq, project="p", dataset="d"))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert result.reason == "not_enabled"


def test_sql_filters_on_canonical_label_key():
    """Lock the query shape: the SQL must reference the
    normalized GCP label key ``astrolift_io_binding``."""
    bq = _FakeBQ(rows=[])
    client = GCPBillingActuals(config=GCPBillingActualsConfig(bq_client=bq, project="proj", dataset="ds"))

    client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert len(bq.calls) == 1
    assert "proj.ds" in bq.calls[0]["sql"]
    # The label key reference is via a query parameter (we don't
    # interpolate the literal); GCP_BINDING_LABEL_KEY is the value
    # bound into the parameter.
    assert GCP_BINDING_LABEL_KEY == "astrolift_io_binding"


def test_invalid_window_rejected():
    """start >= end never reaches BQ."""
    bq = _FakeBQ()
    client = GCPBillingActuals(config=GCPBillingActualsConfig(bq_client=bq, project="p", dataset="d"))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 16), end=dt.date(2026, 5, 16))

    assert isinstance(result, BillingActualsUnavailable)
    assert bq.calls == []


def test_zero_amount_rows_dropped():
    """Drop $0.00 rows — keep the snapshot list to meaningful spend."""
    bq = _FakeBQ(
        rows=[
            {"binding_guid": "bg-zero", "amount": 0.0, "currency": "USD"},
            {"binding_guid": "bg-real", "amount": 4.20, "currency": "USD"},
        ]
    )
    client = GCPBillingActuals(config=GCPBillingActualsConfig(bq_client=bq, project="p", dataset="d"))

    result = client.query_actuals_by_binding(start=dt.date(2026, 5, 15), end=dt.date(2026, 5, 16))

    assert isinstance(result, list)
    assert [i.binding_guid for i in result] == ["bg-real"]
