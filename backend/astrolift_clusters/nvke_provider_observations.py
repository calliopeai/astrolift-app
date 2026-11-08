"""Site-operator provider observations through the original actor's encrypted outbox."""

import copy
import hashlib
import hmac
import re
from dataclasses import asdict, replace
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from nvke.client import NvkeError
from nvke.contract import upstream_revision
from nvke.infrastructure import timestamp
from nvke.infrastructure_client import MAX_ROWS

from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeConnection, NvkeOperation
from astrolift_clusters.nvke_connections import open_infrastructure
from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken
from core.permissions import Permission, PermissionDenied
from core.secrets import encrypt_at_rest

KEY = "infrastructure.providers"
_SALT = "astrolift.nvke.provider-identities.v1"
_STATES = {"NOT-INSTALLED", "NOT-CONFIGURED", "DEGRADED", "CONFIGURED", "UNKNOWN"}
_HEALTH = {"HEALTHY", "FAILING", "UNKNOWN", "STALE", "PENDING", "NO-IDENTITY", "UNSUPPORTED", "UNREADABLE"}
_PROVIDER_REASONS = {
    "provider_crd_inventory_unreadable",
    "provider_crds_not_established",
    "provider_manager_inventory_unreadable",
    "provider_secret_reference_invalid",
    "provider_secret_reference_absent",
    "provider_secret_presence_unreadable",
    "provider_secret_absent",
    "provider_endpoint_absent",
    "provider_manager_not_found",
    "provider_manager_unavailable",
    "provider_configuration_present",
}
_CREDENTIAL_REASONS = {
    "identity_api_not_served",
    "identity_list_forbidden",
    "identity_list_unavailable",
    "no_identity_observes_secret",
    "invalid_last_checked_time",
    "last_checked_time_in_future",
    "provider_check_overdue",
    "provider_manager_not_found",
    "identity_outside_manager_namespace",
    "identity_outside_manager_filter",
    "provider_manager_unavailable",
    "current_generation_not_checked",
    "provider_check_succeeded",
    "provider_check_failed",
    "provider_check_unknown",
}


def actor(expected_actor_id):
    org, caller = sites.authorized(Permission.NVKE_ADMIN)
    if type(expected_actor_id) is not int or caller.pk != expected_actor_id:
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE provider caller changed")
    token = get_current_api_token()
    if token is not None:
        token = with_active_org_member(
            ApiToken.objects.filter(guid=token.guid, user=caller, organization=org, is_revoked=False),
            user="user",
            organization="organization",
        ).first()
        if token is None or (token.expires_at is not None and token.expires_at <= timezone.now()):
            raise NvkeError("AUTHORITY_REVOKED", "The NVKE provider caller is no longer authorized")
    mark = set_current_api_token(token)
    try:
        with abac.request_attributes(replace(abac.attributes_for(caller.pk), cache={})):
            sites.authorized(Permission.NVKE_ADMIN)
            sites.authorized(Permission.NVKE_READ)
    finally:
        reset_current_api_token(mark)
    return org, caller


def _uuid(value):
    try:
        result = UUID(str(value))
        if not result.int:
            raise ValueError
        return str(result)
    except (ValueError, TypeError, AttributeError):
        raise NvkeError("INVALID_INPUT", "Use nonzero NVKE request and connection UUIDs") from None


