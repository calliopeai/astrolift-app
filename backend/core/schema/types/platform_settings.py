"""Platform settings in the web admin (#1861): read, set and reset Constance.

Install-wide values, the same for every organization, so only the platform
operator reads or changes them, as with ``setFeatureFlag``: the stock org
owner and admin roles hold every permission, ``admin.elevate`` included, so a
permission alone cannot gate an install-wide write (#1978). Changes need a
fresh sign-in and are audited with the old and new value. The rules live in
``core.platform_settings``.
"""

from __future__ import annotations

import dataclasses

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from core import platform_secrets, platform_settings
from core.mutations import mutation_audit
from core.permissions import Permission, check_platform_operator, require_permission


@strawberry.type(name="AstroliftPlatformSetting")
class PlatformSettingType:
    key: str
    group: str
    """The ``CONSTANCE_CONFIG_FIELDSETS`` group it is listed under."""
    kind: str
    """``bool``, ``choice``, ``int``, ``float``, ``decimal`` or ``string``."""
    help_text: str
    value: str
    """The current value as text (``true``/``false`` for yes/no settings); empty for a credential."""
    default: str
    changed: bool
    """The value differs from the default."""
    choices: list[str]
    editable: bool
    read_only_reason: str
    """Why the setting is not edited here; empty when it is."""
    warning: str
    """Shown before a change, which then needs ``confirmed``; empty for most settings."""


@strawberry.type(name="AstroliftPlatformSettingChange")
class PlatformSettingChangeType:
    setting: PlatformSettingType
    previous_value: str


def _to_type(row: platform_settings.PlatformSetting) -> PlatformSettingType:
    return PlatformSettingType(
        key=row.key,
        group=row.group,
        kind=row.kind,
        help_text=row.help_text,
        value=row.value,
        default=row.default,
        changed=row.changed,
        choices=list(row.choices),
        editable=row.editable,
        read_only_reason=row.read_only_reason,
        warning=row.warning,
    )


def _change(before, after) -> MutationResultType[PlatformSettingChangeType]:
    return gql_success(PlatformSettingChangeType(setting=_to_type(after), previous_value=before.value))


def _target(*args, **kwargs):
    key = kwargs.get("key")
    return ("PlatformSetting", key) if key else None


def _extras(result):
    if not result.ok or result.data is None:
        return None
    return {"previous_value": result.data.previous_value, "value": result.data.setting.value}


@strawberry.type(name="AstroliftPlatformSecretStore")
class PlatformSecretStoreType:
    organization_slug: str
    organization_name: str
    cluster_slug: str
    """The cluster whose store holds the organization's agent secrets; empty when it has none."""
    shared_cluster: bool
    spec_count: int


@strawberry.type(name="AstroliftPlatformSecretRef")
class PlatformSecretRefType:
    spec_slug: str
    env_var: str
    uri: str
    exists: bool
    """The store holds a value; false with ``error`` when the probe could not tell."""
    error: str


@strawberry.type(name="AstroliftPlatformSecretInventory")
class PlatformSecretInventoryType:
    store: PlatformSecretStoreType
    provider: str
    can_reveal: bool
    refs: list[PlatformSecretRefType]


def _store_type(row: platform_secrets.SecretStore) -> PlatformSecretStoreType:
    return PlatformSecretStoreType(**dataclasses.asdict(row))


@strawberry.type
class PlatformSettingsQuery:
    @strawberry.field(description="Every install setting, grouped, for the platform operator (#1861).")
    @require_permission(Permission.ADMIN_ELEVATE)
    def astrolift_platform_settings(self, info: Info) -> list[PlatformSettingType]:
        check_platform_operator(info.context.user, gate=Permission.ADMIN_ELEVATE)
        return [_to_type(row) for row in platform_settings.list_settings()]

    @strawberry.field(
        description="Each organization's agent secret store: the cluster it resolves from (#1861)."
    )
    @require_permission(Permission.ADMIN_ELEVATE)
    def astrolift_platform_secret_stores(self, info: Info) -> list[PlatformSecretStoreType]:
        check_platform_operator(info.context.user, gate=Permission.ADMIN_ELEVATE)
        return [_store_type(row) for row in platform_secrets.list_stores()]

    @strawberry.field(
        description=(
            "One organization's agent secret refs and whether its store holds each, never the "
            "values (#1861). Probed as dispatch resolves them."
        )
    )
    @require_permission(Permission.ADMIN_ELEVATE)
    def astrolift_platform_secret_inventory(
        self, info: Info, organization_slug: str
    ) -> PlatformSecretInventoryType | None:
        check_platform_operator(info.context.user, gate=Permission.ADMIN_ELEVATE)
        found = platform_secrets.inventory(organization_slug)
        if found is None:
            return None
        return PlatformSecretInventoryType(
            store=_store_type(found.store),
            provider=found.provider,
            can_reveal=found.can_reveal,
            refs=[PlatformSecretRefType(**dataclasses.asdict(ref)) for ref in found.refs],
        )


@strawberry.type
class PlatformSettingsMutations:
    @strawberry.field(
        description=(
            "Set an install setting (#1861). ``value`` is text, ``true``/``false`` for a yes/no "
            "setting, validated as the Django admin validates it. A setting with a ``warning`` "
            "needs ``confirmed``. Platform operator only, with a fresh sign-in."
        )
    )
    @mutation_audit(action="admin.platform_setting.set", target=_target, extras=_extras)
    @requires_elevation(action_label="Change a platform setting")
    @require_permission(Permission.ADMIN_ELEVATE)
    def set_platform_setting(
        self, info: Info, key: str, value: str, confirmed: bool = False
    ) -> MutationResultType[PlatformSettingChangeType]:
        check_platform_operator(info.context.user, gate=Permission.ADMIN_ELEVATE)
        try:
            before, after = platform_settings.set_setting(key, value, confirmed=confirmed)
        except platform_settings.PlatformSettingError as exc:
            return gql_failure(exc.code, exc.message, field="value" if exc.code == "VALIDATION" else "key")
        return _change(before, after)

    @strawberry.field(description="Put an install setting back to its default (#1861).")
    @mutation_audit(action="admin.platform_setting.reset", target=_target, extras=_extras)
    @requires_elevation(action_label="Reset a platform setting")
    @require_permission(Permission.ADMIN_ELEVATE)
    def reset_platform_setting(
        self, info: Info, key: str, confirmed: bool = False
    ) -> MutationResultType[PlatformSettingChangeType]:
        check_platform_operator(info.context.user, gate=Permission.ADMIN_ELEVATE)
        try:
            before, after = platform_settings.reset_setting(key, confirmed=confirmed)
        except platform_settings.PlatformSettingError as exc:
            return gql_failure(exc.code, exc.message, field="key")
        return _change(before, after)
