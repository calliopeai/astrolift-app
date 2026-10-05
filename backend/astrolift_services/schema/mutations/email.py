"""EmailServiceMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.operation_context import managed_service_operation
from astrolift_services.models import (
    ManagedService,
)
from astrolift_services.schema.email_delivery import EmailDeliveryMutations, email_test_audit
from astrolift_services.schema.mutations.helpers import (
    _caller_org_id,
    _client_ip,
    _resolve_email_service,
)
from astrolift_services.schema.mutations.types import (
    AddEmailSuppressionEntryInput,
    CreateEmailTemplateInput,
    DeleteEmailTemplateInput,
    RemoveEmailSuppressionEntryInput,
    SendManagedServiceTestEmailInput,
    UpdateEmailTemplateInput,
    _EmailSuppressionAddPayload,
    _EmailSuppressionRemovePayload,
    _EmailTemplateDeletedPayload,
)
from astrolift_services.schema.types import (
    EmailTemplateType,
    ManagedServiceTestEmailResultType,
)
from astrolift_services.scopes import managed_service_scope_by_guid
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.permissions import Permission, PermissionDenied, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class EmailServiceMutations(EmailDeliveryMutations):
    @strawberry.field
    @email_test_audit(
        action="managed_service.test_email.send",
        extras=lambda result: (
            {
                "managed_service_id": str(result.data.managed_service_id),
                "recipient": result.data.recipient,
                "transport": result.data.transport,
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @require_permission(
        Permission.APP_UPDATE,
        Permission.MANAGED_SERVICE_UPDATE,
        scope=managed_service_scope_by_guid(
            "input.managed_service_id",
            permissions=(
                Permission.APP_UPDATE,
                Permission.MANAGED_SERVICE_UPDATE,
            ),
        ),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def send_managed_service_test_email(
        self,
        info: Info,
        input: SendManagedServiceTestEmailInput,
    ) -> MutationResultType[ManagedServiceTestEmailResultType]:
        """Compatibility entry point for the same durable, exact-source diagnostic."""
        from _sdk.email_delivery import EmailDeliveryUnavailable
        from aws.email_delivery import _mailbox

        from astrolift_services import email_delivery

        svc = ManagedService.objects.filter(
            guid=str(input.managed_service_id),
            registered_app__organization_id=_caller_org_id(),
            deleted_at__isnull=True,
        ).first()
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "managed service not found", field="managedServiceId"
            )
        if svc.kind != ManagedService.Kind.EMAIL:
            return gql_failure(
                ErrorCode.PRECONDITION.value, "An email service is required.", field="managedServiceId"
            )
        recipient = (input.recipient or "").strip()
        try:
            _mailbox(recipient)
        except EmailDeliveryUnavailable:
            return gql_failure(
                ErrorCode.VALIDATION.value, "A single valid email recipient is required.", field="recipient"
            )
        if input.expected_version is None or input.request_id is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "Review emailDeliveryTestSupport and supply expectedVersion and a stable requestId before sending.",
                field="managedServiceId",
            )
        try:
            row = email_delivery.send_test(
                info,
                service_id=input.managed_service_id,
                request_id=input.request_id,
                expected_version=input.expected_version,
                recipient=recipient,
                subject=input.subject,
                body=input.body,
            )
        except (email_delivery.EmailTestUnavailable, EmailDeliveryUnavailable) as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc), field="managedServiceId")
        except PermissionDenied:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "Email test authority is unavailable.")
        except Exception:
            return gql_failure(ErrorCode.INTERNAL.value, "Email delivery test is unavailable.")
        if row.accepted_at is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "Provider acceptance is unconfirmed; inspect emailDeliveryTests before attempting another test.",
                field="managedServiceId",
            )
        return gql_success(
            ManagedServiceTestEmailResultType(
                managed_service_id=input.managed_service_id,
                recipient=row.recipient,
                subject=(input.subject or "").strip() or "[Astrolift] Email delivery test",
                sent_at=row.accepted_at,
                transport="aws_ses",
            )
        )

    # ---- Email suppression list (#631) -------------------------------

    @strawberry.field
    @mutation_audit(
        action="managed_service.email.suppression.add",
        extras=lambda result: (
            {"address": result.data.address, "reason": result.data.reason}
            if result.ok and result.data is not None
            else None
        ),
    )
    @require_permission(
        Permission.APP_UPDATE,
        Permission.MANAGED_SERVICE_UPDATE,
        scope=managed_service_scope_by_guid(
            "input.managed_service_id",
            permissions=(
                Permission.APP_UPDATE,
                Permission.MANAGED_SERVICE_UPDATE,
            ),
        ),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def add_email_suppression_entry(
        self,
        info: Info,
        input: AddEmailSuppressionEntryInput,
    ) -> MutationResultType[_EmailSuppressionAddPayload]:
        """Add an address to the SES account-level suppression list (#631).

        Sensitive op: the audit row carries actor + IP + reason +
        operator-supplied note so the trail captures the disclosure
        source. Suppression mutations are restricted to operators with
        ``managed_service.update`` because a manual entry can mask a
        legitimate deliverability problem (operator suppresses the
        complainer rather than fixing the content).
        """
        # Lazy imports — keep the mutation module light when email
        # observability isn't reached.
        from _sdk import UnsupportedOperationError
        from _sdk.email import SuppressionReason

        from astrolift_services.email_observability import driver_for_plugin_slug

        svc = _resolve_email_service(input.managed_service_id)
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found or not an email service",
                field="managedServiceId",
            )

        address = (input.address or "").strip()
        if not address:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "address is required",
                field="address",
            )

        # Validate reason maps onto the protocol enum.
        try:
            reason = SuppressionReason(input.reason or "MANUAL")
        except ValueError:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"reason must be one of {[r.value for r in SuppressionReason]}",
                field="reason",
            )

        plugin_slug = svc.app_environment.tenant_cluster.provider_plugin.slug
        region = svc.app_environment.tenant_cluster.region or ""
        try:
            driver = driver_for_plugin_slug(
                plugin_slug=plugin_slug,
                region=region,
            )
        except LookupError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )

        try:
            entry = driver.add_suppression_entry(
                address=address,
                reason=reason,
                note=input.note or "",
            )
        except UnsupportedOperationError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )
        except Exception as exc:  # noqa: BLE001
            return gql_failure(
                ErrorCode.INTERNAL.value,
                f"{type(exc).__name__}: {exc}",
                field="managedServiceId",
            )

        # Sibling audit row carries the IP + the operator note so the
        # trail captures why the address was suppressed.
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.email.suppression.add.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "address": address,
                    "reason": entry.reason.value,
                    "note": input.note or "",
                    "plugin_slug": plugin_slug,
                    "region": region,
                    "client_ip": ip,
                },
            )
        )

        return gql_success(
            _EmailSuppressionAddPayload(
                address=entry.address,
                reason=entry.reason.value,
            )
        )

    @strawberry.field
    @mutation_audit(
        action="managed_service.email.suppression.remove",
        extras=lambda result: (
            {"address": result.data.address, "removed": result.data.removed}
            if result.ok and result.data is not None
            else None
        ),
    )
    @require_permission(
        Permission.APP_UPDATE,
        Permission.MANAGED_SERVICE_UPDATE,
        scope=managed_service_scope_by_guid(
            "input.managed_service_id",
            permissions=(
                Permission.APP_UPDATE,
                Permission.MANAGED_SERVICE_UPDATE,
            ),
        ),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def remove_email_suppression_entry(
        self,
        info: Info,
        input: RemoveEmailSuppressionEntryInput,
    ) -> MutationResultType[_EmailSuppressionRemovePayload]:
        """Remove an address from the suppression list (#631).

        Idempotent: removing an address that wasn't on the list returns
        ``ok=True`` with ``data.removed=False``. Sensitive op: audit
        row carries the actor + IP so an operator un-suppressing a
        previously bounced address can be tracked back to the source.
        """
        from _sdk import UnsupportedOperationError

        from astrolift_services.email_observability import driver_for_plugin_slug

        svc = _resolve_email_service(input.managed_service_id)
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found or not an email service",
                field="managedServiceId",
            )
        address = (input.address or "").strip()
        if not address:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "address is required",
                field="address",
            )

        plugin_slug = svc.app_environment.tenant_cluster.provider_plugin.slug
        region = svc.app_environment.tenant_cluster.region or ""
        try:
            driver = driver_for_plugin_slug(
                plugin_slug=plugin_slug,
                region=region,
            )
        except LookupError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )

        try:
            removed = driver.remove_suppression_entry(address=address)
        except UnsupportedOperationError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )
        except Exception as exc:  # noqa: BLE001
            return gql_failure(
                ErrorCode.INTERNAL.value,
                f"{type(exc).__name__}: {exc}",
                field="managedServiceId",
            )

        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.email.suppression.remove.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "address": address,
                    "removed": removed,
                    "plugin_slug": plugin_slug,
                    "region": region,
                    "client_ip": ip,
                },
            )
        )

        return gql_success(
            _EmailSuppressionRemovePayload(
                address=address,
                removed=removed,
            )
        )

    # ---- Email templates (#635) --------------------------------------

    @strawberry.field
    @mutation_audit(
        action="managed_service.email.template.create",
        extras=lambda result: {"name": result.data.name} if result.ok and result.data is not None else None,
    )
    @require_permission(
        Permission.APP_UPDATE,
        Permission.MANAGED_SERVICE_UPDATE,
        scope=managed_service_scope_by_guid(
            "input.managed_service_id",
            permissions=(
                Permission.APP_UPDATE,
                Permission.MANAGED_SERVICE_UPDATE,
            ),
        ),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def create_email_template(
        self,
        info: Info,
        input: CreateEmailTemplateInput,
    ) -> MutationResultType[EmailTemplateType]:
        """Create a new transactional-email template on the backend
        (#635).

        Sensitive op — template content can leak personal data into the
        audit trail, so the audit row carries the template name only
        (not the body). Subject + bodies are routed through the
        backend's storage path and never persisted in the platform DB."""
        from _sdk import UnsupportedOperationError

        from astrolift_services.email_observability import driver_for_plugin_slug

        svc = _resolve_email_service(input.managed_service_id)
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found or not an email service",
                field="managedServiceId",
            )
        name = (input.name or "").strip()
        if not name:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "name is required",
                field="name",
            )
        subject = (input.subject or "").strip()
        if not subject:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "subject is required",
                field="subject",
            )
        html_body = input.html_body or ""
        text_body = input.text_body or ""
        if not html_body and not text_body:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "at least one of htmlBody or textBody is required",
                field="htmlBody",
            )

        plugin_slug = svc.app_environment.tenant_cluster.provider_plugin.slug
        region = svc.app_environment.tenant_cluster.region or ""
        try:
            driver = driver_for_plugin_slug(
                plugin_slug=plugin_slug,
                region=region,
            )
        except LookupError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )

        try:
            template = driver.create_template(
                name=name,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
        except UnsupportedOperationError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )
        except Exception as exc:  # noqa: BLE001 -- cloud-side transient
            # ManagedServiceError on duplicate name surfaces as a clean
            # PRECONDITION; everything else collapses to INTERNAL.
            msg = str(exc)
            code = (
                ErrorCode.PRECONDITION.value if "already exists" in msg.lower() else ErrorCode.INTERNAL.value
            )
            return gql_failure(
                code,
                f"{type(exc).__name__}: {msg}",
                field="name",
            )

        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.email.template.create.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "name": template.name,
                    "plugin_slug": plugin_slug,
                    "region": region,
                    "client_ip": ip,
                },
            )
        )
        return gql_success(
            EmailTemplateType(
                name=template.name,
                subject=template.subject,
                html_body=template.html_body,
                text_body=template.text_body,
                created_at=template.created_at,
            )
        )

    @strawberry.field
    @mutation_audit(
        action="managed_service.email.template.update",
        extras=lambda result: {"name": result.data.name} if result.ok and result.data is not None else None,
    )
    @require_permission(
        Permission.APP_UPDATE,
        Permission.MANAGED_SERVICE_UPDATE,
        scope=managed_service_scope_by_guid(
            "input.managed_service_id",
            permissions=(
                Permission.APP_UPDATE,
                Permission.MANAGED_SERVICE_UPDATE,
            ),
        ),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def update_email_template(
        self,
        info: Info,
        input: UpdateEmailTemplateInput,
    ) -> MutationResultType[EmailTemplateType]:
        """Update an existing template (#635). Replaces every field;
        partial updates aren't portable across backends."""
        from _sdk import UnsupportedOperationError

        from astrolift_services.email_observability import driver_for_plugin_slug

        svc = _resolve_email_service(input.managed_service_id)
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found or not an email service",
                field="managedServiceId",
            )
        name = (input.name or "").strip()
        if not name:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "name is required",
                field="name",
            )
        subject = (input.subject or "").strip()
        if not subject:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "subject is required",
                field="subject",
            )
        html_body = input.html_body or ""
        text_body = input.text_body or ""
        if not html_body and not text_body:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "at least one of htmlBody or textBody is required",
                field="htmlBody",
            )

        plugin_slug = svc.app_environment.tenant_cluster.provider_plugin.slug
        region = svc.app_environment.tenant_cluster.region or ""
        try:
            driver = driver_for_plugin_slug(
                plugin_slug=plugin_slug,
                region=region,
            )
        except LookupError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )

        try:
            template = driver.update_template(
                name=name,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
        except UnsupportedOperationError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )
        except Exception as exc:  # noqa: BLE001 -- cloud-side / not found
            msg = str(exc)
            code = ErrorCode.NOT_FOUND.value if "not found" in msg.lower() else ErrorCode.INTERNAL.value
            return gql_failure(
                code,
                f"{type(exc).__name__}: {msg}",
                field="name",
            )

        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.email.template.update.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "name": template.name,
                    "plugin_slug": plugin_slug,
                    "region": region,
                    "client_ip": ip,
                },
            )
        )
        return gql_success(
            EmailTemplateType(
                name=template.name,
                subject=template.subject,
                html_body=template.html_body,
                text_body=template.text_body,
                created_at=template.created_at,
            )
        )

    @strawberry.field
    @mutation_audit(
        action="managed_service.email.template.delete",
        extras=lambda result: (
            {"name": result.data.name, "deleted": result.data.deleted}
            if result.ok and result.data is not None
            else None
        ),
    )
    @require_permission(
        Permission.APP_UPDATE,
        Permission.MANAGED_SERVICE_UPDATE,
        scope=managed_service_scope_by_guid(
            "input.managed_service_id",
            permissions=(
                Permission.APP_UPDATE,
                Permission.MANAGED_SERVICE_UPDATE,
            ),
        ),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def delete_email_template(
        self,
        info: Info,
        input: DeleteEmailTemplateInput,
    ) -> MutationResultType[_EmailTemplateDeletedPayload]:
        """Delete a template by name (#635). Idempotent — deleting a
        missing template returns ``ok=True`` with ``data.deleted=False``."""
        from _sdk import UnsupportedOperationError

        from astrolift_services.email_observability import driver_for_plugin_slug

        svc = _resolve_email_service(input.managed_service_id)
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found or not an email service",
                field="managedServiceId",
            )
        name = (input.name or "").strip()
        if not name:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "name is required",
                field="name",
            )

        plugin_slug = svc.app_environment.tenant_cluster.provider_plugin.slug
        region = svc.app_environment.tenant_cluster.region or ""
        try:
            driver = driver_for_plugin_slug(
                plugin_slug=plugin_slug,
                region=region,
            )
        except LookupError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )

        try:
            deleted = driver.delete_template(name=name)
        except UnsupportedOperationError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                str(exc),
                field="managedServiceId",
            )
        except Exception as exc:  # noqa: BLE001 -- cloud-side transient
            return gql_failure(
                ErrorCode.INTERNAL.value,
                f"{type(exc).__name__}: {exc}",
                field="name",
            )

        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.email.template.delete.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "name": name,
                    "deleted": deleted,
                    "plugin_slug": plugin_slug,
                    "region": region,
                    "client_ip": ip,
                },
            )
        )
        return gql_success(_EmailTemplateDeletedPayload(name=name, deleted=deleted))
