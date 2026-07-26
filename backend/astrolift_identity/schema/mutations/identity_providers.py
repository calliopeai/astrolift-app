"""IdentityProviderMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import (
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.models import (
    IdentityProvider,
    Organization,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
    _validate_idp_config,
)
from astrolift_identity.schema.mutations.types import (
    CreateIdentityProviderInput,
    SetActiveIdentityProviderInput,
    SoftDeleteByGuidInput,
    UpdateIdentityProviderInput,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    IdentityProviderType,
    identity_provider_to_type,
)
from astrolift_identity.step_up import requires_elevation
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.optimistic import check_version_match as _check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class IdentityProviderMutations:
    # ---- Identity providers ------------------------------------------

    @strawberry.field
    @mutation_audit(action="identity_provider.create")
    @requires_elevation(action_label="identity_provider.create")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def create_identity_provider(
        self, info: Info, input: CreateIdentityProviderInput
    ) -> MutationResultType[IdentityProviderType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        valid_kinds = {k.value for k in IdentityProvider.Kind}
        if input.kind not in valid_kinds:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"kind must be one of {sorted(valid_kinds)}",
                field="kind",
            )

        validated = _validate_idp_config(input)
        if validated is not None:
            return validated

        from django.db import transaction
        from django.utils import timezone

        actor = _actor()
        is_active = False
        with transaction.atomic():
            idp = IdentityProvider.objects.create(
                organization=org,
                kind=input.kind,
                display_name=(input.display_name or "").strip(),
                config=input.config or {},
                metadata_url=input.metadata_url or "",
                oidc_discovery_url=input.oidc_discovery_url or "",
                client_id=input.client_id or "",
                client_secret_ref=input.client_secret_ref or "",
                is_default=False,
            )

            if input.set_active:
                # Stamp the audit columns atomically with the org
                # binding flip — if either side fails the whole switch
                # rolls back. ``activated_at`` is distinct from
                # ``updated_at`` so subsequent config edits don't
                # tick the FE's "active since" caption forward (#467).
                idp.activated_at = timezone.now()
                idp.last_switched_by = actor
                idp.save(
                    update_fields=[
                        "activated_at",
                        "last_switched_by",
                        "updated_at",
                        "version",
                    ]
                )
                org.identity_provider_id = idp.pk
                org.save(update_fields=["identity_provider", "updated_at", "version"])
                is_active = True

        return gql_success(identity_provider_to_type(idp, is_active=is_active))

    @strawberry.field
    @mutation_audit(action="identity_provider.update")
    @requires_elevation(action_label="identity_provider.update")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def update_identity_provider(
        self, info: Info, input: UpdateIdentityProviderInput
    ) -> MutationResultType[IdentityProviderType]:
        # #1183 SSO-hijack fix: this mutation overwrites client_id /
        # client_secret_ref / discovery URL. Scoped to the caller org so
        # a foreign IdP guid reads as not-found and can't be tampered.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        idp = (
            IdentityProvider.objects.select_related("organization")
            .filter(guid=str(input.id), organization_id=org_id)
            .first()
        )
        if idp is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "identity provider not found")

        # #497 — optimistic-concurrency gate.
        mismatch = _check_version_match(idp, if_match_version=input.if_match_version, kind="IdentityProvider")
        if mismatch is not None:
            return mismatch

        for field in (
            "display_name",
            "config",
            "metadata_url",
            "oidc_discovery_url",
            "client_id",
            "client_secret_ref",
        ):
            new_value = getattr(input, field)
            if new_value is not None:
                setattr(idp, field, new_value)
        idp.save()
        active_id = (
            Organization.objects.filter(pk=idp.organization_id)
            .values_list("identity_provider_id", flat=True)
            .first()
        )
        return gql_success(identity_provider_to_type(idp, is_active=(idp.pk == active_id)))

    @strawberry.field
    @mutation_audit(action="identity_provider.set_active")
    @requires_elevation(action_label="identity_provider.set_active")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def set_active_identity_provider(
        self, info: Info, input: SetActiveIdentityProviderInput
    ) -> MutationResultType[IdentityProviderType]:
        from django.db import transaction
        from django.utils import timezone

        # #1183: setting the active IdP flips the org's login provider.
        # Scoped to the caller org so a foreign IdP guid reads as
        # not-found and can't be used to hijack another org's SSO.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        idp = (
            IdentityProvider.objects.select_related("organization")
            .filter(guid=str(input.id), organization_id=org_id)
            .first()
        )
        if idp is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "identity provider not found")
        org = idp.organization
        actor = _actor()

        # Stamp the audit fields and the org binding inside one
        # transaction so the "active since {date}" + "by {user}"
        # caption on the settings page (#415) never desyncs from the
        # actual binding. ``activated_at`` is set unconditionally so
        # re-promoting a previously-active IdP rolls the caption
        # forward to the new activation moment, not the original one.
        with transaction.atomic():
            idp.activated_at = timezone.now()
            idp.last_switched_by = actor
            idp.save(
                update_fields=[
                    "activated_at",
                    "last_switched_by",
                    "updated_at",
                    "version",
                ]
            )
            org.identity_provider_id = idp.pk
            org.save(update_fields=["identity_provider", "updated_at", "version"])
        return gql_success(identity_provider_to_type(idp, is_active=True))

    @strawberry.field
    @mutation_audit(action="identity_provider.delete")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def soft_delete_identity_provider(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        idp = (
            IdentityProvider.objects.select_related("organization")
            .filter(guid=str(input.id), organization_id=org_id)
            .first()
        )
        if idp is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "identity provider not found")
        # Refuse to delete the IdP that's currently active — the operator
        # must set_active to a different one first, or this install would
        # be left without a way to log in.
        if idp.organization.identity_provider_id == idp.pk:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "this provider is currently active; pick a different one before deleting",
            )
        idp.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
