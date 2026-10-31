"""Which app environments a managed domain serves (#2572).

An environment is bound at creation to the zone ``resolve_managed_domain``
picked (``AppEnvironment.managed_domain``), and the render reads that binding
for the platform hostname's Ingress. Deleting the zone new apps resolve through
left every later registration with no hostname and nothing saying so, so the
delete names what is bound to the zone.
"""

from __future__ import annotations

from astrolift_clusters.models import ManagedDomain
from core.permissions import Permission
from core.tenancy import get_current_tenant

# How many dependents a refusal names and the domain type lists.
DEPENDENTS_SHOWN = 10


def _platform_operator(user) -> bool:
    from django.core.exceptions import PermissionDenied

    from core.permissions import require_platform_operator

    try:
        require_platform_operator(user)
    except PermissionDenied:
        return False
    return True


def dependent_environments(domain: ManagedDomain, user) -> tuple[int, list]:
    """How many live app environments are bound to ``domain``, and the first ten.

    A shared zone's environments span organizations, so the platform operator
    sees every org's. Anyone else sees the environments of apps they may read
    in the selected organization, the visibility the routes diagnostics use.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.scopes import live_app_owners
    from astrolift_registry.visibility import visible_registry_apps

    environments = AppEnvironment.objects.filter(
        managed_domain_id=domain.pk,
        registered_app__deleted_at__isnull=True,
        registered_app__organization__deleted_at__isnull=True,
    )
    if not (domain.organization_id is None and _platform_operator(user)):
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        environments = environments.filter(
            registered_app__in=visible_registry_apps(
                live_app_owners(RegisteredApp.objects.filter(organization_id=org_id)),
                Permission.APP_READ,
            )
        )
    environments = environments.select_related("registered_app__organization").order_by(
        "registered_app__organization__slug", "registered_app__slug", "name", "pk"
    )
    return environments.count(), list(environments[:DEPENDENTS_SHOWN])


def environment_path(environment) -> str:
    app = environment.registered_app
    return f"{app.organization.slug}/{app.slug}/{environment.name}"


def default_zone_refusal(domain: ManagedDomain, total: int, environments: list) -> str:
    """Why ``softDeleteManagedDomain`` kept an effective default zone."""
    names = ", ".join(environment_path(environment) for environment in environments)
    if total == 0:
        bound = "No environment resolves through it yet."
    elif total == 1:
        bound = f"1 environment resolves through it: {names}."
    elif total > len(environments):
        bound = f"{total} environments resolve through it: {names}, and {total - len(environments)} more."
    else:
        bound = f"{total} environments resolve through it: {names}."
    return (
        f"{domain.zone} is the effective default zone for new app hostnames. {bound} "
        "Apps registered after it is deleted get the next default zone, or no hostname at all. "
        "Retry with force to delete it anyway."
    )
