"""Default-alert-rule seeding — the missing caller for ``render_for_app``.

When an app is registered, the platform seeds one :class:`AlertRule` per
:data:`astrolift_operations.alert_rules.DEFAULT_RULES` template so a fresh
app arrives with the spec-08 §10.2 standard alert set already in place
(high 5xx, high latency, restart loop, OOM, health-check, cron streak,
CPU/memory saturation) rather than an empty alert config.

The pure-policy templates live in ``alert_rules`` (no ORM there); this
module is the ORM bridge: it renders the templates for one app via
``render_for_app`` and persists an :class:`AlertRule` row per template.

Idempotent by design: each seeded rule's name is namespaced by the app
slug (``"<slug>: <title>"``) so it is unique within the org's active
rules (the ``alert_rule_name_unique_active_per_org`` partial constraint),
and the seeder skips any rule whose name already exists live. Re-running
against an already-seeded app is a no-op, so wiring it into a
registration path that can be retried (or that runs on both create and
resync) never duplicates.

The seeded predicates carry the template's PromQL ``expression`` +
``for_seconds`` (no ``kind``), so the synchronous
``alert_evaluators.evaluate`` dispatcher treats them as no-fire — they
are the Prometheus-path rules. Only operator-created ``kind`` predicates
(e.g. ``ses_bounce_rate``) fire through the evaluation loop.
"""

from __future__ import annotations

import logging

from astrolift_operations.alert_rules import Severity as TemplateSeverity
from astrolift_operations.alert_rules import render_for_app
from astrolift_operations.models import AlertRule
from astrolift_operations.security_events import DetectorKind

log = logging.getLogger(__name__)


# Template severities (``info`` / ``warning`` / ``critical``) → the
# AlertRule model's severity values (``info`` / ``warn`` / ``critical``).
# The two vocabularies agree except WARNING→"warn", so translate
# explicitly rather than assuming the string is a valid model choice.
_SEVERITY_MAP: dict[str, str] = {
    TemplateSeverity.INFO.value: AlertRule.Severity.INFO.value,
    TemplateSeverity.WARNING.value: AlertRule.Severity.WARN.value,
    TemplateSeverity.CRITICAL.value: AlertRule.Severity.CRITICAL.value,
}

_MAX_NAME_LEN = 200  # AlertRule.name max_length.


def _rule_name_for(*, app_slug: str, title: str) -> str:
    """App-namespaced rule name, bounded to the column width.

    Namespacing by slug keeps two apps in one org from colliding on the
    org-unique ``name`` constraint (the templates carry static titles that
    don't mention the app)."""
    return f"{app_slug}: {title}"[:_MAX_NAME_LEN]


def seed_default_alert_rules(app) -> int:
    """Seed the default AlertRule set for ``app``. Returns the count created.

    Best-effort + idempotent: skips templates whose (org-unique) rule name
    already exists live, so a re-run creates nothing. Never raises — a
    seeding failure must not fail app registration; the caller wraps this
    too, but we isolate per-rule here so one bad row doesn't skip the rest.
    """
    templates = render_for_app(app_name=app.slug, namespace=app.k8s_namespace)
    created = 0
    for tpl in templates:
        name = _rule_name_for(app_slug=app.slug, title=tpl.title)
        # Default manager excludes soft-deleted rows, so this checks the
        # live set the unique constraint guards.
        if AlertRule.objects.filter(organization_id=app.organization_id, name=name).exists():
            continue
        try:
            AlertRule.objects.create(
                organization_id=app.organization_id,
                name=name,
                target=AlertRule.Target.APP.value,
                target_id=app.slug,
                predicate={
                    "expression": tpl.expression,
                    "for_seconds": tpl.for_seconds,
                    "template_slug": tpl.slug,
                },
                severity=_SEVERITY_MAP.get(tpl.severity.value, AlertRule.Severity.WARN.value),
                notify_channels=[],
                is_active=True,
            )
            created += 1
        except Exception:  # noqa: BLE001 — one bad row must not skip the rest
            log.warning(
                "alert seed: failed to create default rule %r for app %s",
                name,
                app.slug,
                exc_info=True,
            )
    return created


# The security burst detectors (#151) are org-wide, not per-app: the burst
# they count is "one actor against this tenant", so they seed once per
# organization with target=global instead of once per app.
#
# The seeded severity is the *base* severity the policy assigns to a
# threshold crossing (WARNING). ``security_events`` escalates to CRITICAL
# at 5x the threshold, but the AlertEvent takes its severity from the rule
# row, so the escalation shows up in the evaluator's log line rather than
# on the event.
_SECURITY_RULES: tuple[tuple[DetectorKind, str], ...] = (
    (DetectorKind.FAILED_LOGIN_BURST, "Failed authentication burst"),
    (DetectorKind.PERMISSION_DENIED_BURST, "Permission denial burst"),
)


def seed_security_alert_rules(organization) -> int:
    """Seed the org-wide security burst rules. Returns the count created.

    Same contract as the per-app seeder: idempotent (the rule name is
    org-unique, so a re-run creates nothing) and best-effort per row.

    No thresholds in the predicate — ``security_events.DEFAULTS`` carries
    the spec values, and an operator editing the rule then overrides only
    the knob they mean to change.
    """
    created = 0
    for kind, title in _SECURITY_RULES:
        name = title[:_MAX_NAME_LEN]
        if AlertRule.objects.filter(organization=organization, name=name).exists():
            continue
        try:
            AlertRule.objects.create(
                organization=organization,
                name=name,
                target=AlertRule.Target.GLOBAL.value,
                target_id="",
                predicate={"kind": kind.value},
                severity=AlertRule.Severity.WARN.value,
                notify_channels=[],
                is_active=True,
            )
            created += 1
        except Exception:  # noqa: BLE001 — one bad row must not skip the rest
            log.warning(
                "alert seed: failed to create security rule %r for org %s",
                name,
                getattr(organization, "slug", organization),
                exc_info=True,
            )
    return created


__all__ = ["seed_default_alert_rules", "seed_security_alert_rules"]
