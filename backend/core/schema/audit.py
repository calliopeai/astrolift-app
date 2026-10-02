"""GraphQL mutation audit log.

Strawberry extension that records every mutation execution:
who called it, when, with what variables, and whether it succeeded.

Sensitive fields (password, pin, token) are redacted from logged variables.
Manifest/dotenv-shaped arguments have their embedded secret literals
masked structurally rather than wholesale (#1920); see ``MANIFEST_TEXT_KEYS``.
Which rule applies to a variable is decided by the schema position the
operation binds it to, never by the variable's client-chosen name; see
``redact_operation_variables``.
"""

import logging
from collections.abc import Mapping
from typing import Any

import strawberry
from django.conf import settings
from django.db import models
from graphql import (
    DocumentNode,
    GraphQLInputType,
    GraphQLSchema,
    TypeInfo,
    TypeInfoVisitor,
    Visitor,
    get_named_type,
    is_input_object_type,
    is_list_type,
    is_non_null_type,
    visit,
)
from strawberry.extensions import SchemaExtension
from strawberry.utils.logging import StrawberryLogger

from core.tenancy import get_current_tenant

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    "password",
    "pin",
    "token",
    "secret",
    "ssn",
    "credit_card",
    "secure",
    "credential",
    "private_key",
    "privatekey",
}
SECRET_VALUE_KEYS = {
    "value",
    "values",
    "plaintext",
    "env_vars",
    "envvars",
    "trigger_payload",
    "triggerpayload",
}

# Arguments that carry a whole manifest/dotenv document rather than one
# isolated secret value (#1920): ``updateManifest.rawManifest``,
# ``registerApp.manifestRaw``, and ``bulkImportAppSecrets.dotenvText``
# all embed ``[env]``-table (or KEY=value) secret literals inline with
# ordinary, useful-for-audit document content. A blanket
# ``***REDACTED***`` (right for ``SECRET_VALUE_KEYS``) would also erase
# that non-secret content; these get the same structural mask the
# GraphQL response fields use instead, via ``astrolift_manifest.env_edit``.
MANIFEST_TEXT_KEYS = {"rawmanifest", "manifestraw", "rawmanifeststaged"}
DOTENV_TEXT_KEYS = {"dotenvtext"}

# Input fields whose name gives no hint of the credentials inside them
# (#1920): an IdP's per-kind config JSON and a cluster's kubeconfig /
# token auth config. ``test_sensitive_input_fields_exist_in_schema``
# fails if a rename orphans an entry.
SENSITIVE_INPUT_FIELDS = frozenset(
    {
        "CreateIdentityProviderInput.config",
        "UpdateIdentityProviderInput.config",
        "RegisterTenantClusterInput.authConfig",
        "RunAstroliftAgentInput.callbackUrl",
        "RunAstroliftAgentInput.triggerPayload",
        "StartWorkflowDefinitionInput.inputs",
    }
)

_MASKED = "***REDACTED***"


def _name_rule(name: str) -> str | None:
    """How a field or argument called ``name`` is masked: ``"manifest"``,
    ``"dotenv"``, ``"mask"``, or None for not at all."""
    normalized = name.lower()
    if normalized in MANIFEST_TEXT_KEYS:
        return "manifest"
    if normalized in DOTENV_TEXT_KEYS:
        return "dotenv"
    # Generic value fields are cheap to lose from diagnostics and
    # catastrophic to retain when they belong to a secret mutation.
    if normalized in SECRET_VALUE_KEYS or any(token in normalized for token in SENSITIVE_KEYS):
        return "mask"
    return None


def _field_rule(type_name: str, field_name: str) -> str | None:
    if f"{type_name}.{field_name}" in SENSITIVE_INPUT_FIELDS:
        return "mask"
    return _name_rule(field_name)


def _apply_rule(rule: str, value: Any) -> Any:
    if rule == "manifest" and isinstance(value, str):
        return _redact_manifest_text(value)
    if rule == "dotenv" and isinstance(value, str):
        return _redact_dotenv_text(value)
    return _MASKED


def _redact(value: Any, *, secret_operation: bool = False) -> Any:
    """Recursively redact sensitive mutation variables by key name.

    Right only where the keys are not the client's to choose, or are
    all there is to go on: inside an input object (keys are schema
    field names) and inside a JSON scalar. Top-level variables go
    through :func:`redact_operation_variables`, because a variable's own
    name (``$m``) is whatever the client typed.
    """
    if isinstance(value, dict):
        redacted = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            rule = _name_rule(key)
            redacted[key] = (
                _apply_rule(rule, child) if rule else _redact(child, secret_operation=secret_operation)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact(item, secret_operation=secret_operation) for item in value]
    return value


