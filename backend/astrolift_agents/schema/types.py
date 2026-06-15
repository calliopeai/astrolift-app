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
    # Relay path to the live RFB framebuffer; empty until the task is
    # RUNNING on a VNC-capable pod. The client derives the ws URL from it.
    vnc_url: str
    # Short-lived presigned GET URL for the task's latest framebuffer JPEG
    # snapshot (``snapshots/<guid>/latest.jpg``). Non-null only for a RUNNING
    # VNC task whose pod-side uploader has written at least one frame; null
    # otherwise (non-VNC task, no frame yet, or no blob store configured).
    # The gallery polls this to render snapshot tiles before exploding into
    # the live VncViewer session at ``vnc_url``.
    snapshot_url: str | None


@strawberry.type(name="AstroliftAgentEnvironmentSpec")
class AgentEnvironmentSpecType:
    """A reusable, org-scoped recipe for the container environment an
    agent task runs in.

    ``secret_refs`` carries only the URIs/env-var bindings — never the
    secret values (those are resolved by the dispatcher at launch). The
    read surface is operator-facing.
    """

    id: GUID
    name: str
    slug: str
    image_tag: str
    runtime: str
    agent_type: str
    tool_preset: str
    allow_install: bool
    vnc_enabled: bool
    secret_refs: JSON
    env_vars: JSON
    config_repo: str
    config_branch: str
    config_manifest_path: str
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftAgentRuntime")
class AgentRuntimeType:
    """A selectable agent runtime from the public runtime catalog.

    ``name`` is the short-name an AgentEnvironmentSpec references; ``image``
    is the default-tagged public base image it resolves to. The watchable
    ``-vnc`` variant is derived at spawn, not surfaced here.
    """

    name: str
    image: str


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


def _resolve_snapshot_url(t) -> str | None:
    """Mint a short-lived presigned GET URL for ``t``'s latest snapshot.

    Returns ``None`` (never raises) unless the task is a RUNNING, VNC-capable
    task that has a frozen ``snapshot_key`` *and* the pod-side uploader has
    written at least one frame to it. The presign is deliberately gated so the
    common case (non-VNC or not-yet-running tasks) short-circuits before
    touching the blob store — keeping the list resolvers free of per-row blob
    calls for tasks that can't be watched.

    ``BlobStoreNotFoundError`` (no frame uploaded yet) and
    ``BlobStoreNotConfiguredError`` (no blob store on this install) both map to
    ``None`` so the gallery renders a placeholder tile instead of erroring.
    """
    from astrolift_agents.models import AgentTask

    if t.status != AgentTask.Status.RUNNING:
        return None
    if not t.vnc_enabled or not t.snapshot_key:
        return None

    from astrolift_agents.snapshot_store import presigned_snapshot_download_url
    from providers._sdk.blob_store import (
        BlobStoreNotConfiguredError,
        BlobStoreNotFoundError,
    )

    try:
        return presigned_snapshot_download_url(
            org=t.organization,
            task_guid=str(t.guid),
        )
    except (BlobStoreNotFoundError, BlobStoreNotConfiguredError):
        return None


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
        snapshot_url=_resolve_snapshot_url(t),
    )


def dispatcher_to_type(d) -> DispatcherInstanceType:
    return DispatcherInstanceType(
        id=GUID(str(d.guid)),
        service_url=d.endpoint or "",
        capabilities=d.capability_labels or {},
        last_heartbeat=d.last_heartbeat_at,
        registered_at=d.created_at,
    )


def agent_env_spec_to_type(s) -> AgentEnvironmentSpecType:
    return AgentEnvironmentSpecType(
        id=GUID(str(s.guid)),
        name=s.name,
        slug=s.slug,
        image_tag=s.image_tag or "",
        runtime=s.runtime or "",
        agent_type=s.agent_type,
        tool_preset=s.tool_preset or "",
        allow_install=s.allow_install,
        vnc_enabled=s.vnc_enabled,
        secret_refs=s.secret_refs or [],
        env_vars=s.env_vars or {},
        config_repo=s.config_repo or "",
        config_branch=s.config_branch or "main",
        config_manifest_path=s.config_manifest_path or "",
        created_at=s.created_at,
        updated_at=s.updated_at,
    )
