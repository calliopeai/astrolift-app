"""Email-observability driver resolution for the email-detail surface.

The GraphQL resolver for the email-detail page picks a driver instance
based on the ``ManagedService.app_environment.tenant_cluster.provider_plugin.slug``.
Each per-cloud driver in ``backend/providers/{aws,gcp,azure}/managed/``
implements :class:`_sdk.email.EmailObservabilityDriver`; this module
wires the lookup so the resolver doesn't dance with the plugin registry
directly.

Why a per-slug lookup instead of going through the existing plugin
registry: the plugin registry binds **infra** drivers (``cluster``,
``dns``, ``tls``, …) by role name. The managed-service entries are
serialized as ``managed:<kind>:<variant>`` keys but those map to the
**lifecycle** driver (provision/update/deprovision), not the email
observability surface. Cloning that machine for a sibling-role surface
would mean changing the plugin manifest shape; the per-slug map below
is the same idea localized to email so we don't churn the plugin
contract.

The factory is read-only — instances are stateless once constructed,
and the AWS driver re-uses one boto3 client per region. Caching one
instance per (slug, region) keeps the resolver path warm without
holding cluster-scoped state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from _sdk import EmailObservabilityDriver


@dataclass(frozen=True)
class _CacheKey:
    slug: str
    region: str


_CACHE: dict[_CacheKey, EmailObservabilityDriver] = {}
_LOCK = threading.Lock()


def driver_for_plugin_slug(
    *,
    plugin_slug: str,
    region: str,
) -> EmailObservabilityDriver:
    """Resolve the observability driver for one plugin slug + region.

    Caches instances per ``(slug, region)`` so the resolver path doesn't
    re-build a fresh boto3 client on every page load. The cache is
    process-local; a Django reload starts fresh.

    Raises :class:`LookupError` when the slug is unknown — the resolver
    catches this and returns an envelope with an "unsupported on this
    cloud" hint.
    """
    key = _CacheKey(slug=plugin_slug, region=region)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        driver = _build_driver(slug=plugin_slug, region=region)
        _CACHE[key] = driver
        return driver


def reset_driver_cache() -> None:
    """Test helper — clears the per-(slug, region) cache."""
    with _LOCK:
        _CACHE.clear()


def _build_driver(*, slug: str, region: str) -> EmailObservabilityDriver:
    if slug == "aws":
        from aws.managed.email_ses import SESEmailConfig
        from aws.managed.email_ses_obs import AmazonSESObservabilityDriver

        return AmazonSESObservabilityDriver(
            config=SESEmailConfig(region=region or "us-east-1"),
        )
    if slug == "gcp":
        from gcp.managed.email_obs import GcpEmailObservabilityDriver

        return GcpEmailObservabilityDriver()
    if slug == "azure":
        from azure.managed.email_obs import AzureAcsEmailObservabilityDriver

        return AzureAcsEmailObservabilityDriver()

    raise LookupError(
        f"no email-observability driver registered for plugin slug {slug!r} "
        "(supported: aws, gcp, azure; k8s_native is in-cluster only and "
        "doesn't ship a managed email backend)"
    )


def register_driver_override(*, slug: str, region: str, driver: EmailObservabilityDriver) -> None:
    """Test seam — register a stub driver under one (slug, region) key.

    Used by the resolver tests so they don't need to round-trip through
    moto for read-only operations that already have full unit coverage
    in the providers test suite. Production code never calls this; the
    cache is keyed on the same tuple so the override flows through the
    same path.
    """
    with _LOCK:
        _CACHE[_CacheKey(slug=slug, region=region)] = driver


# Re-export type for typing-only consumers.
__all__ = [
    "driver_for_plugin_slug",
    "register_driver_override",
    "reset_driver_cache",
]


# Silence "Any" unused in case the module gets static analysis later.
_ = Any