def redact_operation_variables(
    variables: Mapping[str, Any] | None,
    *,
    document: DocumentNode | None,
    schema: GraphQLSchema | None,
) -> tuple[dict[str, Any], bool]:
    """Redact ``variables`` by where the operation uses each one (#1920).

    The client names its variables, so ``$m`` can carry a manifest and
    ``$v`` a secret value. What a value is depends on the schema
    position it fills: the argument or input field the document binds
    it to, found by walking ``document`` against ``schema``. A variable
    in a sensitive position gets that position's rule; one filling an
    input object is walked field by field against the type; one the walk
    cannot place (unused, or under a field the schema does not define)
    is masked outright, as is everything when there is no parsed
    document.

    Also returns whether the operation touched anything sensitive: a
    sensitive or unplaceable position anywhere in the document, literal
    or variable, or a masked value. GraphQL errors quote variable values
    and print source excerpts, so such an operation's errors are not
    safe to store either.
    """
    variables = dict(variables or {})
    if document is None or schema is None:
        return {str(name): _MASKED for name in variables}, True
    collector = _VariablePositions(TypeInfo(schema))
    visit(document, TypeInfoVisitor(collector.type_info, collector))
    sensitive = collector.sensitive
    redacted: dict[str, Any] = {}
    for name, value in variables.items():
        positions = collector.positions.get(str(name))
        if not positions:
            redacted[str(name)] = _MASKED
            continue
        for rule, input_type in positions:
            value, masked = _redact_at(value, rule, input_type)
            sensitive = sensitive or masked
        redacted[str(name)] = value
    return redacted, sensitive


class _VariablePositions(Visitor):
    """Records, for each ``$variable``, the rule and input type of every
    place the document uses it."""

    def __init__(self, type_info: TypeInfo) -> None:
        super().__init__()
        self.type_info = type_info
        self.positions: dict[str, list[tuple[str | None, GraphQLInputType | None]]] = {}
        self.sensitive = False
        self._rules: list[str | None] = []

    def enter_variable_definition(self, *_args: Any) -> Any:
        return self.SKIP

    def enter_argument(self, node: Any, *_args: Any) -> None:
        if self.type_info.get_argument() is None:
            self._push("mask")
        else:
            self._push(_name_rule(node.name.value))

    def leave_argument(self, *_args: Any) -> None:
        self._rules.pop()

    def enter_object_field(self, node: Any, *_args: Any) -> None:
        parent = get_named_type(self.type_info.get_parent_input_type())
        name = node.name.value
        if is_input_object_type(parent):
            self._push(_field_rule(parent.name, name) if name in parent.fields else "mask")
        elif parent is None:
            self._push("mask")
        else:
            # A key inside a JSON scalar literal: the caller's own name.
            self._push(_name_rule(name))

    def leave_object_field(self, *_args: Any) -> None:
        self._rules.pop()

    def enter_variable(self, node: Any, *_args: Any) -> None:
        rule = self._rules[-1] if self._rules else "mask"
        self.positions.setdefault(node.name.value, []).append((rule, self.type_info.get_input_type()))

    def _push(self, rule: str | None) -> None:
        # Anything nested under a masked position stays masked.
        rule = rule or (self._rules[-1] if self._rules else None)
        if rule is not None:
            self.sensitive = True
        self._rules.append(rule)


def _redact_at(value: Any, rule: str | None, input_type: GraphQLInputType | None) -> tuple[Any, bool]:
    if rule is not None:
        return _apply_rule(rule, value), True
    if input_type is None:
        return _MASKED, True
    return _redact_typed(value, input_type)


def _redact_typed(value: Any, input_type: Any) -> tuple[Any, bool]:
    if is_non_null_type(input_type):
        input_type = input_type.of_type
    if is_list_type(input_type):
        if isinstance(value, list):
            items = [_redact_typed(item, input_type.of_type) for item in value]
            return [item for item, _ in items], any(masked for _, masked in items)
        # Input coercion accepts a single item where a list is expected.
        return _redact_typed(value, input_type.of_type)
    if is_input_object_type(input_type):
        if value is None:
            return None, False
        if not isinstance(value, dict):
            return _MASKED, True
        redacted: dict[str, Any] = {}
        any_masked = False
        for key, child in value.items():
            field = input_type.fields.get(key)
            rule = "mask" if field is None else _field_rule(input_type.name, key)
            if rule is not None:
                redacted[key] = _apply_rule(rule, child)
                any_masked = True
            else:
                redacted[key], child_masked = _redact_typed(child, field.type)
                any_masked = any_masked or child_masked
        return redacted, any_masked
    if isinstance(value, (dict, list)):
        # A JSON scalar: its keys are the caller's own, so only the
        # key-name heuristic has anything to go on.
        redacted_json = _redact(value)
        return redacted_json, redacted_json != value
    return value, False


def _touches_secrets(execution_context: Any) -> bool:
    _, sensitive = redact_operation_variables(
        execution_context.variables,
        document=execution_context.graphql_document,
        schema=getattr(execution_context.schema, "_schema", None),
    )
    return sensitive


