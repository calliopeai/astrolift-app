"""Brain read tokens (#2320): connect a client to a brain the caller can read.
Apps hosted on a brain (#2243): create one from a brain in one call.
Managing semantic layers (#2424): create, share, rebuild and delete them."""

from __future__ import annotations

import inspect
import re

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.operation_context import named_environment
from astrolift_registry.schema.brain_types import (
    BrainCreationType,
    BrainRebuildType,
    BrainShareAccess,
    BrainSubstrateInput,
    BrainType,
    RepoVisibility,
    brain_to_type,
    creation_to_type,
    selection_from_input,
)
from astrolift_registry.schema.mutations.helpers import _actor
from astrolift_registry.schema.types import RegisteredAppType
from astrolift_registry.scopes import app_scope_by_slug, registration_project_scope
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission


@strawberry.type(name="AstroliftBrainConnection")
class BrainConnectionType:
    """What a client stores to mount a brain: its endpoints, the header to send
    and a token for this caller and this brain, valid until ``expires_at``."""

    id: GUID
    slug: str
    endpoints: list[str]
    header: str
    token: str
    expires_at: str


def _target(*args, **kwargs):
    slug = kwargs.get("slug")
    return ("RegisteredApp", slug) if slug else None


@strawberry.input(name="HostAppOnBrainInput")
class HostAppOnBrainInput:
    """An app from the brain-app template: ``image`` serving HTTP on ``port``,
    reading ``brain_slug``. A blank name or slug is generated."""

    brain_slug: str
    project_id: GUID
    image: str
    name: str | None = None
    slug: str | None = None
    port: int | None = None


@strawberry.type(name="AstroliftBrainApp")
class BrainAppType:
    """The new app and how its first deploy reads the brain: ``env_keys`` are
    set on every workload of ``environment_name`` when it deploys."""

    app: RegisteredAppType
    brain_slug: str
    template: str
    environment_name: str
    env_keys: list[str]


def _host_target(*args, **kwargs):
    data = kwargs.get("input")
    return ("RegisteredApp", data.brain_slug) if data is not None else None


def _host_extras(result) -> dict | None:
    data = getattr(result, "data", None)
    if data is None:
        return None
    return {"brain": data.brain_slug, "app": data.app.slug, "template": data.template}


