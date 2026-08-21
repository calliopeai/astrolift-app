"""Read an ``astrolift.toml`` back into a :class:`PipelineDef` (#1531).

The inverse of ``toml_writer``. Until now this direction did not exist:
``astrolift_ci_convert`` turned GitHub Actions and GitLab YAML *into* TOML
and nothing ever read the result, which is why the writer could emit every
job's steps into one shared array for as long as it did (#1544).

``toml_fetcher``'s docstring says step 4 is "parse and validate the TOML
(import the DSL parser from ``astrolift_ci_convert``)". This is that
parser. It is deliberately the writer's mirror rather than a general TOML
schema: the round-trip is the contract, and
``tests/test_toml_round_trip.py`` holds the two together.

Parsing is total — every failure is a ``TomlReadError`` naming the path
that was wrong, because the caller is a webhook handling an operator's
repository and "invalid pipeline" with no location is not actionable at
3am.
"""

from __future__ import annotations

import tomllib
from typing import Any

from astrolift_ci_convert.common.types import JobDef, PipelineDef, ScheduleDef, StepDef


class TomlReadError(ValueError):
    """The TOML is not a pipeline. Carries the offending path."""

    def __init__(self, message: str, *, path: str = "") -> None:
        super().__init__(f"{path}: {message}" if path else message)
        self.path = path


def read_toml(text: str) -> PipelineDef:
    """Parse *text* into a :class:`PipelineDef`."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TomlReadError(f"not valid TOML: {exc}") from exc

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TomlReadError("a pipeline needs a 'name'", path="name")

    on = _table(raw.get("on"), "on")
    push = _table(on.get("push"), "on.push")
    pull_request = _table(on.get("pull_request"), "on.pull_request")

    return PipelineDef(
        name=name,
        jobs=_read_jobs(raw.get("jobs")),
        env=_str_map(raw.get("env"), "env"),
        on_push_branches=_str_list(push.get("branches"), "on.push.branches"),
        on_push_tags=_str_list(push.get("tags"), "on.push.tags"),
        on_pull_request_branches=_str_list(pull_request.get("branches"), "on.pull_request.branches"),
        on_schedule=_read_schedules(on.get("schedule")),
        on_workflow_dispatch="workflow_dispatch" in on,
    )


def _read_jobs(raw: Any) -> list[JobDef]:
    if raw is None:
        return []
    jobs_table = _table(raw, "jobs")

    jobs: list[JobDef] = []
    # Sorted so two reads of the same file produce the same order. TOML
    # tables are unordered by spec, and a DAG that reorders itself between
    # runs is a debugging nightmare for no benefit — `needs` carries the
    # real ordering.
    for job_id in sorted(jobs_table):
        path = f"jobs.{job_id}"
        body = _table(jobs_table[job_id], path)
        jobs.append(
            JobDef(
                job_id=job_id,
                name=str(body.get("name") or job_id),
                runs_on=str(body.get("runs_on") or "astrolift/default"),
                container=str(body.get("container") or ""),
                needs=_str_list(body.get("needs"), f"{path}.needs"),
                steps=_read_steps(body.get("steps"), path),
                env=_str_map(body.get("env"), f"{path}.env"),
                outputs=_str_map(body.get("outputs"), f"{path}.outputs"),
            )
        )
    return jobs


def _read_steps(raw: Any, job_path: str) -> list[StepDef]:
    if raw is None:
        return []
    path = f"{job_path}.steps"
    if not isinstance(raw, list):
        raise TomlReadError("steps must be an array of tables ([[jobs.<id>.steps]])", path=path)

    steps: list[StepDef] = []
    for index, entry in enumerate(raw):
        step_path = f"{path}[{index}]"
        body = _table(entry, step_path)
        uses = body.get("uses")
        run = body.get("run")
        if uses is None and run is None:
            raise TomlReadError("a step needs either 'run' or 'uses'", path=step_path)
        if uses is not None and run is not None:
            raise TomlReadError(
                "a step has 'run' and 'uses'; they are alternatives, and which one "
                "wins would be silent either way",
                path=step_path,
            )
        steps.append(
            StepDef(
                name=str(body.get("name") or f"step {index + 1}"),
                uses=str(uses) if uses is not None else None,
                run=str(run) if run is not None else None,
                env=_str_map(body.get("env"), f"{step_path}.env"),
                with_params=_str_map(body.get("with"), f"{step_path}.with"),
            )
        )
    return steps


def _read_schedules(raw: Any) -> list[ScheduleDef]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TomlReadError("schedule must be an array of tables", path="on.schedule")
    out: list[ScheduleDef] = []
    for index, entry in enumerate(raw):
        path = f"on.schedule[{index}]"
        cron = _table(entry, path).get("cron")
        if not isinstance(cron, str) or not cron.strip():
            raise TomlReadError("a schedule needs a 'cron'", path=f"{path}.cron")
        out.append(ScheduleDef(cron=cron))
    return out


# ---- shape helpers --------------------------------------------------------


def _table(value: Any, path: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TomlReadError(f"expected a table, got {type(value).__name__}", path=path)
    return value


def _str_list(value: Any, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TomlReadError("expected an array of strings", path=path)
    return list(value)


def _str_map(value: Any, path: str) -> dict:
    table = _table(value, path)
    # Values are coerced rather than refused: TOML types an unquoted 8080
    # as an int and an operator writing `PORT = 8080` means the string, but
    # a key that is not a string is a shape error rather than a typo.
    return {str(k): str(v) for k, v in table.items()}
