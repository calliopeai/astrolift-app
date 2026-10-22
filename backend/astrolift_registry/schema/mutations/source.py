"""Point an app at another GitHub repository (#2478): ``setAppSource``.

The checks live in ``astrolift_scm.services.source_change``; the wiring that
follows the repository is autowire's.
"""

from __future__ import annotations

import logging
import urllib.parse

import strawberry
from django.db import IntegrityError, transaction
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import RegisteredAppType, app_to_type, build_autowire_status
from astrolift_registry.scopes import app_scope_by_guid
from astrolift_scm.org_repo_policy import rejection_reason
from astrolift_scm.services.autowire import run_autowire
from astrolift_scm.services.source_change import (
    SourceChangeRefused,
    installed_repository,
    parse_github_repo,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.optimistic import check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

logger = logging.getLogger(__name__)

_TORN_DOWN = frozenset(
    {
        RegisteredApp.ProvisioningStatus.TEARING_DOWN.value,
        RegisteredApp.ProvisioningStatus.DEREGISTERED.value,
    }
)
_ALREADY_REGISTERED = "another app in this organization is registered from this repository and manifest path"


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


def _followed_source_url(source_url: str, previous_repo: str, repo: str) -> str:
    """``source_url`` re-pointed at ``repo`` when it named ``previous_repo``;
    any other URL, or none, is left as the operator set it."""
    parts = urllib.parse.urlsplit(source_url or "")
    path = parts.path.strip("/").removesuffix(".git")
    if not parts.netloc or path.casefold() != previous_repo.casefold():
        return source_url
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"/{repo}", "", ""))


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
        # Wiring a repository for an app on its way out would put back the CI
        # files teardown removes (#2480).
        if app.provisioning_status in _TORN_DOWN:
            return gql_failure(ErrorCode.PRECONDITION.value, "the app is being torn down")
        if app.source_kind != RegisteredApp.SourceKind.GITHUB.value or not app.source_repo:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "only an app registered from a GitHub repository can change its repository",
                field="sourceRepo",
            )

        previous = app.source_repo
        try:
            repo = parse_github_repo(input.source_repo)
            if repo != previous:
                refusal = rejection_reason(repo, app.organization)
                if refusal:
                    return gql_failure(ErrorCode.VALIDATION.value, refusal, field="sourceRepo")
                # GitHub names ignore case; the other app may belong to a team
                # the caller cannot see, so it is not named.
                if (
                    RegisteredApp.objects.filter(
                        organization_id=app.organization_id,
                        source_repo__iexact=repo,
                        manifest_path=app.manifest_path,
                        deleted_at__isnull=True,
                    )
                    .exclude(pk=app.pk)
                    .exists()
                ):
                    return gql_failure(ErrorCode.CONFLICT.value, _ALREADY_REGISTERED, field="sourceRepo")
                repo = installed_repository(app.organization_id, repo)
        except SourceChangeRefused as exc:
            return gql_failure(exc.code, exc.message, field="sourceRepo")

        if repo != previous:
            app.source_repo = repo
            app.source_url = _followed_source_url(app.source_url, previous, repo)
            # The verdicts were about the old repository's wiring.
            app.autowire_state = {}
            try:
                with transaction.atomic():
                    app.save(
                        update_fields=["source_repo", "source_url", "autowire_state", "updated_at", "version"]
                    )
            except IntegrityError:
                return gql_failure(ErrorCode.CONFLICT.value, _ALREADY_REGISTERED, field="sourceRepo")
            request = getattr(info.context, "request", None)
            try:
                run_autowire(app, actor=getattr(request, "user", None) if request else None)
            except Exception:  # noqa: BLE001 - the change stands; the autowire status says what is unwired
                logger.exception("set_app_source: autowire failed for %s", app.slug)

        return gql_success(
            AppSourceChangeType(
                app=app_to_type(app, info=info, autowire=build_autowire_status(app)),
                previous_source_repo=previous,
            )
        )
