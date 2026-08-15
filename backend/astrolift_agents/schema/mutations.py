"""Mutations for the Agent Dispatch Layer.

Skill + ToolDef CRUD, Brief assembly, and Task launch/cancel. Every
resolver carries ``@mutation_audit`` + ``@require_permission`` +
``@tenant_scoped`` per the platform convention and the tenancy
guardrail. ``@tenant_scoped`` only asserts a tenant context exists, so
each resolver applies its own org filter / org binding explicitly.

Skills/Briefs/ToolDefs reuse the app-tier grants because they are
agent-workload building blocks. Environment-spec CRUD and user-facing agent
dispatch use their dedicated grants so API-token scopes can authorize those
CLI operations without granting broad app mutation authority.
"""

from __future__ import annotations

import hashlib

import strawberry
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from strawberry.types import Info

from astrolift_agents.models import (
    AgentEnvironmentSpec,
    AgentSecretBindingOverride,
    AgentSecretBundleRef,
    AgentTask,
    Brief,
    BriefSkillRef,
    OrgSkillRepo,
    Skill,
    ToolDef,
)
from astrolift_agents.schema.types import (
    AgentEnvironmentSpecType,
    AgentRunFamily,
    AgentRunMode,
    AgentRunSpecType,
    AgentSecretBundleAttachmentType,
    AgentSecretBundleType,
    AgentSecretRevealType,
    AgentSecretStatusType,
    AgentTaskInputMessageType,
    AgentTaskType,
    OrgSkillRepoType,
    SkillType,
    ToolDefType,
    agent_env_spec_to_type,
    agent_run_spec_to_type,
    agent_secret_bundle_attachment_to_type,
    agent_secret_bundle_to_type,
    agent_task_input_message_to_type,
    agent_task_to_type,
    org_skill_repo_to_type,
    skill_to_type,
    tool_def_to_type,
)
from astrolift_agents.services.agent_package import (
    AgentPackageError,
    normalize_environment_values,
    normalize_secret_references,
)
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from astrolift_identity.step_up import requires_elevation
from astrolift_registry.cron import CronValidationError, validate_cron_expression
from astrolift_registry.models import Workload
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

JSON = strawberry.scalars.JSON


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@strawberry.input
class SkillInput:
    name: str
    slug: str
    description: str
    content: str
    # Nullable-with-None default rather than a non-null []/{} default:
    # Strawberry's SDL printer can't render an empty-collection literal
    # on a JSON arg. The resolver coerces None → [] (mirrors the sibling
    # JSON-input convention in astrolift_clusters).
    dependencies: JSON | None = None


@strawberry.input
class ToolDefInput:
    name: str
    slug: str
    description: str
    adapter: str
    input_schema: JSON
    output_schema: JSON
    handler_ref: str
    # See SkillInput.dependencies — None default, resolver coerces to {}.
    implementation_config: JSON | None = None


@strawberry.input
class CreateAgentEnvironmentSpecInput:
    name: str
    slug: str
    agent_type: str
    # When set, the explicit image ref wins over ``runtime``; leave blank
    # to resolve the image from the runtime catalog instead.
    image_tag: str = ""
    runtime: str = ""
    tool_preset: str = ""
    allow_install: bool = False
    vnc_enabled: bool = False
    # When on, task pods use the cluster's cloud-native model provider
    # (AWS→Bedrock, GCP→Vertex) via a minted workload identity instead of
    # an ANTHROPIC_API_KEY. See the dispatcher's managed-model wiring.
    managed_model: bool = False
    config_repo: str = ""
    config_branch: str = "main"
    config_manifest_path: str = ""
    # Secret URIs only — never values. See SkillInput.dependencies for why
    # these are None-defaulted JSON (the SDL printer can't render an empty
    # collection literal); the resolver coerces None → []/{}.
    secret_refs: JSON | None = None
    env_vars: JSON | None = None


@strawberry.input
class UpdateAgentEnvironmentSpecInput:
    # Every field optional: only supplied (non-None) fields are applied so
    # the mutation is a partial update. ``slug`` is immutable (it's the
    # lookup key) and intentionally absent.
    name: str | None = None
    agent_type: str | None = None
    image_tag: str | None = None
    runtime: str | None = None
    tool_preset: str | None = None
    allow_install: bool | None = None
    vnc_enabled: bool | None = None
    managed_model: bool | None = None
    config_repo: str | None = None
    config_branch: str | None = None
    config_manifest_path: str | None = None
    secret_refs: JSON | None = None
    env_vars: JSON | None = None


@strawberry.input
class RunAstroliftAgentInput:
    """Ad-hoc Once dispatch of a registered agent (spec 33, PR-1).

    ``agent_slug`` is the ``Workload(kind=agent)`` slug under one of the
    caller's apps; the workload is resolved org-scoped (a foreign-org slug
    is not resolvable). ``environment_spec_id`` optionally pins the
    container-environment recipe to launch into (else the workload's own
    image/runtime is used). ``trigger_payload`` is opaque per-dispatch
    context (e.g. an inline prompt or input map) folded into the task's
    brief context; ``None`` is the no-payload manual case. ``timeout_seconds``
    bounds the run (defaults to the AgentTask model default).
    """

    agent_slug: str
    environment_spec_id: GUID | None = None
    # See SkillInput.dependencies for why this is None-defaulted JSON (the
    # SDL printer can't render an empty-collection literal); the resolver
    # coerces None -> {}.
    trigger_payload: JSON | None = None
    timeout_seconds: int | None = None


@strawberry.input
class RegisterOrgSkillRepoInput:
    """Register a per-org skill repo under an alias (spec 39d).

    ``alias`` is the short handle a manifest references as
    ``"<alias>/<skill-path>@<ref>"`` (unique per org). ``repo_full_name`` is
    ``owner/repo`` on the source host. ``source_connection_id`` links a
    :class:`SourceConnection` for a PRIVATE repo; omit it (null) for a PUBLIC
    repo fetched anonymously. ``default_ref`` is the branch/tag/sha fetched
    when a manifest ref carries no ``@`` pin.
    """

    alias: str
    repo_full_name: str
    source_kind: str = "github"
    default_ref: str = "main"
    display_name: str | None = None
    source_connection_id: GUID | None = None


@strawberry.input
class UpdateOrgSkillRepoInput:
    """Partial update of a registered org skill repo. Only supplied
    (non-``None``) fields are applied; ``alias`` is the immutable lookup key
    and is intentionally absent.

    Connection link control: pass ``source_connection_id`` (an org-owned
    connection GUID) to attach/re-point it for a PRIVATE repo, or set
    ``detach_source_connection = true`` to drop the link (the repo becomes
    PUBLIC). Setting both is rejected. Omitting both leaves the link
    unchanged."""

    id: GUID
    repo_full_name: str | None = None
    source_kind: str | None = None
    default_ref: str | None = None
    display_name: str | None = None
    is_active: bool | None = None
    source_connection_id: GUID | None = None
    detach_source_connection: bool = False


@strawberry.input
class RemoveOrgSkillRepoInput:
    id: GUID


@strawberry.input
class AgentRunSpecInput:
    """Partial write of an agent ``Workload(kind=agent)`` run-spec (spec 33).

    Serves BOTH run-spec editors in one mutation:

    - Task family — ``run_mode`` (once/loop/schedule/trigger),
      ``run_cron_expression`` (schedule), ``run_max_parallel`` (loop cap),
      ``run_paused``.
    - Service family — ``replicas`` (baseline desired count),
      ``scheduled_scale_to`` (scale-up target), ``scale_up_cron`` /
      ``scale_down_cron``.

    Every field is optional: only fields the caller actually supplies
    (non-``None``) are written; an omitted / ``None`` field leaves the
    stored value unchanged (partial update, mirrors
    ``UpdateAgentEnvironmentSpecInput``). ``run_family`` / ``run_mode``
    use the typed enums so the editor can't submit an unknown value;
    ``run_max_parallel`` / ``replicas`` / ``scheduled_scale_to`` are
    null-defaulted ints (``None`` = leave unchanged — distinct from an
    explicit value, including 0 for ``run_max_parallel`` which is a Loop
    soft-pause).
    """

    run_family: AgentRunFamily | None = None
    run_mode: AgentRunMode | None = None
    run_cron_expression: str | None = None
    run_paused: bool | None = None
    run_max_parallel: int | None = None
    # Service baseline replica count the Deployment renderer reads
    # (``spawners.k8s_job._render_agent`` → ``spec.replicas``). Service-only,
    # >= 1; ``None`` leaves it unchanged. See ``update_agent_run_spec`` for the
    # family gate.
    replicas: int | None = None
    scheduled_scale_to: int | None = None
    scale_up_cron: str | None = None
    scale_down_cron: str | None = None
    # Turn scheduled scaling back OFF (#952). A normal update can't express
    # "remove the schedule": ``scheduled_scale_to`` is a null-defaulted int
    # where ``None`` means "leave unchanged", so there's no value that resets
    # it to NULL. This flag clears the whole scheduled-scaling triple in one
    # call — ``scale_up_cron`` / ``scale_down_cron`` to "" and
    # ``scheduled_scale_to`` to NULL — so unchecking "Scheduled scaling" and
    # saving actually persists the off state. ``replicas`` (the Service
    # baseline) is deliberately untouched. Combining it with an explicit
    # scaling value in the same call is rejected (contradictory intent).
    clear_scheduled_scaling: bool = False


# ---------------------------------------------------------------------------
# Result payloads
# ---------------------------------------------------------------------------


@strawberry.type(name="AstroliftAssembleBriefResult")
class AssembleBriefResult:
    ok: bool
    brief_id: GUID | None = None


@strawberry.type(name="AstroliftLaunchTaskResult")
class LaunchTaskResult:
    ok: bool
    task_id: GUID | None = None


@strawberry.type(name="AstroliftAgentTriggerResult")
class AgentTriggerResult:
    """Result of creating an agent trigger webhook (#983). ``signing_secret``
    is the plaintext secret — shown ONCE here, stored only as a hash."""

    ok: bool
    message: str = ""
    slug: str | None = None
    endpoint: str | None = None
    signing_secret: str | None = None


