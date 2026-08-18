"""
Provider-plugin discovery + loader.

Walks the ``astrolift.providers`` entry-point group, imports each plugin
module, adapts its ``_sdk.base.ProviderPlugin`` manifest to the in-process
``astrolift_drivers.registry.PluginManifest`` shape, and registers it with
the singleton ``plugins`` registry.

Loader runs once, at Django boot, from ``AstroliftClustersConfig.ready()``.
DB-side ``ProviderPlugin`` row seeding is intentionally NOT done here —
``apps.ready()`` runs before migrations on a fresh database, so anything
touching ORM models has to live in a management command. See
``astrolift_clusters/management/commands/bootstrap_provider_plugins.py``.

Failure policy: a plugin that fails to import (missing optional cloud
SDK, broken module) is logged and skipped, never fatal. The control
plane stays up; the missing plugin just isn't selectable on cluster
registration.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any

from astrolift_drivers.managed_resolution import managed_role
from astrolift_drivers.registry import PluginManifest, plugins

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "astrolift.providers"


def _adapt(plugin_id: str, plugin_obj: Any) -> PluginManifest:
    """Translate ``_sdk.base.ProviderPlugin`` -> backend ``PluginManifest``.

    The two manifests overlap but aren't identical:
      - SDK uses ``id``; backend uses ``plugin_id``.
      - SDK has ``managed_service_drivers`` (kind/variant tuple -> class);
        backend's ``PluginManifest`` doesn't carry that today, so we surface
        each managed-service driver as a synthetic role name
        ``managed:<kind>:<variant>`` in the driver dict. That keeps every
        capability addressable through the same ``plugins.get(id, role)``
        path the control plane already uses; the catalog code can split
        them back apart if it cares. Callers go through
        ``astrolift_drivers.managed_resolution.resolve_managed_driver``
        rather than ``plugins.get`` directly, because a cloud-hosted
        cluster also reaches the ``k8s_native`` drivers (#1484).
      - SDK has no version field; we default to "0.0.0" and let the
        DB-side row carry whatever the operator stamps.
    """
    drivers: dict[str, Any] = dict(plugin_obj.drivers)
    for (kind, variant), driver_cls in getattr(plugin_obj, "managed_service_drivers", {}).items():
        drivers[managed_role(kind, variant)] = driver_cls

    capabilities = tuple(sorted(plugin_obj.drivers.keys()))

    return PluginManifest(
        plugin_id=plugin_id,
        display_name=plugin_obj.display_name,
        version="0.0.0",
        drivers=drivers,
        capabilities=capabilities,
    )


def discover_and_register() -> list[str]:
    """Idempotently load every astrolift.providers entry point.

    Returns the list of plugin_ids that registered successfully. Safe to
    call more than once: plugins already in the registry are skipped
    rather than re-registered (the registry raises on dup, which we
    swallow here because Django's autoreload re-imports apps).
    """
    loaded: list[str] = []

    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        # importlib.metadata raises in pathological install layouts (e.g.
        # zipped site-packages on some Python builds). Don't take the
        # control plane down with us.
        logger.exception("provider plugin discovery: entry_points() failed")
        return loaded

    for ep in eps:
        plugin_id = ep.name
        try:
            plugin_obj = ep.load()
        except Exception:
            logger.exception(
                "provider plugin %r failed to import (entry point: %s)",
                plugin_id,
                ep.value,
            )
            continue

        # _sdk.base.ProviderPlugin uses `id`, not `plugin_id`. Trust the
        # entry-point key when it disagrees — operators see the entry-point
        # name in pyproject.toml, not the dataclass field.
        manifest_id = getattr(plugin_obj, "id", plugin_id)
        if manifest_id != plugin_id:
            logger.warning(
                "provider plugin entry-point name %r differs from manifest id %r; using entry-point name",
                plugin_id,
                manifest_id,
            )

        try:
            manifest = _adapt(plugin_id, plugin_obj)
        except Exception:
            logger.exception("provider plugin %r failed manifest adaptation", plugin_id)
            continue

        try:
            plugins.register(manifest)
        except ValueError:
            # Already registered (Django autoreload). Idempotent: log + skip.
            logger.debug("provider plugin %r already registered, skipping", plugin_id)
            loaded.append(plugin_id)
            continue
        except Exception:
            logger.exception("provider plugin %r failed registry insert", plugin_id)
            continue

        loaded.append(plugin_id)
        logger.info("provider plugin %r registered (%d drivers)", plugin_id, len(manifest.drivers))

    return loaded
