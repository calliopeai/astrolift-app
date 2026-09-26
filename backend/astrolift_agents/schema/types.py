"""GraphQL types for the Agent Dispatch Layer.

Surfaces Skills, ToolDefs, Briefs, AgentTasks, and DispatcherInstances.
Token hashes, secret references, and internal routing credentials are
intentionally NOT exposed — the read surface is operator-facing only.
"""

from __future__ import annotations

import datetime as dt
import enum

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.enum
class AgentRunFamily(enum.Enum):
    """The agent's native Job-vs-Deployment split (spec 33).

    Mirrors :class:`astrolift_registry.models.Workload.RunFamily`:

    - ``TASK``    — runs to completion (a k8s Job; the dispatch pipeline).
                    Carries ``run_mode`` (once/loop/schedule) + the loop
                    concurrency cap (``run_max_parallel``).
    - ``SERVICE`` — always-on (a k8s Deployment); carries ``replicas`` and
                    the scheduled-scaling cron pair.

    The wire form is the uppercase member name (``TASK`` / ``SERVICE``);
    the resolver maps ``.value`` to the lowercase model string.
    """

    TASK = "task"
    SERVICE = "service"


@strawberry.enum
class AgentRunMode(enum.Enum):
    """The Task-family trigger mode (spec 33).

    Mirrors :class:`astrolift_registry.models.Workload.RunMode`; ignored
    when ``run_family == SERVICE`` (a Service is always-on, not dispatched):

    - ``ONCE``     — manual Dispatch-now (PR-1).
    - ``LOOP``     — continuous re-dispatch with a concurrency cap (PR-6).
    - ``SCHEDULE`` — cron-driven, reads ``run_cron_expression`` (PR-4).
    - ``TRIGGER``  — bound to a webhook / event / condition (PR-6).
    """

    ONCE = "once"
    LOOP = "loop"
    SCHEDULE = "schedule"
    TRIGGER = "trigger"


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


@strawberry.type(name="AstroliftAgentTaskDispatcher")
class AgentTaskDispatcherType:
    """The dispatcher an AgentTask was routed to, projected for the fleet
    map (#1091).

    A lightweight reference — routing identity plus the cluster the
    dispatcher spawns onto — never the endpoint or ``api_key_hash``.
    ``cluster_id`` is the co-located :class:`TenantCluster`'s GUID, which
    the map correlates with cluster heartbeat liveness to colour the
    cluster layer; it is null when the dispatcher runs standalone.
    """

    id: GUID
    name: str
    slug: str
    cloud: str
    region: str
    cluster_id: GUID | None
    cluster_name: str


@strawberry.type(name="AstroliftAgentTaskEvent")
class AgentTaskEventType:
    sequence: int
    turn_id: str
    message_id: str
    kind: str
    text: str
    created_at: dt.datetime
    request: JSON | None = None


@strawberry.type(name="AstroliftAgentTaskInputReply")
class AgentTaskInputReplyType:
    id: GUID
    request_sequence: int
    response: JSON
    author_label: str
    created_at: dt.datetime


@strawberry.type(name="AstroliftAgentTask")
class AgentTaskType:
    id: GUID
    agent_slug: str
    agent_name: str
    project_slug: str
    status: str
    callback_url: str
    # Terminal output payload; null until the task reaches a terminal
    # state with a result (or on a failure-only outcome).
    result: JSON | None
    # Human-readable failure reason for a terminal-failed task — the spawn /
    # dispatch error message off the model's ``failure`` blob. Null unless the
    # task failed. Crucial for debugging spawn failures, which never produce pod
    # logs (the pod was never created), so ``agentTaskLogs`` is empty and this
    # is the only signal.
    failure_message: str | None
    event_sequence: int
    created_at: dt.datetime
    # Lifecycle cursor for the fleet map (#1091): ``transition_to`` bumps
    # ``updated_at`` on every state change, so it is the incremental cursor
    # the live map polls on — there is no separate transition-log model.
    updated_at: dt.datetime
    # Pre-run pipeline stamps (null until the task reaches that state).
    queued_at: dt.datetime | None
    provisioning_at: dt.datetime | None
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
    # Node-layer projection for the fleet dispatch map (#1091): the pod the
    # task runs as, the namespace it landed in, and the dispatcher that
    # routed it (with the cluster it spawned onto). ``dispatcher`` is null
    # until the Controller selects one at PROVISIONING.
    pod_name: str
    namespace: str
    dispatcher: AgentTaskDispatcherType | None