@strawberry.type
class BrainMutations:
    @strawberry.field(
        description=(
            "A 15-minute read token for one brain the caller can read (#2320), with its endpoints "
            "and the header to send it in. The cluster edge admits it to that brain only; a revoked "
            "share stops new tokens at once. The token is never written to the audit log."
        )
    )
    @mutation_audit(action="brain.token.issue", target=_target)
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def issue_brain_read_token(self, info: Info, slug: str) -> MutationResultType[BrainConnectionType]:
        from astrolift_registry.brain_tokens import BrainTokenError, connection_descriptor
        from astrolift_registry.brains import visible_brains
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Not visible and not a brain read the same, so existence never leaks.
        brain = (
            visible_brains().filter(organization_id=org_id, slug=slug.strip()).first()
            if slug.strip() and org_id is not None
            else None
        )
        if brain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "brain not found", field="slug")
        user = _actor()
        if user is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "a brain token is issued to a person")
        try:
            row = connection_descriptor(user=user, brain=brain)
        except BrainTokenError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        return gql_success(
            BrainConnectionType(
                id=GUID(row["id"]),
                slug=row["slug"],
                endpoints=[env["url"] for env in row["environments"]],
                header=row["header"],
                token=row["token"],
                expires_at=row["expires_at"],
            )
        )

    @strawberry.field(
        description=(
            "Create an app hosted on a brain the caller can read (#2243), from the brain-app "
            "template: one public web workload running ``image``. Each deploy gives it the "
            "brain's MCP URLs and a read token for that brain as env (BRAIN_*), rotated on every "
            "deploy and revoked when the app is deleted. Deploy it with startDeployment."
        )
    )
    @mutation_audit(action="brain.app.create", target=_host_target, extras=_host_extras)
    @require_permission(
        Permission.APP_CREATE,
        scope=registration_project_scope("input.project_id", permission=Permission.APP_CREATE),
    )
    @tenant_scoped()
    def host_app_on_brain(self, info: Info, input: HostAppOnBrainInput) -> MutationResultType[BrainAppType]:
        from astrolift_graphql.results import MutationResultType as Result
        from astrolift_registry import brain_apps
        from astrolift_registry.brains import visible_brains
        from astrolift_registry.models import RegisteredApp
        from astrolift_registry.schema.mutations.registration import RegistrationMutations
        from astrolift_registry.schema.mutations.types import RegisterAppInput
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        slug = input.brain_slug.strip()
        brain = (
            visible_brains().filter(organization_id=org_id, slug=slug).first()
            if slug and org_id is not None
            else None
        )
        if brain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "brain not found", field="brainSlug")
        user = _actor()
        if user is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "an app is hosted on a brain by a person")
        if not brain_apps.mcp_urls(brain):
            return gql_failure(
                ErrorCode.PRECONDITION.value, f"brain {slug!r} is not served yet", field="brainSlug"
            )
        image = input.image.strip()
        if not image or any(ch.isspace() for ch in image):
            return gql_failure(ErrorCode.VALIDATION.value, "image must be an image reference", field="image")
        port = input.port if input.port is not None else brain_apps.DEFAULT_PORT
        if not 1 <= port <= 65535:
            return gql_failure(ErrorCode.VALIDATION.value, "port must be between 1 and 65535", field="port")
        app_slug = (input.slug or "").strip()
        if app_slug and RegisteredApp.objects.filter(organization_id=org_id, slug=app_slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"app with slug {app_slug!r} already exists in this organization",
                field="slug",
            )
        if not app_slug:
            from astrolift_identity.models import Organization
            from astrolift_registry.schema.mutations.helpers import _generate_unique_app_slug

            app_slug = _generate_unique_app_slug(Organization.objects.get(pk=org_id))
        # The undecorated resolver: this call already checked APP_CREATE on
        # the same project and is the one audit row (with the app in extras).
        import inspect

        registered = inspect.unwrap(RegistrationMutations.register_app)(
            self,
            info,
            input=RegisterAppInput(
                project_id=input.project_id,
                name=input.name,
                slug=app_slug,
                source_repo="",
                build_mode=RegisteredApp.BuildMode.NONE.value,
                manifest_raw=brain_apps.brain_app_manifest(
                    slug=app_slug, brain_slug=slug, image=image, port=port
                ),
            ),
        )
        if not registered.ok or registered.data is None:
            return Result(ok=False, errors=registered.errors, data=None)
        app = RegisteredApp.objects.get(organization_id=org_id, slug=registered.data.slug)
        brain_apps.bind(app=app, brain=brain, granted_by=user)
        return gql_success(
            BrainAppType(
                app=registered.data,
                brain_slug=slug,
                template=brain_apps.TEMPLATE_KEY,
                environment_name="production",
                env_keys=list(brain_apps.ENV_KEYS),
            )
        )


# ---------------------------------------------------------------------------
# Managing semantic layers (#2424)
# ---------------------------------------------------------------------------

_SLUG = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_ENV = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")


@strawberry.input(name="CreateBrainInput")
class CreateBrainInput:
    """A new semantic layer: ``repo`` (missing, or empty) is scaffolded from
    the Mimwell edition at ``templateRef`` (default: the install's pinned
    edition), registered as ``slug`` in the project and, unless ``deploy`` is
    false, deployed to ``environmentName``."""

    project_id: GUID
    slug: str
    source_connection_id: GUID
    repo: str
    name: str | None = None
    team_id: GUID | None = None
    visibility: RepoVisibility = RepoVisibility.PRIVATE
    template_ref: str | None = None
    environment_name: str = "production"
    deploy: bool = True
    substrate: BrainSubstrateInput | None = strawberry.field(
        default=None,
        description=(
            "The Mimwell cell and persistence to host it on, checked against the matrix; the files "
            "cell with no persistence when omitted. The slug must be the client slug plus -brain."
        ),
    )


@strawberry.input(name="UpdateBrainSubstrateInput")
class UpdateBrainSubstrateInput:
    """Graduate a brain: the full selection it should have from now on. A
    null image keeps the current one."""

    slug: str
    substrate: BrainSubstrateInput


