"""The push data contract the mobile app consumes (#2161).

``backend/contracts/push-notifications.json`` is generated here, on real
Postgres, from the real dispatcher and the real push drivers:

* ``events``: one notification per kind in ``NOTIFICATION_TEMPLATES``, as the
  device receives it (title, body, category and the ``data`` map). Alerts,
  sessions, app uptime and expiry, certificate warnings and agent messages go
  through their production emitters. The deploy, secret, cluster-bootstrap
  and deregister kinds have a template but no emitter yet; they are emitted
  with the payload keys their templates read.
* ``deliveries``: the alert notification as each driver sends it to iOS and
  Android, captured by a recording transport. Direct APNs (#2256) sends to
  iOS only; its request is recorded whole, with the per-send provider token
  and notification id replaced by placeholders.
* ``routing``: two installs that both hold an app called ``web``, and what a
  client must do with each payload (``docs/mobile-push-contract.md``).

The test fails when the committed file differs. Regenerate it with
``ASTROLIFT_WRITE_PUSH_CONTRACT=1 pytest astrolift_operations/tests/test_push_contract_2161.py``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from _sdk.notification import DevicePlatform, NotificationPayload, PushTarget
from aws.notification_sns import SNSNotificationConfig, SNSNotificationDriver
from azure.notification_anh import AzureNotificationHubsConfig, AzureNotificationHubsDriver
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from gcp.notification_fcm import FCMConfig, FCMNotificationDriver
from k8s_native.notification_apns import APNsConfig, APNsNotificationDriver, APNsResponse
from k8s_native.notification_otlp import WebhookSMTPConfig, WebhookSMTPNotificationDriver

import core.events as _events_mod
from astrolift_agents.models import AgentTask
from astrolift_agents.services.agent_messages import signal_task_finished
from astrolift_identity.models import AstroliftSession, ClientKind, Member, Organization, Project, Team
from astrolift_lifecycle.models import CustomDomain
from astrolift_operations import alert_engine, cert_expiry_monitor, uptime_probe
from astrolift_operations.models import (
    AlertEvent,
    AlertRule,
    AppUptimeResult,
    DeviceRegistration,
    NotificationPreference,
)
from astrolift_operations.notification_dispatch import (
    NOTIFICATION_TEMPLATES,
    PUSH_PAYLOAD_VERSION,
    dispatch_event,
    emit_session_created_event,
    set_driver_override_for_tests,
    unset_driver_override_for_tests,
)
from astrolift_operations.tests.fixtures.notification_driver import MemoryNotificationDriver
from astrolift_registry import expiry
from astrolift_registry.models import Container, RegisteredApp, Workload
from config.schema import schema
from core.events import Event, register_event_subscriber
from core.permissions import Permission
from core.schema.context import StrawberryContext
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "push-notifications.json"

INSTALL_A = "install-5f0c2a9e41d7b3086ae2c41f"
INSTALL_B = "install-b81d40e6c2a95f17d03e6b2a"
ORG_A = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d01"
ORG_B = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d02"
APP_A = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1da1"
APP_B = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1db1"
DEPLOYMENT = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d11"
SECRET = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d21"
ALERT_RULE = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d31"
ALERT_EVENT = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d32"
CLUSTER = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d41"
DOMAIN = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d51"
SESSION = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d61"
AGENT_TASK = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d71"
AGENT_MESSAGE = "019a0f3c-6b2e-7c41-8d5e-2f7a9b0c1d72"
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

REQUIRED_KEYS = {"payload_version", "install_id", "event_kind", "target_kind", "action_url"}
OPTIONAL_KEYS = {"organization_id", "target_id"}


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def driver():
    snapshot = list(_events_mod._subscribers)
    register_event_subscriber(dispatch_event)
    recorder = MemoryNotificationDriver()
    set_driver_override_for_tests(recorder)
    yield recorder
    unset_driver_override_for_tests()
    _events_mod._subscribers[:] = snapshot


def _person(org: Organization, name: str, token: str):
    user = get_user_model().objects.create(email=f"{name}@example.test", username=name)
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG.value,
        scope_id=org.pk,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    DeviceRegistration.objects.create(
        user=user,
        device_token=token,
        platform="ios",
        label=f"{name}'s iPhone",
        driver="memory",
        organization=org,
    )
    return user


def _install(org_guid: str, app_guid: str, slug: str, person: str, token: str) -> SimpleNamespace:
    """An org holding app ``web``; each of the two orgs plays one install."""
    org = Organization.objects.create(guid=org_guid, name=slug.title(), slug=slug)
    team = Team.objects.create(organization=org, name="Platform", slug=f"platform-{slug}")
    project = Project.objects.create(organization=org, team=team, name="Core", slug=f"core-{slug}")
    user = _person(org, person, token)
    app = RegisteredApp.objects.create(
        guid=app_guid,
        organization=org,
        team=team,
        project=project,
        name="Web",
        slug="web",
        k8s_namespace=f"{slug}-web",
        provisioning_status="ready",
        created_by=user,
    )
    return SimpleNamespace(org=org, user=user, app=app)


def _one(driver: MemoryNotificationDriver, emit) -> NotificationPayload:
    driver.sent.clear()
    emit()
    assert len(driver.sent) == 1, [payload.data for _token, _platform, payload in driver.sent]
    return driver.sent[0][2]


def _event(payload: NotificationPayload) -> dict[str, Any]:
    return {
        "event_kind": payload.data["event_kind"],
        "title": payload.title,
        "body": payload.body,
        "category": payload.category,
        "data": payload.data,
    }


def _notifications(driver, monkeypatch, capture_on_commit) -> dict[str, NotificationPayload]:
    """Every kind the dispatcher sends, from install A's org."""
    monkeypatch.setenv("ASTROLIFT_INSTALL_ID", INSTALL_A)
    a = _install(ORG_A, APP_A, "acme", "reba", "apns-token-reba")
    user, org, app = a.user, a.org, a.app
    NotificationPreference.objects.create(
        user=user, channel="push", event_kind="alert.resolved", enabled=True
    )
    sent: dict[str, NotificationPayload] = {}

    def emit(kind: str, payload: dict[str, Any], resource_kind: str, resource_id: str):
        Event.emit(
            kind,
            payload,
            resource_kind=resource_kind,
            resource_id=resource_id,
            actor_user_id=user.pk,
            organization_id=org.pk,
        )

    deploy = {
        "app_slug": "web",
        "environment_name": "production",
        "deployment_guid": DEPLOYMENT,
        "triggerer_user_id": user.pk,
    }
    for kind in ("deploy.approved", "deploy.rejected", "deploy.failed"):
        sent[kind] = _one(driver, lambda kind=kind: emit(kind, deploy, "deployment", DEPLOYMENT))
    sent["secret.revealed"] = _one(
        driver,
        lambda: emit(
            "secret.revealed", {"key": "DATABASE_URL", "actor_email": "ops@example.test"}, "secret", SECRET
        ),
    )
    sent["cluster.bootstrap_failed"] = _one(
        driver,
        lambda: emit(
            "cluster.bootstrap_failed",
            {"cluster_name": "prod-us-east", "reason": "IAM role missing", "cluster_guid": CLUSTER},
            "cluster",
            CLUSTER,
        ),
    )
    sent["app.deregister_pending"] = _one(
        driver,
        lambda: emit(
            "app.deregister_pending",
            {"app_slug": "web", "app_guid": APP_A, "triggerer_user_id": user.pk},
            "registered_app",
            APP_A,
        ),
    )

    rule = AlertRule.objects.create(guid=ALERT_RULE, organization=org, name="5xx surge", severity="critical")
    incident = AlertEvent.objects.create(
        guid=ALERT_EVENT,
        rule=rule,
        organization=org,
        fired_at=NOW,
        severity="critical",
        summary="5xx surge is firing",
    )
    for kind in ("alert.fired", "alert.resolved"):
        sent[kind] = _one(driver, lambda kind=kind: alert_engine._emit_alert_event(rule, incident, kind))

    for is_up in (False, True):
        result = AppUptimeResult(
            registered_app=app,
            checked_at=NOW,
            target_url="https://web.acme.example.test/healthz",
            status_code=200 if is_up else 503,
            latency_ms=120,
            is_up=is_up,
            detail="" if is_up else "HTTP 503",
        )
        sent["app.recovered" if is_up else "app.down"] = _one(
            driver, lambda result=result: uptime_probe._emit_transition(app, result)
        )

    app.expires_at = datetime(2026, 10, 22, 13, 0, tzinfo=UTC)
    app.save(update_fields=["expires_at"])
    sent["app.expiring"] = _one(driver, lambda: expiry.warn(app, "1h"))

    domain = CustomDomain.objects.create(
        guid=DOMAIN, registered_app=app, hostname="web.acme.example.test", cert_expires_at=NOW
    )
    status = SimpleNamespace(health="expiring", days_until_expiry=6)
    sent["domain.cert_expiring"] = _one(
        driver,
        lambda: cert_expiry_monitor._emit(
            domain,
            cert_expiry_monitor.EXPIRING_EVENT,
            cert_expiry_monitor._payload_for(domain, status, threshold_days=7),
        ),
    )
    sent["domain.cert_renewal_failed"] = _one(
        driver,
        lambda: cert_expiry_monitor._emit(
            domain,
            cert_expiry_monitor.RENEWAL_FAILED_EVENT,
            cert_expiry_monitor._payload_for(domain, status, threshold_days=None),
        ),
    )

    session = AstroliftSession.objects.create(
        guid=SESSION, user=user, session_key="push-contract-session", client_kind=ClientKind.MOBILE.value
    )
    sent["auth.session.created"] = _one(
        driver,
        lambda: emit_session_created_event(
            user_id=user.pk,
            session_pk=session.pk,
            session_guid=str(session.guid),
            client_kind="mobile",
            ip_address="192.0.2.10",
            user_agent="AstroliftMobile/1.0",
            label="Pixel 9",
            organization_id=org.pk,
            occurred_at=NOW,
        ),
    )

    bind_role(
        user,
        permissions=[Permission.AGENT_READ, Permission.APP_READ],
        kind="ORG",
        scope_id=org.pk,
        slug="push-reader",
    )
    agent = Workload.objects.create(
        registered_app=app, name="Triage", slug="triage", kind=Workload.Kind.AGENT
    )
    Container.objects.create(workload=agent, name="agent", is_primary=True, image_ref="example.test/agent:1")
    task = AgentTask.objects.create(
        guid=AGENT_TASK,
        organization=org,
        agent_definition=agent,
        status=AgentTask.Status.COMPLETED,
        triggered_by_user=user,
        result={"output": "Reviewed 3 alerts; none needs you."},
    )

    def finished():
        with capture_on_commit(execute=False) as fan_out:
            signal_task_finished(task, AgentTask.Status.COMPLETED)
        task.agent_messages.update(guid=AGENT_MESSAGE)
        for callback in fan_out:
            callback()

    sent["agent.message.sent"] = _one(driver, finished)
    return sent


