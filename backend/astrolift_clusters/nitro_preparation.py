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
    from botocore.config import Config

    return boto3.client(
        "ecs",
        region_name=config["cluster"].split(":")[3],
        config=Config(connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 1}),
    )


def readiness():
    """This process's confidential workspace check, when a preparation task stands in for it."""
    from aws.nitro_readiness import check

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
    # Task memory is a nominal sizing gate; each preparer checks actual tmpfs
    # before resolving secrets, rather than assuming a Fargate memory ratio.
    ready = definition.get("status") == "ACTIVE" and memory >= 16384
    return check(
        "preparation_task",
        "ready" if ready else "not_ready",
        "The preparation task definition is active with at least 16 GiB nominal memory; "
        "the preparer must verify its actual memory workspace before resolving secrets.",
        task_definition=definition.get("taskDefinitionArn"),
        cpu=cpu,
        memory_mib=memory,
        workspace_verified=False,
    )


class PreparationUncertain(RuntimeError):
    """The preparer may still be live; retry without retiring its artifacts."""


OWNER_TAG = "astrolift.io/nitro-preparation"
STOP_TIMEOUT_SECONDS = 120


def _started_by(task) -> str:
    return "np-" + task.guid.hex


def _record(task, run, **changes):
    from django.db import transaction

    from astrolift_agents.models import AgentTask

    with transaction.atomic():
        current = AgentTask.all_objects.select_for_update().get(pk=task.pk)
        launch = (current.dispatch_target or {}).get("nitro_preparation") or {}
        if launch.get("run") != run:
            raise PreparationUncertain("Confidential preparation launch authority changed")
        launch = {**launch, **changes}
        current.dispatch_target = {**current.dispatch_target, "nitro_preparation": launch}
        current.save(update_fields=["dispatch_target", "updated_at", "version"])
        task.dispatch_target = current.dispatch_target
    return launch


def _launch(task, config, ecs):
    from django.db import transaction

    from astrolift_agents.models import AgentTask

    # Resolve a family once: an idempotent retry must retain the exact revision,
    # network and ownership tag even if the parent is rolled while it waits.
    task.refresh_from_db(fields=["dispatch_target"])
    existing = (task.dispatch_target or {}).get("nitro_preparation") or {}
    if existing.get("request"):
        return existing
    try:
        definition = ecs.describe_task_definition(taskDefinition=config["task_definition"])["taskDefinition"]
        revision = definition["taskDefinitionArn"]
    except Exception:
        raise PreparationUncertain("The confidential preparation definition could not be resolved") from None
    with transaction.atomic():
        current = AgentTask.all_objects.select_for_update().get(pk=task.pk)
        existing = (current.dispatch_target or {}).get("nitro_preparation") or {}
        if existing.get("request"):
            return existing
        run = uuid.uuid4().hex
        launch = {
            "run": run,
            "config": config,
            "deadline": time.time() + TIMEOUT_SECONDS,
            "request": {
                "cluster": config["cluster"],
                "taskDefinition": revision,
                "launchType": "FARGATE",
                "count": 1,
                "clientToken": run,
                "startedBy": _started_by(task),
                "tags": [{"key": OWNER_TAG, "value": config["task_definition"]}],
                "networkConfiguration": {
                    "awsvpcConfiguration": {
                        "subnets": config["subnets"],
                        "securityGroups": config["security_groups"],
                        "assignPublicIp": "DISABLED",
                    }
                },
                "overrides": {
                    "containerOverrides": [
                        {
                            "name": config["container"],
                            "environment": [{"name": MARKER, "value": f"{task.guid}:{run}"}],
                        }
                    ]
                },
            },
        }
        current.dispatch_target = {**current.dispatch_target, "nitro_preparation": launch}
        current.save(update_fields=["dispatch_target", "updated_at", "version"])
        task.dispatch_target = current.dispatch_target
        return launch


def _describe(ecs, cluster, arn):
    try:
        response = ecs.describe_tasks(cluster=cluster, tasks=[arn])
        rows = response.get("tasks", [])
        if response.get("failures") or len(rows) != 1 or rows[0].get("taskArn") != arn:
            raise ValueError
        return rows[0]
    except Exception:
        raise PreparationUncertain("The confidential preparation task state is not confirmed") from None


def _discover(task, launch, ecs):
    config = launch["config"]
    token = None
    found = []
    while True:
        arguments = {"cluster": config["cluster"], "startedBy": _started_by(task)}
        if token:
            arguments["nextToken"] = token
        try:
            response = ecs.list_tasks(**arguments)
        except Exception:
            raise PreparationUncertain("The confidential preparation task could not be located") from None
        for arn in response.get("taskArns", []):
            row = _describe(ecs, config["cluster"], arn)
            if row.get("taskDefinitionArn") != launch["request"]["taskDefinition"]:
                continue
            overrides = row.get("overrides", {}).get("containerOverrides", [])
            if any(
                item.get("name") == MARKER and item.get("value") == f"{task.guid}:{launch['run']}"
                for container in overrides
                if container.get("name") == config["container"]
                for item in container.get("environment", [])
            ):
                found.append(arn)
        token = response.get("nextToken")
        if not token:
            break
    if len(found) != 1:
        raise PreparationUncertain("The confidential preparation task identity is not confirmed")
    _record(task, launch["run"], task_arn=found[0])
    return found[0]


