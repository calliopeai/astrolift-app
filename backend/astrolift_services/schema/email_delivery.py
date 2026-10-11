"""Content-free email test history and explicit, reviewed test-send intent."""

from datetime import datetime
from functools import wraps

import strawberry
from _sdk.email_delivery import EmailDeliveryUnavailable
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType, failure, success
from astrolift_graphql.pagination import PageType
from astrolift_identity.operation_context import managed_service_operation
from astrolift_services import email_delivery as delivery
from astrolift_services.scopes import managed_service_scope_by_guid
from core.decorators import tenant_scoped
from core.mutations import MutationResult, mutation_audit
from core.permissions import PermissionDenied, require_permission


def email_test_audit(*, action, extras=None):
    def decorate(fn):
        audited = mutation_audit(action=action, extras=extras)(fn)

        @wraps(audited)
        def wrapped(*args, **kwargs):
            result = audited(*args, **kwargs)
            if isinstance(result, MutationResult) and not result.ok:
                error = result.errors[0]
                message = "Email delivery test is unavailable." if error.code == "INTERNAL" else error.message
                return failure(str(error.code), message, field=error.field)
            return result

        return wrapped

    return decorate


@strawberry.type(name="EmailDeliveryTest")
class EmailDeliveryTestType:
    id: GUID
    managed_service_id: GUID
    version: int
    request_id: GUID
    sender: str
    recipient: str
    status: str
    account_id: str
    region: str
    identity: str
    transport: str
    provider_message_id: str | None
    event_tracking_configured: bool
    simulator: bool
    created_at: datetime
    accepted_at: datetime | None
    observed_at: datetime | None
    reason_code: str | None


def test_to_type(row):
    return EmailDeliveryTestType(
        id=GUID(str(row.guid)),
        managed_service_id=GUID(str(row.managed_service.guid)),
        version=row.version,
        request_id=GUID(str(row.request_id)),
        sender=row.sender,
        recipient=row.recipient,
        status=delivery.display_status(row),
        account_id=row.account_id,
        region=row.region,
        identity=row.identity,
        transport="aws_ses",
        provider_message_id=row.provider_message_id or None,
        event_tracking_configured=row.accepted_at is not None and bool(row.feedback_topic_arns),
        simulator=row.simulator,
        created_at=row.created_at,
        accepted_at=row.accepted_at,
        observed_at=row.observed_at,
        reason_code=row.reason_code or None,
    )


@strawberry.input
class SendEmailDeliveryTestInput:
    managed_service_id: GUID
    expected_version: int
    request_id: GUID
    recipient: str
    subject: str | None = None
    body: str | None = None


@strawberry.type
class EmailDeliveryTestSupport:
    allowed: bool
    reason: str | None
    service_version: int | None
    sender: str | None
    identity: str | None
    account_id: str | None
    region: str | None


@strawberry.type
class EmailDeliveryQuery:
    @strawberry.field
    @require_permission(
        *delivery.READ,
        scope=managed_service_scope_by_guid("managed_service_id", permissions=delivery.READ),
        operation=managed_service_operation("managed_service_id"),
    )
    @tenant_scoped()
    def email_delivery_tests_page(
        self, info: Info, managed_service_id: GUID, after: str | None = None, limit: int = 25
    ) -> PageType[EmailDeliveryTestType]:
        return delivery.test_history_page(info, managed_service_id, after=after, limit=limit).map(
            test_to_type
        )

    @strawberry.field
    @require_permission(
        *delivery.READ,
        scope=managed_service_scope_by_guid("managed_service_id", permissions=delivery.READ),
        operation=managed_service_operation("managed_service_id"),
    )
    @tenant_scoped()
    def email_delivery_tests(
        self, info: Info, managed_service_id: GUID, limit: int = 25
    ) -> list[EmailDeliveryTestType]:
        return [test_to_type(row) for row in delivery.test_history(info, managed_service_id, limit=limit)]

    @strawberry.field
    @require_permission(
        *delivery.READ,
        scope=managed_service_scope_by_guid("managed_service_id", permissions=delivery.READ),
        operation=managed_service_operation("managed_service_id"),
    )
    @tenant_scoped()
    def email_delivery_test_support(self, info: Info, managed_service_id: GUID) -> EmailDeliveryTestSupport:
        try:
            row = delivery._fresh(info, managed_service_id)
            config, sender, _ = delivery.source(row)
        except (PermissionDenied, delivery.EmailTestUnavailable):
            return EmailDeliveryTestSupport(
                allowed=False,
                reason="The current role or exact applied email binding does not support test sends.",
                service_version=None,
                sender=None,
                identity=None,
                account_id=None,
                region=None,
            )
        return EmailDeliveryTestSupport(
            allowed=True,
            reason=None,
            service_version=row.version,
            sender=sender,
            identity=config.identity,
            account_id=config.credential.declared_account,
            region=config.region,
        )


@strawberry.type
class EmailDeliveryMutations:
    @strawberry.field
    @email_test_audit(
        action="email_delivery_test.send",
        extras=lambda result: {"test_id": str(result.data.id), "status": result.data.status}
        if result.ok and result.data is not None
        else None,
    )
    @require_permission(
        *delivery.WRITE,
        scope=managed_service_scope_by_guid("input.managed_service_id", permissions=delivery.WRITE),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def send_email_delivery_test(
        self, info: Info, input: SendEmailDeliveryTestInput
    ) -> MutationResultType[EmailDeliveryTestType]:
        try:
            row = delivery.send_test(
                info,
                service_id=input.managed_service_id,
                expected_version=input.expected_version,
                request_id=input.request_id,
                recipient=input.recipient,
                subject=input.subject,
                body=input.body,
            )
        except (delivery.EmailTestUnavailable, EmailDeliveryUnavailable) as exc:
            return failure("PRECONDITION", str(exc), field="managedServiceId")
        except PermissionDenied:
            return failure("PERMISSION_DENIED", "Email test authority is unavailable.")
        except Exception:
            # Content-bearing native errors must not escape to the generic audit traceback.
            return failure("INTERNAL", "Email delivery test is unavailable.")
        return success(test_to_type(row))
