"""Agent messages: send, read, reply, deliver back to the agent (#2434).

Below GraphQL, MCP and the run's REST endpoint, so every path shares one set
of rules:

* **Audience.** An agent addresses only people who can read it (agent.read on
  its app, judged as that person, alone). Anyone else named is refused with
  the whole send, never silently dropped. A team reaches its members who can
  read the agent; a team none of whose members can is refused.
* **Redaction.** The run's own secret references and recognisable
  credentials are replaced before anything is stored.
* **Rate.** At most ``AGENT_MESSAGE_RATE_PER_MINUTE`` messages per agent.
* **Replies** go back the way the agent can take them: the run's gate when
  the message carries one of its requests, the run's input channel while it
  runs, otherwise a queued answer the agent's next run collects, started
  right away when the person may dispatch the agent.
* **Reads** are filtered on the reader's current access to the agent, so a
  person who loses the agent stops receiving and cannot reply.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from astrolift_agents.models import (
    AgentMessage,
    AgentMessageRecipient,
    AgentMessageReply,
    AgentTask,
    AgentTaskEvent,
)
from astrolift_agents.models.agent_message import MAX_BODY_CHARS, MAX_REPLY_CHARS, MAX_TITLE_CHARS
from core.mutations import AuditEntry, emit_audit
from core.permissions import Permission

log = logging.getLogger(__name__)

MAX_OPTIONS = 8
MAX_ATTACHMENTS = 10
MAX_ADDRESSEES = 50
MAX_EXPIRY = dt.timedelta(days=30)
_OPTION_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}\Z")
_REDACTED = "[redacted]"
# Credentials an agent might paste by mistake. Values only the run holds
# (its secret values) never reach the control plane, so the shapes and the
# run's own secret references are what the send path can recognise.
_CREDENTIALS = re.compile(
    r"alft_[a-z]{2,4}_[A-Za-z0-9_\-]{8,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|sk-[A-Za-z0-9_\-]{20,}"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)


@dataclass
class AgentMessageError(ValueError):
    code: str  # validation | refused | not_found | precondition | rate_limited
    message: str
    field: str = ""
    status: int = 400

    def __str__(self) -> str:
        return self.message


def rate_per_minute() -> int:
    return int(getattr(settings, "AGENT_MESSAGE_RATE_PER_MINUTE", 30))


# ---------------------------------------------------------------------------
# Access, judged as one person
# ---------------------------------------------------------------------------


def _as_person(user_id: int, organization_id: int):
    """Context in which visibility helpers answer for ``user_id`` alone: no
    API token and no request attributes of whoever is asking."""
    import contextlib

    from astrolift_identity.abac import RequestAttributes, request_attributes
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from core.tenancy import TenantContext, tenant_context

    @contextlib.contextmanager
    def scope():
        marker = set_current_api_token(None)
        try:
            with (
                tenant_context(TenantContext(organization_id=organization_id, actor_user_id=user_id)),
                request_attributes(RequestAttributes(actor_user_id=user_id)),
            ):
                yield
        finally:
            reset_current_api_token(marker)

    return scope()


def can_read_agent(user, organization_id: int, workload_id: int | None) -> bool:
    """Whether ``user`` may read the agent: an active member of the org with
    agent.read on its app, by the same rule as the agent list."""
    from astrolift_agents.visibility import agent_workloads
    from astrolift_identity.api_tokens import session_may_act_in

    if workload_id is None or user is None or not user.is_active:
        return False
    if not session_may_act_in(user, organization_id):
        return False
    with _as_person(user.pk, organization_id):
        return agent_workloads(organization_id, Permission.AGENT_READ).filter(pk=workload_id).exists()


def _may_dispatch(user, organization_id: int, workload) -> bool:
    from astrolift_agents.scopes import agent_workload_app_scope
    from astrolift_identity.abac import operation_attributes
    from astrolift_identity.operation_context import agent_region_operation
    from core.permissions import PermissionDenied, check_permission

    args = {"agent_slug": workload.slug}
    try:
        operation = agent_region_operation(args)[0]
        with operation_attributes(**(operation.attributes() if operation is not None else {})):
            scope = agent_workload_app_scope("agent_slug", Permission.AGENT_DISPATCH)(args)
            check_permission(Permission.AGENT_DISPATCH, scope=scope)
    except PermissionDenied:
        return False
    return True


def visible_recipient_rows(user, organization_id: int):
    """The caller's recipient rows on messages from agents they can still
    read, in the current tenant and API token."""
    from astrolift_agents.visibility import agent_workloads

    if user is None or organization_id is None:
        return AgentMessageRecipient.objects.none()
    return AgentMessageRecipient.objects.filter(
        organization_id=organization_id,
        user_id=user.pk,
        message__organization_id=organization_id,
        message__deleted_at__isnull=True,
        message__agent_definition__in=agent_workloads(organization_id, Permission.AGENT_READ).values("pk"),
    )


# ---------------------------------------------------------------------------
# Redaction and validation
# ---------------------------------------------------------------------------


def _secret_refs(task: AgentTask | None) -> list[str]:
    spec = getattr(task, "environment_spec", None) if task is not None else None
    if spec is None:
        return []
    refs = [str(row.get("uri") or "") for row in spec.secret_refs or [] if isinstance(row, dict)]
    refs += list(
        spec.secret_binding_overrides.filter(deleted_at__isnull=True)
        .exclude(uri="")
        .values_list("uri", flat=True)
    )
    return sorted({ref for ref in refs if len(ref) >= 6}, key=len, reverse=True)


def redact(text: str, task: AgentTask | None = None) -> str:
    """``text`` without the run's secret references or recognisable credentials."""
    from core.logging import redact_url_secrets

    if not text:
        return text
    for ref in _secret_refs(task):
        text = text.replace(ref, _REDACTED)
    return redact_url_secrets(_CREDENTIALS.sub(_REDACTED, text))


