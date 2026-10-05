"""Authenticated GraphQL with actual PostgreSQL and native signed SES HTTP."""

import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Member, Organization, Project, Role, RoleBinding, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services import email_delivery
from astrolift_services.models import EmailDeliveryObservation, EmailDeliveryTest, ManagedService
from core.permissions import Permission
from providers.tests.aws.test_email_delivery_http_2289 import ACCOUNT, MARKER, TOPIC
from providers.tests.aws.test_email_delivery_http_2289 import wire as wire_fixture

pytestmark = pytest.mark.django_db


@pytest.fixture
def wire():
    yield from wire_fixture.__wrapped__()


@pytest.fixture
def world(client, wire, monkeypatch, settings):
    cache.clear()
    org = Organization.objects.create(name="Mail diagnostics", slug="mail-diagnostics-2289")
    user = get_user_model().objects.create_user(username="email-operator-2289", password="fixture-only")
    member = Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk)
    team = Team.objects.create(organization=org, name="Mail", slug="mail-team")
    project = Project.objects.create(organization=org, team=team, name="Mail", slug="mail-project")
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={
            "name": "AWS",
            "plugin_version": "0.2.0",
            "capabilities_manifest": {},
            "config_schema": {},
        },
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="Mail cluster",
        slug="mail-cluster",
        provider_plugin=plugin,
        endpoint="https://cluster.example.test",
        region="us-west-2",
        provider_config={"account_id": ACCOUNT},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Mail app",
        slug="mail-app",
        provisioning_status="ready",
        manifest_raw='astrolift_version = 1\nname = "mail-app"\n',
    )
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="production")
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="email",
        variant="ses",
        name="Diagnostic sender",
        backend_ref="email/example.test",
        applied_config={"identity": "example.test"},
        config={"identity": "example.test"},
        status="active",
    )
    role = Role.objects.create(
        organization=org,
        name="Mail operator",
        slug="mail-operator",
        scope_level="APP",
        permissions=[
            Permission.APP_READ.value,
            Permission.APP_UPDATE.value,
            Permission.MANAGED_SERVICE_UPDATE.value,
        ],
    )
    binding = RoleBinding.objects.create(user=user, role=role, scope_kind="APP", scope_id=app.pk)
    wire["owner"] = str(svc.guid)
    settings.SES_EVENTS_SNS_TOPIC_ARN = TOPIC
    from aws.email_delivery import AmazonSESTestDelivery

    original = AmazonSESTestDelivery.__init__

    def native_build(service, **kwargs):
        result = wire["build"](service, **kwargs)

        def received(model, **_kwargs):
            if model.name == "GetEmailIdentity" and wire.get("after_source"):
                wire["after_source"]("/v2/email/identities/example.test")
            if model.name == "SendEmail" and wire.get("after_send"):
                wire["after_send"]()

        result.meta.events.register("after-call.*.*", received)
        return result

    def native_driver(self, config, **kwargs):
        original(self, config, build=native_build, **kwargs)

    monkeypatch.setattr(AmazonSESTestDelivery, "__init__", native_driver)
    client.force_login(user)
    return SimpleNamespace(
        org=org,
        user=user,
        member=member,
        app=app,
        env=env,
        cluster=cluster,
        svc=svc,
        role=role,
        binding=binding,
        client=client,
        wire=wire,
    )


def send(world, *, request_id=None, **overrides):
    payload = {
        "managedServiceId": str(world.svc.guid),
        "expectedVersion": world.svc.version,
        "requestId": str(request_id or uuid4()),
        "recipient": "qa@example.test",
        "subject": MARKER,
        "body": MARKER,
    }
    payload.update(overrides)
    response = world.client.post(
        "/app/gql/config/",
        content_type="application/json",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="web",
        data=json.dumps(
            {
                "query": "mutation($input:SendEmailDeliveryTestInput!){sendEmailDeliveryTest(input:$input){ok errors{code message} data{id status sender recipient eventTrackingConfigured providerMessageId requestId}}}",
                "variables": {"input": payload},
            }
        ),
    )
    assert response.status_code == 200, response.content
    data = response.json()
    assert not data.get("errors"), data
    return data["data"]["sendEmailDeliveryTest"]


def outbound(world):
    return [row for row in world.wire["requests"] if row[1] == "/v2/email/outbound-emails"]


