"""CertExpiryTickWorkflow — one tick of the certificate-expiry monitor.

Runs daily via the platform schedule registry. Thin Temporal entry point
around :func:`astrolift_workflows.activities.cert_expiry.
check_cert_expiry_tick` so the sweep has durable retries and each pass is
its own run in the Temporal UI.

Coalescing tick model (mirrors the uptime + alert ticks): the activity
timeout is bounded well under the interval so a stuck sweep can't overlap
the next. The bound is generous rather than tight because the sweep talks
to each domain's TLS driver to refresh the cached cert snapshot before
evaluating it.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.cert_expiry import (
        CertExpirySummary,
        check_cert_expiry_tick,
    )


_TICK_TIMEOUT = timedelta(minutes=30)
"""Well under the 24 h interval; roomy enough for a fleet-wide sweep of
per-domain cert-provider reads."""


@workflow.defn(name="CertExpiryTickWorkflow")
class CertExpiryTickWorkflow:
    @workflow.run
    async def run(self) -> CertExpirySummary:
        return await workflow.execute_activity(
            check_cert_expiry_tick,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
