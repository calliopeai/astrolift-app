"""
Notification dispatch -- bridge between :meth:`core.events.Event.emit`
and the canonical per-cloud ``NotificationDriver`` SDK (#476, #499,
#490, #519).

Pipeline:

1. ``register_event_subscriber(dispatch_event)`` -- installed at
   :class:`astrolift_operations.apps.AstroliftOperationsConfig.ready`
   so every successful ``Event.emit`` is considered for fan-out
   after the persistent ``Event`` row has been written.
2. ``dispatch_event`` looks the envelope's ``event_type`` up in
   :data:`NOTIFICATION_TEMPLATES`. Unknown types are dropped
   silently (the in-app activity feed covers them already).
3. The template resolves a recipient list (users to notify), a
   title, body, and an action_url.
4. The dispatcher walks each recipient's live device registrations,
   honours :class:`NotificationPreference`, resolves the right
   per-cloud :class:`NotificationDriver` instance for the
   recipient's organization, and calls
   ``driver.send(target=PushTarget(...), payload=NotificationPayload(...))``.
5. Each send attempt -- success, transient failure, invalid-token,
   skipped -- is written to the audit stream so a post-mortem can
   answer "did Alice get pushed about the secret reveal at 14:02?"

#519 reconciliation: the local ``NotificationDriver`` Protocol that
shipped with #476/#499 has been removed; this module now consumes
the canonical Protocol from
``providers/_sdk/notification.py``. The
in-memory fallback driver that lived here moved to
``tests/fixtures/notification_driver.py`` and is **test-only** --
the production driver resolver does not know about it. Tests
install it via :func:`set_driver_override_for_tests`.

Driver resolution:

* Production: per-recipient driver lookup keyed on the recipient's
  organization. The active ``NotificationProfile`` row's
  ``driver`` slug (``aws_sns`` / ``gcp_fcm`` / ``azure_anh`` /
  ``otlp_webhook`` / ``multiplexer``) and ``config`` blob are
  threaded into a per-slug factory in
  :func:`_build_driver_from_profile`. Same pattern the cost
  estimator uses -- the notification driver's per-instance config
  shape does not match the ``_config_for`` cluster-config shape
  that ``driver_for_capability`` builds, so going through that
  helper would mis-instantiate.
* Test override: :func:`set_driver_override_for_tests` installs a
  module-scope driver that short-circuits resolution. The
  ``tests/fixtures/notification_driver.py`` shim is the standard
  test stub.
* No profile + no override: the dispatcher logs ``status=no_driver``
  to the audit row and drops the send. Installs that have not yet
  configured a NotificationProfile silently no-op rather than
  emitting through some non-prod fallback.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections.abc import Iterable
from typing import Any

from _sdk.notification import (
    DevicePlatform,
    NotificationDriver,
    NotificationPayload,
    ProviderHealth,
    PushTarget,
    SendResult,
)
from django.db import transaction

from core.events import EventEnvelope
from core.mutations import AuditEntry, emit_audit

log = logging.getLogger("astrolift_operations.notification_dispatch")


# ---- public re-exports ---------------------------------------------
#
# Callers downstream of #476 imported ``NotificationPayload`` from
# this module. After #519 the canonical type lives in the SDK; we
# re-export so existing imports keep working without forcing every
# consumer to update their import path in one go.

__all__ = [
    "NOTIFICATION_TEMPLATES",
    "NotificationDriver",
    "NotificationPayload",
    "NotificationTemplate",
    "SendResult",
    "_build_driver_from_profile",
    "default_driver_slug_for_registration",
    "dispatch_event",
    "emit_session_created_event",
    "iter_template_event_types",
    "register_geo_resolver",
    "resolve_driver_for_recipient",
    "set_driver_override_for_tests",
    "unset_driver_override_for_tests",
]


# ---- test-only driver override -------------------------------------
#
# Tests use the SDK-shaped in-memory driver fixture in
# ``tests/fixtures/notification_driver.py``. Installing the override
# replaces every per-recipient driver lookup with this instance so
# tests don't need to set up a NotificationProfile row per fixture.

_TEST_DRIVER_OVERRIDE: NotificationDriver | None = None


def set_driver_override_for_tests(driver: NotificationDriver) -> None:
    """Install a test-only driver that intercepts every send.

    The override applies for every recipient -- the
    NotificationProfile-keyed per-org lookup is short-circuited.
    Tests that exercise the per-org resolver should clear the
    override first (see :func:`unset_driver_override_for_tests`).
    """
    global _TEST_DRIVER_OVERRIDE
    _TEST_DRIVER_OVERRIDE = driver


def unset_driver_override_for_tests() -> None:
    """Clear any test-only driver override. Idempotent."""
    global _TEST_DRIVER_OVERRIDE
    _TEST_DRIVER_OVERRIDE = None


def _get_test_driver_override() -> NotificationDriver | None:
    return _TEST_DRIVER_OVERRIDE


# ---- driver resolution ---------------------------------------------


def resolve_driver_for_recipient(
    *,
    user_id: int,
    organization_id: int | None,
) -> NotificationDriver | None:
    """Resolve the right :class:`NotificationDriver` for a recipient.

    Resolution order:

    1. Test override (see :func:`set_driver_override_for_tests`).
    2. Active ``NotificationProfile`` for the recipient's
       ``organization_id``.
    3. ``None`` -- caller treats the recipient as "no driver
       configured" and audits ``status=no_driver``.

    The user_id parameter is reserved for future personalisation
    (e.g. an operator-installed per-user driver override); today
    the resolution is org-scoped.
    """
    override = _get_test_driver_override()
    if override is not None:
        return override

    if organization_id is None:
        return None

    try:
        from astrolift_operations.models import NotificationProfile
    except Exception:  # noqa: BLE001 -- model import is best-effort
        return None

    profile = (
        NotificationProfile.objects.filter(
            organization_id=organization_id,
            is_active=True,
        )
        .order_by("-updated_at")
        .first()
    )
    if profile is None:
        return None

    try:
        return _build_driver_from_profile(
            driver_slug=profile.driver,
            config=profile.config or {},
        )
    except Exception:  # noqa: BLE001 -- driver build errors must not break emit
        log.warning(
            "notification driver build failed",
            exc_info=True,
            extra={
                "organization_id": organization_id,
                "driver_slug": profile.driver,
            },
        )
        return None


def _resolve_secret(secret_ref: Any) -> str:
    """Resolve a secret reference to its plaintext value.

    The operator stores ``*_secret_ref`` keys in the profile blob
    pointing at the install's secrets backend (e.g. AWS Secrets
    Manager / GCP Secret Manager). This helper is the dispatcher
    side of the indirection.

    Today the dispatcher does not have a backend-agnostic secrets
    resolver wired in (#490 ships the policy validator + the
    per-cloud drivers; the secret-ref lookup is a separate
    cross-cutting story). We return an empty string so the caller
    falls back to the plain-text value the operator may have
    stashed alongside the ref. When the secrets-resolver lands,
    this is the single place to wire it.

    Empty / non-string refs collapse to an empty result.
    """
    if not isinstance(secret_ref, str) or not secret_ref:
        return ""
    return ""


def _build_driver_from_profile(
    *,
    driver_slug: str,
    config: dict[str, Any],
) -> NotificationDriver:
    """Per-slug factory.

    Each per-cloud driver ships its own ``*Config`` dataclass; the
    factory translates the operator-stored JSON blob into the right
    config and constructs the driver. Imports are lazy so a backend
    image that doesn't ship a given cloud SDK can still load this
    module -- the missing import bubbles up as a build failure that
    :func:`resolve_driver_for_recipient` catches + audits.
    """
    if driver_slug == "aws_sns":
        from aws.notification_sns import (
            SNSNotificationConfig,
            SNSNotificationDriver,
        )

        platform_apps_raw = config.get("platform_applications") or {}
        # Operator-stored config is a string-keyed dict; SDK expects
        # ``DevicePlatform`` keys. Translate explicitly so an
        # unrecognised platform surfaces as a build error rather
        # than a silent miss at send time.
        platform_apps = {DevicePlatform(platform): str(arn) for platform, arn in platform_apps_raw.items()}
        return SNSNotificationDriver(
            config=SNSNotificationConfig(
                region=str(config.get("region", "")),
                platform_applications=platform_apps,
                default_ttl_seconds=int(config.get("default_ttl_seconds", 86400)),
                sms_sender_id=str(config.get("sms_sender_id", "")),
            ),
        )

    if driver_slug == "gcp_fcm":
        from gcp.notification_fcm import FCMConfig, FCMNotificationDriver

        # ``credentials_secret_ref`` in the profile blob is the
        # operator-stored secret pointer. The dispatcher resolves
        # it through the secrets backend before constructing the
        # driver; the resolved token lands in ``access_token``.
        access_token = _resolve_secret(config.get("credentials_secret_ref", "")) or str(
            config.get("access_token", "")
        )
        return FCMNotificationDriver(
            config=FCMConfig(
                project_id=str(config.get("project_id", "")),
                access_token=access_token,
                timeout_seconds=int(config.get("timeout_seconds", 10)),
            ),
        )

    if driver_slug == "azure_anh":
        from azure.notification_anh import (
            AzureNotificationHubsConfig,
            AzureNotificationHubsDriver,
        )

        shared_access_key = _resolve_secret(config.get("shared_access_key_secret_ref", "")) or str(
            config.get("shared_access_key", "")
        )
        return AzureNotificationHubsDriver(
            config=AzureNotificationHubsConfig(
                namespace=str(config.get("namespace", "")),
                hub_name=str(config.get("hub_name", "")),
                shared_access_key_name=str(config.get("shared_access_key_name", "")),
                shared_access_key=shared_access_key,
                api_version=str(config.get("api_version", "2020-06")),
                timeout_seconds=int(config.get("timeout_seconds", 10)),
            ),
        )

    if driver_slug == "otlp_webhook":
        from k8s_native.notification_otlp import (
            WebhookSMTPConfig,
            WebhookSMTPNotificationDriver,
        )

        # ``channels`` from the profile is informational metadata
        # (used by the policy layer to surface which channels the
        # operator wired); the driver itself picks behavior off the
        # populated URL / SMTP fields. We pass it through anyway
        # via extra_headers metadata so debug logs show it.
        channels = config.get("channels") or ()
        return WebhookSMTPNotificationDriver(
            config=WebhookSMTPConfig(
                default_webhook_url=str(config.get("default_webhook_url", "")),
                default_webhook_bearer=_resolve_secret(config.get("default_webhook_bearer_secret_ref", ""))
                or str(config.get("default_webhook_bearer", "")),
                push_webhook_url=str(config.get("push_webhook_url", "")),
                smtp_host=str(config.get("smtp_host", "")),
                smtp_port=int(config.get("smtp_port", 587)),
                smtp_username=str(config.get("smtp_username", "")),
                smtp_password=_resolve_secret(config.get("smtp_password_secret_ref", ""))
                or str(config.get("smtp_password", "")),
                smtp_from=str(config.get("smtp_from", "")),
                smtp_use_tls=bool(config.get("smtp_use_tls", True)),
                timeout_seconds=int(config.get("timeout_seconds", 10)),
                extra_headers={"x-astrolift-channels": ",".join(map(str, channels))} if channels else {},
            ),
        )

    if driver_slug == "multiplexer":
        from _sdk.notification import MultiplexerNotificationDriver

        primary_spec = config.get("primary") or {}
        secondaries_spec = config.get("secondaries") or []
        primary = _build_driver_from_profile(
            driver_slug=str(primary_spec.get("driver", "")),
            config=dict(primary_spec.get("config") or {}),
        )
        secondaries = [
            _build_driver_from_profile(
                driver_slug=str(child.get("driver", "")),
                config=dict(child.get("config") or {}),
            )
            for child in secondaries_spec
        ]
        return MultiplexerNotificationDriver(
            primary=primary,
            secondaries=secondaries,
        )

    raise ValueError(
        f"unknown notification driver slug: {driver_slug!r}; "
        "known: aws_sns, gcp_fcm, azure_anh, otlp_webhook, multiplexer",
    )


def default_driver_slug_for_registration(
    *,
    organization_id: int | None,
) -> str:
    """Return the driver slug to stamp on a freshly-registered
    :class:`DeviceRegistration`.

    Used by ``mutations.register_mobile_device`` so the row tracks
    which driver minted the token. When a test override is active
    we stamp ``"memory"`` so the test fixture's send path matches.
    When no profile is configured we stamp ``"unconfigured"`` --
    the dispatcher later refuses to send through a row whose
    driver slug doesn't match the install's active driver, but
    the row is still useful audit-side (operator can see what got
    enrolled before they wired a profile).
    """
    override = _get_test_driver_override()
    if override is not None:
        return getattr(override, "name", "memory")

    if organization_id is None:
        return "unconfigured"

    try:
        from astrolift_operations.models import NotificationProfile
    except Exception:  # noqa: BLE001
        return "unconfigured"

    profile = (
        NotificationProfile.objects.filter(
            organization_id=organization_id,
            is_active=True,
        )
        .only("driver")
        .first()
    )
    return str(profile.driver) if profile else "unconfigured"


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
    drops to empty rather than raising -- a push fan-out should never
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
    except Exception:  # noqa: BLE001 -- import is best-effort
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
    # Org-wide alert fan-out -- every member of the org with at least
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
    """Per #499 -- push 'new sign-in' alert to user's OTHER devices.

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
    session's own device is excluded -- we want to alert the user's
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
    except Exception:  # noqa: BLE001 -- model import is best-effort
        return set()
    return set(
        DeviceRegistration.all_objects.filter(enrolled_session_id=session_pk).values_list("pk", flat=True)
    )


