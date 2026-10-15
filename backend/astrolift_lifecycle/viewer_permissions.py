"""What the viewer may do to an environment, for clients to offer only that (#1867).

The environment mutations check ``app.deploy`` (pause and resume deploys or
ingress) or ``app.update`` (its settings) on the owning app, with the
environment's name and region as the ABAC facts (``environment_operation``).
This answers the same question for a page of environments with one grant
read: the resolver's ``_decide_from_grants`` under the same facts, behind the
same bearer-token ceilings. Advisory: the mutation still decides.
"""

from __future__ import annotations

from core.permissions import Permission

ACTIONS = (Permission.APP_DEPLOY, Permission.APP_UPDATE)


def environment_viewer_permissions(environments) -> dict[int, list[str]]:
    """Sorted permission keys per ``AppEnvironment.pk``; empty without an actor."""
    from astrolift_identity.abac import (
        RequestAttributes,
        current_attributes,
        operation_attributes,
        request_attributes,
    )
    from astrolift_identity.operation_context import environment_context
    from astrolift_identity.permission_resolver import (
        _app_scope_chains,
        _decide_from_grants,
        _is_superuser,
        _live_grants,
        _prefetch_app_slugs,
        _share_grants,
        actor_groups,
    )
    from astrolift_registry.scopes import _credential_app_ids, _credential_scope
    from core.permissions import PermissionDenied, PermissionScope, ScopeKind, _check_permission_decision
    from core.tenancy import get_current_tenant

    rows = list(environments)
    result: dict[int, list[str]] = {env.pk: [] for env in rows}
    tenant = get_current_tenant()
    if not rows or tenant is None or tenant.actor_user_id is None or tenant.organization_id is None:
        return result
    with request_attributes(current_attributes() or RequestAttributes()):
        app_ids = {env.registered_app_id for env in rows}
        chains = _app_scope_chains(tenant, app_ids)
        groups = actor_groups(tenant)
        grants = tuple(_live_grants(tenant, {link for chain in chains.values() for link in chain}))
        shares = _share_grants(tenant, app_ids)
        superuser = _is_superuser(tenant.actor_user_id)
        _prefetch_app_slugs(tenant, chains)
        ceilings = {permission: _credential_app_ids(app_ids, permission) for permission in ACTIONS}
        for env in rows:
            app_id = env.registered_app_id
            chain = chains.get(app_id)
            scope = PermissionScope(ScopeKind.APP, app_id)
            held = []
            with operation_attributes(**environment_context(env).attributes()):
                for permission in ACTIONS:

                    def decide(permission=permission, chain=chain, app_id=app_id):
                        if superuser:
                            return True, "django superuser"
                        if not chain:
                            return False, "no scope"
                        return _decide_from_grants(
                            tenant, permission, chain, grants, tuple(shares.get(app_id, ())), groups
                        ).as_tuple()

                    try:
                        _credential_scope(scope, permission, allowed_app_ids=ceilings[permission])
                        _check_permission_decision(permission, scope, decide)
                    except PermissionDenied:
                        continue
                    held.append(permission.value)
            result[env.pk] = sorted(held)
    return result