@strawberry.type(name="AstroliftAgentScaleResult")
class AgentScaleResult:
    """Result of an on-demand Service-agent scale (#1012). ``desired`` echoes
    the (clamped) target; ``ready`` is the driver's read-back when surfaced."""

    ok: bool
    message: str = ""
    desired_replicas: int | None = None
    ready_replicas: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _resolve_org(org_id: strawberry.ID) -> tuple[Organization | None, object | None]:
    """Resolve ``org_id`` to an Organization, asserting it matches the
    caller's active tenant (superusers bypass). Returns
    ``(org, None)`` on success or ``(None, failure_envelope)``."""
    tenant = get_current_tenant()
    active = tenant.organization_id if tenant else None
    if active is None:
        return None, gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
    org = Organization.objects.filter(guid=str(org_id), deleted_at__isnull=True).first()
    if org is None:
        return None, gql_failure(ErrorCode.NOT_FOUND.value, "organization not found", field="orgId")
    if org.pk != active:
        # The decorator established a tenant context; reject an org_id
        # that points elsewhere so a caller can't write into a foreign
        # org by passing its GUID.
        return None, gql_failure(ErrorCode.PERMISSION_DENIED.value, "organization mismatch", field="orgId")
    return org, None


# Valid OrgSkillRepo source hosts — the host-prefix half of every
# ``SourceConnection.Kind`` ("github_pat" → "github", etc.). Derived so the
# set stays in lockstep with the connection kinds the platform supports.
def _skill_repo_source_kinds() -> frozenset[str]:
    from astrolift_scm.models import SourceConnection

    return frozenset(k.value.split("_", 1)[0] for k in SourceConnection.Kind)


_SKILL_REPO_SOURCE_KINDS = _skill_repo_source_kinds()


def _resolve_org_skill_repo_connection(org, connection_id: strawberry.ID):
    """Resolve a ``SourceConnection`` for an OrgSkillRepo link, org-scoped.

    Returns ``(connection, None)`` on success or ``(None, failure_envelope)``
    when the id doesn't resolve to a live connection in ``org`` — so a caller
    can't attach another tenant's credential to its skill repo (the foreign id
    is NOT_FOUND for the same non-leak reason as the sibling resolvers)."""
    from astrolift_scm.models import SourceConnection

    conn = SourceConnection.objects.filter(
        guid=str(connection_id), organization=org, deleted_at__isnull=True
    ).first()
    if conn is None:
        return None, gql_failure(
            ErrorCode.NOT_FOUND.value,
            "source connection not found",
            field="sourceConnectionId",
        )
    return conn, None


def _dispatch_actor(info: Info):
    """Build a workflow ``Actor`` for the dispatching caller.

    Mirrors ``astrolift_lifecycle.schema.mutations._actor_from_request``:
    prefer the authenticated request user, fall back to the tenant
    context's actor, then to a ``system`` actor. Imported lazily so the
    schema module doesn't pull the workflow package at import time.
    """
    from astrolift_workflows.inputs import Actor

    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) if request else None
    if user is not None and getattr(user, "is_authenticated", False):
        return Actor(kind="user", user_id=user.pk, display=getattr(user, "username", "") or "")
    tenant = get_current_tenant()
    if tenant and tenant.actor_user_id:
        return Actor(kind="user", user_id=tenant.actor_user_id, display="")
    return Actor(kind="system", display="system")


def _input_author(info: Info) -> tuple[object | None, str]:
    """Resolve ``(user, display_label)`` for a queued steering message.

    Module-level on purpose: this is called from a ROOT mutation resolver,
    where Strawberry binds ``self`` to the schema root value (``None``), so
    a ``self._helper()`` would pass a direct-invocation test and then raise
    ``AttributeError`` on every real request.

    Prefers the authenticated request user, falls back to the tenant
    context's actor id (API-token callers carry an actor without a request
    user), then to no author at all — an automation caller still gets to
    queue a message, it is just labelled ``system``.
    """
    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) if request else None
    if user is not None and getattr(user, "is_authenticated", False):
        label = getattr(user, "username", "") or getattr(user, "email", "") or ""
        return user, label
    tenant = get_current_tenant()
    actor_user_id = tenant.actor_user_id if tenant else None
    if actor_user_id:
        from django.contrib.auth import get_user_model

        actor = get_user_model().objects.filter(pk=actor_user_id).first()
        if actor is not None:
            return actor, (getattr(actor, "username", "") or getattr(actor, "email", "") or "")
    return None, "system"


# ---------------------------------------------------------------------------
# Root mutation
# ---------------------------------------------------------------------------


@strawberry.type(name="AstroliftImportSkillsResult")
class ImportSkillsResult:
    imported_skills: list[str]
    imported_tools: list[str]
    source_ref: str


# ---------------------------------------------------------------------------
# Agent secret VALUE management (#1173)
# ---------------------------------------------------------------------------


def _agent_secret_target(*args, **kwargs):
    """``@mutation_audit`` target hook for agent secret writes.

    Records ``('AgentSecret', '<spec-slug>:<env-var>')`` so the audit row
    identifies which ref was touched — never the value. Returns ``None`` to
    skip targeting when invoked without the expected args (test scaffolding).
    """
    env_spec_slug = kwargs.get("env_spec_slug")
    env_var = kwargs.get("env_var")
    if env_spec_slug is None and len(args) >= 3:
        env_spec_slug = args[2]
    if env_var is None and len(args) >= 4:
        env_var = args[3]
    if not env_spec_slug or not env_var:
        return None
    return "AgentSecret", f"{env_spec_slug}:{env_var}"


def _load_spec_and_ref(env_spec_slug: str, env_var: str):
    """Resolve ``(spec, ref, None)`` for a tenant-scoped spec + the
    ``secret_refs`` entry bound to ``env_var``, or ``(None, None, failure)``.

    Org-scoped to the caller's active tenant (a spec in another org is
    NOT_FOUND, no leak). ``ref`` is the normalized ``{"uri", "env_var"}``.
    """
    from astrolift_dispatch.agent_secrets import effective_secret_refs

    tenant = get_current_tenant()
    org_pk = tenant.organization_id if tenant else None
    spec = (
        AgentEnvironmentSpec.objects.select_related("organization")
        .filter(slug=env_spec_slug, organization_id=org_pk, deleted_at__isnull=True)
        .first()
    )
    if spec is None:
        return None, None, gql_failure(ErrorCode.NOT_FOUND.value, "environment spec not found")
    ref = next(
        (r for r in effective_secret_refs(spec) if r["env_var"] == env_var),
        None,
    )
    if ref is None:
        return (
            None,
            None,
            gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"no secret ref bound to env var {env_var!r} on spec {env_spec_slug!r}",
                field="envVar",
            ),
        )
    return spec, ref, None


def _load_agent_spec(env_spec_slug: str):
    tenant = get_current_tenant()
    org_pk = tenant.organization_id if tenant else None
    spec = (
        AgentEnvironmentSpec.objects.select_related("organization")
        .filter(slug=env_spec_slug, organization_id=org_pk, deleted_at__isnull=True)
        .first()
    )
    if spec is None:
        return None, gql_failure(ErrorCode.NOT_FOUND.value, "environment spec not found")
    return spec, None


def _load_agent_bundle(spec, bundle_id, *, allow_project: bool = False):
    from astrolift_agents.services.project_membership import agent_spec_belongs_to_project
    from astrolift_services.models import SecretBundle

    bundle = (
        SecretBundle.objects.select_related("project")
        .filter(
            guid=str(bundle_id),
            organization_id=spec.organization_id,
            team__isnull=True,
            deleted_at__isnull=True,
        )
        .first()
    )
    if bundle is None:
        return None, gql_failure(ErrorCode.NOT_FOUND.value, "secret bundle not found")
    if bundle.project_id is not None:
        if not allow_project or not agent_spec_belongs_to_project(spec, bundle.project):
            return None, gql_failure(ErrorCode.NOT_FOUND.value, "secret bundle not found")
        from astrolift_agents.services.agent_cluster import (
            NoAgentClusterError,
            resolve_agent_cluster,
        )

        try:
            runtime_cluster = resolve_agent_cluster(spec.organization)
        except NoAgentClusterError:
            runtime_cluster = None
        if runtime_cluster is None or runtime_cluster.pk != bundle.tenant_cluster_id:
            return None, gql_failure(
                ErrorCode.PRECONDITION.value,
                "project bundle secrets cluster does not match the agent runtime cluster",
            )
    return bundle, None


def _agent_secrets_backend(spec):
    """Resolve ``(backend, None)`` for the spec's org secret store, or
    ``(None, failure)``.

    Uses the SAME cluster resolution the dispatcher spawns onto
    (:func:`resolve_agent_cluster`) so a value written here lands in the
    exact store the pod reads at launch.
    """
    from astrolift_agents.services.agent_cluster import (
        NoAgentClusterError,
        resolve_agent_cluster,
    )
    from astrolift_dispatch.agent_secrets import resolve_secrets_backend

    try:
        cluster = resolve_agent_cluster(spec.organization)
    except NoAgentClusterError as exc:
        return None, gql_failure(ErrorCode.PRECONDITION.value, str(exc))
    try:
        return resolve_secrets_backend(cluster), None
    except Exception:  # noqa: BLE001 — never reflect provider response bodies
        return None, gql_failure(
            ErrorCode.PRECONDITION.value,
            "no secret store is available for this org; inspect the provider audit log",
        )


