"""Encrypted completion outbox; workflow history carries only row identities."""

from __future__ import annotations

import logging
import math
import random
import re
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from astrolift_agents.completion_webhook import (
    RETRY_WINDOW_SECONDS,
    CallbackConfigurationError,
    classify_response,
    encoded_body,
    normalize_allowed_hosts,
    retry_delay,
    validate_destination,
)
from astrolift_agents.models import AgentTask, AgentTaskCallbackPolicy, AgentTaskCompletionCallback
from astrolift_lifecycle.services.secrets import read_org_secret, write_org_secret
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

logger = logging.getLogger(__name__)
SECRET_PREFIX = "astrolift/agent-callbacks/"
TERMINAL = frozenset({"completed", "failed", "cancelled", "timed_out"})
SECRET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def configure_policy(org_id: int, allowed_hosts: list[str]):
    hosts = normalize_allowed_hosts(allowed_hosts)
    with transaction.atomic():
        from astrolift_identity.models import Organization

        Organization.objects.select_for_update().get(pk=org_id)
        row, _ = AgentTaskCallbackPolicy.all_objects.get_or_create(organization_id=org_id)
        row.allowed_hosts = hosts
        row.deleted_at = None
        row.deleted_by_id = None
        row.save()
    return row


def _secret_name(name: str) -> str:
    if not isinstance(name, str) or not SECRET_NAME.fullmatch(name):
        raise CallbackConfigurationError("callbackSecretRef must be a secret name of at most 128 characters")
    return SECRET_PREFIX + name


def set_callback_secret(org_id: int, name: str, value: str) -> None:
    key = _secret_name(name)
    if not isinstance(value, str) or not 32 <= len(value.encode("utf-8")) <= 4096:
        raise CallbackConfigurationError("signing keys must contain between 32 and 4096 UTF-8 bytes")
    write_org_secret(org_id, key, value)


def callback_configuration(
    org_id: int, *, callback_url=None, callback_secret_ref=None, correlation_id=None, callback_mode="FULL"
) -> dict | None:
    if callback_url is None:
        if callback_secret_ref is not None or correlation_id is not None or callback_mode != "FULL":
            raise CallbackConfigurationError("callbackUrl is required when callback options are supplied")
        return None
    if callback_mode not in AgentTaskCompletionCallback.Mode.values:
        raise CallbackConfigurationError("callbackMode must be FULL or NOTIFY")
    if correlation_id is not None and (not isinstance(correlation_id, str) or len(correlation_id) > 128):
        raise CallbackConfigurationError("correlationId must contain at most 128 characters")
    secret_key = _secret_name(callback_secret_ref)
    policy = AgentTaskCallbackPolicy.objects.filter(organization_id=org_id).first()
    validate_destination(callback_url, policy.allowed_hosts if policy else [])
    if not read_org_secret(org_id, secret_key):
        raise CallbackConfigurationError("callback signing secret is missing or unavailable")
    return {
        "callback_url": callback_url,
        "secret_ref": callback_secret_ref,
        "correlation_id": correlation_id or "",
        "mode": callback_mode,
    }


def normalized_usage(value: object) -> dict:
    source = value if isinstance(value, dict) else {}
    usage = {"input_tokens": None, "output_tokens": None, "total_cost_usd": None, "model": None}
    for key in ("input_tokens", "output_tokens"):
        item = source.get(key)
        if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 2**63 - 1:
            usage[key] = item
    cost = source.get("total_cost_usd")
    if (
        isinstance(cost, (int, float))
        and not isinstance(cost, bool)
        and 0 <= cost <= 1e15
        and math.isfinite(cost)
    ):
        usage["total_cost_usd"] = cost
    model = source.get("model")
    if isinstance(model, str) and len(model) <= 256 and not any(ord(char) < 32 for char in model):
        usage["model"] = model
    return usage


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def _metadata(task, row):
    usage = normalized_usage(task.completion_usage)
    usage["duration_ms"] = (
        max(0, int((task.ended_at - task.started_at).total_seconds() * 1000))
        if task.started_at and task.ended_at
        else None
    )
    attempt = 1
    if task.agent_run_id:
        from astrolift_agents.models.task_meter import TaskMeteringRecord

        meter = TaskMeteringRecord.objects.filter(agent_run_id=task.agent_run_id).first()
        if meter:
            for output, field in (("input_tokens", "token_input"), ("output_tokens", "token_output")):
                if usage[output] is None:
                    usage[output] = getattr(meter, field)
        attempt = max(1, task.agent_run.retry_count + 1)
    result = task.result if isinstance(task.result, dict) else {}
    exit_code = result.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        exit_code = 0 if task.status == AgentTask.Status.COMPLETED else None
    return {
        "_result_present": task.result is not None,
        "_failure_present": task.failure is not None,
        "event": "agent_task.finished",
        "task_id": str(task.guid),
        "correlation_id": row.correlation_id,
        "agent_slug": task.agent_definition.slug if task.agent_definition_id else "",
        "status": task.status,
        "exit_code": exit_code,
        "attempt": attempt,
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.ended_at),
        "usage": usage,
    }


