"""Mutations for the Agent Dispatch Layer.

Skill + ToolDef CRUD, Brief assembly, and Task launch/cancel. Every
resolver carries ``@mutation_audit`` + ``@require_permission`` +
``@tenant_scoped`` per the platform convention and the tenancy
guardrail. ``@tenant_scoped`` only asserts a tenant context exists, so
each resolver applies its own org filter / org binding explicitly.

Permissions reuse the app-tier grants (Skills/Briefs/ToolDefs are
agent-workload building blocks): ``APP_READ`` is implied by the read
surface, writes gate on ``APP_CREATE`` / ``APP_UPDATE`` / ``APP_DELETE``,
and Brief assemble/launch/cancel gate on ``APP_DEPLOY``. The user-facing
agent dispatch (``run_astrolift_agent``, spec 33 PR-1) gates on the
dedicated ``AGENT_DISPATCH`` grant instead.
"""

from __future__ import annotations

import hashlib

import strawberry
from django.db import transaction
from strawberry.types import Info

from astrolift_agents.models import (
    AgentEnvironmentSpec,
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
    AgentTaskType,
    OrgSkillRepoType,
    SkillType,
    ToolDefType,
    agent_env_spec_to_type,
    agent_run_spec_to_type,
    agent_task_to_type,
    org_skill_repo_to_type,
    skill_to_type,
    tool_def_to_type,
)
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
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


# ---------------------------------------------------------------------------
# Root mutation
# ---------------------------------------------------------------------------


@strawberry.type(name="AstroliftImportSkillsResult")
class ImportSkillsResult:
    imported_skills: list[str]
    imported_tools: list[str]
    source_ref: str


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
    @require_permission(Permission.APP_CREATE)
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
                config_repo=(input.config_repo or "").strip()[:512],
                config_branch=(input.config_branch or "main").strip()[:128],
                config_manifest_path=(input.config_manifest_path or "").strip()[:512],
                secret_refs=list(input.secret_refs or []),
                env_vars=dict(input.env_vars or {}),
            )
        return gql_success(agent_env_spec_to_type(spec))

    @strawberry.field
    @mutation_audit(action="agents.env_spec.update")
    @require_permission(Permission.APP_UPDATE)
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
        if input.config_repo is not None:
            spec.config_repo = input.config_repo.strip()[:512]
        if input.config_branch is not None:
            spec.config_branch = input.config_branch.strip()[:128]
        if input.config_manifest_path is not None:
            spec.config_manifest_path = input.config_manifest_path.strip()[:512]
        if input.secret_refs is not None:
            spec.secret_refs = list(input.secret_refs)
        if input.env_vars is not None:
            spec.env_vars = dict(input.env_vars)
        spec.save()
        return gql_success(agent_env_spec_to_type(spec))

    @strawberry.field
    @mutation_audit(action="agents.env_spec.delete")
    @require_permission(Permission.APP_DELETE)
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
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def cancel_task(self, info: Info, id: strawberry.ID) -> MutationResultType[None]:
        """Cancel an AgentTask.

        Only ``DRAFT`` / ``QUEUED`` / ``PROVISIONING`` tasks cancel
        directly (a ``RUNNING`` task needs a stop signal to the
        Dispatcher); an illegal transition surfaces as a PRECONDITION
        failure rather than a 500.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        task = AgentTask.objects.filter(guid=str(id), organization_id=org_pk, deleted_at__isnull=True).first()
        if task is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "task not found")
        try:
            task.transition_to(AgentTask.Status.CANCELLED)
        except ValueError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        return gql_success(None)

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
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import DispatchAgentTaskInput

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        slug = (input.agent_slug or "").strip()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "agentSlug is required", field="agentSlug")

        # Resolve the agent Workload org-scoped: the workload lives under a
        # RegisteredApp whose organization must be the caller's active tenant.
        # A foreign-org (or non-agent) slug is not resolvable so the surface
        # never dispatches another tenant's agent or a non-agent workload.
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

        org = Organization.objects.filter(pk=org_pk, deleted_at__isnull=True).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        # Optional environment spec, org-scoped. A foreign-org spec id is
        # NOT_FOUND for the same non-leak reason as the workload lookup.
        env_spec = None
        if input.environment_spec_id is not None:
            env_spec = AgentEnvironmentSpec.objects.filter(
                guid=str(input.environment_spec_id),
                organization_id=org_pk,
                deleted_at__isnull=True,
            ).first()
            if env_spec is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "environment spec not found",
                    field="environmentSpecId",
                )

        timeout_seconds = input.timeout_seconds if input.timeout_seconds and input.timeout_seconds > 0 else 300

        # Ad-hoc input frozen on the task so the spawner surfaces it to the
        # pod as ASTROLIFT_TRIGGER_PAYLOAD (#930). None/empty -> no env var.
        dispatch_input = input.trigger_payload or None

        with transaction.atomic():
            task = AgentTask.objects.create(
                organization=org,
                agent_definition=workload,
                environment_spec=env_spec,
                status=AgentTask.Status.DRAFT,
                timeout_seconds=timeout_seconds,
                dispatch_input=dispatch_input,
                # Freeze VNC eligibility from the spec so the task stays
                # self-describing if the spec is later edited or deleted
                # (mirrors execute_agent_stage._create_agent_task_sync).
                vnc_enabled=bool(env_spec and env_spec.vnc_enabled),
            )
            task.transition_to(AgentTask.Status.QUEUED)

        # Enqueue the durable dispatch. Workflow id is keyed to the task guid
        # so a duplicate fire joins the in-flight run. When Temporal is
        # disabled (dev/CI) this is a logged no-op and the task stays QUEUED
        # until a worker picks it up — the FE still gets a pollable task.
        start_workflow(
            "DispatchAgentTaskWorkflow",
            args=[DispatchAgentTaskInput(agent_task_id=task.pk, actor=_dispatch_actor(info))],
            workflow_id=f"DispatchAgentTaskWorkflow-{task.guid}",
        )

        return gql_success(agent_task_to_type(task))

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def create_agent_trigger(
        self, info: Info, agent_slug: str
    ) -> AgentTriggerResult:
        """Create an inbound trigger webhook bound to an agent ``Workload``
        (spec 33, PR-6 / #983).

        The dispatch side (``dispatch_agent_task_from_webhook``) + the model's
        ``agent_definition`` FK already exist; this is the creation seam. Returns
        the endpoint + plaintext signing secret (shown once). Fire it with
        ``POST <endpoint>`` and header ``X-Astrolift-Signature: <secret>``.
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

        result = create_agent_webhook_trigger(workload)
        return AgentTriggerResult(
            ok=True,
            slug=result["slug"],
            endpoint=result["endpoint"],
            signing_secret=result["signing_secret"],
        )

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def scale_service_agent(
        self, info: Info, agent_slug: str, target_replicas: int
    ) -> AgentScaleResult:
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
                organization=org, repo_url=repo_url, branch=branch,
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
            return gql_failure(
                ErrorCode.VALIDATION.value, "repoFullName is required", field="repoFullName"
            )
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