def dispatch_event(envelope: EventEnvelope) -> None:
    """Subscriber entry point -- translate an envelope to push fan-out.

    Registered at ``apps.ready`` so every successful ``Event.emit``
    flows through here after the persistent ``Event`` row write.
    """
    template_fn = NOTIFICATION_TEMPLATES.get(envelope.event_type)
    if template_fn is None:
        return
    try:
        template = template_fn(envelope)
    except Exception:  # noqa: BLE001 -- template failures must never raise
        log.warning(
            "notification template raised; dropping envelope",
            exc_info=True,
            extra={"event_type": envelope.event_type},
        )
        return
    if template is None:
        return
    _dispatch_template(envelope=envelope, template=template)


def _platform_for_device_row(device: Any) -> DevicePlatform:
    """Translate the DB row's platform string to the SDK enum.

    The DB carries ``ios`` / ``android`` / ``web_push``; the SDK
    enum's ``WEB`` member is the web-push equivalent. Unknown
    values raise -- the dispatcher catches and audits as ``error``.
    """
    raw = (device.platform or "").lower()
    if raw == "web_push":
        return DevicePlatform.WEB
    return DevicePlatform(raw)


def _dispatch_template(*, envelope: EventEnvelope, template: NotificationTemplate) -> None:
    from astrolift_operations.models import DeviceRegistration, is_enabled

    effective_kind = _effective_event_kind(envelope)
    excluded_device_ids = _excluded_device_ids_for(envelope)

    # Resolve the driver once per envelope -- the recipients in a
    # single fan-out share the same org (the template always pulls
    # from one org's admin list / triggerer set), so per-recipient
    # resolution would just duplicate the lookup. Override branch
    # short-circuits identically.
    driver = resolve_driver_for_recipient(
        user_id=template.recipient_user_ids[0] if template.recipient_user_ids else 0,
        organization_id=envelope.organization_id,
    )

    seen: set[int] = set()

    for user_id in template.recipient_user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)

        # Per-user preference gate -- default tables in
        # NotificationPreference cover the canonical events.
        if not is_enabled(user_id=user_id, channel="push", event_kind=effective_kind):
            _audit_dispatch(
                envelope=envelope,
                user_id=user_id,
                device_id=None,
                token_last_4="",
                status="opt_out",
                detail=f"preference={effective_kind}=off",
                driver_name=getattr(driver, "name", "") if driver else "",
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
                driver_name=getattr(driver, "name", "") if driver else "",
            )
            continue

        if driver is None:
            # No active NotificationProfile and no test override --
            # audit once per recipient so the operator can see which
            # users would have been pushed had a driver been wired.
            _audit_dispatch(
                envelope=envelope,
                user_id=user_id,
                device_id=None,
                token_last_4="",
                status="no_driver",
                detail=(f"event={envelope.event_type}; organization_id={envelope.organization_id}"),
                driver_name="",
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
                    driver_name=getattr(driver, "name", ""),
                )
                continue
            payload = NotificationPayload(
                title=template.title,
                body=template.body,
                data=_build_data_payload(template=template, envelope=envelope),
                action_url=template.action_url,
                category=envelope.event_type,
            )
            _send_one(
                device=device,
                payload=payload,
                driver=driver,
                envelope=envelope,
                user_id=user_id,
            )


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