def _text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or "\x00" in value:
        raise AgentMessageError("validation", f"{field} must be a string", field)
    value = value.strip()
    if required and not value:
        raise AgentMessageError("validation", f"{field} is required", field)
    if len(value) > maximum:
        raise AgentMessageError("validation", f"{field} exceeds {maximum} characters", field)
    return value


def _options(raw: Any, task: AgentTask | None) -> list[dict[str, str]]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_OPTIONS:
        raise AgentMessageError("validation", f"options must be a list of at most {MAX_OPTIONS}", "options")
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or not {"id", "label"} <= set(item) <= {"id", "label", "description"}:
            raise AgentMessageError("validation", "each option needs an id and a label", "options")
        key = item["id"]
        if not isinstance(key, str) or not _OPTION_ID.fullmatch(key) or key in seen:
            raise AgentMessageError("validation", "option ids must be valid and unique", "options")
        if redact(key, task) != key:
            raise AgentMessageError("validation", "option ids must not contain credentials", "options")
        seen.add(key)
        out.append(
            {
                "id": key,
                "label": redact(_text(item["label"], "options", 200, required=True), task),
                "description": redact(_text(item.get("description"), "options", 1000), task),
            }
        )
    return out


def _install_hosts() -> set[str]:
    hosts = set()
    for name in ("FRONTEND_URL", "PLATFORM_API_URL", "APP_BASE_URL"):
        host = urlparse(str(getattr(settings, name, "") or "")).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def _attachments(raw: Any, task: AgentTask | None) -> list[dict[str, str]]:
    """References only: an artifact id, a URL on this install, or a semantic-layer citation."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ATTACHMENTS:
        raise AgentMessageError(
            "validation", f"attachments must be a list of at most {MAX_ATTACHMENTS}", "attachments"
        )
    out = []
    for item in raw:
        if not isinstance(item, dict) or not {"kind", "ref"} <= set(item) <= {"kind", "ref", "label"}:
            raise AgentMessageError("validation", "each attachment needs a kind and a ref", "attachments")
        kind = item["kind"]
        if kind not in {"artifact", "url", "citation"}:
            raise AgentMessageError(
                "validation", "attachment kind must be artifact, url or citation", "attachments"
            )
        ref = _text(item["ref"], "attachments", 1000, required=True)
        if kind == "url":
            parsed = urlparse(ref)
            on_install = (not parsed.scheme and not parsed.netloc and ref.startswith("/")) or (
                parsed.scheme == "https" and (parsed.hostname or "").lower() in _install_hosts()
            )
            if not on_install:
                raise AgentMessageError(
                    "validation", "a url attachment must point at this install", "attachments"
                )
        out.append(
            {
                "kind": kind,
                "ref": redact(ref, task),
                "label": redact(_text(item.get("label"), "attachments", 200), task),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


def _resolve_audience(
    organization_id: int, workload, *, users: Any, teams: Any, default_user_id: int | None
) -> list[tuple[Any, Any]]:
    """``[(user, team or None)]`` the message goes to. Raises ``refused``
    naming every addressee outside the agent's audience; a name that is not a
    person in the org reads the same as one who cannot see the agent."""
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import Member, Team

    users = users or []
    teams = teams or []
    if not isinstance(users, list) or not all(isinstance(item, str) for item in users):
        raise AgentMessageError("validation", "to must be a list of email addresses", "to")
    if not isinstance(teams, list) or not all(isinstance(item, str) for item in teams):
        raise AgentMessageError("validation", "teams must be a list of team slugs", "teams")
    if len(users) + len(teams) > MAX_ADDRESSEES:
        raise AgentMessageError("validation", f"address at most {MAX_ADDRESSEES} people and teams", "to")

    user_model = get_user_model()
    resolved: dict[int, tuple[Any, Any]] = {}
    refused: list[str] = []
    workload_id = workload.pk if workload is not None else None

    for address in dict.fromkeys(item.strip().lower() for item in users if item.strip()):
        person = user_model.objects.filter(email__iexact=address, is_active=True).order_by("pk").first()
        if person is None or not can_read_agent(person, organization_id, workload_id):
            refused.append(address)
            continue
        resolved.setdefault(person.pk, (person, None))

    for slug in dict.fromkeys(item.strip() for item in teams if item.strip()):
        team = Team.objects.filter(
            organization_id=organization_id, slug=slug, deleted_at__isnull=True
        ).first()
        reached = []
        if team is not None:
            members = Member.objects.filter(
                scope_kind=Member.ScopeKind.TEAM,
                scope_id=team.pk,
                is_active=True,
                deleted_at__isnull=True,
                user__is_active=True,
            ).select_related("user")
            reached = [m.user for m in members if can_read_agent(m.user, organization_id, workload_id)]
        if not reached:
            refused.append(f"team:{slug}")
            continue
        for person in reached:
            resolved.setdefault(person.pk, (person, team))

    if refused:
        raise AgentMessageError(
            "refused",
            "not in this agent's audience (people who can read the agent): " + ", ".join(refused),
            "to",
            status=403,
        )
    if not users and not teams and default_user_id is not None:
        person = user_model.objects.filter(pk=default_user_id, is_active=True).first()
        if person is not None and can_read_agent(person, organization_id, workload_id):
            resolved[person.pk] = (person, None)
    return list(resolved.values())


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _audit(
    action: str, message: AgentMessage, *, actor_user_id: int | None, decision="ALLOW", **extra
) -> None:
    emit_audit(
        AuditEntry(
            actor_user_id=actor_user_id,
            organization_id=message.organization_id,
            action=action,
            decision=decision,
            target_kind="AgentMessage",
            target_id=str(message.guid),
            duration_ms=0,
            permissions=(),
            extra={
                "agent_task": str(message.agent_task.guid) if message.agent_task_id else "",
                "agent": message.agent_definition.slug if message.agent_definition_id else "",
                "kind": message.kind,
                "source": message.source,
                **extra,
            },
        )
    )


def _create(
    task: AgentTask,
    *,
    kind: str,
    source: str,
    title: str,
    body: str,
    options: list,
    attachments: list,
    thread_id,
    reply_to: AgentMessage | None,
    request_sequence: int | None,
    expires_at,
    audience: list[tuple[Any, Any]],
    signal_key: str = "",
    client_request_id=None,
) -> AgentMessage:
    message = AgentMessage.objects.create(
        organization_id=task.organization_id,
        agent_definition_id=task.agent_definition_id,
        agent_task=task,
        thread_id=thread_id or task.guid,
        reply_to=reply_to,
        kind=kind,
        source=source,
        title=title,
        body=body,
        options=options,
        attachments=attachments,
        request_sequence=request_sequence,
        expires_at=expires_at,
        signal_key=signal_key,
        client_request_id=client_request_id,
    )
    AgentMessageRecipient.objects.bulk_create(
        [
            AgentMessageRecipient(
                organization_id=task.organization_id, message=message, user=person, team=team
            )
            for person, team in audience
        ]
    )
    transaction.on_commit(lambda: _fan_out(message.pk))
    return message


@transaction.atomic
def send_agent_message(task: AgentTask, payload: Any) -> tuple[AgentMessage, bool]:
    """A run's own message, authorised by the run's identity (its callback key).

    ``payload``: ``kind``, ``body``, optional ``title``, ``to`` (emails),
    ``teams`` (slugs), ``options``, ``attachments``, ``thread_id``,
    ``reply_to`` (a message id), ``request_sequence``, ``expires_in_seconds``,
    ``client_request_id``. Returns ``(message, created)``.
    """
    if not isinstance(payload, dict):
        raise AgentMessageError("validation", "message must be a JSON object")
    allowed = {
        "kind",
        "title",
        "body",
        "to",
        "teams",
        "options",
        "attachments",
        "thread_id",
        "reply_to",
        "request_sequence",
        "expires_in_seconds",
        "client_request_id",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AgentMessageError("validation", f"unknown message field(s): {', '.join(unknown)}")
    kind = payload.get("kind") or AgentMessage.Kind.UPDATE
    if kind not in AgentMessage.Kind.values:
        raise AgentMessageError(
            "validation", f"kind must be one of {', '.join(AgentMessage.Kind.values)}", "kind"
        )

    request_id = None
    if payload.get("client_request_id") is not None:
        try:
            request_id = uuid.UUID(str(payload["client_request_id"]))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AgentMessageError(
                "validation", "client_request_id must be a UUID", "client_request_id"
            ) from exc
        previous = AgentMessage.all_objects.filter(agent_task=task, client_request_id=request_id).first()
        if previous is not None:
            return previous, False

    if (
        task.status not in {AgentTask.Status.PROVISIONING, AgentTask.Status.RUNNING}
        or task.cancel_requested_at
    ):
        raise AgentMessageError("precondition", "the run has ended; it sends no messages", status=409)
    if task.agent_definition_id is None:
        raise AgentMessageError("precondition", "the run has no agent to send as", status=409)

    title = redact(_text(payload.get("title"), "title", MAX_TITLE_CHARS), task)
    body = redact(_text(payload.get("body"), "body", MAX_BODY_CHARS, required=True), task)
    options = _options(payload.get("options"), task)
    attachments = _attachments(payload.get("attachments"), task)

    request_sequence = payload.get("request_sequence")
    if request_sequence is not None:
        if type(request_sequence) is not int or request_sequence < 1:
            raise AgentMessageError(
                "validation", "request_sequence must be a positive integer", "request_sequence"
            )
        event = AgentTaskEvent.objects.filter(
            agent_task=task, sequence=request_sequence, request__isnull=False
        ).first()
        if event is None:
            raise AgentMessageError(
                "not_found", "the run has no input request with that sequence", "request_sequence", 404
            )
        wanted = (
            AgentTaskEvent.Kind.APPROVAL_REQUIRED
            if kind == AgentMessage.Kind.APPROVAL
            else AgentTaskEvent.Kind.INPUT_REQUIRED
        )
        if event.kind != wanted:
            raise AgentMessageError(
                "validation", f"request {request_sequence} is not a {kind} request", "request_sequence"
            )
    elif kind == AgentMessage.Kind.APPROVAL:
        # Approvals go through the run's gate and its action-safety policy,
        # never a parallel path: the run raises the request first.
        raise AgentMessageError(
            "validation",
            "an approval names the run's approval request (request_sequence); raise it on the callback first",
            "request_sequence",
        )
    if kind == AgentMessage.Kind.APPROVAL:
        options = [
            {"id": "allow", "label": "Allow", "description": ""},
            {"id": "deny", "label": "Deny", "description": ""},
        ]

    expires_at = None
    if payload.get("expires_in_seconds") is not None:
        seconds = payload["expires_in_seconds"]
        if type(seconds) is not int or not 60 <= seconds <= MAX_EXPIRY.total_seconds():
            raise AgentMessageError(
                "validation", "expires_in_seconds must be from 60 to 2592000", "expires_in_seconds"
            )
        expires_at = timezone.now() + dt.timedelta(seconds=seconds)

    thread_id = None
    if payload.get("thread_id") is not None:
        try:
            thread_id = uuid.UUID(str(payload["thread_id"]))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AgentMessageError("validation", "thread_id must be a UUID", "thread_id") from exc
    reply_to = None
    if payload.get("reply_to") is not None:
        try:
            reply_guid = uuid.UUID(str(payload["reply_to"]))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AgentMessageError("validation", "reply_to must be a message id", "reply_to") from exc
        reply_to = AgentMessage.objects.filter(
            guid=reply_guid,
            organization_id=task.organization_id,
            agent_definition_id=task.agent_definition_id,
        ).first()
        if reply_to is None:
            raise AgentMessageError("not_found", "reply_to names no message of this agent", "reply_to", 404)
        thread_id = thread_id or reply_to.thread_id

    from astrolift_registry.models import Workload

    # All runs of one agent share its rate allowance. Serialize admission
    # before counting, including concurrent sends with different run keys.
    if not Workload.objects.select_for_update().filter(pk=task.agent_definition_id).first():
        raise AgentMessageError("precondition", "the run's agent is no longer available", status=409)
    if request_id is not None:
        previous = AgentMessage.all_objects.filter(agent_task=task, client_request_id=request_id).first()
        if previous is not None:
            return previous, False
    since = timezone.now() - dt.timedelta(minutes=1)
    sent = AgentMessage.objects.filter(
        agent_definition_id=task.agent_definition_id, source=AgentMessage.Source.AGENT, created_at__gte=since
    ).count()
    if sent >= rate_per_minute():
        raise AgentMessageError(
            "rate_limited",
            f"this agent sent {sent} messages in the last minute; the limit is {rate_per_minute()}",
            status=429,
        )

    audience = _resolve_audience(
        task.organization_id,
        task.agent_definition,
        users=payload.get("to"),
        teams=payload.get("teams"),
        default_user_id=task.triggered_by_user_id,
    )
    if not audience:
        raise AgentMessageError(
            "validation",
            "name who the message is for (to or teams); this run has no person to default to",
            "to",
        )
    try:
        with transaction.atomic():
            message = _create(
                task,
                kind=kind,
                source=AgentMessage.Source.AGENT,
                title=title,
                body=body,
                options=options,
                attachments=attachments,
                thread_id=thread_id,
                reply_to=reply_to,
                request_sequence=request_sequence,
                expires_at=expires_at,
                audience=audience,
                client_request_id=request_id,
            )
    except IntegrityError:
        previous = AgentMessage.all_objects.filter(agent_task=task, client_request_id=request_id).first()
        if request_id is None or previous is None:
            raise
        return previous, False
    _audit("agent.message.send", message, actor_user_id=None, recipients=len(audience))
    return message, True


def send_platform_message(
    task: AgentTask,
    *,
    kind: str,
    title: str,
    body: str,
    signal_key: str,
    user_ids: list[int] | None = None,
    options: list | None = None,
    request_sequence: int | None = None,
) -> AgentMessage | None:
    """A platform signal about a run, as a message: once per ``signal_key``,
    to ``user_ids`` (default: the person who launched the run) who can read
    the agent. Never raises: a signal must not break what raised it."""
    try:
        if task.agent_definition_id is None:
            return None
        if AgentMessage.all_objects.filter(
            organization_id=task.organization_id, signal_key=signal_key
        ).exists():
            return None
        from django.contrib.auth import get_user_model

        ids = user_ids if user_ids is not None else [task.triggered_by_user_id]
        people = get_user_model().objects.filter(pk__in=[i for i in ids if i is not None], is_active=True)
        audience = [
            (person, None)
            for person in people
            if can_read_agent(person, task.organization_id, task.agent_definition_id)
        ]
        if not audience:
            return None
        with transaction.atomic():
            message = _create(
                task,
                kind=kind,
                source=AgentMessage.Source.PLATFORM,
                title=redact(title[:MAX_TITLE_CHARS], task),
                body=redact(body[:MAX_BODY_CHARS], task),
                options=[
                    {
                        "id": option["id"],
                        "label": redact(option["label"], task),
                        "description": redact(option.get("description") or "", task),
                    }
                    for option in options or []
                ],
                attachments=[{"kind": "url", "ref": f"/agents/runs/{task.guid}", "label": "Run"}],
                thread_id=None,
                reply_to=None,
                request_sequence=request_sequence,
                expires_at=None,
                audience=audience,
                signal_key=signal_key[:128],
            )
        _audit(
            "agent.message.send",
            message,
            actor_user_id=None,
            recipients=len(audience),
            signal=signal_key[:128],
        )
        return message
    except IntegrityError:
        return None
    except Exception:  # noqa: BLE001 - a signal must never break the transition that raised it
        log.exception("agent message signal %s failed", signal_key)
        return None


# Platform signals --------------------------------------------------------


def signal_task_finished(task: AgentTask, status: str) -> None:
    """Finished and failed runs become a result or an alert for the person who launched them."""
    agent = task.agent_definition.name if task.agent_definition_id else "Agent"
    if status == AgentTask.Status.COMPLETED:
        output = (task.result or {}).get("output") if isinstance(task.result, dict) else ""
        send_platform_message(
            task,
            kind=AgentMessage.Kind.RESULT,
            title=f"{agent} finished",
            body=str(output or "The run finished.")[:2000],
            signal_key=f"task.completed:{task.guid}",
        )
    elif status in {AgentTask.Status.FAILED, AgentTask.Status.TIMED_OUT}:
        reason = (task.failure or {}).get("message") if isinstance(task.failure, dict) else ""
        send_platform_message(
            task,
            kind=AgentMessage.Kind.ALERT,
            title=f"{agent} {'timed out' if status == AgentTask.Status.TIMED_OUT else 'failed'}",
            body=str(reason or "The run did not finish.")[:2000],
            signal_key=f"task.{status}:{task.guid}",
        )


def signal_gate_requests(task: AgentTask, events: list[AgentTaskEvent]) -> None:
    """A run waiting at a gate (an input or approval request) asks the person who launched it."""
    agent = task.agent_definition.name if task.agent_definition_id else "Agent"
    for event in events:
        request = event.request or {}
        if event.kind == AgentTaskEvent.Kind.APPROVAL_REQUIRED:
            tool = (request.get("tool") or {}).get("name") or "a tool"
            send_platform_message(
                task,
                kind=AgentMessage.Kind.APPROVAL,
                title=f"{agent} asks to use {tool}"[:MAX_TITLE_CHARS],
                body=event.text or f"{agent} is waiting for approval to use {tool}.",
                signal_key=f"gate:{task.guid}:{event.sequence}",
                options=[
                    {"id": "allow", "label": "Allow", "description": ""},
                    {"id": "deny", "label": "Deny", "description": ""},
                ],
                request_sequence=event.sequence,
            )
        elif event.kind == AgentTaskEvent.Kind.INPUT_REQUIRED:
            questions = request.get("questions") or []
            first = questions[0] if questions else {}
            options = [
                {"id": f"o{i}", "label": o.get("label", ""), "description": o.get("description", "")}
                for i, o in enumerate(first.get("options") or [])
            ]
            send_platform_message(
                task,
                kind=AgentMessage.Kind.QUESTION,
                title=f"{agent} has a question"[:MAX_TITLE_CHARS],
                body="\n\n".join(q.get("text", "") for q in questions) or event.text,
                signal_key=f"gate:{task.guid}:{event.sequence}",
                options=options if len(questions) == 1 else [],
                request_sequence=event.sequence,
            )


# ---------------------------------------------------------------------------
# Fan-out to the person's own channels
# ---------------------------------------------------------------------------


def _fan_out(message_pk: int) -> None:
    """One ``agent.message.sent`` event per message: the existing channels
    (web push and email by each person's notification preferences, the org's
    webhook subscriptions, Slack among them) pick it up from there."""
    try:
        from core.events import Event

        message = AgentMessage.objects.select_related("agent_definition", "agent_task").get(pk=message_pk)
        recipients = list(message.recipients.values_list("user_id", flat=True))
        Event.emit(
            "agent.message.sent",
            {
                "message_id": str(message.guid),
                "thread_id": str(message.thread_id),
                "kind": message.kind,
                "title": message.title,
                "summary": message.body[:240],
                "agent_slug": message.agent_definition.slug if message.agent_definition_id else "",
                "agent_name": message.agent_definition.name if message.agent_definition_id else "",
                "agent_task_id": str(message.agent_task.guid) if message.agent_task_id else "",
                "recipient_user_ids": recipients,
                "target_url": f"{str(getattr(settings, 'FRONTEND_URL', '')).rstrip('/')}/inbox?thread={message.thread_id}",
            },
            resource_kind="agent_message",
            resource_id=str(message.guid),
            organization_id=message.organization_id,
            registered_app_id=message.agent_definition.registered_app_id
            if message.agent_definition_id
            else None,
        )
    except Exception:  # noqa: BLE001 - delivery to other channels is best-effort
        log.exception("agent message fan-out failed for %s", message_pk)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def mark_delivered(rows) -> None:
    pending = [row for row in rows if row.delivered_at is None]
    if pending:
        now = timezone.now()
        AgentMessageRecipient.objects.filter(
            pk__in=[row.pk for row in pending], delivered_at__isnull=True
        ).update(delivered_at=now)
        for row in pending:
            row.delivered_at = now


def mark_read(user, organization_id: int, *, message_ids=None, thread_id=None) -> int:
    rows = visible_recipient_rows(user, organization_id).filter(read_at__isnull=True)
    if message_ids is not None:
        rows = rows.filter(message__guid__in=message_ids)
    if thread_id is not None:
        rows = rows.filter(message__thread_id=thread_id)
    now = timezone.now()
    return AgentMessageRecipient.objects.filter(pk__in=list(rows.values_list("pk", flat=True))).update(
        read_at=now, delivered_at=now
    )


# ---------------------------------------------------------------------------
# Replying
# ---------------------------------------------------------------------------


def _reply_text(message: AgentMessage, body: str, choices: list[str], decision: str, label: str) -> str:
    labels = {o["id"]: o["label"] for o in message.options or []}
    picked = ", ".join(labels.get(c, c) for c in choices)
    parts = [f"Reply from {label or 'a person'} to your message {message.guid}"]
    if message.title:
        parts[0] += f' ("{message.title}")'
    if decision:
        parts.append(f"Decision: {decision}")
    if picked:
        parts.append(f"Choice: {picked}")
    if body:
        parts.append(body)
    content = "\n".join(parts[1:])
    if len(content) > MAX_REPLY_CHARS:
        raise AgentMessageError("validation", "body and choices exceed the live-run input limit", "body")
    # Keep every accepted answer character. The input row carries its
    # author separately when a full-size answer leaves no room for a header.
    header_budget = MAX_REPLY_CHARS - len(content) - 1
    return f"{parts[0][:header_budget]}\n{content}" if header_budget > 0 else content


@transaction.atomic
def reply(
    message: AgentMessage,
    user,
    *,
    body: str = "",
    choices: list[str] | None = None,
    decision: str = "",
    client_request_id: str | None = None,
) -> AgentMessageReply:
    """Answer ``message`` as ``user`` (who must be a recipient that can still
    read the agent) and send the answer back to the agent."""
    from astrolift_agents.services.agent_task_input import AgentTaskInputError, queue_agent_task_input
    from astrolift_agents.services.agent_task_requests import TaskInputRequestError, reply_to_request

    # Serialize retries with both delivery and reply persistence. A failed
    # reply insert must roll back its gate answer or queued input as well.
    message = AgentMessage.objects.select_for_update().filter(pk=message.pk).first()
    if message is None:
        raise AgentMessageError("not_found", "message not found", "messageId", 404)
    if not visible_recipient_rows(user, message.organization_id).filter(message=message).exists():
        raise AgentMessageError("not_found", "message not found", "messageId", 404)
    if not can_read_agent(user, message.organization_id, message.agent_definition_id):
        raise AgentMessageError("not_found", "message not found", "messageId", 404)

    choices = list(choices or [])
    body = _text(body, "body", MAX_REPLY_CHARS)
    decision = (decision or "").strip().lower()
    request_id = None
    if client_request_id is not None:
        try:
            request_id = uuid.UUID(str(client_request_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AgentMessageError(
                "validation", "clientRequestId must be a UUID", "clientRequestId"
            ) from exc
        previous = AgentMessageReply.all_objects.filter(
            message=message, author=user, client_request_id=request_id
        ).first()
        if previous is not None:
            if (
                previous.deleted_at is not None
                or previous.body != redact(body, message.agent_task)
                or previous.choices != choices
                or previous.decision != decision
            ):
                raise AgentMessageError(
                    "precondition", "clientRequestId has already been used", "clientRequestId", 409
                )
            return previous

    if message.expires_at is not None and message.expires_at <= timezone.now():
        raise AgentMessageError("precondition", "this question has expired", status=409)
    option_ids = {o["id"] for o in message.options or []}
    if any(not isinstance(c, str) or c not in option_ids for c in choices):
        raise AgentMessageError("validation", "choices must be the message's option ids", "choices")
    if message.kind == AgentMessage.Kind.APPROVAL:
        if decision not in {"allow", "deny"}:
            raise AgentMessageError("validation", "an approval needs an allow or deny decision", "decision")
    elif decision:
        raise AgentMessageError("validation", "only an approval takes a decision", "decision")
    if not body and not choices and not decision:
        raise AgentMessageError("validation", "say something: a body, a choice or a decision", "body")

    label = getattr(user, "username", "") or getattr(user, "email", "") or ""
    task = (
        AgentTask.objects.select_for_update().filter(pk=message.agent_task_id).first()
        if message.agent_task_id is not None
        else None
    )
    body = redact(body, task)
    fields: dict[str, Any] = {}
    if message.request_sequence is not None and task is not None:
        # The run's own gate: the existing reply path and, for an allow, its
        # action-safety policy (Zentinelle) and the controller permission.
        if message.kind == AgentMessage.Kind.APPROVAL:
            from astrolift_agents.visibility import agent_tasks

            controller = (
                agent_tasks(message.organization_id, Permission.AGENT_TASK_SEND_INPUT)
                .filter(pk=task.pk)
                .exists()
            )
            if not controller:
                raise AgentMessageError(
                    "refused", "approving needs agent_task.send_input on this run", "decision", status=403
                )
            response: dict[str, Any] = {"decision": decision}
            if body:
                response["reason"] = body
        else:
            event = AgentTaskEvent.objects.filter(agent_task=task, sequence=message.request_sequence).first()
            questions = ((event.request or {}).get("questions") if event else None) or []
            if len(questions) != 1:
                raise AgentMessageError(
                    "precondition",
                    "this request has several questions; answer it in the run view",
                    status=409,
                )
            question = questions[0]
            labels = {o["id"]: o["label"] for o in message.options or []}
            picked = [labels[c] for c in choices] or ([body] if body else [])
            if not picked:
                raise AgentMessageError("validation", "pick an option or write an answer", "choices")
            response = {"answers": {question["id"]: picked if question.get("multiple") else picked[0]}}
        try:
            gate_reply = reply_to_request(
                task=task,
                sequence=message.request_sequence,
                response=response,
                author=user,
                author_label=label,
            )
        except TaskInputRequestError as exc:
            code = {404: "not_found", 403: "refused", 409: "precondition"}.get(exc.status, "validation")
            raise AgentMessageError(code, str(exc), status=exc.status) from exc
        fields.update(route=AgentMessageReply.Route.GATE, gate_reply=gate_reply, delivered_at=timezone.now())
    elif task is not None and task.status == AgentTask.Status.RUNNING and not task.cancel_requested_at:
        try:
            queued = queue_agent_task_input(
                task=task,
                message=_reply_text(message, body, choices, decision, label),
                author=user,
                author_label=label,
            )
        except AgentTaskInputError as exc:
            raise AgentMessageError("precondition", exc.message, status=409) from exc
        fields.update(route=AgentMessageReply.Route.LIVE_RUN, input_message=queued)
    else:
        fields.update(route=AgentMessageReply.Route.QUEUED)

    row = AgentMessageReply.objects.create(
        organization_id=message.organization_id,
        message=message,
        author=user,
        author_label=label[:255],
        body=redact(body, task),
        choices=choices,
        decision=decision,
        client_request_id=request_id,
        **fields,
    )
    AgentMessageRecipient.objects.filter(message=message, user=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    if row.route == AgentMessageReply.Route.QUEUED:
        # The workflow must see the committed reply and task; never launch
        # it while this transaction could still roll back. Preserve the
        # caller's token ceiling if an outer transaction owns the commit.
        wake_context = contextvars.copy_context()
        transaction.on_commit(lambda: wake_context.run(_wake, row, user))
    return row


def reply_payload(row: AgentMessageReply) -> dict[str, Any]:
    """An answer as the agent reads it."""
    return {
        "id": str(row.guid),
        "message_id": str(row.message.guid),
        "thread_id": str(row.message.thread_id),
        "kind": row.message.kind,
        "author": row.author_label,
        "body": row.body,
        "choices": row.choices,
        "decision": row.decision,
        "created_at": row.created_at.isoformat(),
    }


def _wake(row: AgentMessageReply, user) -> None:
    """Start the agent with the answer when the person may run it; otherwise
    the answer stays queued for its next run (a schedule tick, say)."""
    from astrolift_agents.services.agent_dispatch import AgentDispatchError, dispatch_registered_agent
    from astrolift_registry.models import Workload
    from astrolift_workflows.inputs import Actor
    from core.run_trigger import request_trigger

    workload = row.message.agent_definition
    if (
        workload is None
        or workload.deleted_at is not None
        or workload.run_family != Workload.RunFamily.TASK
        or not _may_dispatch(user, row.organization_id, workload)
    ):
        return
    from astrolift_identity.api_tokens import get_current_api_token

    token = get_current_api_token()
    try:
        task = dispatch_registered_agent(
            organization_id=row.organization_id,
            team_id=token.team_id if token is not None else None,
            agent_slug=workload.slug,
            workload_id=workload.pk,
            actor=Actor(kind="user", user_id=user.pk, display=row.author_label),
            trigger_payload={"agent_message_reply": reply_payload(row)},
            trigger="agent_message_reply",
            trigger_kind=request_trigger(),
        )
    except AgentDispatchError as exc:
        log.info("agent message reply %s stays queued: %s", row.guid, exc.message)
        return
    except Exception:  # noqa: BLE001 - the answer is kept; the next run collects it
        log.exception("waking agent for reply %s failed", row.guid)
        return
    AgentMessageReply.objects.filter(pk=row.pk).update(
        route=AgentMessageReply.Route.WOKE, wake_task=task, delivered_at=timezone.now()
    )
    row.route, row.wake_task, row.delivered_at = AgentMessageReply.Route.WOKE, task, timezone.now()


def claim_queued_replies(task: AgentTask, *, limit: int = 20) -> list[AgentMessageReply]:
    """Queued answers to messages of the run's agent, handed to this run once."""
    if task.agent_definition_id is None:
        return []
    with transaction.atomic():
        rows = list(
            AgentMessageReply.objects.select_for_update(skip_locked=True)
            .filter(
                organization_id=task.organization_id,
                message__agent_definition_id=task.agent_definition_id,
                route=AgentMessageReply.Route.QUEUED,
                delivered_at__isnull=True,
            )
            .select_related("message")
            .order_by("created_at", "id")[: max(1, min(limit, 50))]
        )
        if rows:
            AgentMessageReply.objects.filter(pk__in=[r.pk for r in rows]).update(delivered_at=timezone.now())
    return rows


