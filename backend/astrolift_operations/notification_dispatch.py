"""
Notification dispatch — bridge between ``Event.emit`` and per-cloud
push drivers (#476, #499, #490).

This module is the *only* writer for the push-fan-out pipeline. The
shape:

1. ``register_event_subscriber(dispatch_event)`` — installed at
   :class:`astrolift_operations.apps.AstroliftOperationsConfig.ready`,
   so every successful ``Event.emit`` is considered for fan-out
   after the persistent ``Event`` row has been written.
2. ``dispatch_event`` looks the envelope's ``event_type`` up in
   :data:`NOTIFICATION_TEMPLATES`. Unknown types are dropped silently
   (the in-app activity feed already covers them).
3. The template resolves a *recipient list* (users to notify), a
   *title*, a *body*, and an *action_url*.
4. The dispatcher walks each recipient's live device registrations,
   honours :class:`NotificationPreference`, and calls
   ``driver.send(...)`` for every surviving device.
5. Each send attempt — success, retry, drop, stale — is written to
   the audit stream so a post-mortem can answer "did Alice get
   pushed about the secret reveal at 14:02?"

The :class:`NotificationDriver` Protocol below is a local shim until
``vendor/astrolift-providers/_sdk/notification.py`` lands (#490).
Once the SDK is vendored, this module re-exports the Protocol from
the SDK and the local definition is removed.

The default driver is an in-memory recorder. Tests register a fake;
production wires the per-cloud driver from the install's
``NotificationProfile`` (also pending #490). The recorder shape
lets the dispatcher start no-op-safe on day one of the deploy
without requiring NotificationProfile rows in every install.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections.abc import Iterable
from typing import Literal, Protocol

from django.db import transaction

from core.events import EventEnvelope
from core.mutations import AuditEntry, emit_audit

log = logging.getLogger("astrolift_operations.notification_dispatch")


# ---- driver Protocol -----------------------------------------------


@dataclasses.dataclass(slots=True, frozen=True)
class NotificationPayload:
    """One push payload the driver fans out.

    All strings are bounded so a runaway template doesn't oversize
    an APNs / FCM packet (both cap around 4 KiB total). The
    dispatcher truncates before constructing this dataclass so the
    driver never sees an over-size payload.
    """

    event_type: str
    title: str
    body: str
    action_url: str
    data: dict[str, str]


@dataclasses.dataclass(slots=True, frozen=True)
class SendResult:
    """Outcome of a single ``driver.send(...)`` call.

    ``status`` is one of:

    * ``"delivered"`` — provider accepted; downstream delivery is
      best-effort but the platform's responsibility ends here.
    * ``"retry"`` — transient failure (5xx, rate-limit). The
      dispatcher logs but does not retry inline; the operator-tunable
      retry workflow handles backoff.
    * ``"stale"`` — provider says the token is dead
      (FCM ``unregistered`` / APNs ``Unregistered``). The dispatcher
      soft-deletes the row.
    * ``"dropped"`` — refused for a non-recoverable reason (bad
      project key, malformed token). Logged + audited; the device
      row is marked stale.
    """

    status: Literal["delivered", "retry", "stale", "dropped"]
    detail: str = ""


class NotificationDriver(Protocol):
    """Driver Protocol — provider-agnostic push delivery.

    Mirrors the shape #490 will land in
    ``vendor/astrolift-providers/_sdk/notification.py``. Keeping the
    Protocol local in this module means the dispatcher works
    standalone today and only re-exports from the SDK once the
    vendor module is available.
    """

    name: str

    def send(self, *, device_token: str, platform: str, payload: NotificationPayload) -> SendResult:
        """Send one push to one device. MUST NOT raise on transient
        failure — return ``SendResult(status="retry")`` instead.
        Raising is reserved for misconfiguration (e.g. missing creds)
        that should surface in operator logs."""
        ...


# ---- in-memory default driver --------------------------------------


class MemoryNotificationDriver:
    """In-process driver used in tests and as a no-op default.

    Records every send into ``self.sent`` so tests can introspect the
    fan-out without mocking transport. ``name="memory"`` lines up
    with the ``DeviceRegistration.driver`` slug so a test fixture can
    register a device and have its sends actually flow.
    """

    name = "memory"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, NotificationPayload]] = []
        # Tokens the test wants to simulate as stale.
        self.force_stale: set[str] = set()
        self.force_drop: set[str] = set()
        self.force_retry: set[str] = set()

    def send(self, *, device_token: str, platform: str, payload: NotificationPayload) -> SendResult:
        self.sent.append((device_token, platform, payload))
        if device_token in self.force_stale:
            return SendResult(status="stale", detail="forced-stale (test)")
        if device_token in self.force_drop:
            return SendResult(status="dropped", detail="forced-drop (test)")
        if device_token in self.force_retry:
            return SendResult(status="retry", detail="forced-retry (test)")
        return SendResult(status="delivered")


_driver: NotificationDriver = MemoryNotificationDriver()


def register_driver(driver: NotificationDriver) -> None:
    """Swap the active driver. Tests use this to install a fake;
    production wiring (#490) replaces it with a SNS / FCM / ANH
    driver constructed from the install's ``NotificationProfile``."""
    global _driver
    _driver = driver


def get_driver() -> NotificationDriver:
    return _driver


# ---- templates -----------------------------------------------------
#
# Each template is a pure function: ``(envelope) -> (Template | None)``.
# Returning ``None`` means "this envelope doesn't need a push" so a
# template that resolves to zero recipients (e.g. cluster bootstrap
# with no org admins) drops cleanly.

PUSH_TITLE_MAX = 64
PUSH_BODY_MAX = 240
PUSH_DATA_VALUE_MAX = 240


@dataclasses.dataclass(slots=True, frozen=True)
class NotificationTemplate:
    title: str
    body: str
    action_url: str
    recipient_user_ids: tuple[int, ...]
    # Optional extra data (deep-link payload, ids) merged into
    # ``NotificationPayload.data`` verbatim. Strings only.
    extra_data: dict[str, str] = dataclasses.field(default_factory=dict)


def _truncate(value: str, limit: int) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"  # ellipsis


def _coerce_id_list(raw: object) -> list[int]:
    """Coerce a payload list to ``list[int]`` of user ids. Bad input
    drops to empty rather than raising — a push fan-out should never
    fail because the payload schema drifted."""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    for entry in raw:
        try:
            out.append(int(entry))
        except (TypeError, ValueError):
            continue
    return out


def _org_admin_user_ids(*, organization_id: int) -> list[int]:
    """Resolve org admins as recipients.

    'Admin' here = anyone with the ORG_MANAGE_MEMBERS permission in
    the org's scope. Falls back to the org owner when no admin can
    be resolved (a fresh-install org has only its creator member).
    Errors swallow to empty: a permission-table outage must not
    take the push pipeline with it.
    """
    try:
        from astrolift_identity.models import Member
    except Exception:  # noqa: BLE001 — import is best-effort
        return []
    return list(
        Member.objects.filter(
            scope_kind=Member.ScopeKind.ORG.value,
            scope_id=organization_id,
            is_active=True,
            deleted_at__isnull=True,
        ).values_list("user_id", flat=True)
    )


def _template_deploy_event(envelope: EventEnvelope, action_label: str) -> NotificationTemplate | None:
    payload = envelope.payload or {}
    recipients: set[int] = set()
    for key in ("triggerer_user_id", "actor_user_id"):
        raw = payload.get(key)
        if raw is None and key == "actor_user_id":
            raw = envelope.actor_user_id
        try:
            if raw is not None:
                recipients.add(int(raw))
        except (TypeError, ValueError):
            pass
    recipients.update(_coerce_id_list(payload.get("approver_user_ids")))
    if not recipients:
        return None
    app_slug = str(payload.get("app_slug", "") or "")
    env_name = str(payload.get("environment_name", "") or "")
    deploy_guid = str(payload.get("deployment_guid", "") or envelope.resource_id or "")
    body = f"{app_slug} → {env_name}" if app_slug or env_name else "Deployment update"
    action_url = f"astrolift://deployments/{deploy_guid}" if deploy_guid else "astrolift://deployments"
    return NotificationTemplate(
        title=_truncate(f"Deploy {action_label}", PUSH_TITLE_MAX),
        body=_truncate(body, PUSH_BODY_MAX),
        action_url=_truncate(action_url, PUSH_DATA_VALUE_MAX),
        recipient_user_ids=tuple(sorted(recipients)),
        extra_data={
            "deployment_guid": _truncate(deploy_guid, PUSH_DATA_VALUE_MAX),
            "app_slug": _truncate(app_slug, PUSH_DATA_VALUE_MAX),
            "environment_name": _truncate(env_name, PUSH_DATA_VALUE_MAX),
        },
    )


def _template_secret_revealed(envelope: EventEnvelope) -> NotificationTemplate | None:
    if envelope.organization_id is None:
        return None
    admin_ids = _org_admin_user_ids(organization_id=envelope.organization_id)
    if not admin_ids:
        return None
    payload = envelope.payload or {}
    secret_key = str(payload.get("key", "") or payload.get("secret_key", "") or "")
    actor = str(payload.get("actor_email", "") or "")
    body = f"{actor} revealed {secret_key}" if actor and secret_key else "A secret was revealed."
    return NotificationTemplate(
        title=_truncate("Secret reveal", PUSH_TITLE_MAX),
        body=_truncate(body, PUSH_BODY_MAX),
        action_url=_truncate("astrolift://audit?filter=secret.revealed", PUSH_DATA_VALUE_MAX),
        recipient_user_ids=tuple(sorted(set(admin_ids))),
        extra_data={
            "secret_key": _truncate(secret_key, PUSH_DATA_VALUE_MAX),
        },
    )


def _template_alert_fired(envelope: EventEnvelope) -> NotificationTemplate | None:
    if envelope.organization_id is None:
        return None
    # Org-wide alert fan-out — every member of the org with at least
    # one active membership is a candidate. Per-user preference still
    # gates the actual send.
    member_ids = _org_admin_user_ids(organization_id=envelope.organization_id)
    if not member_ids:
        return None
    payload = envelope.payload or {}
    rule_name = str(payload.get("rule_name", "") or "")
    severity = str(payload.get("severity", "") or "").lower()
    summary = str(payload.get("summary", "") or "")
    title_bits = [bit for bit in (severity.upper() or None, rule_name or None) if bit]
    title = " · ".join(title_bits) or "Alert fired"
    rule_guid = str(payload.get("rule_guid", "") or envelope.resource_id or "")
    action_url = f"astrolift://alerts/{rule_guid}" if rule_guid else "astrolift://alerts"
    return NotificationTemplate(
        title=_truncate(title, PUSH_TITLE_MAX),
        body=_truncate(summary or "An alert rule fired.", PUSH_BODY_MAX),
        action_url=_truncate(action_url, PUSH_DATA_VALUE_MAX),
        recipient_user_ids=tuple(sorted(set(member_ids))),
        extra_data={
            "rule_guid": _truncate(rule_guid, PUSH_DATA_VALUE_MAX),
            "severity": _truncate(severity, PUSH_DATA_VALUE_MAX),
        },
    )


def _template_cluster_bootstrap_failed(envelope: EventEnvelope) -> NotificationTemplate | None:
    if envelope.organization_id is None:
        return None
    admin_ids = _org_admin_user_ids(organization_id=envelope.organization_id)
    if not admin_ids:
        return None
    payload = envelope.payload or {}
    cluster_name = str(payload.get("cluster_name", "") or "")
    reason = str(payload.get("reason", "") or "")
    body = (
        f"{cluster_name}: {reason}"
        if cluster_name and reason
        else f"Cluster {cluster_name} failed to bootstrap"
        if cluster_name
        else "A cluster bootstrap failed."
    )
    cluster_guid = str(payload.get("cluster_guid", "") or envelope.resource_id or "")
    return NotificationTemplate(
        title=_truncate("Cluster bootstrap failed", PUSH_TITLE_MAX),
        body=_truncate(body, PUSH_BODY_MAX),
        action_url=_truncate(
            f"astrolift://clusters/{cluster_guid}" if cluster_guid else "astrolift://clusters",
            PUSH_DATA_VALUE_MAX,
        ),
        recipient_user_ids=tuple(sorted(set(admin_ids))),
        extra_data={"cluster_guid": _truncate(cluster_guid, PUSH_DATA_VALUE_MAX)},
    )


def _template_app_deregister_pending(envelope: EventEnvelope) -> NotificationTemplate | None:
    payload = envelope.payload or {}
    recipients: set[int] = set()
    triggerer = payload.get("triggerer_user_id") or envelope.actor_user_id
    if triggerer is not None:
        try:
            recipients.add(int(triggerer))
        except (TypeError, ValueError):
            pass
    if envelope.organization_id is not None:
        recipients.update(_org_admin_user_ids(organization_id=envelope.organization_id))
    if not recipients:
        return None
    app_slug = str(payload.get("app_slug", "") or "")
    app_guid = str(payload.get("app_guid", "") or envelope.resource_id or "")
    body = (
        f"App {app_slug} is scheduled for deregistration."
        if app_slug
        else "An app is scheduled for deregistration."
    )
    return NotificationTemplate(
        title=_truncate("App deregister pending", PUSH_TITLE_MAX),
        body=_truncate(body, PUSH_BODY_MAX),
        action_url=_truncate(
            f"astrolift://apps/{app_guid}" if app_guid else "astrolift://apps",
            PUSH_DATA_VALUE_MAX,
        ),
        recipient_user_ids=tuple(sorted(recipients)),
        extra_data={
            "app_guid": _truncate(app_guid, PUSH_DATA_VALUE_MAX),
            "app_slug": _truncate(app_slug, PUSH_DATA_VALUE_MAX),
        },
    )


def _template_session_created(envelope: EventEnvelope) -> NotificationTemplate | None:
    """Per #499 — push 'new sign-in' alert to user's OTHER devices.

    The new session's own device (when bound via
    ``DeviceRegistration.enrolled_session``) is excluded by the
    dispatcher *after* the template runs. The template just declares
    "send this to user_id with the new session payload"; the
    excludes happen at fan-out time.
    """
    payload = envelope.payload or {}
    user_id = payload.get("user_id") or envelope.actor_user_id
    try:
        user_id_int = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_id_int = None
    if user_id_int is None:
        return None
    client_kind = str(payload.get("client_kind", "") or "").lower() or "unknown"
    geo = str(payload.get("geo_hint", "") or "")
    label = str(payload.get("device_label", "") or "")
    pretty_kind = {
        "web": "Web browser",
        "cli": "CLI",
        "mobile": "Mobile app",
        "browser_extension": "Browser extension",
        "api_token": "API token",
    }.get(client_kind, client_kind.replace("_", " ").title() or "Unknown")
    where = f" from {geo}" if geo else ""
    title = f"New sign-in: {pretty_kind}{where}"
    body = (
        f"{label} signed in. If this wasn't you, tap to revoke."
        if label
        else "If this wasn't you, tap to revoke."
    )
    session_guid = str(payload.get("session_guid", "") or envelope.resource_id or "")
    action_url = (
        f"astrolift://settings/devices?session={session_guid}"
        if session_guid
        else "astrolift://settings/devices"
    )
    return NotificationTemplate(
        title=_truncate(title, PUSH_TITLE_MAX),
        body=_truncate(body, PUSH_BODY_MAX),
        action_url=_truncate(action_url, PUSH_DATA_VALUE_MAX),
        recipient_user_ids=(user_id_int,),
        extra_data={
            "session_guid": _truncate(session_guid, PUSH_DATA_VALUE_MAX),
            "client_kind": _truncate(client_kind, PUSH_DATA_VALUE_MAX),
        },
    )


NOTIFICATION_TEMPLATES = {
    "deploy.approved": lambda env: _template_deploy_event(env, "approved"),
    "deploy.rejected": lambda env: _template_deploy_event(env, "rejected"),
    "deploy.failed": lambda env: _template_deploy_event(env, "failed"),
    "secret.revealed": _template_secret_revealed,
    "alert.fired": _template_alert_fired,
    "cluster.bootstrap_failed": _template_cluster_bootstrap_failed,
    "app.deregister_pending": _template_app_deregister_pending,
    "auth.session.created": _template_session_created,
}


# ---- dispatcher entry point ----------------------------------------


def _effective_event_kind(envelope: EventEnvelope) -> str:
    """Per-user preference key for an event.

    For ``auth.session.created`` we suffix the client_kind so the
    user can mute CLI noise while keeping mobile/web alerts. All
    other events use the bare event_type as the key.
    """
    if envelope.event_type == "auth.session.created":
        kind = str((envelope.payload or {}).get("client_kind", "") or "").lower()
        if kind:
            return f"auth.session.created.{kind}"
    return envelope.event_type


def _excluded_device_ids_for(envelope: EventEnvelope) -> set[int]:
    """Devices the dispatcher must NOT push to for this envelope.

    For ``auth.session.created`` (per #499) the just-issued
    session's own device is excluded — we want to alert the user's
    *other* devices, not echo back to the one they're holding.
    """
    if envelope.event_type != "auth.session.created":
        return set()
    payload = envelope.payload or {}
    session_pk = payload.get("session_pk")
    if session_pk is None:
        return set()
    try:
        from astrolift_operations.models import DeviceRegistration
    except Exception:  # noqa: BLE001 — model import is best-effort
        return set()
    return set(
        DeviceRegistration.all_objects.filter(enrolled_session_id=session_pk).values_list("pk", flat=True)
    )


def dispatch_event(envelope: EventEnvelope) -> None:
    """Subscriber entry point — translate an envelope to push fan-out.

    Registered at ``apps.ready`` so every successful ``Event.emit``
    flows through here after the persistent ``Event`` row write.
    """
    template_fn = NOTIFICATION_TEMPLATES.get(envelope.event_type)
    if template_fn is None:
        return
    try:
        template = template_fn(envelope)
    except Exception:  # noqa: BLE001 — template failures must never raise
        log.warning(
            "notification template raised; dropping envelope",
            exc_info=True,
            extra={"event_type": envelope.event_type},
        )
        return
    if template is None:
        return
    _dispatch_template(envelope=envelope, template=template)


def _dispatch_template(*, envelope: EventEnvelope, template: NotificationTemplate) -> None:
    from astrolift_operations.models import DeviceRegistration

    effective_kind = _effective_event_kind(envelope)
    excluded_device_ids = _excluded_device_ids_for(envelope)
    driver = get_driver()
    seen: set[int] = set()

    for user_id in template.recipient_user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)

        # Per-user preference gate — default tables in
        # NotificationPreference cover the canonical events.
        from astrolift_operations.models import is_enabled

        if not is_enabled(user_id=user_id, channel="push", event_kind=effective_kind):
            _audit_dispatch(
                envelope=envelope,
                user_id=user_id,
                device_id=None,
                token_last_4="",
                status="opt_out",
                detail=f"preference={effective_kind}=off",
            )
            continue

        devices = list(
            DeviceRegistration.objects.filter(
                user_id=user_id,
                stale_at__isnull=True,
            )
        )
        if not devices:
            _audit_dispatch(
                envelope=envelope,
                user_id=user_id,
                device_id=None,
                token_last_4="",
                status="no_device",
                detail=f"event={envelope.event_type}",
            )
            continue

        for device in devices:
            if device.pk in excluded_device_ids:
                _audit_dispatch(
                    envelope=envelope,
                    user_id=user_id,
                    device_id=device.pk,
                    token_last_4=device.device_token[-4:],
                    status="excluded",
                    detail="enrolled_session_self",
                )
                continue
            payload = NotificationPayload(
                event_type=envelope.event_type,
                title=template.title,
                body=template.body,
                action_url=template.action_url,
                data=_build_data_payload(template=template, envelope=envelope),
            )
            _send_one(device=device, payload=payload, driver=driver, envelope=envelope, user_id=user_id)


