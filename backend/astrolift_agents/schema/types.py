"""GraphQL types for the Agent Dispatch Layer.

Surfaces Skills, ToolDefs, Briefs, AgentTasks, and DispatcherInstances.
Token hashes, secret references, and internal routing credentials are
intentionally NOT exposed — the read surface is operator-facing only.
"""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftSkill")
class SkillType:
    id: GUID
    name: str
    slug: str
    description: str
    content: str
    skill_version: int
    is_global: bool
    is_active: bool
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftToolDef")
class ToolDefType:
    id: GUID
    name: str
    slug: str
    description: str
    adapter: str
    input_schema: JSON
    output_schema: JSON
    handler_ref: str
    created_at: dt.datetime


@strawberry.type(name="AstroliftBrief")
class BriefType:
    id: GUID
    content_hash: str
    storage_key: str
    # The Brief model splits resolved config into ``manifest_snapshot``
    # (env/manifest overrides) — surfaced here as ``config``. Secret
    # references and raw context are not exposed on the read surface.
    config: JSON
    created_at: dt.datetime


@strawberry.type(name="AstroliftAgentTask")
class AgentTaskType:
    id: GUID
    status: str
    callback_url: str
    # Terminal output payload; null until the task reaches a terminal
    # state with a result (or on a failure-only outcome).
    result: JSON | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    # Maps to the model's ``ended_at`` (any terminal transition stamps it).
    finished_at: dt.datetime | None
    # True when this task launched on a VNC-capable pod.
    vnc_enabled: bool
    # Relay path to the live noVNC framebuffer; empty until the task is
    # RUNNING on a VNC-capable pod. The client derives the ws URL from it.
    vnc_url: str


@strawberry.type(name="AstroliftDispatcherInstance")
class DispatcherInstanceType:
    id: GUID
    # Maps to the model's ``endpoint`` (registration/heartbeat URL).
    service_url: str
    # Maps to the model's ``capability_labels`` (routing match tags).
    capabilities: JSON
    last_heartbeat: dt.datetime | None
    # Maps to BaseCoreModel ``created_at`` (when the instance registered).
    registered_at: dt.datetime


def skill_to_type(s) -> SkillType:
    return SkillType(
        id=GUID(str(s.guid)),
        name=s.name,
        slug=s.slug,
        description=s.description or "",
        content=s.content or "",
        skill_version=s.skill_version,
        is_global=s.is_global,
        is_active=s.is_active,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def tool_def_to_type(t) -> ToolDefType:
    return ToolDefType(
        id=GUID(str(t.guid)),
        name=t.name,
        slug=t.slug,
        description=t.description or "",
        adapter=t.adapter,
        input_schema=t.input_schema or {},
        output_schema=t.output_schema or {},
        handler_ref=t.handler_ref or "",
        created_at=t.created_at,
    )


def brief_to_type(b) -> BriefType:
    return BriefType(
        id=GUID(str(b.guid)),
        content_hash=b.content_hash,
        storage_key=b.storage_key or "",
        config=b.manifest_snapshot or {},
        created_at=b.created_at,
    )


def agent_task_to_type(t) -> AgentTaskType:
    return AgentTaskType(
        id=GUID(str(t.guid)),
        status=t.status,
        callback_url=t.callback_url or "",
        result=t.result,
        created_at=t.created_at,
        started_at=t.started_at,
        finished_at=t.ended_at,
        vnc_enabled=t.vnc_enabled,
        vnc_url=t.vnc_url or "",
    )


def dispatcher_to_type(d) -> DispatcherInstanceType:
    return DispatcherInstanceType(
        id=GUID(str(d.guid)),
        service_url=d.endpoint or "",
        capabilities=d.capability_labels or {},
        last_heartbeat=d.last_heartbeat_at,
        registered_at=d.created_at,
    )
