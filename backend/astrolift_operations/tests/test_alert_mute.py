"""Tests for AlertMute model + mute/unmute mutations (#434 scope C)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_operations.alert_mute import active_mute_for_rule, is_rule_muted
from astrolift_operations.models import AlertMute, AlertRule
from astrolift_operations.schema.mutations import (
    MuteAlertRuleInput,
    OperationsMutation,
    UnmuteAlertRuleInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _ctx(org, *, actor_user_id=None):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    rule = AlertRule.objects.create(
        organization=org,
        name="High error rate",
        target="app",
        severity="critical",
    )
    return org, rule


# ---- model + helper ---------------------------------------------


def test_is_rule_muted_returns_false_when_no_mute():
    _, rule = _scaffold()
    assert is_rule_muted(rule) is False
    assert active_mute_for_rule(rule) is None


def test_is_rule_muted_returns_true_within_ttl():
    org, rule = _scaffold()
    AlertMute.objects.create(
        rule=rule,
        organization=org,
        ttl_until=timezone.now() + dt.timedelta(minutes=15),
        reason="planned maintenance",
    )
    assert is_rule_muted(rule) is True
    mute = active_mute_for_rule(rule)
    assert mute is not None
    assert mute.reason == "planned maintenance"


def test_expired_mute_is_inactive():
    org, rule = _scaffold()
    AlertMute.objects.create(
        rule=rule,
        organization=org,
        ttl_until=timezone.now() - dt.timedelta(seconds=1),
        reason="old",
    )
    assert is_rule_muted(rule) is False


def test_soft_deleted_mute_is_inactive():
    org, rule = _scaffold()
    mute = AlertMute.objects.create(
        rule=rule,
        organization=org,
        ttl_until=timezone.now() + dt.timedelta(hours=1),
        reason="x",
    )
    mute.soft_delete()
    assert is_rule_muted(rule) is False


def test_longest_lived_mute_wins():
    org, rule = _scaffold()
    AlertMute.objects.create(
        rule=rule,
        organization=org,
        ttl_until=timezone.now() + dt.timedelta(minutes=5),
        reason="short",
    )
    AlertMute.objects.create(
        rule=rule,
        organization=org,
        ttl_until=timezone.now() + dt.timedelta(hours=4),
        reason="long",
    )
    mute = active_mute_for_rule(rule)
    assert mute is not None
    assert mute.reason == "long"


# ---- muteAlertRule mutation --------------------------------------


def test_mute_alert_rule_happy_path(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org, rule = _scaffold()

    with _ctx(org):
        result = OperationsMutation().mute_alert_rule(
            _info(),
            input=MuteAlertRuleInput(
                rule_id=str(rule.guid),
                duration_seconds=3600,
                reason="known outage",
            ),
        )
    assert result.ok, result.errors
    assert is_rule_muted(rule)
    assert result.data.active_mute is not None
    assert result.data.active_mute.reason == "known outage"


def test_mute_requires_permission():
    org, rule = _scaffold()
    with _ctx(org):
        result = OperationsMutation().mute_alert_rule(
            _info(),
            input=MuteAlertRuleInput(
                rule_id=str(rule.guid),
                duration_seconds=60,
                reason="x",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_mute_requires_reason(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org, rule = _scaffold()
    with _ctx(org):
        result = OperationsMutation().mute_alert_rule(
            _info(),
            input=MuteAlertRuleInput(
                rule_id=str(rule.guid),
                duration_seconds=60,
                reason="   ",
            ),
        )
    assert not result.ok
    assert result.errors[0].field == "reason"


def test_mute_rejects_non_positive_duration(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org, rule = _scaffold()
    with _ctx(org):
        result = OperationsMutation().mute_alert_rule(
            _info(),
            input=MuteAlertRuleInput(
                rule_id=str(rule.guid),
                duration_seconds=0,
                reason="x",
            ),
        )
    assert not result.ok
    assert result.errors[0].field == "durationSeconds"


def test_mute_rejects_over_one_week(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org, rule = _scaffold()
    with _ctx(org):
        result = OperationsMutation().mute_alert_rule(
            _info(),
            input=MuteAlertRuleInput(
                rule_id=str(rule.guid),
                duration_seconds=8 * 24 * 60 * 60,
                reason="x",
            ),
        )
    assert not result.ok
    assert result.errors[0].field == "durationSeconds"


def test_mute_unknown_rule_returns_not_found(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org = Organization.objects.create(name="Acme", slug="acme")
    with _ctx(org):
        result = OperationsMutation().mute_alert_rule(
            _info(),
            input=MuteAlertRuleInput(
                rule_id="00000000-0000-0000-0000-000000000000",
                duration_seconds=60,
                reason="x",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- unmuteAlertRule mutation ------------------------------------


def test_unmute_alert_rule_clears_active(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org, rule = _scaffold()
    AlertMute.objects.create(
        rule=rule,
        organization=org,
        ttl_until=timezone.now() + dt.timedelta(hours=2),
        reason="x",
    )
    with _ctx(org):
        result = OperationsMutation().unmute_alert_rule(
            _info(),
            input=UnmuteAlertRuleInput(rule_id=str(rule.guid)),
        )
    assert result.ok
    assert not is_rule_muted(rule)


def test_unmute_is_idempotent(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org, rule = _scaffold()
    with _ctx(org):
        result = OperationsMutation().unmute_alert_rule(
            _info(),
            input=UnmuteAlertRuleInput(rule_id=str(rule.guid)),
        )
    assert result.ok


def test_unmute_preserves_history(permission_resolver):
    """Soft-delete leaves the row queryable in the audit history."""
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org, rule = _scaffold()
    AlertMute.objects.create(
        rule=rule,
        organization=org,
        ttl_until=timezone.now() + dt.timedelta(hours=1),
        reason="recorded reason",
    )
    with _ctx(org):
        OperationsMutation().unmute_alert_rule(
            _info(),
            input=UnmuteAlertRuleInput(rule_id=str(rule.guid)),
        )
    # All AlertMute rows survived; they're just soft-deleted.
    # ``objects`` filters them out — use ``all_objects`` to see the
    # full audit history.
    assert AlertMute.all_objects.filter(rule=rule).count() == 1
    assert AlertMute.all_objects.filter(rule=rule, deleted_at__isnull=False).exists()