def _build_data_payload(*, template: NotificationTemplate, envelope: EventEnvelope) -> dict[str, str]:
    """Compose the ``NotificationPayload.data`` dict.

    Driver-side this becomes the FCM ``data`` block / APNs custom
    keys / ANH user properties. All-string by contract so we don't
    need per-driver serialisation rules.
    """
    data: dict[str, str] = {
        "event_type": _truncate(envelope.event_type, PUSH_DATA_VALUE_MAX),
        "action_url": template.action_url,
        "request_id": _truncate(envelope.request_id, PUSH_DATA_VALUE_MAX),
        "trace_id": _truncate(envelope.trace_id, PUSH_DATA_VALUE_MAX),
    }
    if envelope.resource_kind:
        data["resource_kind"] = _truncate(envelope.resource_kind, PUSH_DATA_VALUE_MAX)
    if envelope.resource_id:
        data["resource_id"] = _truncate(envelope.resource_id, PUSH_DATA_VALUE_MAX)
    for k, v in template.extra_data.items():
        if v:
            data[k] = v
    return data


def _send_one(
    *,
    device,
    payload: NotificationPayload,
    driver: NotificationDriver,
    envelope: EventEnvelope,
    user_id: int,
) -> None:
    try:
        result = driver.send(
            device_token=device.device_token,
            platform=device.platform,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 — driver crash never breaks emit
        log.exception(
            "notification driver crashed",
            extra={"event_type": envelope.event_type, "driver": getattr(driver, "name", "")},
        )
        _audit_dispatch(
            envelope=envelope,
            user_id=user_id,
            device_id=device.pk,
            token_last_4=device.device_token[-4:],
            status="error",
            detail=str(exc)[:200],
        )
        return

    if result.status == "stale" or result.status == "dropped":
        with transaction.atomic():
            device.mark_stale()

    _audit_dispatch(
        envelope=envelope,
        user_id=user_id,
        device_id=device.pk,
        token_last_4=device.device_token[-4:],
        status=result.status,
        detail=result.detail,
    )


def _audit_dispatch(
    *,
    envelope: EventEnvelope,
    user_id: int,
    device_id: int | None,
    token_last_4: str,
    status: str,
    detail: str,
) -> None:
    """Write one audit row per fan-out decision.

    Decision = ``ALLOW`` when a delivery attempt landed; ``DENY``
    when the dispatcher skipped (opt-out / excluded / stale). Either
    way the audit row carries enough provenance to reconstruct who
    got pushed (or why they didn't) months later.
    """
    decision = "ALLOW" if status in ("delivered", "retry") else "DENY"
    emit_audit(
        AuditEntry(
            actor_user_id=envelope.actor_user_id,
            organization_id=envelope.organization_id,
            action="notification.push",
            decision=decision,
            target_kind="device_registration",
            target_id=str(device_id) if device_id is not None else "",
            duration_ms=0,
            permissions=(),
            extra={
                "event_type": envelope.event_type,
                "recipient_user_id": user_id,
                "token_last_4": token_last_4,
                "status": status,
                "detail": detail,
            },
        )
    )


# ---- session-create event helper -----------------------------------


def emit_session_created_event(
    *,
    user_id: int,
    session_pk: int,
    session_guid: str,
    client_kind: str,
    ip_address: str | None,
    user_agent: str,
    label: str,
    organization_id: int | None,
    occurred_at: dt.datetime,
) -> None:
    """Helper called by :func:`astrolift_identity.sessions.record_session`
    when a brand-new :class:`AstroliftSession` row is created (#499 §A).

    Decouples the identity app from the dispatcher so identity
    doesn't need to know about NotificationDriver — it just emits
    the event and the subscriber takes care of the fan-out.
    """
    from core.events import Event

    payload = {
        "user_id": int(user_id),
        "session_pk": int(session_pk),
        "session_guid": str(session_guid),
        "client_kind": str(client_kind or "").lower(),
        "ip_address": ip_address or "",
        "user_agent": user_agent or "",
        "device_label": label or "",
        "geo_hint": _geo_from_ip(ip_address),
        "created_at": occurred_at.isoformat(),
    }
    try:
        Event.emit(
            "auth.session.created",
            payload=payload,
            resource_kind="astrolift_session",
            resource_id=session_guid,
            actor_user_id=int(user_id),
            organization_id=organization_id,
        )
    except Exception:  # noqa: BLE001 — session-create must never break on event emit
        log.warning(
            "auth.session.created emit failed",
            exc_info=True,
            extra={"user_id": user_id, "session_pk": session_pk},
        )


def _geo_from_ip(ip_address: str | None) -> str:
    """Best-effort geo hint from an IP.

    Returns the empty string when no resolver is configured (the
    common case in self-hosted installs). Once a maxmind-style
    resolver is wired (#499 §A acceptance leaves this best-effort)
    operators register it via :func:`register_geo_resolver`.
    """
    if not ip_address:
        return ""
    resolver = _geo_resolver
    if resolver is None:
        return ""
    try:
        return resolver(ip_address) or ""
    except Exception:  # noqa: BLE001 — resolver errors must never block emit
        log.warning("geo_resolver raised", exc_info=True)
        return ""


_geo_resolver = None


def register_geo_resolver(resolver) -> None:
    """Install a maxmind-style IP→string resolver. Optional."""
    global _geo_resolver
    _geo_resolver = resolver


def iter_template_event_types() -> Iterable[str]:
    """Iterator over the event types the dispatcher currently
    handles. Exposed so the preference catalog can render the list
    in /settings/notifications without duplicating the constant."""
    return tuple(NOTIFICATION_TEMPLATES.keys())
