"""A scheduled run's result goes to Slack, Teams and email (#2244).

HTTP is stubbed at ``httpx.post`` and mail uses Django's locmem outbox, so
these drive the real card building, credential read, retry bookkeeping and
audit record without touching a network.
"""

from __future__ import annotations

import logging
import uuid

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from astrolift_lifecycle.services.secrets import write_org_secret
from astrolift_operations.models import WorkflowRun
from core.mutations import register_audit_writer
from workflows.delivery import DeliveryConfigurationError, deliver_run, validate_channel, validate_webhook_url
from workflows.models import DeliveryChannel, Workflow, WorkflowDefinition, WorkflowDelivery, WorkflowStage

pytestmark = pytest.mark.django_db

SLACK_URL = "https://hooks.slack.com/services/T000/B000/" + "s3cr3t" * 4
TEAMS_URL = (
    "https://prod-01.westus.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?sig=" + "t0k" * 8
)
BOT_TOKEN = "xoxb-1234567890-" + "b0t" * 8


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Delivery Org", slug=f"dlv-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def owner():
    return get_user_model().objects.create_user(
        username=f"owner-{uuid.uuid4().hex[:6]}", email="owner@example.com", password="pw"
    )


@pytest.fixture
def scheduled(org, owner):
    definition = WorkflowDefinition.objects.create(
        name="Digest",
        slug=f"digest-{uuid.uuid4().hex[:8]}",
        organization=org,
        model_label="",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=[],
        transitions=[],
        is_enabled=True,
    )
    WorkflowStage.objects.create(
        definition=definition,
        slug=f"{definition.slug}-0",
        order=0,
        kind=WorkflowStage.StageKind.CHECKPOINT,
        role="snapshot",
    )
    return Workflow.objects.create(
        organization=org,
        definition=definition,
        name="Morning digest",
        slug="morning-digest",
        trigger_kind=Workflow.TriggerKind.SCHEDULE,
        schedule_cron="0 7 * * *",
        created_by=owner,
    )


@pytest.fixture
def http(monkeypatch):
    """Record every POST and answer from a per-host queue (default 200)."""
    calls: list[dict] = []
    answers: dict[str, list] = {}

    def fake_post(url, *, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers or {}})
        host = httpx.URL(url).host
        queue = answers.get(host) or [200]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer if isinstance(answer, tuple) else (answer, None)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr("workflows.delivery.httpx.post", fake_post)
    return calls, answers


@pytest.fixture
def email_on(monkeypatch):
    monkeypatch.setattr("astrolift_operations.notification_email.notifications_email_enabled", lambda: True)


@pytest.fixture
def audit():
    entries = []
    register_audit_writer(entries.append)
    yield entries
    from core.mutations import _log_audit_entry

    register_audit_writer(_log_audit_entry)


def _channel(org, kind, *, secret=None, **fields):
    channel = DeliveryChannel.objects.create(
        organization=org, name=f"{kind}-{uuid.uuid4().hex[:4]}", kind=kind, **fields
    )
    if secret:
        write_org_secret(org, channel.secret_key, secret)
    return channel


def _settled_run(wf, status="completed", *, result=None, failure=None):
    """A fire's run, created and settled the way the schedule wrapper does."""
    from astrolift_workflows.activities.configured_workflow_schedule_fire import _create_sync
    from astrolift_workflows.activities.workflow_stage_activities import _finalize_workflow_run_records

    fire = _create_sync({"workflow_guid": str(wf.guid), "organization_id": wf.organization_id})
    run_id = fire.run_input.workflow_run_id
    _finalize_workflow_run_records(run_id, status, result, failure)
    run = WorkflowRun.objects.get(pk=int(run_id))
    return run, wf.runs.get(temporal_workflow_id=run.workflow_id)


