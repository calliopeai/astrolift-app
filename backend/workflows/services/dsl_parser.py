"""In-repo YAML DSL parser for user-authored WorkflowDefinitions (#866).

Parses ``.astrolift/workflows.yaml`` from a tenant's source repo into
structured dicts that ``workflow_sync.sync_workflows_from_repo`` can
upsert into the database.

File format (YAML)::

    workflows:
      - slug: "code-review"
        name: "Code Review"
        pattern_kind: "chained"
        model_label: "workflows.workflowdefinition"   # optional; defaults to ""
        states:                                         # optional hand-override
          - name: "pending"
            label: "Pending"
            is_initial: true
            is_final: false
          - name: "done"
            label: "Done"
            is_initial: false
            is_final: true
        transitions:                                    # optional hand-override
          - from_state: "pending"
            to_state: "done"
            label: "Complete"
        stages:
          - kind: "agent_dispatch"
            skill_refs: ["code-reviewer"]
            timeout_seconds: 300
            on_failure: "retry"
          - kind: "human_gate"
            timeout_seconds: 3600
            on_failure: "fail"

Only ``slug``, ``name``, and ``stages`` are required on each workflow
entry. All other fields carry documented defaults. Stage ``kind`` is
required; every other stage field is optional.

``parse_workflows_dsl`` is pure (no DB access); validation is also pure.
The upsert lives in ``workflow_sync``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml

from workflows.back_edges import (
    LoopContractError,
    validate_back_edge,
    validate_loop_plan,
)
from workflows.stage_limits import DEFAULT_STAGE_ATTEMPTS, validate_stage_attempts

# Valid choices mirror WorkflowDefinition.PatternKind and
# WorkflowStage.StageKind / WorkflowStage.OnFailure. We keep them here
# as frozensets so the parser doesn't import Django models at module
# load time (keeps this importable in non-Django contexts like tests
# that don't need the full ORM stack).
_VALID_PATTERN_KINDS = frozenset(
    {"single", "chained", "fan_out", "review_loop"}
)
_VALID_STAGE_KINDS = frozenset({"agent_dispatch", "human_gate", "checkpoint", "aggregation", "workflow", "collection", "format_record"})
_VALID_ON_FAILURE = frozenset({"fail", "retry", "skip", "escalate"})

# Default states/transitions injected when the author omits them. The
# default is minimal: a two-state machine (pending → done) that the
# stage execution layer overrides in practice. Authors who want richer
# state machines can supply them explicitly.
_DEFAULT_STATES = [
    {"name": "pending", "label": "Pending", "is_initial": True, "is_final": False},
    {"name": "done", "label": "Done", "is_initial": False, "is_final": True},
]
_DEFAULT_TRANSITIONS = [
    {"from_state": "pending", "to_state": "done", "label": "Complete"},
]


class DslParseError(ValueError):
    """Raised when the DSL file cannot be parsed or contains structural errors."""


def parse_workflows_dsl(yaml_content: str) -> list[dict]:
    """Parse YAML content into a list of WorkflowDefinition dicts.

    Each returned dict has the shape::

        {
            "slug": str,
            "name": str,
            "pattern_kind": str,
            "model_label": str,
            "states": list[dict],
            "transitions": list[dict],
            "is_enabled": bool,
            "stages": [
                {
                    "order": int,              # 0-based, derived from list position
                    "kind": str,
                    "skill_refs": list[str],
                    "fan_out_count": int | None,
                    "on_failure": str,
                    "timeout_seconds": int,
                },
                ...
            ],
        }

    Raises ``DslParseError`` on YAML syntax errors or when the top-level
    structure is missing the ``workflows`` key. Per-workflow validation
    errors are surfaced via ``validate_workflow_dsl``; this function
    only does structural parsing.
    """
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise DslParseError(f"YAML syntax error: {exc}") from exc

    if not isinstance(data, dict):
        raise DslParseError("top-level document must be a YAML mapping")

    raw_workflows = data.get("workflows")
    if not isinstance(raw_workflows, list):
        raise DslParseError("document must contain a 'workflows' list")

    return [_parse_workflow_entry(entry, index) for index, entry in enumerate(raw_workflows)]


def _parse_workflow_entry(entry: Any, index: int) -> dict:
    """Parse a single workflow entry dict from the YAML list."""
    path = f"workflows[{index}]"
    if not isinstance(entry, dict):
        raise DslParseError(f"{path}: each workflow entry must be a mapping")

    slug = _require_str(entry, "slug", path)
    name = _require_str(entry, "name", path)

    pattern_kind = str(entry.get("pattern_kind", "single")).lower()
    model_label = str(entry.get("model_label", ""))
    is_enabled = bool(entry.get("is_enabled", True))

    states = entry.get("states", _DEFAULT_STATES)
    if not isinstance(states, list):
        raise DslParseError(f"{path}.states: must be a list")

    transitions = entry.get("transitions", _DEFAULT_TRANSITIONS)
    if not isinstance(transitions, list):
        raise DslParseError(f"{path}.transitions: must be a list")

    raw_stages = entry.get("stages")
    if not isinstance(raw_stages, list):
        raise DslParseError(f"{path}.stages: must be a list")

    stages = [_parse_stage_entry(stage, index, stage_idx) for stage_idx, stage in enumerate(raw_stages)]

    return {
        "slug": slug,
        "name": name,
        "pattern_kind": pattern_kind,
        "model_label": model_label,
        "is_enabled": is_enabled,
        "states": states,
        "transitions": transitions,
        "stages": stages,
    }


def _parse_stage_entry(entry: Any, workflow_index: int, stage_index: int) -> dict:
    """Parse a single stage entry dict from the YAML list."""
    path = f"workflows[{workflow_index}].stages[{stage_index}]"
    if not isinstance(entry, dict):
        raise DslParseError(f"{path}: each stage entry must be a mapping")

    kind = _require_str(entry, "kind", path)

    skill_refs = entry.get("skill_refs", [])
    if not isinstance(skill_refs, list):
        raise DslParseError(f"{path}.skill_refs: must be a list")
    skill_refs = [str(s) for s in skill_refs]

    fan_out_raw = entry.get("fan_out_count")
    fan_out_count = int(fan_out_raw) if fan_out_raw is not None else None

    on_failure = str(entry.get("on_failure", "fail")).lower()

    from workflows.collections import validate_iteration

    try:
        iteration = validate_iteration(entry.get("iteration", {}), kind=kind)
    except ValueError as exc:
        raise DslParseError(f"{path}.iteration: {exc}") from exc

    try:
        back_edge = validate_back_edge(entry.get("back_edge", {}), kind=kind)
    except LoopContractError as exc:
        raise DslParseError(f"{path}.back_edge: {exc}") from exc

    try:
        max_attempts = validate_stage_attempts(entry.get("max_attempts", DEFAULT_STAGE_ATTEMPTS))
    except ValueError as exc:
        raise DslParseError(f"{path}.max_attempts: {exc}") from exc

    timeout_seconds_raw = entry.get("timeout_seconds", 300)
    try:
        timeout_seconds = int(timeout_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise DslParseError(
            f"{path}.timeout_seconds: must be an integer, got {timeout_seconds_raw!r}"
        ) from exc

    text_fields = {}
    for field in ("agent_ref", "workflow_ref", "environment_spec_slug", "role", "prompt", "output_key"):
        value = entry.get(field, "")
        if not isinstance(value, str):
            raise DslParseError(f"{path}.{field}: must be a string")
        text_fields[field] = value
    approvers = entry.get("approvers", [])
    if not isinstance(approvers, list) or any(not isinstance(value, str) for value in approvers):
        raise DslParseError(f"{path}.approvers: must be a list of strings")
    fan_out_dynamic = entry.get("fan_out_dynamic", False)
    if not isinstance(fan_out_dynamic, bool):
        raise DslParseError(f"{path}.fan_out_dynamic: must be a boolean")

    return {
        **text_fields,
        "approvers": approvers,
        "fan_out_dynamic": fan_out_dynamic,
        "order": stage_index,
        "kind": kind,
        "skill_refs": skill_refs,
        "fan_out_count": fan_out_count,
        "on_failure": on_failure,
        "max_attempts": max_attempts,
        "back_edge": back_edge,
        "iteration": iteration,
        "timeout_seconds": timeout_seconds,
    }


def validate_workflow_dsl(definition: dict) -> list[str]:
    """Return a list of validation error strings for a parsed workflow dict.

    ``definition`` must be one entry from the list returned by
    ``parse_workflows_dsl``. An empty return list means the definition
    is valid.

    This is a pure function — no DB access, no I/O.
    """
    errors: list[str] = []

    slug = definition.get("slug", "")
    if not slug:
        errors.append("slug is required and must be non-empty")

    name = definition.get("name", "")
    if not name:
        errors.append("name is required and must be non-empty")

    pattern_kind = definition.get("pattern_kind", "")
    if pattern_kind not in _VALID_PATTERN_KINDS:
        errors.append(
            f"pattern_kind {pattern_kind!r} is not valid; " f"must be one of {sorted(_VALID_PATTERN_KINDS)}"
        )

    stages = definition.get("stages", [])
    if not stages:
        errors.append("at least one stage is required")

    for i, stage in enumerate(stages):
        stage_path = f"stages[{i}]"
        kind = stage.get("kind", "")
        if kind not in _VALID_STAGE_KINDS:
            errors.append(
                f"{stage_path}.kind {kind!r} is not valid; " f"must be one of {sorted(_VALID_STAGE_KINDS)}"
            )
        on_failure = stage.get("on_failure", "fail")
        if on_failure not in _VALID_ON_FAILURE:
            errors.append(
                f"{stage_path}.on_failure {on_failure!r} is not valid; "
                f"must be one of {sorted(_VALID_ON_FAILURE)}"
            )
        try:
            validate_stage_attempts(stage.get("max_attempts", DEFAULT_STAGE_ATTEMPTS))
        except ValueError as exc:
            errors.append(f"{stage_path}.max_attempts: {exc}")
        timeout_seconds = stage.get("timeout_seconds", 300)
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            errors.append(
                f"{stage_path}.timeout_seconds must be a positive integer, " f"got {timeout_seconds!r}"
            )

    try:
        validate_loop_plan(stages, pattern_kind=pattern_kind, require_review_loop=False)
    except LoopContractError as exc:
        errors.append(str(exc))
    return errors


def _require_str(d: dict, key: str, path: str) -> str:
    value = d.get(key)
    if not isinstance(value, str) or not value:
        raise DslParseError(f"{path}.{key}: required string is missing or empty")
    return value


def emit_workflows_dsl(definitions: list[dict]) -> str:
    """Emit the full parsed authoring contract, including immutable return targets."""
    rows = deepcopy(definitions)
    for definition in rows:
        errors = validate_workflow_dsl(definition)
        if errors:
            raise DslParseError("; ".join(errors))
        for stage in definition["stages"]:
            stage.pop("order", None)
    return yaml.safe_dump({"workflows": rows}, sort_keys=False, allow_unicode=True)
