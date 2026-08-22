"""Cert-expiry monitoring tick activity.

Thin Temporal activity wrapping the synchronous cert-expiry sweep so it
gets durable retries + shows up as its own run history. The ORM work and
the TLS-driver reads are blocking, so it runs in a worker thread
(``thread_sensitive=False``). Mirrors ``activities/uptime.py``.
"""

from __future__ import annotations

import dataclasses

from temporalio import activity


@dataclasses.dataclass
class CertExpirySummary:
    checked: int
    reminders: int
    escalations: int


@activity.defn(name="astrolift.certs.expiry_tick")
async def check_cert_expiry_tick() -> CertExpirySummary:
    """One sweep: evaluate every live custom domain's cached cert against
    the 30 / 14 / 7-day reminder thresholds, emitting
    domain.cert_expiring on a crossing and domain.cert_renewal_failed
    while a cert in the escalation window keeps failing to renew."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_check_cert_expiry_tick_sync, thread_sensitive=False)()


def _check_cert_expiry_tick_sync() -> CertExpirySummary:
    from astrolift_operations.cert_expiry_monitor import run_cert_expiry_sweep

    summary = run_cert_expiry_sweep()
    return CertExpirySummary(
        checked=summary["checked"],
        reminders=summary["reminders"],
        escalations=summary["escalations"],
    )