def stop_preparation(task):
    """Confirm the producer stopped before any consumer retires its artifacts."""
    task.refresh_from_db(fields=["dispatch_target"])
    launch = (task.dispatch_target or {}).get("nitro_preparation") or {}
    if not launch.get("request") or launch.get("stopped"):
        return
    if os.environ.get(MARKER) == f"{task.guid}:{launch['run']}":
        return
    launch = _record(task, launch["run"], stop_requested=True)
    config = launch["config"]
    ecs = _client(config)
    arn = launch.get("task_arn") or _discover(task, launch, ecs)
    row = _describe(ecs, config["cluster"], arn)
    if row["lastStatus"] != "STOPPED":
        try:
            ecs.stop_task(cluster=config["cluster"], task=arn, reason="Confidential preparation stopped")
        except Exception:
            raise PreparationUncertain("The confidential preparation task stop is not confirmed") from None
        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while row["lastStatus"] != "STOPPED":
            if time.monotonic() >= deadline:
                raise PreparationUncertain("The confidential preparation task is still stopping")
            time.sleep(POLL_SECONDS)
            row = _describe(ecs, config["cluster"], arn)
    _record(task, launch["run"], stopped=True)


def prepare(task) -> tuple[bool, str]:
    config = launcher()
    ecs = _client(config)
    launch = _launch(task, config, ecs)
    config = launch["config"]
    ecs = _client(config)
    cluster, run = config["cluster"], launch["run"]
    if launch.get("rejected"):
        return False, launch["error"]
    arn = launch.get("task_arn")
    if not arn:
        # Never replay an expired idempotency token: absence from ListTasks is
        # not proof that an earlier RunTask was rejected.
        if time.time() >= launch["deadline"] or launch.get("stop_requested"):
            arn = _discover(task, launch, ecs)
        else:
            try:
                response = ecs.run_task(**launch["request"])
            except Exception:
                raise PreparationUncertain("The confidential preparation launch is not confirmed") from None
            if response.get("failures") and response.get("tasks") == []:
                error = "The confidential preparation task launch was rejected"
                _record(task, run, stopped=True, rejected=True, ok=False, error=error)
                return False, error
            if response.get("failures") or len(response.get("tasks", [])) != 1:
                raise PreparationUncertain("The confidential preparation launch is not confirmed")
            arn = response["tasks"][0]["taskArn"]
            _record(task, run, task_arn=arn)
    while True:
        row = _describe(ecs, cluster, arn)
        if row["lastStatus"] == "STOPPED":
            _record(task, run, stopped=True)
            break
        task.refresh_from_db(fields=["status", "cancel_requested_at", "dispatch_target"])
        current = task.dispatch_target.get("nitro_preparation") or {}
        if (
            current.get("stop_requested")
            or task.cancel_requested_at
            or task.status in task.TERMINAL_STATUSES
            or time.time() >= launch["deadline"]
        ):
            stop_preparation(task)
            return False, (
                "Confidential image preparation timed out"
                if time.time() >= launch["deadline"]
                else "Task ended or was cancelled during confidential image preparation"
            )
        time.sleep(POLL_SECONDS)
    task.refresh_from_db()
    outcome = (task.dispatch_target or {}).get("nitro_preparation") or {}
    codes = [
        container.get("exitCode")
        for container in row.get("containers", [])
        if container["name"] == config["container"]
    ]
    if outcome.get("run") == run:
        if outcome.get("ok") and codes == [0] and not outcome.get("stop_requested"):
            return True, ""
        if outcome.get("error"):
            return False, outcome["error"]
    return (
        False,
        "The confidential preparation task stopped without a result; review its size and logs "
        + f"({row.get('stoppedReason') or 'no reason reported'})",
    )


def perform(guid: str, run: str) -> bool:
    from astrolift_agents.models import AgentTask
    from astrolift_agents.services.task_target import resolve_task_target
    from astrolift_dispatch.spawners.registry import get_spawner

    task = AgentTask.objects.select_related("organization", "agent_definition", "brief").get(guid=guid)
    launch = (task.dispatch_target or {}).get("nitro_preparation") or {}
    if launch.get("run") != run or not launch.get("request"):
        raise PreparationUncertain("Confidential preparation launch authority differs")
    backend, cluster, namespace = resolve_task_target(task)
    if (
        backend != "k8s_confidential_pod"
        or task.status != AgentTask.Status.PROVISIONING
        or task.cancel_requested_at
        or task.external_id
        or launch.get("stop_requested")
    ):
        ok, error = False, "Task is no longer awaiting confidential image preparation"
    else:
        spawner = get_spawner(backend, cluster=cluster, namespace=namespace)
        try:
            result = spawner.recover(task) or spawner.spawn(task)
            ok, error = result.ok, result.error
        except Exception:
            ok, error = False, "Confidential image preparation failed"
    _record(task, run, ok=ok, error=error)
    return ok