def reserve(
    *,
    site_id,
    expected_actor_id,
    expected_site_version,
    connection_id,
    expected_connection_version,
    request_id,
    timeout_seconds=600,
):
    org, caller = actor(expected_actor_id)
    identity, connection_id = _uuid(request_id), _uuid(connection_id)
    if type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 86400:
        raise NvkeError("INVALID_INPUT", "Use an observation deadline between 30 and 86400 seconds")
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        digest = hmac.new(
            str(settings.SECRET_KEY).encode(),
            outbox.canonical(
                {
                    "key": KEY,
                    "site": str(site.guid),
                    "site_version": expected_site_version,
                    "connection": connection_id,
                    "connection_version": expected_connection_version,
                    "timeout": timeout_seconds,
                }
            ),
            hashlib.sha256,
        ).hexdigest()
        existing = (
            NvkeOperation.all_objects.select_for_update()
            .filter(site=site, actor=caller, request_id=identity)
            .first()
        )
        if existing is not None:
            if existing.deleted_at is not None or not hmac.compare_digest(existing.request_digest, digest):
                raise NvkeError("CONFLICT", "This UUID identifies a different NVKE request")
            if not existing.terminal:
                transaction.on_commit(lambda: outbox.dispatch(existing.pk))
            return existing
        sites._version(site, expected_site_version)
        if not site.is_active or site.upstream_revision != upstream_revision():
            raise NvkeError("SOURCE_CHANGED", "NVKE site authority or contract changed")
        connection = NvkeConnection.objects.filter(
            site=site, guid=connection_id, surface="datastore", is_enabled=True
        ).first()
        if connection is None:
            raise NvkeError(
                "SURFACE_UNAVAILABLE", "Use the explicit enabled NVKE management-Kubernetes connection"
            )
        sites._version(connection, expected_connection_version)
        token = get_current_api_token()
        sealed = encrypt_at_rest(outbox.canonical({"provider_observation_format": 1}))
        row = NvkeOperation.objects.create(
            site=site,
            organization=org,
            actor=caller,
            request_id=UUID(identity),
            request_digest=digest,
            operation_key=KEY,
            expected_site_version=site.version,
            authority={
                "connection_id": str(connection.guid),
                "connection_version": connection.version,
                "connection_digest": outbox.connection_digest(connection),
                "revision": upstream_revision(),
                "organization_namespace": site.organization_namespace,
                "token_id": str(token.guid) if token else None,
            },
            input_backend_kind=sealed.backend_kind,
            input_ciphertext=sealed.backend_ref,
            deadline=timezone.now() + timedelta(seconds=timeout_seconds),
            created_by=caller,
            updated_by=caller,
        )
        transaction.on_commit(lambda: outbox.dispatch(row.pk))
        return row