@strawberry.type(name="AstroliftAgentInteraction")
class AgentInteractionType:
    """One control-plane-observed interaction for an AgentTask (#1216).

    The per-agent-task telemetry the LiveFlowMap P3 interaction map reads:
    an authenticated agent->controller Control API call, a control-plane
    tool call, a signal, or a gate stage. ``kind`` is the interaction class
    (``control_api`` / ``tool_call`` / ``signal`` / ``gate``); ``name`` is a
    stable label (endpoint / tool / signal / gate stage); ``status`` is the
    coarse outcome (``ok`` / ``error`` / ``pending`` / ``approved`` /
    ``rejected``); ``detail`` is a small structured payload (http method,
    status code, new_status, ...).
    """

    id: GUID
    kind: str
    name: str
    status: str
    occurred_at: dt.datetime
    detail: JSON


@strawberry.type(name="AstroliftAgentTaskInputMessage")
class AgentTaskInputMessageType:
    """A follow-up prompt queued for a running AgentTask (#1390).

    The steering channel is queued, not duplex: the message sits here until
    the runner reaches its next turn boundary (one harness invocation
    finishing) and consumes it through the state callback. ``deliveredAt``
    is null while it is still queued and stamped once the runner has taken
    it — which is also the signal that a harness with no follow-up support
    left it behind.
    """

    id: GUID
    message: str
    # Display label frozen at enqueue time; empty for a system/automation
    # caller. Deliberately not a user object — the run history has to stay
    # readable after the author is deleted.
    author: str
    created_at: dt.datetime
    delivered_at: dt.datetime | None
    client_request_id: str | None = None


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
    # When on, the task pod uses the cluster's cloud-native model provider
    # (AWS→Bedrock, GCP→Vertex) via a workload-identity ServiceAccount
    # instead of an ANTHROPIC_API_KEY (resolved by the dispatcher at spawn).
    managed_model: bool
    run_as_non_root: bool
    box_workspace: bool
    model_gateway: bool
    gpu: int
    gpu_type: str
    mig_profile: str
    secret_refs: JSON
    env_vars: JSON
    config_repo: str
    config_branch: str
    config_manifest_path: str
    # The owner (#1866): a project (with its team), or a team alone. Both
    # null means the spec is org-shared: every spec reader in the org sees it
    # and every team's agents may run with it.
    team_id: GUID | None
    project_id: GUID | None
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftAgentBox")
class AgentBoxType:
    """One warm pod that exists to be attached to (#128).

    Not an AgentTask: a task is dispatched, does a thing and ends, and its
    interesting fields are about its outcome. A box has no outcome. What a
    caller needs from it is where it is, whether it is attachable yet, and
    the command that lands them inside it.

    ``attach_command`` is returned rather than assembled by each client so
    the tmux invocation lives in one place. It is ``new-session -A``, which
    attaches to the session if it exists and creates it if it does not, so a
    client racing the pod's own keep-alive loop attaches instead of erroring.
    """

    id: GUID
    name: str
    slug: str
    status: str
    # The agent / spec the box was ensured from. Either may be empty: a box
    # ensured from a spec alone has no agent, which is the path the IDE
    # button takes since it knows a runtime, not a registered workload.
    agent_slug: str
    environment_spec_slug: str
    image: str
    # Seconds of no attached client *and* no pane output before the in-pod
    # loop ends the session. ``0`` means never reap.
    idle_timeout_seconds: int
    # The tmux session inside the pod. Constant across every box, and kept
    # on the type because the CLI reads it; see ``box_attach_command`` for
    # why it is not per-box (#1470).
    session_name: str
    # What to run under ``astro exec --app <slug> --`` to join the session.
    attach_command: list[str]
    namespace: str
    pod_name: str
    # Whose box this is. An org surface that cannot say which node belongs to
    # whom cannot answer "can I destroy this one". Empty for a box ensured by
    # an API token, which has no human owner and is shared org-wide.
    owner_email: str
    # Non-empty only on a box that failed to start, whose teardown did not
    # complete, or whose gateway key cannot be renewed past Zentinelle's key
    # lifetime (#1851); it is the sentence an operator needs, not a stack trace.
    last_error: str
    created_at: dt.datetime
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    last_attached_at: dt.datetime | None


