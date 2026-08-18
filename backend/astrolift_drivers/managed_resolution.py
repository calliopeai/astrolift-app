"""Managed-service driver resolution: the cluster's plugin, then in-cluster (#1484).

``astrolift_clusters.plugin_loader`` flattens every plugin's
``managed_service_drivers`` into the registry's flat driver namespace under the
synthetic role ``managed:<kind>:<variant>``. Resolution used to be a single
``plugins.get(<cluster plugin>, role)``, which meant a cluster carried either
cloud-managed drivers or in-cluster ones and never both: no cloud plugin
registers a ``k8s_native`` driver, so an app on EKS, GKE or AKS could not book
any in-cluster variant at all.

That contradicted the portability rule ``_sdk.coverage.is_portable`` encodes --
a kind is reachable everywhere once it has an in-cluster variant, because
Astrolift runs the cluster. Every tenant cluster *is* a Kubernetes cluster; an
in-cluster variant is defined by running in that cluster, not by which cloud
provisioned it. The rule was right and the runtime did not implement it, so the
lookup falls back to ``k8s_native``.

Resolution order, in full and on purpose::

    1. <cluster plugin>   managed:<kind>:<variant>
    2. k8s_native         managed:<kind>:<variant>
    3. <cluster plugin>   managed:<kind>:
    4. k8s_native         managed:<kind>:

**The cluster's own plugin wins an exact (kind, variant) collision.** No
(kind, variant) is registered by both a cloud plugin and ``k8s_native`` today,
so this is a policy choice rather than a bug fix -- which is exactly why it is
written down here instead of being left to whichever dict a caller happened to
consult first. The cloud plugin wins because:

* A live service was provisioned through whichever driver its cluster's plugin
  resolved. If a later release taught ``k8s_native`` the same (kind, variant)
  and the in-cluster driver started winning, deprovision would call the wrong
  driver against a handle it does not own -- leaving the real cloud resource
  running and billing while the row reports itself torn down.
* The plugin bound to a cluster is the operator's explicit registration choice.
  A fallback exists to widen what is reachable; it must never override a choice
  that was made deliberately.

An exact variant match beats an empty-variant default in *either* plugin, which
is why 2 comes before 3: a request naming ``memcached`` must not be served by a
cloud plugin's generic ``managed:cache:`` entry that would go and provision
ElastiCache instead.

**The fallback is one-way.** ``k8s_native`` never reaches into ``aws``, ``gcp``
or ``azure``, so a driver that genuinely needs cloud APIs stays unreachable
from a plugin that cannot provide them. This widens in-cluster reach; it does
not merge the plugins.

:class:`ResolvedManagedDriver` carries the plugin the driver actually came from
because every caller needs it to build the driver's config. An in-cluster
driver resolved from an AKS cluster takes the ``k8s_native`` config branch --
which attaches that cluster's own ``ClusterDriver``, AKS included -- not the
Azure one. Passing ``cluster.provider_plugin.slug`` there would hand a
``MemcachedDriver`` an ``AzureBlobConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astrolift_drivers import registry as _registry_module
from astrolift_drivers.registry import DriverNotFound, PluginRegistry

IN_CLUSTER_PLUGIN = "k8s_native"
"""The one plugin a cloud-hosted cluster may borrow managed-service drivers
from. Its drivers talk to the tenant cluster through the cluster driver the
cluster already has, so they need nothing the cloud plugins cannot supply."""


def managed_role(kind: str, variant: str = "") -> str:
    """The synthetic registry role a managed-service driver is filed under.

    Kept here so the ``managed:<kind>:<variant>`` spelling has one owner --
    ``plugin_loader`` writes these keys and this module reads them.
    """
    return f"managed:{kind}:{variant}"


@dataclass(frozen=True)
class ResolvedManagedDriver:
    """A managed-service driver plus the plugin it was resolved from."""

    plugin_slug: str
    """The plugin that owns the driver. Config building keys off *this*, not
    off the cluster's plugin -- they differ whenever the fallback fired."""

    driver_cls: Any
    role: str

    @property
    def is_in_cluster(self) -> bool:
        return self.plugin_slug == IN_CLUSTER_PLUGIN


def resolution_order(
    cluster_plugin_slug: str,
    kind: str,
    variant: str = "",
) -> tuple[tuple[str, str], ...]:
    """The ``(plugin_slug, role)`` pairs tried, in order. See module docstring."""
    exact = managed_role(kind, variant)
    default = managed_role(kind, "")
    borrows_in_cluster = cluster_plugin_slug != IN_CLUSTER_PLUGIN

    order: list[tuple[str, str]] = [(cluster_plugin_slug, exact)]
    if borrows_in_cluster:
        order.append((IN_CLUSTER_PLUGIN, exact))
    if default != exact:
        order.append((cluster_plugin_slug, default))
        if borrows_in_cluster:
            order.append((IN_CLUSTER_PLUGIN, default))
    return tuple(order)


def resolve_managed_driver(
    *,
    cluster_plugin_slug: str,
    kind: str,
    variant: str = "",
    registry: PluginRegistry | None = None,
) -> ResolvedManagedDriver:
    """Resolve the driver for ``(kind, variant)`` on a cluster running
    ``cluster_plugin_slug``.

    Raises :class:`DriverNotFound` naming every candidate that was tried, so a
    failure says which plugin was expected to carry the variant rather than
    only that one lookup missed.
    """
    # Read the singleton off the module rather than binding it at import: the
    # loader mutates it after this module is imported, and tests swap it out.
    reg = registry if registry is not None else _registry_module.plugins
    order = resolution_order(cluster_plugin_slug, kind, variant)

    for plugin_slug, role in order:
        try:
            driver_cls = reg.get(plugin_slug, role)
        except DriverNotFound:
            continue
        return ResolvedManagedDriver(plugin_slug=plugin_slug, driver_cls=driver_cls, role=role)

    tried = ", ".join(f"{plugin_slug}/{role}" for plugin_slug, role in order)
    raise DriverNotFound(
        f"no managed-service driver for kind={kind!r} variant={variant!r} "
        f"on a {cluster_plugin_slug!r} cluster (tried: {tried})",
    )
