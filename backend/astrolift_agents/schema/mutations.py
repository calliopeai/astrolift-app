"""Mutations for the agent platform.

Skill + ToolDef registry
------------------------
* ``importSkillsFromRepo(repoUrl, branch?)`` — fetch a GitHub config
  repo's ``astrolift.toml`` and materialize org-scoped Skill + ToolDef
  rows from its ``[skills.*]`` / ``[tools.*]`` tables.
* ``createSkill(input)`` — create a new org-scoped skill.
* ``updateSkill(id, input)`` — update mutable fields of an existing skill.
* ``deleteSkill(id)`` — soft-delete an org-scoped skill.

AgentTask
---------
* ``enqueueAgentTask(input)`` — create a new task in DRAFT state then
  immediately transition it to QUEUED.
* ``cancelAgentTask(id)`` — cancel a task that is not yet in a terminal
  state.

AgentEnvironmentSpec
--------------------
* ``createAgentEnvironmentSpec(input)`` — create a new container-env spec.
* ``updateAgentEnvironmentSpec(slug, input)`` — update mutable fields.
* ``deleteAgentEnvironmentSpec(slug)`` — soft-delete the spec.

All mutations return the standard ``MutationResult`` envelope so the FE
branches on ``ok`` / ``errors`` uniformly. Permission-denied and
unexpected errors are surfaced via ``@mutation_audit`` so resolvers never
raise.
"""

from __future__ import annotations

import logging

import requests
import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Skill
from astrolift_agents.schema.types import (
    AgentEnvironmentSpecType,
    AgentTaskType,
    ImportSkillsResultType,
    SkillType,
    agent_environment_spec_to_type,
    agent_task_to_type,
    skill_to_type,
)
from astrolift_agents.services.skill_importer import (
    InvalidRepoURLError,
    import_skills_from_repo,
)
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


def _resolve_org() -> Organization | None:
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return None
    return Organization.objects.filter(pk=tenant.organization_id).first()


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@strawberry.input
class CreateSkillInput:
    name: str
    slug: str
    description: str = ""
    content: str = ""
    agent_type: str = ""
    is_global: bool = False


@strawberry.input
class UpdateSkillInput:
    name: str | None = None
    description: str | None = None
    content: str | None = None
    agent_type: str | None = None
    is_active: bool | None = None


@strawberry.input
class EnqueueAgentTaskInput:
    timeout_seconds: int = 300
    callback_url: str = ""
    source_ref: str = ""


@strawberry.input
class CreateAgentEnvironmentSpecInput:
    name: str
    slug: str
    image_tag: str
    agent_type: str
    tool_preset: str = ""
    allow_install: bool = False
    vnc_enabled: bool = False
    # Pass an empty list/dict when no refs/vars are needed; None is treated
    # the same way in the resolver to keep the wire format forgiving.
    secret_refs: strawberry.scalars.JSON | None = None
    env_vars: strawberry.scalars.JSON | None = None
    config_repo: str = ""
    config_branch: str = "main"


@strawberry.input
class UpdateAgentEnvironmentSpecInput:
    name: str | None = None
    image_tag: str | None = None
    tool_preset: str | None = None
    allow_install: bool | None = None
    vnc_enabled: bool | None = None
    secret_refs: strawberry.scalars.JSON | None = None
    env_vars: strawberry.scalars.JSON | None = None
    config_repo: str | None = None
    config_branch: str | None = None


# ---------------------------------------------------------------------------
# Mutation type
# ---------------------------------------------------------------------------


