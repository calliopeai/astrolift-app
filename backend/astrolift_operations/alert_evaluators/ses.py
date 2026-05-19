"""SES bounce-rate / complaint-rate predicate kinds (#757, #627, #639).

Two handlers wired into the alert evaluator dispatcher:

* ``ses_bounce_rate`` — fires when the 7-day rolling bounce rate
  (bounces / delivery_attempts × 100) exceeds the predicate's
  ``threshold_pct``. Default threshold is 5.0% — AWS itself flags
  accounts whose rolling rate crosses 5% as at-risk and 10% as
  paused. Operators typically want a warning well before SES throttles
  them, so 5.0% is the conservative default and individual rules may
  set lower (e.g. 2.0% for high-deliverability tenants).
* ``ses_complaint_rate`` — same shape, sourced from the ``complaints``
  counter rather than ``bounces``. AWS pauses accounts at 0.5% so the
  default threshold here is 0.1% (a 5× safety margin).

Both handlers read from :class:`EmailObservabilityDriver.get_send_statistics`
which returns 15-minute buckets for the last 14 days oldest-first. We
slice the last 7 days × 24 hours × 4 buckets = 672 points and compute
the rate over that window. Zero-attempt windows are explicitly
no-fire — an idle account can't have a "rate" — to avoid flapping on
mailers that send in bursts.

Why a tail slice rather than a timestamp filter:

* The SDK contract orders the list oldest-first and guarantees a
  uniform 15-minute cadence; a tail slice is O(1) and matches the
  upstream payload shape exactly. Drivers that down-sample (Azure
  ACS aggregates daily, GCP's stub returns hourly) still produce an
  ordered list — the tail slice grabs the rightmost chunk regardless,
  which is what an operator means by "the recent window".
* A timestamp filter would force every driver to populate ``timestamp``
  consistently; the protocol allows synthetic timestamps for stubs
  (GCP, Azure) and a tail-slice consumer doesn't care.

The handler returns ``False`` (no fire) on any driver-side exception —
the dispatcher already logs the traceback. Treating driver failures as
"no fire" is safer than "fire": a region partition shouldn't page on-call
with a metric the operator can't act on; the next eval-loop tick gets a
fresh read.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_operations.models import AlertRule

log = logging.getLogger(__name__)


# 7 days × 24 hours × 4 (15-min buckets/hour) = 672 buckets.
_SEVEN_DAY_BUCKETS = 7 * 24 * 4

# Tuned defaults — see module docstring. AlertRule predicates may override
# via ``threshold_pct``.
_DEFAULT_BOUNCE_THRESHOLD_PCT = 5.0
_DEFAULT_COMPLAINT_THRESHOLD_PCT = 0.1


def _resolve_driver(rule: AlertRule):
    """Pull the EmailObservabilityDriver for this rule's bound service.

    Returns ``None`` when the rule isn't bound to a managed service or
    the bound service isn't on a cloud with an email-observability
    driver. The caller treats ``None`` as no-fire.
    """
    from astrolift_services.email_observability import driver_for_plugin_slug

    service = rule.managed_service
    if service is None:
        log.debug(
            "ses-predicate: rule %s has no managed_service binding; skipping",
            rule.pk,
        )
        return None
    plugin = service.app_environment.tenant_cluster.provider_plugin
    region = (service.config or {}).get("region") or "us-east-1"
    try:
        return driver_for_plugin_slug(plugin_slug=plugin.slug, region=region)
    except LookupError:
        log.debug(
            "ses-predicate: no driver for plugin %r region %r (rule %s)",
            plugin.slug,
            region,
            rule.pk,
        )
        return None


def _recent_send_stats(rule: AlertRule):
    """7-day tail of ``get_send_statistics`` for the bound service, or
    None when the driver isn't resolvable.
    """
    driver = _resolve_driver(rule)
    if driver is None:
        return None
    stats = driver.get_send_statistics()
    if not stats:
        return []
    if len(stats) > _SEVEN_DAY_BUCKETS:
        return stats[-_SEVEN_DAY_BUCKETS:]
    return stats


def _rate_pct(numerator: int, denominator: int) -> float:
    """Safe percentage; returns 0.0 when denominator is 0 (the caller
    short-circuits to no-fire on a zero-attempt window upstream)."""
    if denominator <= 0:
        return 0.0
    return (numerator / denominator) * 100.0


def evaluate_ses_bounce_rate(rule: AlertRule, predicate: dict) -> bool:
    """Fire when the 7d rolling bounce rate exceeds ``threshold_pct``."""
    threshold = float(predicate.get("threshold_pct", _DEFAULT_BOUNCE_THRESHOLD_PCT))
    recent = _recent_send_stats(rule)
    if not recent:
        return False
    total_attempts = sum(int(s.delivery_attempts) for s in recent)
    if total_attempts == 0:
        return False
    total_bounces = sum(int(s.bounces) for s in recent)
    return _rate_pct(total_bounces, total_attempts) > threshold


def evaluate_ses_complaint_rate(rule: AlertRule, predicate: dict) -> bool:
    """Fire when the 7d rolling complaint rate exceeds ``threshold_pct``."""
    threshold = float(predicate.get("threshold_pct", _DEFAULT_COMPLAINT_THRESHOLD_PCT))
    recent = _recent_send_stats(rule)
    if not recent:
        return False
    total_attempts = sum(int(s.delivery_attempts) for s in recent)
    if total_attempts == 0:
        return False
    total_complaints = sum(int(s.complaints) for s in recent)
    return _rate_pct(total_complaints, total_attempts) > threshold


__all__ = [
    "evaluate_ses_bounce_rate",
    "evaluate_ses_complaint_rate",
]
