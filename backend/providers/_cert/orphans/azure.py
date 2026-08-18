"""Azure orphan scanner: Postgres Flexible, Redis, Service Bus, Blob, role
assignments (spec 43 §3.2).

The AWS equivalents are RDS, ElastiCache, SQS, S3 and IAM. Azure's differences
from that list are the interesting part of this module.

*Service Bus entities carry no tags.* A namespace does, but the queues and
topics inside it do not: ``azure/managed/queue_servicebus.py`` packs the
ownership envelope into the ``userMetadata`` free-form string as a ``k=v;``
blob. So the scan parses that blob back into a tag mapping rather than
pretending an untagged entity is unowned. A queue left behind inside a surviving
namespace is residue that costs money and blocks the next run's name.

*Blob containers carry metadata, not tags.* The storage account has ARM tags;
the container has metadata with its own ``astrolift_io_*`` spelling. Both are
scanned, because teardown can leave either.

*Role assignments have no tag surface.* ``azure/identity_federated.py`` stamps a
fixed description on every grant it creates and prunes by it. That description
is the only durable marker, so an assignment is campaign residue when the
description matches and the principal or scope carries the campaign slug. A
grant that outlives its identity is the "no dangling IAM/role/grant" clause of
VERIFY-CLEAN, and on Azure it is the one most likely to be missed: deleting a
managed identity leaves its assignments behind as orphaned principal IDs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from _cert.orphans.model import CloudResource, ScanReport, scan_families

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from _cert.campaign import Campaign

CLOUD = "azure"

PLATFORM_ASSIGNMENT_DESCRIPTION = "astrolift.io/managed-by=platform workload-identity grant"
"""The marker ``azure/identity_federated.py`` writes on every grant it creates.
Duplicated deliberately rather than imported: importing the driver pulls the
Azure SDK into a scanner that must import cleanly wherever a harness runs, and
``tests/_cert/test_orphans_azure.py`` pins the two strings together."""


class AzureInventory(Protocol):
    """Read-only listings the scan needs. One method per resource family."""

    def postgres_flexible_servers(self) -> Iterable[CloudResource]: ...

    def redis_caches(self) -> Iterable[CloudResource]: ...

    def service_bus_namespaces(self) -> Iterable[CloudResource]: ...

    def service_bus_entities(self) -> Iterable[CloudResource]: ...

    def storage_accounts(self) -> Iterable[CloudResource]: ...

    def blob_containers(self) -> Iterable[CloudResource]: ...

    def managed_identities(self) -> Iterable[CloudResource]: ...

    def role_assignments(self) -> Iterable[CloudResource]: ...


def scan(inventory: AzureInventory, campaign: Campaign) -> ScanReport:
    """Everything in ``inventory`` that still belongs to ``campaign``.

    Call ``raise_if_dirty()`` on the result.
    """
    return scan_families(
        cloud=CLOUD,
        campaign=campaign,
        families=[
            ("postgres_flexible", inventory.postgres_flexible_servers),
            ("redis", inventory.redis_caches),
            ("service_bus_namespace", inventory.service_bus_namespaces),
            ("service_bus_entity", inventory.service_bus_entities),
            ("storage_account", inventory.storage_accounts),
            ("blob_container", inventory.blob_containers),
            ("managed_identity", inventory.managed_identities),
            ("role_assignment", inventory.role_assignments),
        ],
    )


def parse_user_metadata(blob: str) -> dict[str, str]:
    """Unpack the ``k=v;`` ownership blob a Service Bus entity carries.

    Tolerant on purpose: the writer truncates at 1024 characters, so the last
    pair can arrive without its value. A half-written pair is dropped rather
    than raising -- the entity is still found by name, and a scanner that dies
    on malformed input reports nothing at all.
    """
    tags: dict[str, str] = {}
    for pair in blob.split(";"):
        key, sep, value = pair.partition("=")
        if sep and key.strip() and value.strip():
            tags[key.strip()] = value.strip()
    return tags


def is_platform_assignment(description: str) -> bool:
    """Whether a role assignment is one the platform created.

    A separate function because it is the whole safety argument for the
    role-assignment family: the campaign slug can legitimately appear in a scope
    an operator granted by hand, and a scanner that reports those invites
    someone to delete them.
    """
    return description == PLATFORM_ASSIGNMENT_DESCRIPTION


def _attr(obj: Any, name: str, default: str = "") -> str:
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return str(value) if value else default


def _tags(obj: Any, name: str = "tags") -> dict[str, str]:
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    if not value:
        return {}
    return {str(k): str(v) for k, v in dict(value).items()}


class LiveAzureInventory:
    """The credentialed adapter, built from the clients the drivers use.

    Untested against a live subscription -- Phase 2 is its first real run. The
    scan logic it feeds is covered offline with fakes.
    """

    def __init__(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        postgres_client: Any | None = None,
        redis_client: Any | None = None,
        service_bus_client: Any | None = None,
        storage_client: Any | None = None,
        blob_client: Any | None = None,
        msi_client: Any | None = None,
        authorization_client: Any | None = None,
    ) -> None:
        self._subscription_id = subscription_id
        self._resource_group = resource_group
        self._postgres = postgres_client
        self._redis = redis_client
        self._service_bus = service_bus_client
        self._storage = storage_client
        self._blob = blob_client
        self._msi = msi_client
        self._authorization = authorization_client

    def _credential(self) -> Any:
        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential()

    def _postgres_client(self) -> Any:
        if self._postgres is None:
            from azure.mgmt.postgresqlflexibleservers import PostgreSQLManagementClient

            self._postgres = PostgreSQLManagementClient(
                credential=self._credential(),
                subscription_id=self._subscription_id,
            )
        return self._postgres

    def _redis_mgmt(self) -> Any:
        if self._redis is None:
            from azure.mgmt.redis import RedisManagementClient

            self._redis = RedisManagementClient(
                credential=self._credential(),
                subscription_id=self._subscription_id,
            )
        return self._redis

    def _service_bus_mgmt(self) -> Any:
        if self._service_bus is None:
            from azure.mgmt.servicebus import ServiceBusManagementClient

            self._service_bus = ServiceBusManagementClient(
                credential=self._credential(),
                subscription_id=self._subscription_id,
            )
        return self._service_bus

    def _storage_mgmt(self) -> Any:
        if self._storage is None:
            from azure.mgmt.storage import StorageManagementClient

            self._storage = StorageManagementClient(
                credential=self._credential(),
                subscription_id=self._subscription_id,
            )
        return self._storage

    def _msi_mgmt(self) -> Any:
        if self._msi is None:
            from azure.mgmt.msi import ManagedServiceIdentityClient

            self._msi = ManagedServiceIdentityClient(
                credential=self._credential(),
                subscription_id=self._subscription_id,
            )
        return self._msi

    def _authorization_mgmt(self) -> Any:
        if self._authorization is None:
            from azure.mgmt.authorization import AuthorizationManagementClient

            self._authorization = AuthorizationManagementClient(
                credential=self._credential(),
                subscription_id=self._subscription_id,
            )
        return self._authorization

    # -- families ------------------------------------------------------------

    def postgres_flexible_servers(self) -> Iterable[CloudResource]:
        client = self._postgres_client()
        for server in client.servers.list_by_resource_group(self._resource_group):
            yield CloudResource(
                identifier=_attr(server, "name"),
                location=_attr(server, "location"),
                tags=_tags(server),
            )

    def redis_caches(self) -> Iterable[CloudResource]:
        client = self._redis_mgmt()
        for cache in client.redis.list_by_resource_group(self._resource_group):
            yield CloudResource(
                identifier=_attr(cache, "name"),
                location=_attr(cache, "location"),
                tags=_tags(cache),
            )

    def service_bus_namespaces(self) -> Iterable[CloudResource]:
        client = self._service_bus_mgmt()
        for namespace in client.namespaces.list_by_resource_group(self._resource_group):
            yield CloudResource(
                identifier=_attr(namespace, "name"),
                location=_attr(namespace, "location"),
                tags=_tags(namespace),
            )

    def service_bus_entities(self) -> Iterable[CloudResource]:
        client = self._service_bus_mgmt()
        for namespace in client.namespaces.list_by_resource_group(self._resource_group):
            namespace_name = _attr(namespace, "name")
            for queue in client.queues.list_by_namespace(self._resource_group, namespace_name):
                yield CloudResource(
                    identifier=f"{namespace_name}/queues/{_attr(queue, 'name')}",
                    location=_attr(namespace, "location"),
                    tags=parse_user_metadata(_attr(queue, "user_metadata")),
                )
            for topic in client.topics.list_by_namespace(self._resource_group, namespace_name):
                yield CloudResource(
                    identifier=f"{namespace_name}/topics/{_attr(topic, 'name')}",
                    location=_attr(namespace, "location"),
                    tags=parse_user_metadata(_attr(topic, "user_metadata")),
                )

    def storage_accounts(self) -> Iterable[CloudResource]:
        client = self._storage_mgmt()
        for account in client.storage_accounts.list_by_resource_group(self._resource_group):
            yield CloudResource(
                identifier=_attr(account, "name"),
                location=_attr(account, "location"),
                tags=_tags(account),
            )

    def blob_containers(self) -> Iterable[CloudResource]:
        client = self._storage_mgmt()
        for account in client.storage_accounts.list_by_resource_group(self._resource_group):
            account_name = _attr(account, "name")
            for container in client.blob_containers.list(self._resource_group, account_name):
                yield CloudResource(
                    identifier=f"{account_name}/{_attr(container, 'name')}",
                    location=_attr(account, "location"),
                    tags=_metadata(container),
                )

    def managed_identities(self) -> Iterable[CloudResource]:
        client = self._msi_mgmt()
        for identity in client.user_assigned_identities.list_by_resource_group(self._resource_group):
            yield CloudResource(
                identifier=_attr(identity, "name"),
                location=_attr(identity, "location"),
                tags=_tags(identity),
            )

    def role_assignments(self) -> Iterable[CloudResource]:
        """Platform-created grants only.

        Filtering on the platform's description first is what keeps this from
        reporting an operator's own assignments: the campaign slug can appear in
        a scope path that a human granted deliberately, and deleting that would
        be the scanner causing the outage it is meant to prevent.

        Known limit: the campaign handle here has to come from the scope, since
        a principal id is a GUID. An assignment scoped at the resource group,
        for a principal whose identity is already deleted, carries nothing that
        names the campaign and will not be reported. Catching those needs the
        identity deleted last, which is the deprovision order the campaign is
        checking anyway.
        """
        client = self._authorization_mgmt()
        scope = f"/subscriptions/{self._subscription_id}"
        for assignment in client.role_assignments.list_for_scope(scope):
            if not is_platform_assignment(_attr(assignment, "description")):
                continue
            principal = _attr(assignment, "principal_id")
            assignment_scope = _attr(assignment, "scope")
            yield CloudResource(identifier=f"{assignment_scope} -> {principal}")


def _metadata(container: Any) -> Mapping[str, str]:
    """Blob-container metadata, which lives under ``metadata`` rather than
    ``tags`` and uses the ``astrolift_io_*`` spelling."""
    raw = container.get("metadata") if isinstance(container, dict) else getattr(container, "metadata", None)
    if not raw:
        return {}
    return {str(k): str(v) for k, v in dict(raw).items()}
