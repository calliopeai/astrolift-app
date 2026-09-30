"""Apply signed policy actions through existing task, input and box controls."""

import hashlib
import hmac
import json
import logging
import re
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from astrolift_agents.models import (
    AgentBox,
    AgentDispatchQuarantine,
    AgentEnforcementAction,
    AgentEnforcementNonce,
    AgentTask,
    AgentTaskEvent,
)
from astrolift_identity.org_modules import AGENT_POLICY_ENFORCEMENT, module_state
from astrolift_operations.models import AuditEvent, ZentinelleClusterGateway
from astrolift_operations.zentinelle_connect import _install_call, _install_credential, revoke_agent_key

log = logging.getLogger(__name__)


class AgentEnforcementError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def signature(body, credential):
    key = hashlib.sha256(credential.encode()).digest()
    return "sha256=" + hmac.new(key, canonical(body), hashlib.sha256).hexdigest()


def _validate(connection, body, presented):
    connection.refresh_from_db()
    credential = _install_credential(connection)
    if (
        not isinstance(presented, str)
        or not re.fullmatch(r"sha256=[0-9a-f]{64}", presented)
        or not hmac.compare_digest(signature(body, credential), presented)
    ):
        raise AgentEnforcementError("Invalid enforcement signature", 401)
    required = {
        "version",
        "action_id",
        "nonce",
        "expires_at",
        "install_id",
        "tenant_id",
        "target_kind",
        "target_id",
        "agent_id",
        "cluster_id",
        "action",
        "block_level",
        "mode",
        "policy_id",
        "policy_name",
        "reason",
        "message",
        "evidence_url",
        "turn_id",
        "tool_call_id",
    }
    if (
        set(body) - (required | {"quarantine_target"})
        or not required <= set(body)
        or type(body["version"]) is not int
        or body["version"] != 1
    ):
        raise AgentEnforcementError("Invalid enforcement envelope")
    if (
        type(body["expires_at"]) is not int
        or not 0 < body["expires_at"] - int(timezone.now().timestamp()) <= 120
    ):
        raise AgentEnforcementError("Expired enforcement action", 401)
    if (
        body["install_id"] != connection.zentinelle_install_id
        or body["tenant_id"] not in connection.tenant_ids
    ):
        raise AgentEnforcementError("Enforcement tenant is outside the install", 403)
    for field in ("action_id", "nonce", "target_id"):
        try:
            UUID(body[field])
        except (ValueError, TypeError, AttributeError) as exc:
            raise AgentEnforcementError("Invalid enforcement identity") from exc
    if (
        body["target_kind"] not in {"task", "box"}
        or body["action"] not in {"log", "alert", "warn", "steer", "redact", "require_approval", "block"}
        or body["mode"] not in {"audit", "enforce"}
    ):
        raise AgentEnforcementError("Invalid enforcement action or mode")
    if body["block_level"] not in {None, "tool_call", "turn", "revoke_key", "stop", "quarantine"}:
        raise AgentEnforcementError("Invalid block level")
    for field in required - {"version", "expires_at", "block_level"}:
        if not isinstance(body[field], str) or len(body[field]) > (4000 if field == "message" else 1000):
            raise AgentEnforcementError("Invalid enforcement field")
    target = (
        (AgentTask if body["target_kind"] == "task" else AgentBox)
        .objects.filter(
            organization_id=connection.organization_id,
            guid=body["target_id"],
            model_gateway_connection=connection,
            model_gateway_agent_id=body["agent_id"],
        )
        .first()
    )
    if target is None:
        raise AgentEnforcementError("Enforcement target unavailable", 404)
    if isinstance(target, AgentTask):
        actual_cluster = (target.dispatch_target or {}).get("cluster_guid", "")
    else:
        from astrolift_agents.services.agent_cluster import resolve_agent_cluster

        actual_cluster = str(resolve_agent_cluster(target.organization).guid)
    if actual_cluster != body["cluster_id"]:
        raise AgentEnforcementError("Enforcement cluster does not match the saved target", 403)
    if (
        actual_cluster
        and not ZentinelleClusterGateway.objects.filter(
            connection=connection, zentinelle_cluster_id=actual_cluster, cluster__deleted_at__isnull=True
        ).exists()
    ):
        raise AgentEnforcementError("Enforcement cluster is outside the install", 403)
    return target


