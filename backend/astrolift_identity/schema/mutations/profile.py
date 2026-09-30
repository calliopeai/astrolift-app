"""ProfileMutations — split from the monolithic mutations module."""

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
    Organization,
)
from astrolift_identity.schema.mutations.helpers import (
    _idp_locked_fields,
    _my_profile_payload,
)
from astrolift_identity.schema.mutations.types import (
    MarkOnboardingCompleteInput,
    UpdateMyProfileInput,
    UpdateMyUiPreferencesInput,
    _MarkOnboardingCompletePayload,
)
from astrolift_identity.schema.types import (
    MyProfileType,
    UiPreferencesType,
    organization_to_type,
    ui_preferences_to_type,
)
from astrolift_identity.scopes import identity_organization_scope
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class ProfileMutations:
    @strawberry.field
    @mutation_audit(action="profile.update_self")
    def update_my_profile(self, info: Info, input: UpdateMyProfileInput) -> MutationResultType[MyProfileType]:
        """Self-service profile edit. Gated by the org-level
        ``allow_user_profile_edit`` toggle and per-field IdP locks
        (a field claimed by the IdP at last login can't be edited
        locally because the next sync would overwrite it).

        Intentionally not gated by ``@require_permission`` — every
        authenticated user is allowed to edit *their own* profile.
        Admins can disable the entire feature org-wide via the
        toggle, which the resolver enforces here.
        """

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        org = Organization.objects.filter(pk=org_id).first() if org_id is not None else None
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")

        if not org.allow_user_profile_edit:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "profile editing is disabled by your organization administrator",
            )

        session = getattr(request, "session", None) if request else None
        locked = _idp_locked_fields(viewer, session=session)

        if input.first_name is not None and "first_name" in locked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "first_name is managed by your identity provider",
                field="firstName",
            )
        if input.last_name is not None and "last_name" in locked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "last_name is managed by your identity provider",
                field="lastName",
            )
        if input.email is not None and "email" in locked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "email is managed by your identity provider",
                field="email",
            )

        if input.first_name is not None:
            viewer.first_name = input.first_name.strip()[:150]
        if input.last_name is not None:
            viewer.last_name = input.last_name.strip()[:150]
        if input.email is not None:
            email = input.email.strip()[:254]
            if email and "@" not in email:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "email must be a valid address",
                    field="email",
                )
            viewer.email = email
        viewer.save(update_fields=["first_name", "last_name", "email"])

        # ── Timezone preference (#775) ──────────────────────────────────
        # Stored in UserPreferences (lazy one-to-one off auth.User).
        # Validated against zoneinfo.available_timezones(); an empty
        # string clears the override so the UI reverts to browser-detected.
        if input.timezone is not None:
            from zoneinfo import available_timezones

            from astrolift_identity.models import UserPreferences

            tz_val = input.timezone.strip()
            if tz_val and tz_val not in available_timezones():
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"{tz_val!r} is not a valid IANA timezone name",
                    field="timezone",
                )
            prefs = UserPreferences.for_user(viewer)
            prefs.timezone = tz_val
            prefs.save(update_fields=["timezone"])
            # ``for_user`` returns a fresh row via get_or_create; keep the
            # reverse one-to-one cache on ``viewer`` in sync so the payload
            # below reflects this write instead of a stale cached row.
            viewer.preferences = prefs

        return gql_success(_my_profile_payload(viewer, org, locked))

    @strawberry.field
    @mutation_audit(action="profile.update_ui_preferences")
    def update_my_ui_preferences(
        self, info: Info, input: UpdateMyUiPreferencesInput
    ) -> MutationResultType[UiPreferencesType]:
        """Save the viewer's own UI preferences (#2154), so they follow the
        person across browsers.

        Self-service like ``update_my_profile``: every signed-in person may
        change their own display choices, so there is no permission gate, and
        the row is keyed on the viewer, never on an id from the input. The
        whole input is validated before anything is written, so a bad value
        leaves the stored preferences as they were.
        """
        from strawberry import UNSET

        from astrolift_identity.models import UserPreferences
        from astrolift_identity.ui_preferences import (
            CHOICE_FIELDS,
            UiPreferenceError,
            org_restricted_default,
            validate_choice,
        )
        from core.appearance import AppearanceError, validate_appearance

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        changes: dict[str, object] = {}
        try:
            for column in CHOICE_FIELDS:
                value = getattr(input, column)
                if value is UNSET:
                    continue
                changes[column] = "" if value is None else validate_choice(column, value)
        except UiPreferenceError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field=exc.field)
        if input.home_layout_asked is not UNSET:
            changes["home_layout_asked"] = bool(input.home_layout_asked)
        if input.flow_particles is not UNSET:
            changes["flow_particles"] = input.flow_particles
        if input.appearance is not UNSET:
            try:
                changes["appearance"] = validate_appearance(input.appearance)
            except AppearanceError as exc:
                return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="appearance")
        # Choosing a layout answers the first-sign-in question, as it does
        # in the browser store.
        if changes.get("home_layout"):
            changes["home_layout_asked"] = True

        prefs = UserPreferences.for_user(viewer)
        if changes:
            for column, value in changes.items():
                setattr(prefs, column, value)
            prefs.save(update_fields=sorted(changes))

        tenant = get_current_tenant()
        org_default = org_restricted_default(tenant.organization_id if tenant else None)
        return gql_success(ui_preferences_to_type(prefs, org_default=org_default))

    @strawberry.field
    @mutation_audit(action="organization.mark_onboarding_complete")
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def mark_onboarding_complete(
        self, info: Info, input: MarkOnboardingCompleteInput
    ) -> MutationResultType[_MarkOnboardingCompletePayload]:
        """Mark the active organization's first-run wizard as done.

        Idempotent on purpose: the FE may race a manual re-run with
        the auto-open path, and we want both calls to succeed so the
        wizard reliably closes. ``already_completed`` lets the FE
        suppress the "great, you're set up!" toast on the re-entry
        path. ``skip`` only affects the audit row's ``extra`` payload —
        the persisted timestamp is the same.
        """
        from django.utils import timezone

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        already = org.onboarding_completed_at is not None
        if not already:
            org.onboarding_completed_at = timezone.now()
            org.save(update_fields=["onboarding_completed_at", "updated_at", "version"])

        return gql_success(
            _MarkOnboardingCompletePayload(
                organization=organization_to_type(org),
                already_completed=already,
            )
        )
