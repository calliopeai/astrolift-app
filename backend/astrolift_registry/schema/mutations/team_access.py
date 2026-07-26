"""TeamAccessMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Team
from astrolift_registry.models import AppTeamAccess, RegisteredApp
from astrolift_registry.schema.mutations.helpers import (
    _actor,
    _downgrade_to_deployer,
    _ensure_owner_access,
)
from astrolift_registry.schema.mutations.types import (
    GrantTeamAccessInput,
    MoveAppToTeamInput,
    RevokeTeamAccessInput,
    _SoftDeletePayload,
)
from astrolift_registry.schema.types import (
    AppTeamAccessType,
    RegisteredAppType,
    app_team_access_to_type,
    app_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class TeamAccessMutations:
    @strawberry.field
    @mutation_audit(action="app.move_to_team")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def move_app_to_team(
        self, info: Info, input: MoveAppToTeamInput
    ) -> MutationResultType[RegisteredAppType]:
        """Move the app's primary / home team to ``targetTeamId``.

        Distinct from ``transferApp`` (which re-parents to a target
        team *and* project): this mutation focuses on the team
        membership semantics. It re-points ``RegisteredApp.team`` to
        the target team and keeps the ``AppTeamAccess`` join table
        in sync — the target team gains an OWNER row (idempotent) and
        the previous home team is downgraded to DEPLOYER (or
        materialized at DEPLOYER if no row existed) rather than
        revoked. Downgrade-only is the safer default; the previous
        team's existing humans don't lose access mid-flight, and the
        operator can ``revokeTeamAccessFromApp`` explicitly later.
        """

        # Org-scope the SOURCE app to the caller's tenant — the target team
        # below is validated against ``app.organization_id``, so scoping the
        # source binds the whole move to the caller's org. Fails closed
        # (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.select_related("organization", "team", "project")
            .filter(guid=str(input.app_id), organization_id=org_id, deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        target = (
            Team.objects.select_related("organization")
            .filter(guid=str(input.target_team_id), deleted_at__isnull=True)
            .first()
        )
        if target is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "target team not found",
                field="targetTeamId",
            )
        if target.organization_id != app.organization_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cross-organization move is not permitted",
                field="targetTeamId",
            )

        # The existing project must belong to the new team or the tree
        # breaks. ``transferApp`` allows callers to specify a new
        # project alongside; ``moveAppToTeam`` keeps the API narrow
        # (team-only) and refuses orphaning the project.
        if app.project.team_id != target.id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "the app's project belongs to a different team — use transferApp to move both",
                field="targetTeamId",
            )

        previous_team_id = app.team_id
        if previous_team_id == target.id:
            # No-op when already on the target team — but still ensure
            # an OWNER row exists for it.
            _ensure_owner_access(app, target.id, actor=_actor())
            return gql_success(app_to_type(app))

        app.team = target
        app.save(update_fields=["team", "updated_at", "version"])

        _ensure_owner_access(app, target.id, actor=_actor())
        _downgrade_to_deployer(app, previous_team_id, actor=_actor())

        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.grant_team_access")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def grant_team_access_to_app(
        self, info: Info, input: GrantTeamAccessInput
    ) -> MutationResultType[AppTeamAccessType]:
        """Upsert a team's access to an app at the requested level.

        Re-granting at the same level is idempotent (no row change);
        changing the level updates the existing row in place. The
        unique constraint over ``(app, team) WHERE deleted_at IS
        NULL`` prevents duplicate active grants.
        """

        normalized_level = (input.access_level or "").strip().lower()
        valid_levels = {choice.value for choice in AppTeamAccess.AccessLevel}
        if normalized_level not in valid_levels:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"accessLevel must be one of {sorted(valid_levels)}",
                field="accessLevel",
            )

        # Org-scope the SOURCE app to the caller's tenant — the target team
        # below is validated against ``app.organization_id``, so scoping the
        # source binds the grant to the caller's org. Fails closed
        # (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.select_related("organization", "team")
            .filter(guid=str(input.app_id), organization_id=org_id, deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        team = (
            Team.objects.select_related("organization")
            .filter(guid=str(input.team_id), deleted_at__isnull=True)
            .first()
        )
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamId")

        if team.organization_id != app.organization_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "team must belong to the same organization as the app",
                field="teamId",
            )

        access = (
            AppTeamAccess.objects.select_related("registered_app", "team")
            .filter(registered_app=app, team=team, deleted_at__isnull=True)
            .first()
        )
        if access is None:
            access = AppTeamAccess.objects.create(
                registered_app=app,
                team=team,
                access_level=normalized_level,
            )
        elif access.access_level != normalized_level:
            access.access_level = normalized_level
            access.save(update_fields=["access_level", "updated_at", "version"])

        # Refresh so app + team relations are populated for the
        # type-conversion path.
        access = AppTeamAccess.objects.select_related("registered_app", "team").filter(pk=access.pk).first()
        return gql_success(app_team_access_to_type(access, home_team_id=app.team_id))

    @strawberry.field
    @mutation_audit(action="app.revoke_team_access")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def revoke_team_access_from_app(
        self, info: Info, input: RevokeTeamAccessInput
    ) -> MutationResultType[_SoftDeletePayload]:
        """Soft-delete the team's access grant to the app.

        Refuses when the grant being revoked is the last active OWNER
        row — that would orphan the app. Operators must promote
        another team to OWNER or move the app to a different home
        team first.
        """

        # Org-scope the SOURCE app to the caller's tenant — the target team
        # below is matched against ``app``, so scoping the source binds the
        # revoke to the caller's org. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = (
            RegisteredApp.objects.select_related("organization", "team")
            .filter(guid=str(input.app_id), organization_id=org_id, deleted_at__isnull=True)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        team = (
            Team.objects.select_related("organization")
            .filter(guid=str(input.team_id), deleted_at__isnull=True)
            .first()
        )
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamId")

        access = AppTeamAccess.objects.filter(registered_app=app, team=team, deleted_at__isnull=True).first()
        if access is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "team has no active access grant on this app",
                field="teamId",
            )

        if access.access_level == AppTeamAccess.AccessLevel.OWNER.value:
            remaining_owners = (
                AppTeamAccess.objects.filter(
                    registered_app=app,
                    access_level=AppTeamAccess.AccessLevel.OWNER.value,
                    deleted_at__isnull=True,
                )
                .exclude(pk=access.pk)
                .count()
            )
            if remaining_owners == 0:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "cannot revoke the last OWNER grant — promote another team first or move the app",
                    field="teamId",
                )

        access.soft_delete(by=_actor())
        return gql_success(
            _SoftDeletePayload(id=GUID(str(access.guid)), deleted=True),
        )
