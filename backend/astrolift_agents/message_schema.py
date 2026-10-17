"""GraphQL for agent messages (#2434): the inbox every client reads.

Every resolver reads the caller's own recipient rows, in the active org, on
messages from agents the caller can still read (agent.read, through the
current API token's ceiling). Gated on agent.read held anywhere in the org;
the rows narrow from there.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncGenerator

import strawberry
from django.db.models import Count, Max, Q
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType, clamp_limit, decode_cursor, encode_cursor, keyset_page
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.schema.ws_auth import ws_identity
from core.tenancy import get_current_tenant


@strawberry.type(name="AstroliftAgentMessageOption")
class AgentMessageOptionType:
    id: str
    label: str
    description: str | None = None


@strawberry.type(name="AstroliftAgentMessageAttachment")
class AgentMessageAttachmentType:
    kind: str
    ref: str
    label: str | None = None


@strawberry.type(name="AstroliftAgentMessageReply")
class AgentMessageReplyType:
    id: GUID
    message_id: GUID
    author_label: str
    body: str
    choices: list[str]
    decision: str | None
    route: str
    delivered_at: dt.datetime | None
    wake_task_id: GUID | None
    created_at: dt.datetime


@strawberry.type(name="AstroliftAgentMessage")
class AgentMessageType:
    id: GUID
    thread_id: GUID
    kind: str
    source: str
    title: str
    body: str
    options: list[AgentMessageOptionType]
    attachments: list[AgentMessageAttachmentType]
    agent_slug: str | None
    agent_name: str | None
    task_id: GUID | None
    task_status: str | None
    reply_to_id: GUID | None
    request_sequence: int | None
    expires_at: dt.datetime | None
    created_at: dt.datetime
    delivered_at: dt.datetime | None
    read_at: dt.datetime | None
    replies: list[AgentMessageReplyType]
    answerable: bool = strawberry.field(
        description="A question or approval this person may still answer: not expired and not answered by them."
    )


@strawberry.type(name="AstroliftAgentMessagePage")
class AgentMessagePageType:
    items: list[AgentMessageType]
    next_cursor: str | None
    total_count: int | None
    unread_count: int


@strawberry.type(name="AstroliftAgentMessageThread")
class AgentMessageThreadType:
    thread_id: GUID
    agent_slug: str | None
    agent_name: str | None
    task_id: GUID | None
    task_status: str | None
    last_message: AgentMessageType
    last_message_at: dt.datetime
    message_count: int
    unread_count: int


@strawberry.type(name="AstroliftAgentMessageThreadPage")
class AgentMessageThreadPageType:
    items: list[AgentMessageThreadType]
    next_cursor: str | None
    unread_count: int


@strawberry.type(name="AstroliftAgentMessagesMarked")
class AgentMessagesMarkedType:
    marked: int


@strawberry.input(name="AstroliftReplyToAgentMessageInput")
class ReplyToAgentMessageInput:
    message_id: GUID
    body: str | None = None
    choices: list[str] | None = None
    decision: str | None = strawberry.field(default=None, description="allow or deny, for an approval.")
    client_request_id: str | None = None


def _reply_to_type(row) -> AgentMessageReplyType:
    delivered = row.delivered_at or (row.input_message.delivered_at if row.input_message_id else None)
    return AgentMessageReplyType(
        id=GUID(str(row.guid)),
        message_id=GUID(str(row.message.guid)),
        author_label=row.author_label,
        body=row.body,
        choices=list(row.choices or []),
        decision=row.decision or None,
        route=row.route,
        delivered_at=delivered,
        wake_task_id=GUID(str(row.wake_task.guid)) if row.wake_task_id else None,
        created_at=row.created_at,
    )


def _message_to_type(recipient, *, now=None) -> AgentMessageType:
    from astrolift_agents.models import AgentMessage

    message = recipient.message
    now = now or dt.datetime.now(dt.UTC)
    replies = sorted(message.replies.all(), key=lambda row: (row.created_at, row.pk))
    expired = message.expires_at is not None and message.expires_at <= now
    answered = any(row.author_id == recipient.user_id for row in replies)
    task = message.agent_task
    agent = message.agent_definition
    return AgentMessageType(
        id=GUID(str(message.guid)),
        thread_id=GUID(str(message.thread_id)),
        kind=message.kind,
        source=message.source,
        title=message.title,
        body=message.body,
        options=[
            AgentMessageOptionType(id=o["id"], label=o["label"], description=o.get("description") or None)
            for o in message.options or []
        ],
        attachments=[
            AgentMessageAttachmentType(kind=a["kind"], ref=a["ref"], label=a.get("label") or None)
            for a in message.attachments or []
        ],
        agent_slug=agent.slug if agent is not None else None,
        agent_name=agent.name if agent is not None else None,
        task_id=GUID(str(task.guid)) if task is not None else None,
        task_status=task.status if task is not None else None,
        reply_to_id=GUID(str(message.reply_to.guid)) if message.reply_to_id else None,
        request_sequence=message.request_sequence,
        expires_at=message.expires_at,
        created_at=message.created_at,
        delivered_at=recipient.delivered_at,
        read_at=recipient.read_at,
        replies=[_reply_to_type(row) for row in replies],
        answerable=message.kind in {AgentMessage.Kind.QUESTION, AgentMessage.Kind.APPROVAL}
        and not expired
        and not answered,
    )


def _with_message(qs):
    return qs.select_related(
        "message",
        "message__agent_definition",
        "message__agent_task",
        "message__reply_to",
    ).prefetch_related("message__replies__input_message", "message__replies__wake_task")


def _viewer():
    from django.contrib.auth import get_user_model

    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None or tenant.actor_user_id is None:
        return None, None
    user = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
    return user, tenant.organization_id


def _guid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def message_types(rows) -> list[AgentMessageType]:
    from astrolift_agents.services.agent_messages import mark_delivered

    rows = list(rows)
    mark_delivered(rows)
    now = dt.datetime.now(dt.UTC)
    return [_message_to_type(row, now=now) for row in rows]


@strawberry.type
class AgentMessagesQuery:
    @strawberry.field
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_agent_messages(
        self,
        info: Info,
        thread_id: GUID | None = None,
        unread_only: bool = False,
        after: str | None = None,
        first: int = 50,
    ) -> AgentMessagePageType:
        """The caller's agent messages, newest first; one thread with ``threadId``."""
        from astrolift_agents.services.agent_messages import visible_recipient_rows

        user, org_id = _viewer()
        rows = visible_recipient_rows(user, org_id)
        unread = rows.filter(read_at__isnull=True).count()
        if thread_id is not None:
            guid = _guid(thread_id)
            rows = rows.filter(message__thread_id=guid) if guid else rows.none()
        if unread_only:
            rows = rows.filter(read_at__isnull=True)
        page = keyset_page(_with_message(rows), cursor=after, limit=first)
        return AgentMessagePageType(
            items=message_types(page.rows),
            next_cursor=page.next_cursor,
            total_count=page.total_count,
            unread_count=unread,
        )

    @strawberry.field
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_agent_message_threads(
        self,
        info: Info,
        unread_only: bool = False,
        after: str | None = None,
        first: int = 25,
    ) -> AgentMessageThreadPageType:
        """The caller's threads, one per agent run (or a thread the agent named), latest activity first."""
        from astrolift_agents.services.agent_messages import visible_recipient_rows

        user, org_id = _viewer()
        rows = visible_recipient_rows(user, org_id)
        unread_total = rows.filter(read_at__isnull=True).count()
        threads = rows.values("message__thread_id").annotate(
            last=Max("created_at"),
            count=Count("pk"),
            unread=Count("pk", filter=Q(read_at__isnull=True)),
        )
        if unread_only:
            threads = threads.filter(unread__gt=0)
        decoded = decode_cursor(after) if after else None
        if decoded is not None:
            last, thread = decoded
            threads = threads.filter(Q(last__lt=last) | Q(last=last, message__thread_id__lt=thread))
        limit = clamp_limit(first, default=25, maximum=100)
        picked = list(threads.order_by("-last", "-message__thread_id")[: limit + 1])
        more = len(picked) > limit
        picked = picked[:limit]
        latest = {}
        for row in _with_message(
            rows.filter(message__thread_id__in=[item["message__thread_id"] for item in picked])
        ).order_by("message__thread_id", "-created_at", "-pk"):
            latest.setdefault(row.message.thread_id, row)
        now = dt.datetime.now(dt.UTC)
        items = []
        for item in picked:
            head = latest[item["message__thread_id"]]
            message = head.message
            items.append(
                AgentMessageThreadType(
                    thread_id=GUID(str(message.thread_id)),
                    agent_slug=message.agent_definition.slug if message.agent_definition_id else None,
                    agent_name=message.agent_definition.name if message.agent_definition_id else None,
                    task_id=GUID(str(message.agent_task.guid)) if message.agent_task_id else None,
                    task_status=message.agent_task.status if message.agent_task_id else None,
                    last_message=_message_to_type(head, now=now),
                    last_message_at=item["last"],
                    message_count=item["count"],
                    unread_count=item["unread"],
                )
            )
        next_cursor = encode_cursor(picked[-1]["last"], picked[-1]["message__thread_id"]) if more else None
        return AgentMessageThreadPageType(items=items, next_cursor=next_cursor, unread_count=unread_total)


