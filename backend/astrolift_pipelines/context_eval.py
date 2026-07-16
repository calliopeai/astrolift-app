"""Pipeline context variable evaluator — ${VAR} substitution (#70).

Resolves ``${...}`` expressions in string values at job dispatch time.
This is substitution-only: no arithmetic, no comparisons, no function
calls. Every ``${...}`` token resolves to a string or raises
``ContextEvalError`` if the variable is unknown.

Context graph (v1, per DSL spec #65):

    ctx.git.sha           — commit SHA
    ctx.git.branch        — branch name (empty for tag pushes)
    ctx.git.tag           — tag name (empty for branch pushes)
    ctx.git.repo          — org/repo string
    ctx.git.actor         — triggering actor's login
    ctx.pipeline.name     — pipeline filename stem
    ctx.pipeline.run_id   — Temporal workflow ID
    ctx.pipeline.run_number  — monotonic integer counter per pipeline
    env.<KEY>             — pipeline-level + job-level env (job wins)
    secrets.<NAME>        — REDACTED at eval time; injected by K8s secret mount
    jobs.<id>.outputs.<key>  — output from an upstream job (needs must be met)

Secret handling: ``${secrets.X}`` resolves to the placeholder string
``__SECRET_<NAME>__`` in the evaluated config that is logged. The actual
secret value is mounted into the pod via K8s secret refs, never
interpolated into a string that touches logs or Temporal history.
"""

from __future__ import annotations

import re
from typing import Any

_VAR_RE = re.compile(r"\$\{([^}]+)\}")

_SECRET_PLACEHOLDER_PREFIX = "__SECRET_"
_SECRET_PLACEHOLDER_SUFFIX = "__"


class ContextEvalError(ValueError):
    """Raised when a context variable cannot be resolved."""


