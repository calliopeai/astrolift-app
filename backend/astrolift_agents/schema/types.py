"""GraphQL types for the agent platform.

Covers the Skill + ToolDef registry, Brief, AgentTask, and
AgentEnvironmentSpec.

A :class:`~astrolift_agents.models.skill.Skill` is a versioned unit of
instructions injected into an agent Brief at assembly time; a
:class:`~astrolift_agents.models.skill.ToolDef` describes a callable tool
the agent can invoke. A :class:`~astrolift_agents.models.brief.Brief` is the
pre-assembled, content-hashed package fetched by the agent at boot. An
:class:`~astrolift_agents.models.agent_task.AgentTask` is the discrete unit
of agent dispatch, and an
:class:`~astrolift_agents.models.agent_environment_spec.AgentEnvironmentSpec`
is the reusable org-scoped container-environment recipe.

Wire-format notes
-----------------
* ``adapter``, ``agentType``, ``status``, and related fields are surfaced as
  plain lowercase strings rather than Strawberry enums. Strawberry serializes
  enums by their uppercase Python member name, which would force the FE to
  special-case the casing; the underlying values are already stable lowercase
  tokens read directly by the dispatch layer, so a string keeps both sides in
  lock-step ([[strawberry_enum_wire_format]]).
* The GraphQL ``version`` field on SkillType is sourced from the model column
  ``skill_version`` so it doesn't collide with the row-level optimistic
  concurrency ``version`` carried by ``TrackingMixin``.
"""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Brief, Skill, ToolDef
from astrolift_graphql import GUID


@strawberry.type(name="AstroliftToolDef")
class ToolDefType:
    id: GUID
    skill_slug: str
    name: str
    slug: str
    description: str
    input_schema: strawberry.scalars.JSON
    output_schema: strawberry.scalars.JSON
    adapter: str
    handler_ref: str
    implementation_config: strawberry.scalars.JSON
    commands: strawberry.scalars.JSON
    required_packages: strawberry.scalars.JSON
    capability_group: str
    agent_type_bindings: strawberry.scalars.JSON
    is_builtin: bool
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftSkill")
class SkillType:
    id: GUID
    name: str
    slug: str
    description: str
    content: str
    dependencies: strawberry.scalars.JSON
    content_hash: str
    version: int
    """The skill's content version (model column ``skill_version``).
    Bumped on every content change so a Brief can pin the exact version
    it assembled."""

    is_global: bool
    is_active: bool
    agent_type: str
    scaffolding_tags: strawberry.scalars.JSON
    tool_defs: list[ToolDefType]
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftImportSkillsResult")
class ImportSkillsResultType:
    """Payload returned by ``importSkillsFromRepo``.

    ``sourceRef`` is the canonical ``owner/repo@branch`` the skills were
    parsed from so the caller can echo provenance in the UI. The two
    slug lists name exactly the rows that were created or updated by this
    import (idempotent re-imports report the same slugs)."""

    imported_skills: list[str]
    imported_tools: list[str]
    source_ref: str


def tooldef_to_type(tool: ToolDef) -> ToolDefType:
    return ToolDefType(
        id=GUID(str(tool.guid)),
        skill_slug=tool.skill.slug,
        name=tool.name,
        slug=tool.slug,
        description=tool.description or "",
        input_schema=tool.input_schema or {},
        output_schema=tool.output_schema or {},
        adapter=tool.adapter,
        handler_ref=tool.handler_ref or "",
        implementation_config=tool.implementation_config or {},
        commands=tool.commands or [],
        required_packages=tool.required_packages or [],
        capability_group=tool.capability_group or "",
        agent_type_bindings=tool.agent_type_bindings or [],
        is_builtin=tool.is_builtin,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
    )


