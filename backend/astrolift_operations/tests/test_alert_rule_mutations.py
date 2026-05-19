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
    return tenant_context(
        TenantContext(
            organization_id=org.id,
            actor_user_id=actor_user_id,
        )
    )


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
        organization=org,
        name="dup",
        target="global",
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
        organization=org,
        name="orig",
        target="app",
        severity="warn",
        is_active=True,
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
        organization=org,
        name="r",
        target="app",
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
        organization=org,
        name="r",
        target="app",
    )
    event = AlertEvent.objects.create(
        rule=rule,
        organization=org,
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
        organization=org,
        name="r",
        target="app",
    )
    event = AlertEvent.objects.create(
        rule=rule,
        organization=org,
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


# ---- managed_service binding (#757) ------------------------------


def _make_email_service_for(org):
    """Build the dependency chain needed to attach a ManagedService
    to an AlertRule. Same shape as test_email_observability._scaffold,
    inlined here so the alert-mutation tests stay self-contained."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Project
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from astrolift_services.models import ManagedService

    team = org.teams.first()
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug=f"demo-msvc-{org.slug}",
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="aws")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-msvc-{org.slug}",
        name="Cluster",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
        region="us-east-1",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug=f"app-msvc-{org.slug}",
        provisioning_status="ready",
        manifest_raw='astrolift_version = 1\nname = "app"\n',
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.EMAIL,
        variant="ses",
        name="ses",
        status=ManagedService.Status.ACTIVE,
        config={"region": "us-east-1", "identity": "mail.example.com"},
    )


def test_create_alert_rule_with_managed_service_id(permission_resolver):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    service = _make_email_service_for(org)

    with _ctx(org):
        result = OperationsMutation().create_alert_rule(
            _info(),
            input=CreateAlertRuleInput(
                name="SES bounce alert",
                target="app",
                target_id=service.registered_app.slug,
                severity="critical",
                predicate={
                    "kind": "ses_bounce_rate",
                    "threshold_pct": 5.0,
                },
                managed_service_id=str(service.guid),
            ),
        )
    assert result.ok, result.errors
    rule = AlertRule.objects.get(name="SES bounce alert")
    assert rule.managed_service_id == service.pk
    # And the response carries the GUID for the FE.
    assert str(result.data.managed_service_id) == str(service.guid)


def test_create_alert_rule_unknown_managed_service_returns_not_found(
    permission_resolver,
):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)

    with _ctx(org):
        result = OperationsMutation().create_alert_rule(
            _info(),
            input=CreateAlertRuleInput(
                name="bogus",
                target="app",
                managed_service_id="00000000-0000-0000-0000-000000000000",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "managedServiceId"


def test_create_alert_rule_cross_org_managed_service_rejected(
    permission_resolver,
):
    """A ManagedService owned by org B cannot be bound to a rule on
    org A. Cross-tenant lookups must NOT_FOUND, not pass."""
    org_a = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_CREATE)
    other_org = Organization.objects.create(name="Other", slug="other")
    Team.objects.create(organization=other_org, name="Eng", slug="eng-other")
    foreign_service = _make_email_service_for(other_org)

    with _ctx(org_a):
        result = OperationsMutation().create_alert_rule(
            _info(),
            input=CreateAlertRuleInput(
                name="evil",
                target="app",
                managed_service_id=str(foreign_service.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_update_alert_rule_repoints_managed_service(permission_resolver):
    org = _scaffold()
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    svc_a = _make_email_service_for(org)
    rule = AlertRule.objects.create(
        organization=org,
        name="ses-rule",
        target="app",
        managed_service=svc_a,
    )
    # A second managed service to repoint to.
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.models import ManagedService

    env_b = AppEnvironment.objects.create(
        registered_app=svc_a.registered_app,
        name="staging",
        tenant_cluster=svc_a.app_environment.tenant_cluster,
    )
    svc_b = ManagedService.objects.create(
        registered_app=svc_a.registered_app,
        app_environment=env_b,
        kind=ManagedService.Kind.EMAIL,
        variant="ses",
        name="ses-staging",
        status=ManagedService.Status.ACTIVE,
        config={"region": "us-east-1"},
    )

    with _ctx(org):
        result = OperationsMutation().update_alert_rule(
            _info(),
            input=UpdateAlertRuleInput(
                id=str(rule.guid),
                managed_service_id=str(svc_b.guid),
            ),
        )
    assert result.ok, result.errors
    rule.refresh_from_db()
    assert rule.managed_service_id == svc_b.pk