def test_native_send_saved_once_and_replay_does_not_send(world, caplog):
    nonce = uuid4()
    first = send(world, request_id=nonce)
    assert first["ok"], first
    assert first["data"]["status"] == "accepted"
    assert first["data"]["eventTrackingConfigured"]
    second = send(world, request_id=nonce)
    assert second["ok"] and second["data"]["id"] == first["data"]["id"]
    assert len(outbound(world)) == 1
    row = EmailDeliveryTest.objects.get()
    assert row.requester_id == world.user.pk and row.managed_service_id == world.svc.pk
    assert row.source_sha256 and row.intent_sha256
    assert MARKER not in str(row.__dict__)
    assert MARKER not in caplog.text
    from core.schema.audit import MutationAuditLog

    assert MARKER not in str(list(MutationAuditLog.objects.values()))


def test_conflicting_replay_refuses_without_second_send(world):
    nonce = uuid4()
    assert send(world, request_id=nonce)["ok"]
    again = send(world, request_id=nonce, recipient="other@example.test")
    assert not again["ok"] and again["errors"][0]["message"] == "EMAIL_TEST_INTENT_CONFLICT"
    assert len(outbound(world)) == 1


@pytest.mark.parametrize("variant", ["smtp", "azure_acs", "sendgrid"])
def test_unsupported_selected_transport_never_uses_install_fallback(world, variant):
    world.svc.variant = variant
    world.svc.save()
    result = send(world)
    assert not result["ok"] and not world.wire["requests"]
    assert EmailDeliveryTest.objects.count() == 0


def test_stale_reviewed_service_version_refuses_before_native_io(world):
    result = send(world, expectedVersion=world.svc.version - 1)
    assert not result["ok"] and not world.wire["requests"]
    assert EmailDeliveryTest.objects.count() == 0


@pytest.mark.parametrize("field", ["subject", "body"])
def test_unpaired_unicode_is_editable_content_refusal_before_intent(world, field, caplog):
    result = send(world, **{field: "private-invalid-unicode-2289\ud800"})
    assert not result["ok"]
    assert result["errors"][0]["message"] == "INVALID_TEST_CONTENT"
    assert not world.wire["requests"]
    assert EmailDeliveryTest.objects.count() == 0
    assert "private-invalid-unicode-2289" not in caplog.text


def test_current_scope_withdrawal_refuses_before_native_io(world):
    world.binding.soft_delete()
    result = send(world)
    assert not result["ok"] and not world.wire["requests"]


@pytest.mark.parametrize("withdraw", ["role", "member", "cluster", "source"])
def test_withdrawal_after_real_provider_reply_blocks_send(world, withdraw):
    def after(path):
        if "/identities/" not in path:
            return
        if withdraw == "role":
            world.binding.soft_delete()
        elif withdraw == "member":
            world.member.is_active = False
            world.member.save()
        elif withdraw == "cluster":
            world.cluster.soft_delete()
        else:
            world.svc.backend_ref = "email/foreign.example.test"
            world.svc.save()

    world.wire["after_source"] = after
    result = send(world)
    assert not result["ok"], result
    assert EmailDeliveryTest.objects.get().status in {"failed", "unknown"}
    assert not outbound(world)


def test_account_suppression_is_persisted_and_never_bypassed(world):
    world.wire["suppressed"] = True
    result = send(world)
    assert result["ok"] and result["data"]["status"] == "suppressed"
    assert not outbound(world)


def event(row, *, kind="Delivery", message_id=None, topic=None, account=None):
    now = timezone.now().isoformat().replace("+00:00", "Z")
    return (
        {"TopicArn": topic or TOPIC, "MessageId": str(uuid4())},
        {
            "eventType": kind,
            "mail": {
                "messageId": message_id or row.provider_message_id,
                "sendingAccountId": account or ACCOUNT,
                "source": row.sender,
                "destination": [row.recipient],
                "timestamp": now,
                "tags": {
                    "astrolift_test_id": [str(row.guid)],
                    "astrolift_managed_service_id": [str(row.managed_service.guid)],
                },
                "commonHeaders": {"subject": MARKER},
            },
            kind[0].lower() + kind[1:]: {"timestamp": now, "private": MARKER},
        },
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("Delivery", "delivered"),
        ("Bounce", "bounced"),
        ("Complaint", "complained"),
        ("DeliveryDelay", "deferred"),
        ("Reject", "rejected"),
    ],
)
def test_correlated_outcomes_are_content_free_and_duplicate_safe(world, kind, expected):
    assert send(world)["ok"]
    row = EmailDeliveryTest.objects.get()
    envelope, message = event(row, kind=kind)
    assert email_delivery.observe_test(envelope, message)
    assert email_delivery.observe_test(envelope, message)
    row.refresh_from_db()
    assert row.status == expected
    assert EmailDeliveryObservation.objects.count() == 1
    assert MARKER not in str(list(EmailDeliveryObservation.objects.values()))


