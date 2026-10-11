"""Operator-only exact install SMTP support, intentional send and safe history."""

from datetime import datetime

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType, failure, success
from astrolift_graphql.pagination import PageType
from astrolift_operations import install_alert_mail as delivery
from astrolift_operations.install_alert_smtp import AlertMailUnavailable
from astrolift_operations.scopes import org_scope
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import PermissionDenied, require_permission


@strawberry.type
class InstallAlertMailTest:
    id: GUID
    request_id: GUID
    version: int
    event_kind: str
    transport: str
    sender: str
    recipient: str
    status: str
    reason_code: str | None
    created_at: datetime
    accepted_at: datetime | None
    delivery_observed: bool


def projected(row):
    return InstallAlertMailTest(
        id=GUID(str(row.guid)),
        request_id=GUID(str(row.request_id)),
        version=row.version,
        event_kind=row.event_kind,
        transport="smtp",
        sender=row.sender,
        recipient=row.recipient,
        status=row.status,
        reason_code=row.reason_code or None,
        created_at=row.created_at,
        accepted_at=row.accepted_at,
        delivery_observed=False,
    )


@strawberry.type
class InstallAlertMailSupport:
    allowed: bool
    reason: str | None
    transport: str | None
    sender: str | None
    recipient: str | None
    tls_mode: str | None
    source_fingerprint: str | None
    checked_at: datetime


@strawberry.input
class SendInstallAlertMailTestInput:
    request_id: GUID
    expected_source_fingerprint: str
    event_kind: str = "deploy.failed"


@strawberry.type
class InstallAlertMailQuery:
    @strawberry.field
    @require_permission(delivery.GATE, scope=org_scope(delivery.GATE))
    @tenant_scoped()
    def install_alert_mail_support(
        self, info: Info, event_kind: str = "deploy.failed"
    ) -> InstallAlertMailSupport:
        from django.utils import timezone

        _, actor = delivery.admitted(info)
        try:
            source = delivery.observed_source(actor, event_kind)
        except AlertMailUnavailable as exc:
            return InstallAlertMailSupport(
                allowed=False,
                reason=str(exc),
                transport=None,
                sender=None,
                recipient=None,
                tls_mode=None,
                source_fingerprint=None,
                checked_at=timezone.now(),
            )
        return InstallAlertMailSupport(
            allowed=True,
            reason=None,
            transport="smtp",
            sender=source.sender,
            recipient=source.recipient,
            tls_mode="implicit" if source.use_ssl else "starttls",
            source_fingerprint=source.fingerprint,
            checked_at=timezone.now(),
        )

    @strawberry.field
    @require_permission(delivery.GATE, scope=org_scope(delivery.GATE))
    @tenant_scoped()
    def install_alert_mail_tests_page(
        self, info: Info, event_kind: str = "deploy.failed", after: str | None = None, limit: int = 25
    ) -> PageType[InstallAlertMailTest]:
        return delivery.history(info, event_kind=event_kind, after=after, limit=limit).map(projected)


@strawberry.type
class InstallAlertMailMutation:
    @strawberry.field
    @mutation_audit(
        action="install_alert_mail.test",
        extras=lambda result: {"test_id": str(result.data.id), "status": result.data.status}
        if result.ok and result.data
        else None,
    )
    @require_permission(delivery.GATE, scope=org_scope(delivery.GATE))
    @tenant_scoped()
    def send_install_alert_mail_test(
        self, info: Info, input: SendInstallAlertMailTestInput
    ) -> MutationResultType[InstallAlertMailTest]:
        try:
            row = delivery.send_test(
                info,
                request_id=input.request_id,
                expected_source=input.expected_source_fingerprint,
                event_kind=input.event_kind,
            )
        except AlertMailUnavailable as exc:
            return failure("PRECONDITION", str(exc))
        except PermissionDenied:
            return failure("PERMISSION_DENIED", "Install alert email authority is unavailable.")
        except Exception:
            return failure("INTERNAL", "Install alert email test is unavailable.")
        return success(projected(row))
