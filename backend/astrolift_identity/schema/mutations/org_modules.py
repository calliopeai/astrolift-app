"""OrganizationModuleMutations — an org admin turns a per-org module on or off (#1859)."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import OrganizationModule
from astrolift_identity.org_modules import INSTALL_SWITCHES, allowed_by_install
from astrolift_identity.schema.mutations.helpers import _actor
from astrolift_identity.schema.mutations.types import (
    SetOrganizationModuleInput,
    _OrganizationModulePayload,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


def _module_target(*args, **kwargs):
    """Stamp the module key onto the audit row, so each change is queryable per module."""
    payload = kwargs.get("input")
    if payload is None and len(args) >= 3:
        payload = args[2]
    key = getattr(payload, "key", "")
    return ("OrganizationModule", key) if key else None


@strawberry.type
class OrganizationModuleMutations:
    @strawberry.field(
        description=(
            "Turn a per-organization module on or off for the active organization "
            "(org admins: ``org.update``). ``key`` is ``chat_studio_integration``, "
            "``agent_live_attach`` or ``chat_studio_agent_runs``. Turning on a module "
            "the install admin has forced off (``astroliftServerInfo.featureFlags``, "
            "``modules.*_allowed``) is refused with PRECONDITION; turning one off "
            "always succeeds."
        )
    )
    @mutation_audit(action="org.module.set", target=_module_target)
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def set_organization_module(
        self, info: Info, input: SetOrganizationModuleInput
    ) -> MutationResultType[_OrganizationModulePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        key = (input.key or "").strip()
        if key not in INSTALL_SWITCHES:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown module {key!r}; expected one of {sorted(INSTALL_SWITCHES)}",
                field="key",
            )
        if input.enabled and not allowed_by_install(key):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"the {key} module is turned off on this install; ask the install admin",
                field="key",
            )

        actor = _actor()
        row, _created = OrganizationModule.objects.get_or_create(
            organization_id=org_id,
            key=key,
            defaults={"created_by": actor},
        )
        row.enabled = bool(input.enabled)
        row.updated_by = actor
        row.save()
        return gql_success(_OrganizationModulePayload(key=key, enabled=row.enabled))
