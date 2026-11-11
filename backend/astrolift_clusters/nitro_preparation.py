"""Confidential image preparation in an on-demand ECS task (#2678).

Preparation holds a whole plaintext image in a memory workspace. An operator
who configures ``NITRO_PREPARATION_TASK`` moves that out of the always-on API
and worker: the worker starts a one-shot task from the same image and role,
waits for it, and adopts the Pod it created. The task reads the run from the
database itself, so no Brief, secret or key travels through ECS.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid

from django.conf import settings

#: Set only inside the preparation task, as ``<task guid>:<run>``.
MARKER = "ASTROLIFT_CONFIDENTIAL_PREPARATION"
POLL_SECONDS = 5
TIMEOUT_SECONDS = 3600


def launcher():
    """The operator's preparation task, or None when this process prepares images itself."""
    raw = settings.NITRO_PREPARATION_TASK
    if not raw or os.environ.get(MARKER):
        return None
    try:
        value = json.loads(raw)
        if (
            not re.fullmatch(
                r"arn:[a-z0-9-]+:ecs:[a-z0-9-]+:[0-9]{12}:cluster/[A-Za-z0-9_-]+", value["cluster"]
            )
            or not all(isinstance(value[key], str) and value[key] for key in ("task_definition", "container"))
            or not all(
                isinstance(value[key], list)
                and value[key]
                and all(isinstance(row, str) for row in value[key])
                for key in ("subnets", "security_groups")
            )
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ValueError("The confidential preparation task configuration is malformed") from None
    return value


def _client(config):
    import boto3

    return boto3.client("ecs", region_name=config["cluster"].split(":")[3])


def readiness():
    """This process's confidential workspace check, when a preparation task stands in for it."""
    from aws.nitro_readiness import WORKSPACE_BYTES, check

    try:
        config = launcher()
    except ValueError as exc:
        return check("preparation_task", "not_ready", str(exc))
    if config is None:
        return None
    try:
        definition = _client(config).describe_task_definition(taskDefinition=config["task_definition"])[
            "taskDefinition"
        ]
        cpu, memory = int(definition["cpu"]), int(definition["memory"])
    except Exception:
        return check("preparation_task", "unknown", "The confidential preparation task could not be read.")
    # Fargate sizes the task's memory workspace at half its memory.
    ready = definition.get("status") == "ACTIVE" and memory * 1024**2 // 2 >= WORKSPACE_BYTES
    return check(
        "preparation_task",
        "ready" if ready else "not_ready",
        "Confidential images are prepared in an on-demand task with at least 16 GiB of memory.",
        task_definition=definition.get("taskDefinitionArn"),
        cpu=cpu,
        memory_mib=memory,
    )


def _started_by(task) -> str:
    return "np-" + task.guid.hex


def prepare(task) -> tuple[bool, str]:
    """Run the preparation task for ``task`` to completion. Returns ``(ok, error)``."""
    config = launcher()
    ecs = _client(config)
    cluster = config["cluster"]
    arn = run = None
    # A replacement worker waits on the task its predecessor started.
    for found in ecs.list_tasks(cluster=cluster, startedBy=_started_by(task), desiredStatus="RUNNING")[
        "taskArns"
    ]:
        for row in ecs.describe_tasks(cluster=cluster, tasks=[found])["tasks"]:
            for override in row.get("overrides", {}).get("containerOverrides", []):
                for item in override.get("environment", []):
                    if item["name"] == MARKER:
                        arn, run = found, item["value"].partition(":")[2]
    if arn is None:
        run = uuid.uuid4().hex
        response = ecs.run_task(
            cluster=cluster,
            taskDefinition=config["task_definition"],
            launchType="FARGATE",
            count=1,
            startedBy=_started_by(task),
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": config["subnets"],
                    "securityGroups": config["security_groups"],
                    "assignPublicIp": "DISABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": config["container"],
                        "environment": [{"name": MARKER, "value": f"{task.guid}:{run}"}],
                    }
                ]
            },
        )
        if response.get("failures") or len(response.get("tasks", [])) != 1:
            return False, "The confidential preparation task could not be started"
        arn = response["tasks"][0]["taskArn"]
    deadline = time.monotonic() + TIMEOUT_SECONDS
    stopped = ""
    while True:
        rows = ecs.describe_tasks(cluster=cluster, tasks=[arn])["tasks"]
        if not rows:
            return False, "The confidential preparation task disappeared"
        if rows[0]["lastStatus"] == "STOPPED":
            break
        if not stopped:
            task.refresh_from_db(fields=["status", "cancel_requested_at"])
            if task.cancel_requested_at or task.status in task.TERMINAL_STATUSES:
                stopped = "Task ended or was cancelled during confidential image preparation"
            elif time.monotonic() > deadline:
                stopped = "Confidential image preparation timed out"
            if stopped:
                ecs.stop_task(cluster=cluster, task=arn, reason=stopped)
        time.sleep(POLL_SECONDS)
    if stopped:
        return False, stopped
    task.refresh_from_db()
    outcome = (task.dispatch_target or {}).get("nitro_preparation") or {}
    codes = [
        row.get("exitCode") for row in rows[0].get("containers", []) if row["name"] == config["container"]
    ]
    if outcome.get("run") == run:
        if outcome.get("ok") and codes == [0]:
            return True, ""
        if outcome.get("error"):
            return False, outcome["error"]
    return (
        False,
        "The confidential preparation task stopped without a result; review its size and logs "
        f"({rows[0].get('stoppedReason') or 'no reason reported'})",
    )


def perform(guid: str, run: str) -> bool:
    """Inside the preparation task: prepare and create the run's Pod, then record the outcome."""
    from astrolift_agents.models import AgentTask
    from astrolift_agents.services.task_target import resolve_task_target
    from astrolift_dispatch.spawners.registry import get_spawner

    task = AgentTask.objects.select_related("organization", "agent_definition", "brief").get(guid=guid)
    backend, cluster, namespace = resolve_task_target(task)
    if (
        backend != "k8s_confidential_pod"
        or task.status != AgentTask.Status.PROVISIONING
        or task.cancel_requested_at
        or task.external_id
    ):
        ok, error = False, "Task is no longer awaiting confidential image preparation"
    else:
        spawner = get_spawner(backend, cluster=cluster, namespace=namespace)
        try:
            result = spawner.recover(task) or spawner.spawn(task)
            ok, error = result.ok, result.error
        except Exception:  # noqa: BLE001 - provider text may carry run material
            ok, error = False, "Confidential image preparation failed"
    task.refresh_from_db()
    task.dispatch_target = {
        **task.dispatch_target,
        "nitro_preparation": {"run": run, "ok": ok, "error": error},
    }
    task.save(update_fields=["dispatch_target", "updated_at", "version"])
    return ok
