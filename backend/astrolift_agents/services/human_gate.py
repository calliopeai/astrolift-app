"""
Human-gate notification service (#59).

When a WorkflowStageExecution reaches RUNNING for a stage of kind
``human_gate``, reviewers must be notified so they can act. This module
handles:

  1. Assignee resolution (stage → definition → org admin fallback).
  2. Email notification via Django's configured mail backend (SES or SMTP).
  3. Slack webhook notification (org-level or per-workflow override).
  4. Event emission via core.events so the in-app activity feed + outbound
     webhooks also fire.

The public entry point is ``notify_human_gate(workflow_run, stage)``. It is
called as a Temporal activity (durable, retryable) on the stage status
transition, so all I/O here should be idempotent — the caller may retry on
transient failure.

Notification delivery is best-effort per channel: a failed Slack post does
not prevent the email from sending, and vice versa. Both failures are logged
and surfaced in the WorkflowStageExecution.result JSON so operators can see
what happened without polling external systems.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
import requests

from core.events import Event

if TYPE_CHECKING:
    from workflows.models import WorkflowInstance

log = logging.getLogger(__name__)

# Default reminder interval when not configured on the workflow definition.
DEFAULT_REMINDER_HOURS = 24


def notify_human_gate(workflow_run: "WorkflowInstance", stage: dict) -> dict[str, bool]:
    """Notify reviewers that a human_gate stage needs their attention.

    *workflow_run* is a ``workflows.WorkflowInstance``. *stage* is the
    stage definition dict from ``WorkflowDefinition.states`` (as stored in
    the JSON column) augmented with a ``notification_config`` key from the
    definition-level ``NotificationConfig`` if present.

    Returns a dict reporting which channels succeeded:
        {"email": True, "slack": False}

    Never raises — failures are logged and returned.
    """
    notification_config: dict = stage.get("notification_config") or {}
    channels: list[str] = notification_config.get("channels", ["email"])

    recipients = _resolve_recipients(workflow_run, stage)
    if not recipients:
        log.warning(
            "human_gate stage %r on workflow_run %s has no resolvable recipients",
            stage.get("name"),
            workflow_run.pk,
        )

    results: dict[str, bool] = {}

    if "email" in channels:
        ok = True
        for recipient_email in recipients:
            ok = send_gate_notification_email(recipient_email, workflow_run, stage) and ok
        results["email"] = ok

    if "slack" in channels:
        webhook_url = notification_config.get("slack_webhook_url") or getattr(
            settings, "ASTROLIFT_SLACK_WEBHOOK_URL", ""
        )
        if webhook_url:
            results["slack"] = _send_slack_notification(webhook_url, workflow_run, stage)
        else:
            log.warning(
                "slack channel configured but no webhook URL for workflow_run %s",
                workflow_run.pk,
            )
            results["slack"] = False

    # Emit platform event so webhooks / activity feed pick it up.
    try:
        _emit_gate_event(workflow_run, stage, results)
    except Exception:
        log.exception("failed to emit human_gate event for workflow_run %s", workflow_run.pk)

    return results


def send_gate_notification_email(
    recipient_email: str,
    workflow_run: "WorkflowInstance",
    stage: dict,
) -> bool:
    """Send a human-gate review request email to *recipient_email*.

    Returns True on success, False on any failure. Never raises.
    """
    try:
        workflow_def = workflow_run.workflow
        workflow_name = getattr(workflow_def, "name", str(workflow_def.pk))
        stage_name = stage.get("label") or stage.get("name", "human_gate")

        subject = f"[Astrolift] Review needed: {workflow_name} — {stage_name}"

        review_url = _build_review_url(workflow_run)
        triggered_by = _resolve_triggered_by(workflow_run)

        text_body = (
            f"A workflow is waiting for your review.\n\n"
            f"Workflow: {workflow_name}\n"
            f"Stage:    {stage_name}\n"
            f"Triggered by: {triggered_by}\n"
            f"Review URL: {review_url}\n\n"
            f"Please review and approve or reject the gate to let the workflow continue."
        )

        html_body = (
            f"<p>A workflow is waiting for your review.</p>"
            f"<table>"
            f"<tr><td><b>Workflow</b></td><td>{workflow_name}</td></tr>"
            f"<tr><td><b>Stage</b></td><td>{stage_name}</td></tr>"
            f"<tr><td><b>Triggered by</b></td><td>{triggered_by}</td></tr>"
            f"</table>"
            f"<p><a href='{review_url}'>Open review</a></p>"
        )

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@astrolift.dev"),
            to=[recipient_email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return True

    except Exception:
        log.exception(
            "failed to send human_gate email to %s for workflow_run %s",
            recipient_email,
            workflow_run.pk,
        )
        return False


# ── Internal helpers ──────────────────────────────────────────────────────────


def _resolve_recipients(workflow_run: "WorkflowInstance", stage: dict) -> list[str]:
    """Return the list of email addresses to notify.

    Resolution order (matches spec #59):
    1. Stage-level ``assignee_email`` field.
    2. WorkflowDefinition-level ``default_assignee_email``.
    3. Org admin fallback — first superuser email in the platform.
    """
    emails: list[str] = []

    # 1. Stage-level assignee.
    stage_email = stage.get("assignee_email")
    if stage_email:
        emails.append(stage_email)
        return emails

    # 2. Definition-level default assignee.
    workflow_def = workflow_run.workflow
    def_email = getattr(workflow_def, "default_assignee_email", None)
    if def_email:
        emails.append(def_email)
        return emails

    # 3. Org admin fallback.
    try:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if admin and admin.email:
            emails.append(admin.email)
    except Exception:
        log.exception("failed to resolve org admin fallback recipient")

    return emails


def _build_review_url(workflow_run: "WorkflowInstance") -> str:
    """Best-effort deep link to the workflow instance review UI."""
    try:
        return (
            getattr(settings, "ASTROLIFT_BASE_URL", "").rstrip("/")
            + f"/app/workflows/instances/{workflow_run.pk}/"
        )
    except Exception:
        return ""


def _resolve_triggered_by(workflow_run: "WorkflowInstance") -> str:
    try:
        user = getattr(workflow_run, "created_by", None)
        if user is None:
            return "system"
        name = (user.get_full_name() or "").strip() if hasattr(user, "get_full_name") else ""
        return name or getattr(user, "email", None) or "system"
    except Exception:
        return "system"


def _send_slack_notification(
    webhook_url: str,
    workflow_run: "WorkflowInstance",
    stage: dict,
) -> bool:
    """Post a Block Kit message to the configured Slack webhook."""
    try:
        workflow_name = getattr(workflow_run.workflow, "name", str(workflow_run.pk))
        stage_name = stage.get("label") or stage.get("name", "human_gate")
        review_url = _build_review_url(workflow_run)
        triggered_by = _resolve_triggered_by(workflow_run)

        payload = {
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":pause_button: *Review needed*: `{workflow_name}` — `{stage_name}`\n"
                            f"Triggered by: {triggered_by}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Review"},
                            "url": review_url,
                            "style": "primary",
                        },
                    ],
                },
            ]
        }
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception:
        log.exception(
            "failed to post Slack notification for workflow_run %s",
            workflow_run.pk,
        )
        return False


def _emit_gate_event(
    workflow_run: "WorkflowInstance",
    stage: dict,
    delivery_results: dict[str, bool],
) -> None:
    """Emit a ``workflow.human_gate.notified`` platform event."""
    Event.emit(
        "workflow.human_gate.notified",
        payload={
            "workflow_run_id": workflow_run.pk,
            "stage_name": stage.get("name"),
            "delivery": delivery_results,
        },
    )