@transaction.atomic
def requeue_undelivered_replies(task: AgentTask) -> int:
    """Keep answers when a run ends before consuming its input channel."""
    from django.db.models import F

    from astrolift_agents.models import AgentTaskInputMessage

    if task.status not in AgentTask.TERMINAL_STATUSES:
        return 0
    replies = AgentMessageReply.objects.filter(
        organization_id=task.organization_id,
        message__agent_task_id=task.pk,
        route=AgentMessageReply.Route.LIVE_RUN,
        delivered_at__isnull=True,
    )
    # The runner claims these same input rows under a lock. Whichever path
    # wins owns delivery: consumed input stays live; abandoned input moves
    # to the next run and cannot also be claimed by a late old callback.
    inputs = list(
        AgentTaskInputMessage.objects.select_for_update()
        .filter(
            agent_task=task,
            delivered_at__isnull=True,
            pk__in=replies.values("input_message_id"),
        )
        .order_by("pk")
    )
    if not inputs:
        return 0
    ids = [row.pk for row in inputs]
    moved = list(replies.filter(input_message_id__in=ids).select_related("message"))
    now = timezone.now()
    AgentTaskInputMessage.objects.filter(pk__in=ids).update(
        deleted_at=now, updated_at=now, version=F("version") + 1
    )
    count = AgentMessageReply.objects.filter(pk__in=[row.pk for row in moved]).update(
        route=AgentMessageReply.Route.QUEUED, updated_at=now, version=F("version") + 1
    )
    for row in moved:
        _audit(
            "agent.message.reply.requeued",
            row.message,
            actor_user_id=row.author_id,
            reply_id=str(row.guid),
            reason="run_ended_before_input_consumed",
        )
    return count


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def purge_expired_messages(*, now=None) -> int:
    """Delete messages past their org's retention (the audit retention window,
    ``Organization.audit_log_retention_days``): a message lives as long as the
    audit rows that record it being sent and answered."""
    from astrolift_identity.models import Organization

    now = now or timezone.now()
    removed = 0
    for org_id, days in Organization.objects.values_list("pk", "audit_log_retention_days"):
        cutoff = now - dt.timedelta(days=max(1, int(days or 365)))
        _, per_model = AgentMessage.all_objects.filter(organization_id=org_id, created_at__lt=cutoff).delete()
        removed += per_model.get(AgentMessage._meta.label, 0)
    return removed
