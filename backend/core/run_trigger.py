"""How a run started, in one vocabulary across every kind of run (#2152).

Agent tasks and workflow runs record it in their own ``trigger_kind``
column. Deployments, scheduled job runs and task runs already carry a
trigger in their own words; :func:`normalize` maps those onto this set so
the run audit can filter and show one column for all of them.

* ``manual``: a person, from the UI (a session).
* ``api``: a person or a CI job through a token (API token, MCP, deploy token).
* ``schedule``: the platform, on a cron or loop tick.
* ``webhook``: an inbound webhook or an SCM push.
* ``parent``: another run (a workflow stage, a nested workflow).
* ``unknown``: recorded before the column existed, and the data cannot say.
"""

from __future__ import annotations

from django.db import models


class RunTrigger(models.TextChoices):
    MANUAL = "manual"
    API = "api"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    PARENT = "parent"
    UNKNOWN = "unknown"


def request_trigger() -> str:
    """``api`` when the current request authenticated with an API token, else ``manual``.

    For a run started by a GraphQL mutation: the same caller is a person at
    the UI or a script holding a token, and only the token says which.
    """
    from astrolift_identity.api_tokens import get_current_api_token

    return RunTrigger.API if get_current_api_token() is not None else RunTrigger.MANUAL


# Each source's own trigger word, per run kind, as the shared vocabulary.
_SOURCE_TRIGGERS: dict[str, dict[str, str]] = {
    "deployment": {
        "manual": RunTrigger.MANUAL,
        "rollback": RunTrigger.MANUAL,
        "promotion": RunTrigger.MANUAL,
        "ci": RunTrigger.API,
        "push": RunTrigger.WEBHOOK,
        "scheduled": RunTrigger.SCHEDULE,
    },
    "job": {
        "scheduled": RunTrigger.SCHEDULE,
        "manual": RunTrigger.MANUAL,
    },
    "task": {
        "manual": RunTrigger.MANUAL,
        "api": RunTrigger.API,
        "workflow": RunTrigger.PARENT,
    },
}


def normalize(kind: str, source_trigger: str) -> str:
    """A run's trigger in the shared vocabulary.

    Agent and workflow runs already store it; the other kinds map their own
    word. Anything unmapped is ``unknown`` rather than a guess.
    """
    table = _SOURCE_TRIGGERS.get(kind)
    if table is None:
        return source_trigger if source_trigger in RunTrigger.values else RunTrigger.UNKNOWN
    return table.get(source_trigger, RunTrigger.UNKNOWN)


def source_triggers(kind: str, triggers: list[str]) -> list[str] | None:
    """The source's own trigger words that normalize to any of ``triggers``.

    ``None`` for a kind that stores the shared vocabulary itself, so the
    caller filters on ``triggers`` directly.
    """
    table = _SOURCE_TRIGGERS.get(kind)
    if table is None:
        return None
    wanted = set(triggers)
    return [word for word, shared in table.items() if shared in wanted]


def deployment_run_trigger(deploy_trigger_kind: str, *, via_token: bool) -> str:
    """The trigger for the ``WorkflowRun`` mirror of a deployment's workflow.

    A manual-shaped deploy (manual, rollback, promotion) started through an
    API token is ``api``; everything else follows :func:`normalize`.
    """
    shared = normalize("deployment", deploy_trigger_kind)
    if shared == RunTrigger.MANUAL and via_token:
        return RunTrigger.API
    return shared
