"""Tests for per-app alert subscription mutations + query (#747)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.models import UserAlertSubscription
from astrolift_operations.schema.mutations import (
    ClearAlertSubscriptionInput,
    OperationsMutation,
    SetAlertSubscriptionInput,
)
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import RegisteredApp
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db

User = get_user_model()


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="sub-acme")
    team = Team.objects.create(organization=org, name="Eng", slug="sub-eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="sub-demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="MyApp",
        slug="sub-myapp",
        provisioning_status="ready",
    )
    user = User.objects.create_user(username="sub-user", email="sub@test")
    bind_role(
        user, permissions=[Permission.APP_READ], kind="ORG", scope_id=org.pk, slug="subscription-reader"
    )
    return org, app, user


def test_set_alert_subscription_creates_new_row():
    org, app, user = _scaffold()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=app.slug,
                alert_kind="deploy_failure",
                channel="email",
                enabled=True,
            ),
        )
    assert result.ok is True
    assert result.data.alert_kind == "deploy_failure"
    assert result.data.channel == "email"
    assert result.data.enabled is True

    assert (
        UserAlertSubscription.objects.filter(
            user=user, registered_app=app, alert_kind="deploy_failure", deleted_at__isnull=True
        ).count()
        == 1
    )


def test_set_alert_subscription_upserts_existing():
    org, app, user = _scaffold()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=app.slug, alert_kind="deploy_failure", channel="email", enabled=True
            ),
        )
        result = OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=app.slug, alert_kind="deploy_failure", channel="both", enabled=False
            ),
        )
    assert result.ok is True
    assert result.data.channel == "both"
    assert result.data.enabled is False
    assert (
        UserAlertSubscription.objects.filter(
            user=user, registered_app=app, alert_kind="deploy_failure", deleted_at__isnull=True
        ).count()
        == 1
    )


def test_set_alert_subscription_rejects_invalid_kind():
    org, app, user = _scaffold()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=app.slug, alert_kind="bogus_kind", channel="email", enabled=True
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value


def test_set_alert_subscription_rejects_invalid_channel():
    org, app, user = _scaffold()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=app.slug, alert_kind="deploy_failure", channel="sms", enabled=True
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value


def test_set_alert_subscription_not_found_app():
    org, app, user = _scaffold()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug="does-not-exist", alert_kind="deploy_failure", channel="email", enabled=True
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value


def test_clear_alert_subscription_soft_deletes():
    org, app, user = _scaffold()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        created = OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=app.slug, alert_kind="error_spike", channel="web", enabled=True
            ),
        )
        sub_guid = created.data.id
        result = OperationsMutation().clear_alert_subscription(
            info=_info(),
            input=ClearAlertSubscriptionInput(id=sub_guid),
        )
    assert result.ok is True
    assert (
        UserAlertSubscription.objects.filter(
            user=user, registered_app=app, alert_kind="error_spike", deleted_at__isnull=True
        ).count()
        == 0
    )


def test_clear_alert_subscription_not_found():
    org, app, user = _scaffold()
    import uuid

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = OperationsMutation().clear_alert_subscription(
            info=_info(),
            input=ClearAlertSubscriptionInput(id=str(uuid.uuid4())),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value


def test_my_alert_subscriptions_query_returns_caller_rows():
    org, app, user = _scaffold()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=app.slug, alert_kind="deploy_success", channel="both", enabled=True
            ),
        )
        OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=app.slug, alert_kind="preview_created", channel="web", enabled=False
            ),
        )
        subs = OperationsQuery().astrolift_my_alert_subscriptions(info=_info())

    assert len(subs) == 2
    kinds = {s.alert_kind for s in subs}
    assert kinds == {"deploy_success", "preview_created"}


def test_my_alert_subscriptions_filters_by_app_slug():
    org, _app, user = _scaffold()

    # create a second app
    app2 = RegisteredApp.objects.create(
        organization=org,
        team=_app.team,
        project=_app.project,
        name="App2",
        slug="sub-app2",
        provisioning_status="ready",
    )
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=_app.slug, alert_kind="deploy_failure", channel="email", enabled=True
            ),
        )
        OperationsMutation().set_alert_subscription(
            info=_info(),
            input=SetAlertSubscriptionInput(
                app_slug=app2.slug, alert_kind="error_spike", channel="web", enabled=True
            ),
        )
        subs = OperationsQuery().astrolift_my_alert_subscriptions(info=_info(), app_slug=_app.slug)

    assert len(subs) == 1
    assert subs[0].app_slug == _app.slug
