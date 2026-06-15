"""Mutations for the Agent Dispatch Layer.

Skill + ToolDef CRUD, Brief assembly, and Task launch/cancel. Every
resolver carries ``@mutation_audit`` + ``@require_permission`` +
``@tenant_scoped`` per the platform convention and the tenancy
guardrail. ``@tenant_scoped`` only asserts a tenant context exists, so
each resolver applies its own org filter / org binding explicitly.

Permissions reuse the app-tier grants (Skills/Briefs/ToolDefs are
agent-workload building blocks): ``APP_READ`` is implied by the read
surface, writes gate on ``APP_CREATE`` / ``APP_UPDATE`` / ``APP_DELETE``,
and dispatch (assemble/launch/cancel) gates on ``APP_DEPLOY``.
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
    Skill,
    ToolDef,
)
from astrolift_agents.schema.types import (
    AgentEnvironmentSpecType,
    SkillType,
    ToolDefType,
    agent_env_spec_to_type,
    skill_to_type,
    tool_def_to_type,
)
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
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
    secret_refs: JSON | None = None
    env_vars: JSON | None = None


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
    @mutation_audit(action="agents.skill.import_from_repo")
    @require_permission(Permission.SKILL_IMPORT)
    @tenant_scoped()
    def import_skills_from_repo(
        self,
        info: Info,
        repo_url: str,
        branch: str = "main",
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
            result = import_skills_from_repo(organization=org, repo_url=repo_url, branch=branch)
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


def json_canonical(payload: dict) -> str:
    """Deterministic JSON encoding for content-hashing (sorted keys,
    no insignificant whitespace)."""
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