def apply_signed(connection, body, presented):
    if not isinstance(body, dict) or len(canonical(body)) > 16 * 1024:
        raise AgentEnforcementError("Invalid enforcement body")
    target = _validate(connection, body, presented)
    intent = {key: value for key, value in body.items() if key not in {"nonce", "expires_at"}}
    with transaction.atomic():
        try:
            with transaction.atomic():
                AgentEnforcementNonce.objects.create(
                    organization_id=connection.organization_id,
                    connection=connection,
                    nonce=body["nonce"],
                    expires_at=datetime.fromtimestamp(body["expires_at"], UTC),
                )
        except IntegrityError as exc:
            raise AgentEnforcementError("Enforcement nonce was already used", 409) from exc
        row, created = AgentEnforcementAction.objects.get_or_create(
            connection=connection,
            external_id=body["action_id"],
            defaults={
                "organization_id": connection.organization_id,
                "payload": intent,
                "agent_task": target if isinstance(target, AgentTask) else None,
                "agent_box": target if isinstance(target, AgentBox) else None,
            },
        )
        row = AgentEnforcementAction.objects.select_for_update().get(pk=row.pk)
        if created:
            AuditEvent.objects.create(
                organization_id=connection.organization_id,
                actor_kind="service",
                actor_id=connection.zentinelle_install_id,
                action="agents.policy.received",
                decision="ALLOW",
                target_kind=body["target_kind"],
                target_id=str(target.guid),
                data={
                    "action_id": str(row.external_id),
                    "policy_id": body["policy_id"],
                    "evidence_url": body["evidence_url"],
                },
            )
        if row.payload != intent:
            raise AgentEnforcementError("Enforcement identity conflicts with a recorded action", 409)
        if not created:
            if row.status == "applying":
                return {
                    "status": "failed",
                    "applied_action": "unknown",
                    "reason": "Prior delivery is unresolved; inspect the target before retrying",
                }
            transaction.on_commit(
                lambda: _annotate(target, row)
                if isinstance(target, AgentTask)
                else _annotate_box(target, row)
            )
            return row.outcome
    enabled, _ = module_state(connection.organization_id, AGENT_POLICY_ENFORCEMENT)
    try:
        if not enabled or (body["mode"] == "audit" and body["action"] != "alert"):
            outcome = {
                "status": "observed",
                "applied_action": "log",
                "reason": "Organization or policy is observe-only",
            }
        else:
            outcome = _apply(target, body)
    except Exception as exc:
        # Provider errors can carry sensitive request details. Keep only their
        # type here; the existing control path owns its detailed failure log.
        outcome = {"status": "failed", "applied_action": "unknown", "reason": type(exc).__name__}
    outcome.update(policy_id=body["policy_id"], evidence_url=body["evidence_url"])
    with transaction.atomic():
        row.status, row.outcome = outcome["status"], outcome
        row.save(update_fields=["status", "outcome", "updated_at", "version"])
        AuditEvent.objects.create(
            organization_id=connection.organization_id,
            actor_kind="service",
            actor_id=connection.zentinelle_install_id,
            action="agents.policy.enforcement",
            decision="ALLOW" if outcome["status"] in {"applied", "observed"} else "DENY",
            target_kind=body["target_kind"],
            target_id=str(target.guid),
            data={
                "action_id": str(row.external_id),
                "policy_id": body["policy_id"],
                "policy_name": body["policy_name"],
                "evidence_url": body["evidence_url"],
                "requested_action": body["action"],
                **outcome,
            },
        )
        if isinstance(target, AgentTask):
            from astrolift_agents.models import AgentInteraction

            AgentInteraction.objects.create(
                organization_id=target.organization_id,
                agent_task=target,
                kind=AgentInteraction.Kind.CONTROL_API,
                name="zentinelle_policy",
                status=outcome["status"],
                detail={
                    "action_id": str(row.external_id),
                    "policy_id": body["policy_id"],
                    "policy_name": body["policy_name"],
                    "evidence_url": body["evidence_url"],
                    **outcome,
                },
                occurred_at=timezone.now(),
            )
    if isinstance(target, AgentTask):
        _annotate(target, row)
    else:
        _annotate_box(target, row)
    return outcome


def _revoke(target):
    outcome = revoke_agent_key(
        connection=target.model_gateway_connection, agent_id=target.model_gateway_agent_id
    )
    if str(outcome) not in {"revoked", "unknown_agent"}:
        raise AgentEnforcementError("Gateway key revocation was not confirmed", 502)
    if isinstance(target, AgentBox):
        target.model_gateway_expires_at = timezone.now()
        target.save(update_fields=["model_gateway_expires_at", "updated_at", "version"])
    return {"status": "applied", "applied_action": "revoke_key"}