def _routing_payloads(driver, monkeypatch, down_a: NotificationPayload) -> dict[str, dict[str, str]]:
    """Install B: another org that also holds an app called ``web``."""
    monkeypatch.setenv("ASTROLIFT_INSTALL_ID", INSTALL_B)
    b = _install(ORG_B, APP_B, "globex", "sam", "apns-token-sam")
    result = AppUptimeResult(
        registered_app=b.app,
        checked_at=NOW,
        target_url="https://web.globex.example.test/healthz",
        status_code=503,
        latency_ms=95,
        is_up=False,
        detail="HTTP 503",
    )
    down_b = _one(driver, lambda: uptime_probe._emit_transition(b.app, result))
    return {"a": dict(down_a.data), "b": dict(down_b.data)}


class _RecordingHttp:
    def __init__(self, response: dict[str, Any]) -> None:
        self.posts: list[tuple[dict[str, str], dict[str, Any]]] = []
        self._response = response

    def post(self, *, url: str, headers: dict[str, str], body: str) -> dict[str, Any]:
        self.posts.append((dict(headers), json.loads(body)))
        return self._response


class _RecordingSns:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> dict[str, str]:
        self.published.append(kwargs)
        return {"MessageId": "sns-message-1"}


class _RecordingApns:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    def post(self, *, url: str, headers: dict[str, str], body: bytes) -> APNsResponse:
        self.posts.append((url, dict(headers), json.loads(body)))
        return APNsResponse(200, "apns-id-1")


