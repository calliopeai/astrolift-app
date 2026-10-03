"""Module constants and helper functions for the mutation package."""

from __future__ import annotations

import datetime as dt
import logging

from strawberry.types import Info

from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


def _caller_org_id() -> int | None:
    """Current tenant's organization id, or None when there's no tenant
    context. Mutations over org-owned rows MUST treat None as
    deny-by-default (not-found), never as "all rows" (#1042 / #1183).

    ``@tenant_scoped()`` only asserts a tenant context exists; it does
    NOT filter any queryset. Every mutation that fetches by slug or guid
    has to add the org constraint itself or it reads/writes cross-org.
    """
    tenant = get_current_tenant()
    return tenant.organization_id if tenant is not None else None


_BULK_APP_CAP = 20


_WEBHOOK_TEST_TIMEOUT_SECONDS = 10


def _deliver_test_webhook(
    *,
    url: str,
    secret: bytes,
    payload: dict,
    event_type: str,
    format: str = "generic",
) -> dict:
    """POST ``payload`` to ``url`` with the standard webhook headers
    + HMAC signature. Returns a result dict the caller folds into
    :class:`WebhookTestResultType`.

    Delegates to :func:`webhook_delivery.post_webhook`, which is the same
    function ``DeliverWebhookWorkflow`` uses (#1598). It used to be a second
    copy of the signing and sending logic, which meant a passing manual test
    proved nothing about real delivery -- and real delivery did not exist, so
    nobody could notice.

    Synchronous on purpose: the workflow handles retries and backoff, but a
    manual test wants the immediate verdict so the operator can wire the
    integration without leaving the UI.

    Still does *not* call ``record_delivery_outcome``. A test fire must not
    move ``failure_count`` or trip the auto-disable, and that is the one
    difference between this path and the real one.
    """
    from astrolift_operations.webhook_delivery import post_webhook

    return post_webhook(
        url=url,
        secret=secret,
        payload=payload,
        event_type=event_type,
        format=format,
        timeout_seconds=_WEBHOOK_TEST_TIMEOUT_SECONDS,
    )


def _materialize_app_log_lines(
    *,
    cluster,
    namespace: str,
    pod_name: str | None,
    container: str | None,
    tail_lines: int,
    since: dt.datetime | None,
    until: dt.datetime | None,
    authority=None,
    workload_slug: str | None = None,
):
    """Drain the cluster driver's async log generator into a list
    bounded by ``tail_lines``.

    The export resolver is a synchronous mutation but the driver
    returns an :class:`AsyncIterator` of ``PodLogLine`` instances.
    We collect with a fresh event loop and clamp to ``tail_lines``
    so a chatty pod can't blow the resolver's memory budget. The
    ``since`` / ``until`` filter runs in-process — the cluster
    driver's k8s ``--since-time=`` path is per-plugin and out of
    scope for the first cut.
    """
    import asyncio

    from core.cluster_observability import stream_app_logs

    async def _drain():
        collected: list = []
        # Ask for one more than the configured cap so the serializer
        # can honestly distinguish "cap hit" from "stream ended at
        # exactly the cap". The serializer drops the spare in either
        # case — only the truncation flag depends on it.
        gen = stream_app_logs(
            cluster=cluster,
            namespace=namespace,
            pod_name=pod_name or "",
            container=container,
            tail_lines=tail_lines + 1,
            follow=False,
            **(
                {
                    "validate": authority.check,
                    "validate_pod": lambda name: authority.check_pod(
                        name, workload_slug=workload_slug, container=container
                    ),
                }
                if authority is not None
                else {}
            ),
        )
        if authority is not None:
            from astrolift_lifecycle.preview_log_access import guarded_log_lines

            gen = guarded_log_lines(gen, authority)
        try:
            async for line in gen:
                ts = getattr(line, "timestamp", None)
                if since is not None and ts is not None and ts < since:
                    continue
                if until is not None and ts is not None and ts > until:
                    continue
                collected.append(line)
                if len(collected) >= tail_lines + 1:
                    # +1 over the cap lets the serializer detect
                    # truncation honestly.
                    break
        finally:
            try:
                await gen.aclose()
            except Exception:  # noqa: BLE001
                pass
        return collected

    try:
        loop = asyncio.new_event_loop()
        try:
            if authority is not None:
                from astrolift_lifecycle.preview_log_access import EXPORT_TIMEOUT_SECONDS

                return loop.run_until_complete(asyncio.wait_for(_drain(), EXPORT_TIMEOUT_SECONDS))
            return loop.run_until_complete(_drain())
        finally:
            loop.close()
    except RuntimeError:
        # An outer event loop is already running (rare for a sync
        # resolver; protects against pytest-asyncio harness misuse).
        return asyncio.run(_drain())


def _build_audit_export_url(*, info: Info, guid: str, token: str) -> str:
    """Build the absolute download URL for an audit export. Uses the
    incoming request to honor the public base URL when reverse-proxied
    (X-Forwarded-Host); falls back to ``PLATFORM_API_URL`` for cases
    where the resolver runs outside a request context."""
    from django.conf import settings

    base_url_setting = getattr(settings, "DJANGO_BASE_URL", None) or getattr(settings, "BASE_URL", "app/")
    base_url = (base_url_setting or "app/").lstrip("/")
    relative = f"/{base_url}audit_exports/{guid}/{token}/"

    request = getattr(info.context, "request", None) if info and info.context else None
    if request is not None:
        try:
            return request.build_absolute_uri(relative)
        except Exception:  # noqa: BLE001 — never let URL building break the mutation
            pass

    platform_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    if platform_url:
        return platform_url + relative
    return relative


def _build_app_log_export_url(*, info: Info, guid: str, token: str) -> str:
    """Build the absolute download URL for an app-log export (#483).
    Same shape as the audit-export URL builder but mounted under
    ``/app/app_log_exports/`` per :mod:`astrolift_operations.urls`."""
    from django.conf import settings

    base_url_setting = getattr(settings, "DJANGO_BASE_URL", None) or getattr(settings, "BASE_URL", "app/")
    base_url = (base_url_setting or "app/").lstrip("/")
    relative = f"/{base_url}app_log_exports/{guid}/{token}/"

    request = getattr(info.context, "request", None) if info and info.context else None
    if request is not None:
        try:
            return request.build_absolute_uri(relative)
        except Exception:  # noqa: BLE001 — never let URL building break the mutation
            pass

    platform_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    if platform_url:
        return platform_url + relative
    return relative
