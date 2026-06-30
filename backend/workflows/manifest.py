"""Declarative workflow manifest serializer (spec 40 §5.4).

A :class:`~workflows.models.WorkflowDefinition` (tier 1) ⇄ a declarative
**TOML manifest** — the canonical, versionable, human-authorable
representation of a workflow + its ordered stages. The visual builder
(§5.1) is one authoring path; the TOML is the standard.

Two pure functions form the round-trip:

* :func:`parse_workflow_manifest` — TOML → a structured
  :class:`ParsedWorkflowManifest` (definition fields + ordered stage
  specs). No persistence; used for preview/validate and by the
  catalogue seed (40-cat, #969) which parses bundled ``catalogue/*.toml``.
* :func:`emit_workflow_manifest` — a :class:`ParsedWorkflowManifest` →
  TOML. :func:`definition_to_manifest` builds that structured form from a
  persisted ``WorkflowDefinition`` so the export path is
  ``emit_workflow_manifest(definition_to_manifest(defn))``.

The round-trip is lossless: ``parse(emit(parse(x))) == parse(x)`` and
``emit`` is stable. Parsing reuses :mod:`astrolift_manifest`'s ``tomllib``
loader and the spec 39 ``SkillRef`` grammar verbatim for the ``skills``
field — no hand-rolled TOML, no second skill grammar. Validation failures
surface as :class:`~astrolift_manifest.parser.ManifestError` (the same
structured error the app already raises), never a raw exception.
"""

from __future__ import annotations

import dataclasses
import tomllib
from typing import Any

import tomli_w

from astrolift_manifest.parser import (
    ManifestError,
    _line_col_from_message,
    _parse_skills,
)
from astrolift_manifest.types import SkillRef
from workflows.models import WorkflowDefinition, WorkflowStage

# Valid value sets are sourced from the models so the serializer stays in
# lockstep with #966 — a new pattern/kind/on_failure choice needs no edit here.
_VALID_PATTERNS = set(WorkflowDefinition.PatternKind.values)
_VALID_STAGE_KINDS = set(WorkflowStage.StageKind.values)
_VALID_ON_FAILURE = set(WorkflowStage.OnFailure.values)

_DEFAULT_PATTERN = WorkflowDefinition.PatternKind.SINGLE.value
_DEFAULT_ON_FAILURE = WorkflowStage.OnFailure.FAIL.value
_DEFAULT_TIMEOUT = 300


@dataclasses.dataclass
class WorkflowDefSpec:
    """The ``[workflow]`` table — a ``WorkflowDefinition``'s identity fields."""

    slug: str
    name: str
    pattern: str
    description: str = ""


@dataclasses.dataclass
class WorkflowStageSpec:
    """One ``[[stage]]`` — maps 1:1 to a ``WorkflowStage`` (``order`` = index).

    ``fan_out`` mirrors the manifest's tri-state: ``0`` (none), a positive
    ``int`` (static count), or the string ``"dynamic"`` (derive from the
    prior stage's output). ``prompt``/``approvers`` are human-gate fields
    persisted on ``WorkflowStage`` (#969), so :func:`definition_to_manifest`
    emits them and a model-backed export round-trips losslessly.
    """

    order: int
    kind: str
    role: str = ""
    agent: str | None = None
    skills: list[str] = dataclasses.field(default_factory=list)
    on_failure: str = _DEFAULT_ON_FAILURE
    timeout: int = _DEFAULT_TIMEOUT
    fan_out: int | str = 0
    prompt: str | None = None
    approvers: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ParsedWorkflowManifest:
    """The structured form a manifest round-trips through."""

    definition: WorkflowDefSpec
    stages: list[WorkflowStageSpec]


# --------------------------------------------------------------------------- #
# Parse: TOML → ParsedWorkflowManifest
# --------------------------------------------------------------------------- #


