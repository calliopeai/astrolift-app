"""Private GCP infrastructure actual adapter; not exposed as product costing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _sdk._telemetry import driver_op
from _sdk.cost import BillingActualLineItem, BillingActualsResult, BillingActualsUnavailable

if TYPE_CHECKING:
    from datetime import date

GCP_MANAGED_SERVICE_LABEL_KEY = "astrolift_io_managed_service_id"

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
