"""Temporary apps (#2433): an app with an expiry that tears itself down.

An app whose ``expires_at`` is set is temporary. ``AppExpiryWorkflow`` (one
per app, id ``AppExpiryWorkflow-<app guid>``) holds a durable timer: it warns
the owner and the app's team 24 h and 1 h before, then on expiry archives the
app (replicas to zero, deploys suppressed) and runs ``DeregisterAppWorkflow``
with the managed services kept. Those are deleted after the organization's
grace period (default 7 days), or with the app when it was created with
``delete_data_on_expiry``. The app record is soft-deleted, so its audit trail
and history stay. ``sweep`` re-arms any app whose timer is missing.

The workflow only asks :func:`next_step` what to do; every decision reads
the row, so extending, shortening or clearing an expiry is a row write and a
``reschedule`` signal, and a worker restart changes nothing.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import re
from typing import Any

from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

MIN_TTL = dt.timedelta(minutes=15)
DEFAULT_MAX_TTL = dt.timedelta(days=90)
DEFAULT_DATA_GRACE_DAYS = 7
#: The longest a policy may allow, and the longest grace it may keep data for.
POLICY_MAX_TTL = dt.timedelta(days=366)
POLICY_MAX_GRACE_DAYS = 90

#: Warnings before expiry, longest first. Each is sent once per expiry.
WARNINGS: tuple[tuple[str, dt.timedelta], ...] = (
    ("24h", dt.timedelta(hours=24)),
    ("1h", dt.timedelta(hours=1)),
)

#: Where an app can be created from, for ``ttl_required_surfaces``.
SURFACES: dict[str, str] = {
    "chat_studio": "Chat Studio",
    "mcp": "MCP",
    "api_token": "API tokens",
}

_UNITS = {
    "m": dt.timedelta(minutes=1),
    "h": dt.timedelta(hours=1),
    "d": dt.timedelta(days=1),
    "w": dt.timedelta(weeks=1),
    "mo": dt.timedelta(days=30),
}
_DURATION = re.compile(r"^(\d{1,6})(mo|m|h|d|w)$")


class ExpiryError(ValueError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


def parse_duration(text: str) -> dt.timedelta:
    """``30m``, ``4h``, ``7d``, ``6w`` or ``3mo`` (30 days) as a timedelta."""
    match = _DURATION.match((text or "").strip().lower())
    if match is None:
        raise ExpiryError(
            f"{text!r} is not a duration: use minutes, hours, days, weeks or months (30m, 4h, 7d, 6w, 3mo)"
        )
    return int(match.group(1)) * _UNITS[match.group(2)]


def format_duration(delta: dt.timedelta) -> str:
    seconds = int(delta.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds // 60}m"


# ---------------------------------------------------------------------------
# Organization policy
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Policy:
    max_ttl: dt.timedelta = DEFAULT_MAX_TTL
    #: Members (no ``org.update``) may create temporary apps only.
    temporary_only_for_members: bool = False
    #: Surfaces an app cannot be created from without an expiry.
    ttl_required_surfaces: tuple[str, ...] = ()
    data_grace_days: int = DEFAULT_DATA_GRACE_DAYS


def policy_for(org) -> Policy:
    raw = dict(getattr(org, "temporary_app_policy", None) or {})
    try:
        return Policy(
            max_ttl=dt.timedelta(seconds=int(raw.get("max_ttl_seconds") or DEFAULT_MAX_TTL.total_seconds())),
            temporary_only_for_members=bool(raw.get("temporary_only_for_members", False)),
            ttl_required_surfaces=tuple(s for s in raw.get("ttl_required_surfaces") or () if s in SURFACES),
            data_grace_days=int(raw.get("data_grace_days", DEFAULT_DATA_GRACE_DAYS)),
        )
    except (TypeError, ValueError):
        log.warning("organization %s has an unreadable temporary_app_policy; using defaults", org.pk)
        return Policy()


def policy_json(
    *,
    max_ttl: str | None,
    temporary_only_for_members: bool,
    ttl_required_surfaces: list[str],
    data_grace_days: int,
) -> dict[str, Any]:
    """The stored form of a policy, validated. Raises ``ExpiryError``."""
    ttl = parse_duration(max_ttl) if max_ttl else DEFAULT_MAX_TTL
    if not MIN_TTL <= ttl <= POLICY_MAX_TTL:
        raise ExpiryError("maxTtl must be between 15 minutes and 366 days", field="maxTtl")
    unknown = sorted(set(ttl_required_surfaces) - set(SURFACES))
    if unknown:
        raise ExpiryError(
            f"unknown surface(s) {', '.join(unknown)}; use {', '.join(SURFACES)}", field="ttlRequiredSurfaces"
        )
    if not 0 <= data_grace_days <= POLICY_MAX_GRACE_DAYS:
        raise ExpiryError(
            f"dataGraceDays must be between 0 and {POLICY_MAX_GRACE_DAYS}", field="dataGraceDays"
        )
    return {
        "max_ttl_seconds": int(ttl.total_seconds()),
        "temporary_only_for_members": bool(temporary_only_for_members),
        "ttl_required_surfaces": sorted(set(ttl_required_surfaces)),
        "data_grace_days": int(data_grace_days),
    }


# ---------------------------------------------------------------------------
# Resolving and checking an expiry
# ---------------------------------------------------------------------------


def resolve(
    org,
    *,
    expires_in: str | None,
    expires_at: dt.datetime | None,
    now: dt.datetime | None = None,
) -> dt.datetime | None:
    """The absolute expiry from either form, inside the policy. ``None``
    when neither is given. Raises ``ExpiryError``."""
    now = now or timezone.now()
    if expires_in and expires_at:
        raise ExpiryError("give expiresIn or expiresAt, not both", field="expiresIn")
    if expires_in:
        when = now + parse_duration(expires_in)
        field = "expiresIn"
    elif expires_at:
        when = expires_at if timezone.is_aware(expires_at) else timezone.make_aware(expires_at, dt.UTC)
        field = "expiresAt"
    else:
        return None
    if when - now < MIN_TTL:
        raise ExpiryError("an app must live at least 15 minutes", field=field)
    limit = policy_for(org).max_ttl
    if when - now > limit + dt.timedelta(seconds=1):
        raise ExpiryError(
            f"this organization allows apps to live at most {format_duration(limit)}", field=field
        )
    return when


def current_surface() -> str:
    """``mcp``, ``chat_studio``, ``api_token`` or ``web`` for this call."""
    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_registry.brain_manage import _surface

    marked = _surface.get()
    if marked != "graphql":
        return marked
    return "api_token" if get_current_api_token() is not None else "web"


def _is_org_admin(org) -> bool:
    from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission

    try:
        check_permission(Permission.ORG_UPDATE, scope=PermissionScope(kind=ScopeKind.ORG, id=org.pk))
    except PermissionDenied:
        return False
    return True


def permanent_refusal(org, *, surface: str | None = None) -> str | None:
    """Why this caller may not have a permanent app here, or ``None``."""
    policy = policy_for(org)
    surface = surface or current_surface()
    if surface in policy.ttl_required_surfaces:
        return (
            f"this organization requires an expiry for apps created through {SURFACES[surface]}: "
            "set expiresIn or expiresAt"
        )
    if policy.temporary_only_for_members and not _is_org_admin(org):
        return "this organization lets members create temporary apps only: set expiresIn or expiresAt"
    return None


# ---------------------------------------------------------------------------
# Setting an expiry
# ---------------------------------------------------------------------------


def _audit(action: str, app, *, actor_user_id: int | None, **extra: Any) -> None:
    from astrolift_registry.brain_manage import audit_context
    from core.mutations import AuditEntry, emit_audit

    emit_audit(
        AuditEntry(
            actor_user_id=actor_user_id,
            organization_id=app.organization_id,
            action=action,
            decision="ALLOW",
            target_kind="RegisteredApp",
            target_id=app.slug,
            duration_ms=0,
            permissions=(),
            extra={"app": str(app.guid), **audit_context(), **extra},
        )
    )


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def set_expiry(
    app,
    expires_at: dt.datetime | None,
    *,
    actor_user_id: int | None,
    delete_data: bool | None = None,
) -> str:
    """Give ``app`` this expiry (``None`` makes it permanent), reset its
    warnings and move its timer. Returns the audited change: ``set``,
    ``extend``, ``shorten`` or ``clear``. Raises ``ExpiryError`` once the
    timer has fired. Permission and policy checks are the caller's."""
    from astrolift_registry.models import RegisteredApp

    with transaction.atomic():
        row = RegisteredApp.objects.select_for_update().get(pk=app.pk)
        if row.expiry_fired_at is not None:
            raise ExpiryError("this app has expired and is being torn down", field="appSlug")
        before = row.expires_at
        row.expires_at = expires_at
        row.expiry_warnings_sent = []
        fields = ["expires_at", "expiry_warnings_sent", "updated_at", "version"]
        if delete_data is not None:
            row.delete_data_on_expiry = bool(delete_data)
            fields.append("delete_data_on_expiry")
        row.save(update_fields=fields)
        if expires_at is None:
            change = "clear"
        elif before is None:
            change = "set"
        else:
            change = "extend" if expires_at > before else "shorten"
        action = {"extend": "app.expiry.extend", "clear": "app.expiry.clear"}.get(change, "app.expiry.set")
        _audit(
            action,
            row,
            actor_user_id=actor_user_id,
            change=change,
            expires_at_before=_iso(before),
            expires_at=_iso(expires_at),
            delete_data_on_expiry=row.delete_data_on_expiry,
        )
        guid, pk = str(row.guid), row.pk
        transaction.on_commit(lambda: arm(pk, guid))
    app.refresh_from_db()
    return change


