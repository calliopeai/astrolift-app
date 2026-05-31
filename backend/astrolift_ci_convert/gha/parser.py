"""Parse a GitHub Actions workflow YAML into PipelineDef.

Mapping decisions
-----------------
Runner labels
  ubuntu-latest / ubuntu-* / self-hosted → astrolift/default
  macos-*                                 → astrolift/macos
  windows-*                               → astrolift/windows

Known action mappings
  actions/checkout@*              → uses: astrolift/git-checkout@v1
  docker/build-push-action@*      → uses: astrolift/docker-build@v1
  actions/setup-node@*            → uses: astrolift/setup-node@v1
  actions/setup-python@*          → uses: astrolift/setup-python@v1
  actions/upload-artifact@*       → uses: astrolift/upload-artifact@v1
  actions/download-artifact@*     → uses: astrolift/download-artifact@v1
  actions/cache@*                 → uses: astrolift/cache@v1

Context variable mappings (${{ ... }})
  github.sha           → ctx.git.sha
  github.ref           → ctx.git.ref
  github.ref_name      → ctx.git.ref_name
  github.head_ref      → ctx.git.head_ref
  github.base_ref      → ctx.git.base_ref
  github.event_name    → ctx.event.name
  github.run_id        → ctx.run.id
  github.run_number    → ctx.run.number
  github.repository    → ctx.repo.full_name
  github.actor         → ctx.actor
  github.workspace     → ctx.workspace
  env.X                → env.X  (pass-through)
  secrets.X            → secrets.X (pass-through)
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from astrolift_ci_convert.common.types import (
    JobDef,
    PipelineDef,
    ScheduleDef,
    ServiceDef,
    StepDef,
)

# Runner label → Astrolift runner
_RUNNER_MAP: dict[str, str] = {
    "ubuntu-latest": "astrolift/default",
    "ubuntu-22.04": "astrolift/default",
    "ubuntu-20.04": "astrolift/default",
    "ubuntu-18.04": "astrolift/default",
    "self-hosted": "astrolift/default",
    "macos-latest": "astrolift/macos",
    "macos-12": "astrolift/macos",
    "macos-13": "astrolift/macos",
    "macos-14": "astrolift/macos",
    "windows-latest": "astrolift/windows",
    "windows-2022": "astrolift/windows",
    "windows-2019": "astrolift/windows",
}

# actions/<name>@* → astrolift/<name>
_ACTION_MAP: dict[str, str] = {
    "actions/checkout": "astrolift/git-checkout@v1",
    "docker/build-push-action": "astrolift/docker-build@v1",
    "actions/setup-node": "astrolift/setup-node@v1",
    "actions/setup-python": "astrolift/setup-python@v1",
    "actions/upload-artifact": "astrolift/upload-artifact@v1",
    "actions/download-artifact": "astrolift/download-artifact@v1",
    "actions/cache": "astrolift/cache@v1",
}

# github.* → ctx.*
_GITHUB_CTX_MAP: dict[str, str] = {
    "github.sha": "ctx.git.sha",
    "github.ref": "ctx.git.ref",
    "github.ref_name": "ctx.git.ref_name",
    "github.head_ref": "ctx.git.head_ref",
    "github.base_ref": "ctx.git.base_ref",
    "github.event_name": "ctx.event.name",
    "github.run_id": "ctx.run.id",
    "github.run_number": "ctx.run.number",
    "github.repository": "ctx.repo.full_name",
    "github.actor": "ctx.actor",
    "github.workspace": "ctx.workspace",
}


def _map_runner(runs_on: Any) -> str:
    """Resolve a GHA runs-on value (string or list) to an Astrolift runner."""
    if isinstance(runs_on, list):
        # e.g. [self-hosted, linux, x64]
        label = runs_on[0] if runs_on else "ubuntu-latest"
    else:
        label = str(runs_on)
    if label in _RUNNER_MAP:
        return _RUNNER_MAP[label]
    # Fallback: any ubuntu-* → default
    if label.startswith("ubuntu-"):
        return "astrolift/default"
    if label.startswith("macos-"):
        return "astrolift/macos"
    if label.startswith("windows-"):
        return "astrolift/windows"
    return "astrolift/default"


def _map_action(uses: str) -> tuple[str | None, list[str]]:
    """Map a GHA `uses` value to an Astrolift action ref.

    Returns (astrolift_ref_or_None, todo_list).
    """
    # Strip version suffix for lookup
    base = re.sub(r"@.*$", "", uses)
    if base in _ACTION_MAP:
        return _ACTION_MAP[base], []
    # Composite / reusable workflow reference (contains '/' and '@')
    return None, [f"action '{uses}' has no Astrolift equivalent — replace manually"]


def _translate_expr(text: str) -> str:
    """Replace ${{ ... }} GHA expressions with Astrolift ${...} equivalents."""

    def _replace(m: re.Match) -> str:
        expr = m.group(1).strip()
        if expr in _GITHUB_CTX_MAP:
            return "${" + _GITHUB_CTX_MAP[expr] + "}"
        if expr.startswith("env.") or expr.startswith("secrets."):
            return "${" + expr + "}"
        # Simple variable reference — pass through
        return "${" + expr + "}"

    return re.sub(r"\$\{\{(.+?)\}\}", _replace, text)


def _parse_env(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {k: _translate_expr(str(v)) for k, v in raw.items()}


def _parse_step(raw: dict) -> StepDef:
    name = str(raw.get("name", raw.get("uses", raw.get("run", "step"))))
    name = _translate_expr(name)

    todos: list[str] = []
    uses_ref: str | None = None
    run_cmd: str | None = None
    with_params: dict = {}

    env = _parse_env(raw.get("env", {}))

    if "uses" in raw:
        mapped, step_todos = _map_action(raw["uses"])
        todos.extend(step_todos)
        if mapped:
            uses_ref = mapped
            raw_with = raw.get("with", {})
            if isinstance(raw_with, dict):
                with_params = {k: _translate_expr(str(v)) for k, v in raw_with.items()}
        else:
            # Emit as run with comment — can't convert to Astrolift action
            run_cmd = f"# converted from: uses: {raw['uses']}"
    elif "run" in raw:
        run_cmd = _translate_expr(str(raw["run"]))

    if raw.get("continue-on-error"):
        todos.append("continue-on-error has no direct equivalent — wrap step in error-handling logic")

    return StepDef(
        name=name,
        uses=uses_ref,
        run=run_cmd,
        env=env,
        with_params=with_params,
        todos=todos,
    )


def _parse_services(raw: dict) -> list[ServiceDef]:
    svcs: list[ServiceDef] = []
    for svc_name, svc_cfg in raw.items():
        if not isinstance(svc_cfg, dict):
            continue
        image = str(svc_cfg.get("image", ""))
        env = _parse_env(svc_cfg.get("env", {}))
        svcs.append(ServiceDef(image=image, name=svc_name, env=env))
    return svcs


def _parse_job(job_id: str, raw: dict) -> JobDef:
    name = str(raw.get("name", job_id))
    runs_on = _map_runner(raw.get("runs-on", "ubuntu-latest"))
    container = ""
    if isinstance(raw.get("container"), dict):
        container = str(raw["container"].get("image", ""))
    elif isinstance(raw.get("container"), str):
        container = raw["container"]

    needs_raw = raw.get("needs", [])
    needs = [needs_raw] if isinstance(needs_raw, str) else list(needs_raw)

    env = _parse_env(raw.get("env", {}))

    steps: list[StepDef] = []
    for step_raw in raw.get("steps", []):
        if not isinstance(step_raw, dict):
            continue
        steps.append(_parse_step(step_raw))

    services: list[ServiceDef] = []
    if isinstance(raw.get("services"), dict):
        services = _parse_services(raw["services"])

    outputs: dict = {}
    if isinstance(raw.get("outputs"), dict):
        outputs = {k: _translate_expr(str(v)) for k, v in raw["outputs"].items()}

    todos: list[str] = []
    if "strategy" in raw and "matrix" in raw.get("strategy", {}):
        todos.append("strategy.matrix is not yet supported — define separate jobs or wait for matrix support")
    if "permissions" in raw:
        todos.append("job-level permissions have no equivalent — manage via Astrolift RBAC")
    if "if" in raw:
        todos.append(f"job condition 'if: {raw['if']}' — verify expression compatibility")

    return JobDef(
        job_id=job_id,
        name=name,
        runs_on=runs_on,
        container=container,
        needs=needs,
        steps=steps,
        env=env,
        services=services,
        outputs=outputs,
        todos=todos,
    )


def parse(yaml_str: str) -> PipelineDef:
    """Parse a GitHub Actions workflow YAML string into a PipelineDef."""
    raw = yaml.safe_load(yaml_str) or {}

    name = str(raw.get("name", "pipeline"))

    env = _parse_env(raw.get("env", {}))

    # PyYAML parses bare `on:` as the boolean True key.
    on_raw = raw.get("on", raw.get(True, {}))
    if isinstance(on_raw, str):
        on_raw = {on_raw: {}}
    elif isinstance(on_raw, list):
        on_raw = {ev: {} for ev in on_raw}
    on_raw = on_raw or {}

    on_push_branches: list[str] = []
    on_push_tags: list[str] = []
    on_pr_branches: list[str] = []
    on_schedule: list[ScheduleDef] = []
    on_dispatch = False
    todos: list[str] = []

    push_cfg = on_raw.get("push", {}) or {}
    if isinstance(push_cfg, dict):
        branches = push_cfg.get("branches", [])
        if isinstance(branches, str):
            branches = [branches]
        on_push_branches = list(branches)
        tags = push_cfg.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        on_push_tags = list(tags)

    pr_cfg = on_raw.get("pull_request", {}) or {}
    if isinstance(pr_cfg, dict):
        branches = pr_cfg.get("branches", [])
        if isinstance(branches, str):
            branches = [branches]
        on_pr_branches = list(branches)

    if "workflow_dispatch" in on_raw:
        on_dispatch = True

    schedule_raw = on_raw.get("schedule", []) or []
    for sched in schedule_raw:
        if isinstance(sched, dict) and "cron" in sched:
            on_schedule.append(ScheduleDef(cron=str(sched["cron"])))

    jobs_raw = raw.get("jobs", {}) or {}
    jobs = [_parse_job(jid, jraw) for jid, jraw in jobs_raw.items() if isinstance(jraw, dict)]

    return PipelineDef(
        name=name,
        jobs=jobs,
        env=env,
        on_push_branches=on_push_branches,
        on_push_tags=on_push_tags,
        on_pull_request_branches=on_pr_branches,
        on_schedule=on_schedule,
        on_workflow_dispatch=on_dispatch,
        todos=todos,
    )