class PipelineContext:
    """Holds the full context available during pipeline job dispatch.

    Attributes:
        git: dict with sha, branch, tag, repo, actor
        pipeline: dict with name, run_id, run_number
        env: merged env dict (pipeline-level, then job-level; job wins)
        secrets: set of known secret names (values never stored here)
        job_outputs: dict mapping job_id → dict of output key→value
    """

    def __init__(
        self,
        *,
        git: dict[str, str] | None = None,
        pipeline: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        secrets: set[str] | None = None,
        job_outputs: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.git: dict[str, str] = git or {}
        self.pipeline: dict[str, Any] = pipeline or {}
        self.env: dict[str, str] = env or {}
        self.secrets: set[str] = secrets or set()
        self.job_outputs: dict[str, dict[str, str]] = job_outputs or {}

    @classmethod
    def from_pipeline_run(cls, run) -> PipelineContext:
        """Build a PipelineContext from a PipelineRun model instance."""
        ref = run.trigger_ref or ""
        branch = ""
        tag = ""
        if ref.startswith("refs/heads/"):
            branch = ref.removeprefix("refs/heads/")
        elif ref.startswith("refs/tags/"):
            tag = ref.removeprefix("refs/tags/")
        else:
            branch = ref  # bare branch name from GitLab MR

        pipeline = run.pipeline
        return cls(
            git={
                "sha": run.trigger_ref or "",
                "branch": branch,
                "tag": tag,
                "repo": "",  # populated by caller from webhook payload
                "actor": run.trigger_actor or "",
            },
            pipeline={
                "name": pipeline.name,
                "run_id": run.temporal_workflow_id or "",
                "run_number": str(run.run_number),
            },
            env={},
        )


def _resolve_variable(var_path: str, ctx: PipelineContext) -> str:
    """Resolve a single variable path to a string value.

    Raises ContextEvalError if the path is unknown or the referenced
    upstream job has no output for the given key.
    """
    parts = var_path.strip().split(".")

    if not parts:
        raise ContextEvalError(f"Empty variable path: ${{{var_path}}}")

    namespace = parts[0]

    if namespace == "ctx":
        if len(parts) < 3:
            raise ContextEvalError(f"ctx variable must have at least two levels: ${{{var_path}}}")
        sub_ns = parts[1]
        key = ".".join(parts[2:])
        if sub_ns == "git":
            if key not in ctx.git:
                raise ContextEvalError(f"Unknown ctx.git variable: {key}")
            return ctx.git[key]
        if sub_ns == "pipeline":
            if key not in ctx.pipeline:
                raise ContextEvalError(f"Unknown ctx.pipeline variable: {key}")
            return str(ctx.pipeline[key])
        raise ContextEvalError(f"Unknown ctx sub-namespace: {sub_ns}")

    if namespace == "env":
        if len(parts) < 2:
            raise ContextEvalError(f"env variable must include a key: ${{{var_path}}}")
        key = ".".join(parts[1:])
        if key not in ctx.env:
            raise ContextEvalError(f"Unknown env variable: {key}")
        return ctx.env[key]

    if namespace == "secrets":
        if len(parts) < 2:
            raise ContextEvalError(f"secrets variable must include a name: ${{{var_path}}}")
        name = ".".join(parts[1:])
        # Secrets are valid if they're in the declared set.
        # The placeholder is what goes into logs/history; the real value
        # is injected by the K8s secret mount, never by this evaluator.
        if name not in ctx.secrets and ctx.secrets:
            raise ContextEvalError(f"Secret not declared in pipeline scope: {name}")
        return f"{_SECRET_PLACEHOLDER_PREFIX}{name}{_SECRET_PLACEHOLDER_SUFFIX}"

    if namespace == "jobs":
        # jobs.<id>.outputs.<key>
        if len(parts) < 4 or parts[2] != "outputs":
            raise ContextEvalError(f"jobs variable must be jobs.<id>.outputs.<key>: ${{{var_path}}}")
        job_id = parts[1]
        output_key = ".".join(parts[3:])
        outputs = ctx.job_outputs.get(job_id)
        if outputs is None:
            raise ContextEvalError(
                f"Job '{job_id}' has no outputs (check `needs` declaration): ${{{var_path}}}"
            )
        if output_key not in outputs:
            raise ContextEvalError(f"Job '{job_id}' has no output '{output_key}': ${{{var_path}}}")
        return outputs[output_key]

    raise ContextEvalError(f"Unknown variable namespace '{namespace}': ${{{var_path}}}")


def evaluate(template: str, ctx: PipelineContext) -> str:
    """Substitute all ``${VAR}`` expressions in ``template``.

    Returns the interpolated string. Raises ``ContextEvalError`` on
    unknown variables. Raises ``TypeError`` if ``template`` is not a string.
    """
    if not isinstance(template, str):
        raise TypeError(f"evaluate() expects a str, got {type(template).__name__}")

    def replacer(match: re.Match) -> str:
        var_path = match.group(1)
        return _resolve_variable(var_path, ctx)

    return _VAR_RE.sub(replacer, template)


def evaluate_dict(data: dict[str, Any], ctx: PipelineContext) -> dict[str, Any]:
    """Recursively substitute variables in all string values of a dict.

    Non-string values (int, bool, None, list, nested dict) are passed
    through. Lists are evaluated element-by-element.
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        result[key] = _eval_value(value, ctx)
    return result


def _eval_value(value: Any, ctx: PipelineContext) -> Any:
    if isinstance(value, str):
        return evaluate(value, ctx)
    if isinstance(value, dict):
        return evaluate_dict(value, ctx)
    if isinstance(value, list):
        return [_eval_value(item, ctx) for item in value]
    return value


def is_secret_placeholder(value: str) -> bool:
    """Return True if ``value`` is a secret placeholder string."""
    return value.startswith(_SECRET_PLACEHOLDER_PREFIX) and value.endswith(_SECRET_PLACEHOLDER_SUFFIX)


def extract_secret_name(placeholder: str) -> str | None:
    """Extract the secret name from a placeholder string, or None."""
    if not is_secret_placeholder(placeholder):
        return None
    return placeholder[len(_SECRET_PLACEHOLDER_PREFIX) : -len(_SECRET_PLACEHOLDER_SUFFIX)]
