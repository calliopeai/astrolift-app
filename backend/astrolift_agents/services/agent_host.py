"""Authenticated commands for attaching to existing Astrolift tasks."""

import re
from uuid import UUID

from django.db import transaction
from django.db.models import Q

from astrolift_agents.models import AgentHostAction, AgentHostAuthority, AgentTaskEvent
from astrolift_agents.services import agent_host_projection as projection
from astrolift_agents.services.agent_task_input import queue_agent_task_input
from astrolift_agents.services.agent_task_requests import reply_to_request
from astrolift_agents.visibility import agent_tasks
from astrolift_identity.org_modules import AGENT_LIVE_ATTACH, module_state
from astrolift_operations.models import AuditEvent
from core.permissions import Permission, PermissionDenied

_RESOURCE = re.compile(r"ahp-session:/([0-9a-f-]{36})(/chat)?\Z")


class HostError(ValueError):
    def __init__(self, message, code=-32602, data=None):
        super().__init__(message)
        self.code, self.data = code, data


def fallback(reason):
    return {"ahp_available": False, "reason": reason, "fallback": "agent_task.watch"}


class AgentHost:
    def __init__(self, user, tenant):
        self.user, self.tenant = user, tenant
        self.client_id = None
        self.subscriptions = set()
        self.server_seq = 0
        self.channel_from_seq = {}
        self.catalog_state = {}

    def _module(self):
        enabled, reason = module_state(self.tenant.organization_id, AGENT_LIVE_ATTACH)
        if not enabled:
            raise HostError("Live attach is unavailable", -32003, fallback(reason))

    def _task(self, channel, permission=Permission.AGENT_READ):
        match = _RESOURCE.fullmatch(channel) if isinstance(channel, str) else None
        if not match:
            raise HostError("Unknown channel", -32002)
        try:
            guid = UUID(match[1])
        except ValueError:
            raise HostError("Unknown channel", -32002) from None
        task = agent_tasks(self.tenant.organization_id, permission).filter(guid=guid).first()
        if task is None:
            raise HostError("Task unavailable or permission denied", -32002)
        return task

    def _snapshot(self, channel):
        if isinstance(channel, str) and channel.startswith("ahp-terminal:/"):
            self._module()
            from astrolift_agents.services.agent_host_terminal import snapshot

            return snapshot(self, channel)
        if channel == projection.ROOT:
            AgentHostAuthority.objects.get_or_create(organization_id=self.tenant.organization_id)
            authority = AgentHostAuthority.objects.get(organization_id=self.tenant.organization_id)
            enabled, reason = module_state(self.tenant.organization_id, AGENT_LIVE_ATTACH)
            from astrolift_agents.services.agent_host_terminal import catalog

            state = {
                "terminals": catalog(self) if self.client_id and enabled else [],
                "agents": [
                    {
                        "provider": "astrolift",
                        "displayName": "Astrolift",
                        "description": "Attach to registered running tasks",
                        "models": [],
                    }
                ],
                "_meta": {"ahp_available": True} if enabled else fallback(reason),
            }
            last = (
                AgentHostAction.objects.filter(
                    authority=authority,
                    actor=self.user,
                    client_id=self.client_id,
                    client_sequence__isnull=False,
                )
                .order_by("-client_sequence")
                .values_list("client_sequence", flat=True)
                .first()
                or 0
            )
            state["_meta"]["astrolift.nextClientSeq"] = last + 1
            return {"resource": channel, "state": state, "fromSeq": authority.server_sequence}
        self._module()
        task = self._task(channel)
        sequence, states = projection.snapshots([task])
        return {"resource": channel, "state": states[channel], "fromSeq": sequence}

    def command(self, method, params):
        if not isinstance(params, dict):
            raise HostError("params must be an object")
        if self.client_id is None and method != "initialize":
            raise HostError("initialize must be the first message", -32000)
        if method == "initialize":
            if self.client_id is not None:
                raise HostError("Already initialized", -32000)
            if projection.PROTOCOL_VERSION not in params.get("protocolVersions", []):
                raise HostError(
                    "Unsupported protocol version",
                    -32005,
                    {"supportedVersions": [projection.PROTOCOL_VERSION]},
                )
            client = params.get("clientId")
            if (
                not isinstance(client, str)
                or not 1 <= len(client) <= 128
                or params.get("channel") != projection.ROOT
            ):
                raise HostError("initialize requires root channel and a bounded clientId")
            channels = params.get("initialSubscriptions", [])
            if (
                not isinstance(channels, list)
                or len(channels) > 100
                or not all(isinstance(c, str) for c in channels)
            ):
                raise HostError("At most 100 initial subscriptions are allowed")
            # Take all snapshots at one sequence, rather than losing events
            # between sequential channel subscriptions.
            self.client_id = client
            try:
                snaps = self._snapshots(channels)
            except Exception:
                self.client_id = None
                raise
            self.subscriptions = set(channels)
            self.channel_from_seq = {snap["resource"]: snap["fromSeq"] for snap in snaps}
            self.server_seq = max(
                [s["fromSeq"] for s in snaps] or [self._snapshot(projection.ROOT)["fromSeq"]]
            )
            return {
                "protocolVersion": projection.PROTOCOL_VERSION,
                "serverSeq": self.server_seq,
                "serverInfo": {"name": "astrolift", "version": "1.0.0"},
                "snapshots": snaps,
            }
        if method == "subscribe":
            channel = params.get("channel")
            if len(self.subscriptions) >= 100 and channel not in self.subscriptions:
                raise HostError("Subscription limit reached")
            snap = self._snapshot(channel)
            self.subscriptions.add(channel)
            self.channel_from_seq[channel] = snap["fromSeq"]
            # Existing subscriptions keep their cursor: advancing it here
            # would skip another channel's pending actions.
            return {"snapshot": snap}
        if method == "unsubscribe":
            self.subscriptions.discard(params.get("channel"))
            self.channel_from_seq.pop(params.get("channel"), None)
            return {}
        if method == "listSessions":
            self._module()
            if params.get("channel") != projection.ROOT:
                raise HostError("listSessions requires root channel")
            limit, offset = params.get("limit", 50), params.get("cursor", "0")
            if (
                type(limit) is not int
                or not 1 <= limit <= 100
                or not isinstance(offset, str)
                or not offset.isdecimal()
            ):
                raise HostError("Invalid page")
            offset = int(offset)
            if offset > 1_000_000:
                raise HostError("Invalid page")
            rows = list(
                agent_tasks(self.tenant.organization_id, Permission.AGENT_READ).order_by(
                    "-updated_at", "-id"
                )[offset : offset + limit + 1]
            )
            _, states = projection.snapshots(rows[:limit])
            result = {
                "items": [
                    {
                        **projection.summary(task, states[projection.chat_uri(task)]),
                        "_meta": {"ahp_available": True},
                    }
                    for task in rows[:limit]
                ]
            }
            if len(rows) > limit:
                result["nextCursor"] = str(offset + limit)
            return result
        if method == "reconnect":
            if params.get("clientId") != self.client_id:
                raise HostError("Reconnect clientId differs from initialized identity")
            channels, last = params.get("subscriptions"), params.get("lastSeenServerSeq")
            if (
                not isinstance(channels, list)
                or len(channels) > 100
                or not all(isinstance(c, str) for c in channels)
                or type(last) is not int
                or last < 0
            ):
                raise HostError("Invalid reconnect")
            valid, missing = [], []
            for channel in channels:
                try:
                    self._snapshot(channel)
                    valid.append(channel)
                except HostError:
                    missing.append(channel)
            self.subscriptions = set(valid)
            self.channel_from_seq = dict.fromkeys(valid, last)
            self.server_seq = last
            actions = self.poll()
            return {"type": "replay", "actions": actions, "missing": missing}
        if method == "dispatchAction":
            return self.dispatch(params)
        raise HostError("Method not supported by this attach-only host", -32601)

    @transaction.atomic
    def _snapshots(self, channels):
        AgentHostAuthority.objects.get_or_create(organization_id=self.tenant.organization_id)
        authority = AgentHostAuthority.objects.select_for_update().get(
            organization_id=self.tenant.organization_id
        )
        tasks, terminals = {}, {}

        for channel in channels:
            if channel.startswith("ahp-terminal:/"):
                terminals[channel] = self._snapshot(channel)
            elif channel != projection.ROOT:
                self._module()
                task = self._task(channel)
                tasks[task.pk] = task
        root = self._snapshot(projection.ROOT)
        sequence, states = projection.snapshots(list(tasks.values())) if tasks else (root["fromSeq"], {})
        root["fromSeq"] = sequence
        authority.refresh_from_db()
        sequence = authority.server_sequence
        root["fromSeq"] = sequence
        return [
            root
            if channel == projection.ROOT
            else {
                "resource": channel,
                "state": terminals[channel]["state"] if channel in terminals else states[channel],
                "fromSeq": sequence,
            }
            for channel in channels
        ]

    def poll(self):
        if self.client_id is None:
            return []
        tasks, boxes = {}, {}
        for channel in self.subscriptions:
            if channel == projection.ROOT:
                continue
            self._module()
            if channel.startswith("ahp-terminal:/"):
                from astrolift_agents.services.agent_host_terminal import resolve

                box = resolve(self, channel)
                boxes[box.pk] = box
            else:
                task = self._task(channel)
                tasks[task.pk] = task
        if tasks:
            projection.snapshots(list(tasks.values()))
        authority = AgentHostAuthority.objects.filter(organization_id=self.tenant.organization_id).first()
        if authority is None:
            return []
        if self.server_seq > authority.server_sequence:
            raise HostError("Replay sequence is ahead of this authority")
        rows = (
            AgentHostAction.objects.filter(
                authority=authority,
                server_sequence__gt=self.server_seq,
                server_sequence__lte=authority.server_sequence,
                channel__in=self.subscriptions,
            )
            .filter(Q(agent_task_id__in=tasks) | Q(agent_box_id__in=boxes))
            .order_by("server_sequence")
        )
        result = [
            projection.envelope(row)
            for row in rows
            if row.server_sequence > self.channel_from_seq.get(row.channel, 0)
            and (not row.rejection_reason or row.actor_id == self.user.pk)
        ]
        self.server_seq = authority.server_sequence
        return result

    def _audit(self, action, channel, accepted, reason=""):
        AuditEvent.objects.create(
            organization_id=self.tenant.organization_id,
            actor_kind="user",
            actor_id=str(self.user.pk),
            action="agent_host.dispatch",
            decision="ALLOW" if accepted else "DENY",
            target_kind="agent_task",
            data={
                "action_type": action.get("type"),
                "channel": channel,
                "client_id": self.client_id,
                "reason": reason[:1000],
            },
        )

    @transaction.atomic
    def dispatch(self, params):
        channel, action, seq = params.get("channel"), params.get("action"), params.get("clientSeq")
        if not isinstance(action, dict) or type(seq) is not int or not 1 <= seq <= 2**53 - 1:
            raise HostError("dispatchAction requires an action and a positive clientSeq")
        try:
            self._module()
            task = self._task(channel)
        except HostError as exc:
            self._audit(action, channel, False, str(exc))
            if not isinstance(channel, str) or len(channel) > 255:
                raise
            AgentHostAuthority.objects.get_or_create(organization_id=self.tenant.organization_id)
            authority = AgentHostAuthority.objects.select_for_update().get(
                organization_id=self.tenant.organization_id
            )
            previous = AgentHostAction.objects.filter(
                authority=authority, actor=self.user, client_id=self.client_id, client_sequence=seq
            ).first()
            if previous:
                if previous.channel != channel or previous.action != action:
                    raise HostError("clientSeq already identifies another action") from None
                return projection.envelope(previous)
            row = projection.append_action(
                authority,
                None,
                channel,
                action,
                actor=self.user,
                origin={"clientId": self.client_id, "clientSeq": seq},
                rejection=str(exc),
            )
            authority.save()
            return projection.envelope(row)
        AgentHostAuthority.objects.get_or_create(organization_id=task.organization_id)
        authority = AgentHostAuthority.objects.select_for_update().get(organization_id=task.organization_id)
        previous = AgentHostAction.objects.filter(
            authority=authority, actor=self.user, client_id=self.client_id, client_sequence=seq
        ).first()
        if previous:
            if previous.channel != channel or previous.action != action:
                raise HostError("clientSeq already identifies another action") from None
            return projection.envelope(previous)
        current = projection.project_locked(authority, task)
        origin = {"clientId": self.client_id, "clientSeq": seq}
        reason = ""
        try:
            if channel != projection.chat_uri(task):
                raise HostError("Controller actions require the task chat channel")
            self._apply(task, current, action)
        except (HostError, ValueError) as exc:
            reason = str(exc)
        self._audit(action, channel, not reason, reason)
        row = projection.append_action(
            authority, task, channel, action, actor=self.user, origin=origin, rejection=reason
        )
        if not reason:
            current.chat = projection.reduce_chat(current.chat, action)
            current.save()
        authority.save()
        return projection.envelope(row)

    def _apply(self, task, current, action):
        kind = action.get("type")
        permission = (
            Permission.AGENT_DISPATCH if kind == "chat/turnCancelled" else Permission.AGENT_TASK_SEND_INPUT
        )
        self._task(projection.chat_uri(task), permission)
        if kind == "chat/turnCancelled":
            if (
                set(action) != {"type", "turnId", "duration"}
                or action["turnId"] != current.chat.get("activeTurn", {}).get("id")
                or type(action["duration"]) is not int
                or not 0 <= action["duration"] <= 86_400_000
            ):
                raise HostError("No matching active turn")
            from astrolift_workflows.activities.agent_stage import _cancel_agent_task_sync

            if not _cancel_agent_task_sync(str(task.guid)):
                raise HostError("Task stop could not be confirmed")
        elif kind == "chat/pendingMessageSet":
            if (
                set(action) != {"type", "kind", "id", "message"}
                or action.get("kind") != "steering"
                or not isinstance(action.get("message"), dict)
                or set(action["message"]) != {"text", "origin"}
                or action["message"]["origin"] != {"kind": "user"}
            ):
                raise HostError("Only an explicit user steering message is supported")
            if not isinstance(action["message"].get("text"), str) or not isinstance(action.get("id"), str):
                raise HostError("Steering requires text and an idempotency UUID")
            queue_agent_task_input(
                task=task, message=action["message"]["text"], author=self.user, client_request_id=action["id"]
            )
        elif kind in {"chat/toolCallConfirmed", "chat/inputCompleted"}:
            key = action.get("toolCallId") if kind == "chat/toolCallConfirmed" else action.get("requestId")
            if not isinstance(key, str) or not re.fullmatch(r"(?:request|tool)-[1-9][0-9]{0,5}", key):
                raise HostError("Unknown input request")
            events = AgentTaskEvent.objects.filter(
                agent_task=task, organization_id=task.organization_id, request__isnull=False
            )
            event = (
                events.filter(sequence=int(key[8:])).first()
                if key.startswith("request-")
                else events.filter(data__call_id=key).order_by("-sequence").first()
            )
            if event is None:
                raise HostError("Unknown input request")
            if kind == "chat/toolCallConfirmed":
                if (
                    event.kind != "approval_required"
                    or action.get("turnId") != event.turn_id
                    or type(action.get("approved")) is not bool
                    or not set(action)
                    <= {"type", "turnId", "toolCallId", "approved", "confirmed", "reason", "reasonMessage"}
                ):
                    raise HostError("Invalid tool confirmation; editing registered tool input is unavailable")
                if action["approved"] and action.get("confirmed") != "user-action":
                    raise HostError("A controller approval requires user-action confirmation")
                if not action["approved"] and action.get("reason") not in {"denied", "skipped"}:
                    raise HostError("A denied tool requires a cancellation reason")
                response = {"decision": "allow" if action["approved"] else "deny"}
                if action.get("reasonMessage"):
                    response["reason"] = action["reasonMessage"]
            else:
                if (
                    event.kind != "input_required"
                    or action.get("response") != "accept"
                    or not set(action) <= {"type", "requestId", "response", "answers"}
                ):
                    raise HostError("The runner requires complete question answers")
                answers = action.get("answers")
                if not isinstance(answers, dict) or not all(
                    isinstance(value, dict)
                    and value.get("state") == "submitted"
                    and isinstance(value.get("value"), dict)
                    and "value" in value["value"]
                    for value in answers.values()
                ):
                    raise HostError("Complete submitted answers are required")
                response = {"answers": {key: value["value"]["value"] for key, value in answers.items()}}
            reply_to_request(task=task, sequence=event.sequence, response=response, author=self.user)
        else:
            raise HostError("This action is not supported by the registered agent")

    def catalog_notifications(self):
        """Root lifecycle notifications are hints; listSessions is authoritative.

        Recompute visibility before emitting, including group/API-token ceilings.
        Only changed tasks need their durable projection refreshed.
        """
        if projection.ROOT not in self.subscriptions:
            return []
        self._module()
        try:
            tasks = list(agent_tasks(self.tenant.organization_id, Permission.AGENT_READ))
        except PermissionDenied:
            tasks = []
        current, result = {}, []
        for task in tasks:
            resource = projection.session_uri(task)
            previous = self.catalog_state.get(resource)
            if previous is not None and previous[0] == task.version:
                current[resource] = previous
                continue
            _, states = projection.snapshots([task])
            info = projection.summary(task, states[projection.chat_uri(task)])
            current[resource] = (task.version, info)
            if previous is None:
                result.append(
                    {"method": "root/sessionAdded", "params": {"channel": projection.ROOT, "summary": info}}
                )
            else:
                changes = {
                    key: value
                    for key, value in info.items()
                    if key not in {"resource", "provider", "createdAt"} and previous[1].get(key) != value
                }
                if changes:
                    result.append(
                        {
                            "method": "root/sessionSummaryChanged",
                            "params": {"channel": projection.ROOT, "session": resource, "changes": changes},
                        }
                    )
        for resource in self.catalog_state.keys() - current.keys():
            result.append(
                {"method": "root/sessionRemoved", "params": {"channel": projection.ROOT, "session": resource}}
            )
        self.catalog_state = current
        return result
