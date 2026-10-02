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
import json
import tomllib
from typing import Any

import tomli_w

from astrolift_manifest.parser import (
    ManifestError,
    _line_col_from_message,
    _parse_skills,
)
from astrolift_manifest.types import SkillRef
from core.run_input_contract import InputContractError, no_input_schema, validate_schema
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.stage_limits import DEFAULT_STAGE_ATTEMPTS, validate_stage_attempts

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
    input_schema: dict = dataclasses.field(default_factory=no_input_schema)


@dataclasses.dataclass
class WorkflowStageSpec:
    """One ``[[stage]]`` — maps 1:1 to a ``WorkflowStage`` (``order`` = index).

    ``fan_out`` mirrors the manifest's tri-state: ``0`` (none), a positive
    ``int`` (static count), or the string ``"dynamic"`` (derive from the
    prior stage's output). Agent stages may select an environment recipe,
    add instructions and publish their structured result under
    ``output_key``; human gates reuse ``prompt`` for their approval question.
    """

    order: int
    kind: str
    role: str = ""
    agent: str | None = None
    workflow: str | None = None
    environment_spec_slug: str | None = None
    skills: list[str] = dataclasses.field(default_factory=list)
    on_failure: str = _DEFAULT_ON_FAILURE
    max_attempts: int = DEFAULT_STAGE_ATTEMPTS
    timeout: int = _DEFAULT_TIMEOUT
    fan_out: int | str = 0
    prompt: str | None = None
    output_key: str | None = None
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
    output_keys = [stage.output_key or f"stage_{stage.order}" for stage in stages]
    duplicates = sorted({key for key in output_keys if output_keys.count(key) > 1})
    if duplicates:
        raise ManifestError(
            "output_key values must be unique: " + ", ".join(duplicates),
            path="stage.output_key",
        )
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

    try:
        input_schema = (
            validate_schema(json.loads(d["input_schema_json"]))
            if "input_schema_json" in d
            else no_input_schema()
        )
    except (InputContractError, ValueError, TypeError) as exc:
        raise ManifestError(
            "input_schema_json must contain a supported JSON Schema object", path="workflow.input_schema_json"
        ) from exc
    return WorkflowDefSpec(
        slug=slug, name=name, pattern=pattern, description=description, input_schema=input_schema
    )


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

    workflow_ref = d.get("workflow")
    if workflow_ref is not None and (not isinstance(workflow_ref, str) or not workflow_ref.strip()):
        raise ManifestError(
            "workflow must be a non-empty child workflow slug",
            path=f"{base}.workflow",
        )
    if kind == WorkflowStage.StageKind.WORKFLOW and workflow_ref is None:
        raise ManifestError(
            'kind="workflow" requires a workflow child slug',
            path=f"{base}.workflow",
        )
    if kind != WorkflowStage.StageKind.WORKFLOW and workflow_ref is not None:
        raise ManifestError(
            'workflow is only valid when kind="workflow"',
            path=f"{base}.workflow",
        )

    environment_spec_slug = d.get("environment_spec_slug")
    if environment_spec_slug is not None and (
        not isinstance(environment_spec_slug, str) or not environment_spec_slug.strip()
    ):
        raise ManifestError(
            "environment_spec_slug must be a non-empty string",
            path=f"{base}.environment_spec_slug",
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

    try:
        max_attempts = validate_stage_attempts(d.get("max_attempts", DEFAULT_STAGE_ATTEMPTS))
    except ValueError as exc:
        raise ManifestError(str(exc), path=f"{base}.max_attempts") from exc

    timeout = d.get("timeout", _DEFAULT_TIMEOUT)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 0:
        raise ManifestError("timeout must be a non-negative integer (seconds)", path=f"{base}.timeout")

    fan_out = _parse_fan_out(d.get("fan_out", 0), f"{base}.fan_out")

    prompt = d.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ManifestError("prompt must be a string", path=f"{base}.prompt")

    output_key = d.get("output_key")
    if output_key is not None and (not isinstance(output_key, str) or not output_key.strip()):
        raise ManifestError("output_key must be a non-empty string", path=f"{base}.output_key")

    approvers = d.get("approvers", [])
    if not isinstance(approvers, list) or any(not isinstance(a, str) for a in approvers):
        raise ManifestError("approvers must be a list of strings", path=f"{base}.approvers")

    return WorkflowStageSpec(
        order=index,
        kind=kind,
        role=role,
        agent=agent,
        workflow=workflow_ref,
        environment_spec_slug=environment_spec_slug,
        skills=skills,
        on_failure=on_failure,
        max_attempts=max_attempts,
        timeout=timeout,
        fan_out=fan_out,
        prompt=prompt,
        output_key=output_key,
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
    if parsed.definition.input_schema != no_input_schema():
        workflow["input_schema_json"] = json.dumps(
            parsed.definition.input_schema, sort_keys=True, separators=(",", ":")
        )

    doc: dict[str, Any] = {"workflow": workflow}

    stages: list[dict[str, Any]] = []
    for stage in parsed.stages:
        row: dict[str, Any] = {"kind": stage.kind}
        if stage.role:
            row["role"] = stage.role
        if stage.agent is not None:
            row["agent"] = stage.agent
        if stage.workflow is not None:
            row["workflow"] = stage.workflow
        if stage.environment_spec_slug is not None:
            row["environment_spec_slug"] = stage.environment_spec_slug
        if stage.skills:
            row["skills"] = list(stage.skills)
        if stage.on_failure != _DEFAULT_ON_FAILURE:
            row["on_failure"] = stage.on_failure
        if stage.max_attempts != DEFAULT_STAGE_ATTEMPTS:
            row["max_attempts"] = validate_stage_attempts(stage.max_attempts)
        if stage.timeout != _DEFAULT_TIMEOUT:
            row["timeout"] = stage.timeout
        if stage.fan_out != 0:
            row["fan_out"] = stage.fan_out
        if stage.prompt is not None:
            row["prompt"] = stage.prompt
        if stage.output_key is not None:
            row["output_key"] = stage.output_key
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
        input_schema=definition.input_schema,
    )

    stages: list[WorkflowStageSpec] = []
    rows = definition.stages.order_by("order").select_related("agent_definition")
    for stage in rows:
        agent_slug = stage.agent_definition.slug if stage.agent_definition else (stage.agent_ref or None)
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
                workflow=stage.workflow_ref or None,
                environment_spec_slug=stage.environment_spec_slug or None,
                skills=list(stage.skill_refs or []),
                on_failure=stage.on_failure,
                max_attempts=stage.max_attempts,
                timeout=stage.timeout_seconds,
                fan_out=fan_out,
                prompt=stage.prompt or None,
                output_key=stage.output_key or None,
                approvers=list(stage.approvers or []),
            )
        )

    return ParsedWorkflowManifest(definition=def_spec, stages=stages)