@strawberry.type
class AgentsMutation:
    # -----------------------------------------------------------------------
    # Skill + ToolDef registry
    # -----------------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="skill.import")
    @require_permission(Permission.SKILL_IMPORT)
    @tenant_scoped()
    def import_skills_from_repo(
        self,
        info: Info,
        repo_url: str,
        branch: str = "main",
    ) -> MutationResultType[ImportSkillsResultType]:
        org = _resolve_org()
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        branch = (branch or "main").strip() or "main"

        try:
            result = import_skills_from_repo(
                organization=org,
                repo_url=repo_url,
                branch=branch,
            )
        except InvalidRepoURLError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="repoUrl")
        except requests.HTTPError as exc:
            # The repo/branch doesn't exist or our PAT can't reach it.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"could not fetch repo: {exc}",
                field="repoUrl",
            )

        return gql_success(
            ImportSkillsResultType(
                imported_skills=result.imported_skills,
                imported_tools=result.imported_tools,
                source_ref=result.source_ref,
            )
        )

    @strawberry.field
    @mutation_audit(action="skill.create")
    @require_permission(Permission.SKILL_WRITE)
    @tenant_scoped()
    def create_skill(
        self,
        info: Info,
        input: CreateSkillInput,
    ) -> MutationResultType[SkillType]:
        name = (input.name or "").strip()
        if not name:
            return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")
        slug = (input.slug or "").strip()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")

        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "tenant context required")

        if Skill.objects.filter(
            organization_id=tenant.organization_id,
            slug=slug,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"a skill with slug {slug!r} already exists in this organization",
                field="slug",
            )

        with transaction.atomic():
            skill = Skill.objects.create(
                organization_id=tenant.organization_id,
                name=name,
                slug=slug,
                description=(input.description or "").strip(),
                content=(input.content or "").strip(),
                agent_type=(input.agent_type or "").strip(),
                is_global=input.is_global,
            )

        return gql_success(skill_to_type(skill))

    @strawberry.field
    @mutation_audit(action="skill.update")
    @require_permission(Permission.SKILL_WRITE)
    @tenant_scoped()
    def update_skill(
        self,
        info: Info,
        id: GUID,
        input: UpdateSkillInput,
    ) -> MutationResultType[SkillType]:
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "tenant context required")

        skill = Skill.objects.filter(
            guid=str(id),
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        if skill is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "skill not found")

        update_fields: list[str] = ["updated_at", "version"]

        if input.name is not None:
            name = input.name.strip()
            if not name:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "name must not be empty",
                    field="name",
                )
            skill.name = name
            update_fields.append("name")

        if input.description is not None:
            skill.description = input.description
            update_fields.append("description")

        if input.content is not None:
            skill.content = input.content
            update_fields.append("content")

        if input.agent_type is not None:
            skill.agent_type = input.agent_type
            update_fields.append("agent_type")

        if input.is_active is not None:
            skill.is_active = input.is_active
            update_fields.append("is_active")

        skill.save(update_fields=update_fields)
        return gql_success(skill_to_type(skill))

    @strawberry.field
    @mutation_audit(action="skill.delete")
    @require_permission(Permission.SKILL_WRITE)
    @tenant_scoped()
    def delete_skill(
        self,
        info: Info,
        id: GUID,
    ) -> MutationResultType[SkillType]:
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "tenant context required")

        skill = (
            Skill.objects.filter(
                guid=str(id),
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .prefetch_related("tool_defs")
            .first()
        )
        if skill is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "skill not found")

        snapshot = skill_to_type(skill)
        skill.deleted_at = timezone.now()
        skill.save(update_fields=["deleted_at", "updated_at", "version"])
        return gql_success(snapshot)

    # -----------------------------------------------------------------------
    # AgentTask
    # -----------------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="agent_task.enqueue")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def enqueue_agent_task(
        self,
        info: Info,
        input: EnqueueAgentTaskInput,
    ) -> MutationResultType[AgentTaskType]:
        """Create a new AgentTask and immediately transition it to QUEUED."""
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "tenant context required")

        with transaction.atomic():
            task = AgentTask.objects.create(
                organization_id=tenant.organization_id,
                timeout_seconds=max(1, input.timeout_seconds),
                callback_url=(input.callback_url or "").strip(),
                source_ref=(input.source_ref or "").strip(),
            )
            task.transition_to(AgentTask.Status.QUEUED)

        return gql_success(agent_task_to_type(task))

    @strawberry.field
    @mutation_audit(action="agent_task.cancel")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def cancel_agent_task(
        self,
        info: Info,
        id: GUID,
    ) -> MutationResultType[AgentTaskType]:
        """Cancel an agent task that has not yet reached a terminal state."""
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "tenant context required")

        task = AgentTask.objects.filter(
            guid=str(id),
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        if task is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "agent task not found")

        terminal = {
            AgentTask.Status.COMPLETED,
            AgentTask.Status.FAILED,
            AgentTask.Status.TIMED_OUT,
            AgentTask.Status.CANCELLED,
        }
        if task.status in terminal:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"task is in status {task.status!r} — only non-terminal tasks can be cancelled",
            )

        try:
            task.transition_to(AgentTask.Status.CANCELLED)
        except ValueError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))

        return gql_success(agent_task_to_type(task))

    # -----------------------------------------------------------------------
    # AgentEnvironmentSpec
    # -----------------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="agent_env_spec.create")
    @require_permission(Permission.AGENT_ENV_SPEC_CREATE)
    @tenant_scoped()
    def create_agent_environment_spec(
        self,
        info: Info,
        input: CreateAgentEnvironmentSpecInput,
    ) -> MutationResultType[AgentEnvironmentSpecType]:
        name = (input.name or "").strip()
        if not name:
            return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")
        slug = (input.slug or "").strip()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "slug is required", field="slug")
        image_tag = (input.image_tag or "").strip()
        if not image_tag:
            return gql_failure(
                ErrorCode.VALIDATION.value, "imageTag is required", field="imageTag"
            )

        valid_agent_types = {c.value for c in AgentEnvironmentSpec.AgentType}
        agent_type = (input.agent_type or "").strip()
        if agent_type not in valid_agent_types:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown agentType {agent_type!r}; valid values: {sorted(valid_agent_types)}",
                field="agentType",
            )

        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "tenant context required")

        if AgentEnvironmentSpec.objects.filter(
            organization_id=tenant.organization_id,
            slug=slug,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"an environment spec with slug {slug!r} already exists in this organization",
                field="slug",
            )

        with transaction.atomic():
            spec = AgentEnvironmentSpec.objects.create(
                organization_id=tenant.organization_id,
                name=name,
                slug=slug,
                image_tag=image_tag,
                agent_type=agent_type,
                tool_preset=(input.tool_preset or "").strip(),
                allow_install=input.allow_install,
                vnc_enabled=input.vnc_enabled,
                # Accept None from the wire to mean "empty" for list/dict fields.
                secret_refs=input.secret_refs if input.secret_refs is not None else [],
                env_vars=input.env_vars if input.env_vars is not None else {},
                config_repo=(input.config_repo or "").strip(),
                config_branch=(input.config_branch or "main").strip() or "main",
            )

        return gql_success(agent_environment_spec_to_type(spec))

    @strawberry.field
    @mutation_audit(action="agent_env_spec.update")
    @require_permission(Permission.AGENT_ENV_SPEC_UPDATE)
    @tenant_scoped()
    def update_agent_environment_spec(
        self,
        info: Info,
        slug: str,
        input: UpdateAgentEnvironmentSpecInput,
    ) -> MutationResultType[AgentEnvironmentSpecType]:
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "tenant context required")

        spec = AgentEnvironmentSpec.objects.filter(
            slug=slug,
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        if spec is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment spec not found")

        update_fields: list[str] = ["updated_at", "version"]

        if input.name is not None:
            name = input.name.strip()
            if not name:
                return gql_failure(
                    ErrorCode.VALIDATION.value, "name must not be empty", field="name"
                )
            spec.name = name
            update_fields.append("name")

        if input.image_tag is not None:
            image_tag = input.image_tag.strip()
            if not image_tag:
                return gql_failure(
                    ErrorCode.VALIDATION.value, "imageTag must not be empty", field="imageTag"
                )
            spec.image_tag = image_tag
            update_fields.append("image_tag")

        if input.tool_preset is not None:
            spec.tool_preset = input.tool_preset.strip()
            update_fields.append("tool_preset")

        if input.allow_install is not None:
            spec.allow_install = input.allow_install
            update_fields.append("allow_install")

        if input.vnc_enabled is not None:
            spec.vnc_enabled = input.vnc_enabled
            update_fields.append("vnc_enabled")

        if input.secret_refs is not None:
            spec.secret_refs = input.secret_refs
            update_fields.append("secret_refs")

        if input.env_vars is not None:
            spec.env_vars = input.env_vars
            update_fields.append("env_vars")

        if input.config_repo is not None:
            spec.config_repo = input.config_repo.strip()
            update_fields.append("config_repo")

        if input.config_branch is not None:
            spec.config_branch = (input.config_branch.strip() or "main")
            update_fields.append("config_branch")

        spec.save(update_fields=update_fields)
        return gql_success(agent_environment_spec_to_type(spec))

    @strawberry.field
    @mutation_audit(action="agent_env_spec.delete")
    @require_permission(Permission.AGENT_ENV_SPEC_DELETE)
    @tenant_scoped()
    def delete_agent_environment_spec(
        self,
        info: Info,
        slug: str,
    ) -> MutationResultType[AgentEnvironmentSpecType]:
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "tenant context required")

        spec = AgentEnvironmentSpec.objects.filter(
            slug=slug,
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        if spec is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment spec not found")

        snapshot = agent_environment_spec_to_type(spec)
        spec.deleted_at = timezone.now()
        spec.save(update_fields=["deleted_at", "updated_at", "version"])
        return gql_success(snapshot)
