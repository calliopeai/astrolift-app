"""Bundle key reflector (#441).

The platform persists ``SecretBundle.last_known_keys`` as a thin cache
over the secrets-backend payload so the operator UI can render the
``keyCount`` field on every attachment without a per-attachment round-
trip.  Three refresh paths feed it:

* **Workflow refresh** -- the rotation activity calls
  :py:func:`refresh_bundle_known_keys` immediately after fetching values
  from the secrets backend so the cache and the materialised k8s Secret
  stay in lockstep.

* **Lazy refresh on read** -- the GraphQL resolver calls
  :py:func:`maybe_refresh_bundle_known_keys` when ``last_key_enum_at``
  is older than :py:data:`STALE_AFTER`.  Errors here are swallowed --
  a stale count is better than a 500 on the bundles list page.

* **Background sweep** -- the existing
  ``SecretBundleScheduledRefreshWorkflow`` (#365) fans out the rotation
  activity for every actively-referenced bundle on an hourly cadence;
  the cache is refreshed as a side effect.

The cache is intentionally per-bundle (not per-attachment) -- every
attachment of the same bundle projects the same key set, modulo the
attachment's ``prefix`` which is applied at materialise time and not
relevant to the count.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

log = logging.getLogger("astrolift_services.bundle_keys")


STALE_AFTER = timedelta(hours=1)
"""Lazy-refresh threshold.  A bundle whose ``last_key_enum_at`` is
older than this is re-enumerated on the next read.  Tracks the
``SecretBundleScheduledRefreshWorkflow`` cadence so the lazy path
rarely fires in practice -- it's the safety net for installs where
the scheduled workflow is disabled or hasn't run yet."""


def _set_known_keys(bundle, keys: list[str]) -> None:
    """Stamp ``last_known_keys`` + ``last_key_enum_at`` atomically.

    Sorted + de-duplicated so two consecutive refreshes with the same
    key set don't trigger spurious history rows on downstream watchers.
    """
    from astrolift_services.models import SecretBundle

    cleaned = sorted({str(k) for k in keys if k})
    now = timezone.now()
    SecretBundle.all_objects.filter(pk=bundle.pk).update(
        last_known_keys=cleaned,
        last_key_enum_at=now,
    )
    # Mirror onto the in-memory instance so callers that hold a row
    # see the refreshed values without an extra round-trip.
    bundle.last_known_keys = cleaned
    bundle.last_key_enum_at = now


def refresh_bundle_known_keys(
    bundle,
    *,
    secrets_backend: Any,
) -> list[str]:
    """Pull the bundle's key set from ``secrets_backend`` and persist
    it onto ``bundle.last_known_keys``.

    Returns the freshly-enumerated key list (already sorted).  Raises
    on backend errors -- the caller decides whether to swallow (lazy
    refresh) or bubble (workflow refresh, where a hard failure should
    surface as an activity error so the operator can investigate).

    A backend that genuinely can't enumerate keys (raises
    ``NotImplementedError`` from :py:meth:`list_keys`) is handled
    specially: the cache is *not* mutated so the prior snapshot
    survives a partial-rotate against a fall-back driver.

    A bundle whose location is outside its org's secret namespace raises
    :class:`~astrolift_dispatch.agent_secrets.SecretRefNamespaceError`
    before the store is touched (#1921): even its key names are not read.
    """
    from astrolift_dispatch.agent_secrets import SecretRefNamespaceError, unscoped_bundle_reason

    unscoped = unscoped_bundle_reason(bundle, organization=bundle.organization)
    if unscoped is not None:
        raise SecretRefNamespaceError(f"secret bundle {bundle.slug!r}: {unscoped}")
    list_keys = getattr(secrets_backend, "list_keys", None)
    if not callable(list_keys):
        # Driver predates #441's protocol bump.  Treat as
        # NotImplementedError so the cache survives.
        log.warning(
            "secrets backend %s has no list_keys -- keeping prior snapshot for bundle %s",
            type(secrets_backend).__name__,
            bundle.guid,
        )
        return list(bundle.last_known_keys or [])
    try:
        keys = list_keys(bundle.backend_ref)
    except NotImplementedError:
        log.info(
            "secrets backend %s.list_keys not implemented for bundle %s; keeping prior snapshot",
            type(secrets_backend).__name__,
            bundle.guid,
        )
        return list(bundle.last_known_keys or [])
    cleaned = sorted({str(k) for k in (keys or []) if k})
    _set_known_keys(bundle, cleaned)
    return cleaned