# --------------------------------------------------------------------------- #
# Persist: ParsedWorkflowManifest → org-scoped WorkflowDefinition + stages
# --------------------------------------------------------------------------- #


def _fan_out_columns(fan_out: int | str) -> tuple[int | None, bool]:
    """Map the manifest fan-out tri-state to the model's two columns (#969)."""
    if fan_out == "dynamic":
        return None, True
    if isinstance(fan_out, int) and not isinstance(fan_out, bool) and fan_out > 0:
        return fan_out, False
    return None, False


def _unique_definition_slug(base_slug: str, organization) -> str:
    """First free ``(organization, slug)`` derived from ``base_slug``."""
    candidate = base_slug or "imported-workflow"
    suffix = 0
    while WorkflowDefinition.objects.filter(
        organization=organization, slug=candidate, deleted_at__isnull=True
    ).exists():
        suffix += 1
        candidate = f"{base_slug}-{suffix}"
    return candidate


def create_definition_from_manifest(
    parsed: ParsedWorkflowManifest, *, organization, created_by=None, is_enabled: bool = False
):
    """Persist a structured manifest as an org-scoped ``WorkflowDefinition`` +
    its ordered stages. The single create path shared by the visual-flow
    importers (#984) and any future native-TOML create surface — it consumes
    the same ``ParsedWorkflowManifest`` the parser and importers emit, so the
    persistence rules (org scope, fan-out tri-state → two columns, role-only
    globals) live in exactly one place.

    Imported definitions land disabled for operator review by default. Local
    ``agent`` slugs are retained in ``agent_ref`` and eagerly bound when the
    matching org workload already exists; otherwise they remain late-bound
    and can resolve after that agent is registered. The slug is made unique
    within the org on collision.

    ``is_enabled`` defaults to ``False`` for a brand-new import; the
    ``replace``-versioning path (#1822) passes the superseded definition's
    own ``is_enabled`` so a version created to replace an already-enabled,
    in-use definition does not land disabled under configured Workflows that
    keep firing on a schedule.
    """
    slug = _unique_definition_slug(parsed.definition.slug, organization)
    definition = WorkflowDefinition.objects.create(
        organization=organization,
        name=parsed.definition.name,
        slug=slug,
        description=parsed.definition.description or "",
        pattern_kind=parsed.definition.pattern,
        input_schema=parsed.definition.input_schema,
        model_label="",
        is_enabled=is_enabled,
        created_by=created_by,
        updated_by=created_by,
    )
    for stage in parsed.stages:
        agent_definition = None
        if stage.agent:
            from astrolift_registry.models import Workload

            agent_definition = Workload.objects.filter(
                registered_app__organization=organization,
                slug=stage.agent,
                kind=Workload.Kind.AGENT,
                deleted_at__isnull=True,
            ).first()
        fan_out_count, fan_out_dynamic = _fan_out_columns(stage.fan_out)
        WorkflowStage.objects.create(
            definition=definition,
            slug=f"{slug}-stage-{stage.order}",
            order=stage.order,
            kind=stage.kind,
            role=stage.role or "",
            agent_definition=agent_definition,
            agent_ref=stage.agent or "",
            workflow_ref=stage.workflow or "",
            environment_spec_slug=stage.environment_spec_slug or "",
            skill_refs=list(stage.skills),
            on_failure=stage.on_failure,
            max_attempts=validate_stage_attempts(stage.max_attempts),
            timeout_seconds=stage.timeout,
            fan_out_count=fan_out_count,
            fan_out_dynamic=fan_out_dynamic,
            prompt=stage.prompt or "",
            output_key=stage.output_key or "",
            approvers=list(stage.approvers),
            created_by=created_by,
            updated_by=created_by,
        )
    return definition


