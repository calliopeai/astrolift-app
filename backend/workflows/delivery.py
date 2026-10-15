"""Deliver a scheduled workflow run's result to Slack, Teams and email (#2244).

A configured Workflow names the org's delivery channels it posts to. When a
scheduled run settles, ``ConfiguredWorkflowScheduleWorkflow`` calls
:func:`deliver_run` through an activity: each channel gets a compact card
(workflow, outcome, a short excerpt, a link back to the run), and a failed
run also emails the workflow's owner.

Every target gets one ``WorkflowDelivery`` row per run, which is both the
audit record and the idempotency key: a retried activity skips a target that
was already delivered or failed for good. A transient failure (no response,
408, 429, 5xx) leaves the row ``retrying`` and asks for a retry; on the last
attempt it is recorded as failed. Nothing here raises into the run.

Channel credentials (incoming webhook URLs, the Slack bot token) are read
from the org secret store at send time and never logged, persisted on a row,
or handed to Temporal.
"""

from __future__ import annotations

import dataclasses
import html
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

SECRET_PREFIX = "astrolift/workflow-delivery/"
HTTP_TIMEOUT_SECONDS = 10.0
SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
EXCERPT_LIMIT = 500
MAX_EMAIL_RECIPIENTS = 20

# Incoming webhook hosts per kind. A webhook URL anywhere else is refused,
# so a channel can never be pointed at an internal address.
WEBHOOK_HOSTS: dict[str, tuple[str, ...]] = {
    "slack_webhook": ("hooks.slack.com",),
    # Teams: the Workflows app (Power Automate) and legacy connector URLs.
    "teams_webhook": (".webhook.office.com", ".logic.azure.com", ".api.powerplatform.com"),
}
SLACK_CHANNEL_ID = re.compile(r"[A-Z0-9]{8,20}\Z")
SLACK_BOT_TOKEN = re.compile(r"xoxb-[A-Za-z0-9-]{10,250}\Z")
EMAIL_ADDRESS = re.compile(r"[^@\s<>,;]{1,64}@[^@\s<>,;]{1,255}\Z")
# Slack app errors worth another attempt; any other ``ok: false`` is final.
SLACK_RETRYABLE_ERRORS = frozenset({"ratelimited", "internal_error", "fatal_error", "service_unavailable"})


class DeliveryConfigurationError(ValueError):
    """A channel's configuration is not acceptable. The message is safe to show."""


# ---- configuration --------------------------------------------------------


def validate_webhook_url(kind: str, url: object) -> str:
    """Return ``url`` if it is an https incoming webhook on ``kind``'s hosts."""
    if not isinstance(url, str) or len(url) > 2048:
        raise DeliveryConfigurationError("Enter the channel's incoming webhook URL.")
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    allowed = WEBHOOK_HOSTS.get(kind, ())
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or not any(host == suffix or (suffix.startswith(".") and host.endswith(suffix)) for suffix in allowed)
    ):
        raise DeliveryConfigurationError(
            "The webhook URL must be an https URL from Slack or Microsoft Teams."
        )
    return url.strip()


def normalize_recipients(values: object) -> list[str]:
    if not isinstance(values, list) or not values:
        raise DeliveryConfigurationError("Add at least one email address.")
    out: list[str] = []
    for value in values:
        address = value.strip() if isinstance(value, str) else ""
        if not EMAIL_ADDRESS.fullmatch(address):
            raise DeliveryConfigurationError("Each recipient must be one email address.")
        if address.lower() not in (a.lower() for a in out):
            out.append(address)
    if len(out) > MAX_EMAIL_RECIPIENTS:
        raise DeliveryConfigurationError(f"At most {MAX_EMAIL_RECIPIENTS} recipients per channel.")
    return out


def validate_channel(
    kind: str, *, secret: str | None, slack_channel: str, recipients: object, creating: bool
):
    """Check one channel's fields; returns ``(slack_channel, recipients)``.

    ``secret`` is the webhook URL or bot token. ``None`` on an update keeps
    the stored one; a create must supply it.
    """
    from workflows.models import DeliveryChannel

    if kind not in DeliveryChannel.Kind.values:
        raise DeliveryConfigurationError("Choose Slack, Microsoft Teams or email.")
    if kind == DeliveryChannel.Kind.EMAIL:
        return "", normalize_recipients(recipients)
    if secret is None and creating:
        raise DeliveryConfigurationError("Enter the channel's credential.")
    if kind == DeliveryChannel.Kind.SLACK_APP:
        channel = (slack_channel or "").strip()
        if not SLACK_CHANNEL_ID.fullmatch(channel):
            raise DeliveryConfigurationError("Enter the Slack channel ID, for example C0123456789.")
        if secret is not None and not SLACK_BOT_TOKEN.fullmatch(secret.strip()):
            raise DeliveryConfigurationError("Enter the Slack app's bot token (xoxb-...).")
        return channel, []
    if secret is not None:
        validate_webhook_url(kind, secret)
    return "", []