def maybe_refresh_bundle_known_keys(bundle) -> list[str]:
    """Lazy-refresh entry point.  Returns the current (possibly newly
    refreshed) key list.

    Skips when the cache is fresh, when the bundle has no
    ``backend_ref`` (newly created, never written), or when there's no
    active attachment binding the bundle to a cluster (no cluster to
    resolve a secrets-backend driver from).  Swallows errors -- a stale
    count is better than a 500 on the bundles list page; the operator
    can fire ``rotateSecretBundle`` to force a refresh.
    """
    if not bundle.backend_ref:
        return list(bundle.last_known_keys or [])
    last = bundle.last_key_enum_at
    if last is not None and (timezone.now() - last) < STALE_AFTER:
        return list(bundle.last_known_keys or [])
    try:
        backend = _resolve_secrets_backend_for(bundle)
    except _NoBackendAvailable as exc:
        log.debug(
            "lazy refresh for bundle %s skipped: %s",
            bundle.guid,
            exc,
        )
        return list(bundle.last_known_keys or [])
    except Exception as exc:  # noqa: BLE001 — resolver bug must not 500 the list page
        log.warning(
            "lazy refresh for bundle %s: backend resolution failed: %s -- keeping prior snapshot",
            bundle.guid,
            exc,
        )
        return list(bundle.last_known_keys or [])
    try:
        return refresh_bundle_known_keys(
            bundle,
            secrets_backend=backend,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "lazy refresh for bundle %s failed: %s -- keeping prior snapshot",
            bundle.guid,
            exc,
        )
        return list(bundle.last_known_keys or [])


def known_key_count(bundle) -> int:
    """Convenience wrapper for resolvers: returns the cached count,
    triggering a lazy refresh first."""
    return len(maybe_refresh_bundle_known_keys(bundle))


def force_refresh_bundle_known_keys(bundle) -> list[str]:
    """Eager-refresh entry point.  Bypasses the
    ``last_key_enum_at < STALE_AFTER`` short-circuit so the attach
    mutation (and other "I changed this bundle, re-enumerate now"
    callers) can populate the cache without waiting for it to age out.

    Errors are swallowed the same way :py:func:`maybe_refresh_bundle_known_keys`
    swallows them -- attach should never 500 because the secrets
    backend was momentarily unreachable.
    """
    if not bundle.backend_ref:
        return list(bundle.last_known_keys or [])
    try:
        backend = _resolve_secrets_backend_for(bundle)
    except _NoBackendAvailable as exc:
        log.debug(
            "eager refresh for bundle %s skipped: %s",
            bundle.guid,
            exc,
        )
        return list(bundle.last_known_keys or [])
    except Exception as exc:  # noqa: BLE001 — resolver bug must not fail attach
        log.warning(
            "eager refresh for bundle %s: backend resolution failed: %s -- keeping prior snapshot",
            bundle.guid,
            exc,
        )
        return list(bundle.last_known_keys or [])
    try:
        return refresh_bundle_known_keys(
            bundle,
            secrets_backend=backend,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "eager refresh for bundle %s failed: %s -- keeping prior snapshot",
            bundle.guid,
            exc,
        )
        return list(bundle.last_known_keys or [])


class _NoBackendAvailable(RuntimeError):
    """Raised internally when we can't reach a secrets backend for a
    bundle (no active attachment, no cluster bound, plugin missing the
    'secrets' driver).  Caught by the lazy-refresh path -- it just
    means we can't enumerate right now, not that anything is broken."""


def _resolve_secrets_backend_for(bundle) -> Any:
    """Walk a bundle to a concrete secrets-backend driver instance.

    A bundle isn't directly bound to a cluster -- it's bound via every
    ``AppSecretBundleRef``.  Any cluster that the bundle is referenced
    on can serve the read (the same backend is exposed on every cluster
    in the install per spec), so we pick the first one with a managed
    cluster + a 'secrets' driver-capable plugin.
    """
    if bundle.tenant_cluster_id is not None:
        from core.app_deploy import AppDeployError, driver_for_capability

        try:
            return driver_for_capability(bundle.tenant_cluster, "secrets")
        except AppDeployError as exc:
            raise _NoBackendAvailable(str(exc)) from exc

    from astrolift_services.models import AppSecretBundleRef

    ref = (
        AppSecretBundleRef.objects.filter(
            secret_bundle=bundle,
            deleted_at__isnull=True,
        )
        .select_related("app_environment__tenant_cluster__provider_plugin")
        .order_by("pk")
        .first()
    )
    if ref is None:
        raise _NoBackendAvailable("bundle has no active attachments")
    cluster = ref.app_environment.tenant_cluster
    if cluster is None:
        raise _NoBackendAvailable("attached env has no cluster bound")
    from core.app_deploy import AppDeployError, driver_for_capability

    try:
        return driver_for_capability(cluster, "secrets")
    except AppDeployError as exc:
        raise _NoBackendAvailable(str(exc)) from exc


__all__ = [
    "STALE_AFTER",
    "force_refresh_bundle_known_keys",
    "known_key_count",
    "maybe_refresh_bundle_known_keys",
    "refresh_bundle_known_keys",
]