def _stop(target):
    if isinstance(target, AgentBox):
        from astrolift_agents.services.agent_box import stop_agent_box

        stop_agent_box(target, reason="Zentinelle enforcement", require_teardown=True)
        return {"status": "applied", "applied_action": "stop_box"}
    from astrolift_workflows.activities.agent_stage import _cancel_agent_task_sync

    target.refresh_from_db()
    if target.status in AgentTask.TERMINAL_STATUSES:
        return {"status": "applied", "applied_action": "stop_task", "reason": "Task is already terminal"}
    result = _cancel_agent_task_sync(str(target.guid))
    return {
        "status": "applied" if result.get("ok") else "failed",
        "applied_action": "stop_task",
        "reason": str(result.get("error") or "")[:1000],
    }


def _pending_gate(target, body):
    if not isinstance(target, AgentTask):
        return None
    from astrolift_agents.models import AgentTaskInputReply

    events = AgentTaskEvent.objects.filter(
        agent_task=target, turn_id=body["turn_id"], request__isnull=False
    ).filter(Q(message_id=body["tool_call_id"]) | Q(data__call_id=body["tool_call_id"]))
    answered = set(
        AgentTaskInputReply.objects.filter(request_event__agent_task=target).values_list(
            "request_event__sequence", flat=True
        )
    )
    return next(
        (
            event
            for event in events
            if event.sequence not in answered and event.request.get("kind") == "approval"
        ),
        None,
    )


def _apply(target, body):
    action, level = body["action"], body["block_level"]
    if action in {"log", "alert", "warn"}:
        if action == "alert":
            _alert(target, body)
        return {"status": "applied", "applied_action": action}
    if action == "steer" and isinstance(target, AgentTask):
        from astrolift_agents.services.agent_task_input import queue_agent_task_input

        queue_agent_task_input(
            task=target,
            message=body["message"],
            author_label=f'Zentinelle: {body["policy_name"]}',
            client_request_id=body["action_id"],
        )
        return {"status": "applied", "applied_action": "steer"}
    if action == "require_approval" and _pending_gate(target, body) is not None:
        return {
            "status": "applied",
            "applied_action": "require_approval",
            "reason": "The reported tool is already held for approval",
        }
    if action == "block" and level == "tool_call":
        gate = _pending_gate(target, body)
        if gate is not None:
            from astrolift_agents.services.agent_task_requests import reply_to_request

            reply_to_request(
                task=target,
                sequence=gate.sequence,
                response={"decision": "deny", "reason": body["reason"]},
                author=None,
            )
            return {"status": "applied", "applied_action": "block_tool"}
    if action == "block" and level == "quarantine":
        kind = body.get("quarantine_target", "agent")
        subject = target.agent_definition if kind == "agent" else target.environment_spec
        if kind not in {"agent", "spec"} or subject is None:
            raise AgentEnforcementError("Quarantine target is unavailable")
        AgentDispatchQuarantine.objects.get_or_create(
            organization_id=target.organization_id,
            target_kind=kind,
            target_guid=subject.guid,
            cleared_at__isnull=True,
            defaults={
                "reason": body["reason"],
                "policy_id": body["policy_id"],
                "evidence_url": body["evidence_url"],
            },
        )
        stopped = _stop(target)
        stopped.update(applied_action=f"quarantine_{kind}")
        return stopped
    if action == "block" and level == "stop":
        result = _stop(target)
        return result
    try:
        result = _revoke(target)
    except AgentEnforcementError:
        result = _stop(target)
        result["reason"] = "Model-key revocation was unavailable; attempted the existing stop control"
        return result
    if not (action == "block" and level == "revoke_key"):
        result["reason"] = "The requested harness control is unavailable; revoked model access instead"
    return result


def assert_not_quarantined(target):
    agent = getattr(target, "agent_definition", None) or getattr(target, "agent", None)
    spec = target.environment_spec
    query = Q()
    if agent:
        query |= Q(target_kind="agent", target_guid=agent.guid)
    if spec:
        query |= Q(target_kind="spec", target_guid=spec.guid)
    if not (agent or spec):
        return
    if AgentDispatchQuarantine.objects.filter(
        query, organization_id=target.organization_id, cleared_at__isnull=True
    ).exists():
        raise ValueError(
            "Agent or environment spec is quarantined; an agent.dispatch controller must clear it"
        )


