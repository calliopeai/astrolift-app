"""Parse a GitLab CI YAML file into PipelineDef.

GitLab CI top-level keys that are NOT jobs
------------------------------------------
  .default, variables, stages, image, services, before_script,
  after_script, cache, workflow, include, pages

Any other top-level mapping key is treated as a job.

Stage → needs DAG
-----------------
When a pipeline uses `stages:` and a job does not declare explicit `needs:`,
all jobs in the *previous* stage are injected as implicit dependencies. This
mirrors GitLab's stage-ordering semantics.

CI_* variable mappings
----------------------
  CI_COMMIT_SHA          → ${ctx.git.sha}
  CI_COMMIT_REF_NAME     → ${ctx.git.ref_name}
  CI_COMMIT_REF_SLUG     → ${ctx.git.ref_name}
  CI_COMMIT_SHORT_SHA    → ${ctx.git.sha}
  CI_COMMIT_BRANCH       → ${ctx.git.ref_name}
  CI_COMMIT_TAG          → ${ctx.git.ref_name}
  CI_PIPELINE_ID         → ${ctx.run.id}
  CI_PIPELINE_IID        → ${ctx.run.number}
  CI_JOB_ID             → ${ctx.run.id}
  CI_JOB_NAME           → ${ctx.job.name}
  CI_PROJECT_PATH        → ${ctx.repo.full_name}
  CI_PROJECT_NAME        → ${ctx.repo.name}
  CI_REGISTRY            → ${ctx.registry.host}
  CI_REGISTRY_IMAGE      → ${ctx.registry.image}
  CI_REGISTRY_USER       → ${ctx.registry.user}
  CI_REGISTRY_PASSWORD   → ${ctx.registry.password}
  CI_DEFAULT_BRANCH      → ${ctx.git.default_branch}
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

# Keys that are never jobs.
_NON_JOB_KEYS = frozenset(
    [
        ".default",
        "variables",
        "stages",
        "image",
        "services",
        "before_script",
        "after_script",
        "cache",
        "workflow",
        "include",
        "pages",
    ]
)

_CI_VAR_MAP: dict[str, str] = {
    "CI_COMMIT_SHA": "${ctx.git.sha}",
    "CI_COMMIT_REF_NAME": "${ctx.git.ref_name}",
    "CI_COMMIT_REF_SLUG": "${ctx.git.ref_name}",
    "CI_COMMIT_SHORT_SHA": "${ctx.git.sha}",
    "CI_COMMIT_BRANCH": "${ctx.git.ref_name}",
    "CI_COMMIT_TAG": "${ctx.git.ref_name}",
    "CI_PIPELINE_ID": "${ctx.run.id}",
    "CI_PIPELINE_IID": "${ctx.run.number}",
    "CI_JOB_ID": "${ctx.run.id}",
    "CI_JOB_NAME": "${ctx.job.name}",
    "CI_PROJECT_PATH": "${ctx.repo.full_name}",
    "CI_PROJECT_NAME": "${ctx.repo.name}",
    "CI_REGISTRY": "${ctx.registry.host}",
    "CI_REGISTRY_IMAGE": "${ctx.registry.image}",
    "CI_REGISTRY_USER": "${ctx.registry.user}",
    "CI_REGISTRY_PASSWORD": "${ctx.registry.password}",
    "CI_DEFAULT_BRANCH": "${ctx.git.default_branch}",
}


def _translate_ci_vars(text: str) -> str:
    """Replace $CI_* and ${CI_*} references with Astrolift ctx equivalents."""
    result = text

    # Replace ${VAR} style
    def _replace_braced(m: re.Match) -> str:
        var = m.group(1)
        return _CI_VAR_MAP.get(var, "${" + var + "}")

    result = re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", _replace_braced, result)

    # Replace $VAR style (word boundary)
    def _replace_bare(m: re.Match) -> str:
        var = m.group(1)
        return _CI_VAR_MAP.get(var, "$" + var)

    result = re.sub(r"\$([A-Z_][A-Z0-9_]*)\b", _replace_bare, result)

    return result


def _parse_env(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {k: _translate_ci_vars(str(v)) for k, v in raw.items()}


def _script_to_run(script: Any) -> str:
    """Convert a GitLab script (str or list) to a shell run string."""
    if isinstance(script, list):
        return "\n".join(str(line) for line in script)
    return str(script)


def _parse_services(raw: Any, global_image: str = "") -> list[ServiceDef]:
    svcs: list[ServiceDef] = []
    if isinstance(raw, list):
        for svc in raw:
            if isinstance(svc, str):
                svcs.append(ServiceDef(image=svc))
            elif isinstance(svc, dict):
                image = str(svc.get("name", svc.get("image", "")))
                svcs.append(ServiceDef(image=image))
    return svcs


def _parse_rules(rules: list) -> tuple[list[str], list[str], bool]:
    """Extract push branches, tags, and dispatch flag from GitLab rules."""
    push_branches: list[str] = []
    push_tags: list[str] = []
    dispatch = False

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if_clause = str(rule.get("if", ""))
        when = str(rule.get("when", "on_success"))

        if when == "manual":
            dispatch = True
            continue

        # e.g. $CI_COMMIT_BRANCH == "main"
        branch_m = re.search(r'CI_COMMIT_BRANCH\s*==\s*["\']([^"\']+)["\']', if_clause)
        if branch_m:
            push_branches.append(branch_m.group(1))

        # e.g. $CI_COMMIT_TAG =~ /^v/
        tag_m = re.search(r"CI_COMMIT_TAG", if_clause)
        if tag_m and "=~" in if_clause:
            push_tags.append("*")  # best-effort: tag any pattern → wildcard

    return push_branches, push_tags, dispatch


def _parse_only(only: Any) -> tuple[list[str], list[str]]:
    """Parse legacy `only:` key."""
    push_branches: list[str] = []
    push_tags: list[str] = []

    if isinstance(only, list):
        for item in only:
            item_s = str(item)
            if item_s == "tags":
                push_tags.append("*")
            elif item_s not in ("merge_requests",):
                push_branches.append(item_s)
    elif isinstance(only, dict):
        refs = only.get("refs", [])
        for ref in refs:
            if ref == "tags":
                push_tags.append("*")
            elif ref not in ("merge_requests",):
                push_branches.append(ref)

    return push_branches, push_tags


def _parse_job(
    job_id: str,
    raw: dict,
    global_image: str,
    global_before: list[str],
    global_after: list[str],
    stage_needs: list[str],
) -> JobDef:
    name = str(raw.get("name", job_id))

    # Image: job-level overrides global
    image = str(raw.get("image", global_image))
    container = image

    env = _parse_env(raw.get("variables", {}))

    # Needs / dependencies
    explicit_needs = raw.get("needs", raw.get("dependencies"))
    if explicit_needs is not None:
        if isinstance(explicit_needs, list):
            needs = [(n["job"] if isinstance(n, dict) else str(n)) for n in explicit_needs]
        else:
            needs = [str(explicit_needs)]
    elif stage_needs:
        needs = list(stage_needs)
    else:
        needs = []

    steps: list[StepDef] = []

    # before_script: job-level overrides global
    before = raw.get("before_script", global_before)
    if before:
        run = _translate_ci_vars(_script_to_run(before))
        steps.append(StepDef(name="before_script", run=run))

    # script
    script = raw.get("script")
    if script:
        run = _translate_ci_vars(_script_to_run(script))
        steps.append(StepDef(name="script", run=run))

    # after_script
    after = raw.get("after_script", global_after)
    if after:
        run = _translate_ci_vars(_script_to_run(after))
        steps.append(StepDef(name="after_script", run=run))

    # Services
    services = _parse_services(raw.get("services"))

    # Artifacts → outputs
    outputs: dict = {}
    artifacts = raw.get("artifacts", {}) or {}
    if isinstance(artifacts, dict) and "paths" in artifacts:
        for i, p in enumerate(artifacts["paths"]):
            outputs[f"artifact_{i}"] = str(p)

    todos: list[str] = []

    if "parallel" in raw:
        todos.append("parallel/matrix is not yet supported — define separate jobs or wait for matrix support")

    if "extends" in raw:
        extends_val = raw["extends"]
        todos.append(f"'extends: {extends_val}' — resolve manually; YAML anchors are expanded where possible")

    if "trigger" in raw:
        todos.append(
            "'trigger' (child pipeline) has no direct equivalent — restructure as a separate pipeline"
        )

    if "cache" in raw:
        todos.append("'cache' is a deferred feature — configure caching in a future Astrolift release")

    if "environment" in raw:
        env_name = raw["environment"]
        if isinstance(env_name, dict):
            env_name = env_name.get("name", env_name)
        todos.append(
            f"'environment: {env_name}' — configure environment promotion via Astrolift Environments UI"
        )

    return JobDef(
        job_id=job_id,
        name=name,
        runs_on="astrolift/default",
        container=container,
        needs=needs,
        steps=steps,
        env=env,
        services=services,
        outputs=outputs,
        todos=todos,
    )


def parse(yaml_str: str) -> PipelineDef:
    """Parse a GitLab CI YAML string into a PipelineDef."""
    # Resolve YAML anchors by letting PyYAML expand them.
    raw = yaml.safe_load(yaml_str) or {}

    name = "pipeline"

    global_env = _parse_env(raw.get("variables", {}))
    global_image = str(raw.get("image", ""))
    global_before = raw.get("before_script", [])
    global_after = raw.get("after_script", [])
    stages: list[str] = list(raw.get("stages", []))

    on_push_branches: list[str] = []
    on_push_tags: list[str] = []
    on_pr_branches: list[str] = []
    on_schedule: list[ScheduleDef] = []
    on_dispatch = False
    todos: list[str] = []

    # Handle include (remote templates)
    if "include" in raw:
        todos.append(
            "'include' directives reference external templates — verify they are available in Astrolift"
        )

    # Collect job definitions
    job_defs: list[JobDef] = []

    # Build stage → job_ids mapping for implicit needs DAG
    stage_to_jobs: dict[str, list[str]] = {s: [] for s in stages}

    # Two-pass: first collect stage membership, then build needs
    candidate_jobs: list[tuple[str, dict]] = [
        (k, v)
        for k, v in raw.items()
        if not k.startswith(".") and k not in _NON_JOB_KEYS and isinstance(v, dict)
    ]

    for job_id, job_raw in candidate_jobs:
        stage = str(job_raw.get("stage", stages[0] if stages else ""))
        if stage in stage_to_jobs:
            stage_to_jobs[stage].append(job_id)

    for job_id, job_raw in candidate_jobs:
        stage = str(job_raw.get("stage", stages[0] if stages else ""))
        stage_idx = stages.index(stage) if stage in stages else -1

        # Implicit needs = all jobs in the previous stage
        if stage_idx > 0 and "needs" not in job_raw and "dependencies" not in job_raw:
            prev_stage = stages[stage_idx - 1]
            stage_needs = list(stage_to_jobs.get(prev_stage, []))
        else:
            stage_needs = []

        # Trigger detection (when: manual in job-level rules or when: manual)
        if job_raw.get("when") == "manual":
            on_dispatch = True

        # Pull triggers from rules
        rules = job_raw.get("rules", [])
        if rules:
            br, tg, disp = _parse_rules(rules)
            on_push_branches.extend(br)
            on_push_tags.extend(tg)
            if disp:
                on_dispatch = True
        elif "only" in job_raw:
            br, tg = _parse_only(job_raw["only"])
            on_push_branches.extend(br)
            on_push_tags.extend(tg)

        job_def = _parse_job(job_id, job_raw, global_image, global_before, global_after, stage_needs)
        job_defs.append(job_def)

    # Deduplicate trigger info
    on_push_branches = list(dict.fromkeys(on_push_branches))
    on_push_tags = list(dict.fromkeys(on_push_tags))

    return PipelineDef(
        name=name,
        jobs=job_defs,
        env=global_env,
        on_push_branches=on_push_branches,
        on_push_tags=on_push_tags,
        on_pull_request_branches=on_pr_branches,
        on_schedule=on_schedule,
        on_workflow_dispatch=on_dispatch,
        todos=todos,
    )
