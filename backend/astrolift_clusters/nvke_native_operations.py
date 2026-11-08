"""Native operator admission uses the same encrypted, actor-owned durable outbox."""

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from nvke.client import NvkeError
from nvke.contract import upstream_revision
from nvke.native_workflows import validate_input, workflows

from astrolift_clusters import nvke_maintenance as maintenance
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters import nvke_temporal as native
from astrolift_clusters.models import NvkeConnection, NvkeOperation, NvkeTemporalAuthority
from astrolift_identity import abac
from astrolift_identity.api_tokens import get_current_api_token
from core.permissions import Permission
from core.secrets import encrypt_at_rest

PREFIX = "temporal."


def cluster_observation(site, actor, source_id, expected_source_version):
    with abac.request_attributes(replace(abac.attributes_for(actor.pk), cache={})):
        source, observation = maintenance.read(
            site_id=str(site.guid),
            operation_id=str(source_id),
            expected_actor_id=actor.pk,
            expected_version=expected_source_version,
        )
    controller = NvkeConnection.objects.filter(site=site, surface="controller", is_enabled=True).first()
    if (
        controller is None
        or source.expected_site_version != site.version
        or source.authority.get("connection_id") != str(controller.guid)
        or source.authority.get("connection_digest") != outbox.connection_digest(controller)
        or source.authority.get("revision") != site.upstream_revision
        or source.authority.get("organization_namespace") != site.organization_namespace
    ):
        raise NvkeError("SOURCE_CHANGED", "Review this cluster using the current controller authority")
    return source, observation, controller


def reserve_text(*, parameters_text, expected_actor_id, **kwargs):
    native._actor(expected_actor_id, require_read=True)
    try:
        if not isinstance(parameters_text, str) or len(parameters_text.encode("utf-8")) > 65536:
            raise ValueError
        parameters = json.loads(parameters_text)
    except (ValueError, TypeError, UnicodeError):
        raise NvkeError("INVALID_INPUT", "Use one bounded native workflow JSON input object") from None
    return reserve(expected_actor_id=expected_actor_id, parameters=parameters, **kwargs)


def workflow_name(key):
    if isinstance(key, str) and key.startswith(PREFIX):
        name = key[len(PREFIX) :]
        if any(item["name"] == name for item in workflows()):
            return name
    raise NvkeError("INVALID_INPUT", "Unknown native NVKE workflow")


