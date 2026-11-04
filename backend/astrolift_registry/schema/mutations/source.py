"""Point an app at another GitHub repository (#2478): ``setAppSource``.

The checks and the change live in ``astrolift_scm.services.source_change``,
which the App webhook receiver also runs for a repository renamed on GitHub;
the wiring that follows the repository is autowire's.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import RegisteredAppType, app_to_type, build_autowire_status
from astrolift_registry.scopes import app_scope_by_guid
from astrolift_scm.services.source_change import TORN_DOWN, SourceChangeRefused, change_source
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.optimistic import check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.input(name="SetAppSourceInput")
class SetAppSourceInput:
    id: GUID
    source_repo: str = strawberry.field(description="The GitHub repository, as owner/name.")
    if_match_version: int | None = None


@strawberry.type(name="AstroliftAppSourceChange")
class AppSourceChangeType:
    app: RegisteredAppType
    previous_source_repo: str = strawberry.field(
        description="The repository the app pointed at before the call; app.sourceRepo when nothing changed."
    )


def _source_extras(result) -> dict | None:
    data = getattr(result, "data", None)
    if data is None:
        return None
    return {"previous_source_repo": data.previous_source_repo, "source_repo": data.app.source_repo}


@strawberry.type
class SourceMutations:
    @strawberry.field(
        description=(
            "Point a GitHub app at another repository, or at its repository's new name after a "
            "rename on GitHub (#2478). The repository must be in the organization's GitHub App "
            "installation, and the name GitHub lists is stored; sourceUrl follows when it named the "
            "old repository. Autowire then runs against the repository: the CI workflow and the CI "
            "push role follow it, and the CI secrets are pushed to it, which rotates the deploy "
            "token. Audited as app.set_source."
        )
    )
    @mutation_audit(
        action="app.set_source",
        target=lambda self, info, input: ("RegisteredApp", str(input.id)),
        extras=_source_extras,
    )
    @require_permission(
        Permission.APP_UPDATE, scope=app_scope_by_guid("input.id", permission=Permission.APP_UPDATE)
    )
    @tenant_scoped()
    def set_app_source(self, info: Info, input: SetAppSourceInput) -> MutationResultType[AppSourceChangeType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.select_related("organization")
            .filter(guid=str(input.id), organization_id=org_id, deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        mismatch = check_version_match(app, if_match_version=input.if_match_version, kind="App")
        if mismatch is not None:
            return mismatch
        if app.provisioning_status in TORN_DOWN:
            return gql_failure(ErrorCode.PRECONDITION.value, "the app is being torn down")
        if app.source_kind != RegisteredApp.SourceKind.GITHUB.value or not app.source_repo:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "only an app registered from a GitHub repository can change its repository",
                field="sourceRepo",
            )

        previous = app.source_repo
        request = getattr(info.context, "request", None)
        try:
            change_source(app, input.source_repo, actor=getattr(request, "user", None) if request else None)
        except SourceChangeRefused as exc:
            return gql_failure(exc.code, exc.message, field="sourceRepo")

        return gql_success(
            AppSourceChangeType(
                app=app_to_type(app, info=info, autowire=build_autowire_status(app)),
                previous_source_repo=previous,
            )
        )