def parse_workflow_manifest(toml_str: str) -> ParsedWorkflowManifest:
    """Parse a workflow manifest TOML string into its structured form.

    Raises :class:`ManifestError` (with a dotted ``path`` like
    ``stage[0].kind``) on any syntax or semantic problem so the UI/CLI can
    point at the offending row.
    """
    try:
        data: dict[str, Any] = tomllib.loads(toml_str)
    except tomllib.TOMLDecodeError as exc:
        line, col = _line_col_from_message(str(exc))
        raise ManifestError(f"invalid TOML: {exc}", line=line, column=col) from exc

    workflow = data.get("workflow")
    if not isinstance(workflow, dict):
        raise ManifestError("missing required [workflow] table", path="workflow")

    definition = _parse_definition(workflow)

    raw_stages = data.get("stage", [])
    if not isinstance(raw_stages, list):
        raise ManifestError("[[stage]] must be an array of tables", path="stage")

    stages = [_parse_stage(item, i) for i, item in enumerate(raw_stages)]
    return ParsedWorkflowManifest(definition=definition, stages=stages)


def _parse_definition(d: dict[str, Any]) -> WorkflowDefSpec:
    slug = _require_str(d, "slug", "workflow.slug")
    name = _require_str(d, "name", "workflow.name")

    pattern = d.get("pattern", _DEFAULT_PATTERN)
    if not isinstance(pattern, str) or pattern not in _VALID_PATTERNS:
        raise ManifestError(
            f"pattern must be one of {sorted(_VALID_PATTERNS)}, got {pattern!r}",
            path="workflow.pattern",
        )

    description = d.get("description", "")
    if not isinstance(description, str):
        raise ManifestError("description must be a string", path="workflow.description")

    return WorkflowDefSpec(slug=slug, name=name, pattern=pattern, description=description)


def _parse_stage(d: Any, index: int) -> WorkflowStageSpec:
    base = f"stage[{index}]"
    if not isinstance(d, dict):
        raise ManifestError("each [[stage]] must be a table", path=base)

    kind = _require_str(d, "kind", f"{base}.kind")
    if kind not in _VALID_STAGE_KINDS:
        raise ManifestError(
            f"kind must be one of {sorted(_VALID_STAGE_KINDS)}, got {kind!r}",
            path=f"{base}.kind",
        )

    role = d.get("role", "")
    if not isinstance(role, str):
        raise ManifestError("role must be a string", path=f"{base}.role")

    agent = d.get("agent")
    if agent is not None and (not isinstance(agent, str) or not agent.strip()):
        raise ManifestError(
            "agent must be a non-empty local agent slug (omit it for role-only globals)",
            path=f"{base}.agent",
        )

    # Reuse the spec 39 skill-ref grammar verbatim (local / catalogue /
    # org-repo). _parse_skills validates each entry and returns SkillRefs;
    # we re-canonicalize back to strings for storage + lossless emit.
    skill_refs = _parse_skills(d.get("skills", []), f"{base}.skills")
    skills = [_skillref_to_string(r) for r in skill_refs]

    on_failure = d.get("on_failure", _DEFAULT_ON_FAILURE)
    if not isinstance(on_failure, str) or on_failure not in _VALID_ON_FAILURE:
        raise ManifestError(
            f"on_failure must be one of {sorted(_VALID_ON_FAILURE)}, got {on_failure!r}",
            path=f"{base}.on_failure",
        )

    timeout = d.get("timeout", _DEFAULT_TIMEOUT)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 0:
        raise ManifestError("timeout must be a non-negative integer (seconds)", path=f"{base}.timeout")

    fan_out = _parse_fan_out(d.get("fan_out", 0), f"{base}.fan_out")

    prompt = d.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ManifestError("prompt must be a string", path=f"{base}.prompt")

    approvers = d.get("approvers", [])
    if not isinstance(approvers, list) or any(not isinstance(a, str) for a in approvers):
        raise ManifestError("approvers must be a list of strings", path=f"{base}.approvers")

    return WorkflowStageSpec(
        order=index,
        kind=kind,
        role=role,
        agent=agent,
        skills=skills,
        on_failure=on_failure,
        timeout=timeout,
        fan_out=fan_out,
        prompt=prompt,
        approvers=list(approvers),
    )


def _parse_fan_out(value: Any, path: str) -> int | str:
    """Manifest tri-state: ``0``/omit = none, ``int>0`` = static, ``"dynamic"``."""
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise ManifestError('fan_out must be a non-negative integer or "dynamic"', path=path)
    if isinstance(value, int):
        if value < 0:
            raise ManifestError('fan_out must be a non-negative integer or "dynamic"', path=path)
        return value
    if isinstance(value, str):
        if value != "dynamic":
            raise ManifestError(f'fan_out string must be "dynamic", got {value!r}', path=path)
        return "dynamic"
    raise ManifestError('fan_out must be a non-negative integer or "dynamic"', path=path)


