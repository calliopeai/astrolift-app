"""Synthetic uptime probing for deployed apps — the outage catcher.

Periodically GETs each READY app's public health URL, records an
``AppUptimeResult``, and on an up<->down *transition* emits an
``app.down`` / ``app.recovered`` event so the notification pipeline
alerts subscribers (push + in-app now; email hangs off the same event).

Why the health path, not ``/``
------------------------------
During the 2026-07-07 ``pickup-windows-tool`` outage, ``GET /`` returned
``302 -> Cognito`` the *whole time* — the ALB auth action runs before the
upstream forward, so a probe of ``/`` would have looked healthy while the
app 504'd. We probe the app's configured HTTP health path instead: the
ALB health-checks that same path, so it's auth-bypassed and actually
reaches the pod — a true end-to-end alive check.

Why not just watch pods
-----------------------
The pod was ``1/1 Running`` throughout the outage. Pod/deploy health and
edge reachability are different failure domains (routing, target
registration, IRSA, TLS, DNS). This probes reachability.
"""

from __future__ import annotations

import logging
import time

import httpx
from django.utils import timezone

from astrolift_operations.models import AppUptimeResult
from astrolift_registry.models import RegisteredApp
from core.events import Event

log = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 8.0

# ALB gateway errors that mean the upstream is unreachable — the exact
# class today's outage fell into. A response with any other status (incl.
# an auth 302 or an app-level 4xx/500) means the app *responded*, so it's
# reachable. Kept tight + explicit so a real maintenance 503 reads as down
# but an app bug doesn't page the whole org.
DOWN_STATUSES: frozenset[int] = frozenset({502, 503, 504})


def _health_path_for(app: RegisteredApp) -> str:
    """The primary container's HTTP health path, or ``/`` as a fallback.

    Walks primary workload -> primary container; uses ``healthcheck_value``
    only when ``healthcheck_kind == http``. The fallback ``/`` is a weaker
    signal for auth-gated apps (see module docstring) but better than no
    probe for apps that declare no HTTP healthcheck.
    """
    try:
        workload = (
            app.workloads.filter(deleted_at__isnull=True, is_primary=True).first()
            or app.workloads.filter(deleted_at__isnull=True).first()
        )
        if workload is None:
            return "/"
        container = (
            workload.containers.filter(deleted_at__isnull=True, is_primary=True).first()
            or workload.containers.filter(deleted_at__isnull=True).first()
        )
        if container is None or container.healthcheck_kind != "http":
            return "/"
        path = (container.healthcheck_value or "").strip()
        if not path:
            return "/"
        return path if path.startswith("/") else f"/{path}"
    except Exception:  # noqa: BLE001 — resolution is best-effort
        return "/"


def _probe_url_for(app: RegisteredApp) -> str | None:
    """Full ``https://<managed-hostname><health-path>`` or None when the
    app has no managed hostname yet (nothing to probe)."""
    from astrolift_registry.schema.types import _compute_managed_hostname

    host = _compute_managed_hostname(app)
    if not host:
        return None
    return f"https://{host}{_health_path_for(app)}"


def classify_up(*, responded: bool, status_code: int | None) -> bool:
    """Reachability verdict. Down = no response OR an ALB gateway error."""
    if not responded:
        return False
    return status_code not in DOWN_STATUSES


def probe_app(app: RegisteredApp) -> AppUptimeResult | None:
    """Probe one app, persist the result, and emit a transition event.

    Returns the persisted ``AppUptimeResult`` (or None when the app has no
    managed hostname to probe).
    """
    url = _probe_url_for(app)
    if url is None:
        return None

    status_code: int | None = None
    detail = ""
    started = time.monotonic()
    try:
        resp = httpx.get(
            url,
            timeout=PROBE_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": "astrolift-uptime-probe/1"},
        )
        status_code = resp.status_code
        responded = True
    except Exception as exc:  # noqa: BLE001 — any transport failure = down
        responded = False
        detail = f"{type(exc).__name__}: {exc}"[:255]
    latency_ms = int((time.monotonic() - started) * 1000)

    is_up = classify_up(responded=responded, status_code=status_code)
    if not is_up and not detail:
        detail = f"HTTP {status_code}" if status_code is not None else "no response"

    # State before this probe — the last recorded result for the app.
    prev = (
        AppUptimeResult.objects.filter(registered_app=app, deleted_at__isnull=True)
        .order_by("-checked_at")
        .first()
    )
    result = AppUptimeResult.objects.create(
        registered_app=app,
        checked_at=timezone.now(),
        target_url=url,
        status_code=status_code,
        latency_ms=latency_ms,
        is_up=is_up,
        detail=detail,
    )

    # Fire on a transition, or on a first-ever observation that's already
    # down (so a permanently-broken app still pages once).
    transitioned = (prev is not None and prev.is_up != is_up) or (prev is None and not is_up)
    if transitioned:
        _emit_transition(app, result)
    return result


def _emit_transition(app: RegisteredApp, result: AppUptimeResult) -> None:
    event_type = "app.recovered" if result.is_up else "app.down"
    Event.emit(
        event_type,
        payload={
            "app_slug": app.slug,
            "app_name": app.name,
            "target_url": result.target_url,
            "status_code": result.status_code,
            "detail": result.detail,
            "latency_ms": result.latency_ms,
        },
        resource_kind="registered_app",
        resource_id=str(app.guid),
        registered_app_id=app.id,
        organization_id=app.organization_id,
    )
    log.info(
        "uptime: app %s transitioned to %s (%s)",
        app.slug,
        "up" if result.is_up else "down",
        result.detail or result.status_code,
    )


def probe_all_deployed_apps() -> dict[str, int]:
    """Probe every READY app with a managed hostname. Returns a summary."""
    apps = RegisteredApp.objects.filter(
        deleted_at__isnull=True,
        provisioning_status=RegisteredApp.ProvisioningStatus.READY,
    ).select_related("organization")

    checked = 0
    down = 0
    for app in apps:
        try:
            result = probe_app(app)
        except Exception:  # noqa: BLE001 — one bad app must not stop the sweep
            log.exception("uptime: probe_app raised for %s", app.slug)
            continue
        if result is None:
            continue
        checked += 1
        if not result.is_up:
            down += 1
    return {"checked": checked, "down": down}