def receipt_source(row):
    if row.operation_key != KEY:
        return None
    try:
        identity = str(UUID(row.authority["connection_id"]))
        version = row.authority["connection_version"]
        revision = row.authority["revision"]
        if (
            type(version) is not int
            or version <= 0
            or not isinstance(revision, str)
            or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        ):
            return None
        return {
            "connection_id": identity,
            "connection_version": version,
            "site_version": row.expected_site_version,
            "upstream_revision": revision,
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def checkpoint(row):
    with outbox.original_actor(row):
        org, _caller = actor(row.actor_id)
        site = sites.site_row(row.site.guid, org)
        if (
            not site.is_active
            or site.version != row.expected_site_version
            or site.upstream_revision != upstream_revision()
            or row.authority.get("revision") != site.upstream_revision
            or row.authority.get("organization_namespace") != site.organization_namespace
        ):
            raise NvkeError("SOURCE_CHANGED", "NVKE provider site authority changed")
        connection = NvkeConnection.objects.filter(
            site=site, guid=row.authority.get("connection_id"), surface="datastore", is_enabled=True
        ).first()
        if (
            connection is None
            or connection.version != row.authority.get("connection_version")
            or not hmac.compare_digest(
                outbox.connection_digest(connection), row.authority.get("connection_digest", "")
            )
        ):
            raise NvkeError("SOURCE_CHANGED", "NVKE management-Kubernetes authority changed")
        return connection


def _source(row):
    return {
        "operation": str(row.guid),
        "site": str(row.site.guid),
        "organization": row.organization_id,
        "actor": row.actor_id,
        "request_digest": row.request_digest,
        "generation": row.generation,
        "workflow_run_id": row.workflow_run_id,
    }


def attempt(row, *, execution):
    from astrolift_clusters.nvke_runtime import _state

    admitted_generation = row.generation
    admitted_authority = copy.deepcopy(row.authority)

    def transition(status, stage, **kwargs):
        row.generation = admitted_generation
        return _state(row, status, stage, expected_run_id=execution[1], **kwargs)

    def fence():
        current = NvkeOperation.objects.get(pk=row.pk)
        if (
            current.generation != admitted_generation
            or current.workflow_run_id != execution[1]
            or current.terminal
            or current.operation_key != KEY
            or current.authority != admitted_authority
        ):
            raise NvkeError("EXECUTION_REFUSED", "NVKE provider observation execution changed")
        if current.cancellation_requested_at:
            raise NvkeError("OPERATION_CANCELLED", "NVKE provider observation was cancelled")
        if timezone.now() >= current.deadline:
            raise NvkeError("DEADLINE_EXCEEDED", "NVKE provider observation deadline was reached")
        return checkpoint(row)

    try:
        connection = fence()
        parameters = outbox.decode_input(row)
        if (
            parameters != {"provider_observation_format": 1}
            or type(parameters.get("provider_observation_format")) is not int
        ):
            raise NvkeError("SOURCE_CHANGED", "NVKE provider observation input changed")
        row.generation = admitted_generation
        if transition("running", "provider_inventory_reading") == "execution_refused":
            return "execution_refused"
        with open_infrastructure(connection, checkpoint=fence) as client:
            observation = client.inspect()
        with transaction.atomic():
            sites.site_row(row.site.guid, row.organization, locked=True)
            NvkeOperation.objects.select_for_update().get(pk=row.pk)
            NvkeConnection.objects.select_for_update().get(pk=connection.pk)
            fence()
            return transition(
                "succeeded",
                "provider_inventory_observed",
                progress={
                    "provider_observation_format": 1,
                    "source": _source(row),
                    "connection_id": row.authority["connection_id"],
                    "connection_version": row.authority["connection_version"],
                    "observation": asdict(observation),
                },
            )
    except PermissionDenied:
        row.generation = admitted_generation
        return transition(
            "failed",
            "provider_inventory_refused",
            error=NvkeError("AUTHORITY_REVOKED", "The original provider caller is no longer authorized"),
        )
    except (NvkeError, sites.NvkeSiteError) as exc:
        if exc.code == "EXECUTION_REFUSED":
            return "execution_refused"
        row.generation = admitted_generation
        status = (
            "cancelled"
            if exc.code == "OPERATION_CANCELLED"
            else "awaiting_ready"
            if getattr(exc, "retryable", False)
            else "failed"
        )
        return transition(
            status,
            "provider_inventory_read_pending" if status == "awaiting_ready" else "provider_inventory_refused",
            error=NvkeError(exc.code, str(exc)),
        )
    except Exception:
        row.generation = admitted_generation
        return transition(
            "failed",
            "provider_inventory_refused",
            error=NvkeError(
                "OBSERVATION_UNAVAILABLE", "The NVKE provider observation could not be collected"
            ),
        )


def _bool(value, *, nullable=False):
    if type(value) is bool or (nullable and value is None):
        return value
    raise ValueError


def _count(value, *, minimum=0):
    if value is None:
        return None
    if type(value) is not int or not minimum <= value <= 9223372036854775807:
        raise ValueError
    return str(value)


def _name(value, *, namespace=False):
    from nvke.infrastructure import _NAME, _NAMESPACE

    if value is None:
        return None
    if not isinstance(value, str) or (_NAMESPACE if namespace else _NAME).fullmatch(value) is None:
        raise ValueError
    return value


def _date(value):
    if value is not None and timestamp(value) is None:
        raise ValueError
    return value


def _credentials(value):
    if value is None:
        return None, []
    fields = {
        "health",
        "reason",
        "identity_namespace",
        "identity_name",
        "identity_generation",
        "ready_status",
        "observed_generation",
        "last_checked_time",
        "next_check_time",
        "additional_identities",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value["health"] not in _HEALTH
        or value["reason"] not in _CREDENTIAL_REASONS
    ):
        raise ValueError
    result = {
        "health": value["health"],
        "reason": value["reason"],
        "identity_namespace": _name(value["identity_namespace"], namespace=True),
        "identity_name": _name(value["identity_name"]),
        "identity_generation": _count(value["identity_generation"], minimum=1),
        "ready_status": value["ready_status"],
        "observed_generation": _count(value["observed_generation"], minimum=1),
        "last_checked_time": _date(value["last_checked_time"]),
        "next_check_time": _date(value["next_check_time"]),
    }
    if result["ready_status"] not in {None, "True", "False", "Unknown"}:
        raise ValueError
    additional = value["additional_identities"]
    if not isinstance(additional, list) or len(additional) > MAX_ROWS:
        raise ValueError
    rows, seen = [], set()
    if result["identity_name"] is not None:
        seen.add((result["identity_namespace"], result["identity_name"]))
    for item in additional:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError
        namespace, name = _name(item[0], namespace=True), _name(item[1])
        if namespace is None or name is None or (namespace, name) in seen:
            raise ValueError
        seen.add((namespace, name))
        rows.append({"key": namespace + "/" + name, "namespace": namespace, "name": name})
    if (result["identity_namespace"] is None) != (result["identity_name"] is None):
        raise ValueError
    if result["health"] in {"HEALTHY", "FAILING"}:
        expected_status = "True" if result["health"] == "HEALTHY" else "False"
        expected_reason = (
            "provider_check_succeeded" if result["health"] == "HEALTHY" else "provider_check_failed"
        )
        if (
            result["identity_name"] is None
            or result["identity_generation"] is None
            or result["observed_generation"] != result["identity_generation"]
            or result["ready_status"] != expected_status
            or result["last_checked_time"] is None
            or result["reason"] != expected_reason
        ):
            raise ValueError
    result["additional_identity_count"] = len(rows)
    return result, sorted(rows, key=lambda r: (r["namespace"], r["name"]))


def _snapshot(*, site_id, operation_id, expected_actor_id, expected_version):
    _org, _caller = actor(expected_actor_id)
    row = outbox.read(operation_id)
    if str(row.site.guid) != str(site_id):
        raise NvkeError("NOT_FOUND", "NVKE provider observation not found")
    if (
        row.operation_key != KEY
        or row.status != "succeeded"
        or type(expected_version) is not int
        or row.version != expected_version
    ):
        raise NvkeError(
            "SNAPSHOT_UNAVAILABLE", "Use a successful provider observation at its reviewed version"
        )
    try:
        data = outbox.decode_result(row)
        if (
            not isinstance(data, dict)
            or set(data)
            != {"provider_observation_format", "source", "connection_id", "connection_version", "observation"}
            or type(data["provider_observation_format"]) is not int
            or data["provider_observation_format"] != 1
            or data["connection_id"] != row.authority["connection_id"]
            or str(UUID(data["connection_id"])) != data["connection_id"]
            or type(data["connection_version"]) is not int
            or data["connection_version"] <= 0
            or data["connection_version"] != row.authority["connection_version"]
        ):
            raise ValueError
        source, expected_source = data["source"], _source(row)
        if (
            not isinstance(source, dict)
            or set(source) != set(expected_source)
            or any(type(source[k]) is not type(v) or source[k] != v for k, v in expected_source.items())
        ):
            raise ValueError
        observation = data["observation"]
        if (
            not isinstance(observation, dict)
            or set(observation) != {"upstream_revision", "observed_at", "complete", "providers"}
            or not isinstance(observation["upstream_revision"], str)
            or re.fullmatch(r"[0-9a-f]{40}", observation["upstream_revision"]) is None
            or observation["upstream_revision"] != row.authority["revision"]
            or timestamp(observation["observed_at"]) is None
            or not isinstance(observation["providers"], list)
            or len(observation["providers"]) != 2
        ):
            raise ValueError
        rows, identities = [], {}
        for provider, value in zip(("nico", "launchlayer"), observation["providers"], strict=True):
            fields = {
                "provider",
                "state",
                "reason",
                "complete",
                "crds_established",
                "controller_available",
                "manager_namespace",
                "manager_name",
                "ready_replicas",
                "desired_replicas",
                "secret_namespace",
                "secret_name",
                "secret_present",
                "endpoint_configured",
                "unconfigured_is_valid",
                "credentials",
            }
            if (
                not isinstance(value, dict)
                or set(value) != fields
                or value["provider"] != provider
                or value["state"] not in _STATES
                or value["reason"] not in _PROVIDER_REASONS
                or value["unconfigured_is_valid"] is not (provider == "launchlayer")
            ):
                raise ValueError
            credentials, more = _credentials(value["credentials"])
            if provider == "launchlayer" and credentials is not None:
                raise ValueError
            rows.append(
                {
                    "provider": provider,
                    "state": value["state"],
                    "reason": value["reason"],
                    "complete": _bool(value["complete"]),
                    "crds_established": _bool(value["crds_established"], nullable=True),
                    "controller_available": _bool(value["controller_available"], nullable=True),
                    "manager_namespace": _name(value["manager_namespace"], namespace=True),
                    "manager_name": _name(value["manager_name"]),
                    "ready_replicas": _count(value["ready_replicas"]),
                    "desired_replicas": _count(value["desired_replicas"]),
                    "secret_namespace": _name(value["secret_namespace"], namespace=True),
                    "secret_name": _name(value["secret_name"]),
                    "secret_present": _bool(value["secret_present"], nullable=True),
                    "endpoint_configured": _bool(value["endpoint_configured"], nullable=True),
                    "unconfigured_is_valid": value["unconfigured_is_valid"],
                    "credentials": credentials,
                }
            )
            normalized = rows[-1]
            if normalized["state"] == "CONFIGURED" and (
                normalized["crds_established"] is not True
                or normalized["controller_available"] is not True
                or normalized["secret_present"] is not True
                or normalized["manager_name"] is None
                or normalized["manager_namespace"] is None
                or normalized["secret_name"] is None
                or normalized["secret_namespace"] is None
                or normalized["endpoint_configured"] is False
            ):
                raise ValueError
            if normalized["state"] == "NOT-INSTALLED" and (
                normalized["crds_established"] is not False or credentials is not None
            ):
                raise ValueError
            if normalized["state"] == "UNKNOWN" and normalized["complete"]:
                raise ValueError
            identities[provider] = more
        complete = _bool(observation["complete"])
        if complete != all(p["complete"] for p in rows):
            raise ValueError
        return (
            row,
            {
                "observed_at": observation["observed_at"],
                "upstream_revision": observation["upstream_revision"],
                "complete": complete,
                "connection_id": data["connection_id"],
                "connection_version": data["connection_version"],
                "providers": rows,
            },
            identities,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise NvkeError(
            "SNAPSHOT_UNAVAILABLE", "The saved NVKE provider observation is unavailable"
        ) from None


def observation(**kwargs):
    row, data, _identities = _snapshot(**kwargs)
    return row, data


def identities_page(*, provider, search="", limit=25, after=None, **kwargs):
    row, data, identities = _snapshot(**kwargs)
    if (
        not isinstance(provider, str)
        or provider not in {"nico", "launchlayer"}
        or not isinstance(search, str)
        or len(search) > 200
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise NvkeError("INVALID_INPUT", "Use a known provider and bounded identity search/page size")
    scope = {
        "actor": kwargs["expected_actor_id"],
        "org": row.organization_id,
        "site": str(row.site.guid),
        "operation": str(row.guid),
        "version": row.version,
        "provider": provider,
        "search": search,
        "limit": limit,
    }
    offset = 0
    if after is not None:
        try:
            cursor = signing.loads(after, salt=_SALT, max_age=3600)
            offset = cursor["offset"]
            if cursor["scope"] != scope or type(offset) is not int or offset < 0:
                raise ValueError
        except Exception:
            raise NvkeError(
                "INVALID_CURSOR", "The provider identity cursor is invalid for this view"
            ) from None
    values = [
        item
        for item in identities[provider]
        if search.casefold() in (item["namespace"] + "/" + item["name"]).casefold()
    ]
    if offset > len(values):
        raise NvkeError("INVALID_CURSOR", "The provider identity cursor is invalid for this view")
    page = values[offset : offset + limit]
    next_cursor = (
        signing.dumps({"scope": scope, "offset": offset + limit}, salt=_SALT, compress=True)
        if offset + limit < len(values)
        else None
    )
    return row, data["observed_at"], page, next_cursor, len(values)
