"""GraphQL mutation audit log.

Strawberry extension that records every mutation execution:
who called it, when, with what variables, and whether it succeeded.

Sensitive fields (password, pin, token) are redacted from logged variables.
"""

import logging
from typing import Any

from django.conf import settings
from django.db import models
from strawberry.extensions import SchemaExtension

from core.tenancy import get_current_tenant

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {"password", "pin", "token", "secret", "ssn", "credit_card", "secure"}
SECRET_VALUE_KEYS = {"value", "values", "plaintext"}


def _redact(value: Any, *, secret_operation: bool = False) -> Any:
    """Recursively redact sensitive mutation variables.

    Secret mutations commonly name their plaintext argument simply ``value``.
    Those generic value keys are always redacted: operation names are
    caller-controlled and a document may contain several mutations, so an
    action-name heuristic cannot be a confidentiality boundary.
    """
    if isinstance(value, dict):
        redacted = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.lower()
            sensitive = any(token in normalized for token in SENSITIVE_KEYS)
            # GraphQL operation names are caller-controlled and one document
            # may execute more than one mutation. Never make plaintext safety
            # depend on the operation/action classifier: generic value fields
            # are cheap to lose from diagnostics and catastrophic to retain
            # when they belong to a secret mutation.
            sensitive = sensitive or normalized in SECRET_VALUE_KEYS
            redacted[key] = (
                "***REDACTED***" if sensitive else _redact(child, secret_operation=secret_operation)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact(item, secret_operation=secret_operation) for item in value]
    return value


def _is_secret_operation(action: str) -> bool:
    normalized = (action or "").lower()
    return ".secret." in normalized or "push_secrets" in normalized or normalized.startswith("secret_bundle.")


class MutationAuditLog(models.Model):
    """Persistent log of every GraphQL mutation execution."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mutation_audit_logs",
    )
    # The org the mutation is filed under: the tenant org, when the actor
    # belonged to it (``core.mutations.attributable_organization_id``).
    # Tenant-facing readers must filter on it: the variables alone cannot
    # say whose row it is (#1955). NULL otherwise, including every row
    # written before the column existed; no tenant view shows those. The
    # index is the composite one below, built concurrently (core.0014).
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mutation_audit_logs",
        db_index=False,
    )
    operation = models.CharField(max_length=200, db_index=True)
    variables = models.JSONField(default=dict, blank=True)
    success = models.BooleanField(default=True)
    errors = models.JSONField(default=list, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["user", "-timestamp"]),
            models.Index(fields=["operation", "-timestamp"]),
            models.Index(fields=["organization", "-timestamp"], name="core_mal_org_time_idx"),
        ]

    def __str__(self):
        return f"{self.operation} by {self.user} at {self.timestamp}"


class MutationAuditExtension(SchemaExtension):
    """Strawberry schema extension that logs mutations to the database."""

    def on_operation(self):
        # Judged before the mutation runs, as @mutation_audit does (#1955).
        organization_id = self._attributable_organization_id()
        yield  # Let the operation execute
        # After execution, log if it was a mutation
        try:
            request = self.execution_context
            if not request or not request.query:
                return

            query = request.query.strip()
            if not query.lower().startswith("mutation"):
                return

            # Prefer the dot-notation action set by @mutation_audit (e.g.
            # "cluster.install_prereqs") over the raw GraphQL operation name
            # (e.g. "installClusterPrereqs").  The lifecycle-audit resolver
            # filters on dot-notation prefixes, so this must match.
            try:
                from core.mutations import _mutation_action_local

                operation_name = (
                    getattr(_mutation_action_local, "action", None) or request.operation_name or "unknown"
                )
            except Exception:
                operation_name = request.operation_name or "unknown"

            # Get user from context
            user = None
            ip_address = None
            context = getattr(request, "context", None)
            if context:
                user = getattr(context, "user", None)
                if user and not user.is_authenticated:
                    user = None
                req = getattr(context, "request", None)
                if req:
                    ip_address = _get_client_ip(req)

            # Check for errors
            result = request.result
            has_errors = bool(result and result.errors)
            error_messages = []
            if has_errors and result.errors:
                error_messages = [str(e) for e in result.errors[:5]]

            # Redact sensitive variables. A mutation sent with inline
            # literals and no `variables` key leaves `request.variables`
            # as None; `variables` is NOT NULL, and JSONField's `default`
            # only backfills an omitted kwarg, not an explicit None.
            variables = _redact(
                request.variables or {},
                secret_operation=_is_secret_operation(operation_name),
            )

            MutationAuditLog.objects.create(
                user=user,
                organization_id=organization_id,
                operation=operation_name,
                variables=variables,
                success=not has_errors,
                errors=error_messages,
                ip_address=ip_address,
            )
        except Exception as e:
            logger.warning(f"Mutation audit log failed: {e}")

    def _attributable_organization_id(self) -> int | None:
        """The org this mutation's row may carry; see
        ``core.mutations.attributable_organization_id``. ``None`` for a
        query, which is never logged."""
        try:
            request = self.execution_context
            query = (request.query or "").strip().lower() if request else ""
            if not query.startswith("mutation"):
                return None
            from core.mutations import attributable_organization_id

            return attributable_organization_id(get_current_tenant())
        except Exception as e:
            logger.warning("Mutation audit attribution failed: %s", e)
            return None


def _get_client_ip(request) -> str | None:
    """Extract client IP from Django request."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