def manifest_expires_in(raw: str) -> str | None:
    """A manifest's ``[app] expires_in``, or ``None`` (also for a manifest that
    does not parse: registration reports that on its own)."""
    if not (raw or "").strip():
        return None
    from astrolift_manifest.parser import parse_raw

    try:
        return parse_raw(raw).app_expires_in or None
    except Exception:  # noqa: BLE001 - registration records the parse failure
        return None


def started(app, *, actor_user_id: int | None) -> None:
    """A new app: audit and arm its expiry, taking a fetched manifest's
    ``[app] expires_in`` when no expiry was given (clamped to the policy)."""
    if app.expires_at is None:
        declared = (app.manifest_normalized or {}).get("app_expires_in")
        if not declared:
            return
        now = timezone.now()
        ttl = min(max(parse_duration(declared), MIN_TTL), policy_for(app.organization).max_ttl)
        app.expires_at = now + ttl
        app.save(update_fields=["expires_at", "updated_at", "version"])
    _audit(
        "app.expiry.set",
        app,
        actor_user_id=actor_user_id,
        change="set",
        expires_at=_iso(app.expires_at),
        delete_data_on_expiry=app.delete_data_on_expiry,
    )
    pk, guid = app.pk, str(app.guid)
    transaction.on_commit(lambda: arm(pk, guid))


