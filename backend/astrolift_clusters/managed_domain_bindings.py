"""Which app environments a managed domain serves, and rebinding the ones that
lost theirs (#2572).

An environment is bound at creation to the zone ``resolve_managed_domain``
picked (``AppEnvironment.managed_domain``), and the render reads that binding
for the platform hostname's Ingress. Deleting the zone new apps resolve through
left every later registration with no hostname and nothing saying so, so the
delete names what is bound to the zone, and the backfill binds the environments
created while no zone resolved.
"""

from __future__ import annotations

from dataclasses import dataclass

from astrolift_clusters.models import ManagedDomain, resolve_managed_domain
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


@dataclass(frozen=True)
class Binding:
    """One unbound environment, and the zone and URL the backfill gave it.
    ``zone`` is empty when no zone resolves for its organization."""

    organization_slug: str
    app_slug: str
    environment_name: str
    zone: str
    previous_url: str
    url: str


@dataclass(frozen=True)
class BindingBackfill:
    dry_run: bool
    bound: list[Binding]
    skipped: list[Binding]


def backfill_managed_domain_bindings(*, dry_run: bool = False) -> BindingBackfill:
    """Bind every live environment with no managed domain to the zone its
    organization resolves to now, and rewrite the URL it advertises to match.

    Binding the FK alone was not enough (#1690): the render reads the FK, but
    the UI kept showing the URL the row was created with. Idempotent: bound
    rows are never revisited. ``dry_run`` reports what would change and
    writes nothing.
    """
    from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
    from core.app_deploy import environment_hostname_label

    bound: list[Binding] = []
    skipped: list[Binding] = []
    unbound = (
        AppEnvironment.objects.filter(managed_domain__isnull=True, deleted_at__isnull=True)
        .select_related("registered_app__organization")
        .order_by("pk")
    )
    for env in unbound:
        app = env.registered_app
        org = getattr(app, "organization", None)
        domain = resolve_managed_domain(org, for_preview=False)
        if domain is None:
            skipped.append(
                Binding(
                    organization_slug=getattr(org, "slug", ""),
                    app_slug=app.slug,
                    environment_name=env.name,
                    zone="",
                    previous_url=env.url,
                    url=env.url,
                )
            )
            continue
        host = (app.subdomain or app.slug or "").strip()
        if host and (env.k8s_namespace or "").strip():
            # An environment in a namespace of its own serves its own
            # hostname (#1922): a preview the one its row records, which
            # its URL already names, any other ``<label>-<env>``.
            is_preview = PreviewEnvironment.all_objects.filter(app_environment=env).exists()
            host = "" if is_preview else environment_hostname_label(host, env.name)
        url = f"https://{host}.{domain.zone}" if host else env.url
        if not dry_run:
            changes: dict = {"managed_domain": domain}
            if url != env.url:
                changes["url"] = url
            AppEnvironment.objects.filter(pk=env.pk).update(**changes)
        bound.append(
            Binding(
                organization_slug=getattr(org, "slug", ""),
                app_slug=app.slug,
                environment_name=env.name,
                zone=domain.zone,
                previous_url=env.url,
                url=url,
            )
        )
    return BindingBackfill(dry_run=dry_run, bound=bound, skipped=skipped)