@strawberry.type
class AgentMessagesMutation:
    @strawberry.field
    @mutation_audit(action="agent.message.read")
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    def mark_agent_messages_read(
        self, info: Info, ids: list[GUID] | None = None, thread_id: GUID | None = None
    ) -> MutationResultType[AgentMessagesMarkedType]:
        """Mark the caller's messages read: these ``ids``, one thread, or (neither) all of them."""
        from astrolift_agents.services.agent_messages import mark_read

        user, org_id = _viewer()
        if user is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        message_ids = None
        if ids is not None:
            message_ids = [guid for guid in (_guid(value) for value in ids) if guid is not None]
        thread = _guid(thread_id) if thread_id is not None else None
        if thread_id is not None and thread is None:
            return gql_failure(ErrorCode.VALIDATION.value, "threadId must be an id", field="threadId")
        marked = mark_read(user, org_id, message_ids=message_ids, thread_id=thread)
        return gql_success(AgentMessagesMarkedType(marked=marked))

    @strawberry.field
    @mutation_audit(
        action="agent.message.reply",
        target=lambda self, info, input: ("AgentMessage", str(input.message_id)),
    )
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    def reply_to_agent_message(
        self, info: Info, input: ReplyToAgentMessageInput
    ) -> MutationResultType[AgentMessageReplyType]:
        """Answer a message; the answer goes back to the agent.

        To a running run it arrives on the run's input channel; a message that
        carries the run's own request (an approval, a question) answers that
        gate, approvals through the run's action-safety policy and only by a
        person with agent_task.send_input on the run. When the run has ended,
        the answer waits for the agent's next run and, if the caller may run
        the agent, starts one with it.
        """
        from astrolift_agents.services.agent_messages import AgentMessageError, reply, visible_recipient_rows

        user, org_id = _viewer()
        if user is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        guid = _guid(input.message_id)
        recipient = (
            visible_recipient_rows(user, org_id).filter(message__guid=guid).select_related("message").first()
            if guid
            else None
        )
        if recipient is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "message not found", field="messageId")
        try:
            row = reply(
                recipient.message,
                user,
                body=input.body or "",
                choices=input.choices or [],
                decision=input.decision or "",
                client_request_id=input.client_request_id,
            )
        except AgentMessageError as exc:
            code = {
                "validation": ErrorCode.VALIDATION.value,
                "not_found": ErrorCode.NOT_FOUND.value,
                "refused": ErrorCode.PERMISSION_DENIED.value,
            }.get(exc.code, ErrorCode.PRECONDITION.value)
            return gql_failure(code, exc.message, field=exc.field or None)
        return gql_success(_reply_to_type(row))


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------

