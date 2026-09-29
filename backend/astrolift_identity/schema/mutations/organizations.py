"""OrganizationMutations — split from the monolithic mutations module."""

from __future__ import annotations

import logging

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
    _actor,
    _resolve_org,
)
from astrolift_identity.schema.mutations.types import (
    CreateOrganizationInput,
    SoftDeleteByGuidInput,
    UpdateOrganizationInput,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    OrganizationType,
    organization_to_type,
)
from astrolift_operations.observability_profile import (
    RETENTION_LOGS,
    RETENTION_METRICS,
    RETENTION_METRICS_ROLLUP,
    RETENTION_TRACES,
)
from core.appearance import AppearanceError, validate_appearance
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.resource_tags import ResourceTagError, validate_resource_tags
from core.tenancy import get_current_tenant


@strawberry.type
class OrganizationMutations:
    @strawberry.field
    @mutation_audit(action="org.create")
    @require_permission(Permission.ORG_UPDATE)
    def create_organization(
        self, info: Info, input: CreateOrganizationInput
    ) -> MutationResultType[OrganizationType]:
        if Organization.objects.filter(slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"organization with slug {input.slug!r} already exists",
                field="slug",
            )
        org = Organization.objects.create(
            name=input.name,
            slug=input.slug,
            website=input.website or "",
        )
        # Seed the org-wide security burst rules (#151) so a new tenant
        # detects a credential-stuffing run out of the box instead of only
        # recording the failures in the audit log. There is no
        # ``org.created`` platform Event to subscribe to, so this is the
        # hook, mirroring the per-app seeding at app registration:
        # idempotent, and best-effort because an alert-config failure must
        # never fail the org create.
        try:
            from astrolift_operations.alert_seed import seed_security_alert_rules

            seed_security_alert_rules(org)
        except Exception:
            logging.getLogger(__name__).exception(
                "create_organization: security alert-rule seeding failed for %s",
                org.slug,
            )
        return gql_success(organization_to_type(org))

    @strawberry.field
    @mutation_audit(action="org.update")
    @require_permission(Permission.ORG_UPDATE)
    def update_organization(
        self, info: Info, input: UpdateOrganizationInput
    ) -> MutationResultType[OrganizationType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        org = _resolve_org(input.id, org_id)
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        if input.name is not None:
            org.name = input.name
        if input.website is not None:
            org.website = input.website
        if input.audit_log_retention_days is not None:
            days = int(input.audit_log_retention_days)
            # Sane bound (spec ceiling ~7 years). The DB column is a
            # PositiveIntegerField, which still admits 0 and absurdly
            # large values; both the /administration/organization and
            # /administration/audit surfaces write this field, so guard
            # it here rather than trusting client-side min/max alone.
            if days < 1 or days > 2557:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "auditLogRetentionDays must be between 1 and 2557 (about 7 years)",
                    field="auditLogRetentionDays",
                )
            org.audit_log_retention_days = days
        if input.log_retention_days_default is not None:
            log_days = int(input.log_retention_days_default)
            # Bounded by the platform log-retention window rather than a
            # literal, so this and the Logs surface that reads the column
            # cannot drift apart. Above the ceiling the aggregator would
            # be asked for lines the platform never promised to keep.
            if log_days < 1 or log_days > RETENTION_LOGS.max_days:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"logRetentionDaysDefault must be between 1 and {RETENTION_LOGS.max_days}",
                    field="logRetentionDaysDefault",
                )
            org.log_retention_days_default = log_days
        # The same shape three more times (#1602), each against its own
        # window. Table-driven rather than three more copies of the branch
        # above: the branches differ only in which field, which ceiling and
        # which camelCase name go in, and the ceiling is the part that must
        # not get copy-pasted wrong. The table puts all three side by side
        # where a mismatched pairing is visible.
        #
        # The rollup gets RETENTION_METRICS_ROLLUP and not RETENTION_METRICS
        # deliberately: its column defaults to 365 and RETENTION_METRICS
        # caps at 365, so bounding it there would leave a knob whose only
        # legal value is the one it already has.
        for field_name, column, window, camel in (
            (
                "metrics_retention_days_default",
                "metrics_retention_days_default",
                RETENTION_METRICS,
                "metricsRetentionDaysDefault",
            ),
            (
                "metrics_rollup_retention_days_default",
                "metrics_rollup_retention_days_default",
                RETENTION_METRICS_ROLLUP,
                "metricsRollupRetentionDaysDefault",
            ),
            (
                "trace_retention_days_default",
                "trace_retention_days_default",
                RETENTION_TRACES,
                "traceRetentionDaysDefault",
            ),
        ):
            supplied = getattr(input, field_name, None)
            if supplied is None:
                continue
            days_value = int(supplied)
            if days_value < 1 or days_value > window.max_days:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"{camel} must be between 1 and {window.max_days}",
                    field=camel,
                )
            setattr(org, column, days_value)
        if input.allow_user_profile_edit is not None:
            org.allow_user_profile_edit = input.allow_user_profile_edit
        if input.default_resource_tags is not None:
            # Refused here rather than at provision time. A tag that only
            # AWS accepts would otherwise provision fine for weeks and then
            # fail the first GCP resource, with the resource half made and
            # nothing pointing at the tag as the cause.
            try:
                org.default_resource_tags = validate_resource_tags(input.default_resource_tags)
            except ResourceTagError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    str(exc),
                    field="defaultResourceTags",
                )
        if input.managed_service_isolation_policy is not None:
            from astrolift_drivers.isolation import IsolationError, parse_policy
            from astrolift_services.models import ManagedService

            raw = input.managed_service_isolation_policy
            if not isinstance(raw, dict):
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "managedServiceIsolationPolicy must be an object mapping kind to mode",
                    field="managedServiceIsolationPolicy",
                )
            known_kinds = {kind for kind, _label in ManagedService.Kind.choices}
            unknown = sorted(str(kind) for kind in raw if str(kind) not in known_kinds)
            if unknown:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"managedServiceIsolationPolicy names unknown kinds {unknown}",
                    field="managedServiceIsolationPolicy",
                )
            try:
                policy = parse_policy(raw)
            except IsolationError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    str(exc),
                    field="managedServiceIsolationPolicy",
                )
            org.managed_service_isolation_policy = {kind: mode.value for kind, mode in policy.items()}

        if input.appearance_default is not None:
            # Validated here for the same reason the tags above are: the
            # client's normalize() protects the client, not the column, and an
            # org admin can post this mutation without a browser. A rejected
            # axis says what was allowed rather than being dropped silently.
            try:
                org.appearance_default = validate_appearance(input.appearance_default)
            except AppearanceError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    str(exc),
                    field="appearanceDefault",
                )
        if input.appearance_locked is not None:
            org.appearance_locked = bool(input.appearance_locked)
        if input.restricted_settings_default is not None:
            from astrolift_identity.ui_preferences import UiPreferenceError, validate_restricted_settings

            try:
                org.restricted_settings_default = validate_restricted_settings(
                    input.restricted_settings_default, field="restrictedSettingsDefault"
                )
            except UiPreferenceError as exc:
                return gql_failure(ErrorCode.VALIDATION.value, str(exc), field=exc.field)
        org.save()
        return gql_success(organization_to_type(org))

    @strawberry.field
    @mutation_audit(action="org.delete")
    @require_permission(Permission.ORG_DELETE)
    def soft_delete_organization(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        org = _resolve_org(input.id, org_id)
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")
        org.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
