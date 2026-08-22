"""Tests for the security-burst predicate kinds (real Postgres).

Covers what the counting half has to get right for the burst policy in
``security_events`` to mean anything: the threshold and window come from
the detector defaults (overridable per rule), the count is per *actor*
rather than per org, the two detectors split the audit surface by action
prefix so one burst is never counted twice, ALLOW rows and other tenants'
rows are invisible, the cooldown suppresses the next burst, and the whole
thing runs from the live sweep on a seeded rule.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_operations import alert_engine, alert_evaluators
from astrolift_operations.alert_evaluators.security import (
    evaluate_failed_login_burst,
    evaluate_permission_denied_burst,
)
from astrolift_operations.alert_seed import seed_security_alert_rules
from astrolift_operations.models import AlertEvent, AlertRule, AuditEvent

pytestmark = pytest.mark.django_db

FAILED_LOGIN = "failed_login_burst"
PERMISSION_DENIED = "permission_denied_burst"


def _org() -> Organization:
    slug = f"org-{uuid.uuid4().hex[:8]}"
    return Organization.objects.create(name=slug.upper(), slug=slug)


def _rule(org, *, kind: str, **predicate) -> AlertRule:
    return AlertRule.objects.create(
        organization=org,
        name=f"{kind}-{uuid.uuid4().hex[:6]}",
        target=AlertRule.Target.GLOBAL.value,
        target_id="",
        predicate={"kind": kind, **predicate},
        severity=AlertRule.Severity.WARN.value,
        is_active=True,
    )


def _audit(
    org,
    *,
    count: int,
    action: str = "auth.step_up.denied",
    actor_id: str = "7",
    decision: str = AuditEvent.Decision.DENY,
    ago_seconds: int = 1,
) -> None:
    """Insert ``count`` audit rows at a chosen point in time.

    Raw SQL because ``occurred_at`` is ``auto_now_add`` and even
    ``bulk_create`` runs the field's ``pre_save``, so the ORM cannot
    place a row before now; the append-only trigger refuses the UPDATE
    that would fix it afterwards but allows the INSERT. Same approach as
    ``astrolift_identity.tests.test_members_query``.
    """
    from django.db import connection

    at = timezone.now() - timedelta(seconds=ago_seconds)
    with connection.cursor() as cur:
        for _ in range(count):
            cur.execute(
                """
                INSERT INTO astrolift_operations_auditevent
                  (guid, organization_id, occurred_at, actor_kind, actor_id,
                   actor_display, action, decision, target_kind, target_id,
                   target_slug, target_parent_chain, request_id, request_ip,
                   request_user_agent, request_session_age_seconds, data,
                   reasoning)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s, %s, %s, %s)
                """,
                [
                    str(uuid.uuid4()),
                    org.id,
                    at,
                    "user",
                    actor_id,
                    "",
                    action,
                    decision,
                    "resolver",
                    "Mutation.x",
                    "",
                    "[]",
                    "",
                    "",
                    "",
                    None,
                    "{}",
                    "[]",
                ],
            )


# ---- registration ---------------------------------------------------


def test_both_security_kinds_are_registered():
    """Unregistered kinds resolve to no-fire, so registration IS the wiring."""
    assert alert_evaluators.KIND_HANDLERS[FAILED_LOGIN] is evaluate_failed_login_burst
    assert alert_evaluators.KIND_HANDLERS[PERMISSION_DENIED] is evaluate_permission_denied_burst


# ---- threshold + window --------------------------------------------


def test_failed_login_burst_fires_at_the_default_threshold():
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN)
    _audit(org, count=5)  # default threshold is 5 in a 5-minute window

    assert alert_evaluators.evaluate(rule) is True


def test_failed_login_burst_stays_clear_below_the_threshold():
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN)
    _audit(org, count=4)

    assert alert_evaluators.evaluate(rule) is False


def test_denials_outside_the_window_do_not_count():
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN)
    _audit(org, count=8, ago_seconds=600)  # window is 300s

    assert alert_evaluators.evaluate(rule) is False


def test_threshold_override_from_the_predicate_applies():
    """Operator-typed JSON: a quoted number still configures the detector."""
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN, threshold="3")
    _audit(org, count=3)

    assert alert_evaluators.evaluate(rule) is True


# ---- per-subject counting ------------------------------------------


def test_burst_is_counted_per_actor_not_org_wide():
    """Twelve denials spread over three actors is ordinary traffic, not a
    burst: the threshold is 'N failures for one user'."""
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN)
    for actor in ("1", "2", "3"):
        _audit(org, count=4, actor_id=actor)

    assert alert_evaluators.evaluate(rule) is False


def test_one_actor_inside_the_crowd_still_fires():
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN)
    _audit(org, count=4, actor_id="1")
    _audit(org, count=4, actor_id="2")
    _audit(org, count=5, actor_id="3")

    assert alert_evaluators.evaluate(rule) is True


# ---- what does and does not count ----------------------------------


def test_allowed_actions_do_not_count():
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN)
    _audit(org, count=20, decision=AuditEvent.Decision.ALLOW)

    assert alert_evaluators.evaluate(rule) is False


def test_another_tenants_denials_do_not_count():
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN)
    _audit(_org(), count=20)

    assert alert_evaluators.evaluate(rule) is False


def test_auth_denials_belong_to_the_login_detector_only():
    org = _org()
    login = _rule(org, kind=FAILED_LOGIN)
    denied = _rule(org, kind=PERMISSION_DENIED)
    _audit(org, count=12, action="auth.attestation.failed")

    assert alert_evaluators.evaluate(login) is True
    assert alert_evaluators.evaluate(denied) is False


def test_mutation_denials_belong_to_the_permission_detector_only():
    org = _org()
    login = _rule(org, kind=FAILED_LOGIN)
    denied = _rule(org, kind=PERMISSION_DENIED)
    _audit(org, count=12, action="app.deploy")  # threshold 10 in a 60s window

    assert alert_evaluators.evaluate(denied) is True
    assert alert_evaluators.evaluate(login) is False


# ---- cooldown -------------------------------------------------------


def _prior_alert(rule, *, seconds_ago: int) -> AlertEvent:
    now = timezone.now()
    return AlertEvent.objects.create(
        rule=rule,
        organization_id=rule.organization_id,
        fired_at=now - timedelta(seconds=seconds_ago),
        resolved_at=now - timedelta(seconds=seconds_ago - 1),
        severity=rule.severity,
        summary="earlier burst",
    )


def test_cooldown_suppresses_a_burst_right_after_the_last_alert():
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN)
    _prior_alert(rule, seconds_ago=60)  # cooldown is 30 minutes
    _audit(org, count=6)

    assert alert_evaluators.evaluate(rule) is False


def test_cooldown_lifts_once_it_has_elapsed():
    org = _org()
    rule = _rule(org, kind=FAILED_LOGIN)
    _prior_alert(rule, seconds_ago=4000)
    _audit(org, count=6)

    assert alert_evaluators.evaluate(rule) is True


# ---- end to end through the live sweep ------------------------------


def test_seeded_rules_fire_alert_events_from_the_sweep():
    """The whole path: org seeding -> active rule -> run_alert_sweep ->
    AlertEvent rows, with nothing stubbed."""
    org = _org()
    assert seed_security_alert_rules(org) == 2
    _audit(org, count=6, action="auth.step_up.denied")
    _audit(org, count=11, action="app.deploy")

    summary = alert_engine.run_alert_sweep()

    assert summary["evaluated"] == 2
    assert summary["fired"] == 2
    fired = {e.rule.predicate["kind"] for e in AlertEvent.objects.select_related("rule")}
    assert fired == {FAILED_LOGIN, PERMISSION_DENIED}


def test_sweep_is_quiet_on_a_seeded_org_with_no_denials():
    org = _org()
    seed_security_alert_rules(org)
    _audit(org, count=50, decision=AuditEvent.Decision.ALLOW, action="app.deploy")

    summary = alert_engine.run_alert_sweep()

    assert summary["fired"] == 0
    assert AlertEvent.objects.count() == 0