@strawberry.input(name="ShareBrainInput")
class ShareBrainInput:
    slug: str
    team_slug: str
    access: BrainShareAccess


@strawberry.input(name="UnshareBrainInput")
class UnshareBrainInput:
    slug: str
    team_slug: str


@strawberry.input(name="RebuildBrainInput")
class RebuildBrainInput:
    slug: str
    environment_name: str = "production"
    ingest: bool = False


@strawberry.input(name="ArchiveBrainInput")
class ArchiveBrainInput:
    slug: str


def _brain_target(*args, **kwargs):
    data = kwargs.get("input")
    return ("RegisteredApp", data.slug) if data is not None else None


def _create_extras(result) -> dict:
    from astrolift_registry.brain_manage import audit_context

    data = getattr(result, "data", None)
    out = audit_context()
    if data is not None:
        out.update(repo=data.repo, template_ref=data.template_ref, creation=str(data.id))
    return out


def _also_requires(permission: Permission, scope_factory, args: dict, why: str):
    """A second permission the effect needs beyond the resolver's gate, with
    the same token ceiling; the failure envelope when the caller lacks it."""
    from core.permissions import PermissionDenied, check_permission

    try:
        check_permission(permission, scope=scope_factory(args))
    except PermissionDenied:
        return gql_failure(ErrorCode.PERMISSION_DENIED.value, why)
    return None


def _team_in_org(org_id: int, team_slug: str):
    from astrolift_identity.models import Team

    slug = (team_slug or "").strip()
    return (
        Team.objects.filter(organization_id=org_id, slug=slug, deleted_at__isnull=True).first()
        if slug
        else None
    )


