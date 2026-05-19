"""Predicate-kind dispatcher for the alert-rule evaluation loop (#757).

``AlertRule.predicate`` is an opaque JSON blob; this package turns one
into a fire/no-fire decision. The existing default-rule templates in
``astrolift_operations.alert_rules`` carry PromQL expressions that the
metrics layer fires asynchronously; this dispatcher is for **synchronous
predicate kinds** that pull from a backend SDK call rather than waiting
on Prometheus to scrape and alert. The first two consumers are the SES
bounce-rate and complaint-rate predicates, which read from
``EmailObservabilityDriver.get_send_statistics()`` directly.

Predicate shape::

    {
        "kind": "ses_bounce_rate",
        "threshold_pct": 5.0
    }

The dispatcher resolves ``kind`` against ``KIND_HANDLERS`` and calls the
matching handler. Handlers MUST be pure of side-effects beyond the
driver-read they perform — they only return a bool. Persisting the
``AlertEvent`` row + channel fan-out happens in the calling loop, which
is owned by the alert-delivery workflow.

Unknown kinds return ``False`` (no fire) — the evaluator logs but doesn't
raise so a typo in one rule's JSON doesn't poison every other rule the
loop is walking. PromQL-style legacy predicates (the ones that carry a
``metric`` / ``comparator`` / ``threshold`` shape, no ``kind``) are
handled by the existing PromQL path; this dispatcher only fires on
predicates that explicitly carry a ``kind``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_operations.models import AlertRule

from astrolift_operations.alert_evaluators.ses import (
    evaluate_ses_bounce_rate,
    evaluate_ses_complaint_rate,
)

log = logging.getLogger(__name__)


# Predicate kind → handler. Each handler takes ``(rule, predicate_dict)``
# and returns ``True`` when the rule should fire. Handlers MAY consult
# ``rule.managed_service`` (which is the FK added in migration 0015)
# without re-querying. Adding a new kind is one line here + a handler.
KIND_HANDLERS: dict[str, Callable[[AlertRule, dict], bool]] = {
    "ses_bounce_rate": evaluate_ses_bounce_rate,
    "ses_complaint_rate": evaluate_ses_complaint_rate,
}


def evaluate(rule: AlertRule) -> bool:
    """Return ``True`` when this rule's predicate currently fires.

    Returns ``False`` for predicates without a recognized ``kind`` —
    those are evaluated through the PromQL / metrics path elsewhere
    in the operations stack, not here.
    """
    predicate = dict(rule.predicate or {})
    kind = predicate.get("kind")
    if not kind:
        return False
    handler = KIND_HANDLERS.get(kind)
    if handler is None:
        log.debug(
            "alert_evaluators: no handler for predicate kind %r on rule %s",
            kind,
            rule.pk,
        )
        return False
    try:
        return handler(rule, predicate)
    except Exception:
        # An evaluator that explodes must not take down the loop —
        # log + treat as no-fire so the next tick gets a fresh read.
        log.exception(
            "alert_evaluators: handler %r raised for rule %s",
            kind,
            rule.pk,
        )
        return False


__all__ = [
    "KIND_HANDLERS",
    "evaluate",
    "evaluate_ses_bounce_rate",
    "evaluate_ses_complaint_rate",
]
