"""Turn persisted audit entries into ``AUDIT.*`` events for Zentinelle.

``zentinelle_integration`` locked the wire vocabulary (``AUDIT.app.deploy``
and friends) and the subscription template, and ``webhook_fanout`` routes
emitted events to subscriptions, but nothing ever emitted an event with one
of those types: ``mutation_audit`` entries only ever reached the
``AuditEvent`` table. A Zentinelle subscription therefore listed thirteen
event types and delivered zero.

This module is the missing join. After an audit row is written, the entry's
action is mapped onto the Zentinelle catalog; a mapped, allowed, org-scoped
entry is emitted through ``core.events.Event`` wrapped in the locked
``ZentinelleEnvelope`` so the generic webhook format can POST it as-is.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any

from django.db import transaction

from astrolift_operations.zentinelle_integration import (
    PAYLOAD_VERSION,
    REQUIRED_PAYLOAD_KEYS,
    ZentinelleEnvelope,
    ZentinelleEventType,
    idempotency_key_for,
    validate_payload,
)
from core.mutations import AuditEntry

log = logging.getLogger("astrolift_operations.zentinelle_bridge")

_T = ZentinelleEventType

# Audit action -> catalog type. Only actions whose meaning matches the
# catalog entry are listed; everything else stays a plain audit row.
ACTION_EVENT_TYPES: dict[str, ZentinelleEventType] = {
    "role_binding.grant": _T.ROLE_BINDING_GRANT,
    "role_binding.revoke": _T.ROLE_BINDING_REVOKE,
    "invitation.create": _T.USER_INVITED,
    "invitation.accept": _T.USER_ACCEPTED,
    "identity.user.anonymized": _T.USER_DEACTIVATED,
    "app.deploy": _T.APP_DEPLOY,
    "deployment.start": _T.APP_DEPLOY,
    "deployment.approve": _T.APP_DEPLOY,
    "deployment.approve_by_token": _T.APP_DEPLOY,
    "app.force_redeploy": _T.APP_DEPLOY,
    "deployment.redeploy": _T.APP_DEPLOY,
    "deployment.promote": _T.APP_DEPLOY,
    "deployment.rollback": _T.APP_DEPLOY,
    "app.create": _T.APP_REGISTERED,
    "app.register_app_repo": _T.APP_REGISTERED,
    "app.register_agent_repo": _T.APP_REGISTERED,
    "app.deregister": _T.APP_DEREGISTERED,
    "app.delete": _T.APP_DEREGISTERED,
    "app.tear_down": _T.APP_DEREGISTERED,
    "app.secret.rotate": _T.SECRET_ROTATED,
    "secret_bundle.rotate": _T.SECRET_ROTATED,
    "app.deploy_token.rotate": _T.SECRET_ROTATED,
    "webhook.rotate_secret": _T.SECRET_ROTATED,
    "scm.webhook.rotate": _T.SECRET_ROTATED,
    "app.secret.reveal": _T.SECRET_VIEWED,
    "agents.secret.reveal": _T.SECRET_VIEWED,
    "agents.secret.bundle.key.reveal": _T.SECRET_VIEWED,
    "project.secret.bundle.key.reveal": _T.SECRET_VIEWED,
    "managed_service.connection.reveal": _T.SECRET_VIEWED,
    "org.update": _T.ORG_SETTINGS_UPDATED,
    "observability.retention_hold.place": _T.OBSERVABILITY_PROFILE_UPDATED,
    "observability.retention_hold.release": _T.OBSERVABILITY_PROFILE_UPDATED,
    "agents.task.launch": _T.AGENT_TASK_LAUNCHED,
    "agents.agent.dispatch": _T.AGENT_DISPATCHED,
    "agents.env_spec.create": _T.AGENT_ENV_SPEC_CHANGED,
    "agents.env_spec.update": _T.AGENT_ENV_SPEC_CHANGED,
    "agents.env_spec.delete": _T.AGENT_ENV_SPEC_CHANGED,
    "workflow.stage.changed": _T.AGENT_WORKFLOW_STAGE_CHANGED,
    "workflow.gate.decided": _T.AGENT_WORKFLOW_GATE_DECIDED,
    "agents.model_spend": _T.AGENT_MODEL_SPEND,
}


def event_type_for_action(action: str) -> ZentinelleEventType | None:
    return ACTION_EVENT_TYPES.get(action)


def _secret_kind(kind: str) -> bool:
    return "secret" in kind.lower()


def build_payload(entry: AuditEntry, event_type: ZentinelleEventType) -> dict[str, Any]:
    """Best-effort catalog payload. Every required key is present; a key the
    audit entry cannot supply is ``None`` rather than invented."""
    extra = dict(entry.extra or {})
    kind = (entry.target_kind or "").strip()
    target = entry.target_id
    payload: dict[str, Any] = {
        "action": entry.action,
        "decision": entry.decision,
        "target_kind": kind or None,
        "target_id": str(target) if target is not None else None,
        "permissions": list(entry.permissions),
        **extra,
    }
    known: dict[str, Any] = {
        "app_slug": extra.get("app_slug")
        or (str(target) if kind.lower() in ("app", "registeredapp") else None),
        "repo_url": extra.get("repo_url") or (str(target) if kind == "repo" else None),
        "secret_path": extra.get("secret_path") or (str(target) if _secret_kind(kind) else None),
        "rotation_kind": extra.get("rotation_kind") or "manual",
        "subject_user_id": extra.get("subject_user_id") or (str(target) if kind.lower() == "user" else None),
        "invited_by_user_id": extra.get("invited_by_user_id") or entry.actor_user_id,
        "fields_changed": extra.get("fields_changed") or [],
        "task_id": extra.get("task_id") or (str(target) if "task" in kind.lower() else None),
        "agent_slug": extra.get("agent_slug") or extra.get("workload_slug"),
        "status": extra.get("status") or entry.action.rsplit(".", 1)[-1],
        "dispatcher": extra.get("dispatcher") or "astrolift",
        "env_spec_slug": extra.get("env_spec_slug") or extra.get("slug"),
        "workflow_run_id": extra.get("workflow_run_id"),
        "stage_id": extra.get("stage_id") or extra.get("stage_slug"),
        "provider": extra.get("provider"),
        "model_id": extra.get("model_id") or extra.get("model"),
        "input_tokens": extra.get("input_tokens", 0),
        "output_tokens": extra.get("output_tokens", 0),
        "estimated_usd": extra.get("estimated_usd", 0),
    }
    for key in REQUIRED_PAYLOAD_KEYS[event_type]:
        payload.setdefault(key, known.get(key))
    return payload


def build_envelope(
    entry: AuditEntry, *, event_id: str, occurred_at_unix: int | None = None
) -> dict[str, Any] | None:
    """The locked wire envelope for ``entry``, or ``None`` when the entry
    is not Zentinelle evidence (unmapped action, denied, no org)."""
    event_type = event_type_for_action(entry.action)
    if event_type is None or entry.decision != "ALLOW" or not entry.organization_id:
        return None
    org_id = int(entry.organization_id)
    payload = build_payload(entry, event_type)
    validate_payload(event_type=event_type, payload=payload)
    envelope = ZentinelleEnvelope(
        payload_version=PAYLOAD_VERSION,
        event_type=event_type.value,
        event_id=event_id,
        org_id=org_id,
        actor_user_id=entry.actor_user_id,
        occurred_at_unix=occurred_at_unix or int(time.time()),
        payload=payload,
        idempotency_key=idempotency_key_for(org_id=org_id, event_id=event_id),
    )
    return dataclasses.asdict(envelope)


def bridge_audit_entry(entry: AuditEntry) -> bool:
    """Emit the entry as an ``AUDIT.*`` event when it is catalog evidence.
    Never raises: audit persistence must not depend on the bridge."""
    try:
        from core.events import Event
        from core.fields import uuid7

        envelope = build_envelope(entry, event_id=str(uuid7()))
        if envelope is None:
            return False
        # Savepoint: a failed event insert must not poison the mutation's
        # transaction the audit row was just written in.
        with transaction.atomic():
            Event.emit(
                envelope["event_type"],
                envelope,
                resource_kind=entry.target_kind or "",
                resource_id=entry.target_id,
                actor_user_id=entry.actor_user_id,
                organization_id=int(entry.organization_id),
            )
        return True
    except Exception:  # noqa: BLE001 - evidence bridge is best-effort
        log.warning("zentinelle bridge failed for %s", entry.action, exc_info=True)
        return False


def emit_agent_task_transition(task: Any, status: str) -> None:
    """Emit durable execution evidence for model state transitions.

    Task transitions happen in workers, outside GraphQL mutation audit hooks,
    so they need an explicit bridge call. Failures are isolated from the task
    state machine; the audit row and task status remain authoritative.
    """
    try:
        from core.events import Event
        from core.fields import uuid7

        definition = getattr(task, "agent_definition", None)
        agent_slug = getattr(definition, "slug", None) or "unknown"
        dispatcher = getattr(task, "dispatcher", None)
        dispatcher_id = getattr(dispatcher, "guid", None) or getattr(dispatcher, "name", None)
        base = {
            "task_id": str(task.guid),
            "agent_slug": agent_slug,
            "status": status,
            "dispatcher": str(dispatcher_id or dispatcher or "astrolift"),
        }
        event_type = {
            "completed": _T.AGENT_TASK_COMPLETED,
            "failed": _T.AGENT_TASK_FAILED,
        }.get(status, _T.AGENT_TASK_LAUNCHED)
        events = [(event_type, base)]
        if status == "completed":
            result = task.result if isinstance(task.result, dict) else {}
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else result
            events.append(
                (
                    _T.AGENT_MODEL_SPEND,
                    {
                        "task_id": str(task.guid),
                        "provider": usage.get("provider") or "unknown",
                        "model_id": usage.get("model_id") or usage.get("model") or "unknown",
                        "input_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
                        "output_tokens": int(
                            usage.get("output_tokens") or usage.get("completion_tokens") or 0
                        ),
                        "estimated_usd": usage.get("estimated_usd", usage.get("cost_usd", 0)),
                    },
                )
            )
        for kind, payload in events:
            validate_payload(event_type=kind, payload=payload)
            event_id = str(uuid7())
            envelope = dataclasses.asdict(
                ZentinelleEnvelope(
                    payload_version=PAYLOAD_VERSION,
                    event_type=kind.value,
                    event_id=event_id,
                    org_id=int(task.organization_id),
                    actor_user_id=None,
                    occurred_at_unix=int(time.time()),
                    payload=payload,
                    idempotency_key=idempotency_key_for(org_id=int(task.organization_id), event_id=event_id),
                )
            )
            # Keep a failed event insert inside a savepoint. Event emission is
            # best-effort, but a broken inner transaction must not poison the
            # task transition or the request that triggered it.
            with transaction.atomic():
                Event.emit(
                    kind.value,
                    envelope,
                    resource_kind="agent_task",
                    resource_id=str(task.guid),
                    organization_id=int(task.organization_id),
                )
    except Exception:  # noqa: BLE001 - telemetry cannot break execution
        log.warning("agent task evidence bridge failed", exc_info=True)


def is_zentinelle_envelope(event_type: str, payload: Any) -> bool:
    """Does an emitted event carry a ready-to-post Zentinelle envelope?"""
    return (
        str(event_type or "").startswith("AUDIT.")
        and isinstance(payload, dict)
        and "idempotency_key" in payload
        and "payload_version" in payload
    )