@strawberry.type
class BrainManagementMutations:
    @strawberry.field(
        description=(
            "Create a semantic layer (#2424): check the request, then scaffold the repository from "
            "the verified Mimwell edition in one commit, register it as a brain and deploy it, in "
            "the background. Poll astroliftBrainCreation; a retry for the same slug joins the running one."
        )
    )
    @mutation_audit(action="brain.create", target=_brain_target, extras=_create_extras)
    @require_permission(
        Permission.APP_CREATE,
        scope=registration_project_scope("input.project_id", permission=Permission.APP_CREATE),
    )
    @tenant_scoped()
    def create_brain(self, info: Info, input: CreateBrainInput) -> MutationResultType[BrainCreationType]:
        from astrolift_identity.models import Organization, Project, Team
        from astrolift_registry import brain_manage, brain_template
        from astrolift_registry.models import BrainCreation, RegisteredApp
        from astrolift_scm.providers import ProviderError
        from astrolift_scm.providers.repo_writes import repository_state
        from astrolift_workflows.client import start_workflow
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        user = _actor()
        if org_id is None or user is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "a semantic layer is created by a person")
        project = (
            Project.objects.select_related("team")
            .filter(guid=str(input.project_id), organization_id=org_id, deleted_at__isnull=True)
            .first()
        )
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found", field="projectId")
        if input.team_id is not None:
            team = Team.objects.filter(
                guid=str(input.team_id), organization_id=org_id, deleted_at__isnull=True
            ).first()
            if team is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamId")
            if team.pk != project.team_id:
                return gql_failure(
                    ErrorCode.VALIDATION.value, "a brain belongs to its project's team", field="teamId"
                )
        slug = (input.slug or "").strip()
        if not _SLUG.match(slug):
            return gql_failure(
                ErrorCode.VALIDATION.value, "slug must be lowercase letters, digits and dashes", field="slug"
            )
        repo = (input.repo or "").strip()
        if not _REPO.match(repo) or ".." in repo:
            return gql_failure(ErrorCode.VALIDATION.value, "repo must be owner/name", field="repo")
        environment = (input.environment_name or "production").strip()
        if not _ENV.match(environment):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "environmentName must be a short lowercase name",
                field="environmentName",
            )
        from astrolift_registry import brain_substrate

        try:
            brain_substrate.client_slug(slug)
            selection = brain_substrate.validate(selection_from_input(input.substrate))
        except brain_substrate.SubstrateError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, exc.message, field=exc.field or "substrate")
        name = (input.name or "").strip()[:255] or slug
        running = BrainCreation.objects.filter(
            organization_id=org_id, slug=slug, state__in=BrainCreation.IN_FLIGHT
        ).first()
        if running is not None:
            if running.created_by_id == user.pk and running.repo.lower() == repo.lower():
                return gql_success(creation_to_type(running))
            return gql_failure(
                ErrorCode.CONFLICT.value, f"a brain {slug!r} is already being created", field="slug"
            )
        if RegisteredApp.objects.filter(organization_id=org_id, slug=slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"app with slug {slug!r} already exists in this organization",
                field="slug",
            )
        connection, why = brain_manage.usable_connection(
            str(input.source_connection_id), organization_id=org_id, user_id=user.pk
        )
        if connection is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, why, field="sourceConnectionId")
        host = brain_manage.source_kind(connection)
        visibility = input.visibility.value
        if visibility == "internal" and host not in ("github", "gitlab"):
            return gql_failure(
                ErrorCode.VALIDATION.value, f"{host} has no internal visibility", field="visibility"
            )
        try:
            ref = brain_template.validate_ref(input.template_ref or brain_template.default_ref())
            brain_template.check_ref(ref)
        except brain_template.TemplateError as exc:
            return gql_failure(exc.code, exc.message, field="templateRef")
        try:
            state = repository_state(connection, repo)
        except ProviderError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message, field="repo")
        if state.exists and not state.empty:
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"{repo} already has commits; use an empty or new repository",
                field="repo",
            )
        if not state.exists and connection.kind == "github_app_install":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"{repo} does not exist and the GitHub App cannot create repositories; create the empty "
                "repository first, or use a user OAuth or PAT connection",
                field="repo",
            )
        if input.deploy:
            from astrolift_identity.action_safety import gate

            # The first deploy is started by the workflow, not startDeployment,
            # so its permission is checked here: app.create alone never deploys.
            deny = _also_requires(
                Permission.APP_DEPLOY,
                registration_project_scope("project_id", permission=Permission.APP_DEPLOY),
                {"project_id": input.project_id},
                "deploying needs app.deploy on the project; create it with deploy false",
            )
            if deny is not None:
                return deny
            from astrolift_lifecycle.schema.types import environment_kind

            if environment_kind(environment) == "production":
                deny = gate(info, "deploy.production", resolver_name="create_brain")
                if deny is not None:
                    return deny
        organization = Organization.objects.get(pk=org_id)
        row = BrainCreation.objects.create(
            organization_id=org_id,
            project=project,
            team_id=project.team_id,
            created_by=user,
            source_connection=connection,
            slug=slug,
            name=name,
            repo=repo,
            visibility=visibility,
            template_ref=ref,
            environment_name=environment,
            deploy=bool(input.deploy),
            substrate=selection.as_dict(),
            workflow_id=brain_manage.workflow_id(str(organization.guid), slug),
        )
        handle = start_workflow(
            "BrainCreateWorkflow", args=[row.pk], workflow_id=row.workflow_id, replace_running=False
        )
        if not handle.enqueued:
            brain_manage.fail(
                row.pk, ErrorCode.PRECONDITION.value, "the workflow engine is off on this install"
            )
            return gql_failure(ErrorCode.PRECONDITION.value, "the workflow engine is off on this install")
        return gql_success(creation_to_type(row))

    @strawberry.field(
        description=(
            "Share a semantic layer with a team (#2424): VIEWER reads it, DEPLOYER also edits and "
            "deploys it. Owners are managed on the app, never here. Asks for confirmation and a fresh "
            "verification in the browser (brain.share)."
        )
    )
    @mutation_audit(
        action="brain.share",
        target=_brain_target,
        extras=lambda result: _share_extras(result),
    )
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.slug", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def share_brain(self, info: Info, input: ShareBrainInput) -> MutationResultType[BrainType]:
        from astrolift_identity.action_safety import gate
        from astrolift_registry.brain_manage import find_brain
        from astrolift_registry.models import AppTeamAccess
        from astrolift_registry.schema.mutations.team_access import TeamAccessMutations
        from astrolift_registry.schema.mutations.types import GrantTeamAccessInput

        brain = find_brain(input.slug)
        if brain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "brain not found", field="slug")
        team = _team_in_org(brain.organization_id, input.team_slug)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamSlug")
        if team.pk == brain.team_id:
            return gql_failure(
                ErrorCode.VALIDATION.value, "the brain's home team already owns it", field="teamSlug"
            )
        current = AppTeamAccess.objects.filter(
            registered_app=brain, team=team, deleted_at__isnull=True
        ).first()
        if current is not None and current.access_level == AppTeamAccess.AccessLevel.OWNER.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"team {team.slug!r} owns this brain; change owners with astro app access",
                field="teamSlug",
            )
        if input.access == BrainShareAccess.DEPLOYER:
            # Never grant more than the granter holds: deployer deploys.
            deny = _also_requires(
                Permission.APP_DEPLOY,
                app_scope_by_slug("slug", permission=Permission.APP_DEPLOY),
                {"slug": brain.slug},
                "sharing as deployer needs app.deploy on the brain",
            )
            if deny is not None:
                return deny
        deny = gate(info, "brain.share", app=brain, resolver_name="share_brain")
        if deny is not None:
            return deny
        granted = inspect.unwrap(TeamAccessMutations.grant_team_access_to_app)(
            self,
            info,
            input=GrantTeamAccessInput(
                app_id=GUID(str(brain.guid)), team_id=GUID(str(team.guid)), access_level=input.access.value
            ),
        )
        if not granted.ok:
            from astrolift_graphql.results import MutationResultType as Result

            return Result(ok=False, errors=granted.errors, data=None)
        return gql_success(brain_to_type(brain))

    @strawberry.field(
        description=(
            "Stop sharing a semantic layer with a team (#2424). New read tokens stop at once; tokens "
            "already issued live out their 15 minutes. Never removes the home team or an owner."
        )
    )
    @mutation_audit(action="brain.unshare", target=_brain_target, extras=lambda result: _share_extras(result))
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.slug", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def unshare_brain(self, info: Info, input: UnshareBrainInput) -> MutationResultType[BrainType]:
        from astrolift_registry.brain_manage import find_brain
        from astrolift_registry.models import AppTeamAccess
        from astrolift_registry.schema.mutations.team_access import TeamAccessMutations
        from astrolift_registry.schema.mutations.types import RevokeTeamAccessInput

        brain = find_brain(input.slug)
        if brain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "brain not found", field="slug")
        team = _team_in_org(brain.organization_id, input.team_slug)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamSlug")
        if team.pk == brain.team_id:
            return gql_failure(
                ErrorCode.VALIDATION.value, "the brain's home team cannot be removed", field="teamSlug"
            )
        current = AppTeamAccess.objects.filter(
            registered_app=brain, team=team, deleted_at__isnull=True
        ).first()
        if current is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "the brain is not shared with this team", field="teamSlug"
            )
        if current.access_level == AppTeamAccess.AccessLevel.OWNER.value:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"team {team.slug!r} owns this brain; change owners with astro app access",
                field="teamSlug",
            )
        revoked = inspect.unwrap(TeamAccessMutations.revoke_team_access_from_app)(
            self,
            info,
            input=RevokeTeamAccessInput(app_id=GUID(str(brain.guid)), team_id=GUID(str(team.guid))),
        )
        if not revoked.ok:
            from astrolift_graphql.results import MutationResultType as Result

            return Result(ok=False, errors=revoked.errors, data=None)
        return gql_success(brain_to_type(brain))

    @strawberry.field(
        description=(
            "Rebuild a semantic layer (#2424): redeploy the deploy branch head (a platform build starts a "
            "deployment, a CI-built brain dispatches its CI), or with ingest run the brain's "
            "knowledge-refresh workflow, whose commit then deploys through the normal push path."
        )
    )
    @mutation_audit(
        action="brain.rebuild",
        target=_brain_target,
        extras=lambda result: {
            **_audit_base(),
            **({"mode": result.data.mode} if getattr(result, "data", None) is not None else {}),
        },
    )
    @require_permission(
        Permission.APP_DEPLOY,
        scope=app_scope_by_slug("input.slug", permission=Permission.APP_DEPLOY),
        operation=named_environment("input.slug"),
    )
    @tenant_scoped()
    def rebuild_brain(self, info: Info, input: RebuildBrainInput) -> MutationResultType[BrainRebuildType]:
        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_registry import brain_manage
        from astrolift_registry.models import RegisteredApp
        from astrolift_scm.services.workflows import WorkflowDispatchError, dispatch_astrolift_ci_workflow

        brain = brain_manage.find_brain(input.slug)
        if brain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "brain not found", field="slug")
        environment = (input.environment_name or "production").strip()
        if not AppEnvironment.objects.filter(
            registered_app=brain, name=environment, deleted_at__isnull=True
        ).exists():
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"the brain has no {environment!r} environment",
                field="environmentName",
            )
        if input.ingest or brain.build_mode == RegisteredApp.BuildMode.CI_PUSHED:
            if not brain.source_repo:
                return gql_failure(ErrorCode.PRECONDITION.value, "the brain has no source repository")
            if not input.ingest:
                # A CI-built brain deploys from its CI run: the decorated
                # dispatch resolver keeps its approval refusal and checks
                # app.deploy on every environment the run can reach.
                from astrolift_graphql.results import MutationResultType as Result
                from astrolift_lifecycle.schema.mutations.app_ops import AppOpsMutations
                from astrolift_lifecycle.schema.mutations.deployments import _production_gate
                from astrolift_lifecycle.schema.mutations.types import TriggerDeployWorkflowInput

                env = AppEnvironment.objects.filter(
                    registered_app=brain, name=environment, deleted_at__isnull=True
                ).first()
                deny = _production_gate(info, "deploy.production", brain, env, "rebuild_brain")
                if deny is not None:
                    return deny
                fired = AppOpsMutations().trigger_astrolift_deploy_workflow(
                    info, input=TriggerDeployWorkflowInput(app_slug=brain.slug)
                )
                if not fired.ok or fired.data is None:
                    return Result(ok=False, errors=list(fired.errors or []), data=None)
                return gql_success(
                    BrainRebuildType(
                        slug=brain.slug,
                        mode="ci",
                        deployment_id=None,
                        run_url=fired.data.run_url or None,
                        ingest_run_url=None,
                        rebuilds=brain_manage.rebuild_projections(brain),
                    )
                )
            present, why = brain_manage.ingest_workflow_present(brain)
            if not present:
                return gql_failure(ErrorCode.PRECONDITION.value, why, field="ingest")
            try:
                result = dispatch_astrolift_ci_workflow(brain, workflow_path=brain_manage.INGEST_WORKFLOW)
            except WorkflowDispatchError as exc:
                return gql_failure(ErrorCode.PRECONDITION.value, exc.message)
            if not result.ok:
                return gql_failure(ErrorCode.PRECONDITION.value, result.error_message or "dispatch failed")
            return gql_success(
                BrainRebuildType(
                    slug=brain.slug,
                    mode="ingest",
                    deployment_id=None,
                    run_url=result.run_url or None,
                    ingest_run_url=result.run_url or None,
                    rebuilds=brain_manage.rebuild_projections(brain),
                )
            )
        if brain.build_mode not in (RegisteredApp.BuildMode.PLATFORM_BUILD, RegisteredApp.BuildMode.NONE):
            return gql_failure(
                ErrorCode.PRECONDITION.value, "this brain's build mode cannot be redeployed here"
            )
        from astrolift_lifecycle.schema.mutations.deployments import DeploymentMutations
        from astrolift_lifecycle.schema.mutations.types import StartDeploymentInput

        # The decorated resolver: deploying is its own audited, gated action
        # (deploy.production applies), and its environment policy runs.
        started = DeploymentMutations().start_deployment(
            info,
            input=StartDeploymentInput(
                app_slug=brain.slug,
                environment_name=environment,
                branch=brain.deploy_branch or brain.default_branch or "main",
            ),
        )
        if not started.ok or started.data is None:
            from astrolift_graphql.results import MutationResultType as Result

            return Result(ok=False, errors=list(started.errors or []), data=None)
        return gql_success(
            BrainRebuildType(
                slug=brain.slug,
                mode="deploy",
                deployment_id=GUID(str(started.data.id)),
                run_url=None,
                ingest_run_url=None,
                rebuilds=brain_manage.rebuild_projections(brain),
            )
        )

    @strawberry.field(
        description=(
            "Graduate a semantic layer's substrate (#2424, Mimwell's graduation): check the new cell "
            "against the matrix, write it into client.config.json and astrolift.toml in one commit and "
            "apply the manifest, which provisions what the cell needs; the push deploys it. Persistence "
            "that holds data (postgres, sqlite-volume, efs) is never dropped here."
        )
    )
    @mutation_audit(
        action="brain.substrate.update",
        target=_brain_target,
        extras=lambda result: {**_audit_base(), **_substrate_extras(result)},
    )
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_slug("input.slug", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def update_brain_substrate(
        self, info: Info, input: UpdateBrainSubstrateInput
    ) -> MutationResultType[BrainType]:
        from astrolift_registry import brain_manage

        brain = brain_manage.find_brain(input.slug)
        if brain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "brain not found", field="slug")
        user = _actor()
        if user is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "a substrate is changed by a person")
        # The commit lands on the deploy branch and its push deploys.
        deny = _also_requires(
            Permission.APP_DEPLOY,
            app_scope_by_slug("slug", permission=Permission.APP_DEPLOY),
            {"slug": brain.slug},
            "changing the substrate deploys the brain; it needs app.deploy",
        )
        if deny is not None:
            return deny
        keep = brain_manage.current_selection(brain).image
        try:
            brain_manage.update_substrate(
                brain, selection_from_input(input.substrate, keep_image=keep), user=user
            )
        except brain_manage.SubstrateChangeError as exc:
            return gql_failure(exc.code, exc.message, field=exc.field)
        brain.refresh_from_db()
        return gql_success(brain_to_type(brain, hosted=True))

    @strawberry.field(
        description=(
            "Delete a semantic layer (#2424): archive it (scale to zero, stop deploys), drop it from "
            "the register and revoke every hosted app's binding and token. The repository is never "
            "touched; restoreApp brings the brain back, hosted apps are re-hosted. Asks for "
            "confirmation, a fresh verification and any approval rule (brain.delete)."
        )
    )
    @mutation_audit(action="brain.delete", target=_brain_target, extras=lambda result: _audit_base())
    @require_permission(
        Permission.APP_DELETE, scope=app_scope_by_slug("input.slug", permission=Permission.APP_DELETE)
    )
    @tenant_scoped()
    def archive_brain(self, info: Info, input: ArchiveBrainInput) -> MutationResultType[BrainType]:
        from astrolift_identity.action_safety import gate
        from astrolift_registry import brain_apps
        from astrolift_registry.brain_manage import find_brain
        from astrolift_registry.schema.mutations.archive import ArchiveMutations
        from astrolift_registry.schema.mutations.types import ArchiveAppInput

        brain = find_brain(input.slug)
        if brain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "brain not found", field="slug")
        deny = gate(info, "brain.delete", app=brain, target=brain, resolver_name="archive_brain")
        if deny is not None:
            return deny
        archived = inspect.unwrap(ArchiveMutations.archive_app)(
            self, info, input=ArchiveAppInput(app_slug=brain.slug)
        )
        if not archived.ok:
            from astrolift_graphql.results import MutationResultType as Result

            return Result(ok=False, errors=archived.errors, data=None)
        brain_apps.revoke_app(brain.pk)
        brain.refresh_from_db()
        return gql_success(brain_to_type(brain))


def _audit_base() -> dict:
    from astrolift_registry.brain_manage import audit_context

    return audit_context()


def _substrate_extras(result) -> dict:
    data = getattr(result, "data", None)
    substrate = getattr(data, "substrate", None) if data is not None else None
    if substrate is None:
        return {}
    return {
        "cell": substrate.cell,
        "persistence": list(substrate.persistence),
        "replicas": substrate.replicas,
    }


def _share_extras(result) -> dict:
    out = _audit_base()
    data = getattr(result, "data", None)
    if data is not None:
        out["shared_with"] = [{"team": s.team, "access": s.access} for s in data.shared_with]
    return out
