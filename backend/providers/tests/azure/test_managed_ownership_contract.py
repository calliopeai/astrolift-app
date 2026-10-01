"""Every Azure managed-resource driver refuses a foreign-owned resource (#1365).

#1443 put one verifier behind the nine drivers that already carried a private
``_assert_owned``. The drivers here never had an ownership check of any kind:
they derive a deterministic name and call update or delete against whatever
answers to it. ``postgres_flexible`` was the worst of them, going straight from
``_describe`` to ``servers.begin_delete``, so an operator-built server that
collided on the generated name was deleted.

This is one test over the real driver classes rather than fourteen copies,
because the property being asserted is the same property in every case and a
per-driver copy is how the checks drifted apart in the first place. Each case
below builds the driver against the same fakes its own suite uses, provisions
through the real provision path so the identity envelope is written the way the
driver really writes it, then rewrites the live resource's identity to another
managed service.

The delete leg also asserts the resource survives. A refusal that still issued
the destructive call is the bug, not the message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from _sdk.azure_ownership import OWNERSHIP_ERROR_CODE
from _sdk.managed_service import DeprovisionSpec, UpdateSpec
from _sdk.managed_service_tags import canonical_key
from tests.azure import (
    test_managed_cache_redis as cache_redis_suite,
)
from tests.azure import (
    test_managed_cosmos as cosmos_suite,
)
from tests.azure import (
    test_managed_email_acs as email_acs_suite,
)
from tests.azure import (
    test_managed_encryption_key_vault as key_vault_suite,
)
from tests.azure import (
    test_managed_managed_redis as managed_redis_suite,
)
from tests.azure import (
    test_managed_model_endpoint_aoai as aoai_suite,
)
from tests.azure import (
    test_managed_mssql_sql as mssql_suite,
)
from tests.azure import (
    test_managed_mysql_flexible as mysql_suite,
)
from tests.azure import (
    test_managed_object_store_azure_blob as blob_suite,
)
from tests.azure import (
    test_managed_object_store_blob as blob_legacy_suite,
)
from tests.azure import (
    test_managed_postgres_flexible as postgres_suite,
)
from tests.azure import (
    test_managed_queue_azure_servicebus as servicebus_suite,
)
from tests.azure import (
    test_managed_queue_servicebus as servicebus_legacy_suite,
)
from tests.azure import (
    test_managed_search_aisearch as search_suite,
)
from tests.azure import (
    test_managed_timeseries_monitor as monitor_suite,
)
from tests.azure import (
    test_managed_vector_search as vector_suite,
)

if TYPE_CHECKING:
    from collections.abc import Callable

ARM_ID_KEY = canonical_key("azure")
BLOB_ID_KEY = "astrolift_io_managed_service_id"
FOREIGN = "some-other-managed-service"


@dataclass
class Live:
    """A provisioned resource plus the handful of levers this contract needs."""

    driver: Any
    handle: str
    owner: str
    read_owner: Callable[[], str]
    write_owner: Callable[[str], None]
    exists: Callable[[], bool]
    reprovision: Callable[[], Any]
    #: ``None`` for a driver whose ``update`` performs no provider work and so
    #: has nothing to gate; its refusal surface is provision and deprovision.
    update_kwargs: dict[str, Any] | None = field(default_factory=lambda: {"size": "medium"})
    deprovision_kwargs: dict[str, Any] = field(
        default_factory=lambda: {"delete_data": True, "force_destroy": True},
    )


def _tagged(store: dict[str, Any], name: str, key: str = ARM_ID_KEY) -> tuple[Callable[[], str], Callable[[str], None]]:
    return (
        lambda: str(store[name].tags.get(key, "")),
        lambda value: store[name].tags.__setitem__(key, value),
    )


def _postgres() -> Live:
    mgmt = postgres_suite.FakeMgmtClient()
    driver = postgres_suite.AzurePostgresFlexibleDriver(
        config=postgres_suite.AzurePostgresConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=postgres_suite.FakeSecretClient(),
        ),
    )
    result = driver.provision(postgres_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    servers = mgmt.servers_obj.servers
    read, write = _tagged(servers, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=postgres_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: name in servers,
        reprovision=lambda: driver.provision(postgres_suite._spec()),
    )


def _mysql() -> Live:
    mgmt = mysql_suite.FakeMgmtClient()
    driver = mysql_suite.AzureMySQLFlexibleDriver(
        config=mysql_suite.AzureMySQLConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=mysql_suite.FakeSecretClient(),
        ),
    )
    result = driver.provision(mysql_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    servers = mgmt.servers_obj.servers
    read, write = _tagged(servers, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=mysql_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: name in servers,
        reprovision=lambda: driver.provision(mysql_suite._spec()),
    )


def _cache_redis() -> Live:
    mgmt = cache_redis_suite.FakeMgmtClient()
    driver = cache_redis_suite.AzureCacheRedisDriver(
        config=cache_redis_suite.AzureCacheRedisConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=cache_redis_suite.FakeSecretClient(),
        ),
    )
    result = driver.provision(cache_redis_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    caches = mgmt.redis_obj.caches
    read, write = _tagged(caches, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=cache_redis_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: name in caches,
        reprovision=lambda: driver.provision(cache_redis_suite._spec()),
    )


def _cosmos() -> Live:
    mgmt = cosmos_suite.FakeMgmtClient()
    driver = cosmos_suite.AzureCosmosDriver(
        config=cosmos_suite.AzureCosmosConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            locks_client=cosmos_suite.FakeLocksClient(),
            secret_client=cosmos_suite.FakeSecretClient(),
        ),
    )
    result = driver.provision(cosmos_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    accounts = mgmt.database_accounts_obj.accounts
    read, write = _tagged(accounts, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=cosmos_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: name in accounts,
        reprovision=lambda: driver.provision(cosmos_suite._spec()),
    )


def _email_acs() -> Live:
    mgmt = email_acs_suite.FakeMgmtClient()
    driver = email_acs_suite.AzureCommunicationEmailDriver(
        config=email_acs_suite.AzureCommunicationEmailConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            email_service_name="astrolift-email",
            communication_resource_id=email_acs_suite._COMM_RESOURCE_ID,
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=email_acs_suite.FakeSecretClient(),
            locks_client=email_acs_suite.FakeLocksClient(),
        ),
    )
    result = driver.provision(email_acs_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    domains = mgmt.domains_obj.domains
    read, write = _tagged(domains, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=email_acs_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: name in domains,
        reprovision=lambda: driver.provision(email_acs_suite._spec()),
        update_kwargs={"config": {"user_engagement_tracking": "Enabled"}},
    )


def _aoai() -> Live:
    mgmt = aoai_suite.FakeMgmtClient()
    driver = aoai_suite.AzureOpenAIDriver(
        config=aoai_suite.AzureOpenAIConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            account_name="aoai-acme",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=aoai_suite.FakeSecretClient(),
        ),
    )
    result = driver.provision(aoai_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    deployments = mgmt.deployments_obj.deployments
    read, write = _tagged(deployments, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=aoai_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: name in deployments,
        reprovision=lambda: driver.provision(aoai_suite._spec()),
    )


def _search() -> Live:
    mgmt = search_suite.FakeMgmtClient()
    driver = search_suite.AzureAISearchFullTextDriver(
        config=search_suite.AzureAISearchConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=search_suite.FakeSecretClient(),
        ),
    )
    result = driver.provision(search_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    services = mgmt.services_obj.services
    read, write = _tagged(services, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=search_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: name in services,
        reprovision=lambda: driver.provision(search_suite._spec()),
    )


def _vector_search() -> Live:
    mgmt = vector_suite.FakeMgmtClient()
    driver = vector_suite.AzureAISearchVectorDriver(
        config=vector_suite.AzureAISearchVectorConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=vector_suite.FakeSecretClient(),
            index_client_factory=vector_suite._index_factory,
        ),
    )
    result = driver.provision(vector_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    services = mgmt.services_obj.services
    read, write = _tagged(services, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=vector_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: name in services,
        reprovision=lambda: driver.provision(vector_suite._spec()),
    )


def _timeseries() -> Live:
    monitor = monitor_suite.FakeMonitorClient()
    driver = monitor_suite.AzureMonitorPrometheusDriver(
        config=monitor_suite.AzureMonitorPrometheusConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            monitor_client=monitor,
            log_analytics_client=monitor_suite.FakeLogAnalyticsClient(),
            locks_client=monitor_suite.FakeLocksClient(),
        ),
    )
    result = driver.provision(monitor_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    workspaces = monitor.azure_monitor_workspaces_obj.workspaces
    read, write = _tagged(workspaces, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=monitor_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: name in workspaces,
        reprovision=lambda: driver.provision(monitor_suite._spec()),
    )


def _managed_redis() -> Live:
    management = managed_redis_suite.FakeManagement()
    driver = managed_redis_suite.AzureManagedRedisDriver(
        config=managed_redis_suite.AzureManagedRedisConfig(
            subscription_id="subscription-id",
            resource_group="rg-platform",
            location="eastus",
            keyvault_url="https://vault.vault.azure.net",
            mgmt_client=management,
            secret_client=managed_redis_suite.FakeSecrets(),
        ),
    )
    result = driver.provision(managed_redis_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/")[1]
    clusters = management.clusters
    read, write = _tagged(clusters, name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner="managed-service-id",
        read_owner=read,
        write_owner=write,
        exists=lambda: name in clusters,
        reprovision=lambda: driver.provision(managed_redis_suite._spec()),
    )


def _mssql_database() -> Live:
    driver, mgmt, _ = mssql_suite._database_driver()
    result = driver.provision(mssql_suite._spec())
    assert result.ok, result.message
    _, server_name, database_name = result.handle.split("/")
    rows = mgmt.databases.rows
    key = (server_name, database_name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=mssql_suite.OWNER,
        read_owner=lambda: str(rows[key].tags.get(ARM_ID_KEY, "")),
        write_owner=lambda value: rows[key].tags.__setitem__(ARM_ID_KEY, value),
        exists=lambda: key in rows,
        reprovision=lambda: driver.provision(mssql_suite._spec()),
    )


def _mssql_managed_instance() -> Live:
    driver, mgmt, _ = mssql_suite._mi_driver()
    result = driver.provision(mssql_suite._spec())
    assert result.ok, result.message
    instance_name = result.handle.split("/")[1]
    rows = mgmt.managed_instances.rows
    read, write = _tagged(rows, instance_name)
    return Live(
        driver=driver,
        handle=result.handle,
        owner=mssql_suite.OWNER,
        read_owner=read,
        write_owner=write,
        exists=lambda: instance_name in rows,
        reprovision=lambda: driver.provision(mssql_suite._spec()),
    )


def _blob_container() -> Live:
    client = blob_suite.FakeBlobServiceClient()
    driver = blob_suite.AzureBlobStorageDriver(
        config=blob_suite.AzureBlobConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            storage_account="acmeprod",
            blob_service_client=client,
        ),
    )
    result = driver.provision(blob_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    containers = client.containers
    return Live(
        driver=driver,
        handle=result.handle,
        owner=blob_suite.OWNER,
        read_owner=lambda: str(containers[name].metadata.get(BLOB_ID_KEY, "")),
        write_owner=lambda value: containers[name].metadata.__setitem__(BLOB_ID_KEY, value),
        exists=lambda: name in containers and containers[name].created,
        reprovision=lambda: driver.provision(blob_suite._spec()),
        update_kwargs=None,
    )


def _blob_container_legacy() -> Live:
    client = blob_legacy_suite.FakeBlobServiceClient()
    driver = blob_legacy_suite.BlobStorageDriver(
        config=blob_legacy_suite.BlobStorageConfig(
            storage_account="acmeprod",
            blob_service_client=client,
        ),
    )
    result = driver.provision(blob_legacy_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    containers = client.containers
    return Live(
        driver=driver,
        handle=result.handle,
        owner=blob_legacy_suite.OWNER,
        read_owner=lambda: str(containers[name].metadata.get(BLOB_ID_KEY, "")),
        write_owner=lambda value: containers[name].metadata.__setitem__(BLOB_ID_KEY, value),
        exists=lambda: name in containers and containers[name].created,
        reprovision=lambda: driver.provision(blob_legacy_suite._spec()),
        update_kwargs=None,
    )


def _servicebus_owner(entity: Any) -> str:
    for pair in str(entity.user_metadata or "").split(";"):
        key, sep, value = pair.partition("=")
        if sep and key == ARM_ID_KEY:
            return value
    return ""


def _servicebus_set_owner(entity: Any, value: str) -> None:
    kept = [
        pair for pair in str(entity.user_metadata or "").split(";") if pair and not pair.startswith(f"{ARM_ID_KEY}=")
    ]
    entity.user_metadata = ";".join([*kept, f"{ARM_ID_KEY}={value}"])


def _servicebus_topic() -> Live:
    from .test_servicebus_queue_wire_2032 import OWNER, spec
    from .test_servicebus_topic_wire_2032 import RecordingTopics, recording_config

    api = RecordingTopics()
    driver = servicebus_suite.AzureServiceBusDriver(config=recording_config(api))
    result = driver.provision(spec())
    assert result.ok, result.message
    target = driver._saved_target(result.handle, spec())
    properties = api.rows[target.topic_id]["properties"]

    def write(value: str) -> None:
        kept = [part for part in properties["userMetadata"].split(";") if not part.startswith(f"{ARM_ID_KEY}=")]
        properties["userMetadata"] = ";".join([*kept, f"{ARM_ID_KEY}={value}"])

    return Live(
        driver=driver,
        handle=result.handle,
        owner=OWNER,
        read_owner=lambda: next(
            part.partition("=")[2]
            for part in properties["userMetadata"].split(";")
            if part.startswith(f"{ARM_ID_KEY}=")
        ),
        write_owner=write,
        exists=lambda: target.topic_id in api.rows,
        reprovision=lambda: driver.provision(spec(recorded_handle=result.handle)),
        update_kwargs={"config": {"max_size_in_megabytes": 2048}},
    )


def _servicebus_queue_legacy() -> Live:
    client = servicebus_legacy_suite.FakeSBClient()
    driver = servicebus_legacy_suite.ServiceBusDriver(
        config=servicebus_legacy_suite.ServiceBusConfig(
            subscription_id=servicebus_legacy_suite.SUBSCRIPTION,
            resource_group="rg",
            namespace_name="acme-prod-sb",
            client=client,
        ),
    )
    result = driver.provision(servicebus_legacy_suite._spec())
    assert result.ok, result.message
    name = result.handle.split("/", 1)[1]
    queues = client.queues_obj.queues
    return Live(
        driver=driver,
        handle=result.handle,
        owner=servicebus_legacy_suite.OWNER,
        read_owner=lambda: _servicebus_owner(queues[name]),
        write_owner=lambda value: _servicebus_set_owner(queues[name], value),
        exists=lambda: name in queues,
        reprovision=lambda: driver.provision(servicebus_legacy_suite._spec()),
        update_kwargs=None,
    )


def _key_vault_key() -> Live:
    vault = key_vault_suite.FakeKeyVault()
    driver, _ = key_vault_suite._driver(vault)
    result = driver.provision(key_vault_suite._spec())
    assert result.ok, result.message
    name = key_vault_suite._key_name(result.handle)
    return Live(
        driver=driver,
        handle=result.handle,
        owner="service-1",
        read_owner=lambda: str(vault.keys[name]["tags"].get(ARM_ID_KEY, "")),
        write_owner=lambda value: vault.keys[name]["tags"].__setitem__(ARM_ID_KEY, value),
        exists=lambda: name in vault.keys,
        reprovision=lambda: driver.provision(key_vault_suite._spec()),
        update_kwargs={"config": {"enabled": False}},
    )


CASES: dict[str, Callable[[], Live]] = {
    "postgres_flexible": _postgres,
    "mysql_flexible": _mysql,
    "cache_redis": _cache_redis,
    "cosmos": _cosmos,
    "email_acs": _email_acs,
    "model_endpoint_aoai": _aoai,
    "search_aisearch": _search,
    "vector_search": _vector_search,
    "timeseries_monitor": _timeseries,
    "managed_redis": _managed_redis,
    "mssql_sql_database": _mssql_database,
    "mssql_managed_instance": _mssql_managed_instance,
    "object_store_blob": _blob_container,
    "object_store_blob_legacy": _blob_container_legacy,
    "queue_servicebus_topic": _servicebus_topic,
    "queue_servicebus_queue_legacy": _servicebus_queue_legacy,
    "key_vault_key": _key_vault_key,
}


@pytest.fixture(params=sorted(CASES), ids=sorted(CASES))
def live(request: pytest.FixtureRequest) -> Live:
    return CASES[request.param]()


def _ownership_errors(live: Live) -> list[str]:
    if isinstance(live.driver, (servicebus_legacy_suite.ServiceBusDriver, servicebus_suite.AzureServiceBusDriver)):
        return [OWNERSHIP_ERROR_CODE, "ownership_refused"]
    return [OWNERSHIP_ERROR_CODE]


def test_provision_stamps_an_identity_its_own_read_can_recover(live: Live) -> None:
    """The write side and the read side have to name the same thing.

    A driver that writes an envelope its verifier cannot read refuses its own
    resource on every later update and teardown, which fails closed into an
    undeletable resource rather than a safe one.
    """
    assert live.read_owner() == live.owner


def test_foreign_owner_is_refused_on_delete(live: Live) -> None:
    live.write_owner(FOREIGN)

    refused = live.driver.deprovision(
        DeprovisionSpec(handle=live.handle, managed_service_id=live.owner),
        **live.deprovision_kwargs,
    )

    assert not refused.ok
    assert refused.errors == _ownership_errors(live)
    assert not refused.retryable, "an ownership refusal is permanent; retrying burns the delete window"
    assert live.exists(), "refused the teardown but deleted the resource anyway"


def test_foreign_owner_is_refused_on_update(live: Live) -> None:
    if live.update_kwargs is None:
        pytest.skip("driver performs no provider work on update")
    live.write_owner(FOREIGN)

    refused = live.driver.update(
        UpdateSpec(handle=live.handle, managed_service_id=live.owner, **live.update_kwargs),
    )

    assert not refused.ok
    assert refused.errors == _ownership_errors(live)
    assert not refused.retryable


def test_foreign_owner_is_refused_on_reprovision(live: Live) -> None:
    """Reconcile is the quietest way to adopt somebody else's resource.

    Provision short-circuits on an existing name and reports success, so
    without this gate a collision hands the platform a resource it never made
    and every later operation treats it as ours.
    """
    live.write_owner(FOREIGN)

    refused = live.reprovision()

    assert not refused.ok
    assert refused.errors == _ownership_errors(live)


def test_untagged_resource_is_refused_rather_than_adopted(live: Live) -> None:
    """The absent marker is the case a naive ``!=`` check lets through.

    Adoption of an unowned resource is deliberately not implemented; until it
    is a separately authorized operation, an untagged resource is refused.
    """
    live.write_owner("")

    refused = live.driver.deprovision(
        DeprovisionSpec(handle=live.handle, managed_service_id=live.owner),
        **live.deprovision_kwargs,
    )

    assert not refused.ok
    assert refused.errors == _ownership_errors(live)
    assert live.exists()


def test_our_own_resource_still_reconciles_and_tears_down(live: Live) -> None:
    reconciled = live.reprovision()
    assert reconciled.ok, reconciled.message
    assert live.exists()

    torn_down = live.driver.deprovision(
        DeprovisionSpec(handle=live.handle, managed_service_id=live.owner),
        **live.deprovision_kwargs,
    )

    assert torn_down.ok, torn_down.message
    assert not live.exists()


def test_teardown_replay_after_our_own_delete_stays_idempotent(live: Live) -> None:
    """Temporal retries teardown. The gate must not turn the second call into a
    refusal just because there is no longer a resource to prove ownership of."""
    first = live.driver.deprovision(
        DeprovisionSpec(handle=live.handle, managed_service_id=live.owner),
        **live.deprovision_kwargs,
    )
    assert first.ok, first.message

    second = live.driver.deprovision(
        DeprovisionSpec(handle=live.handle, managed_service_id=live.owner),
        **live.deprovision_kwargs,
    )

    assert second.ok, second.message


def test_every_driver_without_a_prior_check_is_covered() -> None:
    """A rename or a dropped case would make the suite above quietly shrink."""
    assert set(CASES) >= {
        "cache_redis",
        "cosmos",
        "email_acs",
        "key_vault_key",
        "managed_redis",
        "model_endpoint_aoai",
        "mssql_managed_instance",
        "mssql_sql_database",
        "mysql_flexible",
        "object_store_blob",
        "object_store_blob_legacy",
        "postgres_flexible",
        "queue_servicebus_queue_legacy",
        "queue_servicebus_topic",
        "search_aisearch",
        "timeseries_monitor",
        "vector_search",
    }


def test_the_caller_must_name_an_owner_before_a_destructive_call(live: Live) -> None:
    """A ``DeprovisionSpec`` with no identity is the pre-#1443 teardown.

    It cannot prove anything, so it has to refuse rather than fall back to
    "Astrolift made this, close enough" -- that is what made a sibling
    binding's resource deletable.
    """
    refused = live.driver.deprovision(DeprovisionSpec(handle=live.handle), **live.deprovision_kwargs)

    assert not refused.ok
    assert refused.errors == _ownership_errors(live)
    assert live.exists()


def test_a_sibling_binding_cannot_delete_our_resource(live: Live) -> None:
    """The collision that org/app/env tags cannot distinguish.

    Two rows for the same app and environment agree on every descriptive tag;
    only the managed-service id separates them.
    """
    refused = live.driver.deprovision(
        DeprovisionSpec(handle=live.handle, managed_service_id=FOREIGN),
        **live.deprovision_kwargs,
    )

    assert not refused.ok
    assert refused.errors == _ownership_errors(live)
    assert live.exists()
