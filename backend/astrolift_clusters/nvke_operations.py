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
from nvke.inventory import validate_inventory_request
from nvke.operations import action, validate_request

from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeConnection, NvkeOperation, NvkeWorkloadAccessBinding
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


def reserve_text(*, parameters_text, expected_actor_id, **kwargs):
    # Browser editors must not parse native int64 values through JavaScript's
    # floating-point JSON representation before admission.
    _org, actor = sites.authorized(Permission(action(kwargs["operation_key"]).permission))
    if type(expected_actor_id) is not int or actor.pk != expected_actor_id:
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE caller changed before admission")
    if not isinstance(parameters_text, str):
        raise NvkeError("INVALID_INPUT", "NVKE parameters must contain a JSON object")
    try:
        if len(parameters_text.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError
        parameters = json.loads(parameters_text)
    except (ValueError, UnicodeError, TypeError):
        raise NvkeError("INVALID_INPUT", "NVKE parameters must contain one bounded JSON object") from None
    return reserve(parameters=parameters, **kwargs)


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


def operation_permission(key):
    if key == "infrastructure.providers":
        return Permission.NVKE_ADMIN
    if key == "metrics.credentials":
        return Permission.NVKE_ADMIN
    if key == "temporal.reset.RotateEtcdEncryptionKey":
        return Permission.NVKE_ADMIN
    if key.startswith("temporal."):
        from astrolift_clusters.nvke_native_operations import workflow_name

        workflow_name(key)
        return Permission.NVKE_ADMIN
    return Permission(action(key).permission)


def reserve(
    *,
    site_id,
    expected_site_version,
    request_id,
    operation_key,
    parameters,
    timeout_seconds=7200,
    inventory=False,
    resource_source=None,
):
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
        reviewed = {
            "site": str(site.guid),
            "version": expected_site_version,
            "key": operation_key,
            "parameters": parameters,
            "timeout": timeout_seconds,
        }
        if inventory:
            reviewed["inventory"] = True
        if resource_source is not None:
            reviewed["resource_source"] = resource_source
        digest = hmac.new(
            str(settings.SECRET_KEY).encode(),
            canonical(reviewed),
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
        if inventory and resource_source is not None:
            raise NvkeError("INVALID_INPUT", "Choose inventory or resource admission")
        if resource_source is None:
            validate_request(operation_key, parameters, site.organization_namespace)
        if inventory:
            validate_inventory_request(operation_key, parameters)
        connection = NvkeConnection.objects.filter(
            site=site, surface=selected.surface, is_enabled=True
        ).first()
        if connection is None:
            raise NvkeError("SURFACE_UNAVAILABLE", "The requested NVKE interface is not configured")
        resource_guard = None
        if resource_source is not None:
            from astrolift_clusters.nvke_resource_operations import bind

            parameters, resource_guard = bind(
                site=site,
                actor=actor,
                selected=selected,
                parameters=parameters,
                reference=resource_source,
                connection=connection,
            )
            source_row = (
                NvkeOperation.all_objects.select_for_update()
                .filter(guid=resource_source["operation_id"], site=site, organization=org)
                .first()
            )
            if (
                source_row is None
                or source_row.deleted_at is not None
                or source_row.version != resource_source["version"]
                or source_row.status != "succeeded"
            ):
                raise NvkeError(
                    "SOURCE_CHANGED", "The reviewed NVKE resource observation changed during admission"
                )
            if (
                selected.mutates
                and NvkeOperation.objects.filter(
                    site=site,
                    organization=org,
                    authority__resource__identity_digest=resource_guard["identity_digest"],
                    authority__resource__mutates=True,
                )
                .exclude(status__in=["succeeded", "failed", "cancelled"])
                .exists()
            ):
                raise NvkeError(
                    "RESOURCE_BUSY",
                    "Observe or explicitly reconcile the existing NVKE mutation for this resource",
                )
            payload = canonical(parameters)
            validate_request(operation_key, parameters, site.organization_namespace)
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
                **({"inventory": True} if inventory else {}),
                **({"resource": resource_guard} if resource_guard is not None else {}),
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
    if row.operation_key == "infrastructure.providers":
        from astrolift_clusters.nvke_provider_observations import checkpoint as provider_checkpoint

        return provider_checkpoint(row)
    if row.operation_key == "metrics.credentials":
        from astrolift_clusters.nvke_credential_observations import checkpoint as metrics_checkpoint

        return metrics_checkpoint(row)
    if row.operation_key.startswith("temporal."):
        from astrolift_clusters.nvke_native_operations import checkpoint as native_checkpoint

        return native_checkpoint(row)
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


def _cluster_name_digest(row, name):
    return hmac.new(
        str(settings.SECRET_KEY).encode(),
        canonical({"site": str(row.site.guid), "name": name}),
        hashlib.sha256,
    ).hexdigest()


def created_cluster_reference(row, selected, request, response):
    if (
        selected.key != "createCluster"
        or not 200 <= response.status_code < 300
        or not isinstance(response.data, dict)
    ):
        return {}
    data = response.data
    try:
        identity = UUID(data["id"])
    except (KeyError, ValueError, TypeError, AttributeError):
        return {}
    if (
        not identity.int
        or str(identity) != data["id"]
        or not isinstance(data.get("name"), str)
        or not data["name"]
        or data.get("name") != request.get("body", {}).get("name")
    ):
        return {}
    return {
        "kind": "controller_cluster",
        "id": str(identity),
        "name_digest": _cluster_name_digest(row, data["name"]),
    }


def trusted_cluster_ids(row, cluster_name):
    connection = NvkeConnection.objects.filter(site=row.site, surface="controller", is_enabled=True).first()
    if connection is None:
        return frozenset()
    references = (
        NvkeOperation.objects.filter(
            site_id=row.site_id,
            organization_id=row.organization_id,
            operation_key="createCluster",
            authority__connection_id=str(connection.guid),
            authority__revision=row.authority.get("revision"),
            authority__organization_namespace=row.authority.get("organization_namespace"),
            remote_reference__kind="controller_cluster",
            remote_reference__name_digest=_cluster_name_digest(row, cluster_name),
        )
        .values_list("remote_reference__id", flat=True)
        .iterator()
    )
    result = {
        str(value)
        for value in NvkeWorkloadAccessBinding.objects.filter(
            site_id=row.site_id,
            site__organization_id=row.organization_id,
            controller_connection=connection,
            upstream_revision=row.authority.get("revision"),
            name_digest=_cluster_name_digest(row, cluster_name),
        ).values_list("cluster_id", flat=True)
    }
    for value in references:
        try:
            identity = UUID(value)
        except (ValueError, TypeError, AttributeError):
            continue
        if identity.int and str(identity) == value:
            result.add(value)
    return frozenset(result)


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
    if row.operation_key == "infrastructure.providers":
        from astrolift_clusters.nvke_provider_observations import observation

        _org, caller = sites.authorized(Permission.NVKE_READ)
        _row, data = observation(
            site_id=str(row.site.guid),
            operation_id=str(row.guid),
            expected_actor_id=caller.pk,
            expected_version=row.version,
        )
        return {"provider_observation_format": 1, **data}
    sites.authorized(operation_permission(row.operation_key))
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
    sites.authorized(operation_permission(row.operation_key))
    with transaction.atomic():
        row = NvkeOperation.objects.select_for_update().get(pk=row.pk)
        if not row.terminal:
            row.cancellation_requested_at = timezone.now()
            row.save(update_fields=["cancellation_requested_at", "updated_at", "version"])
    return row


def resume(operation_id, *, expected_version, timeout_seconds=7200):
    row = read(operation_id)
    sites.authorized(Permission.NVKE_ADMIN)
    sites.authorized(operation_permission(row.operation_key))
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


def read_request(*, site_id, request_id):
    org, actor = sites.authorized(Permission.NVKE_READ)
    site = sites.site_row(site_id, org)
    try:
        request_id = UUID(str(request_id))
    except (ValueError, TypeError, AttributeError):
        return None
    return (
        NvkeOperation.objects.select_related("site")
        .filter(site=site, organization=org, actor=actor, request_id=request_id)
        .first()
    )