def store_channel_secret(channel, secret: str) -> None:
    from astrolift_lifecycle.services.secrets import write_org_secret

    write_org_secret(channel.organization_id, channel.secret_key, secret.strip())


def channel_has_secret(channel) -> bool:
    from astrolift_lifecycle.services.secrets import list_org_secrets

    return channel.secret_key in list_org_secrets(channel.organization_id, prefix=channel.secret_key)


# ---- the card ---------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ResultCard:
    workflow_name: str
    ok: bool
    outcome: str
    summary: str
    run_url: str
    facts: tuple[tuple[str, str], ...]

    @property
    def title(self) -> str:
        return f"{self.workflow_name}: {'completed' if self.ok else self.outcome}"


def run_url(workflow, instance) -> str:
    base = str(getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    return f"{base}/workflows/{workflow.slug}/runs/{instance.pk}"


def _excerpt(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("summary", "text", "output", "result", "message"):
            if isinstance(value.get(key), str):
                value = value[key]
                break
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_LIMIT else text[: EXCERPT_LIMIT - 1] + "…"


def _duration(run) -> str:
    if not run.started_at or not run.ended_at:
        return ""
    seconds = max(0, int((run.ended_at - run.started_at).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds}s"


def build_card(workflow, run, instance, *, outcome: str) -> ResultCard:
    ok = outcome == "completed"
    if ok:
        summary = _excerpt(
            (run.result or {}).get("final_output") if isinstance(run.result, dict) else run.result
        )
    else:
        summary = _excerpt(
            (run.failure or {}).get("message") if isinstance(run.failure, dict) else run.failure
        )
    facts = [("Outcome", outcome), ("Trigger", run.trigger_kind or "schedule")]
    if run.started_at:
        facts.append(("Started", run.started_at.strftime("%Y-%m-%d %H:%M UTC")))
    if _duration(run):
        facts.append(("Duration", _duration(run)))
    owner = workflow.created_by
    if not ok and owner is not None:
        facts.append(("Owner", owner.get_full_name() or owner.get_username()))
    return ResultCard(
        workflow_name=workflow.name,
        ok=ok,
        outcome=outcome,
        summary=summary,
        run_url=run_url(workflow, instance),
        facts=tuple(facts),
    )


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_payload(card: ResultCard) -> dict[str, Any]:
    head = f"*{_slack_escape(card.title)}*"
    if card.summary:
        head += f"\n{_slack_escape(card.summary)}"
    return {
        "text": card.title,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": head}},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": _slack_escape("  |  ".join(f"{k}: {v}" for k, v in card.facts)),
                    }
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open run"},
                        "url": card.run_url,
                    }
                ],
            },
        ],
    }


def teams_payload(card: ResultCard) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": card.workflow_name, "weight": "Bolder", "size": "Medium", "wrap": True},
        {
            "type": "TextBlock",
            "text": card.outcome,
            "color": "Good" if card.ok else "Attention",
            "spacing": "None",
        },
    ]
    if card.summary:
        body.append({"type": "TextBlock", "text": card.summary, "wrap": True})
    body.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in card.facts]})
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "actions": [{"type": "Action.OpenUrl", "title": "Open run", "url": card.run_url}],
                },
            }
        ],
    }


def email_bodies(card: ResultCard) -> tuple[str, str, str]:
    """``(subject, text, html)`` for one card."""
    facts = "\n".join(f"{k}: {v}" for k, v in card.facts)
    text = "\n\n".join(
        part for part in (card.title, card.summary, facts, f"Open run: {card.run_url}") if part
    )
    rows = "".join(
        f"<tr><td style='color:#666;padding-right:12px'>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>"
        for k, v in card.facts
    )
    body = (
        f"<h2 style='margin:0 0 8px'>{html.escape(card.title)}</h2>"
        + (f"<p>{html.escape(card.summary)}</p>" if card.summary else "")
        + f"<table>{rows}</table>"
        + f"<p><a href='{html.escape(card.run_url, quote=True)}'>Open run</a></p>"
    )
    return card.title, text, body


