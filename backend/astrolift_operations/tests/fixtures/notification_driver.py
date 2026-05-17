"""In-memory NotificationDriver implementing the canonical
``_sdk.notification.NotificationDriver`` Protocol (#519).

Replaces the production-shipped ``MemoryNotificationDriver`` that
lived in ``notification_dispatch`` before #519. This implementation
is **test-only** — the dispatcher's production driver resolver does
not know about this class; tests install it via
:func:`astrolift_operations.notification_dispatch.set_driver_override_for_tests`.

Records every send into ``self.sent`` so assertions can introspect
the fan-out without mocking transport. Each entry is a
``(registration_id, platform, payload)`` tuple — the same triple the
pre-#519 in-memory recorder used so test assertions port over
verbatim.

Status injection hooks let tests drive the dispatcher's
status-handling branches deterministically:

* ``force_invalid_token`` — registration_id → returns
  ``SendResult(status='invalid_token')`` so the dispatcher's
  stale-token soft-delete path can be exercised. Replaces the
  pre-#519 ``force_stale``.
* ``force_failed`` — registration_id → returns
  ``SendResult(status='failed', retriable=False)``. Pre-#519's
  ``force_drop`` mapped to a non-retriable failure; this hook is
  the SDK-shaped equivalent.
* ``force_rate_limited`` — registration_id → returns
  ``SendResult(status='rate_limited', retriable=True)``. Pre-#519's
  ``force_retry`` mapped to a transient failure.

Unsupported targets (email/sms/webhook) return ``unsupported`` so
the dispatcher's per-target multiplex logic can also be tested.
"""

from __future__ import annotations

from datetime import UTC, datetime

from _sdk.notification import (
    DeliveryTarget,
    DeviceRegistration,
    NotificationPayload,
    ProviderHealth,
    PushTarget,
    RegisterDeviceRequest,
    SendResult,
)


class MemoryNotificationDriver:
    """In-process driver used in tests.

    ``name="memory"`` matches the ``DeviceRegistration.driver`` slug
    used by the device-registration fixture so registered devices
    flow cleanly through the dispatcher when this driver is the
    test override.
    """

    name = "memory"

    def __init__(self) -> None:
        # Pre-#519 the recorder stored (device_token, platform,
        # internal-payload). Keep the same triple shape so test
        # assertions read identically; element 2 is now the canonical
        # ``_sdk.notification.NotificationPayload``.
        self.sent: list[tuple[str, str, NotificationPayload]] = []
        # Status-injection hooks keyed by registration_id.
        self.force_invalid_token: set[str] = set()
        self.force_failed: set[str] = set()
        self.force_rate_limited: set[str] = set()
        # When True, ``healthcheck`` returns ``ok=False`` — used by
        # the operator-banner test to assert the dispatcher surfaces
        # driver-side outages instead of swallowing them.
        self.healthy: bool = True

    # ---- registration ----------------------------------------------

    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> DeviceRegistration:
        # Echo the token as the registration_id — the dispatcher's
        # device row uses ``device_token`` as the SDK handle for the
        # in-memory case.
        return DeviceRegistration(
            registration_id=request.device_token,
            user_id=request.user_id,
            device_token=request.device_token,
            platform=request.platform,
            label=request.label,
            provider_metadata=dict(request.provider_metadata),
        )

    def revoke_device(self, *, registration_id: str) -> None:
        # No-op — the dispatcher already soft-deletes the DB row when
        # this driver returns ``invalid_token``. The fixture doesn't
        # need to track revoked ids separately.
        return None

    # ---- send ------------------------------------------------------

    def send(
        self,
        *,
        target: DeliveryTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        if not isinstance(target, PushTarget):
            # Tests can drop email/sms/webhook targets through to
            # cover the dispatcher's per-target branching. The
            # production dispatcher only emits PushTarget today;
            # this branch keeps the fixture protocol-complete.
            return SendResult(
                target=target,
                status="unsupported",
                error="memory driver only supports push targets",
            )

        registration_id = target.registration_id
        platform_value = target.platform.value if hasattr(target.platform, "value") else str(target.platform)
        self.sent.append((registration_id, platform_value, payload))

        if registration_id in self.force_invalid_token:
            return SendResult(
                target=target,
                status="invalid_token",
                error="forced-invalid-token (test)",
            )
        if registration_id in self.force_failed:
            return SendResult(
                target=target,
                status="failed",
                error="forced-failed (test)",
                retriable=False,
            )
        if registration_id in self.force_rate_limited:
            return SendResult(
                target=target,
                status="rate_limited",
                error="forced-rate-limited (test)",
                retriable=True,
            )
        return SendResult(
            target=target,
            status="delivered",
            provider_message_id=f"mem-{len(self.sent)}",
        )

    def send_bulk(
        self,
        *,
        targets: list[DeliveryTarget],
        payload: NotificationPayload,
    ) -> list[SendResult]:
        return [self.send(target=t, payload=payload) for t in targets]

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(
            ok=self.healthy,
            message="memory driver" if self.healthy else "memory driver: forced unhealthy (test)",
            checked_at=datetime.now(UTC).isoformat(),
        )