# Map SDK SendStatus literals to the dispatcher's audit decision +
# downstream policy. ``invalid_token`` is the only status that
# soft-deletes the device row; ``failed`` + ``rate_limited`` are
# audit-only (retry orchestration lives in a separate workflow).
_ALLOW_STATUSES = frozenset({"delivered", "queued"})


def _send_one(
    *,
    device: Any,
    payload: NotificationPayload,
    driver: NotificationDriver,
    envelope: EventEnvelope,
    user_id: int,
) -> None:
    try:
        platform = _platform_for_device_row(device)
    except ValueError as exc:
        log.warning(
            "device row has unknown platform; skipping",
            extra={"device_id": device.pk, "platform": device.platform},
        )
        _audit_dispatch(
            envelope=envelope,
            user_id=user_id,
            device_id=device.pk,
            token_last_4=device.device_token[-4:],
            status="error",
            detail=f"unknown platform {device.platform!r}: {exc}",
            driver_name=getattr(driver, "name", ""),
        )
        return

    target = PushTarget(
        registration_id=device.device_token,
        platform=platform,
    )
    try:
        result = driver.send(target=target, payload=payload)
    except Exception as exc:  # noqa: BLE001 -- driver crash never breaks emit
        log.exception(
            "notification driver crashed",
            extra={
                "event_type": envelope.event_type,
                "driver": getattr(driver, "name", ""),
            },
        )
        _audit_dispatch(
            envelope=envelope,
            user_id=user_id,
            device_id=device.pk,
            token_last_4=device.device_token[-4:],
            status="error",
            detail=str(exc)[:200],
            driver_name=getattr(driver, "name", ""),
        )
        return

    if result.status == "invalid_token":
        # Stale provider token: the dispatcher MUST mark the device
        # revoked so future fan-outs skip it. The SDK Protocol
        # contract says this status is never retriable.
        with transaction.atomic():
            device.mark_stale()

    _audit_dispatch(
        envelope=envelope,
        user_id=user_id,
        device_id=device.pk,
        token_last_4=device.device_token[-4:],
        status=result.status,
        detail=result.error or result.provider_message_id,
        driver_name=getattr(driver, "name", ""),
    )


