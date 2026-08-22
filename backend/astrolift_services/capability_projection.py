"""Project live cluster + environment rows onto the capability
validators in ``astrolift_manifest.capability_check`` (#59).

Those validators are pure on purpose: they take flattened primitives so
the promotion rules can be reasoned about without the ORM. This module
is the flattening step, and it owns the two facts they cannot look up
for themselves:

* what a cluster can actually book -- its own plugin's catalogue plus
  the in-cluster drivers every tenant cluster borrows, which is exactly
  what ``managed_service_catalog.list_catalog`` already answers for
  ``createManagedService``. A capability projection narrower or wider
  than that gate would disagree with the thing it gates.
* which plugin *owns* a variant, which is not the cluster's plugin
  whenever the in-cluster fallback fired. The pin has to name the owner:
  attribute ``cnpg`` on an EKS cluster to ``aws`` and a Postgres that
  runs inside the cluster either way reads as AWS-specific, so
  promoting it to GKE would be refused for no reason.
"""

from __future__ import annotations

from astrolift_manifest.capability_check import (
    CheckReport,
    ClusterCapabilities,
    ResolvedVariantSnapshot,
    promotion_check,
)
from astrolift_services.models import ManagedService

# A cloud plugin's slug *is* the cloud it runs on, so it doubles as the
# pin prefix the cross-cloud rule looks for. ``k8s_native`` is the
# exception: it is a deployment shape rather than a cloud, and calling
# it one would mark every in-cluster variant cloud-specific -- the
# opposite of what it is.
_IN_CLUSTER_CLOUD = "onprem"


def _cloud_provider(plugin_slug: str) -> str:
    from astrolift_drivers.managed_resolution import IN_CLUSTER_PLUGIN

    return _IN_CLUSTER_CLOUD if plugin_slug == IN_CLUSTER_PLUGIN else plugin_slug


def _pin(*, cluster_plugin_slug: str, kind: str, variant: str) -> str:
    """``<owning plugin>/<variant>`` for ``variant`` booked on a cluster
    running ``cluster_plugin_slug``.

    The owner comes from the same resolution order the provisioning
    activities use, so a borrowed in-cluster driver is attributed to
    ``k8s_native`` and not to the cloud that borrowed it.
    """
    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from astrolift_drivers.registry import DriverNotFound

    try:
        owner = resolve_managed_driver(
            cluster_plugin_slug=cluster_plugin_slug,
            kind=kind,
            variant=variant,
        ).plugin_slug
    except DriverNotFound:
        # Provisioned by a driver this control plane no longer carries.
        # Attributing it to the cluster's own plugin makes the pin miss
        # every catalogue, which is the honest answer: nothing here can
        # satisfy it.
        owner = cluster_plugin_slug
    return f"{owner}/{variant}"


def cluster_capabilities(cluster) -> ClusterCapabilities:
    """Flatten a ``TenantCluster``'s bookable managed-service surface.

    ``include_extended`` because the extended tier is a statement about
    what the browse list promises, not about what is provisionable: an
    app already running ``warehouse`` must still promote.
    """
    from astrolift_services.managed_service_catalog import list_catalog

    plugin_slug = cluster.provider_plugin.slug
    rows = [row for row in list_catalog(plugin_slug, include_extended=True) if row.available]
    return ClusterCapabilities(
        cluster_id=cluster.pk,
        cluster_slug=cluster.slug,
        cloud_provider=_cloud_provider(plugin_slug),
        supported_kinds=frozenset(row.kind for row in rows),
        supported_pins=frozenset(
            _pin(cluster_plugin_slug=plugin_slug, kind=row.kind, variant=row.variant) for row in rows
        ),
    )


def environment_resolutions(env) -> tuple[ResolvedVariantSnapshot, ...]:
    """The variants an ``AppEnvironment``'s managed services resolved to.

    Every non-deleted service counts, not only the active ones: a
    service still provisioning is a dependency the promoted deployment's
    bindings will expect on the target.
    """
    plugin_slug = env.tenant_cluster.provider_plugin.slug
    out: list[ResolvedVariantSnapshot] = []
    for svc in ManagedService.objects.filter(
        app_environment=env,
        deleted_at__isnull=True,
    ).order_by("kind", "name"):
        if not svc.variant:
            # A third-party plugin off the catalogue contract can book a
            # service with no variant recorded. There is no pin to carry
            # across, so there is nothing to check.
            continue
        out.append(
            ResolvedVariantSnapshot(
                kind=svc.kind,
                name=svc.name or svc.kind,
                pin=_pin(cluster_plugin_slug=plugin_slug, kind=svc.kind, variant=svc.variant),
            )
        )
    return tuple(out)


def check_promotion(*, source_env, target_env) -> CheckReport:
    """Can ``target_env``'s cluster satisfy every managed service the
    source environment resolved? Returns the full issue list so the
    operator sees every blocker at once instead of bisecting.
    """
    return promotion_check(
        source_resolutions=environment_resolutions(source_env),
        target_cluster=cluster_capabilities(target_env.tenant_cluster),
        source_cloud_provider=_cloud_provider(source_env.tenant_cluster.provider_plugin.slug),
    )