def workflow_id(guid: str) -> str:
    return f"AppExpiryWorkflow-{guid}"


def arm(app_id: int, guid: str) -> None:
    """Wake the app's timer to re-read its row, or start it. Best-effort:
    the sweep starts any timer this misses."""
    from astrolift_workflows.client import signal_workflow, start_workflow
    from astrolift_workflows.inputs import AppExpiryInput

    if signal_workflow(workflow_id(guid), "reschedule"):
        return
    try:
        start_workflow(
            "AppExpiryWorkflow", [AppExpiryInput(registered_app_id=app_id)], workflow_id=workflow_id(guid)
        )
    except Exception:  # noqa: BLE001 - the sweep re-arms it
        log.exception("could not start the expiry timer for app %s", guid)


# ---------------------------------------------------------------------------
# The timer's decisions (run by AppExpiryWorkflow's activities)
# ---------------------------------------------------------------------------


def _live_managed_service_ids(app_id: int) -> list[int]:
    from astrolift_services.models import ManagedService

    return list(
        ManagedService.objects.filter(registered_app_id=app_id, deleted_at__isnull=True)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def next_step(app_id: int, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """What the timer does next for this app:

    * ``sleep`` ``seconds``: wait (a ``reschedule`` signal wakes it early).
    * ``fire``: the expiry is due.
    * ``teardown``: fired, the app is not torn down yet.
    * ``purge`` ``managed_service_ids``: the kept data's grace is over.
    * ``done``: nothing left, the timer ends.

    Sends a due warning on the way (once each, recorded on the row).
    """
    from astrolift_registry.models import RegisteredApp

    now = now or timezone.now()
    app = RegisteredApp.all_objects.select_related("organization").filter(pk=app_id).first()
    if app is None:
        return {"action": "done"}
    if app.expiry_fired_at is None:
        if app.deleted_at is not None or app.expires_at is None:
            return {"action": "done"}
        remaining = app.expires_at - now
        if remaining <= dt.timedelta(0):
            return {"action": "fire"}
        _send_due_warning(app, remaining)
        wake = app.expires_at
        for key, before in WARNINGS:
            if key not in (app.expiry_warnings_sent or []) and app.expires_at - before > now:
                wake = min(wake, app.expires_at - before)
        return {"action": "sleep", "seconds": max(1, int((wake - now).total_seconds()) + 1)}
    if app.deleted_at is None:
        return {
            "action": "teardown",
            "guid": str(app.guid),
            "delete_data": bool(app.delete_data_on_expiry),
        }
    if app.data_retained_until is not None:
        ids = _live_managed_service_ids(app.pk)
        if ids:
            if app.data_retained_until <= now:
                return {"action": "purge", "managed_service_ids": ids}
            return {
                "action": "sleep",
                "seconds": max(1, int((app.data_retained_until - now).total_seconds()) + 1),
            }
    return {"action": "done"}


def _send_due_warning(app, remaining: dt.timedelta) -> None:
    """The most specific due warning not yet sent; an earlier one that was
    missed (an expiry set inside its window) counts as sent with it."""
    from astrolift_registry.models import RegisteredApp

    sent = list(app.expiry_warnings_sent or [])
    due = [key for key, before in WARNINGS if remaining <= before]
    if not due or due[-1] in sent:
        return
    with transaction.atomic():
        row = RegisteredApp.objects.select_for_update().filter(pk=app.pk, expires_at=app.expires_at).first()
        if row is None or due[-1] in (row.expiry_warnings_sent or []):
            return
        row.expiry_warnings_sent = sorted(set(row.expiry_warnings_sent or []) | set(due))
        row.save(update_fields=["expiry_warnings_sent", "updated_at", "version"])
    app.expiry_warnings_sent = row.expiry_warnings_sent
    warn(row, due[-1])


def warning_recipients(app) -> list[int]:
    """Who hears about it: whoever created the app and its team's members,
    less anyone who turned the app's ``app_expiring`` alert off."""
    from astrolift_identity.models import Member
    from astrolift_operations.models import UserAlertSubscription

    ids = set(
        Member.objects.filter(
            scope_kind=Member.ScopeKind.TEAM,
            scope_id=app.team_id,
            is_active=True,
            deleted_at__isnull=True,
            user__is_active=True,
        ).values_list("user_id", flat=True)
    )
    if app.created_by_id:
        ids.add(app.created_by_id)
    muted = set(
        UserAlertSubscription.objects.filter(
            registered_app=app,
            alert_kind=UserAlertSubscription.AlertKind.APP_EXPIRING,
            enabled=False,
            deleted_at__isnull=True,
        ).values_list("user_id", flat=True)
    )
    return sorted(ids - muted)


def warn(app, key: str) -> None:
    """Tell the owner and the team in the app (notification), and through the
    alert pipeline (``app.expiring``: push, email and webhooks)."""
    from astrolift_operations.models import Notification
    from core.events import Event

    recipients = warning_recipients(app)
    when = "in 1 hour" if key == "1h" else "in 24 hours"
    title = f"{app.name} expires {when}"[:255]
    body = (
        f"The temporary app {app.slug} will be torn down at {app.expires_at:%Y-%m-%d %H:%M} UTC. "
        f"Extend it from the app page or with `astro app extend {app.slug}`."
    )
    for user_id in recipients:
        Notification.objects.create(
            organization_id=app.organization_id,
            user_id=user_id,
            kind=Notification.Kind.APP_EXPIRING,
            title=title,
            body=body,
            link=f"/apps/{app.slug}",
        )
    Event.emit(
        "app.expiring",
        payload={
            "app_slug": app.slug,
            "app_guid": str(app.guid),
            "app_name": app.name,
            "warning": key,
            "expires_at": _iso(app.expires_at),
            "recipient_user_ids": recipients,
        },
        resource_kind="app",
        resource_id=str(app.guid),
        registered_app_id=app.pk,
        organization_id=app.organization_id,
    )


def fire(app_id: int, *, now: dt.datetime | None = None) -> bool:
    """Mark the expiry fired and scale the app to zero (archive it), once.
    False when the row says it is not due (extended or cleared meanwhile)."""
    from astrolift_registry.models import RegisteredApp, Workload

    now = now or timezone.now()
    with transaction.atomic():
        app = (
            RegisteredApp.objects.select_for_update().select_related("organization").filter(pk=app_id).first()
        )
        if app is None or app.expires_at is None or app.expires_at > now:
            return False
        if app.expiry_fired_at is not None:
            return True
        app.expiry_fired_at = now
        grace = dt.timedelta(days=policy_for(app.organization).data_grace_days)
        app.data_retained_until = None if app.delete_data_on_expiry else now + grace
        fields = ["expiry_fired_at", "data_retained_until", "updated_at", "version"]
        if app.archived_at is None:
            app.archived_at = now
            fields.append("archived_at")
        app.save(update_fields=fields)
        for wl in Workload.objects.filter(registered_app=app, deleted_at__isnull=True):
            if wl.replicas:
                wl.pre_archive_replicas = wl.replicas
                wl.replicas = 0
                wl.save(update_fields=["replicas", "pre_archive_replicas", "updated_at", "version"])
    return True


def record_teardown(app_id: int, teardown: dict[str, Any] | None) -> None:
    """The ``app.expiry.fire`` audit row: what was torn down and what was kept."""
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.filter(pk=app_id).first()
    if app is None:
        return
    steps = dict((teardown or {}).get("teardown") or {})
    kept = []
    if not app.delete_data_on_expiry:
        from astrolift_services.models import ManagedService

        kept = [
            {"guid": str(guid), "kind": kind, "name": name}
            for guid, kind, name in ManagedService.objects.filter(
                registered_app_id=app.pk, deleted_at__isnull=True
            ).values_list("guid", "kind", "name")
        ]
    _audit(
        "app.expiry.fire",
        app,
        actor_user_id=None,
        expires_at=_iso(app.expires_at),
        torn_down=sorted(k for k, v in steps.items() if v.get("ok")),
        still_live=list((teardown or {}).get("still_live_resources") or []),
        kept_managed_services=kept,
        data_retained_until=_iso(app.data_retained_until),
    )


def record_purge(app_id: int, results: list[dict[str, Any]]) -> None:
    """After the grace period: the kept data is gone (or what failed)."""
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.filter(pk=app_id).first()
    if app is None:
        return
    if all(r.get("ok") for r in results) and not _live_managed_service_ids(app.pk):
        RegisteredApp.all_objects.filter(pk=app.pk).update(data_retained_until=None)
    _audit("app.expiry.purge", app, actor_user_id=None, managed_services=results)


def sweep(*, now: dt.datetime | None = None, limit: int = 500) -> int:
    """Safety net for the timers: start the timer of every temporary app
    whose workflow is not running (Temporal lost it, or arming failed).
    Returns how many were started."""
    from django.db.models import Q

    from astrolift_registry.models import RegisteredApp
    from astrolift_services.models import ManagedService
    from astrolift_workflows.client import start_workflow, workflow_run_state
    from astrolift_workflows.inputs import AppExpiryInput

    now = now or timezone.now()
    kept = ManagedService.objects.filter(deleted_at__isnull=True).values("registered_app_id")
    rows = (
        RegisteredApp.all_objects.filter(
            Q(expires_at__isnull=False, expiry_fired_at__isnull=True, deleted_at__isnull=True)
            | Q(expiry_fired_at__isnull=False, deleted_at__isnull=True)
            | Q(expiry_fired_at__isnull=False, data_retained_until__isnull=False, pk__in=kept)
        )
        .order_by("expires_at")
        .values_list("pk", "guid")[:limit]
    )
    started = 0
    for pk, guid in rows:
        try:
            state = workflow_run_state(workflow_id(str(guid)))
            # No answer is not "not running": starting replaces a running timer.
            if state is None or state.running:
                continue
            start_workflow(
                "AppExpiryWorkflow",
                [AppExpiryInput(registered_app_id=pk)],
                workflow_id=workflow_id(str(guid)),
            )
            started += 1
        except Exception:  # noqa: BLE001 - one app must not stop the sweep
            log.exception("expiry sweep could not arm app %s", guid)
    return started