# --------------------------------------------------------------------------- #
# Replace: re-import onto an existing org-owned definition (#1822)
# --------------------------------------------------------------------------- #


def shape_compatible(definition: WorkflowDefinition, parsed: ParsedWorkflowManifest) -> bool:
    """True when ``parsed`` can overwrite ``definition``'s stage *content* in
    place: the same number of live stages, in the same ``kind`` sequence.

    Positional, not ``order``-value, comparison: a stage soft-deleted by
    ``deleteWorkflowStage`` can leave gaps in ``order``, while a manifest's
    stage order is always the contiguous ``0..N-1`` array index. Content
    fields (prompt, bindings, timeouts, ...) are free to differ; only the
    sequence of kinds has to match, because that sequence is what a
    configured ``Workflow.stage_bindings`` (keyed by order) and any in-flight
    Temporal run (which reads the stage plan once, at start) depend on.
    """
    live = list(definition.stages.filter(deleted_at__isnull=True).order_by("order"))
    if len(live) != len(parsed.stages):
        return False
    manifest_stages = sorted(parsed.stages, key=lambda s: s.order)
    return all(existing.kind == spec.kind for existing, spec in zip(live, manifest_stages, strict=True))


def replace_definition_content(
    definition: WorkflowDefinition,
    parsed: ParsedWorkflowManifest,
    *,
    organization,
    updated_by=None,
) -> None:
    """Overwrite ``definition``'s own fields and its existing stages' content
    from ``parsed`` in place. Caller must already know :func:`shape_compatible`
    is true.

    Every stage is matched to the manifest positionally and edited on its
    existing row: never deleted and recreated. That preserves three things
    a stage's pk is load-bearing for: a configured ``Workflow.stage_bindings``
    dict keyed by ``order`` (unchanged, since ``order`` is never touched
    here), a ``WorkflowStageExecution.stage`` FK (``on_delete=PROTECT``) from
    any past run, and the stage plan an in-flight Temporal run already
    fetched once at start (``get_workflow_stages``: see
    ``astrolift_workflows.activities.workflow_stage_activities``) and will
    keep replaying from its own history regardless of what this writes.
    """
    definition.name = parsed.definition.name
    definition.description = parsed.definition.description or ""
    definition.pattern_kind = parsed.definition.pattern
    definition.input_schema = parsed.definition.input_schema
    definition.updated_by = updated_by
    definition.save(
        update_fields=[
            "name",
            "description",
            "pattern_kind",
            "input_schema",
            "updated_by",
            "updated_at",
            "version",
        ]
    )

    live = list(definition.stages.filter(deleted_at__isnull=True).order_by("order"))
    manifest_stages = sorted(parsed.stages, key=lambda s: s.order)
    for stage_row, stage_spec in zip(live, manifest_stages, strict=True):
        agent_definition = None
        if stage_spec.agent:
            from astrolift_registry.models import Workload

            agent_definition = Workload.objects.filter(
                registered_app__organization=organization,
                slug=stage_spec.agent,
                kind=Workload.Kind.AGENT,
                deleted_at__isnull=True,
            ).first()
        fan_out_count, fan_out_dynamic = _fan_out_columns(stage_spec.fan_out)
        stage_row.role = stage_spec.role or ""
        stage_row.agent_definition = agent_definition
        stage_row.agent_ref = stage_spec.agent or ""
        stage_row.workflow_ref = stage_spec.workflow or ""
        stage_row.environment_spec_slug = stage_spec.environment_spec_slug or ""
        stage_row.skill_refs = list(stage_spec.skills)
        stage_row.max_attempts = validate_stage_attempts(stage_spec.max_attempts)
        stage_row.on_failure = stage_spec.on_failure
        stage_row.timeout_seconds = stage_spec.timeout
        stage_row.fan_out_count = fan_out_count
        stage_row.fan_out_dynamic = fan_out_dynamic
        stage_row.prompt = stage_spec.prompt or ""
        stage_row.output_key = stage_spec.output_key or ""
        stage_row.approvers = list(stage_spec.approvers)
        stage_row.updated_by = updated_by
        stage_row.save(
            update_fields=[
                "role",
                "agent_definition",
                "agent_ref",
                "workflow_ref",
                "environment_spec_slug",
                "skill_refs",
                "on_failure",
                "max_attempts",
                "timeout_seconds",
                "fan_out_count",
                "fan_out_dynamic",
                "prompt",
                "output_key",
                "approvers",
                "updated_by",
                "updated_at",
                "version",
            ]
        )


