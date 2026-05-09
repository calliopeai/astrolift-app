"""Tests for alert rule + event GraphQL mutations (#282)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization, Team
from astrolift_operations.models import AlertEvent, AlertRule
from astrolift_operations.schema.mutations import (
    AcknowledgeAlertEventInput,
    CreateAlertRuleInput,
    DeleteAlertRuleInput,
    OperationsMutation,
    UpdateAlertRuleInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    Team.objects.create(organization=org, name="Eng", slug="eng")
    return org


def _ctx(org, *, actor_user_id=None):
    return tenant_context(TenantContext(
        organization_id=org.id,
        actor_user_id=actor_user_id,
    ))


# ---- create -----------------------------------------------------


def test_create_alert_rule_happy_path(permission_resolver):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)

    with _ctx(org):
        result = OperationsMutation().create_alert_rule(
            _info(),
            input=CreateAlertRuleInput(
                name="High error rate",
                target="app",
                target_id="hello-app",
                severity="critical",
                predicate={
                    "metric": "http_5xx_rate",
                    "comparator": ">",
                    "threshold": 0.05,
                    "duration_seconds": 300,
                },
                notify_channels=[
                    {"kind": "slack", "ref": "#oncall"},
                    {"kind": "email", "ref": "oncall@acme.com"},
                ],
            ),
        )

    assert result.ok, result.errors
    assert AlertRule.objects.count() == 1
    rule = AlertRule.objects.first()
    assert rule.name == "High error rate"
    assert rule.severity == "critical"
    assert rule.target == "app"
    assert rule.is_active is True


def test_create_alert_rule_global_with_target_id_rejected(
    permission_resolver,
):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)

    with _ctx(org):
        result = OperationsMutation().create_alert_rule(
            _info(),
            input=CreateAlertRuleInput(
                name="x",
                target="global",
                target_id="should-not-be-here",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "targetId"


def test_create_alert_rule_invalid_target_rejected(
    permission_resolver,
):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)

    with _ctx(org):
        result = OperationsMutation().create_alert_rule(
            _info(),
            input=CreateAlertRuleInput(
                name="x",
                target="not_a_target",
            ),
        )
    assert not result.ok
    assert result.errors[0].field == "target"


def test_create_alert_rule_invalid_severity_rejected(
    permission_resolver,
):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)

    with _ctx(org):
        result = OperationsMutation().create_alert_rule(
            _info(),
            input=CreateAlertRuleInput(
                name="x",
                target="app",
                severity="emergency",  # not in choices
            ),
        )
    assert not result.ok
    assert result.errors[0].field == "severity"


def test_create_alert_rule_duplicate_name_conflict(permission_resolver):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    AlertRule.objects.create(
        organization=org, name="dup", target="global",
    )

    with _ctx(org):
        result = OperationsMutation().create_alert_rule(
            _info(),
            input=CreateAlertRuleInput(name="dup", target="global"),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"


def test_create_requires_permission():
    org = _scaffold()
    with _ctx(org):
        result = OperationsMutation().create_alert_rule(
            _info(),
            input=CreateAlertRuleInput(name="x", target="app"),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- update ------------------------------------------------------


def test_update_alert_rule_patches_fields(permission_resolver):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    rule = AlertRule.objects.create(
        organization=org, name="orig", target="app",
        severity="warn", is_active=True,
    )

    with _ctx(org):
        result = OperationsMutation().update_alert_rule(
            _info(),
            input=UpdateAlertRuleInput(
                id=str(rule.guid),
                name="renamed",
                severity="critical",
                is_active=False,
            ),
        )
    assert result.ok
    rule.refresh_from_db()
    assert rule.name == "renamed"
    assert rule.severity == "critical"
    assert rule.is_active is False


def test_update_unknown_returns_not_found(permission_resolver):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)

    with _ctx(org):
        result = OperationsMutation().update_alert_rule(
            _info(),
            input=UpdateAlertRuleInput(
                id="00000000-0000-0000-0000-000000000000",
                name="x",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- delete ------------------------------------------------------


def test_delete_alert_rule_soft_deletes(permission_resolver):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_DELETE)
    rule = AlertRule.objects.create(
        organization=org, name="r", target="app",
    )

    with _ctx(org):
        result = OperationsMutation().delete_alert_rule(
            _info(),
            input=DeleteAlertRuleInput(id=str(rule.guid)),
        )
    assert result.ok
    rule.refresh_from_db()
    assert rule.deleted_at is not None


# ---- acknowledge -------------------------------------------------


def test_acknowledge_alert_event_sets_timestamp(permission_resolver):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    rule = AlertRule.objects.create(
        organization=org, name="r", target="app",
    )
    event = AlertEvent.objects.create(
        rule=rule, organization=org,
        fired_at=timezone.now(),
        severity="warn",
        summary="something fired",
    )

    with _ctx(org):
        result = OperationsMutation().acknowledge_alert_event(
            _info(),
            input=AcknowledgeAlertEventInput(id=str(event.guid)),
        )
    assert result.ok
    event.refresh_from_db()
    assert event.acknowledged_at is not None


def test_acknowledge_is_idempotent(permission_resolver):
    """Re-ack on an already-acked event keeps the original
    timestamp + actor; second call returns ok without churn."""
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    rule = AlertRule.objects.create(
        organization=org, name="r", target="app",
    )
    event = AlertEvent.objects.create(
        rule=rule, organization=org,
        fired_at=timezone.now(),
        severity="warn",
        acknowledged_at=timezone.now(),
    )
    first_ack = event.acknowledged_at

    with _ctx(org):
        result = OperationsMutation().acknowledge_alert_event(
            _info(),
            input=AcknowledgeAlertEventInput(id=str(event.guid)),
        )
    assert result.ok
    event.refresh_from_db()
    assert event.acknowledged_at == first_ack