#: How often an open subscription looks for new messages. Polling the
#: recipient table, not the in-process broker, so a message sent by any
#: worker reaches a client connected to any other.
POLL_SECONDS = 2.0


def _new_for_identity(tenant, token, attributes, after_pk):
    """New recipient rows past ``after_pk`` for the pinned identity, rechecked
    each poll: a person or token that lost the org or the agent gets nothing."""
    import dataclasses

    from django.contrib.auth import get_user_model
    from django.db.models import Q as _Q
    from django.utils import timezone

    from astrolift_agents.services.agent_messages import visible_recipient_rows
    from astrolift_identity.abac import request_attributes
    from astrolift_identity.api_tokens import (
        reset_current_api_token,
        session_may_act_in,
        set_current_api_token,
        with_active_org_member,
    )
    from astrolift_identity.models import ApiToken
    from core.tenancy import tenant_context

    user = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
    if user is None or not session_may_act_in(user, tenant.organization_id):
        return None, after_pk
    if token is not None:
        token = with_active_org_member(
            ApiToken.objects.filter(
                _Q(expires_at__isnull=True) | _Q(expires_at__gt=timezone.now()),
                pk=token.pk,
                organization_id=tenant.organization_id,
                user_id=tenant.actor_user_id,
                is_revoked=False,
            ),
            user="user",
            organization="organization",
        ).first()
        if token is None:
            return None, after_pk
    marker = set_current_api_token(token)
    try:
        with tenant_context(tenant), request_attributes(dataclasses.replace(attributes, cache={})):
            rows = visible_recipient_rows(user, tenant.organization_id)
            if after_pk is None:
                return [], rows.order_by("-pk").values_list("pk", flat=True).first() or 0
            fresh = list(_with_message(rows.filter(pk__gt=after_pk)).order_by("pk")[:20])
            watermark = fresh[-1].pk if fresh else after_pk
            return message_types(fresh), watermark
    finally:
        reset_current_api_token(marker)


@strawberry.type
class AgentMessagesSubscription:
    @strawberry.subscription
    @ws_identity
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    async def astrolift_agent_message_received(self, info: Info) -> AsyncGenerator[AgentMessageType, None]:
        """Each new message for the caller, once per connection, from the moment
        it subscribes; reconcile earlier ones with ``astroliftAgentMessages``.
        Ends when the caller loses the organization or the token is revoked."""
        import asyncio

        from asgiref.sync import sync_to_async

        from astrolift_identity.abac import RequestAttributes, current_attributes
        from astrolift_identity.api_tokens import get_current_api_token
        from config.features import Feature, is_enabled

        tenant = get_current_tenant()
        if not is_enabled(Feature.AGENTS) or tenant is None or tenant.organization_id is None:
            return
        if tenant.actor_user_id is None:
            return
        token = get_current_api_token()
        if token is not None and token.organization_id != tenant.organization_id:
            return
        attributes = current_attributes() or RequestAttributes(actor_user_id=tenant.actor_user_id)
        poll = sync_to_async(_new_for_identity)
        _, watermark = await poll(tenant, token, attributes, None)
        while True:
            items, watermark = await poll(tenant, token, attributes, watermark)
            if items is None:
                return
            for item in items:
                yield item
            await asyncio.sleep(POLL_SECONDS)