@dataclasses.dataclass
class ReplaceOutcome:
    """How :func:`replace_definition_from_manifest` applied the manifest.

    ``mode``:

    * ``"created"``: no live org-owned definition shared this slug; same as
      a plain import (a platform-global template sharing the slug, if any,
      is left untouched: globals are read-only to tenants).
    * ``"updated_in_place"``: stage kinds matched positionally, so the
      existing definition and its ``WorkflowStage`` rows were edited in
      place (same pks, same ``order``): configured Workflows, their
      bindings, their Temporal schedules and any in-flight run are all
      unaffected, because nothing about identity moved.
    * ``"versioned"``: the shape changed, so a new ``WorkflowDefinition``
      was created (``definition``) and every configured Workflow that
      pointed at the old one was repointed to it (``repointed_slugs``),
      because every one of them validated clean against the new shape.
    * ``"blocked"``: the shape changed and repointing would break at least
      one configured Workflow's ``stage_bindings`` (``blocked_errors``,
      keyed by Workflow slug). Nothing was written: the attempted new
      version is rolled back, not left as an orphan.
    """

    definition: WorkflowDefinition | None
    mode: str
    repointed_slugs: list[str] = dataclasses.field(default_factory=list)
    blocked_errors: dict[str, list[str]] = dataclasses.field(default_factory=dict)


def replace_definition_from_manifest(
    parsed: ParsedWorkflowManifest, *, organization, created_by=None
) -> ReplaceOutcome:
    """Idempotent upsert of ``parsed`` onto the org's own definition sharing
    its slug (#1822): see :class:`ReplaceOutcome` for the four outcomes.

    Never targets a platform-global template (``organization`` is null): the
    lookup is org-owned only, exactly like :func:`create_definition_from_manifest`,
    so a shared slug with a global is simply not "the org's own" and a fresh
    org-scoped definition is created instead, same as a plain import.

    Self-contained transaction: a ``"blocked"`` outcome leaves the database
    exactly as it was, even though reaching that verdict requires creating
    the candidate new version first (to validate every configured Workflow's
    bindings against its actual stages). Safe to nest inside a caller's own
    ``transaction.atomic()``: ``set_rollback`` only unwinds to this
    function's savepoint.
    """
    from django.core.exceptions import ValidationError as DjangoValidationError
    from django.db import transaction

    from workflows.models import Workflow

    existing = WorkflowDefinition.objects.filter(
        organization=organization, slug=parsed.definition.slug, deleted_at__isnull=True
    ).first()

    with transaction.atomic():
        if existing is None:
            definition = create_definition_from_manifest(
                parsed, organization=organization, created_by=created_by
            )
            return ReplaceOutcome(definition=definition, mode="created")

        if shape_compatible(existing, parsed):
            replace_definition_content(existing, parsed, organization=organization, updated_by=created_by)
            return ReplaceOutcome(definition=existing, mode="updated_in_place")

        new_definition = create_definition_from_manifest(
            parsed, organization=organization, created_by=created_by, is_enabled=existing.is_enabled
        )

        configured = list(
            Workflow.objects.filter(organization=organization, definition=existing, deleted_at__isnull=True)
        )
        blocked: dict[str, list[str]] = {}
        for wf in configured:
            wf.definition = new_definition
            try:
                wf.validate_bindings()
            except DjangoValidationError as exc:
                blocked[wf.slug or str(wf.guid)] = list(exc.messages)
        if blocked:
            transaction.set_rollback(True)
            return ReplaceOutcome(definition=None, mode="blocked", blocked_errors=blocked)

        for wf in configured:
            wf.updated_by = created_by
            wf.save(update_fields=["definition", "updated_by", "updated_at", "version"])

        return ReplaceOutcome(
            definition=new_definition,
            mode="versioned",
            repointed_slugs=[wf.slug or str(wf.guid) for wf in configured],
        )
