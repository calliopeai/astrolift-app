"""Temporary, bounded pod-log snapshots; this is not persisted task history."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from graphql import GraphQLError

from astrolift_agents.schema.types import AgentTaskLogLineType, AgentTaskLogPageType
from astrolift_agents.scopes import agent_task_scope
from astrolift_agents.visibility import agent_tasks
from astrolift_identity.abac import operation_attributes
from core.permissions import Permission, check_permission

SNAPSHOT_LINES = 2000
SNAPSHOT_SECONDS = 300
MAX_PAGE_SIZE = 200
CURSOR_SALT = "astrolift.agent-task-log-page.v1"
_LEVEL = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}T\S+\s+)?(TRACE|DEBUG|DBG|INFO|INF|WARN(?:ING)?|WRN|ERROR|ERR|FATAL)\b", re.I
)
_LEVELS = {
    "trace": "debug",
    "debug": "debug",
    "dbg": "debug",
    "info": "info",
    "inf": "info",
    "warn": "warn",
    "warning": "warn",
    "wrn": "warn",
    "error": "error",
    "err": "error",
    "fatal": "error",
}


def resolve_log_target(org_id, guid):
    """Resolve a visible task and the actual live cluster before any log read."""
    from _sdk.k8s_naming import agent_namespace

    from astrolift_workflows.activities.agent_stage import _resolve_managed_cluster

    task = (
        agent_tasks(org_id, Permission.AGENT_READ)
        .filter(guid=guid, organization_id=org_id)
        .select_related("organization", "dispatcher__tenant_cluster")
        .first()
    )
    if task is None:
        return None
    namespace = task.namespace.strip() or agent_namespace(task.organization.slug)
    if task.dispatch_target:
        # Frozen placement is authoritative; never follow a replacement
        # dispatcher/managed cluster when the original target is unavailable.
        from astrolift_agents.services.task_target import resolve_task_target

        if not isinstance(task.dispatch_target, dict) or task.dispatch_target.get("backend") != "k8s_job":
            return None
        try:
            _, cluster, namespace = resolve_task_target(task)
        except (RuntimeError, ValueError, TypeError, ValidationError):
            return None
        if cluster is None or not cluster.is_active or cluster.deleted_at is not None:
            return None
        return task, cluster, namespace
    dispatcher = task.dispatcher
    if dispatcher is not None and (dispatcher.organization_id != org_id or dispatcher.deleted_at is not None):
        return None
    cluster = dispatcher.tenant_cluster if dispatcher is not None else None
    if cluster is None:
        try:
            cluster = _resolve_managed_cluster(task.organization)
        except Exception:
            return None
    if cluster is None or not cluster.is_active or cluster.deleted_at is not None:
        return None
    if cluster.organization_id not in (None, org_id):
        return None
    return task, cluster, namespace


def _line(line, snapshot, index):
    message = str(line.message)
    match = _LEVEL.match(message)
    level = _LEVELS.get(match.group(1).lower()) if match else None
    if level is None and message.startswith("{"):
        try:
            record = json.loads(message)
            if isinstance(record, dict):
                level = _LEVELS.get(str(record.get("level") or record.get("severity") or "").lower())
        except (ValueError, TypeError):
            pass
    return {
        "id": f"{snapshot}:{index}",
        "timestamp": line.timestamp,
        "level": level,
        "stream": line.stream,
        "message": message,
        "pod_name": line.pod_name,
        "container": line.container,
    }


def empty_log_page(limit):
    return AgentTaskLogPageType(
        items=[],
        next_cursor=None,
        has_more=False,
        page_size=limit,
        live_only=True,
        window_limited=False,
        expires_at=None,
    )


def task_log_page(org_id, guid, *, cursor=None, limit=100):
    from core.cluster_observability import fetch_task_pod_logs

    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    target = resolve_log_target(org_id, guid)
    if target is None:
        return empty_log_page(limit)
    task, cluster, namespace = target
    with operation_attributes(region=cluster.region or None):
        check_permission(Permission.AGENT_READ, scope=agent_task_scope("id")({"id": str(task.guid)}))
    identity = [str(task.organization.guid), str(task.guid), str(cluster.guid), namespace]
    if cursor:
        try:
            marker = signing.loads(cursor, salt=CURSOR_SALT, max_age=SNAPSHOT_SECONDS)
            if marker["identity"] != identity:
                raise ValueError("cursor target changed")
            snapshot, end = marker["snapshot"], marker["end"]
            if not isinstance(snapshot, str) or not isinstance(end, int) or end < 0:
                raise ValueError("invalid page boundary")
        except signing.SignatureExpired as exc:
            raise GraphQLError(
                "Task log page expired; refresh the log.", extensions={"code": "LOG_CURSOR_EXPIRED"}
            ) from exc
        except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
            raise GraphQLError("Invalid task log cursor.", extensions={"code": "LOG_CURSOR_INVALID"}) from exc
        payload = cache.get(f"agent-task-log-page:{snapshot}")
        if payload is None:
            raise GraphQLError(
                "Task log page expired; refresh the log.", extensions={"code": "LOG_CURSOR_EXPIRED"}
            )
        if payload["identity"] != identity or end > len(payload["items"]):
            raise GraphQLError("Invalid task log cursor.", extensions={"code": "LOG_CURSOR_INVALID"})
    else:
        lines = async_to_sync(fetch_task_pod_logs)(
            cluster=cluster,
            namespace=namespace,
            task_guid=str(task.guid),
            pod_name_hint=task.pod_name or "",
            tail=SNAPSHOT_LINES,
            structured=True,
        )
        if not lines:
            return empty_log_page(limit)
        snapshot = uuid4().hex
        payload = {
            "identity": identity,
            "items": [_line(line, snapshot, index) for index, line in enumerate(lines)],
            "window_limited": len(lines) >= SNAPSHOT_LINES,
            "expires_at": timezone.now() + timedelta(seconds=SNAPSHOT_SECONDS),
        }
        cache.set(f"agent-task-log-page:{snapshot}", payload, timeout=SNAPSHOT_SECONDS)
        end = len(payload["items"])
    start = max(0, end - limit)
    next_cursor = (
        signing.dumps(
            {"identity": identity, "snapshot": snapshot, "end": start},
            salt=CURSOR_SALT,
        )
        if start
        else None
    )
    return AgentTaskLogPageType(
        items=[AgentTaskLogLineType(**line) for line in payload["items"][start:end]],
        next_cursor=next_cursor,
        has_more=start > 0,
        page_size=limit,
        live_only=True,
        window_limited=payload["window_limited"],
        expires_at=payload["expires_at"],
    )