def agent_box_to_type(box) -> AgentBoxType:
    from _sdk.agent_session import SESSION_NAME

    from astrolift_agents.services.agent_box import box_attach_command

    return AgentBoxType(
        id=GUID(str(box.guid)),
        name=box.name,
        slug=box.slug,
        status=box.status,
        agent_slug=(box.agent_definition.slug if box.agent_definition_id is not None else ""),
        environment_spec_slug=(box.environment_spec.slug if box.environment_spec_id is not None else ""),
        image=box.image or "",
        idle_timeout_seconds=int(box.idle_timeout_seconds),
        session_name=SESSION_NAME,
        attach_command=box_attach_command(box),
        namespace=box.namespace or "",
        pod_name=box.pod_name or "",
        owner_email=(getattr(box.owner, "email", "") or "" if box.owner_id is not None else ""),
        last_error=box.last_error or "",
        created_at=box.created_at,
        started_at=box.started_at,
        ended_at=box.ended_at,
        last_attached_at=box.last_attached_at,
    )


@strawberry.type(name="AstroliftAgentSecretStatus")
class AgentSecretStatusType:
    """Presence metadata for one AgentEnvironmentSpec secret ref — never the
    value. ``exists`` is true when the install's secret store holds a
    non-empty value at the ref's ``uri``. ``error`` carries a short
    driver/backend message when the presence check couldn't complete (and
    ``exists`` is then false) so the surface degrades instead of failing.
    """

    env_var: str
    uri: str
    exists: bool
    error: str | None
    provider: str = ""
    can_reveal: bool = False
    read_limitation: str | None = None


@strawberry.type(name="AstroliftAgentSecretReveal")
class AgentSecretRevealType:
    env_var: str
    uri: str
    value: str
    provider: str
    revealed_at: dt.datetime


@strawberry.type(name="AstroliftAgentSecretBundle")
class AgentSecretBundleType:
    id: GUID
    slug: str
    name: str
    backend_ref: str
    key_names: list[str]
    provider: str
    can_reveal: bool
    read_limitation: str | None
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftAgentSecretBundleAttachment")
class AgentSecretBundleAttachmentType:
    id: GUID
    bundle_id: GUID
    bundle_slug: str
    bundle_name: str
    environment: str
    prefix: str
    position: int
    key_names: list[str]


@strawberry.type(name="AstroliftOrgSkillRepo")
class OrgSkillRepoType:
    """A per-org registered skill repo (spec 39d).

    An org registers skill repos under short aliases; a manifest references a
    skill in one as ``"<alias>/<skill-path>@<ref>"``. ``sourceConnectionId``
    is the GUID of the linked :class:`SourceConnection` for a PRIVATE repo, or
    null for a PUBLIC repo fetched anonymously — the credential itself is never
    surfaced. ``isPrivate`` is the convenience derivation the UI renders.
    """

    id: GUID
    alias: str
    repo_full_name: str
    source_kind: str
    default_ref: str
    display_name: str
    is_active: bool
    is_private: bool
    source_connection_id: GUID | None
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


@strawberry.type(name="AstroliftAgentTrigger")
class AgentTriggerType:
    """A WorkflowWebhook bound to an agent ``Workload`` (spec 33, PR-6 / #951).

    The read surface for the run-spec editor's Trigger card: shows the bound
    inbound webhook (slug + endpoint), its SCM fan-out filters, the
    ``input_mapping`` applied to the firing payload, and the enabled flag
    (``unbind`` flips it off). The signing secret is NEVER surfaced here — it
    is shown once at creation only.
    """

    slug: str
    endpoint: str
    scm_repo: str
    branch_pattern: str
    input_mapping: JSON
    enabled: bool
    last_triggered_at: dt.datetime | None
    created_at: dt.datetime