def _body(task, row):
    body = {key: value for key, value in row.event_metadata.items() if not key.startswith("_")}
    body["failure_message"] = None
    if row.mode == AgentTaskCompletionCallback.Mode.FULL:
        body["result"] = task.result
        if task.status == "failed" and task.result is None and isinstance(task.failure, dict):
            body["result"] = task.failure.get("output")
        if isinstance(task.failure, dict):
            message = task.failure.get("message") or task.failure.get("error")
            body["failure_message"] = message if isinstance(message, str) else None
    return encoded_body(body)


def _queue(row, task, now):
    sealed = encrypt_at_rest(_body(task, row))
    row.payload_backend_kind = sealed.backend_kind
    row.payload_ciphertext = sealed.backend_ref
    row.generation += 1
    row.status = AgentTaskCompletionCallback.Status.PENDING
    row.attempts = 0
    row.last_error = ""
    row.retry_started_at = now
    row.retry_deadline = now + timedelta(seconds=RETRY_WINDOW_SECONDS)
    row.next_attempt_at = now
    row.delivered_at = None
    row.lease_id = None
    row.lease_until = None
    row.save()
    transaction.on_commit(lambda: enqueue_callback(row.pk, row.generation))


def freeze_callback(task) -> None:
    """Called inside the task's locked terminal transition transaction."""
    if task.status not in TERMINAL:
        return
    row = AgentTaskCompletionCallback.objects.select_for_update().filter(task_id=task.pk).first()
    if row is None or row.final_event_id is not None:
        return
    row.final_event_id = uuid.uuid4()
    row.event_metadata = _metadata(task, row)
    _queue(row, task, timezone.now())


def redeliver_callback(task):
    with transaction.atomic():
        task = AgentTask.objects.select_for_update().get(pk=task.pk)
        row = AgentTaskCompletionCallback.objects.select_for_update().filter(task_id=task.pk).first()
        if row is None or row.final_event_id is None or task.status not in TERMINAL:
            raise CallbackConfigurationError("task has no final callback event to replay")
        now = timezone.now()
        if row.status == AgentTaskCompletionCallback.Status.PENDING:
            raise CallbackConfigurationError("callback delivery is already pending")
        retention_hours = task.agent_run.result_ttl_hours if task.agent_run_id else 72
        if not task.ended_at or now >= task.ended_at + timedelta(hours=retention_hours):
            raise CallbackConfigurationError("task result retention has expired")
        if task.agent_run_id and task.agent_run.deleted_at is not None:
            raise CallbackConfigurationError("task result is unavailable")
        if (
            row.mode == AgentTaskCompletionCallback.Mode.FULL
            and task.status == "completed"
            and task.result is None
        ):
            raise CallbackConfigurationError("task result is unavailable")
        policy = AgentTaskCallbackPolicy.objects.filter(organization_id=row.organization_id).first()
        validate_destination(row.callback_url, policy.allowed_hosts if policy else [])
        if _source_unavailable(task, row, now):
            raise CallbackConfigurationError("task result is unavailable")
        _queue(row, task, now)
    return row


def enqueue_callback(callback_id: int, generation: int) -> bool:
    from astrolift_workflows.client import start_workflow

    row = (
        AgentTaskCompletionCallback.objects.filter(pk=callback_id, generation=generation)
        .select_related("task")
        .first()
    )
    if row is None or row.status != AgentTaskCompletionCallback.Status.PENDING or not row.final_event_id:
        return False
    try:
        handle = start_workflow(
            "DeliverAgentTaskCallbackWorkflow",
            args=[callback_id, generation],
            workflow_id=f"DeliverAgentTaskCallbackWorkflow-{row.task.guid}-{generation}",
            replace_running=False,
        )
        return bool(handle.enqueued)
    except Exception:  # noqa: BLE001 — the encrypted outbox and reconciler preserve the event
        return False


def _purge(row):
    row.payload_ciphertext = b""
    row.payload_backend_kind = ""
    row.next_attempt_at = None
    row.lease_id = None
    row.lease_until = None


