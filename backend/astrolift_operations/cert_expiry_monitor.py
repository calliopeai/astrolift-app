"""Certificate-expiry monitoring sweep — the missing caller for
:mod:`astrolift_operations.cert_expiry`.

``cert_expiry`` (#155, spec 13 §5.3) is the policy layer: it buckets a
cert's health and reports which of the 30 / 14 / 7-day reminder
thresholds just crossed and whether to escalate. Nothing called it, so
Astrolift had no certificate-expiry alerting at all. Worse,
``CustomDomain.cert_expires_at`` was refreshed only when a human
happened to open the domain observability card, and the cached value was
never compared against a threshold — a cert that stopped renewing went
unnoticed until it expired and TLS broke in production.

This module is that caller, run once a day by the schedule registry
(``CertExpiryTickWorkflow``). Same shape as its sweep siblings
``uptime_probe.probe_all_deployed_apps`` and
``alert_engine.run_alert_sweep``: a per-row try/except so one bad domain
can't abort the sweep, and ``Event.emit`` as the fan-out path.

Per domain:

1. **Refresh the driver snapshot first**, through the same TTL-guarded
   refresher the resolver uses. Alerting off a cache only a page view
   fills would page about a cert that silently renewed weeks ago (ACM
   renews ~60 days out, cert-manager ~30), so the reading has to be the
   platform's own, not a visitor's. Best-effort: no cluster, no TLS
   driver, or a driver that raises leaves the cached value in place and
   the evaluation still runs on it.
2. **Evaluate** the cached snapshot against the policy, passing the
   row's own ``cert_expiry_checked_at`` as the crossing watermark so
   each reminder threshold fires exactly once rather than re-paging
   every tick.
3. **Emit** ``domain.cert_expiring`` on a threshold crossing and
   ``domain.cert_renewal_failed`` while the cert sits inside the 7-day
   escalation window with a failed renewal.
4. **Stamp** the watermark. Only after a successful emit — a raised
   emit leaves the watermark alone so the next sweep retries the
   crossing instead of swallowing it.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from astrolift_lifecycle.models import CustomDomain
from astrolift_operations.cert_expiry import CertSnapshot, evaluate
from core.events import Event

log = logging.getLogger(__name__)

EXPIRING_EVENT = "domain.cert_expiring"
RENEWAL_FAILED_EVENT = "domain.cert_renewal_failed"

# Driver-reported renewal status that means the last renewal attempt
# failed (``cert_observability_status`` is copied verbatim from the TLS
# driver's ``renewal_status``: 'auto' / 'manual' / 'failed' / 'unknown').
_FAILED_RENEWAL_STATUS = "failed"


def _snapshot_for(domain: CustomDomain) -> CertSnapshot:
    """Map a CustomDomain row onto the policy's input.

    ``renewal_attempts`` is 1 whenever a failure is observed and 0
    otherwise: the platform records only the *latest* renewal outcome,
    not a consecutive-failure counter, and the policy reads the field
    solely as ``>= 1``. Reporting the one failure we can actually see is
    the honest floor — reporting 0 would hide the RENEWAL_FAILED bucket
    behind the plain expiry buckets.
    """
    failed = (
        domain.cert_observability_status == _FAILED_RENEWAL_STATUS
        or domain.certificate_state == CustomDomain.CertificateState.FAILED
    )
    return CertSnapshot(
        not_after=domain.cert_expires_at,
        last_renewal_failed=failed,
        renewal_attempts=1 if failed else 0,
    )


def _refresh_snapshot(domain: CustomDomain) -> None:
    """Pull the current cert metadata from the TLS driver, best-effort.

    Reuses the resolver's refresher rather than re-deriving the
    cluster -> driver -> ``list_certificates`` path here: one
    implementation of "what does the cloud say about this cert", one 1h
    TTL, one set of swallowed driver errors. Imported inside the
    function because the resolver module pulls in the whole GraphQL
    schema.
    """
    from astrolift_lifecycle.schema.queries import _refresh_cert_metadata_if_stale

    app = domain.registered_app
    _refresh_cert_metadata_if_stale(
        [domain],
        app_slug=app.slug,
        org_id=app.organization_id,
    )


def _payload_for(domain: CustomDomain, status, *, threshold_days: int | None) -> dict:
    app = domain.registered_app
    return {
        "hostname": domain.hostname,
        "app_slug": app.slug,
        "domain_guid": str(domain.guid),
        "health": str(status.health),
        "days_until_expiry": status.days_until_expiry,
        "threshold_days": threshold_days,
        "expires_at": domain.cert_expires_at.isoformat() if domain.cert_expires_at else "",
        "renewal_status": domain.cert_observability_status or "",
        # How fresh the reading is. The refresh above is best-effort, so an
        # operator judging an alert needs to see when the platform last
        # actually heard from the cert provider.
        "snapshot_refreshed_at": (
            domain.cert_metadata_refreshed_at.isoformat() if domain.cert_metadata_refreshed_at else ""
        ),
    }


def _emit(domain: CustomDomain, event_type: str, payload: dict) -> None:
    app = domain.registered_app
    Event.emit(
        event_type,
        payload=payload,
        resource_kind="custom_domain",
        resource_id=str(domain.guid),
        registered_app_id=app.pk,
        organization_id=app.organization_id,
    )


def check_domain(domain: CustomDomain, *, now=None) -> dict[str, int]:
    """Evaluate one domain's cert and emit whatever it warrants.

    Returns ``{"reminders": n, "escalations": n}`` for the sweep summary
    (``reminders`` is 0 or 1 — see the batching note below).
    """
    now = now or timezone.now()
    _refresh_snapshot(domain)
    if domain.cert_expires_at is None:
        # The refresh can only add an expiry, never clear one, so this is
        # the "selected before, unusable now" guard rather than a filter.
        return {"reminders": 0, "escalations": 0}

    status = evaluate(
        _snapshot_for(domain),
        now=now,
        last_check_at=domain.cert_expiry_checked_at,
    )

    reminders = 0
    if status.thresholds_to_fire:
        # One notification per tick, about the tightest threshold crossed.
        # A first sweep (or one after a gap) can cross 30 / 14 / 7 at once;
        # three simultaneous pages about the same cert is noise, and the
        # tightest one is the true urgency. The full set rides in the
        # payload so nothing is lost.
        payload = _payload_for(domain, status, threshold_days=min(status.thresholds_to_fire))
        payload["thresholds_crossed"] = list(status.thresholds_to_fire)
        _emit(domain, EXPIRING_EVENT, payload)
        reminders = 1

    escalations = 0
    if status.should_escalate:
        # Deliberately NOT crossing-gated: a cert inside the 7-day window
        # whose renewal keeps failing is an outage in waiting, so it
        # re-pages each daily sweep until the renewal lands (expiry moves
        # out) or the driver stops reporting failure. Bounded by the
        # window: at most one page a day for at most a week.
        _emit(domain, RENEWAL_FAILED_EVENT, _payload_for(domain, status, threshold_days=None))
        escalations = 1

    domain.cert_expiry_checked_at = now
    domain.save(update_fields=["cert_expiry_checked_at", "updated_at", "version"])
    log.info(
        "cert expiry: %s is %s (%sd left, reminders=%s escalations=%s)",
        domain.hostname,
        status.health,
        status.days_until_expiry,
        reminders,
        escalations,
    )
    return {"reminders": reminders, "escalations": escalations}


def run_cert_expiry_sweep() -> dict[str, int]:
    """One pass over every live domain that has a known cert expiry.

    Rows with no ``cert_expires_at`` are skipped: the platform has never
    heard an expiry for them (state ``not_requested`` / ``issuing``, or a
    BYO cert the driver won't answer for), and there is nothing to
    threshold against.
    """
    domains = (
        CustomDomain.objects.filter(
            deleted_at__isnull=True,
            is_active=True,
            cert_expires_at__isnull=False,
        )
        .select_related("registered_app")
        .order_by("pk")
    )

    checked = 0
    reminders = 0
    escalations = 0
    for domain in domains:
        try:
            outcome = check_domain(domain)
        except Exception:  # noqa: BLE001 — one bad domain must not stop the sweep
            log.exception("cert expiry: check_domain raised for %s", domain.hostname)
            continue
        checked += 1
        reminders += outcome["reminders"]
        escalations += outcome["escalations"]
    return {"checked": checked, "reminders": reminders, "escalations": escalations}