def _skillref_to_string(ref: SkillRef) -> str:
    """Reconstruct the spec 39 reference string from a parsed ``SkillRef``.

    Inverse of :func:`astrolift_manifest.parser._parse_skill_string` for the
    string forms a workflow manifest uses (local ``./path``, catalogue
    ``name[@ref]``, org-repo ``alias/subpath[@ref]``).
    """
    if ref.kind == "local":
        return ref.path or ref.name
    if ref.kind == "org_repo":
        body = f"{ref.repo_alias}/{ref.skill_subpath}"
    else:  # catalogue
        body = ref.name
    return f"{body}@{ref.ref}" if ref.ref else body


def _require_str(d: dict[str, Any], key: str, path: str) -> str:
    value = d.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestError(f"required string {key!r} is missing or empty", path=path)
    return value


# --------------------------------------------------------------------------- #
# Emit: ParsedWorkflowManifest → TOML
# --------------------------------------------------------------------------- #


def emit_workflow_manifest(parsed: ParsedWorkflowManifest) -> str:
    """Serialize a structured manifest back to canonical TOML.

    Only non-default fields are written, so output is minimal and stable;
    ``parse_workflow_manifest`` restores the same defaults, keeping the
    round-trip lossless.
    """
    workflow: dict[str, Any] = {
        "slug": parsed.definition.slug,
        "name": parsed.definition.name,
        "pattern": parsed.definition.pattern,
    }
    if parsed.definition.description:
        workflow["description"] = parsed.definition.description

    doc: dict[str, Any] = {"workflow": workflow}

    stages: list[dict[str, Any]] = []
    for stage in parsed.stages:
        row: dict[str, Any] = {"kind": stage.kind}
        if stage.role:
            row["role"] = stage.role
        if stage.agent is not None:
            row["agent"] = stage.agent
        if stage.skills:
            row["skills"] = list(stage.skills)
        if stage.on_failure != _DEFAULT_ON_FAILURE:
            row["on_failure"] = stage.on_failure
        if stage.timeout != _DEFAULT_TIMEOUT:
            row["timeout"] = stage.timeout
        if stage.fan_out != 0:
            row["fan_out"] = stage.fan_out
        if stage.prompt is not None:
            row["prompt"] = stage.prompt
        if stage.approvers:
            row["approvers"] = list(stage.approvers)
        stages.append(row)

    if stages:
        doc["stage"] = stages

    return tomli_w.dumps(doc)


def definition_to_manifest(definition: WorkflowDefinition) -> ParsedWorkflowManifest:
    """Build the structured form from a persisted ``WorkflowDefinition``.

    Reads the definition's ordered stages so ``emit_workflow_manifest`` can
    render the export TOML. The fan-out tri-state maps back from the two
    columns (#969): ``fan_out_dynamic`` → ``"dynamic"``, a positive
    ``fan_out_count`` → that static count, otherwise ``0`` (none). The
    ``prompt``/``approvers`` columns emit verbatim, so a model-backed export
    is now lossless (closes the #973 gap).
    """
    def_spec = WorkflowDefSpec(
        slug=definition.slug or "",
        name=definition.name or "",
        pattern=definition.pattern_kind,
        description=definition.description or "",
    )

    stages: list[WorkflowStageSpec] = []
    rows = definition.stages.order_by("order").select_related("agent_definition")
    for stage in rows:
        agent_slug = stage.agent_definition.slug if stage.agent_definition else None
        if stage.fan_out_dynamic:
            fan_out: int | str = "dynamic"
        elif stage.fan_out_count:
            fan_out = stage.fan_out_count
        else:
            fan_out = 0
        stages.append(
            WorkflowStageSpec(
                order=stage.order,
                kind=stage.kind,
                role=stage.role or "",
                agent=agent_slug,
                skills=list(stage.skill_refs or []),
                on_failure=stage.on_failure,
                timeout=stage.timeout_seconds,
                fan_out=fan_out,
                prompt=stage.prompt or None,
                approvers=list(stage.approvers or []),
            )
        )

    return ParsedWorkflowManifest(definition=def_spec, stages=stages)
