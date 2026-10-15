"""GraphQL types for workflow result delivery channels (#2244).

A channel's credential (incoming webhook URL, Slack bot token) is write-only:
no type here carries it. ``has_credential`` says whether one is stored.
"""

from __future__ import annotations

import strawberry

from core.schema.common import MutationResult


@strawberry.type
class WorkflowDeliveryChannelType:
    guid: str
    name: str
    kind: str = strawberry.field(description="slack_webhook, slack_app, teams_webhook or email.")
    slack_channel: str
    email_recipients: list[str]
    is_active: bool
    has_credential: bool


@strawberry.type
class WorkflowDeliveryType:
    """One audit record: a run's result delivered (or not) to one target."""

    guid: str
    channel_guid: str | None
    channel_name: str | None
    purpose: str = strawberry.field(description="result, or owner_notice for a failed run's owner email.")
    run_outcome: str
    status: str = strawberry.field(description="pending, retrying, delivered or failed.")
    attempts: int
    last_error: str
    created_at: str
    delivered_at: str | None


@strawberry.type
class WorkflowDeliverySelectionType:
    workflow_guid: str
    channel_guids: list[str]
    recent_deliveries: list[WorkflowDeliveryType]


@strawberry.type
class WorkflowDeliveryChannelResult(MutationResult):
    channel: WorkflowDeliveryChannelType | None = None


@strawberry.type
class WorkflowDeliverySelectionResult(MutationResult):
    selection: WorkflowDeliverySelectionType | None = None


def channel_to_type(channel) -> WorkflowDeliveryChannelType:
    from workflows.delivery import channel_has_secret
    from workflows.models import DeliveryChannel

    return WorkflowDeliveryChannelType(
        guid=str(channel.guid),
        name=channel.name,
        kind=channel.kind,
        slack_channel=channel.slack_channel,
        email_recipients=[str(r) for r in channel.email_recipients or []],
        is_active=channel.is_active,
        has_credential=channel.kind == DeliveryChannel.Kind.EMAIL or channel_has_secret(channel),
    )


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def selection_to_type(workflow) -> WorkflowDeliverySelectionType:
    recent = workflow.deliveries.select_related("channel").order_by("-created_at", "-pk")[:10]
    return WorkflowDeliverySelectionType(
        workflow_guid=str(workflow.guid),
        channel_guids=[
            str(c.guid) for c in workflow.delivery_channels.filter(deleted_at__isnull=True).order_by("name")
        ],
        recent_deliveries=[
            WorkflowDeliveryType(
                guid=str(d.guid),
                channel_guid=str(d.channel.guid) if d.channel_id else None,
                channel_name=d.channel.name if d.channel_id else None,
                purpose=d.purpose,
                run_outcome=d.run_outcome,
                status=d.status,
                attempts=d.attempts,
                last_error=d.last_error,
                created_at=_iso(d.created_at) or "",
                delivered_at=_iso(d.delivered_at),
            )
            for d in recent
        ],
    )