@strawberry.type
class AgentsMutation:
    @strawberry.field
    @mutation_audit(action="agents.skill.create")
    @require_permission(Permission.APP_CREATE)
    @tenant_scoped()
    def create_skill(
        self, info: Info, input: SkillInput, org_id: strawberry.ID
    ) -> MutationResultType[SkillType]:
        org, err = _resolve_org(org_id)
        if err is not None:
            return err
        if not input.name.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")
        if not input.slug.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")

        with transaction.atomic():
            skill = Skill.objects.create(
                organization=org,
                name=input.name.strip()[:255],
                slug=input.slug.strip()[:128],
                description=input.description or "",
                content=input.content or "",
                dependencies=list(input.dependencies or []),
                content_hash=_content_hash(input.content or ""),
                skill_version=1,
            )
        return gql_success(skill_to_type(skill))

    @strawberry.field
    @mutation_audit(action="agents.skill.update")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_skill(self, info: Info, id: strawberry.ID, input: SkillInput) -> MutationResultType[SkillType]:
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        skill = Skill.objects.filter(guid=str(id), organization_id=org_pk, deleted_at__isnull=True).first()
        if skill is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "skill not found")
        if not input.name.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")
        if not input.slug.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")

        skill.name = input.name.strip()[:255]
        skill.slug = input.slug.strip()[:128]
        skill.description = input.description or ""
        skill.content = input.content or ""
        skill.dependencies = list(input.dependencies or [])
        skill.content_hash = _content_hash(input.content or "")
        skill.skill_version = skill.skill_version + 1
        skill.save()
        return gql_success(skill_to_type(skill))

    @strawberry.field
    @mutation_audit(action="agents.skill.delete")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def delete_skill(self, info: Info, id: strawberry.ID) -> MutationResultType[SkillType]:
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        skill = Skill.objects.filter(guid=str(id), organization_id=org_pk, deleted_at__isnull=True).first()
        if skill is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "skill not found")
        skill.soft_delete()
        return gql_success(skill_to_type(skill))

    @strawberry.field
    @mutation_audit(action="agents.tool_def.create")
    @require_permission(Permission.APP_CREATE)
    @tenant_scoped()
    def create_tool_def(
        self, info: Info, skill_id: strawberry.ID, input: ToolDefInput
    ) -> MutationResultType[ToolDefType]:
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        skill = Skill.objects.filter(
            guid=str(skill_id), organization_id=org_pk, deleted_at__isnull=True
        ).first()
        if skill is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "skill not found", field="skillId")
        if not input.name.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")
        if not input.slug.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")
        if input.adapter not in ToolDef.Adapter.values:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown adapter {input.adapter!r}",
                field="adapter",
            )

        with transaction.atomic():
            tool = ToolDef.objects.create(
                skill=skill,
                name=input.name.strip()[:255],
                slug=input.slug.strip()[:128],
                description=input.description or "",
                input_schema=input.input_schema or {},
                output_schema=input.output_schema or {},
                adapter=input.adapter,
                handler_ref=input.handler_ref or "",
                implementation_config=input.implementation_config or {},
            )
        return gql_success(tool_def_to_type(tool))

    @strawberry.field
    @mutation_audit(action="agents.tool_def.update")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_tool_def(
        self, info: Info, id: strawberry.ID, input: ToolDefInput
    ) -> MutationResultType[ToolDefType]:
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        tool = ToolDef.objects.filter(
            guid=str(id),
            skill__organization_id=org_pk,
            deleted_at__isnull=True,
        ).first()
        if tool is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "tool def not found")
        if not input.name.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")
        if not input.slug.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")
        if input.adapter not in ToolDef.Adapter.values:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown adapter {input.adapter!r}",
                field="adapter",
            )

        tool.name = input.name.strip()[:255]
        tool.slug = input.slug.strip()[:128]
        tool.description = input.description or ""
        tool.input_schema = input.input_schema or {}
        tool.output_schema = input.output_schema or {}
        tool.adapter = input.adapter
        tool.handler_ref = input.handler_ref or ""
        tool.implementation_config = input.implementation_config or {}
        tool.save()
        return gql_success(tool_def_to_type(tool))

    @strawberry.field
    @mutation_audit(action="agents.tool_def.delete")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def delete_tool_def(self, info: Info, id: strawberry.ID) -> MutationResultType[ToolDefType]:
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        tool = ToolDef.objects.filter(
            guid=str(id),
            skill__organization_id=org_pk,
            deleted_at__isnull=True,
        ).first()
        if tool is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "tool def not found")
        tool.soft_delete()
        return gql_success(tool_def_to_type(tool))

    # ---- AgentEnvironmentSpec CRUD --------------------------------

    @strawberry.field
    @mutation_audit(action="agents.env_spec.create")
    @require_permission(Permission.AGENT_ENV_SPEC_CREATE)
    @tenant_scoped()
    def create_agent_environment_spec(
        self, info: Info, input: CreateAgentEnvironmentSpecInput, org_id: strawberry.ID
    ) -> MutationResultType[AgentEnvironmentSpecType]:
        org, err = _resolve_org(org_id)
        if err is not None:
            return err
        if not input.name.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")
        if not input.slug.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")
        if input.agent_type not in AgentEnvironmentSpec.AgentType.values:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown agent type {input.agent_type!r}",
                field="agentType",
            )
        try:
            secret_refs = normalize_secret_references(input.secret_refs, field="secretRefs")
            env_vars = normalize_environment_values(input.env_vars, field="envVars")
        except AgentPackageError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc))

        slug = input.slug.strip()[:128]
        if AgentEnvironmentSpec.objects.filter(organization=org, slug=slug, deleted_at__isnull=True).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"an environment spec with slug {slug!r} already exists",
                field="slug",
            )

        with transaction.atomic():
            spec = AgentEnvironmentSpec.objects.create(
                organization=org,
                name=input.name.strip()[:255],
                slug=slug,
                agent_type=input.agent_type,
                image_tag=(input.image_tag or "").strip()[:512],
                runtime=(input.runtime or "").strip()[:64],
                tool_preset=(input.tool_preset or "").strip()[:128],
                allow_install=bool(input.allow_install),
                vnc_enabled=bool(input.vnc_enabled),
                managed_model=bool(input.managed_model),
                config_repo=(input.config_repo or "").strip()[:512],
                config_branch=(input.config_branch or "main").strip()[:128],
                config_manifest_path=(input.config_manifest_path or "").strip()[:512],
                secret_refs=secret_refs,
                env_vars=env_vars,
            )
        return gql_success(agent_env_spec_to_type(spec))

    @strawberry.field
    @mutation_audit(action="agents.env_spec.update")
    @require_permission(Permission.AGENT_ENV_SPEC_UPDATE)
    @tenant_scoped()
    def update_agent_environment_spec(
        self, info: Info, slug: str, input: UpdateAgentEnvironmentSpecInput
    ) -> MutationResultType[AgentEnvironmentSpecType]:
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        spec = AgentEnvironmentSpec.objects.filter(
            slug=slug, organization_id=org_pk, deleted_at__isnull=True
        ).first()
        if spec is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment spec not found")

        if input.agent_type is not None:
            if input.agent_type not in AgentEnvironmentSpec.AgentType.values:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"unknown agent type {input.agent_type!r}",
                    field="agentType",
                )
            spec.agent_type = input.agent_type
        try:
            secret_refs = (
                normalize_secret_references(input.secret_refs, field="secretRefs")
                if input.secret_refs is not None
                else None
            )
            env_vars = (
                normalize_environment_values(input.env_vars, field="envVars")
                if input.env_vars is not None
                else None
            )
        except AgentPackageError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc))
        if input.name is not None:
            if not input.name.strip():
                return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")
            spec.name = input.name.strip()[:255]
        if input.image_tag is not None:
            spec.image_tag = input.image_tag.strip()[:512]
        if input.runtime is not None:
            spec.runtime = input.runtime.strip()[:64]
        if input.tool_preset is not None:
            spec.tool_preset = input.tool_preset.strip()[:128]
        if input.allow_install is not None:
            spec.allow_install = bool(input.allow_install)
        if input.vnc_enabled is not None:
            spec.vnc_enabled = bool(input.vnc_enabled)
        if input.managed_model is not None:
            spec.managed_model = bool(input.managed_model)
        if input.config_repo is not None:
            spec.config_repo = input.config_repo.strip()[:512]
        if input.config_branch is not None:
            spec.config_branch = input.config_branch.strip()[:128]
        if input.config_manifest_path is not None:
            spec.config_manifest_path = input.config_manifest_path.strip()[:512]
        if secret_refs is not None:
            spec.secret_refs = secret_refs
        if env_vars is not None:
            spec.env_vars = env_vars
        spec.save()
        return gql_success(agent_env_spec_to_type(spec))

    @strawberry.field
    @mutation_audit(action="agents.env_spec.delete")
    @require_permission(Permission.AGENT_ENV_SPEC_DELETE)
    @tenant_scoped()
    def delete_agent_environment_spec(
        self, info: Info, slug: str
    ) -> MutationResultType[AgentEnvironmentSpecType]:
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        spec = AgentEnvironmentSpec.objects.filter(
            slug=slug, organization_id=org_pk, deleted_at__isnull=True
        ).first()
        if spec is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment spec not found")
        spec.soft_delete()
        return gql_success(agent_env_spec_to_type(spec))

    # ---- Agent secret VALUE management (#1173) --------------------
    #
    # The env-spec row carries secret *references* only; these mutations
    # write/rotate/delete the referenced VALUES through to the install's
    # secret store via the per-cluster SecretsBackend driver. The value is
    # never persisted in the control plane, never logged, and never placed
    # in an audit row.

    @strawberry.field
    @mutation_audit(action="agents.secret.set", target=_agent_secret_target)
    @requires_elevation(action_label="agents.secret.set")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def set_agent_secret_value(
        self, info: Info, env_spec_slug: str, env_var: str, value: str
    ) -> MutationResultType[AgentSecretStatusType]:
        """Write-through (create or rotate) the VALUE for one of a spec's
        ``secret_refs``, keyed by its ``env_var``.

        ``upsert`` covers create + rotate identically. Errors surface in the
        MutationResult envelope; the resolver never raises and never echoes
        the value.
        """
        from astrolift_dispatch.agent_secrets import write_secret_value

        spec, ref, err = _load_spec_and_ref(env_spec_slug, env_var)
        if err is not None:
            return err
        if value == "":
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "value must not be empty; delete the secret instead",
                field="value",
            )
        backend, berr = _agent_secrets_backend(spec)
        if berr is not None:
            return berr
        try:
            write_secret_value(backend, ref["uri"], value)
        except Exception:  # noqa: BLE001 — never echo a provider payload containing caller input
            return gql_failure(
                ErrorCode.INTERNAL.value,
                "secret store write failed; inspect the provider audit log",
            )
        return gql_success(AgentSecretStatusType(env_var=env_var, uri=ref["uri"], exists=True, error=None))

    @strawberry.field
    @mutation_audit(action="agents.secret.delete", target=_agent_secret_target)
    @requires_elevation(action_label="agents.secret.delete")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def delete_agent_secret_value(
        self, info: Info, env_spec_slug: str, env_var: str
    ) -> MutationResultType[AgentSecretStatusType]:
        """Delete the stored VALUE for one of a spec's ``secret_refs``.

        Idempotent from the operator's view: a ref whose value is already
        absent returns success (desired end-state holds). The env-spec's
        reference itself is untouched — only the store value is removed.
        """
        from astrolift_dispatch.agent_secrets import delete_secret_value

        spec, ref, err = _load_spec_and_ref(env_spec_slug, env_var)
        if err is not None:
            return err
        backend, berr = _agent_secrets_backend(spec)
        if berr is not None:
            return berr
        absent = AgentSecretStatusType(env_var=env_var, uri=ref["uri"], exists=False, error=None)
        try:
            # Probe-then-delete keeps an already-absent ref idempotent while
            # still deleting a present-but-empty provider shell.
            if backend.get(ref["uri"]) is None:
                return gql_success(absent)
            delete_secret_value(backend, ref["uri"])
        except Exception:  # noqa: BLE001 — provider errors can include sensitive response data
            return gql_failure(
                ErrorCode.INTERNAL.value,
                "secret store delete failed; inspect the provider audit log",
            )
        return gql_success(absent)

    @strawberry.field
    @mutation_audit(action="agents.secret.binding.upsert", target=_agent_secret_target)
    @requires_elevation(action_label="agents.secret.binding.upsert")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def upsert_agent_secret_ref(
        self, info: Info, env_spec_slug: str, env_var: str, uri: str
    ) -> MutationResultType[AgentSecretStatusType]:
        """Create/update a durable operator binding over manifest refs."""
        from astrolift_dispatch.agent_secrets import valid_agent_env_var

        spec, err = _load_agent_spec(env_spec_slug)
        if err is not None:
            return err
        env_var = (env_var or "").strip()
        uri = (uri or "").strip()
        if not valid_agent_env_var(env_var):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "envVar must be valid and must not be dispatcher-owned",
                field="envVar",
            )
        if len(env_var) > 255:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "envVar must be at most 255 characters",
                field="envVar",
            )
        if not uri:
            return gql_failure(ErrorCode.VALIDATION.value, "uri is required", field="uri")
        if len(uri) > 512:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "uri must be at most 512 characters",
                field="uri",
            )
        with transaction.atomic():
            override = (
                AgentSecretBindingOverride.objects.select_for_update()
                .filter(environment_spec=spec, env_var=env_var, deleted_at__isnull=True)
                .first()
            )
            if override is None:
                override = AgentSecretBindingOverride(
                    environment_spec=spec,
                    env_var=env_var,
                )
            override.uri = uri
            override.removed = False
            override.save()
        backend, berr = _agent_secrets_backend(spec)
        exists = False
        error = None
        if berr is None:
            from astrolift_dispatch.agent_secrets import read_secret_value

            try:
                exists = bool(read_secret_value(backend, override.uri))
            except Exception:  # noqa: BLE001 — never reflect provider response bodies
                error = "secret presence check failed; inspect the provider audit log"
        return gql_success(
            AgentSecretStatusType(env_var=env_var, uri=override.uri, exists=exists, error=error)
        )

    @strawberry.field
    @mutation_audit(action="agents.secret.binding.remove", target=_agent_secret_target)
    @requires_elevation(action_label="agents.secret.binding.remove")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def remove_agent_secret_ref(
        self, info: Info, env_spec_slug: str, env_var: str
    ) -> MutationResultType[AgentSecretStatusType]:
        """Remove a binding without deleting its provider-side value."""
        spec, ref, err = _load_spec_and_ref(env_spec_slug, env_var)
        if err is not None:
            return err
        with transaction.atomic():
            override = (
                AgentSecretBindingOverride.objects.select_for_update()
                .filter(environment_spec=spec, env_var=env_var, deleted_at__isnull=True)
                .first()
            )
            if override is None:
                override = AgentSecretBindingOverride(environment_spec=spec, env_var=env_var)
            override.uri = ref["uri"]
            override.removed = True
            override.save()
        return gql_success(AgentSecretStatusType(env_var=env_var, uri=ref["uri"], exists=False, error=None))

    @strawberry.field
    @mutation_audit(action="agents.secret.reveal", target=_agent_secret_target)
    @requires_elevation(action_label="agents.secret.reveal")
    @require_permission(Permission.SECRET_READ)
    @tenant_scoped()
    def reveal_agent_secret_value(
        self, info: Info, env_spec_slug: str, env_var: str
    ) -> MutationResultType[AgentSecretRevealType]:
        """Read one value when the backing provider supports disclosure."""
        from astrolift_dispatch.agent_secrets import (
            read_secret_value,
            secret_backend_capabilities,
        )

        spec, ref, err = _load_spec_and_ref(env_spec_slug, env_var)
        if err is not None:
            return err
        backend, berr = _agent_secrets_backend(spec)
        if berr is not None:
            return berr
        capabilities = secret_backend_capabilities(backend)
        if not capabilities["can_reveal"]:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                capabilities["read_limitation"] or "provider does not support reveal",
            )
        try:
            value = read_secret_value(backend, ref["uri"])
        except Exception:  # noqa: BLE001 — never reflect provider response bodies
            return gql_failure(
                ErrorCode.INTERNAL.value,
                "secret store read failed; inspect the provider audit log",
            )
        if value is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "secret value is not set")
        return gql_success(
            AgentSecretRevealType(
                env_var=env_var,
                uri=ref["uri"],
                value=value,
                provider=capabilities["provider"],
                revealed_at=timezone.now(),
            )
        )

    @strawberry.field
    @mutation_audit(action="agents.secret.bundle.create")
    @requires_elevation(action_label="agents.secret.bundle.create")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def create_agent_secret_bundle(
        self,
        info: Info,
        env_spec_slug: str,
        name: str,
        slug: str,
        backend_ref: str = "",
    ) -> MutationResultType[AgentSecretBundleType]:
        from astrolift_dispatch.agent_secrets import secret_backend_capabilities
        from astrolift_services.models import SecretBundle

        spec, err = _load_agent_spec(env_spec_slug)
        if err is not None:
            return err
        name = (name or "").strip()
        normalized_slug = slugify(slug or name)[:200]
        if not name or not normalized_slug:
            return gql_failure(ErrorCode.VALIDATION.value, "name and slug are required")
        path = (backend_ref or "").strip() or (f"agent-bundles/{spec.organization.guid}/{normalized_slug}")
        if len(path) > 512:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "backendRef must be at most 512 characters",
                field="backendRef",
            )
        try:
            with transaction.atomic():
                bundle = SecretBundle.objects.create(
                    organization=spec.organization,
                    team=None,
                    name=name[:200],
                    slug=normalized_slug,
                    backend_ref=path,
                )
        except IntegrityError:
            return gql_failure(ErrorCode.CONFLICT.value, "a bundle with this slug already exists")
        backend, berr = _agent_secrets_backend(spec)
        capabilities = secret_backend_capabilities(backend if berr is None else None)
        return gql_success(agent_secret_bundle_to_type(bundle, capabilities))

    @strawberry.field
    @mutation_audit(action="agents.secret.bundle.update")
    @requires_elevation(action_label="agents.secret.bundle.update")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def update_agent_secret_bundle(
        self,
        info: Info,
        env_spec_slug: str,
        bundle_id: strawberry.ID,
        name: str,
        backend_ref: str,
    ) -> MutationResultType[AgentSecretBundleType]:
        from astrolift_dispatch.agent_secrets import secret_backend_capabilities

        spec, err = _load_agent_spec(env_spec_slug)
        if err is not None:
            return err
        bundle, berr = _load_agent_bundle(spec, bundle_id)
        if berr is not None:
            return berr
        name = (name or "").strip()
        backend_ref = (backend_ref or "").strip()
        if not name or not backend_ref:
            return gql_failure(ErrorCode.VALIDATION.value, "name and backendRef are required")
        if len(name) > 200:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "name must be at most 200 characters",
                field="name",
            )
        if len(backend_ref) > 512:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "backendRef must be at most 512 characters",
                field="backendRef",
            )
        backend = None
        store_err = None
        if backend_ref != bundle.backend_ref:
            # ``last_known_keys`` is only a cache: out-of-band provider writes
            # may not have refreshed it yet. Read the authoritative store and
            # fail closed on an unreadable source before changing the pointer,
            # otherwise an edit can silently orphan a live secret bundle.
            backend, store_err = _agent_secrets_backend(spec)
            if store_err is not None:
                return store_err
            try:
                current_payload = backend.get(bundle.backend_ref) or {}
            except Exception:  # noqa: BLE001 — fail closed without reflecting provider payloads
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "current bundle values could not be checked before moving backendRef; "
                    "inspect the provider audit log",
                    field="backendRef",
                )
            if current_payload:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "backendRef cannot change while the bundle contains keys; "
                    "delete or migrate the provider values first",
                    field="backendRef",
                )
        bundle.name = name
        bundle.backend_ref = backend_ref
        bundle.save(update_fields=["name", "backend_ref", "updated_at", "version"])
        if backend is None:
            backend, store_err = _agent_secrets_backend(spec)
        capabilities = secret_backend_capabilities(backend if store_err is None else None)
        return gql_success(agent_secret_bundle_to_type(bundle, capabilities))

    @strawberry.field
    @mutation_audit(action="agents.secret.bundle.delete")
    @requires_elevation(action_label="agents.secret.bundle.delete")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def delete_agent_secret_bundle(
        self, info: Info, env_spec_slug: str, bundle_id: strawberry.ID
    ) -> MutationResultType[AgentSecretBundleType]:
        from astrolift_dispatch.agent_secrets import secret_backend_capabilities

        spec, err = _load_agent_spec(env_spec_slug)
        if err is not None:
            return err
        bundle, berr = _load_agent_bundle(spec, bundle_id)
        if berr is not None:
            return berr
        if (
            bundle.agent_refs.filter(deleted_at__isnull=True).exists()
            or bundle.app_refs.filter(deleted_at__isnull=True).exists()
        ):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "detach this bundle from every agent and app before deleting it",
            )
        backend, store_err = _agent_secrets_backend(spec)
        if store_err is not None:
            return store_err
        try:
            # Delete the provider-side bundle as well as its control-plane
            # metadata. ``None`` means it is already absent; an empty dict is
            # still a real provider shell and must be removed.
            if backend.get(bundle.backend_ref) is not None:
                backend.delete(bundle.backend_ref)
        except Exception:  # noqa: BLE001 — never expose a provider response body
            return gql_failure(
                ErrorCode.INTERNAL.value,
                "secret bundle delete failed; inspect the provider audit log",
            )
        payload = agent_secret_bundle_to_type(bundle, secret_backend_capabilities(backend))
        bundle.soft_delete()
        return gql_success(payload)

    @strawberry.field
    @mutation_audit(action="agents.secret.bundle.attach")
    @requires_elevation(action_label="agents.secret.bundle.attach")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def attach_agent_secret_bundle(
        self,
        info: Info,
        env_spec_slug: str,
        bundle_id: strawberry.ID,
        environment: str = "default",
        prefix: str = "",
        position: int = 0,
    ) -> MutationResultType[AgentSecretBundleAttachmentType]:
        from astrolift_dispatch.agent_secrets import valid_env_var

        spec, err = _load_agent_spec(env_spec_slug)
        if err is not None:
            return err
        bundle, berr = _load_agent_bundle(spec, bundle_id, allow_project=True)
        if berr is not None:
            return berr
        environment = (environment or "").strip() or "default"
        prefix = (prefix or "").strip()
        if len(environment) > 64:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "environment must be at most 64 characters",
                field="environment",
            )
        if len(prefix) > 64:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "prefix must be at most 64 characters",
                field="prefix",
            )
        if prefix and not valid_env_var(f"{prefix}X"):
            return gql_failure(ErrorCode.VALIDATION.value, "prefix is not env-var safe", field="prefix")
        ref = AgentSecretBundleRef.objects.filter(
            environment_spec=spec,
            secret_bundle=bundle,
            environment=environment,
            deleted_at__isnull=True,
        ).first()
        if ref is None:
            ref = AgentSecretBundleRef(
                environment_spec=spec,
                secret_bundle=bundle,
                environment=environment,
            )
        ref.prefix = prefix
        ref.position = max(0, position)
        ref.save()
        return gql_success(agent_secret_bundle_attachment_to_type(ref))

    @strawberry.field
    @mutation_audit(action="agents.secret.bundle.detach")
    @requires_elevation(action_label="agents.secret.bundle.detach")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def detach_agent_secret_bundle(
        self, info: Info, env_spec_slug: str, attachment_id: strawberry.ID
    ) -> MutationResultType[AgentSecretBundleAttachmentType]:
        spec, err = _load_agent_spec(env_spec_slug)
        if err is not None:
            return err
        ref = (
            AgentSecretBundleRef.objects.select_related("secret_bundle")
            .filter(
                guid=str(attachment_id),
                environment_spec=spec,
                environment_spec__organization_id=spec.organization_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if ref is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "bundle attachment not found")
        payload = agent_secret_bundle_attachment_to_type(ref)
        ref.soft_delete()
        return gql_success(payload)

    @strawberry.field
    @mutation_audit(action="agents.secret.bundle.key.set")
    @requires_elevation(action_label="agents.secret.bundle.key.set")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def set_agent_bundle_secret_value(
        self,
        info: Info,
        env_spec_slug: str,
        bundle_id: strawberry.ID,
        key: str,
        value: str,
    ) -> MutationResultType[AgentSecretBundleType]:
        from astrolift_dispatch.agent_secrets import secret_backend_capabilities, valid_agent_env_var
        from astrolift_services.models import SecretBundle

        spec, err = _load_agent_spec(env_spec_slug)
        if err is not None:
            return err
        bundle, berr = _load_agent_bundle(spec, bundle_id)
        if berr is not None:
            return berr
        key = (key or "").strip()
        if not valid_agent_env_var(key):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "key must be env-var safe and must not be dispatcher-owned",
                field="key",
            )
        if len(key) > 255:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "key must be at most 255 characters",
                field="key",
            )
        if value == "":
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "value must not be empty; delete the key instead",
                field="value",
            )
        backend, store_err = _agent_secrets_backend(spec)
        if store_err is not None:
            return store_err
        try:
            with transaction.atomic():
                locked = SecretBundle.objects.select_for_update().get(pk=bundle.pk)
                payload = dict(backend.get(locked.backend_ref) or {})
                payload[key] = value
                backend.upsert(locked.backend_ref, payload)
                locked.last_known_keys = sorted(payload)
                locked.last_key_enum_at = timezone.now()
                locked.save(update_fields=["last_known_keys", "last_key_enum_at", "updated_at", "version"])
                bundle = locked
        except Exception:  # noqa: BLE001 — write errors may embed the submitted payload
            return gql_failure(
                ErrorCode.INTERNAL.value,
                "secret bundle write failed; inspect the provider audit log",
            )
        return gql_success(agent_secret_bundle_to_type(bundle, secret_backend_capabilities(backend)))

    @strawberry.field
    @mutation_audit(action="agents.secret.bundle.key.delete")
    @requires_elevation(action_label="agents.secret.bundle.key.delete")
    @require_permission(Permission.SECRET_WRITE)
    @tenant_scoped()
    def delete_agent_bundle_secret_value(
        self,
        info: Info,
        env_spec_slug: str,
        bundle_id: strawberry.ID,
        key: str,
    ) -> MutationResultType[AgentSecretBundleType]:
        from astrolift_dispatch.agent_secrets import secret_backend_capabilities
        from astrolift_services.models import SecretBundle

        spec, err = _load_agent_spec(env_spec_slug)
        if err is not None:
            return err
        bundle, berr = _load_agent_bundle(spec, bundle_id)
        if berr is not None:
            return berr
        backend, store_err = _agent_secrets_backend(spec)
        if store_err is not None:
            return store_err
        try:
            with transaction.atomic():
                locked = SecretBundle.objects.select_for_update().get(pk=bundle.pk)
                payload = dict(backend.get(locked.backend_ref) or {})
                payload.pop(key, None)
                backend.upsert(locked.backend_ref, payload)
                locked.last_known_keys = sorted(payload)
                locked.last_key_enum_at = timezone.now()
                locked.save(update_fields=["last_known_keys", "last_key_enum_at", "updated_at", "version"])
                bundle = locked
        except Exception:  # noqa: BLE001 — never reflect provider response bodies
            return gql_failure(
                ErrorCode.INTERNAL.value,
                "secret bundle key delete failed; inspect the provider audit log",
            )
        return gql_success(agent_secret_bundle_to_type(bundle, secret_backend_capabilities(backend)))

    @strawberry.field
    @mutation_audit(action="agents.secret.bundle.key.reveal")
    @requires_elevation(action_label="agents.secret.bundle.key.reveal")
    @require_permission(Permission.SECRET_READ)
    @tenant_scoped()
    def reveal_agent_bundle_secret_value(
        self,
        info: Info,
        env_spec_slug: str,
        bundle_id: strawberry.ID,
        key: str,
    ) -> MutationResultType[AgentSecretRevealType]:
        from astrolift_dispatch.agent_secrets import secret_backend_capabilities

        spec, err = _load_agent_spec(env_spec_slug)
        if err is not None:
            return err
        bundle, berr = _load_agent_bundle(spec, bundle_id)
        if berr is not None:
            return berr
        backend, store_err = _agent_secrets_backend(spec)
        if store_err is not None:
            return store_err
        capabilities = secret_backend_capabilities(backend)
        if not capabilities["can_reveal"]:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                capabilities["read_limitation"] or "provider does not support reveal",
            )
        try:
            payload = backend.get(bundle.backend_ref) or {}
        except Exception:  # noqa: BLE001 — never reflect provider response bodies
            return gql_failure(
                ErrorCode.INTERNAL.value,
                "secret bundle read failed; inspect the provider audit log",
            )
        if key not in payload:
            return gql_failure(ErrorCode.NOT_FOUND.value, "secret bundle key not found")
        return gql_success(
            AgentSecretRevealType(
                env_var=key,
                uri=bundle.backend_ref,
                value=str(payload[key]),
                provider=capabilities["provider"],
                revealed_at=timezone.now(),
            )
        )

    @strawberry.field
    @mutation_audit(action="agents.brief.assemble")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def assemble_brief(
        self,
        info: Info,
        skill_ids: list[strawberry.ID],
        org_id: strawberry.ID,
        config: strawberry.scalars.JSON | None = None,
    ) -> MutationResultType[AssembleBriefResult]:
        """Assemble a Brief from the given Skills.

        The Brief is content-addressed: ``content_hash`` is the SHA-256
        of the canonical (skill guid, version) tuple list plus the
        config payload, so two identical assemblies collide on the
        unique hash. ``storage_key`` is left empty — the object-storage
        write is future work; the row is the assembly record.
        """
        org, err = _resolve_org(org_id)
        if err is not None:
            return err
        if not skill_ids:
            return gql_failure(ErrorCode.VALIDATION.value, "at least one skill is required", field="skillIds")

        from django.db.models import Q

        guids = [str(s) for s in skill_ids]
        skills = list(
            Skill.objects.filter(
                Q(organization_id=org.pk) | Q(is_global=True),
                guid__in=guids,
                deleted_at__isnull=True,
            )
        )
        found = {str(s.guid) for s in skills}
        missing = [g for g in guids if g not in found]
        if missing:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"skill(s) not found: {', '.join(missing)}",
                field="skillIds",
            )

        cfg = config or {}
        # Order by the caller's requested guid order so the hash is
        # stable and independent of DB row ordering.
        by_guid = {str(s.guid): s for s in skills}
        ordered = [by_guid[g] for g in guids]
        canonical = json_canonical(
            {
                "skills": [{"guid": str(s.guid), "version": s.skill_version} for s in ordered],
                "config": cfg,
            }
        )
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        existing = Brief.objects.filter(content_hash=content_hash, deleted_at__isnull=True).first()
        if existing is not None:
            # Identical payload already assembled — return it idempotently.
            return gql_success(AssembleBriefResult(ok=True, brief_id=GUID(str(existing.guid))))

        with transaction.atomic():
            brief = Brief.objects.create(
                organization=org,
                content_hash=content_hash,
                storage_key="",
                manifest_snapshot=cfg,
            )
            for s in ordered:
                BriefSkillRef.objects.create(brief=brief, skill=s, skill_version=s.skill_version)

        return gql_success(AssembleBriefResult(ok=True, brief_id=GUID(str(brief.guid))))

    @strawberry.field
    @mutation_audit(action="agents.task.launch")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def launch_task(
        self,
        info: Info,
        brief_id: strawberry.ID,
        org_id: strawberry.ID,
        callback_url: str | None = None,
    ) -> MutationResultType[LaunchTaskResult]:
        """Create an AgentTask for ``brief_id`` and enqueue it.

        The task is created in ``DRAFT`` then advanced to ``QUEUED`` via
        the model's sanctioned ``transition_to`` so ``queued_at`` is
        stamped and the state machine stays authoritative.
        """
        org, err = _resolve_org(org_id)
        if err is not None:
            return err
        brief = Brief.objects.filter(
            guid=str(brief_id), organization_id=org.pk, deleted_at__isnull=True
        ).first()
        if brief is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "brief not found", field="briefId")

        with transaction.atomic():
            task = AgentTask.objects.create(
                organization=org,
                brief=brief,
                status=AgentTask.Status.DRAFT,
                callback_url=(callback_url or "")[:200],
            )
            task.transition_to(AgentTask.Status.QUEUED)
        return gql_success(LaunchTaskResult(ok=True, task_id=GUID(str(task.guid))))

    @strawberry.field
    @mutation_audit(action="agents.task.cancel")
    @require_permission(Permission.AGENT_DISPATCH)
    @tenant_scoped()
    def cancel_task(self, info: Info, id: strawberry.ID) -> MutationResultType[None]:
        """Cancel an AgentTask.

        A task that has already spawned is stopped through its Dispatcher
        backend before it transitions to ``CANCELLED``. This makes the same
        mutation work as the operator's hard-stop control for a runaway pod.
        An illegal terminal-to-terminal transition surfaces as a PRECONDITION
        failure rather than a 500.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        task = AgentTask.objects.filter(guid=str(id), organization_id=org_pk, deleted_at__isnull=True).first()
        if task is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "task not found")
        if task.status in AgentTask.TERMINAL_STATUSES:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"task is already terminal ({task.status})",
            )

        from astrolift_workflows.activities.agent_stage import _cancel_agent_task_sync

        cancelled = _cancel_agent_task_sync(str(task.guid))
        task.refresh_from_db(fields=["status"])
        if not cancelled["ok"] or task.status != AgentTask.Status.CANCELLED:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"task could not be cancelled: {cancelled['error']} (current status: {task.status})",
            )
        return gql_success(None)

    @strawberry.field
    @mutation_audit(
        action="agents.task.send_input",
        target=lambda self, info, task_id, message: ("AgentTask", str(task_id)),
    )
    @require_permission(Permission.AGENT_TASK_SEND_INPUT)
    @tenant_scoped()
    def send_agent_task_input(
        self, info: Info, task_id: strawberry.ID, message: str
    ) -> MutationResultType[AgentTaskInputMessageType]:
        """Queue a follow-up prompt for a RUNNING AgentTask (#1390).

        The steering channel is queued, not duplex: the message is persisted
        against the task and the runner consumes it at its next turn
        boundary (the point one harness invocation finishes and the runner
        decides whether to run another). Nothing is pushed into the pod
        here, so this returns as soon as the message is durable — an
        ``ok: true`` means "queued", not "the agent has read it". Poll
        ``deliveredAt`` on the returned message for that.

        Deny-by-default behind ``agent_task.send_input``, the operator-grade
        write sibling of the passive ``agent_task.watch``. Tenancy is
        explicit: ``@tenant_scoped`` only asserts a tenant exists, so the
        task fetch carries its own ``organization_id`` filter and another
        org's task guid reads as NOT_FOUND — indistinguishable from a guid
        that does not exist, so the mutation leaks no existence.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        task = AgentTask.objects.filter(
            guid=str(task_id), organization_id=org_pk, deleted_at__isnull=True
        ).first()
        if task is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "task not found", field="taskId")

        from astrolift_agents.services.agent_task_input import (
            AgentTaskInputError,
            queue_agent_task_input,
        )

        author, author_label = _input_author(info)
        try:
            queued = queue_agent_task_input(
                task=task,
                message=message,
                author=author,
                author_label=author_label,
            )
        except AgentTaskInputError as exc:
            code = {
                "validation": ErrorCode.VALIDATION.value,
                "precondition": ErrorCode.PRECONDITION.value,
            }.get(exc.code, ErrorCode.INTERNAL.value)
            return gql_failure(code, exc.message, field=exc.field or None)

        return gql_success(agent_task_input_message_to_type(queued))

    @strawberry.field
    @mutation_audit(
        action="agents.agent.dispatch",
        target=lambda self, info, input: ("workload", input.agent_slug),
    )
    @require_permission(Permission.AGENT_DISPATCH)
    @tenant_scoped()
    def run_astrolift_agent(
        self, info: Info, input: RunAstroliftAgentInput
    ) -> MutationResultType[AgentTaskType]:
        """Dispatch a Once run of a registered agent ``Workload(kind=agent)``.

        Spec 33, PR-1 — the user-facing seam that bridges a registered agent
        Workload to the existing Temporal dispatch pipeline. Unlike
        ``launch_task`` (Brief-based, never sets ``agent_definition`` and
        dead-ends at QUEUED with no pickup), this resolver:

          1. resolves the agent Workload by slug, org-scoped to the caller's
             active tenant (a foreign-org slug is not resolvable -> NOT_FOUND);
          2. creates an ``AgentTask`` with ``agent_definition`` set to that
             Workload (the K8s Job spawner requires it to render the pod image)
             plus the optional ``environment_spec``, in DRAFT then advanced to
             QUEUED via the model's sanctioned ``transition_to``;
          3. enqueues ``DispatchAgentTaskWorkflow`` for the task, which runs it
             through spawn -> poll -> terminal via the ``dispatch_agent_task``
             activity (reusing ``execute_agent_stage``'s sync helpers).

        Returns the created task (id + status) so the FE can poll it to a
        terminal state. Only the run-spec ``once`` mode is wired in PR-1; the
        Workload's run-spec fields carry the other modes for later PRs.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        slug = (input.agent_slug or "").strip()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "agentSlug is required", field="agentSlug")

        from astrolift_agents.services.agent_dispatch import (
            AgentDispatchError,
            dispatch_registered_agent,
        )

        try:
            task = dispatch_registered_agent(
                organization_id=org_pk,
                agent_slug=slug,
                actor=_dispatch_actor(info),
                environment_spec_guid=(
                    str(input.environment_spec_id) if input.environment_spec_id is not None else ""
                ),
                trigger_payload=input.trigger_payload or None,
                timeout_seconds=input.timeout_seconds,
                trigger="manual",
            )
        except AgentDispatchError as exc:
            code = {
                "validation": ErrorCode.VALIDATION.value,
                "not_found": ErrorCode.NOT_FOUND.value,
                "precondition": ErrorCode.PRECONDITION.value,
            }.get(exc.code, ErrorCode.INTERNAL.value)
            return gql_failure(
                code,
                exc.message,
                field={
                    "agent_slug": "agentSlug",
                    "environment_spec_id": "environmentSpecId",
                    "timeout_seconds": "timeoutSeconds",
                }.get(exc.field),
            )

        return gql_success(agent_task_to_type(task))

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def create_agent_trigger(
        self,
        info: Info,
        agent_slug: str,
        scm_repo: str = "",
        branch_pattern: str = "",
        input_mapping: JSON | None = None,
    ) -> AgentTriggerResult:
        """Create / bind an inbound trigger webhook to an agent ``Workload``
        (spec 33, PR-6 / #951).

        The dispatch side (``dispatch_agent_task_from_webhook``) + the model's
        ``agent_definition`` FK already exist; this is the binding seam. Returns
        the endpoint + plaintext signing secret (shown once). Fire it with
        ``POST <endpoint>`` and header ``X-Astrolift-Signature: <secret>``.

        ``scmRepo`` / ``branchPattern`` are optional SCM fan-out filters: set
        them and the binding ALSO fires on matching push/PR events routed to the
        org, not only on a direct POST. ``inputMapping`` is the
        ``{out_key: dotted.source.path}`` spec applied to the firing payload
        before it reaches the dispatched agent Task (the dispatch fan-out reads
        it via ``apply_input_mapping``); omit / ``null`` passes the payload
        through verbatim.
        """
        from astrolift_agents.services.workflow_triggers import (
            create_agent_webhook_trigger,
        )

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return AgentTriggerResult(ok=False, message="no active organization")

        slug = (agent_slug or "").strip()
        if not slug:
            return AgentTriggerResult(ok=False, message="agentSlug is required")

        if input_mapping is not None and not isinstance(input_mapping, dict):
            return AgentTriggerResult(ok=False, message="inputMapping must be an object")

        workload = (
            Workload.objects.filter(
                slug=slug,
                registered_app__organization_id=org_pk,
                registered_app__deleted_at__isnull=True,
                deleted_at__isnull=True,
            )
            .select_related("registered_app", "registered_app__organization")
            .first()
        )
        if workload is None or workload.kind != Workload.Kind.AGENT:
            return AgentTriggerResult(ok=False, message="agent not found")

        result = create_agent_webhook_trigger(
            workload,
            input_mapping=input_mapping or None,
            scm_repo=(scm_repo or "").strip(),
            branch_pattern=(branch_pattern or "").strip(),
        )
        return AgentTriggerResult(
            ok=True,
            slug=result["slug"],
            endpoint=result["endpoint"],
            signing_secret=result["signing_secret"],
        )

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def unbind_agent_trigger(self, info: Info, slug: str) -> AgentTriggerResult:
        """Remove an agent trigger binding by webhook ``slug`` (#951).

        Disables the bound ``WorkflowWebhook`` (``enabled=False``) rather than
        deleting it — webhook slugs are deliberately never recycled (an old URL
        could still be POSTed to), so a disabled row is the documented removal
        semantics (see ``WorkflowWebhook`` model). Org-scoped: a caller can only
        unbind a webhook owned by their active org bound to one of their agents;
        a foreign / unknown slug is a non-leaking "not found".
        """
        from astrolift_agents.models.workflow_trigger import WorkflowWebhook

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return AgentTriggerResult(ok=False, message="no active organization")

        hook_slug = (slug or "").strip()
        if not hook_slug:
            return AgentTriggerResult(ok=False, message="slug is required")

        hook = WorkflowWebhook.objects.filter(
            slug=hook_slug,
            organization_id=org_pk,
            agent_definition__isnull=False,
        ).first()
        if hook is None:
            return AgentTriggerResult(ok=False, message="trigger not found")

        if hook.enabled:
            hook.enabled = False
            hook.save(update_fields=["enabled", "updated_at"])
        return AgentTriggerResult(ok=True, slug=hook.slug)

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def scale_service_agent(self, info: Info, agent_slug: str, target_replicas: int) -> AgentScaleResult:
        """On-demand scale of a Service-family agent's Deployment (#1012).

        The manual override alongside the scheduled scale ticks. Clamps to the
        env replica bounds, patches the live Deployment, and persists
        ``Workload.replicas`` so a later redeploy / scale tick doesn't revert
        the operator's intent. ``target_replicas=0`` is the pause (the scale
        tick + dispatch already honour a 0/scaled-down Service agent). Errors
        if the agent isn't Service-family or hasn't been deployed yet.
        """
        from astrolift_lifecycle.services.k8s_ops import K8sOpError, scale_workload

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return AgentScaleResult(ok=False, message="no active organization")
        if target_replicas < 0:
            return AgentScaleResult(ok=False, message="target_replicas must be >= 0")

        slug = (agent_slug or "").strip()
        workload = (
            Workload.objects.filter(
                slug=slug,
                registered_app__organization_id=org_pk,
                registered_app__deleted_at__isnull=True,
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
        )
        if workload is None or workload.kind != Workload.Kind.AGENT:
            return AgentScaleResult(ok=False, message="agent not found")
        if workload.run_family != Workload.RunFamily.SERVICE:
            return AgentScaleResult(
                ok=False,
                message=f"agent {slug!r} is not a service-family agent (run_family={workload.run_family})",
            )

        try:
            res = scale_workload(workload, int(target_replicas))
        except K8sOpError as exc:
            msg = exc.message
            if exc.code == "NOT_FOUND":
                msg = f"{msg} — deploy the agent before scaling"
            return AgentScaleResult(ok=False, message=msg)

        # Persist intent so a redeploy / scale tick doesn't revert it (mirrors
        # the scale-tick's own write-back of Workload.replicas).
        workload.replicas = int(target_replicas)
        workload.save(update_fields=["replicas", "updated_at"])

        return AgentScaleResult(
            ok=True,
            desired_replicas=res.current_replicas,
            ready_replicas=res.ready_replicas,
        )

    @strawberry.field
    @mutation_audit(
        action="agents.agent.configure_run_spec",
        target=lambda self, info, agent_slug, input: ("workload", agent_slug),
    )
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_agent_run_spec(
        self, info: Info, agent_slug: str, input: AgentRunSpecInput
    ) -> MutationResultType[AgentRunSpecType]:
        """Write the run-spec of a registered agent ``Workload(kind=agent)``.

        Spec 33 — the backend write surface the run-spec editors target:
        the Once/Schedule/Service editor (PR-11) and the Loop/Trigger +
        scaling editor (PR-12) both POST here. A partial update: only the
        fields the caller supplies are written (an omitted / ``None`` field
        is left unchanged), so an editor can save a single toggle without
        round-tripping the whole spec.

        Org-scoped exactly like ``run_astrolift_agent``: the workload is
        resolved through its ``RegisteredApp`` whose organization must be
        the caller's active tenant, so a foreign-org (or non-agent) slug is
        not resolvable. Gates on ``APP_UPDATE`` — configuring an agent
        workload's static run-spec is an app-config write, the same grant
        the Skill / ToolDef / AgentEnvironmentSpec writes in this module use
        (distinct from ``AGENT_DISPATCH``, which authorizes *running* an
        agent, not configuring it).

        Validation returns a structured field error (never a 500):

          * each cron field is shape-checked via the platform cron parser
            when supplied non-empty (and stored normalized); an empty string
            clears it;
          * ``run_mode == schedule`` (Task family) requires a non-empty
            ``run_cron_expression`` — either already stored or in this input;
          * the Service-family fields (``replicas`` / ``scheduled_scale_to``
            / ``scale_*_cron``) are Service-family only — setting any on a
            Task agent is rejected; ``run_max_parallel`` (the Loop cap) is
            Task-family only — setting it on a Service agent is rejected;
          * ``replicas`` (the Service baseline desired count) and
            ``scheduled_scale_to`` must each be a positive int (the env-max
            clamp is deferred to the deploy/scaling tick, which resolves the
            per-env ceiling live via ``resolve_replica_bounds`` + clamps);
          * ``run_max_parallel`` must be >= 0 (0 = Loop soft-pause; null =
            leave unchanged, which the tick reads as the platform default).

        ``clear_scheduled_scaling`` (#952) is the dedicated "remove the
        schedule" seam: a normal partial update can't NULL
        ``scheduled_scale_to`` (``None`` means leave-unchanged), so setting
        the flag clears the whole triple — both scale crons to "" and the
        scale target to NULL (``replicas`` is left untouched). It's rejected
        when combined with an explicit scaling value in the same call.

        Coherence is judged against the EFFECTIVE family/mode (the supplied
        value, else the stored one) so a partial save validates against the
        spec the row will actually have. Returns the updated run-spec so the
        editor reads back the persisted state in one round-trip.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        slug = (agent_slug or "").strip()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "agentSlug is required", field="agentSlug")

        # Resolve the agent Workload org-scoped through its RegisteredApp —
        # the same non-leaking lookup run_astrolift_agent uses. A foreign-org
        # slug is not resolvable, so the surface never edits another tenant's
        # agent (the @tenant_scoped decorator only asserts a tenant exists; it
        # does NOT filter — this query is what enforces isolation).
        workload = (
            Workload.objects.filter(
                slug=slug,
                registered_app__organization_id=org_pk,
                registered_app__deleted_at__isnull=True,
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
        )
        if workload is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "agent not found", field="agentSlug")
        if workload.kind != Workload.Kind.AGENT:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"workload {slug!r} is not an agent (kind={workload.kind})",
                field="agentSlug",
            )

        # ---- Effective family/mode (supplied value, else stored) ---------
        eff_family = input.run_family.value if input.run_family is not None else workload.run_family
        eff_mode = input.run_mode.value if input.run_mode is not None else workload.run_mode

        # ---- Cron shape validation (normalize on success) ---------------
        # Validate every supplied non-empty cron; an explicit "" clears the
        # field (allowed). Track the normalized value to persist.
        normalized: dict[str, str] = {}
        for field_name, gql_field in (
            ("run_cron_expression", "runCronExpression"),
            ("scale_up_cron", "scaleUpCron"),
            ("scale_down_cron", "scaleDownCron"),
        ):
            raw = getattr(input, field_name)
            if raw is None:
                continue
            if not raw.strip():
                normalized[field_name] = ""
                continue
            try:
                normalized[field_name] = validate_cron_expression(raw)
            except CronValidationError as exc:
                return gql_failure(ErrorCode.VALIDATION.value, str(exc), field=gql_field)

        # ---- Family/mode coherence (reject incoherent combos) -----------
        # Scaling fields are Service-only. Reject SETTING a meaningful scaling
        # value (a non-empty cron / a scale target) on a Task-family agent;
        # clearing them ("" / leaving them None) is always fine.
        sets_scale_up = "scale_up_cron" in normalized and normalized["scale_up_cron"] != ""
        sets_scale_down = "scale_down_cron" in normalized and normalized["scale_down_cron"] != ""
        sets_scale_to = input.scheduled_scale_to is not None
        sets_replicas = input.replicas is not None

        # ``clearScheduledScaling`` turns the whole scheduled-scaling triple
        # off; supplying it alongside a meaningful scaling value is
        # contradictory intent (set vs clear in one call) — reject it.
        if input.clear_scheduled_scaling and (sets_scale_up or sets_scale_down or sets_scale_to):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "pass either clearScheduledScaling or the scaling fields, not both",
                field="clearScheduledScaling",
            )
        if eff_family == Workload.RunFamily.TASK.value:
            if sets_scale_up:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "scaleUpCron applies to service-family agents only",
                    field="scaleUpCron",
                )
            if sets_scale_down:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "scaleDownCron applies to service-family agents only",
                    field="scaleDownCron",
                )
            if sets_scale_to:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "scheduledScaleTo applies to service-family agents only",
                    field="scheduledScaleTo",
                )
            if sets_replicas:
                # replicas is the Service Deployment's baseline count; a Task
                # agent runs as a Job and has no replica count to set.
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "replicas applies to service-family agents only",
                    field="replicas",
                )

        # The Loop concurrency cap is a Task-family concern; a Service agent
        # scales via ``replicas``, not a per-task cap. Reject setting it on a
        # Service agent.
        if input.run_max_parallel is not None and eff_family == Workload.RunFamily.SERVICE.value:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "runMaxParallel applies to task-family agents only",
                field="runMaxParallel",
            )

        # Schedule mode (a Task concern) needs a cron to fire on. Check the
        # effective cron: the one supplied in this input, else the stored one.
        if eff_family == Workload.RunFamily.TASK.value and eff_mode == Workload.RunMode.SCHEDULE.value:
            eff_cron = normalized.get("run_cron_expression", workload.run_cron_expression or "")
            if not eff_cron.strip():
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "schedule run_mode requires a run_cron_expression",
                    field="runCronExpression",
                )

        # ---- Numeric sanity --------------------------------------------
        if input.replicas is not None and input.replicas < 1:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "replicas must be a positive integer",
                field="replicas",
            )
        if input.scheduled_scale_to is not None and input.scheduled_scale_to < 1:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "scheduledScaleTo must be a positive integer",
                field="scheduledScaleTo",
            )
        if input.run_max_parallel is not None and input.run_max_parallel < 0:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "runMaxParallel must be zero or a positive integer",
                field="runMaxParallel",
            )

        # ---- Apply (only supplied fields) -------------------------------
        update_fields: list[str] = []
        if input.run_family is not None:
            workload.run_family = input.run_family.value
            update_fields.append("run_family")
        if input.run_mode is not None:
            workload.run_mode = input.run_mode.value
            update_fields.append("run_mode")
        if "run_cron_expression" in normalized:
            workload.run_cron_expression = normalized["run_cron_expression"]
            update_fields.append("run_cron_expression")
        if input.run_paused is not None:
            workload.run_paused = bool(input.run_paused)
            update_fields.append("run_paused")
        if input.run_max_parallel is not None:
            workload.run_max_parallel = int(input.run_max_parallel)
            update_fields.append("run_max_parallel")
        if input.replicas is not None:
            workload.replicas = int(input.replicas)
            update_fields.append("replicas")
        if input.scheduled_scale_to is not None:
            workload.scheduled_scale_to = int(input.scheduled_scale_to)
            update_fields.append("scheduled_scale_to")
        if "scale_up_cron" in normalized:
            workload.scale_up_cron = normalized["scale_up_cron"]
            update_fields.append("scale_up_cron")
        if "scale_down_cron" in normalized:
            workload.scale_down_cron = normalized["scale_down_cron"]
            update_fields.append("scale_down_cron")
        if input.clear_scheduled_scaling:
            # Reset the scheduled-scaling triple to its "off" state. NULLs the
            # scale target (the one value a normal update can't reach) and
            # blanks both crons; ``replicas`` (the Service baseline) is left
            # as-is. Dedup against any "" cron the caller also supplied.
            workload.scale_up_cron = ""
            workload.scale_down_cron = ""
            workload.scheduled_scale_to = None
            for _f in ("scale_up_cron", "scale_down_cron", "scheduled_scale_to"):
                if _f not in update_fields:
                    update_fields.append(_f)

        if update_fields:
            # Bump the optimistic-concurrency version + updated_at alongside
            # the run-spec columns (NamedBaseCoreModel.save tracks them).
            workload.save(update_fields=[*update_fields, "updated_at", "version"])

        return gql_success(agent_run_spec_to_type(workload))

    @strawberry.field
    @mutation_audit(action="agents.skill.import_from_repo")
    @require_permission(Permission.SKILL_IMPORT)
    @tenant_scoped()
    def import_skills_from_repo(
        self,
        info: Info,
        repo_url: str,
        branch: str = "main",
        manifest_path: str = "",
    ) -> MutationResultType[ImportSkillsResult]:
        """Import Skills and ToolDefs from an ``astrolift.toml`` in a
        GitHub repository.  Idempotent — re-importing updates existing
        rows matched on ``(organization, slug)``; versions bump only when
        content changes."""
        import requests

        from astrolift_agents.services.skill_importer import (
            InvalidRepoURLError,
            import_skills_from_repo,
        )

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        from astrolift_identity.models import Organization

        org = Organization.objects.filter(pk=org_pk, deleted_at__isnull=True).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        try:
            result = import_skills_from_repo(
                organization=org,
                repo_url=repo_url,
                branch=branch,
                manifest_path=manifest_path,
            )
        except InvalidRepoURLError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="repoUrl")
        except requests.RequestException as exc:
            # Upstream fetch failed (repo/branch missing, network, non-2xx).
            # That's a client-fixable precondition, not a server INTERNAL.
            return gql_failure(ErrorCode.PRECONDITION.value, f"could not fetch repo: {exc}")
        except Exception as exc:  # noqa: BLE001
            return gql_failure(ErrorCode.INTERNAL.value, str(exc))

        return gql_success(
            ImportSkillsResult(
                imported_skills=result.imported_skills,
                imported_tools=result.imported_tools,
                source_ref=result.source_ref,
            )
        )

    # ---- OrgSkillRepo CRUD (spec 39d) -----------------------------
    #
    # An org registers one or more skill repos under aliases; a manifest
    # references a skill in one as ``"<alias>/<skill-path>@<ref>"`` and the
    # resolver fetches it alongside the built-in catalogue + local skills.
    # Mechanism mirrors source-connection registration: gated on the SAME
    # ``scm.connect`` grant source registration uses (registering a skill
    # repo is the same trust decision as connecting a source host), and
    # tenancy-scoped to the caller's active org exactly like ``ScmMutation``.

    @strawberry.field
    @mutation_audit(action="agents.org_skill_repo.register")
    @require_permission(Permission.SCM_CONNECT)
    @tenant_scoped()
    def register_org_skill_repo(
        self, info: Info, input: RegisterOrgSkillRepoInput, org_id: strawberry.ID
    ) -> MutationResultType[OrgSkillRepoType]:
        org, err = _resolve_org(org_id)
        if err is not None:
            return err

        alias = (input.alias or "").strip()
        if not alias:
            return gql_failure(ErrorCode.VALIDATION.value, "alias is required", field="alias")
        if "/" in alias or "@" in alias:
            # The alias is the first segment of "<alias>/<path>@<ref>"; a '/'
            # or '@' in it would make every manifest ref ambiguous.
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "alias must not contain '/' or '@'",
                field="alias",
            )
        repo_full_name = (input.repo_full_name or "").strip().strip("/")
        if not repo_full_name:
            return gql_failure(ErrorCode.VALIDATION.value, "repoFullName is required", field="repoFullName")
        source_kind = (input.source_kind or "github").strip()
        if source_kind not in _SKILL_REPO_SOURCE_KINDS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown source_kind {source_kind!r}",
                field="sourceKind",
            )

        if OrgSkillRepo.objects.filter(organization=org, alias=alias, deleted_at__isnull=True).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"a skill repo with alias {alias!r} already exists",
                field="alias",
            )

        conn = None
        if input.source_connection_id is not None:
            conn, conn_err = _resolve_org_skill_repo_connection(org, input.source_connection_id)
            if conn_err is not None:
                return conn_err

        with transaction.atomic():
            repo = OrgSkillRepo.objects.create(
                organization=org,
                alias=alias[:128],
                repo_full_name=repo_full_name[:512],
                source_kind=source_kind[:32],
                default_ref=(input.default_ref or "main").strip()[:255] or "main",
                display_name=(input.display_name or "").strip()[:200],
                source_connection=conn,
                is_active=True,
            )
        return gql_success(org_skill_repo_to_type(repo))

    @strawberry.field
    @mutation_audit(action="agents.org_skill_repo.update")
    @require_permission(Permission.SCM_CONNECT)
    @tenant_scoped()
    def update_org_skill_repo(
        self, info: Info, input: UpdateOrgSkillRepoInput
    ) -> MutationResultType[OrgSkillRepoType]:
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        repo = OrgSkillRepo.objects.filter(
            guid=str(input.id), organization_id=org_pk, deleted_at__isnull=True
        ).first()
        if repo is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "skill repo not found")

        if input.source_connection_id is not None and input.detach_source_connection:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "pass either sourceConnectionId (attach) or detachSourceConnection (drop), not both",
                field="sourceConnectionId",
            )

        if input.repo_full_name is not None:
            new_repo = input.repo_full_name.strip().strip("/")
            if not new_repo:
                return gql_failure(
                    ErrorCode.VALIDATION.value, "repoFullName must be non-empty", field="repoFullName"
                )
            repo.repo_full_name = new_repo[:512]
        if input.source_kind is not None:
            sk = input.source_kind.strip()
            if sk not in _SKILL_REPO_SOURCE_KINDS:
                return gql_failure(
                    ErrorCode.VALIDATION.value, f"unknown source_kind {sk!r}", field="sourceKind"
                )
            repo.source_kind = sk[:32]
        if input.default_ref is not None:
            repo.default_ref = (input.default_ref.strip()[:255]) or "main"
        if input.display_name is not None:
            repo.display_name = input.display_name.strip()[:200]
        if input.is_active is not None:
            repo.is_active = bool(input.is_active)
        if input.detach_source_connection:
            repo.source_connection = None
        elif input.source_connection_id is not None:
            org = Organization.objects.filter(pk=org_pk, deleted_at__isnull=True).first()
            if org is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")
            conn, conn_err = _resolve_org_skill_repo_connection(org, input.source_connection_id)
            if conn_err is not None:
                return conn_err
            repo.source_connection = conn

        repo.save()
        return gql_success(org_skill_repo_to_type(repo))

    @strawberry.field
    @mutation_audit(action="agents.org_skill_repo.remove")
    @require_permission(Permission.SCM_CONNECT)
    @tenant_scoped()
    def remove_org_skill_repo(
        self, info: Info, input: RemoveOrgSkillRepoInput
    ) -> MutationResultType[OrgSkillRepoType]:
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        repo = OrgSkillRepo.objects.filter(
            guid=str(input.id), organization_id=org_pk, deleted_at__isnull=True
        ).first()
        if repo is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "skill repo not found")
        repo.soft_delete()
        return gql_success(org_skill_repo_to_type(repo))


def json_canonical(payload: dict) -> str:
    """Deterministic JSON encoding for content-hashing (sorted keys,
    no insignificant whitespace)."""
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