def reserve(
    *,
    site_id,
    expected_site_version,
    expected_actor_id,
    expected_authority_version,
    request_id,
    workflow,
    parameters,
    source_operation_id,
    expected_source_version,
    acknowledge_lost_node_teardown=False,
    timeout_seconds=7200,
):
    org, actor = native._actor(expected_actor_id, require_read=True)
    sites.authorized(Permission.NVKE_READ)
    key = PREFIX + workflow
    name = workflow_name(key)
    parameters = validate_input(name, parameters)
    if type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 86400:
        raise NvkeError("INVALID_INPUT", "Use an observation deadline between 30 and 86400 seconds")
    if type(acknowledge_lost_node_teardown) is not bool or (
        name == "RetireLostEtcdRotationNode" and not acknowledge_lost_node_teardown
    ):
        raise NvkeError("PRECONDITION", "Explicitly acknowledge permanent lost control-plane node teardown")
    try:
        identity = UUID(str(request_id))
        source_id = UUID(str(source_operation_id))
        if not identity.int or not source_id.int:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise NvkeError("INVALID_INPUT", "Use nonzero UUID request and observation identities") from None
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        reviewed = {
            "site": str(site.guid),
            "site_version": expected_site_version,
            "authority_version": expected_authority_version,
            "key": key,
            "parameters": parameters,
            "source": str(source_id),
            "source_version": expected_source_version,
            "acknowledge_lost_node_teardown": acknowledge_lost_node_teardown,
            "timeout": timeout_seconds,
        }
        digest = hmac.new(
            str(settings.SECRET_KEY).encode(), outbox.canonical(reviewed), hashlib.sha256
        ).hexdigest()
        existing = (
            NvkeOperation.all_objects.select_for_update()
            .filter(site=site, actor=actor, request_id=identity)
            .first()
        )
        if existing is not None:
            if existing.deleted_at is not None or not hmac.compare_digest(existing.request_digest, digest):
                raise NvkeError("CONFLICT", "This request ID identifies a different NVKE request")
            if not existing.terminal:
                transaction.on_commit(lambda: outbox.dispatch(existing.pk))
            return existing
        sites._version(site, expected_site_version)
        if not site.is_active or site.upstream_revision != upstream_revision():
            raise NvkeError("SOURCE_CHANGED", "NVKE site authority or contract changed")
        authority = NvkeTemporalAuthority.objects.filter(site=site, is_enabled=True).first()
        if authority is None:
            raise NvkeError("SURFACE_UNAVAILABLE", "Configure explicit native Temporal authority first")
        sites._version(authority, expected_authority_version)
        source, observation, controller = cluster_observation(site, actor, source_id, expected_source_version)
        if observation["cluster_id"] != parameters["clusterID"]:
            raise NvkeError("SOURCE_CHANGED", "Review this cluster using the current controller authority")
        if name == "RetireLostEtcdRotationNode":
            rotation = observation.get("etcd_key_rotation") or {}
            if (
                rotation.get("workflow_id") != parameters["rotationWorkflowID"]
                or rotation.get("state") != "failed"
                or rotation.get("retained_mutation_lock") is not True
                or rotation.get("phase")
                not in {"add_secondary", "promote_primary", "rewrite", "remove_old_keys"}
            ):
                raise NvkeError(
                    "PRECONDITION", "Review the failed rotation's exact retained owner and recoverable phase"
                )
        if (
            NvkeOperation.objects.filter(
                site=site,
                operation_key__startswith=PREFIX,
                authority__native_cluster_id=parameters["clusterID"],
            )
            .exclude(status__in=["succeeded", "failed", "cancelled"])
            .exists()
        ):
            raise NvkeError("RESOURCE_BUSY", "Observe or reconcile the existing native maintenance request")
        token = get_current_api_token()
        sealed = encrypt_at_rest(outbox.canonical(parameters))
        row = NvkeOperation.objects.create(
            site=site,
            organization=org,
            actor=actor,
            request_id=identity,
            request_digest=digest,
            operation_key=key,
            expected_site_version=site.version,
            authority={
                "native_temporal_id": str(authority.guid),
                "native_temporal_digest": native.digest(authority),
                "native_cluster_id": parameters["clusterID"],
                "connection_id": str(controller.guid),
                "connection_digest": outbox.connection_digest(controller),
                "source_id": str(source.guid),
                "source_version": source.version,
                "token_id": str(token.guid) if token else None,
                "revision": upstream_revision(),
                "organization_namespace": site.organization_namespace,
            },
            input_backend_kind=sealed.backend_kind,
            input_ciphertext=sealed.backend_ref,
            deadline=timezone.now() + timedelta(seconds=timeout_seconds),
            created_by=actor,
            updated_by=actor,
        )
        transaction.on_commit(lambda: outbox.dispatch(row.pk))
        return row


def checkpoint(row):
    with outbox.original_actor(row):
        org, _actor = native._actor(row.actor_id)
        site = sites.site_row(row.site.guid, org)
        authority = (
            NvkeTemporalAuthority.objects.select_related("site")
            .filter(
                site=site,
                guid=row.authority.get("native_temporal_id"),
                is_enabled=True,
            )
            .first()
        )
        controller = NvkeConnection.objects.filter(
            site=site, guid=row.authority.get("connection_id"), is_enabled=True, surface="controller"
        ).first()
        if (
            not site.is_active
            or site.version != row.expected_site_version
            or site.upstream_revision != upstream_revision()
            or row.authority.get("revision") != site.upstream_revision
            or site.organization_namespace != row.authority.get("organization_namespace")
            or authority is None
            or native.digest(authority) != row.authority.get("native_temporal_digest")
            or controller is None
            or outbox.connection_digest(controller) != row.authority.get("connection_digest")
        ):
            raise NvkeError("SOURCE_CHANGED", "Native NVKE execution authority changed")
        return authority
