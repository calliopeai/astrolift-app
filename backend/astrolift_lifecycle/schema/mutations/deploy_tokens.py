"""DeployTokenMutations — split from the monolithic mutations module."""

from __future__ import annotations

from datetime import UTC

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.models import (
    DeployToken,
)
from astrolift_lifecycle.schema.mutations.types import (
    CreateDeployTokenInput,
    DeployTokenSecretReveal,
    RevokeDeployTokenInput,
    RotateDeployTokenInput,
    _DeployTokenRevokedPayload,
)
from astrolift_lifecycle.schema.types import (
    deploy_token_to_type,
)
from astrolift_registry.models import RegisteredApp
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class DeployTokenMutations:
    # ---- Deploy tokens (#281) ------------------------------------

    @strawberry.field
    @mutation_audit(action="app.deploy_token.create")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def create_deploy_token(
        self,
        info: Info,
        input: CreateDeployTokenInput,
    ) -> MutationResultType[DeployTokenSecretReveal]:
        import hashlib
        import secrets as secrets_lib
        from datetime import datetime, timedelta

        from astrolift_lifecycle.deploy_tokens import PLAINTEXT_PREFIX

        # Org-scope the app lookup to the caller's tenant BEFORE minting the
        # deploy token — an unscoped slug lookup would let a caller mint a
        # working secret against a sibling org's app. Slugs are unique only
        # within an org. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        # #449: mint with the canonical ``alft_dt_`` prefix so
        # ``verify_token`` + ``DeployTokenAuthMiddleware`` accept the
        # round-trip. Previously hard-coded ``alfdt_`` mismatched the
        # verifier and silently broke CI runners.
        plaintext = PLAINTEXT_PREFIX + secrets_lib.token_urlsafe(32)
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        expires_at = None
        if input.expires_at_iso:
            try:
                expires_at = datetime.fromisoformat(
                    input.expires_at_iso,
                )
            except ValueError:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "expires_at_iso must be ISO-8601",
                    field="expiresAtIso",
                )
        else:
            expires_at = datetime.now(tz=UTC) + timedelta(days=365)
        token = DeployToken.objects.create(
            registered_app=app,
            name=input.name,
            token_hash=digest,
            token_last_4=plaintext[-4:],
            scopes=list(input.scopes or ["app.deploy"]),
            expires_at=expires_at,
        )
        return gql_success(
            DeployTokenSecretReveal(
                token=deploy_token_to_type(token),
                plaintext_secret=plaintext,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.deploy_token.rotate")
    @requires_elevation(action_label="app.deploy_token.rotate")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def rotate_deploy_token(
        self,
        info: Info,
        input: RotateDeployTokenInput,
    ) -> MutationResultType[DeployTokenSecretReveal]:
        import hashlib
        import secrets as secrets_lib
        from datetime import datetime, timedelta

        from astrolift_lifecycle.deploy_tokens import (
            PLAINTEXT_PREFIX,
            rotation_grace_seconds_from_constance,
        )

        # Org-scope the by-guid lookup BEFORE rotating (mints a new secret):
        # DeployToken reaches the org via registered_app. Fails closed
        # (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        token = DeployToken.objects.filter(
            guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id
        ).first()
        if token is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "deploy token not found",
            )
        if token.is_revoked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cannot rotate a revoked token; create a new one",
            )
        # #449: canonical ``alft_dt_`` prefix — see ``create_deploy_token``.
        plaintext = PLAINTEXT_PREFIX + secrets_lib.token_urlsafe(32)
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        # Park the previous hash for the Constance-tunable grace
        # window so CI runners holding the old token keep working
        # until they're updated. The UI surfaces this exact value
        # on the rotate-confirm dialog (#425).
        grace_seconds = rotation_grace_seconds_from_constance()
        now = datetime.now(tz=UTC)
        token.previous_token_hash = token.token_hash
        token.previous_token_expires_at = now + timedelta(seconds=grace_seconds)
        token.token_hash = digest
        token.token_last_4 = plaintext[-4:]
        token.last_rotated_at = now
        token.save(
            update_fields=[
                "previous_token_hash",
                "previous_token_expires_at",
                "token_hash",
                "token_last_4",
                "last_rotated_at",
                "updated_at",
                "version",
            ]
        )
        return gql_success(
            DeployTokenSecretReveal(
                token=deploy_token_to_type(token),
                plaintext_secret=plaintext,
                rotation_grace_seconds=grace_seconds,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.deploy_token.revoke")
    @requires_elevation(action_label="app.deploy_token.revoke")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def revoke_deploy_token(
        self,
        info: Info,
        input: RevokeDeployTokenInput,
    ) -> MutationResultType[_DeployTokenRevokedPayload]:
        # Org-scope the by-guid lookup before revoking: DeployToken reaches
        # the org via registered_app. Fails closed (NOT_FOUND) when org_id
        # is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        token = DeployToken.objects.filter(
            guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id
        ).first()
        if token is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "deploy token not found",
            )
        if not token.is_revoked:
            token.is_revoked = True
            token.save(
                update_fields=[
                    "is_revoked",
                    "updated_at",
                    "version",
                ]
            )
        return gql_success(
            _DeployTokenRevokedPayload(
                id=input.id,
                revoked=True,
            )
        )
