"""EmailServiceMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.operation_context import managed_service_operation
from astrolift_services.models import (
    ManagedService,
)
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
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class EmailServiceMutations:
    @strawberry.field
    @mutation_audit(
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
        scope=managed_service_scope_by_guid("input.managed_service_id"),
        operation=managed_service_operation("input.managed_service_id"),
    )
    @tenant_scoped()
    def send_managed_service_test_email(
        self,
        info: Info,
        input: SendManagedServiceTestEmailInput,
    ) -> MutationResultType[ManagedServiceTestEmailResultType]:
        """Operator-fired test send through a bound `email` kind managed
        service (#401).

        Rides the existing `astrolift_operations.email_infra` plumbing —
        the configured transport (SES / SendGrid / Postmark / SMTP)
        receives the rendered payload.  Suppression list checks fire
        normally so a hard-bounced address won't be retried; bypasses
        UNSUBSCRIBE since the operator triggered it deliberately to
        verify deliverability.
        """
        # Lazy imports — keep mutation module light when email infra
        # isn't reached.
        from astrolift_operations.email_infra import (
            Email,
            EmailError,
            EmailKind,
            configured_transport,
            is_configured,
        )
        from astrolift_operations.email_infra import (
            send as email_send,
        )

        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(
                guid=str(input.managed_service_id),
                registered_app__organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )
        if svc.kind != ManagedService.Kind.EMAIL:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"managed service is {svc.kind!r}, not 'email'; "
                    "test-email is only supported for email kinds (SES, "
                    "SendGrid, Postmark, SMTP variants)"
                ),
                field="managedServiceId",
            )

        recipient = (input.recipient or "").strip()
        if not recipient:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "recipient is required",
                field="recipient",
            )

        # Resolve a sane from_address from the binding config; falls
        # back to a synthesized address scoped to the app so the email's
        # provenance is obvious in the recipient's mailbox.
        config = svc.config or {}
        from_address = (
            config.get("email_from")
            or config.get("EMAIL_FROM")
            or f"noreply@{svc.registered_app.slug}.astrolift.local"
        )
        subject = (input.subject or "").strip() or (f"[Astrolift] Test email from {svc.name or svc.kind}")
        body = (input.body or "").strip() or (
            f"This is a deliverability test fired from the {svc.registered_app.slug} "
            f"settings page against managed service {svc.name or svc.kind} "
            f"({svc.app_environment.name}).  If you received this, the email "
            "binding is working."
        )

        if not is_configured():
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    "no email transport configured on this install; admin "
                    "must set EMAIL_BACKEND before test sends will land"
                ),
                field="managedServiceId",
            )

        try:
            email = Email(
                to_address=recipient,
                subject=subject,
                html_body=f"<p>{body}</p>",
                plain_body=body,
                from_address=from_address,
                kind=EmailKind.MANAGED_SERVICE_TEST,
            )
        except EmailError as exc:
            field = "recipient" if "to_address" in str(exc) else "managedServiceId"
            return gql_failure(
                ErrorCode.VALIDATION.value,
                str(exc),
                field=field,
            )

        try:
            email_send(email, suppression_lookup=lambda _addr: None)
        except EmailError as exc:
            return gql_failure(
                ErrorCode.INTERNAL.value,
                str(exc),
                field="managedServiceId",
            )

        transport = configured_transport().value

        # Cache the operator action for the summary card.
        now = timezone.now()
        svc.last_action_at = now
        svc.last_action_kind = "test_email.send"
        svc.save(update_fields=["last_action_at", "last_action_kind", "updated_at", "version"])

        # Sibling audit row carrying client IP + recipient so the trail
        # captures the disclosure source (operators triggering a test
        # send to an unfamiliar address should be obvious in audit).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.test_email.send.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "kind": svc.kind,
                    "name": svc.name,
                    "app_slug": svc.registered_app.slug,
                    "environment_name": svc.app_environment.name,
                    "recipient": recipient,
                    "from_address": from_address,
                    "transport": transport,
                    "client_ip": ip,
                },
            )
        )

        return gql_success(
            ManagedServiceTestEmailResultType(
                managed_service_id=input.managed_service_id,
                recipient=recipient,
                subject=subject,
                sent_at=now,
                transport=transport,
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
        scope=managed_service_scope_by_guid("input.managed_service_id"),
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
        scope=managed_service_scope_by_guid("input.managed_service_id"),
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
        scope=managed_service_scope_by_guid("input.managed_service_id"),
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
        scope=managed_service_scope_by_guid("input.managed_service_id"),
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
        scope=managed_service_scope_by_guid("input.managed_service_id"),
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
