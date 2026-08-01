"""EnvironmentSettingMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.models import (
    AppEnvironment,
    EnvironmentSetting,
)
from astrolift_lifecycle.schema.mutations.types import (
    ClearEnvironmentSettingInput,
    SetEnvironmentSettingInput,
)
from astrolift_lifecycle.schema.types import (
    EnvironmentSettingType,
    env_setting_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class EnvironmentSettingMutations:
    # ----------------------------------------------------------------
    # Environment-level key/value settings overrides (#744)
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="app.env.setting.set")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_environment_setting(
        self,
        info: Info,
        input: SetEnvironmentSettingInput,
    ) -> MutationResultType[EnvironmentSettingType]:
        tenant = get_current_tenant()
        env = (
            AppEnvironment.objects.select_related("registered_app")
            .filter(guid=str(input.environment_id), deleted_at__isnull=True)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found", field="environmentId")
        if env.registered_app.organization_id != tenant.organization_id:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found", field="environmentId")

        key = (input.key or "").strip()
        if not key:
            return gql_failure(ErrorCode.VALIDATION.value, "key must not be empty", field="key")

        actor = info.context.request.user
        existing = EnvironmentSetting.objects.filter(
            app_environment=env, key=key, deleted_at__isnull=True
        ).first()
        if existing is not None:
            existing.value = input.value
            existing.updated_by = actor
            existing.save(update_fields=["value", "updated_by", "updated_at", "version"])
            return gql_success(env_setting_to_type(existing))

        setting = EnvironmentSetting.objects.create(
            app_environment=env,
            key=key,
            value=input.value,
            created_by=actor,
            updated_by=actor,
        )
        return gql_success(env_setting_to_type(setting))

    @strawberry.field
    @mutation_audit(action="app.env.setting.clear")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def clear_environment_setting(
        self,
        info: Info,
        input: ClearEnvironmentSettingInput,
    ) -> MutationResultType[EnvironmentSettingType]:
        tenant = get_current_tenant()
        env = (
            AppEnvironment.objects.select_related("registered_app")
            .filter(guid=str(input.environment_id), deleted_at__isnull=True)
            .first()
        )
        if env is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found", field="environmentId")
        if env.registered_app.organization_id != tenant.organization_id:
            return gql_failure(ErrorCode.NOT_FOUND.value, "environment not found", field="environmentId")

        key = (input.key or "").strip()
        if not key:
            return gql_failure(ErrorCode.VALIDATION.value, "key must not be empty", field="key")

        actor = info.context.request.user
        setting = EnvironmentSetting.objects.filter(
            app_environment=env, key=key, deleted_at__isnull=True
        ).first()
        if setting is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, f"no active setting for key {key!r}", field="key")

        setting.deleted_at = timezone.now()
        setting.deleted_by = actor
        setting.save(update_fields=["deleted_at", "deleted_by", "updated_at", "version"])
        return gql_success(env_setting_to_type(setting))
