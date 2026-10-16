"""Brain read tokens (#2320): connect a client to a brain the caller can read.
Apps hosted on a brain (#2243): create one from a brain in one call."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_registry.schema.mutations.helpers import _actor
from astrolift_registry.schema.types import RegisteredAppType
from astrolift_registry.scopes import registration_project_scope
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