def _source_unavailable(task, row, now) -> bool:
    if row.mode != AgentTaskCompletionCallback.Mode.FULL:
        return False
    run = task.agent_run if task.agent_run_id else None
    ttl = run.result_ttl_hours if run else 72
    return (
        not task.ended_at
        or now >= task.ended_at + timedelta(hours=ttl)
        or bool(run and run.deleted_at)
        or bool(row.event_metadata.get("_result_present") and task.result is None)
        or bool(row.event_metadata.get("_failure_present") and task.failure is None)
    )


def deliver_attempt(callback_id: int, generation: int, *, now=None, jitter=None) -> dict:
    """Lease a row, POST outside the transaction, settle only our generation."""
    from astrolift_agents.services.task_callback_transport import post_completion_callback

    now = now or timezone.now()
    with transaction.atomic():
        row = (
            AgentTaskCompletionCallback.objects.select_for_update(of=("self",))
            .select_related("task", "task__agent_run", "organization")
            .filter(pk=callback_id)
            .first()
        )
        if row is None or row.generation != generation or row.status != "pending" or not row.final_event_id:
            return {"state": "finished"}
        if row.task.deleted_at or row.organization.deleted_at or _source_unavailable(row.task, row, now):
            row.status = "failed"
            row.last_error = "task or organization is unavailable"
            _purge(row)
            row.save()
            return {"state": "finished", "attempts": row.attempts}
        if row.lease_until and row.lease_until > now:
            return {"state": "waiting", "delay_seconds": (row.lease_until - now).total_seconds()}
        if row.next_attempt_at and row.next_attempt_at > now:
            return {"state": "waiting", "delay_seconds": (row.next_attempt_at - now).total_seconds()}
        # The deadline attempt is allowed; recovery after a prolonged worker outage is bounded.
        if now > row.retry_deadline + timedelta(seconds=30):
            row.status = "failed"
            row.last_error = "callback retry window exhausted"
            _purge(row)
            row.save()
            return {"state": "finished", "attempts": row.attempts}
        lease = uuid.uuid4()
        row.lease_id = lease
        row.lease_until = now + timedelta(seconds=45)
        row.attempts += 1
        row.save()
        attempts = row.attempts
        organization_id = row.organization_id
        destination = row.callback_url
        secret_ref = row.secret_ref
        sealed = EncryptedSecret(row.payload_backend_kind, bytes(row.payload_ciphertext))
    code = None
    error = "callback delivery unavailable"
    classification = "retry"
    try:
        policy = AgentTaskCallbackPolicy.objects.filter(organization_id=organization_id).first()
        hosts = policy.allowed_hosts if policy else []
        validate_destination(destination, hosts)
        secret = read_org_secret(organization_id, _secret_name(secret_ref))
        if secret:
            code = post_completion_callback(destination, hosts, decrypt(sealed), secret)
            classification = classify_response(code)
            error = "" if classification == "delivered" else f"callback HTTP {code}"
        else:
            error = "callback signing secret unavailable"
    except CallbackConfigurationError:
        classification = "failed"
        error = "callback destination is no longer permitted"
    except Exception:  # noqa: BLE001 — HTTP/crypto exceptions can contain payloads or addresses
        pass
    settled_at = now if jitter is not None else timezone.now()
    with transaction.atomic():
        row = AgentTaskCompletionCallback.objects.select_for_update().get(pk=callback_id)
        if row.generation != generation or row.lease_id != lease or row.status != "pending":
            return {"state": "finished"}
        row.lease_id = None
        row.lease_until = None
        row.last_error = error
        delay = (
            retry_delay(
                attempts,
                elapsed_seconds=max(0, (settled_at - row.retry_started_at).total_seconds()),
                jitter=random.random() if jitter is None else jitter,
            )
            if classification == "retry"
            else None
        )
        if classification == "delivered":
            row.status = "delivered"
            row.delivered_at = settled_at
            _purge(row)
        elif classification == "failed" or delay is None:
            row.status = "failed"
            if classification == "retry":
                row.last_error = "callback retry window exhausted"
            _purge(row)
        else:
            row.next_attempt_at = settled_at + timedelta(seconds=delay)
        row.save()
        logger.info(
            "agent task callback attempt",
            extra={
                "task_id": str(row.task.guid),
                "correlation_id": row.correlation_id,
                "status": row.task.status,
                "http_code": code,
                "attempt": attempts,
            },
        )
        return (
            {"state": "retry", "delay_seconds": delay, "attempts": attempts}
            if delay is not None
            else {"state": "finished", "attempts": attempts}
        )