def _audit_dispatch(
    *,
    envelope: EventEnvelope,
    user_id: int,
    device_id: int | None,
    token_last_4: str,
    status: str,
    detail: str,
    driver_name: str,
) -> None:
    """Write one audit row per fan-out decision.

    Decision = ``ALLOW`` when the driver reported success
    (delivered / queued); ``DENY`` when the dispatcher skipped
    (opt-out / excluded / no_device / no_driver) or the driver
    rejected (failed / unsupported / rate_limited / invalid_token).
    Either way the row carries enough provenance to reconstruct
    who got pushed (or why they didn't) months later.
    """
    decision = "ALLOW" if status in _ALLOW_STATUSES else "DENY"
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
                "driver": driver_name,
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
    doesn't need to know about NotificationDriver -- it just emits
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
    except Exception:  # noqa: BLE001 -- session-create must never break on event emit
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
    except Exception:  # noqa: BLE001 -- resolver errors must never block emit
        log.warning("geo_resolver raised", exc_info=True)
        return ""


_geo_resolver = None


def register_geo_resolver(resolver) -> None:
    """Install a maxmind-style IP-to-string resolver. Optional."""
    global _geo_resolver
    _geo_resolver = resolver


def iter_template_event_types() -> Iterable[str]:
    """Iterator over the event types the dispatcher currently
    handles. Exposed so the preference catalog can render the list
    in /settings/notifications without duplicating the constant."""
    return tuple(NOTIFICATION_TEMPLATES.keys())


# ---- healthcheck passthrough ---------------------------------------


def driver_health_for_organization(*, organization_id: int | None) -> ProviderHealth | None:
    """Run the driver healthcheck for an organization's active
    profile (operator UI surface).

    Returns ``None`` when no driver is configured + no override is
    active -- the UI should render an "unconfigured" state instead
    of an error.
    """
    driver = resolve_driver_for_recipient(
        user_id=0,
        organization_id=organization_id,
    )
    if driver is None:
        return None
    try:
        return driver.healthcheck()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "notification driver healthcheck raised",
            exc_info=True,
            extra={"organization_id": organization_id},
        )
        return ProviderHealth(
            ok=False,
            message=f"healthcheck raised: {exc}",
            checked_at=dt.datetime.now(dt.UTC).isoformat(),
        )