@pytest.mark.parametrize("mismatch", ["message_id", "topic", "account"])
def test_foreign_provider_feedback_never_updates_test(world, mismatch):
    assert send(world)["ok"]
    row = EmailDeliveryTest.objects.get()
    kwargs = {mismatch: "foreign"}
    envelope, message = event(row, **kwargs)
    assert email_delivery.observe_test(envelope, message)
    row.refresh_from_db()
    assert row.status == "accepted" and EmailDeliveryObservation.objects.count() == 0


def test_observation_timeout_is_explicit_and_late_delivery_can_recover(world):
    assert send(world)["ok"]
    row = EmailDeliveryTest.objects.get()
    row.accepted_at = timezone.now() - timedelta(minutes=16)
    row.save()
    assert email_delivery.display_status(row) == "observation_timed_out"
    envelope, message = event(row)
    email_delivery.observe_test(envelope, message)
    row.refresh_from_db()
    assert email_delivery.display_status(row) == "delivered"


def test_feedback_before_send_ack_is_saved_and_reconciled(world):
    def early(_path):
        row = EmailDeliveryTest.objects.get()
        envelope, message = event(row, message_id="native-message-123")
        assert email_delivery.observe_test(envelope, message)
        row.refresh_from_db()
        assert row.status == "submitting" and row.provider_message_id == ""

    world.wire["after_source"] = early
    result = send(world)
    assert result["ok"] and result["data"]["status"] == "delivered", result
    assert len(outbound(world)) == 1 and EmailDeliveryObservation.objects.count() == 1


def test_wrong_early_message_is_never_promoted_to_delivery(world):
    def early(_path):
        row = EmailDeliveryTest.objects.get()
        envelope, message = event(row, message_id="another-message")
        assert email_delivery.observe_test(envelope, message)

    world.wire["after_source"] = early
    result = send(world)
    assert result["ok"] and result["data"]["status"] == "accepted", result
    assert len(outbound(world)) == 1


def test_failed_test_does_not_claim_observed_feedback_configuration(world):
    world.wire["suppressed"] = True
    result = send(world)
    assert result["ok"] and not result["data"]["eventTrackingConfigured"]


def test_rate_limit_bounds_actual_sends_and_replay_is_free(world):
    nonce = uuid4()
    assert send(world, request_id=nonce)["ok"]
    assert send(world, request_id=nonce)["ok"]
    assert send(world)["ok"] and send(world)["ok"]
    result = send(world)
    assert not result["ok"] and result["errors"][0]["message"] == "EMAIL_TEST_RATE_LIMITED"
    assert len(outbound(world)) == 3


def graphql(world, query, variables=None):
    response = world.client.post(
        "/app/gql/config/",
        content_type="application/json",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="web",
        data=json.dumps({"query": query, "variables": variables or {}}),
    )
    assert response.status_code == 200
    return response.json()


def test_legacy_entry_uses_exact_source_replay_and_does_not_fallback(world):
    nonce = str(uuid4())
    query = "mutation($input:SendManagedServiceTestEmailInput!){sendManagedServiceTestEmail(input:$input){ok errors{code message field requiresAttestation currentVersion requestedVersion supportedMethods} data{transport recipient}}}"
    payload = {"managedServiceId": str(world.svc.guid), "recipient": "qa@example.test"}
    refused = graphql(world, query, {"input": payload})
    assert not refused.get("errors"), refused
    assert not refused["data"]["sendManagedServiceTestEmail"]["ok"]
    assert not world.wire["requests"]
    payload.update(requestId=nonce, expectedVersion=world.svc.version)
    accepted = graphql(world, query, {"input": payload})
    assert not accepted.get("errors"), accepted
    assert accepted["data"]["sendManagedServiceTestEmail"]["ok"]
    assert graphql(world, query, {"input": payload})["data"]["sendManagedServiceTestEmail"]["ok"]
    assert len(outbound(world)) == 1 and EmailDeliveryTest.objects.count() == 1


def test_permission_refusal_retains_complete_public_envelope(world):
    world.binding.soft_delete()
    query = "mutation($input:SendEmailDeliveryTestInput!){sendEmailDeliveryTest(input:$input){ok errors{code message field requiresAttestation currentVersion requestedVersion supportedMethods} data{id}}}"
    result = graphql(
        world,
        query,
        {
            "input": {
                "managedServiceId": str(world.svc.guid),
                "requestId": str(uuid4()),
                "expectedVersion": world.svc.version,
                "recipient": "qa@example.test",
            }
        },
    )
    assert not result.get("errors"), result
    assert not result["data"]["sendEmailDeliveryTest"]["ok"]
    assert not world.wire["requests"]