# ---- transport --------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SendResult:
    status: str  # delivered | retry | failed
    error: str = ""
    external_ref: str = ""


def _classify(status_code: int | None) -> str:
    if status_code is not None and 200 <= status_code < 300:
        return "delivered"
    if status_code is not None and 400 <= status_code < 500 and status_code not in (408, 429):
        return "failed"
    return "retry"


def _post(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response | None:
    try:
        return httpx.post(url, json=payload, headers=headers or {}, timeout=HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None


def _send_webhook(channel, card: ResultCard, secret: str) -> SendResult:
    from workflows.models import DeliveryChannel

    try:
        url = validate_webhook_url(channel.kind, secret)
    except DeliveryConfigurationError:
        return SendResult("failed", "stored webhook URL is not an accepted Slack or Teams URL")
    payload = (
        slack_payload(card) if channel.kind == DeliveryChannel.Kind.SLACK_WEBHOOK else teams_payload(card)
    )
    response = _post(url, payload)
    if response is None:
        return SendResult("retry", "no response from the webhook")
    status = _classify(response.status_code)
    return SendResult(
        status, "" if status == "delivered" else f"webhook answered HTTP {response.status_code}"
    )


def _send_slack_app(channel, card: ResultCard, secret: str, thread_ts: str) -> SendResult:
    payload = {"channel": channel.slack_channel, **slack_payload(card)}
    if thread_ts:
        payload["thread_ts"] = thread_ts
        # A failure in a thread is still shown in the channel.
        payload["reply_broadcast"] = not card.ok
    response = _post(SLACK_POST_MESSAGE_URL, payload, {"Authorization": f"Bearer {secret}"})
    if response is None:
        return SendResult("retry", "no response from Slack")
    status = _classify(response.status_code)
    if status != "delivered":
        return SendResult(status, f"Slack answered HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        return SendResult("retry", "Slack answered with an unreadable body")
    if not isinstance(body, dict) or not body.get("ok"):
        error = (
            str((body or {}).get("error") or "unknown_error")[:64]
            if isinstance(body, dict)
            else "unknown_error"
        )
        return SendResult(
            "retry" if error in SLACK_RETRYABLE_ERRORS else "failed", f"Slack refused the post: {error}"
        )
    return SendResult("delivered", external_ref=str(body.get("ts") or "")[:64])


def _send_email(recipients: list[str], card: ResultCard, tag: str) -> SendResult:
    from astrolift_operations.notification_email import notifications_email_enabled, send_notice_email

    if not recipients:
        return SendResult("failed", "no email recipients")
    if not notifications_email_enabled():
        return SendResult("failed", "email notifications are turned off on this install")
    subject, text, body = email_bodies(card)
    sent = send_notice_email(to=recipients, subject=subject, html_body=body, text_body=text, tag=tag)
    return (
        SendResult("delivered")
        if sent
        else SendResult("retry", "the mail transport did not accept the message")
    )


# ---- delivery ---------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class DeliverySummary:
    delivered: int = 0
    failed: int = 0
    retrying: int = 0
    skipped: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _thread_parent(workflow, channel) -> str:
    from workflows.models import WorkflowDelivery

    first = (
        WorkflowDelivery.objects.filter(
            workflow=workflow, channel=channel, status=WorkflowDelivery.Status.DELIVERED
        )
        .exclude(external_ref="")
        .order_by("pk")
        .first()
    )
    return first.external_ref if first else ""


def _audit(record, status: str) -> None:
    from core.mutations import AuditEntry, emit_audit

    emit_audit(
        AuditEntry(
            actor_user_id=None,
            organization_id=record.organization_id,
            action="workflow.delivery",
            decision="ALLOW" if status == "delivered" else "DENY",
            target_kind="WorkflowDelivery",
            target_id=str(record.guid),
            duration_ms=0,
            permissions=(),
            extra={
                "workflow": str(record.workflow.guid),
                "workflow_run_id": record.workflow_run_id,
                "target": record.target_key,
                "purpose": record.purpose,
                "status": status,
                "attempt": record.attempts,
                "error": record.last_error,
            },
        )
    )


def _deliver_target(
    *, workflow, run, key, purpose, final_attempt, send, channel=None, user=None, outcome
) -> str:
    from workflows.models import WorkflowDelivery

    with transaction.atomic():
        record, _ = WorkflowDelivery.objects.select_for_update().get_or_create(
            workflow_run=run,
            target_key=key,
            defaults={
                "organization_id": workflow.organization_id,
                "workflow": workflow,
                "channel": channel,
                "recipient_user": user,
                "purpose": purpose,
                "run_outcome": outcome,
            },
        )
        if record.status in (WorkflowDelivery.Status.DELIVERED, WorkflowDelivery.Status.FAILED):
            return record.status
        record.attempts += 1
        record.save(update_fields=["attempts", "updated_at", "version"])

    try:
        result = send()
    except Exception:  # noqa: BLE001 -- a broken target is recorded, never raised into the run
        log.warning("workflow delivery target raised", extra={"target": key}, exc_info=True)
        result = SendResult("retry", "delivery raised an unexpected error")

    status = result.status
    if status == "retry":
        status = WorkflowDelivery.Status.FAILED if final_attempt else WorkflowDelivery.Status.RETRYING
    record.status = status
    record.last_error = result.error[:255]
    record.external_ref = result.external_ref
    record.delivered_at = timezone.now() if status == WorkflowDelivery.Status.DELIVERED else None
    record.save(
        update_fields=["status", "last_error", "external_ref", "delivered_at", "updated_at", "version"]
    )
    _audit(record, status)
    return status


def deliver_run(workflow_run_id: int, *, outcome: str = "", final_attempt: bool = False) -> DeliverySummary:
    """Post one settled scheduled run to its workflow's channels.

    ``outcome`` is what the schedule wrapper saw; the run row wins when it
    has settled. A failed run also emails the workflow's owner.
    """
    from astrolift_lifecycle.services.secrets import read_org_secret
    from astrolift_operations.models import WorkflowRun
    from workflows.models import DeliveryChannel, WorkflowDelivery, WorkflowInstance

    run = WorkflowRun.objects.filter(pk=workflow_run_id).first()
    if run is None or run.organization_id is None:
        return DeliverySummary(skipped="run not found")
    instance = (
        WorkflowInstance.objects.filter(
            organization_id=run.organization_id,
            temporal_workflow_id=run.workflow_id,
            configured_workflow__isnull=False,
        )
        .select_related("configured_workflow__created_by")
        .first()
    )
    workflow = instance.configured_workflow if instance is not None else None
    if workflow is None or workflow.organization_id != run.organization_id:
        return DeliverySummary(skipped="run has no configured workflow")
    if run.status not in (WorkflowRun.Status.RUNNING, ""):
        outcome = run.status
    outcome = outcome or "failed"
    card = build_card(workflow, run, instance, outcome=outcome)
    tag = "workflow.delivery"

    summary = DeliverySummary()

    def tally(status: str) -> None:
        if status == WorkflowDelivery.Status.DELIVERED:
            summary.delivered += 1
        elif status == WorkflowDelivery.Status.RETRYING:
            summary.retrying += 1
        else:
            summary.failed += 1

    channels = workflow.delivery_channels.filter(
        organization_id=workflow.organization_id, is_active=True, deleted_at__isnull=True
    ).order_by("pk")
    emailed: set[str] = set()
    for channel in channels:
        if channel.kind == DeliveryChannel.Kind.EMAIL:
            recipients = [str(r) for r in channel.email_recipients or []]
            emailed.update(r.lower() for r in recipients)

            def send(recipients=recipients):
                return _send_email(recipients, card, tag)

        else:

            def send(channel=channel):
                secret = read_org_secret(channel.organization_id, channel.secret_key)
                if not secret:
                    return SendResult("failed", "the channel's credential is missing")
                if channel.kind == DeliveryChannel.Kind.SLACK_APP:
                    return _send_slack_app(channel, card, secret, _thread_parent(workflow, channel))
                return _send_webhook(channel, card, secret)

        tally(
            _deliver_target(
                workflow=workflow,
                run=run,
                key=f"channel:{channel.guid}",
                purpose=WorkflowDelivery.Purpose.RESULT,
                final_attempt=final_attempt,
                send=send,
                channel=channel,
                outcome=outcome,
            )
        )

    owner = workflow.created_by
    if (
        not card.ok
        and owner is not None
        and owner.is_active
        and owner.email
        and owner.email.lower() not in emailed
    ):
        tally(
            _deliver_target(
                workflow=workflow,
                run=run,
                key=f"owner:{owner.pk}",
                purpose=WorkflowDelivery.Purpose.OWNER_NOTICE,
                final_attempt=final_attempt,
                send=lambda: _send_email([owner.email], card, tag),
                user=owner,
                outcome=outcome,
            )
        )
    return summary