def test_a_completed_run_posts_a_card_with_a_link_back_to_slack_and_teams(scheduled, http, settings):
    settings.FRONTEND_URL = "https://astrolift.example.com"
    calls, _ = http
    slack = _channel(scheduled.organization, "slack_webhook", secret=SLACK_URL)
    teams = _channel(scheduled.organization, "teams_webhook", secret=TEAMS_URL)
    scheduled.delivery_channels.set([slack, teams])
    run, instance = _settled_run(
        scheduled, result={"final_output": {"summary": "3 new incidents, 1 resolved"}}
    )

    summary = deliver_run(run.pk)

    link = f"https://astrolift.example.com/workflows/morning-digest/runs/{instance.pk}"
    assert (summary.delivered, summary.failed, summary.retrying) == (2, 0, 0)
    assert [c["url"] for c in calls] == [SLACK_URL, TEAMS_URL]
    slack_body, teams_body = calls[0]["json"], calls[1]["json"]
    assert slack_body["text"] == "Morning digest: completed"
    assert "3 new incidents, 1 resolved" in slack_body["blocks"][0]["text"]["text"]
    assert slack_body["blocks"][-1]["elements"][0]["url"] == link
    card = teams_body["attachments"][0]["content"]
    assert teams_body["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["actions"] == [{"type": "Action.OpenUrl", "title": "Open run", "url": link}]
    assert any(block.get("text") == "3 new incidents, 1 resolved" for block in card["body"])
    records = WorkflowDelivery.objects.filter(workflow_run=run).order_by("pk")
    assert [(r.channel_id, r.status, r.attempts, r.purpose) for r in records] == [
        (slack.pk, "delivered", 1, "result"),
        (teams.pk, "delivered", 1, "result"),
    ]
    # A completed run does not email its owner.
    assert mail.outbox == []


def test_a_failed_run_with_no_channels_emails_its_owner(scheduled, http, email_on):
    calls, _ = http
    run, instance = _settled_run(scheduled, "failed", failure={"message": "stage 0 timed out"})

    summary = deliver_run(run.pk)

    assert calls == []
    assert summary.delivered == 1
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["owner@example.com"]
    assert message.subject == "Morning digest: failed"
    assert "stage 0 timed out" in message.body
    assert f"/workflows/morning-digest/runs/{instance.pk}" in message.body
    record = WorkflowDelivery.objects.get(workflow_run=run)
    assert (record.purpose, record.recipient_user_id, record.status) == (
        "owner_notice",
        scheduled.created_by_id,
        "delivered",
    )


def test_a_failed_run_posts_to_its_channels_and_notifies_the_owner(scheduled, http, email_on):
    calls, _ = http
    teams = _channel(scheduled.organization, "teams_webhook", secret=TEAMS_URL)
    team_mail = _channel(scheduled.organization, "email", email_recipients=["ops@example.com"])
    scheduled.delivery_channels.set([teams, team_mail])
    run, _ = _settled_run(scheduled, "failed", failure={"message": "boom"})

    deliver_run(run.pk)

    card = calls[0]["json"]["attachments"][0]["content"]
    assert {"type": "TextBlock", "text": "failed", "color": "Attention", "spacing": "None"} in card["body"]
    assert {"title": "Owner", "value": scheduled.created_by.get_username()} in card["body"][-1]["facts"]
    assert sorted(m.to[0] for m in mail.outbox) == ["ops@example.com", "owner@example.com"]


def test_a_retry_skips_delivered_targets_and_the_last_attempt_records_the_failure(scheduled, http, audit):
    calls, answers = http
    slack = _channel(scheduled.organization, "slack_webhook", secret=SLACK_URL)
    teams = _channel(scheduled.organization, "teams_webhook", secret=TEAMS_URL)
    scheduled.delivery_channels.set([slack, teams])
    answers["prod-01.westus.logic.azure.com"] = [503]
    run, _ = _settled_run(scheduled)

    first = deliver_run(run.pk)
    assert (first.delivered, first.retrying) == (1, 1)
    second = deliver_run(run.pk)
    assert (second.delivered, second.retrying) == (1, 1)
    last = deliver_run(run.pk, final_attempt=True)
    assert (last.delivered, last.failed, last.retrying) == (1, 1, 0)

    # Slack was posted once; Teams on every attempt.
    assert [c["url"] for c in calls] == [SLACK_URL, TEAMS_URL, TEAMS_URL, TEAMS_URL]
    record = WorkflowDelivery.objects.get(workflow_run=run, channel=teams)
    assert (record.status, record.attempts, record.last_error) == ("failed", 3, "webhook answered HTTP 503")
    # Settled targets are left alone.
    deliver_run(run.pk)
    assert len(calls) == 4
    assert [e.extra["status"] for e in audit if e.action == "workflow.delivery"] == [
        "delivered",
        "retrying",
        "retrying",
        "failed",
    ]


def test_a_refused_webhook_is_failed_for_good(scheduled, http):
    calls, answers = http
    slack = _channel(scheduled.organization, "slack_webhook", secret=SLACK_URL)
    scheduled.delivery_channels.set([slack])
    answers["hooks.slack.com"] = [404]
    run, _ = _settled_run(scheduled)

    assert deliver_run(run.pk).failed == 1
    deliver_run(run.pk)
    assert len(calls) == 1


def test_credentials_never_reach_rows_audit_or_logs(scheduled, http, audit, caplog):
    _, answers = http
    slack = _channel(scheduled.organization, "slack_webhook", secret=SLACK_URL)
    app = _channel(scheduled.organization, "slack_app", secret=BOT_TOKEN, slack_channel="C0123456789")
    scheduled.delivery_channels.set([slack, app])
    answers["hooks.slack.com"] = [httpx.ConnectError("refused")]
    answers["slack.com"] = [(200, {"ok": False, "error": "not_in_channel"})]
    run, _ = _settled_run(scheduled)

    with caplog.at_level(logging.DEBUG):
        deliver_run(run.pk)

    stored = " ".join(
        str(v)
        for row in [*WorkflowDelivery.objects.values(), *DeliveryChannel.objects.values()]
        for v in row.values()
    )
    texts = [stored, repr(audit), caplog.text]
    for secret in (SLACK_URL, "s3cr3t", BOT_TOKEN, "t0k"):
        assert all(secret not in text for text in texts)
    errors = dict(WorkflowDelivery.objects.filter(workflow_run=run).values_list("channel_id", "last_error"))
    assert errors == {
        slack.pk: "no response from the webhook",
        app.pk: "Slack refused the post: not_in_channel",
    }


def test_webhook_urls_stay_out_of_the_http_client_request_log(scheduled, monkeypatch, caplog):
    # httpx itself logs each request URL at INFO; go through its real client.
    def real_client_post(url, *, json=None, headers=None, timeout=None):
        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        with httpx.Client(transport=transport) as client:
            return client.post(url, json=json, headers=headers, timeout=timeout)

    monkeypatch.setattr("workflows.delivery.httpx.post", real_client_post)
    slack = _channel(scheduled.organization, "slack_webhook", secret=SLACK_URL)
    scheduled.delivery_channels.set([slack])
    run, _ = _settled_run(scheduled)

    with caplog.at_level(logging.INFO, logger="httpx"):
        deliver_run(run.pk)

    assert "HTTP Request: POST https://hooks.slack.com/[redacted]" in caplog.text
    assert "s3cr3t" not in caplog.text


def test_a_slack_app_channel_threads_later_runs_under_the_first_post(scheduled, http):
    calls, answers = http
    app = _channel(scheduled.organization, "slack_app", secret=BOT_TOKEN, slack_channel="C0123456789")
    scheduled.delivery_channels.set([app])
    answers["slack.com"] = [
        (200, {"ok": True, "ts": "1700000000.000100"}),
        (200, {"ok": True, "ts": "1700000500.000200"}),
    ]

    first, _ = _settled_run(scheduled)
    deliver_run(first.pk)
    second, _ = _settled_run(scheduled, "failed", failure={"message": "x"})
    deliver_run(second.pk)

    assert [c["url"] for c in calls] == ["https://slack.com/api/chat.postMessage"] * 2
    assert calls[0]["headers"] == {"Authorization": f"Bearer {BOT_TOKEN}"}
    assert calls[0]["json"]["channel"] == "C0123456789"
    assert "thread_ts" not in calls[0]["json"]
    assert calls[1]["json"]["thread_ts"] == "1700000000.000100"
    # A failure in a thread is broadcast to the channel too.
    assert calls[1]["json"]["reply_broadcast"] is True


def test_email_stays_off_until_the_install_turns_it_on(scheduled, http, monkeypatch):
    monkeypatch.setattr("astrolift_operations.notification_email.notifications_email_enabled", lambda: False)
    run, _ = _settled_run(scheduled, "failed", failure={"message": "x"})

    assert deliver_run(run.pk).failed == 1
    assert mail.outbox == []
    record = WorkflowDelivery.objects.get(workflow_run=run)
    assert record.last_error == "email notifications are turned off on this install"


def test_inactive_and_deleted_channels_are_not_posted(scheduled, http):
    calls, _ = http
    paused = _channel(scheduled.organization, "slack_webhook", secret=SLACK_URL, is_active=False)
    gone = _channel(scheduled.organization, "slack_webhook", secret=SLACK_URL)
    scheduled.delivery_channels.set([paused, gone])
    gone.soft_delete()
    run, _ = _settled_run(scheduled)

    deliver_run(run.pk)
    assert calls == []


@pytest.mark.parametrize(
    "kind,url",
    [
        ("slack_webhook", "http://hooks.slack.com/services/x"),
        ("slack_webhook", "https://hooks.slack.com.evil.example/services/x"),
        ("slack_webhook", "https://user:pw@hooks.slack.com/services/x"),
        ("slack_webhook", "https://169.254.169.254/latest"),
        ("teams_webhook", "https://hooks.slack.com/services/x"),
        ("teams_webhook", "https://webhook.office.com.evil.example/x"),
        ("teams_webhook", "https://example.webhook.office.com:8443/x"),
    ],
)
def test_webhook_urls_off_the_vendor_hosts_are_refused(kind, url):
    with pytest.raises(DeliveryConfigurationError):
        validate_webhook_url(kind, url)


def test_vendor_webhook_urls_are_accepted():
    assert validate_webhook_url("slack_webhook", SLACK_URL) == SLACK_URL
    assert validate_webhook_url("teams_webhook", TEAMS_URL) == TEAMS_URL
    assert validate_webhook_url("teams_webhook", "https://contoso.webhook.office.com/webhookb2/x")


def test_channel_fields_are_validated_per_kind():
    assert validate_channel(
        "email", secret=None, slack_channel="", recipients=[" a@example.com", "A@example.com"], creating=True
    ) == ("", ["a@example.com"])
    with pytest.raises(DeliveryConfigurationError):
        validate_channel("email", secret=None, slack_channel="", recipients=["not an address"], creating=True)
    with pytest.raises(DeliveryConfigurationError):
        validate_channel(
            "slack_app",
            secret="xoxp-user-token-123456",
            slack_channel="C0123456789",
            recipients=None,
            creating=True,
        )
    with pytest.raises(DeliveryConfigurationError):
        validate_channel("slack_webhook", secret=None, slack_channel="", recipients=None, creating=True)
    # An update without a credential keeps the stored one.
    assert validate_channel(
        "teams_webhook", secret=None, slack_channel="", recipients=None, creating=False
    ) == ("", [])
