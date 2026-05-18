"""AWS SNS NotificationDriver (#490).

SNS plays three roles for notifications:

* **Push to mobile** via *platform applications* + *platform
  endpoints* -- the operator pre-registers an APNs (Apple Push
  Notification Service) certificate and an FCM (Firebase Cloud
  Messaging) server key as ``PlatformApplication`` resources;
  this driver creates one ``PlatformEndpoint`` per device token
  and ``Publish``-es into it. SNS handles the APNs / FCM
  hand-off natively, including token refresh + invalid-token
  pruning.
* **Email** via SNS topic subscriptions OR direct SES (we route
  email through SES because SES gives per-message tracking +
  reputation; SNS email topics are best-effort and don't expose
  per-recipient delivery state).
* **SMS** via SNS's ``Publish(PhoneNumber=...)`` direct-send mode.

This driver focuses on the *push* path (the foundation #476
asked for) and provides SMS via direct ``Publish``. For email,
the driver returns ``unsupported`` -- the dispatcher should route
email-shaped targets to SES via ``aws.managed.email_ses`` instead.
That keeps each driver narrow + delegates email back to the
managed-service driver that already exists.

Configuration:

* ``platform_applications`` maps a ``DevicePlatform`` to a
  pre-created SNS ``PlatformApplicationArn``. The driver does
  NOT create platform applications -- that requires the operator
  to hand over their APNs cert / FCM server key, an out-of-band
  step we're not in the business of automating.
* When a platform doesn't have a mapped ``PlatformApplicationArn``,
  ``register_device`` for that platform returns a registration
  that points at no ARN -- ``send`` then fails with
  ``unsupported`` for that target. This lets a partial install
  (iOS only) be valid.

Provider metadata:

The returned ``DeviceRegistration.provider_metadata`` stamps
``endpoint_arn`` (the SNS endpoint ARN to publish into). The
dispatcher round-trips it on every send so we don't pay the
``CreatePlatformEndpoint`` cost twice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from _sdk.notification import (
    DeliveryTarget,
    DevicePlatform,
    DeviceRegistration,
    EmailTarget,
    NotificationDriver,
    NotificationPayload,
    ProviderHealth,
    PushTarget,
    RegisterDeviceRequest,
    SendResult,
    SmsTarget,
    WebhookTarget,
)


@dataclass(frozen=True)
class SNSNotificationConfig:
    """Driver-instance config bound from the cluster's plugin
    config."""

    region: str
    platform_applications: dict[DevicePlatform, str] = field(
        default_factory=dict,
    )
    """Maps ``DevicePlatform`` -> pre-created SNS
    PlatformApplicationArn. Operator-supplied; the driver does
    not auto-create platform applications."""

    default_ttl_seconds: int = 86400
    """Fallback TTL for sends that don't carry one. SNS surfaces
    this as ``AWS.SNS.MOBILE.APNS.TTL`` / ``...GCM.TTL`` message
    attributes."""

    sms_sender_id: str = ""
    """Optional ``AWS.SNS.SMS.SenderID`` -- E.164-style sender
    name carriers display where supported. When empty, SNS picks
    a short code."""


class SNSNotificationDriver(NotificationDriver):
    """SNS implementation of the NotificationDriver protocol.

    Cloud calls are routed through an injectable ``sns_client`` so
    moto-based tests don't need boto3 wiring.
    """

    name = "aws_sns"

    def __init__(
        self,
        *,
        config: SNSNotificationConfig,
        sns_client: Any | None = None,
    ) -> None:
        self._config = config
        if sns_client is not None:
            self._sns = sns_client
        else:
            import boto3

            self._sns = boto3.client("sns", region_name=config.region)

    # ---- registration ----------------------------------------------

    def register_device(
        self,
        request: RegisterDeviceRequest,
    ) -> DeviceRegistration:
        platform_arn = self._config.platform_applications.get(
            request.platform,
        )
        if not platform_arn:
            # No platform app for this platform -- we still return a
            # registration so the operator-side row stores cleanly;
            # send() will surface ``unsupported`` for any target
            # pointing at this registration.
            return DeviceRegistration(
                registration_id=f"unmapped:{request.device_token}",
                user_id=request.user_id,
                device_token=request.device_token,
                platform=request.platform,
                label=request.label,
                provider_metadata={"endpoint_arn": ""},
            )

        # Custom user data lets the operator audit who an endpoint
        # belongs to. SNS treats it as opaque; we stuff a JSON
        # blob with user id + label.
        custom_data = json.dumps(
            {
                "user_id": request.user_id,
                "label": request.label,
                "registered_at": datetime.now(UTC).isoformat(),
            },
            separators=(",", ":"),
        )
        try:
            response = self._sns.create_platform_endpoint(
                PlatformApplicationArn=platform_arn,
                Token=request.device_token,
                CustomUserData=custom_data,
            )
        except Exception as exc:
            raise SNSNotificationError(
                f"create_platform_endpoint failed: {exc}",
            ) from exc

        endpoint_arn = response["EndpointArn"]
        provider_metadata = dict(request.provider_metadata)
        provider_metadata["endpoint_arn"] = endpoint_arn
        provider_metadata["platform_application_arn"] = platform_arn
        return DeviceRegistration(
            registration_id=endpoint_arn,
            user_id=request.user_id,
            device_token=request.device_token,
            platform=request.platform,
            label=request.label,
            provider_metadata=provider_metadata,
        )

    def revoke_device(self, *, registration_id: str) -> None:
        if not registration_id or registration_id.startswith("unmapped:"):
            return
        try:
            self._sns.delete_endpoint(EndpointArn=registration_id)
        except Exception:
            # Idempotent revoke -- if the endpoint is already gone we
            # don't surface that as a failure. The dispatcher only
            # cares that subsequent sends won't land.
            return

    # ---- send ------------------------------------------------------

    def send(
        self,
        *,
        target: DeliveryTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        if isinstance(target, PushTarget):
            return self._send_push(target=target, payload=payload)
        if isinstance(target, SmsTarget):
            return self._send_sms(target=target)
        if isinstance(target, (EmailTarget, WebhookTarget)):
            return SendResult(
                target=target,
                status="unsupported",
                error=(
                    "SNS driver does not handle this channel -- route "
                    "EmailTarget through aws.managed.email_ses and "
                    "WebhookTarget through k8s_native.notification_otlp"
                ),
            )
        return SendResult(
            target=target,
            status="unsupported",
            error=f"unknown target type {type(target).__name__}",
        )

    def send_bulk(
        self,
        *,
        targets: list[DeliveryTarget],
        payload: NotificationPayload,
    ) -> list[SendResult]:
        return [self.send(target=t, payload=payload) for t in targets]

    def healthcheck(self) -> ProviderHealth:
        try:
            self._sns.list_topics(NextToken="")
        except Exception as exc:
            return ProviderHealth(
                ok=False,
                message=f"sns list_topics failed: {exc}",
                checked_at=datetime.now(UTC).isoformat(),
            )
        return ProviderHealth(
            ok=True,
            message="sns reachable",
            checked_at=datetime.now(UTC).isoformat(),
        )

    # ---- internals -------------------------------------------------

    def _send_push(
        self,
        *,
        target: PushTarget,
        payload: NotificationPayload,
    ) -> SendResult:
        endpoint_arn = target.registration_id
        if not endpoint_arn or endpoint_arn.startswith("unmapped:"):
            return SendResult(
                target=target,
                status="unsupported",
                error=(f"no SNS PlatformApplication mapped for platform={target.platform.value}"),
            )
        message = self._wire_payload_for(
            platform=target.platform,
            payload=payload,
        )
        message_attrs = self._message_attrs_for(payload=payload)
        try:
            response = self._sns.publish(
                TargetArn=endpoint_arn,
                Message=message,
                MessageStructure="json",
                MessageAttributes=message_attrs,
            )
        except Exception as exc:
            error_text = str(exc)
            if "EndpointDisabled" in error_text:
                return SendResult(
                    target=target,
                    status="invalid_token",
                    error=error_text,
                    retriable=False,
                )
            if "Throttling" in error_text or "TooManyRequests" in error_text:
                return SendResult(
                    target=target,
                    status="rate_limited",
                    error=error_text,
                    retriable=True,
                )
            return SendResult(
                target=target,
                status="failed",
                error=error_text,
                retriable=True,
            )
        return SendResult(
            target=target,
            status="delivered",
            provider_message_id=response.get("MessageId", ""),
        )

    def _send_sms(self, *, target: SmsTarget) -> SendResult:
        attrs: dict[str, Any] = {}
        if self._config.sms_sender_id:
            attrs["AWS.SNS.SMS.SenderID"] = {
                "DataType": "String",
                "StringValue": self._config.sms_sender_id,
            }
        try:
            response = self._sns.publish(
                PhoneNumber=target.to,
                Message=target.message,
                MessageAttributes=attrs or {},
            )
        except Exception as exc:
            error_text = str(exc)
            return SendResult(
                target=target,
                status="failed",
                error=error_text,
                retriable="Throttling" in error_text,
            )
        return SendResult(
            target=target,
            status="delivered",
            provider_message_id=response.get("MessageId", ""),
        )

    def _wire_payload_for(
        self,
        *,
        platform: DevicePlatform,
        payload: NotificationPayload,
    ) -> str:
        """Build the SNS multi-platform JSON envelope.

        SNS expects a top-level ``default`` plus per-platform
        keys (``APNS``, ``APNS_SANDBOX``, ``GCM``, ``ADM``,
        ``WNS``). The per-platform value is itself a stringified
        JSON document of the platform's native shape (APNs
        ``aps`` dict, FCM ``message`` proto, WNS XML).
        """
        wire: dict[str, str] = {
            "default": payload.body or payload.title,
        }
        apns_dict = {
            "aps": {
                "alert": {
                    "title": payload.title,
                    "body": payload.body,
                },
                **({"badge": payload.badge} if payload.badge is not None else {}),
                **({"category": payload.category} if payload.category else {}),
                "sound": "default",
            },
            **payload.data,
            **({"action_url": payload.action_url} if payload.action_url else {}),
        }
        gcm_dict = {
            "notification": {
                "title": payload.title,
                "body": payload.body,
                **({"channel_id": payload.category} if payload.category else {}),
            },
            "data": {
                **payload.data,
                **({"action_url": payload.action_url} if payload.action_url else {}),
            },
        }
        if platform == DevicePlatform.IOS:
            wire["APNS"] = json.dumps(apns_dict, separators=(",", ":"))
            # Operators sometimes register sandbox-bound platform
            # applications -- SNS's publish call requires the
            # matching key. We stamp both so the operator's choice
            # of platform application controls which lands.
            wire["APNS_SANDBOX"] = wire["APNS"]
        elif platform == DevicePlatform.ANDROID:
            wire["GCM"] = json.dumps(gcm_dict, separators=(",", ":"))
        elif platform == DevicePlatform.WEB:
            # SNS WNS is Windows; for Web Push (Chrome/Firefox) the
            # operator must run a separate Web-Push relay. We surface
            # ``default`` text + stash data so a relay can pick it up.
            wire["GCM"] = json.dumps(gcm_dict, separators=(",", ":"))
        return json.dumps(wire, separators=(",", ":"))

    def _message_attrs_for(
        self,
        *,
        payload: NotificationPayload,
    ) -> dict[str, Any]:
        ttl = payload.ttl_seconds
        if ttl is None:
            ttl = self._config.default_ttl_seconds
        return {
            "AWS.SNS.MOBILE.APNS.TTL": {
                "DataType": "String",
                "StringValue": str(ttl),
            },
            "AWS.SNS.MOBILE.APNS_SANDBOX.TTL": {
                "DataType": "String",
                "StringValue": str(ttl),
            },
            "AWS.SNS.MOBILE.GCM.TTL": {
                "DataType": "String",
                "StringValue": str(ttl),
            },
        }


class SNSNotificationError(Exception):
    """Driver-side failure that the dispatcher should surface to the
    operator as an unrecoverable registration error (vs. a per-send
    delivery failure)."""
