"""Explicit enrollment from an identity-checked controller read, without native writes."""

from uuid import UUID

from django.db import transaction
from nvke.client import NvkeError
from nvke.contract import upstream_revision
from nvke.datastore import _name

from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeConnection, NvkeOperation, NvkeWorkloadAccessBinding
from core.permissions import Permission


def _actor(expected):
    org, actor = sites.authorized(Permission.NVKE_ADMIN)
    if type(expected) is not int or actor.pk != expected:
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE caller changed")
    return org, actor


def enroll(
    *, site_id, expected_site_version, expected_actor_id, source_operation_id, expected_source_version
):
    org, actor = _actor(expected_actor_id)
    sites.authorized(Permission.NVKE_READ)
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        sites._version(site, expected_site_version)
        if not site.is_active or site.upstream_revision != upstream_revision():
            raise NvkeError("SOURCE_CHANGED", "NVKE site authority changed")
        try:
            source_id = UUID(str(source_operation_id))
        except (ValueError, TypeError, AttributeError):
            raise NvkeError("NOT_FOUND", "NVKE observation not found") from None
        source = (
            NvkeOperation.objects.select_for_update()
            .filter(guid=source_id, site=site, organization=org)
            .first()
        )
        if (
            source is None
            or source.operation_key != "getCluster"
            or source.status != "succeeded"
            or type(expected_source_version) is not int
            or source.version != expected_source_version
            or source.authority.get("inventory") is True
        ):
            raise NvkeError(
                "SOURCE_CHANGED", "Choose a successful controller cluster read at its reviewed version"
            )
        connection = NvkeConnection.objects.filter(site=site, surface="controller", is_enabled=True).first()
        if (
            connection is None
            or source.expected_site_version != site.version
            or source.authority.get("connection_id") != str(connection.guid)
            or source.authority.get("connection_digest") != operations.connection_digest(connection)
            or source.authority.get("revision") != site.upstream_revision
            or source.authority.get("organization_namespace") != site.organization_namespace
        ):
            raise NvkeError("SOURCE_CHANGED", "Read the cluster again using the current controller authority")
        request = operations.decode_input(source)
        response = operations.decode_result(source).get("response", {})
        data = response.get("data")
        try:
            identity = UUID(data["id"])
            name = _name(data["name"])
            if (
                not identity.int
                or str(identity) != data["id"]
                or request.get("path", {}).get("clusterID") != str(identity)
                or type(response.get("status_code")) is not int
                or not 200 <= response["status_code"] < 300
            ):
                raise ValueError
        except (KeyError, ValueError, TypeError, AttributeError, NvkeError):
            raise NvkeError(
                "OWNERSHIP_UNVERIFIED", "The controller read did not prove the requested cluster identity"
            ) from None
        name_digest = operations._cluster_name_digest(source, name)
        revoked = (
            NvkeWorkloadAccessBinding.all_objects.filter(
                site=site,
                controller_connection=connection,
                cluster_id=identity,
                name_digest=name_digest,
                upstream_revision=site.upstream_revision,
                deleted_at__isnull=False,
            )
            .order_by("-deleted_at")
            .first()
        )
        if revoked is not None and (source.finished_at is None or source.finished_at <= revoked.deleted_at):
            raise NvkeError("SOURCE_CHANGED", "Read the cluster again after its imported grant was revoked")
        binding, _created = NvkeWorkloadAccessBinding.objects.get_or_create(
            site=site,
            controller_connection=connection,
            cluster_id=identity,
            name_digest=name_digest,
            upstream_revision=site.upstream_revision,
            defaults={
                "source_operation": source,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        return binding


def list_bindings(site_id):
    org, _actor = sites.authorized(Permission.NVKE_READ)
    site = sites.site_row(site_id, org)
    return NvkeWorkloadAccessBinding.objects.filter(site=site).order_by("created_at", "guid")


def revoke(*, site_id, expected_site_version, expected_actor_id, binding_id, expected_version):
    org, actor = _actor(expected_actor_id)
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        sites._version(site, expected_site_version)
        try:
            identity = UUID(str(binding_id))
        except (ValueError, TypeError, AttributeError):
            raise NvkeError("NOT_FOUND", "NVKE access binding not found") from None
        binding = (
            NvkeWorkloadAccessBinding.objects.select_for_update().filter(site=site, guid=identity).first()
        )
        if binding is None:
            raise NvkeError("NOT_FOUND", "NVKE access binding not found")
        sites._version(binding, expected_version)
        binding.soft_delete(by=actor)
        return binding