def agent_trigger_to_type(hook) -> AgentTriggerType:
    org_slug = hook.organization.slug if hook.organization_id else "default"
    return AgentTriggerType(
        slug=hook.slug,
        endpoint=f"/api/webhooks/workflow/{org_slug}/{hook.slug}",
        scm_repo=hook.scm_repo or "",
        branch_pattern=hook.branch_pattern or "",
        input_mapping=hook.input_mapping or {},
        enabled=hook.enabled,
        last_triggered_at=hook.last_triggered_at,
        created_at=hook.created_at,
    )


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


def _agent_task_dispatcher_to_type(d) -> AgentTaskDispatcherType | None:
    """Project an AgentTask's :class:`DispatcherInstance` (+ its co-located
    cluster) for the fleet map. ``None`` when the task has no dispatcher yet
    (draft/queued, before the Controller routes it at PROVISIONING). Reads
    only fields the resolver ``select_related``s (``dispatcher`` and
    ``dispatcher__tenant_cluster``), so it adds no query per task."""
    if d is None:
        return None
    cluster = d.tenant_cluster
    if cluster is not None and cluster.organization_id not in {None, d.organization_id}:
        cluster = None
    return AgentTaskDispatcherType(
        id=GUID(str(d.guid)),
        name=d.name or "",
        slug=d.slug or "",
        cloud=d.cloud or "",
        region=d.region or "",
        cluster_id=GUID(str(cluster.guid)) if cluster is not None else None,
        cluster_name=(cluster.name or "") if cluster is not None else "",
    )


def _agent_task_failure_message(failure) -> str | None:
    """Pull the human-readable message out of the model's ``failure`` blob.

    ``failure`` is a JSONField — a ``{"message": ...}`` dict for a spawn/
    dispatch error, but tolerate a bare string or other shapes. Returns None
    when there's no failure recorded.
    """
    if not failure:
        return None
    if isinstance(failure, dict):
        msg = failure.get("message")
        return str(msg) if msg else None
    return str(failure)


def agent_tasks_to_types(tasks) -> list[AgentTaskType]:
    from astrolift_agents.visibility import watchable_task_ids

    rows = list(tasks)
    watchable = watchable_task_ids(rows)
    return [agent_task_to_type(task, can_watch=task.pk in watchable) for task in rows]


def agent_task_to_type(t, *, can_watch: bool | None = None) -> AgentTaskType:
    if can_watch is None:
        from astrolift_agents.visibility import watchable_task_ids

        can_watch = t.pk in watchable_task_ids([t])
    definition = t.agent_definition
    if definition is not None and definition.registered_app.organization_id != t.organization_id:
        definition = None
    project = t.project
    if project is not None and project.organization_id != t.organization_id:
        project = None
    dispatcher = t.dispatcher
    if dispatcher is not None and dispatcher.organization_id != t.organization_id:
        dispatcher = None
    return AgentTaskType(
        id=GUID(str(t.guid)),
        agent_slug=definition.slug if definition is not None else "",
        agent_name=definition.name if definition is not None else "",
        project_slug=project.slug if project is not None else "",
        status=t.status,
        callback_url=t.callback_url or "",
        result=t.result,
        failure_message=_agent_task_failure_message(t.failure),
        event_sequence=t.event_sequence,
        created_at=t.created_at,
        updated_at=t.updated_at,
        queued_at=t.queued_at,
        provisioning_at=t.provisioning_at,
        started_at=t.started_at,
        finished_at=t.ended_at,
        vnc_enabled=t.vnc_enabled,
        vnc_url=(t.vnc_url or "") if can_watch else "",
        snapshot_url=_resolve_snapshot_url(t) if can_watch else None,
        pod_name=t.pod_name or "",
        namespace=t.namespace or "",
        dispatcher=_agent_task_dispatcher_to_type(dispatcher),
    )


def agent_interaction_to_type(ixn) -> AgentInteractionType:
    return AgentInteractionType(
        id=GUID(str(ixn.guid)),
        kind=ixn.kind,
        name=ixn.name,
        status=ixn.status,
        occurred_at=ixn.occurred_at,
        detail=ixn.detail or {},
    )