def test_provider_ack_saved_privately_after_live_authority_withdrawal(world):
    world.wire["after_send"] = world.binding.soft_delete
    result = send(world)
    assert not result["ok"], result
    assert result["data"] is None
    row = EmailDeliveryTest.objects.get()
    assert row.status == "accepted" and row.provider_message_id == "native-message-123"
    assert len(outbound(world)) == 1


def test_bearer_revocation_after_native_source_reply_refuses_send(world):
    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken

    secret = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Email test operator",
        token_hash=secret.token_hash,
        token_last_4=secret.last4,
        scopes=["admin"],
    )
    world.client.logout()
    world.client.defaults["HTTP_AUTHORIZATION"] = "Bearer " + secret.plaintext

    def revoke(_path):
        token.is_revoked = True
        token.save()

    world.wire["after_source"] = revoke
    result = send(world)
    assert not result["ok"] and not outbound(world)
    assert EmailDeliveryTest.objects.count() == 1


def test_history_has_real_pages_without_per_row_service_queries(world):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    assert send(world)["ok"]
    original = EmailDeliveryTest.objects.get()
    for _ in range(50):
        EmailDeliveryTest.objects.create(
            organization=world.org,
            managed_service=world.svc,
            requester=world.user,
            request_id=uuid4(),
            intent_sha256="1" * 64,
            source_sha256="2" * 64,
            sender=original.sender,
            recipient=original.recipient,
            account_id=original.account_id,
            region=original.region,
            identity=original.identity,
            configuration_set=original.configuration_set,
        )
    query = "query($id:GUID!,$after:String,$limit:Int!){emailDeliveryTestsPage(managedServiceId:$id,after:$after,limit:$limit){items{id managedServiceId status} totalCount nextCursor}}"
    with CaptureQueriesContext(connection) as first_queries:
        first = graphql(world, query, {"id": str(world.svc.guid), "limit": 50})
    assert not first.get("errors"), first
    page = first["data"]["emailDeliveryTestsPage"]
    assert len(page["items"]) == 50 and page["totalCount"] == 51 and page["nextCursor"]
    with CaptureQueriesContext(connection) as second_queries:
        second = graphql(world, query, {"id": str(world.svc.guid), "limit": 50, "after": page["nextCursor"]})
    assert not second.get("errors"), second
    tail = second["data"]["emailDeliveryTestsPage"]
    assert len(tail["items"]) == 1 and tail["nextCursor"] is None and tail["totalCount"] == 51
    assert not {item["id"] for item in page["items"]}.intersection(item["id"] for item in tail["items"])
    assert len(first_queries) == len(second_queries)


def test_foreign_org_cannot_query_or_send_original_service(world):
    foreign = Organization.objects.create(name="Other mail tenant", slug="other-mail-tenant")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=foreign.pk)
    world.org = foreign
    result = send(world)
    assert not result["ok"] and not world.wire["requests"]
    history = graphql(
        world,
        "query($id:GUID!){emailDeliveryTestsPage(managedServiceId:$id){items{id}}}",
        {"id": str(world.svc.guid)},
    )
    assert history.get("errors") or history.get("data", {}).get("emailDeliveryTestsPage") is None


def test_retired_send_intent_cannot_be_automatically_retried(world):
    nonce = uuid4()
    assert send(world, request_id=nonce)["ok"]
    row = EmailDeliveryTest.objects.get()
    row.soft_delete()
    result = send(world, request_id=nonce)
    assert not result["ok"] and result["errors"][0]["message"] == "EMAIL_TEST_INTENT_RETIRED"
    assert len(outbound(world)) == 1


def test_retained_history_blocks_destructive_migration_rollback(world):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    assert send(world)["ok"]
    row = EmailDeliveryTest.objects.get()
    row.soft_delete()
    with pytest.raises(RuntimeError, match="EMAIL_TEST_ROLLBACK_REQUIRES_EMPTY_HISTORY"):
        MigrationExecutor(connection).migrate([("astrolift_services", "0035_merge_model_sources")])
    assert EmailDeliveryTest._base_manager.filter(pk=row.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_empty_mail_history_migration_roundtrip():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    assert not EmailDeliveryTest._base_manager.exists()
    previous = [("astrolift_services", "0035_merge_model_sources")]
    current = [("astrolift_services", "0043_email_delivery_tests")]
    try:
        MigrationExecutor(connection).migrate(previous)
        assert "astrolift_services_emaildeliverytest" not in connection.introspection.table_names()
    finally:
        MigrationExecutor(connection).migrate(current)
    assert "astrolift_services_emaildeliverytest" in connection.introspection.table_names()
    assert not EmailDeliveryTest.objects.exists()
