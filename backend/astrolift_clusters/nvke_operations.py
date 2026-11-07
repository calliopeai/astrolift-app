"""Encrypted NVKE requests form the durable outbox; Temporal carries IDs only."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from nvke.client import MAX_REQUEST_BYTES, NvkeError
from nvke.contract import upstream_revision
from nvke.operations import action, validate_request

from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeConnection, NvkeOperation
from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken
from core.permissions import Permission, PermissionDenied
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest
from core.tenancy import TenantContext, tenant_context


def canonical(value, *, limit=MAX_REQUEST_BYTES):
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError):
        raise NvkeError("INVALID_INPUT", "NVKE input must contain finite JSON values") from None
    if len(data) > limit:
        raise NvkeError("INVALID_INPUT", "NVKE request exceeds the size limit")
    return data


def connection_digest(row):
    return hashlib.sha256(
        canonical(
            {
                "id": str(row.guid),
                "version": row.version,
                "endpoint": row.endpoint,
                "surface": row.surface,
                "enabled": row.is_enabled,
                "allow_http": row.allow_http,
                "timeout": row.timeout_seconds,
                "backend": row.credential_backend_kind,
                "ciphertext_sha256": hashlib.sha256(bytes(row.credential_ciphertext)).hexdigest(),
            }
        )
    ).hexdigest()


def reserve(*, site_id, expected_site_version, request_id, operation_key, parameters, timeout_seconds=7200):
    selected = action(operation_key)
    org, actor = sites.authorized(Permission(selected.permission))
    try:
        request_id = UUID(str(request_id))
    except (ValueError, TypeError, AttributeError):
        raise NvkeError("INVALID_INPUT", "Use a UUID request identifier") from None
    if type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 86400:
        raise NvkeError("INVALID_INPUT", "NVKE operation timeout must be between 30 and 86400 seconds")
    payload = canonical(parameters)
    parameters = json.loads(payload)
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        digest = hmac.new(
            str(settings.SECRET_KEY).encode(),
            canonical(
                {
                    "site": str(site.guid),
                    "version": expected_site_version,
                    "key": operation_key,
                    "parameters": parameters,
                    "timeout": timeout_seconds,
                }
            ),
            hashlib.sha256,
        ).hexdigest()
        existing = (
            NvkeOperation.all_objects.select_for_update()
            .filter(site=site, actor=actor, request_id=request_id)
            .first()
        )
        if existing is not None:
            if existing.deleted_at is not None or not hmac.compare_digest(existing.request_digest, digest):
                raise NvkeError("CONFLICT", "This request ID already identifies a different NVKE request")
            if not existing.terminal:
                transaction.on_commit(lambda: dispatch(existing.pk))
            return existing
        sites._version(site, expected_site_version)
        if not site.is_active or site.upstream_revision != upstream_revision():
            raise NvkeError("SOURCE_CHANGED", "NVKE site is disabled or requires contract review")
        validate_request(operation_key, parameters, site.organization_namespace)
        connection = NvkeConnection.objects.filter(
            site=site, surface=selected.surface, is_enabled=True
        ).first()
        if connection is None:
            raise NvkeError("SURFACE_UNAVAILABLE", "The requested NVKE interface is not configured")
        token = get_current_api_token()
        sealed = encrypt_at_rest(payload)
        row = NvkeOperation.objects.create(
            site=site,
            organization=org,
            actor=actor,
            request_id=request_id,
            request_digest=digest,
            operation_key=operation_key,
            expected_site_version=site.version,
            authority={
                "connection_id": str(connection.guid),
                "connection_digest": connection_digest(connection),
                "token_id": str(token.guid) if token is not None else None,
                "revision": upstream_revision(),
                "organization_namespace": site.organization_namespace,
            },
            input_backend_kind=sealed.backend_kind,
            input_ciphertext=sealed.backend_ref,
            deadline=timezone.now() + timedelta(seconds=timeout_seconds),
            created_by=actor,
            updated_by=actor,
        )
        transaction.on_commit(lambda: dispatch(row.pk))
        return row


@contextmanager
def original_actor(row):
    token = None
    if token_id := row.authority.get("token_id"):
        token = with_active_org_member(
            ApiToken.objects.filter(
                guid=token_id, user_id=row.actor_id, organization_id=row.organization_id, is_revoked=False
            ),
            user="user",
            organization="organization",
        ).first()
        if token is None or (token.expires_at is not None and token.expires_at <= timezone.now()):
            raise NvkeError("AUTHORITY_REVOKED", "The original NVKE caller is no longer authorized")
    context = set_current_api_token(token)
    try:
        with tenant_context(TenantContext(organization_id=row.organization_id, actor_user_id=row.actor_id)):
            yield
    finally:
        reset_current_api_token(context)


def checkpoint(row):
    selected = action(row.operation_key)
    with original_actor(row), abac.request_attributes(replace(abac.attributes_for(row.actor_id), cache={})):
        try:
            org, _actor = sites.authorized(Permission(selected.permission))
        except PermissionDenied:
            raise NvkeError("AUTHORITY_REVOKED", "The original NVKE caller is no longer authorized") from None
        site = sites.site_row(row.site.guid, org)
        if (
            not site.is_active
            or site.version != row.expected_site_version
            or row.authority.get("revision") != upstream_revision()
            or site.upstream_revision != row.authority.get("revision")
            or site.organization_namespace != row.authority.get("organization_namespace")
        ):
            raise NvkeError("SOURCE_CHANGED", "NVKE site authority or contract changed")
        connection = NvkeConnection.objects.filter(
            guid=row.authority.get("connection_id"), site=site, surface=selected.surface, is_enabled=True
        ).first()
        if connection is None or not hmac.compare_digest(
            connection_digest(connection), row.authority.get("connection_digest", "")
        ):
            raise NvkeError("SOURCE_CHANGED", "NVKE connection authority changed")
        return connection


def decode_input(row):
    try:
        return json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext))))
    except Exception:
        raise NvkeError("CREDENTIALS_UNAVAILABLE", "Original NVKE request could not be read") from None


def decode_result(row):
    if not row.result_ciphertext:
        return {}
    try:
        return json.loads(decrypt(EncryptedSecret(row.result_backend_kind, bytes(row.result_ciphertext))))
    except Exception:
        raise NvkeError("CREDENTIALS_UNAVAILABLE", "NVKE operation result could not be read") from None


def save_result(row, result):
    sealed = encrypt_at_rest(canonical(result, limit=64 * 1024 * 1024))
    row.result_backend_kind = sealed.backend_kind
    row.result_ciphertext = sealed.backend_ref


def read(operation_id):
    org, _actor = sites.authorized(Permission.NVKE_READ)
    try:
        guid = UUID(str(operation_id))
    except (ValueError, TypeError, AttributeError):
        raise NvkeError("NOT_FOUND", "NVKE operation not found") from None
    row = (
        NvkeOperation.objects.select_related("site")
        .filter(guid=guid, organization=org, site__organization=org)
        .first()
    )
    if row is None:
        raise NvkeError("NOT_FOUND", "NVKE operation not found")
    return row


def result(operation_id):
    row = read(operation_id)
    sites.authorized(Permission(action(row.operation_key).permission))
    return decode_result(row)


def dispatch(row_id):
    from astrolift_workflows.client import (
        describe_workflow_instance,
        recover_workflow_once,
        start_workflow_once,
    )

    with transaction.atomic():
        row = NvkeOperation.objects.select_for_update().select_related("site").filter(pk=row_id).first()
        if row is None or row.terminal or row.status == "uncertain":
            return row
        try:
            args = [row.pk, row.generation]
            handle = recover_workflow_once("NvkeOperationWorkflow", args, workflow_id=row.workflow_id)
            if handle is not None:
                if row.workflow_run_id and row.workflow_run_id != handle.run_id:
                    raise RuntimeError
                description = describe_workflow_instance(row.workflow_id, run_id=handle.run_id)
                if (
                    description is None
                    or description["run_id"] != handle.run_id
                    or description["workflow_type"] != "NvkeOperationWorkflow"
                ):
                    raise RuntimeError
                if description["status"] == "RUNNING":
                    row.workflow_run_id = handle.run_id
                    row.error_code = ""
                    row.error_message = ""
                    row.save(
                        update_fields=[
                            "workflow_run_id",
                            "error_code",
                            "error_message",
                            "updated_at",
                            "version",
                        ]
                    )
                    return row
                if not description.get("closed_at") or description["status"] not in {
                    "COMPLETED",
                    "FAILED",
                    "TIMED_OUT",
                    "CANCELED",
                    "TERMINATED",
                }:
                    raise RuntimeError
                # The new execution retains encrypted sending/response state.
                # It may observe an accepted write, never submit that write again.
                row.generation += 1
                row.workflow_run_id = ""
                row.save(update_fields=["generation", "workflow_run_id", "updated_at", "version"])
                args = [row.pk, row.generation]
            elif row.workflow_run_id:
                # Expired or missing history cannot prove that the old run stopped.
                raise RuntimeError
            handle = start_workflow_once("NvkeOperationWorkflow", args, workflow_id=row.workflow_id)
            row.workflow_run_id = handle.run_id
            row.error_code = ""
            row.error_message = ""
        except Exception:
            # A failed acknowledgement is not evidence of a failed start. Leave
            # the original workflow ID recoverable; the activity rechecks authority.
            row.error_code = "DISPATCH_PENDING"
            row.error_message = "NVKE workflow dispatch awaits confirmation"
        row.save(update_fields=["workflow_run_id", "error_code", "error_message", "updated_at", "version"])
        return row


def sweep_outbox(*, limit=40):
    # Oldest update first ensures a failed row cannot starve later admissions.
    # Uncertain writes require an explicit reviewed reconciliation, never a tick.
    identifiers = list(
        NvkeOperation.objects.filter(status__in=["queued", "running", "awaiting_ready", "reconciling"])
        .order_by("updated_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    for identifier in identifiers:
        dispatch(identifier)
    return len(identifiers)


def request_cancel(operation_id):
    row = read(operation_id)
    sites.authorized(Permission(action(row.operation_key).permission))
    with transaction.atomic():
        row = NvkeOperation.objects.select_for_update().get(pk=row.pk)
        if not row.terminal:
            row.cancellation_requested_at = timezone.now()
            row.save(update_fields=["cancellation_requested_at", "updated_at", "version"])
    return row


def resume(operation_id, *, expected_version, timeout_seconds=7200):
    row = read(operation_id)
    sites.authorized(Permission.NVKE_ADMIN)
    sites.authorized(Permission(action(row.operation_key).permission))
    if type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 86400:
        raise NvkeError("INVALID_INPUT", "NVKE operation timeout must be between 30 and 86400 seconds")
    with transaction.atomic():
        row = NvkeOperation.objects.select_for_update().select_related("site").get(pk=row.pk)
        if row.version != expected_version:
            raise NvkeError("VERSION_MISMATCH", "NVKE operation changed; refresh before reconciliation")
        if row.status != "uncertain":
            raise NvkeError(
                "PRECONDITION", "Only an uncertain NVKE operation requires explicit reconciliation"
            )
        checkpoint(row)
        progress = decode_result(row)
        if progress.get("sending") and not progress.get("target"):
            raise NvkeError(
                "PRECONDITION", "The original NVKE resource identity requires operator investigation"
            )
        row.status = "reconciling"
        row.deadline = timezone.now() + timedelta(seconds=timeout_seconds)
        row.error_code = ""
        row.error_message = ""
        row.save(update_fields=["status", "deadline", "error_code", "error_message", "updated_at", "version"])
        transaction.on_commit(lambda: dispatch(row.pk))
    row.refresh_from_db()
    return row