def _apns_request(payload: NotificationPayload) -> dict[str, Any]:
    """The direct APNs request for iOS. The provider token is signed afresh
    (ES256 is randomised) and the notification id is per send, so both are
    checked here and recorded as placeholders."""
    key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    apns = _RecordingApns()
    driver = APNsNotificationDriver(
        config=APNsConfig(
            team_id="A1B2C3D4E5",
            key_id="K1L2M3N4O5",
            topic="com.example.astrolift",
            environment="production",
            auth_key=key.decode("ascii"),
            transport=apns,
            clock=NOW.timestamp,
        )
    )
    token = "a1b2c3d4" * 8
    result = driver.send(
        target=PushTarget(registration_id=token, platform=DevicePlatform.IOS), payload=payload
    )
    assert result.status == "delivered", result
    [(url, headers, body)] = apns.posts
    assert re.fullmatch(r"bearer [\w-]+\.[\w-]+\.[\w-]+", headers["authorization"])
    assert headers["apns-collapse-id"] == headers["apns-id"]
    return {
        "url": url,
        "headers": {
            **headers,
            "authorization": "bearer <ES256 provider token>",
            "apns-id": "<per-send UUID>",
            "apns-collapse-id": "<apns-id>",
        },
        "body": body,
    }


def _deliveries(payload: NotificationPayload) -> list[dict[str, Any]]:
    """The alert notification as each driver sends it, per mobile platform."""
    out: list[dict[str, Any]] = []

    def send(driver, platform: DevicePlatform, registration_id: str) -> None:
        result = driver.send(
            target=PushTarget(registration_id=registration_id, platform=platform), payload=payload
        )
        assert result.status in {"delivered", "queued"}, result

    for platform in (DevicePlatform.ANDROID, DevicePlatform.IOS):
        http = _RecordingHttp({"name": "projects/astrolift-mobile/messages/1"})
        fcm = FCMNotificationDriver(
            config=FCMConfig(project_id="astrolift-mobile", access_token="test-token", http_client=http)
        )
        send(fcm, platform, "fcm-registration-token")
        out.append({"driver": "gcp_fcm", "platform": platform.value, "request": http.posts[0][1]})

    for platform, arn in (
        (DevicePlatform.ANDROID, "arn:aws:sns:us-east-1:000000000000:endpoint/GCM/astrolift/1"),
        (DevicePlatform.IOS, "arn:aws:sns:us-east-1:000000000000:endpoint/APNS/astrolift/1"),
    ):
        sns_client = _RecordingSns()
        sns = SNSNotificationDriver(config=SNSNotificationConfig(region="us-east-1"), sns_client=sns_client)
        send(sns, platform, arn)
        published = sns_client.published[0]
        message = json.loads(published["Message"])
        out.append(
            {
                "driver": "aws_sns",
                "platform": platform.value,
                # SNS takes ``Message`` and each platform document in it as
                # JSON strings; they are decoded here to stay readable.
                "request": {
                    "MessageStructure": published["MessageStructure"],
                    "Message": {
                        key: value if key == "default" else json.loads(value)
                        for key, value in message.items()
                    },
                },
            }
        )

    for platform in (DevicePlatform.ANDROID, DevicePlatform.IOS):
        http = _RecordingHttp({"notificationId": "anh-1"})
        anh = AzureNotificationHubsDriver(
            config=AzureNotificationHubsConfig(
                namespace="astrolift",
                hub_name="mobile",
                shared_access_key_name="DefaultFullSharedAccessSignature",
                shared_access_key="dGVzdA==",
                http_client=http,
            )
        )
        send(anh, platform, "anh-installation-1")
        headers, body = http.posts[0]
        out.append(
            {
                "driver": "azure_anh",
                "platform": platform.value,
                "request": {
                    "ServiceBusNotification-Format": headers["ServiceBusNotification-Format"],
                    "ServiceBusNotification-Tags": headers["ServiceBusNotification-Tags"],
                    "body": body,
                },
            }
        )

    http = _RecordingHttp({})
    bridge = WebhookSMTPNotificationDriver(
        config=WebhookSMTPConfig(push_webhook_url="https://push-bridge.example.test/send", http_client=http)
    )
    send(bridge, DevicePlatform.ANDROID, "bridge-registration-1")
    out.append({"driver": "otlp_webhook", "platform": "android", "request": http.posts[0][1]})

    out.append({"driver": "apns", "platform": "ios", "request": _apns_request(payload)})
    return out


