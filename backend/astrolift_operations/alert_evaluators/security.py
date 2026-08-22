"""Security-burst predicate kinds — brute force / denial detection (#151, spec 12 §13).

``astrolift_operations.security_events`` shipped the burst *policy* —
sliding-window thresholds, per-org overrides, cooldown de-duplication,
severity escalation — as pure functions with nothing feeding them. The
platform was already writing the raw signal (a DENY ``AuditEvent`` per
failed step-up, failed attestation, denied SSO exchange and every
permission-denied mutation) and never counting it, so a credential-
stuffing run against the control plane produced audit rows and no alert.

These handlers are the counting half, registered as predicate kinds so
the live sweep (``alert_engine.run_alert_sweep``, one Temporal tick)
evaluates them alongside the SES kinds.

Predicate shape — every threshold optional, ``security_events.DEFAULTS``
supplies the spec values for the ones the operator leaves out::

    {
        "kind": "failed_login_burst",
        "threshold": 5,
        "window_seconds": 300,
        "cooldown_seconds": 1800
    }

The two signals, split by action prefix so one burst is never counted
twice:

* ``failed_login_burst`` — DENY rows under the ``auth.`` prefix, which is
  every failed authentication the identity app records
  (``step_up._emit_deny_audit``, ``step_up_sso``, ``attestation.service``).
* ``permission_denied_burst`` — DENY rows on every other action; that is
  what ``core.mutations`` stamps when a mutation returns
  PERMISSION_DENIED.

The count is *per subject*, not per org: the spec thresholds read "N
failures for one user", so an org-wide count would trip on ordinary
traffic in a busy tenant while never singling out an attacker. We
evaluate the worst subject in the window — one open AlertEvent per rule
is what the engine's transition model holds, and the per-actor breakdown
is in the audit log the operator opens next.

``token_from_new_ip`` — the third detector in ``security_events`` — is
deliberately NOT registered. ``core.mutations.AuditEntry`` carries
neither client IP nor actor kind, so ``audit_writer`` leaves
``request_ip`` empty on every row it writes; the only producer that fills
it is the MCP view. "Used from an unseen IP" over that data compares
empty strings against empty strings and would fire on nothing (or on
everything). It needs the audit spine to record the request IP first,
which is a change to that spine, not to this dispatcher.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import Count
from django.utils import timezone

from astrolift_operations.security_events import (
    DetectorKind,
    config_with_defaults,
)
from astrolift_operations.security_events import evaluate as evaluate_detector

if TYPE_CHECKING:
    from astrolift_operations.models import AlertRule

log = logging.getLogger(__name__)


# Failed-authentication audit actions all live under this prefix; the
# permission-denial detector takes the complement.
_AUTH_ACTION_PREFIX = "auth."

# The knobs ``DetectorConfig`` accepts. Predicate JSON is operator-typed
# (createAlertRule takes it free-form), so a quoted "5" is likely; coerce
# it rather than let the dataclass validator raise and leave the rule
# silently no-fire for the rest of its life.
_NUMERIC_KNOBS = ("threshold", "window_seconds", "cooldown_seconds", "auto_remediate_at")


def _overrides(predicate: dict) -> dict[str, int]:
    """The per-rule threshold overrides, coerced to ints."""
    out: dict[str, int] = {}
    for knob in _NUMERIC_KNOBS:
        value = predicate.get(knob)
        if value is not None:
            out[knob] = int(value)
    return out


def _worst_subject_count(
    rule: AlertRule,
    *,
    window_seconds: int,
    now,
    auth_surface: bool,
) -> int:
    """Highest per-actor DENY count inside the window, for this rule's org."""
    from astrolift_operations.models import AuditEvent

    rows = AuditEvent.objects.filter(
        organization_id=rule.organization_id,
        decision=AuditEvent.Decision.DENY,
        occurred_at__gte=now - timedelta(seconds=window_seconds),
        occurred_at__lte=now,
    )
    rows = (
        rows.filter(action__startswith=_AUTH_ACTION_PREFIX)
        if auth_surface
        else rows.exclude(action__startswith=_AUTH_ACTION_PREFIX)
    )
    # Rows with no actor (``actor_id`` "") stay in and group together: an
    # unauthenticated brute force is precisely the case with nobody to
    # attribute it to, and dropping that bucket would blind the detector
    # to the attack it exists for.
    worst = rows.values("actor_kind", "actor_id").annotate(hits=Count("guid")).order_by("-hits").first()
    return int(worst["hits"]) if worst else 0


def _last_alert_at(rule: AlertRule):
    """When this rule last fired — the cooldown input.

    The engine already refuses to re-fire while an event is open; the
    cooldown is what keeps the *next* burst, minutes after the last one
    resolved, from paging on-call a second time.
    """
    from astrolift_operations.models import AlertEvent

    return (
        AlertEvent.objects.filter(rule=rule).order_by("-fired_at").values_list("fired_at", flat=True).first()
    )


def _evaluate_burst(
    rule: AlertRule,
    predicate: dict,
    *,
    kind: DetectorKind,
    auth_surface: bool,
) -> bool:
    config = config_with_defaults(kind, overrides=_overrides(predicate))
    now = timezone.now()
    count = _worst_subject_count(
        rule,
        window_seconds=config.window_seconds,
        now=now,
        auth_surface=auth_surface,
    )
    decision = evaluate_detector(
        config=config,
        event_count_in_window=count,
        last_alert_at=_last_alert_at(rule),
        now=now,
    )
    if decision.suppressed_by_cooldown:
        log.info(
            "security-predicate: %s still bursting (%d) on rule %s, inside cooldown",
            kind.value,
            decision.count,
            rule.pk,
        )
    elif decision.fire:
        # The escalated severity and ``auto_remediate`` land in the log
        # rather than on the AlertEvent: the event's severity is the
        # rule's, and there is no account-lock / actor-rate-limit
        # actuator in the platform to hand the remediation flag to yet.
        log.warning(
            "security-predicate: %s firing on rule %s (count=%d severity=%s auto_remediate=%s)",
            kind.value,
            rule.pk,
            decision.count,
            decision.severity.value,
            decision.auto_remediate,
        )
    return decision.fire


def evaluate_failed_login_burst(rule: AlertRule, predicate: dict) -> bool:
    """Fire when one actor's failed-authentication count crosses the threshold."""
    return _evaluate_burst(
        rule,
        predicate,
        kind=DetectorKind.FAILED_LOGIN_BURST,
        auth_surface=True,
    )


def evaluate_permission_denied_burst(rule: AlertRule, predicate: dict) -> bool:
    """Fire when one actor's permission-denial count crosses the threshold."""
    return _evaluate_burst(
        rule,
        predicate,
        kind=DetectorKind.PERMISSION_DENIED_BURST,
        auth_surface=False,
    )


__all__ = [
    "evaluate_failed_login_burst",
    "evaluate_permission_denied_burst",
]