def agent_task_input_message_to_type(msg) -> AgentTaskInputMessageType:
    return AgentTaskInputMessageType(
        id=GUID(str(msg.guid)),
        message=msg.body,
        author=msg.author_label or "",
        created_at=msg.created_at,
        delivered_at=msg.delivered_at,
        client_request_id=str(msg.client_request_id) if msg.client_request_id else None,
    )


def dispatcher_to_type(d) -> DispatcherInstanceType:
    return DispatcherInstanceType(
        id=GUID(str(d.guid)),
        service_url=d.endpoint or "",
        capabilities=d.capability_labels or {},
        last_heartbeat=d.last_heartbeat_at,
        registered_at=d.created_at,
    )


@strawberry.type(name="AstroliftAgentListItem")
class AgentListItemType:
    """One row on the Agents list (spec 33 PR-2).

    Projects a ``kind: agent`` :class:`Workload` plus the source-repo
    coordinates from its :class:`RegisteredApp` and the run-spec fields
    (PR-1) the list needs to render a row, alongside a compact last-run
    summary so the list doesn't need a second round-trip per agent.

    The run-spec fields (``run_family`` / ``run_mode`` / ``run_paused`` /
    ``run_cron_expression``) drive the schedule/idle badges; the
    ``last_run_*`` fields render the "last ran 5m ago — succeeded" chip;
    ``running_count`` powers the live "N running" pill. ``last_run_*`` and
    ``running_count`` are computed in bulk by the resolver, not per row.
    """

    id: GUID
    name: str
    slug: str
    app_slug: str
    project_slug: str
    source_repo: str
    source_url: str
    run_family: str
    run_mode: str
    run_paused: bool
    run_cron_expression: str
    # Compact last-run summary (most recent AgentRun for this workload).
    last_run_status: str | None
    last_run_at: dt.datetime | None
    # Number of currently-running AgentRuns for this workload.
    running_count: int
    # Run-spec write-field read parity (#952). The run-spec editors (PR-11
    # #949 / PR-12 #950) read the current spec off this row — the only agent
    # read path the detail page has — so they seed from the persisted values
    # on first load instead of defaults. Mirrors the same fields on
    # :class:`AgentRunSpecType`: ``run_max_parallel`` (Task Loop cap; null =
    # platform default), ``replicas`` (Service baseline), the Service
    # scheduled-scaling target + crons. Defaults keep construction backward
    # compatible.
    run_max_parallel: int | None = None
    replicas: int = 1
    scheduled_scale_to: int | None = None
    scale_up_cron: str = ""
    scale_down_cron: str = ""


@strawberry.type(name="AstroliftAgentListItemPage")
class AgentListItemPageType:
    items: list[AgentListItemType]
    next_cursor: str | None = strawberry.field(default=None)
    total_count: int | None = strawberry.field(default=None)


@strawberry.type(name="AstroliftAgentTaskPage")
class AgentTaskPageType:
    items: list[AgentTaskType]
    next_cursor: str | None = strawberry.field(default=None)
    total_count: int | None = strawberry.field(default=None)


@strawberry.type(name="AstroliftAgentSkill")
class AgentSkillType:
    """One Skill attached to an agent, with its ToolDefs nested (spec 38
    Phase 4).

    Wraps the shared :class:`SkillType` rather than adding a ``tool_defs``
    field to it directly — the bare ``SkillType`` is reused by the Skill
    Registry surface where the per-skill tools are fetched separately, so
    nesting them here keeps that surface unchanged while letting the agent
    detail bundle a skill and its tools in one shape.

    ``position`` is the skill's ordinal within the agent's skill set (the
    :class:`AgentSkillRef` ordering); ``tool_defs`` is every ToolDef on the
    skill, ordered by slug, and is empty when the skill exposes no tools.
    """

    position: int
    skill: SkillType
    tool_defs: list[ToolDefType]