def _routing(data: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    a, b = data["a"], data["b"]
    without_install = {key: value for key, value in a.items() if key != "install_id"}
    unversioned = {key: value for key, value in a.items() if key != "payload_version"}
    newer = {**a, "payload_version": "2"}
    banner = {"opens": False}
    return [
        {
            "name": "Notification from the active install",
            "installs": [INSTALL_A, INSTALL_B],
            "active_install_id": INSTALL_A,
            "data": a,
            "expect": {
                "opens": True,
                "install_id": INSTALL_A,
                "organization_id": ORG_A,
                "action_url": a["action_url"],
            },
        },
        {
            "name": "Background install whose app has the same slug",
            "installs": [INSTALL_A, INSTALL_B],
            "active_install_id": INSTALL_A,
            "data": b,
            "expect": {
                "opens": True,
                "install_id": INSTALL_B,
                "organization_id": ORG_B,
                "action_url": b["action_url"],
            },
        },
        {
            "name": "Install this device no longer holds",
            "installs": [INSTALL_A],
            "active_install_id": INSTALL_A,
            "data": b,
            "expect": {**banner, "reason": "unknown_install"},
        },
        {
            "name": "Payload without install identity",
            "installs": [INSTALL_A, INSTALL_B],
            "active_install_id": INSTALL_A,
            "data": without_install,
            "expect": {**banner, "reason": "missing_install_id"},
        },
        {
            "name": "Payload without payload_version",
            "installs": [INSTALL_A, INSTALL_B],
            "active_install_id": INSTALL_A,
            "data": unversioned,
            "expect": {**banner, "reason": "unsupported_payload_version"},
        },
        {
            "name": "Payload from a newer major version",
            "installs": [INSTALL_A, INSTALL_B],
            "active_install_id": INSTALL_A,
            "data": newer,
            "expect": {**banner, "reason": "unsupported_payload_version"},
        },
    ]


def _document(driver, monkeypatch, capture_on_commit) -> dict[str, Any]:
    sent = _notifications(driver, monkeypatch, capture_on_commit)
    routing = _routing_payloads(driver, monkeypatch, sent["app.down"])
    return {
        "contract": "astrolift.push-notification",
        "payload_version": PUSH_PAYLOAD_VERSION,
        "generated_by": "backend/astrolift_operations/tests/test_push_contract_2161.py",
        "documentation": "docs/mobile-push-contract.md",
        "data_keys": {"required": sorted(REQUIRED_KEYS), "optional": sorted(OPTIONAL_KEYS)},
        "events": [_event(sent[kind]) for kind in sorted(sent)],
        "deliveries": _deliveries(sent["alert.fired"]),
        "routing": _routing(routing),
    }


def _render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def test_contract_file_matches_the_dispatcher_and_drivers(
    driver, monkeypatch, django_capture_on_commit_callbacks
):
    rendered = _render(_document(driver, monkeypatch, django_capture_on_commit_callbacks))
    if os.environ.get("ASTROLIFT_WRITE_PUSH_CONTRACT") == "1":
        CONTRACT.write_text(rendered, encoding="utf-8")
    assert CONTRACT.read_text(encoding="utf-8") == rendered, (
        "contracts/push-notifications.json is stale; regenerate it with "
        "ASTROLIFT_WRITE_PUSH_CONTRACT=1 pytest astrolift_operations/tests/test_push_contract_2161.py"
    )


def test_every_kind_carries_the_versioned_identity_and_no_action(
    driver, monkeypatch, django_capture_on_commit_callbacks
):
    sent = _notifications(driver, monkeypatch, django_capture_on_commit_callbacks)
    assert set(sent) == set(NOTIFICATION_TEMPLATES)
    for kind, payload in sent.items():
        data = payload.data
        assert REQUIRED_KEYS <= set(data) <= REQUIRED_KEYS | OPTIONAL_KEYS, kind
        assert all(isinstance(value, str) and value for value in data.values()), kind
        assert data["payload_version"] == "1"
        assert data["install_id"] == INSTALL_A
        assert data["organization_id"] == ORG_A
        assert data["event_kind"] == kind
        assert data["action_url"] == payload.action_url
        # A tap opens a page that reads; no link names an action or a token.
        assert data["action_url"].startswith("astrolift://"), kind
        assert not data["action_url"].startswith(("astrolift://approve", "astrolift://enroll")), kind
    targets = {
        kind: (payload.data["target_kind"], payload.data.get("target_id")) for kind, payload in sent.items()
    }
    assert targets == {
        "agent.message.sent": ("agent_message", AGENT_MESSAGE),
        "alert.fired": ("alert_event", ALERT_EVENT),
        "alert.resolved": ("alert_event", ALERT_EVENT),
        "app.deregister_pending": ("app", APP_A),
        "app.down": ("app", APP_A),
        "app.expiring": ("app", APP_A),
        "app.recovered": ("app", APP_A),
        "auth.session.created": ("session", SESSION),
        "cluster.bootstrap_failed": ("cluster", CLUSTER),
        "deploy.approved": ("deployment", DEPLOYMENT),
        "deploy.failed": ("deployment", DEPLOYMENT),
        "deploy.rejected": ("deployment", DEPLOYMENT),
        "domain.cert_expiring": ("custom_domain", DOMAIN),
        "domain.cert_renewal_failed": ("custom_domain", DOMAIN),
        "secret.revealed": ("secret", SECRET),
    }
    urls = {kind: payload.action_url for kind, payload in sent.items()}
    assert urls["auth.session.created"] == f"astrolift://sessions/{SESSION}"
    assert urls["alert.fired"] == urls["alert.resolved"] == f"astrolift://alerts/{ALERT_EVENT}"
    assert (
        urls["app.deregister_pending"] == urls["app.down"] == urls["app.expiring"] == "astrolift://apps/web"
    )
    # The phone reads a deployment under its app (``apps/:slug/deployments/:id``)
    # and shows it only when the environment it is given matches.
    assert (
        urls["deploy.approved"]
        == urls["deploy.rejected"]
        == urls["deploy.failed"]
        == f"astrolift://apps/web/deployments/{DEPLOYMENT}?environmentName=production"
    )


def test_installs_with_the_same_app_slug_differ_only_in_identity(
    driver, monkeypatch, django_capture_on_commit_callbacks
):
    sent = _notifications(driver, monkeypatch, django_capture_on_commit_callbacks)
    data = _routing_payloads(driver, monkeypatch, sent["app.down"])
    a, b = data["a"], data["b"]
    assert {key for key in a if a[key] != b[key]} == {"install_id", "organization_id", "target_id"}
    assert (a["install_id"], a["organization_id"], a["target_id"]) == (INSTALL_A, ORG_A, APP_A)
    assert (b["install_id"], b["organization_id"], b["target_id"]) == (INSTALL_B, ORG_B, APP_B)
    assert a["action_url"] == b["action_url"] == "astrolift://apps/web"


def _handshake_install_id() -> str:
    request = RequestFactory().get("/app/gql/config/")
    request.user = AnonymousUser()
    request.session = {}
    result = schema.execute_sync(
        "{ astroliftServerInfo { installId } }", context_value=StrawberryContext(request)
    )
    assert result.errors is None, result.errors
    return str(result.data["astroliftServerInfo"]["installId"])


def test_push_install_id_is_the_handshake_install_id_on_every_host(driver, monkeypatch):
    monkeypatch.delenv("ASTROLIFT_INSTALL_ID", raising=False)
    org = Organization.objects.create(name="Acme", slug="acme-handshake")
    user = _person(org, "dana", "apns-token-dana")
    # The web replica that answered the handshake and the worker that sends
    # the push run on different hosts.
    monkeypatch.setattr("socket.gethostname", lambda: "astrolift-web-6c9f7")
    handshake = _handshake_install_id()
    monkeypatch.setattr("socket.gethostname", lambda: "astrolift-worker-2b81d")
    payload = _one(
        driver,
        lambda: Event.emit(
            "deploy.failed",
            {"deployment_guid": DEPLOYMENT, "triggerer_user_id": user.pk},
            resource_kind="deployment",
            resource_id=DEPLOYMENT,
            organization_id=org.pk,
        ),
    )
    assert payload.data["install_id"] == handshake
    assert handshake.startswith("install-")


def test_operator_install_id_wins_in_the_handshake_and_the_push(driver, monkeypatch):
    monkeypatch.setenv("ASTROLIFT_INSTALL_ID", "acme-prod")
    org = Organization.objects.create(name="Acme", slug="acme-operator")
    user = _person(org, "eli", "apns-token-eli")
    payload = _one(
        driver,
        lambda: Event.emit(
            "deploy.failed",
            {"deployment_guid": DEPLOYMENT, "triggerer_user_id": user.pk},
            resource_kind="deployment",
            resource_id=DEPLOYMENT,
            organization_id=org.pk,
        ),
    )
    assert payload.data["install_id"] == _handshake_install_id() == "acme-prod"
