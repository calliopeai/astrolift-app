"""Actual owner grants, bearer ceilings and mixed operations against PostgreSQL."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.models import (
    AlertEvent,
    AlertRule,
    Event,
    UserAlertSubscription,
    WebhookSubscription,
)
from astrolift_operations.schema.mutations import OperationsMutation
from astrolift_operations.schema.mutations import types as inputs
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import Workload
from astrolift_services.models import AppSecretBundleRef, SecretBundle
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db
PERMISSIONS = [
    Permission.APP_READ,
    Permission.APP_READ_METRICS,
    Permission.APP_UPDATE,
    Permission.APP_DEPLOY,
    Permission.APP_LOG_EXPORT,
    Permission.WEBHOOK_CREATE,
    Permission.WEBHOOK_UPDATE,
    Permission.WEBHOOK_DELETE,
    Permission.AUDIT_LOG_READ,
    Permission.AUDIT_LOG_EXPORT,
    Permission.ORG_READ,
    Permission.ORG_UPDATE,
    Permission.ZENTINELLE_CONNECT,
    Permission.ZENTINELLE_GATEWAY_MANAGE,
]


@pytest.fixture(autouse=True)
def no_index(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *args: None))


@pytest.fixture
def world(monkeypatch):
    w = ScopeWorld("ops-2107")
    w.user = make_user("ops-2107")
    w.cluster = make_cluster(w, "ops-2107")
    w.calls = []
    w.environments, w.workloads, w.rules, w.webhooks = [], [], [], []
    for app in (w.medops_app, w.platform_app):
        w.environments.append(
            AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=w.cluster)
        )
        w.workloads.append(Workload.objects.create(registered_app=app, name="api", slug="api"))
        w.rules.append(
            AlertRule.objects.create(organization=w.org, name=app.slug, target="app", target_id=app.slug)
        )
        w.webhooks.append(
            WebhookSubscription.objects.create(
                organization=w.org,
                registered_app=app,
                url="https://example.invalid/hook",
                secret_hash="secret",
                events=[],
            )
        )
        Event.objects.create(
            organization=w.org,
            registered_app=app,
            team=app.team,
            project=app.project,
            event_type="app.deployed",
            resource_kind="app",
            resource_id=str(app.guid),
        )
        AlertEvent.objects.create(organization=w.org, rule=w.rules[-1], fired_at=timezone.now())
    w.global_rule = AlertRule.objects.create(organization=w.org, name="global", target="global")
    w.global_hook = WebhookSubscription.objects.create(
        organization=w.org, url="https://example.invalid/global", secret_hash="secret"
    )
    w.bundle = SecretBundle.objects.create(
        organization=w.org, project=w.medops_project, tenant_cluster=w.cluster, name="shared", slug="shared"
    )

    def resync(app):
        w.calls.append(app.pk)
        return SimpleNamespace(status="in_sync")

    monkeypatch.setattr("astrolift_registry.services.manifest_sync.resync_app_manifest_from_repo", resync)
    monkeypatch.setattr(
        "astrolift_lifecycle.services.k8s_ops.rollout_restart_workload",
        lambda wl: w.calls.append(wl.registered_app_id),
    )
    return w


def grant(w, kind="TEAM"):
    ids = {"ORG": w.org.pk, "TEAM": w.medops.pk, "PROJECT": w.medops_project.pk, "APP": w.medops_app.pk}
    return bind_role(w.user, permissions=PERMISSIONS, kind=kind, scope_id=ids[kind], slug=uuid.uuid4().hex)


@contextmanager
def selected_sibling(w):
    from astrolift_identity import abac

    with (
        tenant_context(
            TenantContext(
                organization_id=w.org.pk,
                actor_user_id=w.user.pk,
                team_id=w.platform.pk,
                project_id=w.platform_project.pk,
            )
        ),
        abac.request_attributes(abac.RequestAttributes(actor_user_id=w.user.pk)),
    ):
        yield


@contextmanager
def bearer(w, team=True):
    token = set_current_api_token(
        SimpleNamespace(
            organization_id=w.org.pk,
            user_id=w.user.pk,
            team_id=w.medops.pk if team else None,
            scopes=["admin"],
        )
    )
    try:
        yield
    finally:
        reset_current_api_token(token)


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
@pytest.mark.parametrize("route", ["astrolift_app_uptime", "astrolift_app_metrics"])
def test_named_app_read_uses_actual_owner_not_selection(world, kind, route):
    grant(world, kind)
    query = getattr(OperationsQuery(), route)
    with selected_sibling(world):
        assert query(make_info(world.user), app_slug=world.medops_app.slug) is not None
        if kind == "ORG":
            assert query(make_info(world.user), app_slug=world.platform_app.slug) is not None
        else:
            with pytest.raises(PermissionDenied):
                query(make_info(world.user), app_slug=world.platform_app.slug)


@pytest.mark.parametrize("route", ["astrolift_app_uptime", "astrolift_app_metrics"])
def test_team_bearer_cannot_borrow_org_grant_for_sibling_app(world, route):
    grant(world, "ORG")
    with selected_sibling(world), bearer(world), pytest.raises(PermissionDenied):
        getattr(OperationsQuery(), route)(make_info(world.user), app_slug=world.platform_app.slug)


@pytest.mark.parametrize(
    "route,permission",
    [
        ("astrolift_audit_events", Permission.AUDIT_LOG_READ),
        ("astrolift_audit_events_page", Permission.AUDIT_LOG_READ),
        ("astrolift_audit_retention", Permission.AUDIT_LOG_READ),
        ("astrolift_observability_retention", Permission.ORG_READ),
        ("astrolift_zentinelle_connection", Permission.ZENTINELLE_CONNECT),
    ],
)
@pytest.mark.parametrize("identity", ["TEAM", "PROJECT", "ORG_TOKEN"])
def test_org_read_cannot_inherit_upward_or_escape_token_ceiling(world, route, permission, identity):
    grant(world, "ORG" if identity == "ORG_TOKEN" else identity)
    with selected_sibling(world):
        if identity == "ORG_TOKEN":
            with bearer(world), pytest.raises(PermissionDenied):
                getattr(OperationsQuery(), route)(make_info(world.user))
        else:
            with pytest.raises(PermissionDenied):
                getattr(OperationsQuery(), route)(make_info(world.user))


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
@pytest.mark.parametrize("token", [False, True])
@pytest.mark.parametrize(
    "route", ["astrolift_alert_rules_page", "astrolift_alert_events_page", "astrolift_events_page"]
)
def test_collections_filter_before_counts_and_cursors(world, kind, token, route):
    grant(world, kind)
    with selected_sibling(world):
        if token:
            with bearer(world):
                page = getattr(OperationsQuery(), route)(make_info(world.user), limit=1)
        else:
            page = getattr(OperationsQuery(), route)(make_info(world.user), limit=1)
    expected = 3 if route == "astrolift_alert_rules_page" else 2
    if kind != "ORG" or token:
        expected = 1
    if hasattr(page, "total_count"):
        assert page.total_count == expected
    assert len(page.items) == 1
    if expected == 1:
        assert page.next_cursor is None
        if route == "astrolift_alert_rules_page":
            assert page.items[0].name == world.medops_app.slug


@pytest.mark.parametrize("kind", ["TEAM", "ORG"])
def test_subscribe_requires_app_read_and_clear_still_requires_the_owner_user(world, kind):
    grant(world, kind)
    mutation = OperationsMutation()
    with selected_sibling(world):
        own = mutation.set_alert_subscription(
            make_info(world.user),
            input=inputs.SetAlertSubscriptionInput(
                app_slug=world.medops_app.slug, alert_kind="deploy_failure", channel="email", enabled=True
            ),
        )
        assert own.ok
        sibling = mutation.set_alert_subscription(
            make_info(world.user),
            input=inputs.SetAlertSubscriptionInput(
                app_slug=world.platform_app.slug, alert_kind="deploy_failure", channel="email", enabled=True
            ),
        )
        assert sibling.ok == (kind == "ORG")
        other = make_user("other-sub-2107")
        row = UserAlertSubscription.objects.create(
            user=other, registered_app=world.medops_app, alert_kind="deploy_failure", channel="email"
        )
        refused = mutation.clear_alert_subscription(
            make_info(world.user), input=inputs.ClearAlertSubscriptionInput(id=str(row.guid))
        )
        assert not refused.ok
        row.refresh_from_db()
        assert row.deleted_at is None
        assert mutation.clear_alert_subscription(
            make_info(world.user), input=inputs.ClearAlertSubscriptionInput(id=own.data.id)
        ).ok


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
@pytest.mark.parametrize(
    "route,input_type",
    [
        ("bulk_rolling_restart", inputs.BulkRollingRestartInput),
        ("bulk_resync_manifest", inputs.BulkResyncManifestInput),
    ],
)
def test_mixed_bulk_actions_gate_every_actual_app_before_provider(world, kind, route, input_type):
    grant(world, kind)
    with selected_sibling(world):
        result = getattr(OperationsMutation(), route)(
            make_info(world.user),
            input=input_type(app_slugs=[world.medops_app.slug, world.platform_app.slug]),
        )
    assert result.ok_count == (2 if kind == "ORG" else 1)
    assert result.failed_count == (0 if kind == "ORG" else 1)
    assert set(world.calls) == (
        {world.medops_app.pk, world.platform_app.pk} if kind == "ORG" else {world.medops_app.pk}
    )


@pytest.mark.parametrize("token", [False, True])
def test_bulk_attach_requires_source_bundle_and_destination_app_scopes(world, token):
    grant(world, "ORG" if token else "PROJECT")
    with selected_sibling(world):
        context = bearer(world) if token else selected_sibling(world)
        with context:
            result = OperationsMutation().bulk_push_secrets(
                make_info(world.user),
                input=inputs.BulkPushSecretsInput(
                    app_slugs=[world.medops_app.slug, world.platform_app.slug], bundle_slug=world.bundle.slug
                ),
            )
    assert result.ok_count == 1 and result.failed_count == 1
    assert set(AppSecretBundleRef.objects.values_list("registered_app_id", flat=True)) == {
        world.medops_app.pk
    }


def test_app_update_does_not_authorize_reading_a_project_secret_bundle(world):
    grant(world, "APP")
    with selected_sibling(world):
        result = OperationsMutation().bulk_push_secrets(
            make_info(world.user),
            input=inputs.BulkPushSecretsInput(
                app_slugs=[world.medops_app.slug], bundle_slug=world.bundle.slug
            ),
        )
    assert result.ok_count == 0 and result.failed_count == 1
    assert not AppSecretBundleRef.objects.exists()


def test_bulk_environment_policy_denies_only_its_actual_target_before_attach(world):
    grant(world, "ORG")
    env = world.environments[0]
    env.name = "staging"
    env.save(update_fields=["name", "updated_at", "version"])
    Policy.objects.create(
        organization=world.org,
        name="refuse staging",
        slug="refuse-staging",
        effect="DENY",
        action_pattern=Permission.APP_UPDATE.value,
        resource_pattern={"env": ["staging"]},
        actor_pattern={},
        conditions=[],
        scope_level="ORG",
        scope_id=world.org.pk,
    )
    with selected_sibling(world):
        result = OperationsMutation().bulk_push_secrets(
            make_info(world.user),
            input=inputs.BulkPushSecretsInput(
                app_slugs=[world.medops_app.slug, world.platform_app.slug], bundle_slug=world.bundle.slug
            ),
        )
    assert result.ok_count == 1 and result.failed_count == 1
    assert set(AppSecretBundleRef.objects.values_list("registered_app_id", flat=True)) == {
        world.platform_app.pk
    }


WEBHOOK_ACTIONS = [
    "create_webhook_subscription",
    "update_webhook_subscription",
    "delete_webhook_subscription",
    "test_webhook_subscription",
    "rotate_outbound_webhook_secret",
]
ALERT_ACTIONS = [
    "create_alert_rule",
    "update_alert_rule",
    "delete_alert_rule",
    "acknowledge_alert_event",
    "mute_alert_rule",
    "unmute_alert_rule",
]


def webhook_input(w, route, sibling):
    app = w.platform_app if sibling else w.medops_app
    row = w.webhooks[int(sibling)]
    constructors = {
        "create_webhook_subscription": lambda: inputs.CreateWebhookSubscriptionInput(
            url="https://example.invalid/new", events=[], app_slug=app.slug
        ),
        "update_webhook_subscription": lambda: inputs.UpdateWebhookSubscriptionInput(
            id=str(row.guid), url="https://example.invalid/updated"
        ),
        "delete_webhook_subscription": lambda: inputs.DeleteWebhookSubscriptionInput(id=str(row.guid)),
        "test_webhook_subscription": lambda: inputs.TestWebhookInput(id=str(row.guid)),
        "rotate_outbound_webhook_secret": lambda: inputs.RotateOutboundWebhookSecretInput(id=str(row.guid)),
    }
    return constructors[route]()


def alert_input(w, route, sibling):
    app, rule = (w.platform_app if sibling else w.medops_app), w.rules[int(sibling)]
    constructors = {
        "create_alert_rule": lambda: inputs.CreateAlertRuleInput(
            name="new", target="app", target_id=app.slug
        ),
        "update_alert_rule": lambda: inputs.UpdateAlertRuleInput(id=str(rule.guid), name="updated"),
        "delete_alert_rule": lambda: inputs.DeleteAlertRuleInput(id=str(rule.guid)),
        "acknowledge_alert_event": lambda: inputs.AcknowledgeAlertEventInput(
            id=str(AlertEvent.objects.get(rule=rule).guid)
        ),
        "mute_alert_rule": lambda: inputs.MuteAlertRuleInput(
            rule_id=str(rule.guid), duration_seconds=300, reason="check"
        ),
        "unmute_alert_rule": lambda: inputs.UnmuteAlertRuleInput(rule_id=str(rule.guid)),
    }
    return constructors[route]()


@pytest.mark.parametrize("route", WEBHOOK_ACTIONS + ALERT_ACTIONS)
@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG_TOKEN"])
def test_object_mutations_deny_siblings_before_writes_or_provider(world, monkeypatch, route, kind):
    grant(world, "ORG" if kind == "ORG_TOKEN" else kind)
    monkeypatch.setattr(
        "astrolift_operations.schema.mutations.webhooks._deliver_test_webhook",
        lambda **kwargs: pytest.fail("denied webhook provider"),
    )
    build = webhook_input if route in WEBHOOK_ACTIONS else alert_input
    before_rules = list(AlertRule.all_objects.values("id", "name", "deleted_at", "version"))
    before_hooks = list(
        WebhookSubscription.all_objects.values("id", "url", "secret_hash", "deleted_at", "version")
    )
    with selected_sibling(world):
        if kind == "ORG_TOKEN":
            with bearer(world):
                result = getattr(OperationsMutation(), route)(
                    make_info(world.user), input=build(world, route, True)
                )
        else:
            result = getattr(OperationsMutation(), route)(
                make_info(world.user), input=build(world, route, True)
            )
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert list(AlertRule.all_objects.values("id", "name", "deleted_at", "version")) == before_rules
    assert (
        list(WebhookSubscription.all_objects.values("id", "url", "secret_hash", "deleted_at", "version"))
        == before_hooks
    )
    assert world.calls == []


@pytest.mark.parametrize(
    "route", [r for r in WEBHOOK_ACTIONS if r != "test_webhook_subscription"] + ALERT_ACTIONS
)
@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_owned_object_mutations_succeed_with_actual_grant(world, route, kind):
    grant(world, kind)
    build = webhook_input if route in WEBHOOK_ACTIONS else alert_input
    with selected_sibling(world):
        result = getattr(OperationsMutation(), route)(make_info(world.user), input=build(world, route, False))
    assert result.ok, result.errors


@pytest.mark.parametrize("route", ["astrolift_webhook_deliveries", "astrolift_webhook_deliveries_page"])
@pytest.mark.parametrize("kind", ["APP", "TEAM", "ORG_TOKEN"])
def test_webhook_delivery_reads_authorize_subscription_owner(world, route, kind):
    grant(world, "ORG" if kind == "ORG_TOKEN" else kind)
    with selected_sibling(world):
        context = bearer(world) if kind == "ORG_TOKEN" else selected_sibling(world)
        with context:
            assert (
                getattr(OperationsQuery(), route)(
                    make_info(world.user), subscription_id=str(world.webhooks[0].guid)
                )
                is not None
            )
            with pytest.raises(PermissionDenied):
                getattr(OperationsQuery(), route)(
                    make_info(world.user), subscription_id=str(world.webhooks[1].guid)
                )


@pytest.mark.parametrize(
    "route",
    [
        "test_notification_channel",
        "set_notification_profile",
        "export_audit_events",
        "place_observability_retention_hold",
        "release_observability_retention_hold",
        "connect_zentinelle",
        "disconnect_zentinelle",
        "register_zentinelle_cluster",
        "set_zentinelle_gateway_enabled",
        "rotate_zentinelle_gateway_credential",
        "unregister_zentinelle_cluster",
    ],
)
@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "ORG_TOKEN"])
def test_org_mutation_denies_descendants_and_team_credentials_before_processing(world, route, kind):
    grant(world, "ORG" if kind == "ORG_TOKEN" else kind)
    with selected_sibling(world):
        context = bearer(world) if kind == "ORG_TOKEN" else selected_sibling(world)
        with context:
            result = getattr(OperationsMutation(), route)(
                make_info(world.user), input=SimpleNamespace(cluster_id=str(world.cluster.guid))
            )
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert world.calls == []


@pytest.mark.parametrize("target", ["app", "env", "workload"])
def test_alert_creation_destination_must_be_real_and_owned(world, target):
    grant(world, "TEAM")
    ident = {
        "app": world.platform_app.slug,
        "env": str(world.environments[1].guid),
        "workload": str(world.workloads[1].guid),
    }[target]
    before = AlertRule.objects.count()
    with selected_sibling(world):
        result = OperationsMutation().create_alert_rule(
            make_info(world.user),
            input=inputs.CreateAlertRuleInput(name="nope", target=target, target_id=ident),
        )
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert AlertRule.objects.count() == before


def test_org_grant_cannot_create_an_alert_with_missing_target(world):
    grant(world, "ORG")
    with selected_sibling(world):
        result = OperationsMutation().create_alert_rule(
            make_info(world.user),
            input=inputs.CreateAlertRuleInput(name="orphan", target="app", target_id="missing"),
        )
    assert not result.ok and result.errors[0].code == "NOT_FOUND"


def test_org_webhook_lists_keep_live_team_owner_and_team_bearer_ceiling(world):
    grant(world, "ORG")
    team_row = WebhookSubscription.objects.create(
        organization=world.org, team=world.medops, url="https://example.invalid/team", secret_hash="s"
    )
    WebhookSubscription.objects.create(
        organization=world.org, team=world.platform, url="https://example.invalid/sibling", secret_hash="s"
    )
    with selected_sibling(world), bearer(world):
        page = OperationsQuery().astrolift_webhook_subscriptions_page(make_info(world.user), limit=1)
    assert page.total_count == 1 and page.next_cursor is None
    assert str(page.items[0].id) == str(team_row.guid)


def test_alert_environment_policy_uses_persisted_target_and_resets_for_next_rule(world):
    grant(world, "ORG")
    env = world.environments[0]
    env.name = "staging"
    env.save(update_fields=["name", "updated_at", "version"])
    staging = AlertRule.objects.create(
        organization=world.org, name="stage", target="env", target_id=str(env.guid)
    )
    prod = AlertRule.objects.create(
        organization=world.org, name="prod", target="env", target_id=str(world.environments[1].guid)
    )
    Policy.objects.create(
        organization=world.org,
        name="staging update denied",
        slug="stage-deny",
        scope_level="ORG",
        scope_id=world.org.pk,
        effect="DENY",
        action_pattern=Permission.WEBHOOK_UPDATE.value,
        resource_pattern={"env": ["staging"]},
        actor_pattern={},
        conditions=[],
    )
    with selected_sibling(world):
        denied = OperationsMutation().update_alert_rule(
            make_info(world.user), input=inputs.UpdateAlertRuleInput(id=str(staging.guid), name="leak")
        )
        allowed = OperationsMutation().update_alert_rule(
            make_info(world.user), input=inputs.UpdateAlertRuleInput(id=str(prod.guid), name="safe")
        )
    assert not denied.ok and allowed.ok
    staging.refresh_from_db()
    assert staging.name == "stage"


@pytest.mark.parametrize("identity", ["SESSION", "ORG_TEAM_TOKEN"])
def test_http_session_and_issued_token_filter_alerts_and_refuse_sibling_subscription(
    world, settings, identity
):
    from django.test import Client

    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken, Member

    settings.DEBUG = False
    settings.MIDDLEWARE = [item for item in settings.MIDDLEWARE if "DebugToolbar" not in item]
    grant(world, "TEAM" if identity == "SESSION" else "ORG")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk, is_active=True)
    client = Client()
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid)}
    if identity == "SESSION":
        client.force_login(world.user)
        headers["HTTP_X_PLATFORM"] = "WEB"
        headers["HTTP_X_ASTROLIFT_TEAM"] = str(world.platform.pk)
    else:
        issued = mint_token()
        ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            team=world.medops,
            name="ops-http",
            token_hash=issued.token_hash,
            scopes=["admin"],
        )
        headers["HTTP_AUTHORIZATION"] = "Bearer " + issued.plaintext
    response = client.post(
        "/app/gql/config/",
        data={"query": "{ astroliftAlertRulesPage { totalCount items { name } } }"},
        content_type="application/json",
        **headers,
    )
    body = response.json()
    assert response.status_code == 200 and "errors" not in body, body
    page = body["data"]["astroliftAlertRulesPage"]
    assert page["totalCount"] == 1 and page["items"] == [{"name": world.medops_app.slug}]
    response = client.post(
        "/app/gql/config/",
        data={
            "query": """mutation($app: String!) { setAlertSubscription(input: {
        appSlug: $app, alertKind: "deploy_failure", channel: "email", enabled: true
    }) { ok errors { code } } }""",
            "variables": {"app": world.platform_app.slug},
        },
        content_type="application/json",
        **headers,
    )
    body = response.json()
    assert response.status_code == 200 and "errors" not in body, body
    assert body["data"]["setAlertSubscription"] == {"ok": False, "errors": [{"code": "PERMISSION_DENIED"}]}
    assert not UserAlertSubscription.objects.exists()


def test_alert_collection_policy_filters_all_app_environments_before_total(world):
    grant(world, "ORG")
    stage = AppEnvironment.objects.create(
        registered_app=world.medops_app, name="staging", tenant_cluster=world.cluster
    )
    stage_rule = AlertRule.objects.create(
        organization=world.org, name="stage-specific", target="env", target_id=str(stage.guid)
    )
    Policy.objects.create(
        organization=world.org,
        name="hide staging alerts",
        slug="hide-staging",
        scope_level="ORG",
        scope_id=world.org.pk,
        effect="DENY",
        action_pattern=Permission.APP_READ.value,
        resource_pattern={"env": ["staging"]},
        actor_pattern={},
        conditions=[],
    )
    with selected_sibling(world):
        page = OperationsQuery().astrolift_alert_rules_page(make_info(world.user), limit=1)
    assert page.total_count == 1 and page.next_cursor is None
    assert page.items[0].name == world.platform_app.slug
    assert str(stage_rule.guid) != str(page.items[0].id)


@pytest.mark.parametrize("route", ["astrolift_app_metrics", "bulk_rolling_restart"])
@pytest.mark.parametrize("corruption", ["foreign", "deleted"])
def test_org_operator_cannot_reach_foreign_or_deleted_provider_clusters(
    world, monkeypatch, route, corruption
):
    from astrolift_identity.models import Organization

    grant(world, "ORG")
    if corruption == "foreign":
        world.cluster.organization = Organization.objects.create(name="Foreign", slug="foreign-ops-2107")
        world.cluster.save(update_fields=["organization", "updated_at", "version"])
    else:
        world.cluster.soft_delete()
    with selected_sibling(world):
        if route == "astrolift_app_metrics":
            with pytest.raises(PermissionDenied):
                OperationsQuery().astrolift_app_metrics(make_info(world.user), app_slug=world.medops_app.slug)
        else:
            result = OperationsMutation().bulk_rolling_restart(
                make_info(world.user), input=inputs.BulkRollingRestartInput(app_slugs=[world.medops_app.slug])
            )
            assert result.ok_count == 0 and result.failed_count == 1
    assert world.calls == []


@pytest.mark.parametrize("level,allowed", [("viewer", False), ("deployer", True), ("owner", True)])
def test_app_share_preserves_permission_specific_bearer_ceiling(world, level, allowed):
    from astrolift_registry.models import AppTeamAccess

    grant(world, "ORG")
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level=level)
    with selected_sibling(world), bearer(world):
        result = OperationsMutation().bulk_resync_manifest(
            make_info(world.user), input=inputs.BulkResyncManifestInput(app_slugs=[world.platform_app.slug])
        )
    assert result.ok_count == int(allowed)
    assert world.calls == ([world.platform_app.pk] if allowed else [])


@pytest.mark.parametrize(
    "route,input_type",
    [
        ("bulk_rolling_restart", inputs.BulkRollingRestartInput),
        ("bulk_resync_manifest", inputs.BulkResyncManifestInput),
        ("bulk_push_secrets", inputs.BulkPushSecretsInput),
    ],
)
def test_bulk_entry_denial_keeps_public_per_app_result_shape(world, route, input_type):
    kwargs = {"app_slugs": [world.medops_app.slug, world.platform_app.slug]}
    if route == "bulk_push_secrets":
        kwargs["bundle_slug"] = world.bundle.slug
    with selected_sibling(world):
        result = getattr(OperationsMutation(), route)(make_info(world.user), input=input_type(**kwargs))
    assert result.ok_count == 0 and result.failed_count == 2
    assert all(not item.ok and item.errors for item in result.per_app)
    assert world.calls == [] and not AppSecretBundleRef.objects.exists()
    from astrolift_operations.models import AuditEvent

    action = {
        "bulk_rolling_restart": "app.bulk.rolling_restart",
        "bulk_resync_manifest": "app.bulk.resync_manifest",
        "bulk_push_secrets": "app.bulk.push_secrets",
    }[route]
    audit = AuditEvent.objects.filter(action=action).latest("occurred_at")
    assert audit.decision == "DENY" and audit.data["error_code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("kind", ["ORG", "TEAM", "PROJECT"])
def test_non_inheriting_grants_do_not_reveal_descendant_rule_owners(world, kind):
    binding = grant(world, kind)
    binding.inherits = False
    binding.save(update_fields=["inherits", "updated_at", "version"])
    with selected_sibling(world):
        page = OperationsQuery().astrolift_alert_rules_page(make_info(world.user))
    expected = [world.global_rule.name] if kind == "ORG" else []
    assert [row.name for row in page.items] == expected
    assert page.total_count == len(expected)


def test_alert_page_queries_do_not_grow_with_rule_count(world):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    grant(world)
    AlertRule.objects.bulk_create(
        [
            AlertRule(organization=world.org, name=f"own-{n}", target="app", target_id=world.medops_app.slug)
            for n in range(30)
        ]
    )
    with selected_sibling(world), CaptureQueriesContext(connection) as small:
        page = OperationsQuery().astrolift_alert_rules_page(make_info(world.user), limit=1)
    assert len(page.items) == 1
    with selected_sibling(world), CaptureQueriesContext(connection) as large:
        page = OperationsQuery().astrolift_alert_rules_page(make_info(world.user), limit=50)
    assert len(page.items) == 31
    assert len(large) <= len(small) + 1


def test_http_bulk_denial_uses_the_existing_bulk_result_contract(world, settings):
    from django.test import Client

    from astrolift_identity.models import Member

    settings.DEBUG = False
    settings.MIDDLEWARE = [item for item in settings.MIDDLEWARE if "DebugToolbar" not in item]
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk, is_active=True)
    client = Client()
    client.force_login(world.user)
    response = client.post(
        "/app/gql/config/",
        data={
            "query": "mutation($apps: [String!]!) { bulkRollingRestart(input: {appSlugs: $apps}) { okCount failedCount perApp { appSlug ok errors } } }",
            "variables": {"apps": [world.medops_app.slug, world.platform_app.slug]},
        },
        content_type="application/json",
        HTTP_X_PLATFORM="WEB",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
    )
    body = response.json()
    assert response.status_code == 200 and "errors" not in body, body
    result = body["data"]["bulkRollingRestart"]
    assert result["okCount"] == 0 and result["failedCount"] == 2
    assert all(not item["ok"] and item["errors"] for item in result["perApp"])
    assert world.calls == []