class SecretSafeSchema(strawberry.Schema):
    """A ``strawberry.Schema`` whose error log cannot quote a secret (#1920).

    Strawberry logs every GraphQL error at ERROR as ``str(error)``: the
    message (a coercion error quotes the variable value it rejected) and
    the source excerpt around it (inline literals included). That log is
    also what error reporting picks up. For an operation that touches a
    sensitive position, only the error's kind and location are logged."""

    def process_errors(self, errors: list, execution_context: Any = None) -> None:
        try:
            withhold = execution_context is None or _touches_secrets(execution_context)
        except Exception:  # noqa: BLE001 -- cannot tell, so withhold rather than fail the request
            withhold = True
        if not withhold:
            super().process_errors(errors, execution_context)
            return
        for error in errors:
            StrawberryLogger.logger.error(
                "GraphQL %s at path=%s locations=%s; message withheld: the operation carries secret values",
                type(error.original_error or error).__name__,
                error.path,
                [(location.line, location.column) for location in error.locations or ()],
            )


def _redact_manifest_text(text: str) -> str:
    """Mask ``[env]`` values in a manifest-shaped audit variable (#1920).

    The audit log is a durable, superuser-readable record. Unlike a
    live resolver it has no single "current caller" to gate on, so this
    always masks, the same way ``secret_change_diff.build_diff`` always
    masks rather than checking a viewer's ``secret.read`` + elevation."""
    from astrolift_manifest.env_edit import redact_env_values

    return redact_env_values(text)


def _redact_dotenv_text(text: str) -> str:
    """Mask values in a dotenv-shaped audit variable (#1920). See
    :func:`_redact_manifest_text`."""
    from astrolift_manifest.env_edit import redact_dotenv_values

    return redact_dotenv_values(text)


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
        from core.mutations import _mutation_action_local

        # The action name is published per thread by @mutation_audit. Start
        # every operation without one, so a request whose mutation never
        # reaches a decorated resolver (a validation failure, say) is not
        # filed under the previous request's action (#1956).
        _mutation_action_local.action = None
        # Judged before the mutation runs, as @mutation_audit does (#1955).
        organization_id = self._attributable_organization_id()
        context = getattr(self.execution_context, "context", None)
        original_actor = getattr(context, "user", None)
        try:
            yield  # Let the operation execute
            self._log(organization_id, original_actor=original_actor)
        finally:
            _mutation_action_local.action = None

    def _is_mutation(self) -> bool:
        """By the parsed operation type, not by the query text's first word
        (an anonymous ``{...}`` or a document with a leading comment or a
        fragment is still a mutation when it says so) (#1956)."""
        request = self.execution_context
        if not request or not request.query:
            return False
        try:
            from strawberry.types.graphql import OperationType

            return request.operation_type == OperationType.MUTATION
        except Exception:  # noqa: BLE001 - unparseable document
            return request.query.strip().lower().startswith("mutation")

    def _log(self, organization_id, *, original_actor=None):
        # After execution, log if it was a mutation
        try:
            request = self.execution_context
            if not self._is_mutation():
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
            # Self-erasure runs before this post-call insert. Preserve the
            # existing attribution behavior while preventing the request IP
            # from returning, including the legacy path that logs out first.
            from astrolift_identity.anonymization_state import is_anonymized_user

            if any(
                actor is not None and getattr(actor, "is_authenticated", False) and is_anonymized_user(actor)
                for actor in (user, original_actor)
            ):
                ip_address = None

            # A mutation sent with inline literals and no `variables` key
            # leaves `request.variables` as None; `variables` is NOT NULL,
            # and JSONField's `default` only backfills an omitted kwarg,
            # not an explicit None. redact_operation_variables always
            # returns a dict.
            variables, sensitive = redact_operation_variables(
                request.variables,
                document=request.graphql_document,
                schema=getattr(request.schema, "_schema", None),
            )

            # Check for errors: execution errors on the result, and parse or
            # validation errors, which never reach a result (#1956); and a
            # resolver's own envelope failure (``ok: false``) when the
            # document selected it.
            result = request.result
            graphql_errors = list(
                (result.errors if result else None) or getattr(request, "pre_execution_errors", None) or []
            )
            error_messages = [str(e) for e in graphql_errors[:5]]
            envelope_failed = any(
                isinstance(value, dict) and value.get("ok") is False
                for value in (
                    (result.data or {}).values() if result and isinstance(result.data, dict) else ()
                )
            )
            has_errors = bool(graphql_errors) or envelope_failed
            # Error messages quote variable values and print source
            # excerpts, literals included (#1920).
            if sensitive or _is_secret_operation(operation_name):
                error_messages = [_MASKED for _ in error_messages]

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
            query = (request.query or "").strip() if request else ""
            if not query or query.lower().startswith(("query", "subscription")):
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