@transaction.atomic
def _annotate(task, receipt):
    from astrolift_agents.models import AgentHostAction, AgentHostAuthority
    from astrolift_agents.services import agent_host_projection as projection

    authority, _ = AgentHostAuthority.objects.get_or_create(organization_id=task.organization_id)
    authority = AgentHostAuthority.objects.select_for_update().get(pk=authority.pk)
    if AgentHostAction.objects.filter(
        authority=authority, agent_task=task, action__part__id=f"policy-{receipt.guid}"
    ).exists():
        return
    task = AgentTask.objects.get(pk=task.pk)
    task.save(update_fields=["updated_at", "version"])
    state = projection.project_locked(authority, task)
    active = state.chat.get("activeTurn")
    turn = active["id"] if active else f"policy-{receipt.guid.hex}"
    if active is None:
        projection._emit(
            authority,
            task,
            state,
            {
                "type": "chat/turnStarted",
                "turnId": turn,
                "startedAt": timezone.now().isoformat(),
                "message": {"text": "Zentinelle policy outcome", "origin": {"kind": "agent"}},
            },
        )
    policy, outcome = receipt.payload, receipt.outcome
    text = f'Zentinelle [{policy["policy_name"] or policy["policy_id"]}]: {outcome["applied_action"]} ({outcome["status"]}). {outcome.get("reason", "")} Evidence: {policy["evidence_url"]}'
    projection._emit(
        authority,
        task,
        state,
        {
            "type": "chat/responsePart",
            "turnId": turn,
            "part": {
                "kind": "markdown",
                "id": f"policy-{receipt.guid}",
                "content": text,
                "_meta": {
                    "zentinelle.policyId": policy["policy_id"],
                    "zentinelle.evidence": policy["evidence_url"],
                    "zentinelle.actionId": str(receipt.external_id),
                },
            },
        },
    )
    if active is None:
        projection._emit(authority, task, state, {"type": "chat/turnComplete", "turnId": turn, "duration": 0})
    state.task_version = task.version
    state.save()
    authority.save()


def _alert(target, body):
    """Notify the current owner and authorized controllers through their inbox."""
    from astrolift_agents.visibility import agent_tasks, agent_workloads
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import Member
    from astrolift_operations.models import Notification
    from core.permissions import Permission
    from core.tenancy import TenantContext, tenant_context

    owner = target.triggered_by_user_id if isinstance(target, AgentTask) else target.owner_id
    members = Member.objects.filter(
        scope_kind="ORG",
        scope_id=target.organization_id,
        is_active=True,
        lifecycle="active",
        user__is_active=True,
    ).select_related("user")
    marker = set_current_api_token(None)
    try:
        for member in members:
            with tenant_context(
                TenantContext(organization_id=target.organization_id, actor_user_id=member.user_id)
            ):
                if isinstance(target, AgentTask):
                    controller = (
                        agent_tasks(target.organization_id, Permission.AGENT_DISPATCH)
                        .filter(pk=target.pk)
                        .exists()
                    )
                    readable = (
                        agent_tasks(target.organization_id, Permission.AGENT_READ)
                        .filter(pk=target.pk)
                        .exists()
                    )
                elif target.agent_definition_id:
                    controller = (
                        agent_workloads(target.organization_id, Permission.AGENT_DISPATCH)
                        .filter(pk=target.agent_definition_id)
                        .exists()
                    )
                    readable = (
                        agent_workloads(target.organization_id, Permission.AGENT_READ)
                        .filter(pk=target.agent_definition_id)
                        .exists()
                    )
                else:
                    from core.permissions import PermissionScope, ScopeKind

                    scope = PermissionScope(kind=ScopeKind.ORG, id=target.organization_id)
                    controller = _allowed(Permission.AGENT_DISPATCH, scope)
                    readable = _allowed(Permission.AGENT_READ, scope)
                if controller or (member.user_id == owner and readable):
                    Notification.objects.create(
                        organization_id=target.organization_id,
                        user=member.user,
                        kind="system",
                        title=f'Agent policy alert: {body["policy_name"]}'[:255],
                        body=f'{body["message"] or body["reason"]} Evidence: {body["evidence_url"]}',
                        link=f"/agents/runs/{target.guid}"
                        if isinstance(target, AgentTask)
                        else "/agents/boxes",
                    )
    finally:
        reset_current_api_token(marker)


