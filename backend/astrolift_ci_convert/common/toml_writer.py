"""Write Astrolift pipeline TOML from a PipelineDef intermediate type."""

from __future__ import annotations

from astrolift_ci_convert.common.types import JobDef, PipelineDef, ServiceDef, StepDef


def _toml_string(value: str) -> str:
    """Return a TOML-safe quoted string. Uses basic strings with escape."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_multiline_string(value: str) -> str:
    """Return a TOML multi-line literal string for run blocks."""
    # If the value contains ''' we fall back to basic multi-line.
    if "'''" not in value:
        return f"'''\n{value}\n'''"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"""\n{escaped}\n"""'


def _write_env(env: dict, indent: str = "") -> list[str]:
    lines: list[str] = []
    if env:
        lines.append(f"{indent}[env]")
        for k, v in env.items():
            lines.append(f"{indent}{k} = {_toml_string(str(v))}")
    return lines


def _write_step(step: StepDef, job_id: str, step_idx: int) -> list[str]:
    lines: list[str] = []

    for todo in step.todos:
        lines.append(f"# TODO: unsupported — {todo}")

    lines.append("[[jobs.steps]]")
    lines.append(f"name = {_toml_string(step.name)}")

    if step.uses:
        lines.append(f"uses = {_toml_string(step.uses)}")
        if step.with_params:
            lines.append("[jobs.steps.with]")
            for k, v in step.with_params.items():
                lines.append(f"  {k} = {_toml_string(str(v))}")

    if step.run:
        run_val = _toml_multiline_string(step.run)
        lines.append(f"run = {run_val}")

    if step.env:
        lines.append("[jobs.steps.env]")
        for k, v in step.env.items():
            lines.append(f"  {k} = {_toml_string(str(v))}")

    return lines


def _write_service(svc: ServiceDef) -> list[str]:
    lines: list[str] = []
    lines.append("[[jobs.services]]")
    lines.append(f"image = {_toml_string(svc.image)}")
    if svc.name:
        lines.append(f"name = {_toml_string(svc.name)}")
    if svc.env:
        lines.append("[jobs.services.env]")
        for k, v in svc.env.items():
            lines.append(f"  {k} = {_toml_string(str(v))}")
    return lines


def _write_job(job: JobDef) -> list[str]:
    lines: list[str] = []
    lines.append(f"[jobs.{job.job_id}]")
    lines.append(f"name = {_toml_string(job.name)}")
    lines.append(f'runs_on = "{job.runs_on}"')

    if job.container:
        lines.append(f"container = {_toml_string(job.container)}")

    if job.needs:
        needs_list = ", ".join(_toml_string(n) for n in job.needs)
        lines.append(f"needs = [{needs_list}]")

    if job.env:
        lines.append(f"[jobs.{job.job_id}.env]")
        for k, v in job.env.items():
            lines.append(f"  {k} = {_toml_string(str(v))}")

    if job.outputs:
        lines.append(f"[jobs.{job.job_id}.outputs]")
        for k, v in job.outputs.items():
            lines.append(f"  {k} = {_toml_string(str(v))}")

    for todo in job.todos:
        lines.append(f"# TODO: unsupported — {todo}")

    for svc in job.services:
        lines.extend(_write_service(svc))

    for idx, step in enumerate(job.steps):
        lines.extend(_write_step(step, job.job_id, idx))

    return lines


def write_toml(pipeline: PipelineDef) -> str:
    """Serialize a PipelineDef to an Astrolift pipeline TOML string."""
    sections: list[list[str]] = []

    # Header
    header: list[str] = []
    header.append(f"name = {_toml_string(pipeline.name)}")

    sections.append(header)

    # Top-level env
    if pipeline.env:
        env_lines = ["[env]"]
        for k, v in pipeline.env.items():
            env_lines.append(f"{k} = {_toml_string(str(v))}")
        sections.append(env_lines)

    # Triggers
    if pipeline.on_push_branches or pipeline.on_push_tags:
        push: list[str] = ["[on.push]"]
        if pipeline.on_push_branches:
            branches = ", ".join(_toml_string(b) for b in pipeline.on_push_branches)
            push.append(f"branches = [{branches}]")
        if pipeline.on_push_tags:
            tags = ", ".join(_toml_string(t) for t in pipeline.on_push_tags)
            push.append(f"tags = [{tags}]")
        sections.append(push)

    if pipeline.on_pull_request_branches:
        pr: list[str] = ["[on.pull_request]"]
        branches = ", ".join(_toml_string(b) for b in pipeline.on_pull_request_branches)
        pr.append(f"branches = [{branches}]")
        sections.append(pr)

    if pipeline.on_workflow_dispatch:
        sections.append(["[on.workflow_dispatch]"])

    for sched in pipeline.on_schedule:
        sections.append([f"[[on.schedule]]\ncron = {_toml_string(sched.cron)}"])

    for todo in pipeline.todos:
        sections.append([f"# TODO: unsupported — {todo}"])

    # Jobs
    for job in pipeline.jobs:
        sections.append(_write_job(job))

    return "\n\n".join("\n".join(s) for s in sections) + "\n"