def skill_to_type(skill: Skill) -> SkillType:
    # ``tool_defs`` is prefetched by the resolvers; falling back to the
    # related manager keeps this usable from the mutation path too.
    tool_defs = skill.tool_defs.all() if skill.pk else []
    return SkillType(
        id=GUID(str(skill.guid)),
        name=skill.name,
        slug=skill.slug,
        description=skill.description or "",
        content=skill.content or "",
        dependencies=skill.dependencies or [],
        content_hash=skill.content_hash or "",
        version=skill.skill_version,
        is_global=skill.is_global,
        is_active=skill.is_active,
        agent_type=skill.agent_type or "",
        scaffolding_tags=skill.scaffolding_tags or [],
        tool_defs=[tooldef_to_type(t) for t in tool_defs],
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------


@strawberry.type(name="AstroliftBrief")
class BriefType:
    id: GUID
    content_hash: str
    storage_key: str
    status: str
    manifest_snapshot: strawberry.scalars.JSON
    secrets_refs: strawberry.scalars.JSON
    context: strawberry.scalars.JSON
    ttl_seconds: int
    assembled_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


def brief_to_type(brief: Brief) -> BriefType:
    return BriefType(
        id=GUID(str(brief.guid)),
        content_hash=brief.content_hash,
        storage_key=brief.storage_key or "",
        status=brief.status,
        manifest_snapshot=brief.manifest_snapshot or {},
        secrets_refs=brief.secrets_refs or [],
        context=brief.context or {},
        ttl_seconds=brief.ttl_seconds,
        assembled_at=brief.assembled_at,
        created_at=brief.created_at,
        updated_at=brief.updated_at,
    )


# ---------------------------------------------------------------------------
# AgentTask
# ---------------------------------------------------------------------------


@strawberry.type(name="AstroliftAgentTask")
class AgentTaskType:
    id: GUID
    status: str
    external_id: str
    callback_url: str
    timeout_seconds: int
    result: strawberry.scalars.JSON | None
    failure: strawberry.scalars.JSON | None
    queued_at: dt.datetime | None
    provisioning_at: dt.datetime | None
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    telemetry_key: str
    pod_name: str
    source_ref: str
    created_at: dt.datetime
    updated_at: dt.datetime


def agent_task_to_type(task: AgentTask) -> AgentTaskType:
    return AgentTaskType(
        id=GUID(str(task.guid)),
        status=task.status,
        external_id=task.external_id or "",
        callback_url=task.callback_url or "",
        timeout_seconds=task.timeout_seconds,
        result=task.result,
        failure=task.failure,
        queued_at=task.queued_at,
        provisioning_at=task.provisioning_at,
        started_at=task.started_at,
        ended_at=task.ended_at,
        telemetry_key=task.telemetry_key or "",
        pod_name=task.pod_name or "",
        source_ref=task.source_ref or "",
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


# ---------------------------------------------------------------------------
# AgentEnvironmentSpec
# ---------------------------------------------------------------------------


@strawberry.type(name="AstroliftAgentEnvironmentSpec")
class AgentEnvironmentSpecType:
    id: GUID
    name: str
    slug: str
    image_tag: str
    agent_type: str
    tool_preset: str
    allow_install: bool
    vnc_enabled: bool
    secret_ref_count: int
    """Number of secret references wired into this spec. The references
    themselves (the ARNs / vault URIs paired with target env-var names in
    ``secret_refs``) are deliberately NEVER exposed over GraphQL — leaking
    the pointer is enough to help an attacker locate the secret, and the
    value never lives on this row. The UI shows "N secrets wired"; the
    dispatcher resolves the refs to live values at launch time."""
    env_vars: strawberry.scalars.JSON
    config_repo: str
    config_branch: str
    created_at: dt.datetime
    updated_at: dt.datetime


def _secret_ref_count(spec: AgentEnvironmentSpec) -> int:
    refs = spec.secret_refs
    return len(refs) if isinstance(refs, list) else 0


def agent_environment_spec_to_type(spec: AgentEnvironmentSpec) -> AgentEnvironmentSpecType:
    return AgentEnvironmentSpecType(
        id=GUID(str(spec.guid)),
        name=spec.name,
        slug=spec.slug,
        image_tag=spec.image_tag,
        agent_type=spec.agent_type,
        tool_preset=spec.tool_preset or "",
        allow_install=spec.allow_install,
        vnc_enabled=spec.vnc_enabled,
        secret_ref_count=_secret_ref_count(spec),
        env_vars=spec.env_vars or {},
        config_repo=spec.config_repo or "",
        config_branch=spec.config_branch or "main",
        created_at=spec.created_at,
        updated_at=spec.updated_at,
    )
