"""ApiTokenMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import (
    GUID,
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.models import (
    ApiToken,
    Organization,
    Team,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
)
from astrolift_identity.schema.mutations.types import (
    CreateApiTokenInput,
    GenerateInstallEnrollmentQrInput,
    RevokeApiTokenInput,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    ApiTokenPlaintextType,
    EnrollmentQrPayloadType,
    api_token_to_type,
)
from astrolift_identity.scopes import identity_organization_scope
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class ApiTokenMutations:
    # ---- API tokens --------------------------------------------------

    @strawberry.field
    @mutation_audit(action="api_token.create")
    @require_permission(
        Permission.API_TOKEN_CREATE, scope=identity_organization_scope(Permission.API_TOKEN_CREATE)
    )
    @tenant_scoped()
    def create_api_token(
        self, info: Info, input: CreateApiTokenInput
    ) -> MutationResultType[ApiTokenPlaintextType]:
        actor = _actor()
        if actor is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "no actor")

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        team = None
        if input.team_slug:
            team = Team.objects.filter(organization=org, slug=input.team_slug).first()
            if team is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamSlug")

        from astrolift_identity.api_tokens import (
            deadline_from_days,
            mint_token,
            normalize_scopes,
            validate_scopes,
        )

        normalized = normalize_scopes(input.scopes)
        unknown = validate_scopes(normalized)
        if unknown:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown scope(s): {', '.join(unknown)}",
                field="scopes",
            )

        minted = mint_token()
        token = ApiToken.objects.create(
            user=actor,
            organization=org,
            team=team,
            name=input.name.strip(),
            token_hash=minted.token_hash,
            token_last_4=minted.last4,
            scopes=normalized,
            expires_at=deadline_from_days(input.expires_in_days),
        )
        return gql_success(
            ApiTokenPlaintextType(
                api_token=api_token_to_type(token),
                plaintext=minted.plaintext,
            )
        )

    @strawberry.field
    @mutation_audit(action="api_token.revoke")
    @require_permission(
        Permission.API_TOKEN_REVOKE, scope=identity_organization_scope(Permission.API_TOKEN_REVOKE)
    )
    @tenant_scoped()
    def revoke_api_token(
        self, info: Info, input: RevokeApiTokenInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        token = ApiToken.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if token is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "api token not found")
        token.is_revoked = True
        token.save(update_fields=["is_revoked", "updated_at", "version"])
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Install enrollment QR (#494) --------------------------------

    @strawberry.field
    @mutation_audit(action="auth.enrollment.generated")
    def generate_install_enrollment_qr(
        self, info: Info, input: GenerateInstallEnrollmentQrInput
    ) -> MutationResultType[EnrollmentQrPayloadType]:
        # Self-service: any authed user can enroll their own mobile
        # device. The operator's web session IS the proof — same
        # pattern as ``update_my_profile``. EXEMPT in
        # ``test_tenancy_guardrail`` for the same reason.
        actor = _actor()
        if actor is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "authentication required")

        # Bind the enrollment to the operator's active tenant when
        # one is set; the redeemed access token inherits the same
        # organization. Tenants without an active org (e.g. a fresh
        # operator who hasn't picked one yet) get a null binding —
        # the resulting API token is unscoped, same as the existing
        # api_token.create path when no org is picked.
        tenant = get_current_tenant()
        org = None
        if tenant and tenant.organization_id:
            org = Organization.objects.filter(pk=tenant.organization_id).first()

        from astrolift_identity import device_flow as df

        result = df.create_enrollment(
            user=actor,
            organization=org,
            label=input.label or "",
            ttl_seconds=input.ttl_seconds,
        )
        if isinstance(result, str):
            if result == "rate_limited":
                return gql_failure(
                    ErrorCode.RATE_LIMITED.value,
                    f"too many active enrollments (max {df.ENROLLMENT_MAX_ACTIVE_PER_USER});"
                    " wait for one to expire or use it first",
                )
            if result == "no_organization":
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "no active organization — pick one before pairing a mobile device",
                )
            return gql_failure(ErrorCode.PRECONDITION.value, f"enrollment failed: {result}")

        # ---- build the QR payload + render the SVG -------------------
        import base64
        import json

        from django.conf import settings as dj_settings

        from astrolift_identity import _qr

        install_url = (dj_settings.APP_BASE_URL or "").rstrip("/") or "http://localhost:3000"
        install_label = org.name if org else (dj_settings.APP_BASE_URL or "Astrolift")
        payload_dict = {
            "v": 1,
            "install_url": install_url,
            "install_label": install_label,
            "enrollment_token": result.token_plaintext,
            "expires_at": result.expires_at.isoformat(),
            "session_id": result.session_id,
        }
        payload_json = json.dumps(payload_dict, separators=(",", ":"))
        qr_payload = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii")

        # The QR encodes the deep-link URL (per the issue task
        # context); the encoded JSON travels as a query param so
        # mobile scanners that pop a browser still hand off the
        # full payload. Falls back to plain ``install_url`` for
        # operators typing manually.
        deep_link = f"astrolift://enroll?payload={qr_payload}"
        qr = _qr.encode_text(deep_link, _qr.Ecc.M)
        qr_svg = _qr.to_svg(qr)

        return gql_success(
            EnrollmentQrPayloadType(
                qr_payload=qr_payload,
                qr_svg=qr_svg,
                verification_uri=install_url,
                session_id=result.session_id,
                session_guid=GUID(result.session_guid),
                expires_at=result.expires_at,
            )
        )