@strawberry.type(name="AstroliftAgentDetail")
class AgentDetailType:
    """Full read bundle for one ``kind: agent`` :class:`Workload` (spec
    38 Phase 4) — the Build tab's content.

    Composes the agent identity + run-spec basics (the same fields the
    list row carries, so the detail header reads back consistently), the
    primary container image coordinates, the definitional Brief (nullable
    — an agent may be registered before a Brief is assembled), and the
    agent's Skills ordered by :class:`AgentSkillRef` position with each
    skill's ToolDefs nested.

    ``image_ref`` / ``dockerfile_path`` come from the workload's primary
    :class:`Container` (the one with ``is_primary=True``, falling back to
    the first container); both are empty strings when the agent has no
    container row yet. ``brief`` is null until Phase-3 registration (or a
    later re-assembly) populates ``Workload.brief``. ``skills`` is empty
    for an agent with no :class:`AgentSkillRef` rows.
    """

    id: GUID
    name: str
    slug: str
    app_slug: str
    source_repo: str
    run_family: str
    run_mode: str
    run_paused: bool
    run_cron_expression: str
    # Primary container image coordinates (empty when no container yet).
    image_ref: str
    dockerfile_path: str
    # Definitional Brief (Workload.brief); null until assembled.
    brief: BriefType | None
    # Attached Skills, ordered by AgentSkillRef.position, tools nested.
    skills: list[AgentSkillType]
    # Service-family scale config read parity (#1012) — the write surface
    # already exists in updateAgentRunSpec; these expose it back. Defaults
    # keep Task-family detail construction unchanged.
    replicas: int = 1
    scheduled_scale_to: int | None = None
    scale_up_cron: str = ""
    scale_down_cron: str = ""


@strawberry.type(name="AstroliftAgentLiveStatus")
class AgentLiveStatusType:
    """Live operational status for one agent workload (spec 33 PR-2).

    A per-agent rollup the Agents list + detail header poll: how many
    runs are in flight, when the agent last ran and how it ended,
    whether it's paused, whether it's idle (nothing running), and — for
    a ``run_mode == schedule`` agent — when it is next due to fire.

    ``next_scheduled_at`` is non-null only when the agent is on the
    ``schedule`` run mode, has a valid ``run_cron_expression``, and is
    not paused. It is computed from the cron expression using the
    platform's existing cron evaluator (``cron_matches``), scanning
    forward minute-by-minute; null when the agent isn't schedule-driven,
    is paused, or no firing falls inside the look-ahead window.
    """

    workload_id: GUID
    workload_slug: str
    app_slug: str
    run_family: str
    run_mode: str
    is_paused: bool
    # True when no AgentRun is currently RUNNING for this agent.
    is_idle: bool
    running_count: int
    last_run_status: str | None
    last_run_at: dt.datetime | None
    # Next cron firing (UTC) for a schedule-mode, unpaused agent; null
    # otherwise. See the class docstring for the exact non-null contract.
    next_scheduled_at: dt.datetime | None
    # Service-family (run_family=service) live Deployment status (#1012):
    # the Deployment IS the run, so these are its replica counts read live
    # from the cluster. Null for Task-family agents (which use the
    # running_count / last_run_* fields above). ``deployment_ready`` is
    # True when ready==desired and desired>0; False when deployed but not
    # ready; null when the agent isn't a Service agent or isn't deployed.
    desired_replicas: int | None = None
    ready_replicas: int | None = None
    deployment_ready: bool | None = None


@strawberry.type(name="AstroliftAgentRunSpec")
class AgentRunSpecType:
    """The full run-spec of one ``kind=agent`` :class:`Workload` (spec 33).

    The write surface for ``updateAgentRunSpec`` returns this so the
    Run/Schedule/Service editor (PR-11) and the Loop/Trigger/scaling
    editor (PR-12) read back exactly what they wrote in one round-trip.

    Carries BOTH families' fields:

    - Task family: ``run_mode`` (once/loop/schedule/trigger),
      ``run_cron_expression`` (schedule mode), ``run_max_parallel``
      (loop cap; null = platform default), ``run_paused``.
    - Service family: ``replicas`` (current desired count),
      ``scheduled_scale_to`` (scale-up target), ``scale_up_cron`` /
      ``scale_down_cron``.

    ``run_family`` / ``run_mode`` are surfaced as lowercase strings to
    match the read surface (:class:`AgentListItemType` /
    :class:`AgentLiveStatusType`), not the uppercase input enums.
    """

    id: GUID
    slug: str
    kind: str
    run_family: str
    run_mode: str
    run_cron_expression: str
    run_paused: bool
    # Null means "no explicit loop cap" — the Loop tick falls back to the
    # platform default (DEFAULT_LOOP_MAX_PARALLEL), NOT unbounded.
    run_max_parallel: int | None
    # Service replica count the manifest renderer reads (the scaling tick
    # writes the scheduled count back onto it).
    replicas: int
    # Service scheduled-scaling: the up-cron scales to ``scheduled_scale_to``
    # (clamped to the env ceiling by the tick), the down-cron scales to 0.
    scheduled_scale_to: int | None
    scale_up_cron: str
    scale_down_cron: str


