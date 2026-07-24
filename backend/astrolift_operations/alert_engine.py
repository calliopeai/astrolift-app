"""Alert evaluation loop — the missing runtime for AlertRule/AlertEvent.

The models (:class:`AlertRule`, :class:`AlertEvent`) and the per-predicate
dispatcher (:mod:`astrolift_operations.alert_evaluators`) existed but
nothing walked the active rules on a cadence. This module is that sweep,
run once per tick by the platform schedule registry (mirrors
``uptime_probe.probe_all_deployed_apps``):

1. Load every active :class:`AlertRule`.
2. Ask ``alert_evaluators.evaluate`` whether the rule currently fires.
   That dispatcher is safe-by-default — a predicate with no recognized
   ``kind`` (the seeded PromQL defaults), an unresolvable driver, or an
   unconfigured metrics backend all resolve to *no-fire* rather than an
   error — so a fresh install with only default rules produces zero
   events instead of crashing.
3. On a state *transition* (mirrors the uptime prober so we don't re-fire
   every tick):
   * clear → firing: create an :class:`AlertEvent` row and emit
     ``alert.fired`` so the notification fan-out pages subscribers.
   * firing → clear: stamp ``resolved_at`` on the open event and emit
     ``alert.resolved``.
   A rule that keeps firing (open event already present) or keeps clear
   is a no-op — idempotent under repeated ticks.

Muting (``alert_mute``) is honored the way the spec defines it: a muted
rule *still* produces the AlertEvent row (the incident timeline stays
intact) but the outbound ``Event.emit`` is suppressed, so no page goes
out while the operator has silenced it.

The notification fan-out itself stays safe when unconfigured: with no
``NotificationProfile`` the dispatcher audits ``status=no_driver`` and
drops the send (see ``notification_dispatch``), so this loop never emits
a real push/email on a fresh install.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from astrolift_operations import alert_evaluators
from astrolift_operations.alert_mute import is_rule_muted
from astrolift_operations.models import AlertEvent, AlertRule
from core.events import Event

log = logging.getLogger(__name__)

_SUMMARY_MAX = 255  # AlertEvent.summary max_length.


def _open_event_for(rule: AlertRule) -> AlertEvent | None:
    """The rule's currently-firing (unresolved) AlertEvent, if any.

    ``resolved_at IS NULL`` is the "still firing" marker — one open event
    per rule at a time is the transition model, matching the uptime
    prober's "last observation" state."""
    return AlertEvent.objects.filter(rule=rule, resolved_at__isnull=True).order_by("-fired_at").first()


def _fire(rule: AlertRule) -> AlertEvent:
    """Record a firing AlertEvent and (unless muted) emit ``alert.fired``.

    The row is written even when the rule is muted so the incident
    timeline is complete; only the outbound notification is suppressed.
    """
    summary = f"{rule.name} is firing"[:_SUMMARY_MAX]
    event = AlertEvent.objects.create(
        rule=rule,
        organization_id=rule.organization_id,
        fired_at=timezone.now(),
        severity=rule.severity,
        summary=summary,
        detail={
            "predicate": dict(rule.predicate or {}),
            "target": rule.target,
            "target_id": rule.target_id or "",
        },
    )
    if not is_rule_muted(rule):
        _emit_alert_event(rule, event, "alert.fired")
    else:
        log.info("alert engine: rule %s fired but is muted; suppressing page", rule.pk)
    return event


def _resolve(rule: AlertRule, event: AlertEvent) -> None:
    """Resolve the open AlertEvent and (unless muted) emit ``alert.resolved``."""
    event.resolved_at = timezone.now()
    event.save(update_fields=["resolved_at", "updated_at", "version"])
    if not is_rule_muted(rule):
        _emit_alert_event(rule, event, "alert.resolved")


def _emit_alert_event(rule: AlertRule, event: AlertEvent, event_type: str) -> None:
    """Emit through the same ``Event.emit`` path the uptime prober uses.

    Payload keys match ``notification_dispatch``'s alert templates
    (``rule_name`` / ``severity`` / ``summary`` / ``rule_guid``); the
    org id drives recipient resolution + the no-driver no-op."""
    Event.emit(
        event_type,
        payload={
            "rule_name": rule.name,
            "severity": event.severity,
            "summary": event.summary,
            "rule_guid": str(rule.guid),
        },
        resource_kind="alert_rule",
        resource_id=str(rule.guid),
        organization_id=rule.organization_id,
    )
    log.info(
        "alert engine: rule %s (%s) -> %s (event %s)",
        rule.pk,
        rule.name,
        event_type,
        event.pk,
    )


def evaluate_rule(rule: AlertRule) -> str:
    """Evaluate one rule and apply the transition.

    Returns the transition that happened: ``"fired"`` (clear→firing, a new
    AlertEvent), ``"resolved"`` (firing→clear), or ``"none"`` (steady
    state — still firing or still clear, so idempotent no-op)."""
    breach = alert_evaluators.evaluate(rule)
    open_event = _open_event_for(rule)
    if breach and open_event is None:
        _fire(rule)
        return "fired"
    if not breach and open_event is not None:
        _resolve(rule, open_event)
        return "resolved"
    return "none"


def run_alert_sweep() -> dict[str, int]:
    """One evaluation pass over every active AlertRule. Returns a summary.

    ``evaluated`` = rules walked; ``fired`` = clear→firing transitions
    (new AlertEvent rows); ``resolved`` = firing→clear transitions."""
    rules = AlertRule.objects.filter(is_active=True).select_related("organization")

    evaluated = 0
    fired = 0
    resolved = 0
    for rule in rules:
        evaluated += 1
        try:
            transition = evaluate_rule(rule)
        except Exception:  # noqa: BLE001 — one bad rule must not stop the sweep
            log.exception("alert engine: evaluate_rule raised for rule %s", rule.pk)
            continue
        if transition == "fired":
            fired += 1
        elif transition == "resolved":
            resolved += 1
    return {"evaluated": evaluated, "fired": fired, "resolved": resolved}