def poll_enforcements(target):
    """Apply the signed outbox without requiring any attached AHP client.

    An action remains pending at Zentinelle until its recorded local outcome is
    acknowledged. A new delivery nonce permits recovery of a lost receipt while
    the stable action identity prevents repeating external effects.
    """
    from django.core.cache import cache

    connection = target.model_gateway_connection
    if not connection or connection.status != "connected" or not target.model_gateway_agent_id:
        return
    if not cache.add(f"agent-policy-poll:{target.guid}", True, timeout=2):
        return
    path = f'/agents/{quote(target.model_gateway_agent_id, safe="")}/enforcements'
    for tenant in connection.tenant_ids:
        try:
            reply = _install_call(connection, "GET", path + "?tenant_id=" + quote(tenant, safe=""), timeout=5)
            if not reply.ok or not isinstance(reply.body.get("actions"), list):
                continue
            for delivery in reply.body["actions"][:50]:
                body = delivery.get("body") if isinstance(delivery, dict) else None
                if not isinstance(body, dict) or body.get("target_id") != str(target.guid):
                    continue
                try:
                    outcome = apply_signed(connection, body, delivery.get("signature"))
                except AgentEnforcementError as exc:
                    if exc.status == 409:
                        continue
                    log.warning("Refused install policy action for %s: %s", target.guid, type(exc).__name__)
                    continue
                _install_call(
                    connection,
                    "POST",
                    path + "/" + body["action_id"] + "/outcome",
                    {"tenant_id": tenant, "outcome": outcome},
                    timeout=5,
                )
        except Exception:
            # The durable action stays pending; never infer a successful stop
            # or approval from an unavailable control-plane connection.
            log.warning("Policy delivery failed for target %s", target.guid, exc_info=True)


def _allowed(permission, scope):
    from core.permissions import PermissionDenied, check_permission

    try:
        check_permission(permission, scope=scope)
        return True
    except PermissionDenied:
        return False


def visible_quarantines(org_id):
    from astrolift_agents.visibility import agent_workloads, environment_specs
    from core.permissions import Permission

    agents = agent_workloads(org_id, Permission.AGENT_DISPATCH).values_list("guid", flat=True)
    specs = environment_specs(org_id, Permission.AGENT_DISPATCH).values_list("guid", flat=True)
    return AgentDispatchQuarantine.objects.filter(
        Q(target_kind="agent", target_guid__in=agents) | Q(target_kind="spec", target_guid__in=specs),
        organization_id=org_id,
        cleared_at__isnull=True,
    )


def quarantine_scope(args):
    from astrolift_agents.scopes import agent_org_scope
    from astrolift_agents.visibility import (
        agent_workloads,
        app_permission_scope,
        check_org_shared_spec_write,
        environment_specs,
        spec_owner_scope,
    )
    from core.permissions import Permission, ScopeKind
    from core.scope_args import read_guid
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    row = visible_quarantines(org_id).filter(guid=read_guid(args, "id")).first()
    if row and row.target_kind == "agent":
        workload = agent_workloads(org_id, Permission.AGENT_DISPATCH).filter(guid=row.target_guid).first()
        if workload:
            return app_permission_scope(workload.registered_app, Permission.AGENT_DISPATCH)
    if row and row.target_kind == "spec":
        spec = environment_specs(org_id, Permission.AGENT_DISPATCH).filter(guid=row.target_guid).first()
        if spec:
            scope = spec_owner_scope(spec)
            if scope.kind == ScopeKind.ORG:
                return check_org_shared_spec_write(org_id, Permission.AGENT_DISPATCH)
            return scope
    return agent_org_scope(args)


@transaction.atomic
def _annotate_box(box, receipt):
    from astrolift_agents.models import AgentHostAction, AgentHostAuthority, AgentHostTerminal
    from astrolift_agents.services.agent_host_projection import append_action
    from astrolift_agents.services.agent_host_terminal import MAX_CONTENT

    authority, _ = AgentHostAuthority.objects.get_or_create(organization_id=box.organization_id)
    authority = AgentHostAuthority.objects.select_for_update().get(pk=authority.pk)
    policy, outcome = receipt.payload, receipt.outcome
    description = f'\r\n[Zentinelle: {policy["policy_name"] or policy["policy_id"]}] {outcome["applied_action"]} ({outcome["status"]}). {outcome.get("reason", "")} Evidence: {policy["evidence_url"]}\r\n'
    text = "".join(ch for ch in description if ch.isprintable() or ch in "\r\n")
    for view in AgentHostTerminal.objects.select_for_update().filter(authority=authority, agent_box=box):
        if AgentHostAction.objects.filter(
            authority=authority,
            channel=view.channel,
            action___meta__zentinelle_action_id=str(receipt.external_id),
        ).exists():
            continue
        previous = "".join(part.get("value", "") for part in view.state.get("content", []))
        view.state["content"] = [{"type": "unclassified", "value": (previous + text)[-MAX_CONTENT:]}]
        view.save()
        event = append_action(
            authority,
            None,
            view.channel,
            {
                "type": "terminal/data",
                "data": text,
                "_meta": {
                    "zentinelle_action_id": str(receipt.external_id),
                    "policy_id": policy["policy_id"],
                    "evidence_url": policy["evidence_url"],
                },
            },
        )
        event.agent_box = box
        event.save(update_fields=["agent_box"])
    authority.save()