@strawberry.type(name="AstroliftDiscoveredAgentManifest")
class DiscoveredAgentManifestType:
    """One agent manifest found by scanning a repo (spec 33 PR-3).

    Backs the monorepo-discovery step of the agent onboarding wizard: the
    operator points at a repo, the scan walks ``agents/*/astrolift.toml`` +
    a root ``astrolift.toml``, and each agent manifest comes back as one of
    these preview rows WITHOUT anything being persisted. The operator then
    confirms registration via ``registerAgentRepo``.

    ``manifest_path`` is the repo-relative path (the value that becomes
    ``RegisteredApp.manifest_path`` and is the key registration dedupes on).
    ``name`` is the manifest's top-level name; ``slug`` is the agent
    workload's name; ``workload_kind`` is always ``"agent"``.
    ``already_registered`` is True when an app for that repo + manifest path
    already exists, so the wizard can mark agents that are already onboarded
    rather than offering to register them twice.
    """

    manifest_path: str
    name: str
    slug: str
    workload_kind: str
    already_registered: bool


@strawberry.type(name="AstroliftScanAgentManifestsResult")
class ScanAgentManifestsResultType:
    """Outcome of a repo agent-manifest scan (spec 33 PR-3).

    ``ok`` is True when the scan ran (``agents`` may still be empty when the
    repo has no agent manifests); False when the repo couldn't be fetched
    (no source connection / SCM error), in which case ``error`` carries the
    operator-facing message and ``agents`` is empty.
    """

    ok: bool
    agents: list[DiscoveredAgentManifestType]
    error: str | None = None


def agent_run_spec_to_type(w) -> AgentRunSpecType:
    """Project a ``kind=agent`` :class:`Workload` to its run-spec type."""
    return AgentRunSpecType(
        id=GUID(str(w.guid)),
        slug=w.slug,
        kind=w.kind,
        run_family=w.run_family,
        run_mode=w.run_mode,
        run_cron_expression=w.run_cron_expression or "",
        run_paused=w.run_paused,
        run_max_parallel=w.run_max_parallel,
        replicas=w.replicas,
        scheduled_scale_to=w.scheduled_scale_to,
        scale_up_cron=w.scale_up_cron or "",
        scale_down_cron=w.scale_down_cron or "",
    )


def _primary_container(w):
    """The workload's primary container, or the first one, or None.

    Reads from the prefetched ``containers`` set (the agent-detail
    resolver prefetches it) so no per-call query fires. Prefers the row
    flagged ``is_primary`` — the manifest guarantees exactly one — and
    falls back to the first container when none is flagged (an older row),
    or ``None`` when the workload has no container.
    """
    containers = list(w.containers.all())
    if not containers:
        return None
    for c in containers:
        if c.is_primary:
            return c
    return containers[0]


def agent_detail_to_type(w) -> AgentDetailType:
    """Project a ``kind=agent`` :class:`Workload` to its full detail
    bundle (spec 38 Phase 4).

    Reads the run-spec basics off the workload, the image coordinates off
    its primary container, the Brief off ``w.brief`` (nullable), and the
    Skills off the ``agent_skill_refs`` join ordered by ``position`` with
    each skill's ToolDefs nested. Every relation it touches
    (``registered_app``, ``brief``, ``agent_skill_refs__skill__tool_defs``,
    ``containers``) is select_/prefetch_related by the resolver so this
    builder issues no further queries regardless of skill/tool count.
    """
    app = w.registered_app
    container = _primary_container(w)
    skills: list[AgentSkillType] = []
    # ``agent_skill_refs`` is prefetched; sort in Python (the prefetch
    # can't carry an ORDER BY through the reverse accessor reliably for
    # all Django versions) by (position, pk) so equal positions fall back
    # to insertion order, matching AgentSkillRef's documented contract.
    for ref in sorted(w.agent_skill_refs.all(), key=lambda r: (r.position, r.pk)):
        skill = ref.skill
        tool_defs = [
            tool_def_to_type(t)
            for t in sorted(skill.tool_defs.all(), key=lambda t: t.slug)
            if t.deleted_at is None
        ]
        skills.append(
            AgentSkillType(
                position=ref.position,
                skill=skill_to_type(skill),
                tool_defs=tool_defs,
            )
        )
    return AgentDetailType(
        id=GUID(str(w.guid)),
        name=w.name,
        slug=w.slug,
        app_slug=app.slug,
        source_repo=app.source_repo or "",
        run_family=w.run_family,
        run_mode=w.run_mode,
        run_paused=w.run_paused,
        run_cron_expression=w.run_cron_expression or "",
        image_ref=(container.image_ref or "") if container is not None else "",
        dockerfile_path=(container.dockerfile_path or "") if container is not None else "",
        brief=brief_to_type(w.brief) if w.brief_id else None,
        skills=skills,
        replicas=int(getattr(w, "replicas", 1) or 1),
        scheduled_scale_to=getattr(w, "scheduled_scale_to", None),
        scale_up_cron=getattr(w, "scale_up_cron", "") or "",
        scale_down_cron=getattr(w, "scale_down_cron", "") or "",
    )


def org_skill_repo_to_type(r) -> OrgSkillRepoType:
    return OrgSkillRepoType(
        id=GUID(str(r.guid)),
        alias=r.alias,
        repo_full_name=r.repo_full_name,
        source_kind=r.source_kind,
        default_ref=r.default_ref or "main",
        display_name=r.display_name or "",
        is_active=r.is_active,
        is_private=r.is_private,
        source_connection_id=(GUID(str(r.source_connection.guid)) if r.source_connection_id else None),
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def agent_secret_status_to_type(row: dict) -> AgentSecretStatusType:
    return AgentSecretStatusType(
        env_var=row["env_var"],
        uri=row["uri"],
        exists=bool(row["exists"]),
        error=row.get("error"),
        provider=row.get("provider") or "unavailable",
        can_reveal=bool(row.get("can_reveal")),
        read_limitation=row.get("read_limitation"),
    )


def agent_secret_bundle_to_type(bundle, capabilities: dict) -> AgentSecretBundleType:
    return AgentSecretBundleType(
        id=GUID(str(bundle.guid)),
        slug=bundle.slug,
        name=bundle.name,
        backend_ref=bundle.backend_ref,
        key_names=list(bundle.last_known_keys or []),
        provider=capabilities.get("provider") or "unavailable",
        can_reveal=bool(capabilities.get("can_reveal")),
        read_limitation=capabilities.get("read_limitation"),
        created_at=bundle.created_at,
        updated_at=bundle.updated_at,
    )


def agent_secret_bundle_attachment_to_type(ref) -> AgentSecretBundleAttachmentType:
    return AgentSecretBundleAttachmentType(
        id=GUID(str(ref.guid)),
        bundle_id=GUID(str(ref.secret_bundle.guid)),
        bundle_slug=ref.secret_bundle.slug,
        bundle_name=ref.secret_bundle.name,
        environment=ref.environment or "default",
        prefix=ref.prefix or "",
        position=ref.position,
        key_names=list(ref.secret_bundle.last_known_keys or []),
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
        managed_model=s.managed_model,
        run_as_non_root=s.run_as_non_root,
        box_workspace=s.box_workspace,
        model_gateway=s.model_gateway,
        gpu=s.gpu,
        gpu_type=s.gpu_type,
        mig_profile=s.mig_profile,
        secret_refs=s.secret_refs or [],
        env_vars=s.env_vars or {},
        config_repo=s.config_repo or "",
        config_branch=s.config_branch or "main",
        config_manifest_path=s.config_manifest_path or "",
        team_id=GUID(str(s.team.guid)) if s.team_id is not None else None,
        project_id=GUID(str(s.project.guid)) if s.project_id is not None else None,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )
