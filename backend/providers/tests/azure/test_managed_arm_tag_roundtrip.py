"""ARM tag round trip: what a driver writes has to be what it can read back.

#1431: five drivers built the canonical ``astrolift.io/<name>`` dict and handed
it to the SDK unserialized. ARM rejects ``/`` in a tag name, so none of it could
land, and the ownership checks read those same unserialized keys back, so each
driver agreed with itself while describing tags no resource could carry. Every
per-driver test stayed green, because the fakes store whatever the driver sends.

The pairing below is what closes that. Assert the written names are names ARM
will accept, then assert the driver still recognises the resource those names
describe. Either half on its own passes straight through the bug.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

from _sdk.azure_tags import AZURE_TAG_NAME_LIMIT
from _sdk.managed_service_tags import is_owned_by, ownership_key

from . import test_managed_cosmos_api as cosmos_api_tests
from . import test_managed_event_grid as event_grid_tests
from . import test_managed_event_grid_namespace as event_grid_namespace_tests
from . import test_managed_event_hubs as event_hubs_tests
from . import test_managed_managed_redis as managed_redis_tests
from . import test_managed_mssql_sql as mssql_sql_tests

if TYPE_CHECKING:
    from collections.abc import Callable

#: The characters ARM refuses in a tag name, from the Azure naming rules that
#: ``_sdk/azure_tags`` encodes.
ARM_FORBIDDEN = re.compile(r"[<>%&\\?/]")


class RoundTrip(NamedTuple):
    """Tags a driver sent the cloud, plus a way to make it read them back."""

    written: dict[str, str]
    managed_service_id: str
    #: Re-runs provision against the resource the first call created, which is
    #: the path that hits the driver's ownership check.
    reconcile: Callable[[], Any]


def _event_grid() -> RoundTrip:
    driver, mgmt, _ = event_grid_tests._driver()
    spec = event_grid_tests._spec()
    assert driver.provision(spec).ok
    written = dict(mgmt.topics.create_calls[0]["parameters"].tags)
    return RoundTrip(written, spec.managed_service_id, lambda: driver.provision(spec))


def _event_grid_namespace() -> RoundTrip:
    driver, mgmt, _, _ = event_grid_namespace_tests._driver()
    spec = event_grid_namespace_tests._spec()
    assert driver.provision(spec).ok
    written = dict(mgmt.namespaces.create_calls[0]["parameters"].tags)
    return RoundTrip(written, spec.managed_service_id, lambda: driver.provision(spec))


def _event_hubs() -> RoundTrip:
    driver, mgmt, _ = event_hubs_tests._driver("event_hubs")
    spec = event_hubs_tests._spec()
    assert driver.provision(spec).ok
    written = dict(mgmt.namespaces.create_calls[0]["parameters"].as_dict()["tags"])
    return RoundTrip(written, spec.managed_service_id, lambda: driver.provision(spec))


def _cosmos_api() -> RoundTrip:
    driver, mgmt, _, _ = cosmos_api_tests._driver("cosmos_nosql")
    spec = cosmos_api_tests._spec()
    assert driver.provision(spec).ok
    written = dict(mgmt.database_accounts.create_calls[0]["parameters"].as_dict()["tags"])
    return RoundTrip(written, spec.managed_service_id, lambda: driver.provision(spec))


def _managed_redis() -> RoundTrip:
    driver, mgmt, _ = managed_redis_tests._driver()
    spec = managed_redis_tests._spec()
    assert driver.provision(spec).ok
    create = next(call for call in mgmt.calls if call[0] == "cluster.create")
    written = dict(create[-1].tags)
    return RoundTrip(written, spec.managed_service_id, lambda: driver.provision(spec))


def _mssql_sql() -> RoundTrip:
    driver, mgmt, _ = mssql_sql_tests._database_driver()
    spec = mssql_sql_tests._spec(managed_service_id="service-id", binding_id="binding-id")
    assert driver.provision(spec).ok
    written = dict(mgmt.servers.create_calls[0]["parameters"].tags)
    return RoundTrip(written, spec.managed_service_id, lambda: driver.provision(spec))


DRIVERS = {
    "event_grid": _event_grid,
    "event_grid_namespace": _event_grid_namespace,
    "event_hubs": _event_hubs,
    "cosmos_api": _cosmos_api,
    "managed_redis": _managed_redis,
    "mssql_sql": _mssql_sql,
}


@pytest.mark.parametrize("driver_name", sorted(DRIVERS))
def test_the_driver_writes_arm_valid_names_and_reads_its_own_resource_back(driver_name: str) -> None:
    trip = DRIVERS[driver_name]()

    invalid = sorted(key for key in trip.written if ARM_FORBIDDEN.search(key) or len(key) > AZURE_TAG_NAME_LIMIT)
    assert not invalid, f"{driver_name} sends ARM tag names it will reject: {invalid}"

    assert is_owned_by(trip.written, trip.managed_service_id, "azure"), (
        f"{driver_name} writes tags that the shared ownership reader cannot attribute back to "
        f"managed_service_id={trip.managed_service_id!r}"
    )

    second = trip.reconcile()
    assert second.ok, f"{driver_name} refuses to adopt the resource it just created: {second.message}"


@pytest.mark.parametrize("driver_name", sorted(DRIVERS))
def test_the_ownership_envelope_survives_the_write(driver_name: str) -> None:
    """A resource carrying none of these cannot be recognised as ours at all.

    The managed-service id alone is not enough: adoption, binding conflicts and
    cost attribution each read a different key off the same envelope.
    """
    trip = DRIVERS[driver_name]()

    for tag in ("managed_by", "org", "app", "env", "cluster"):
        assert trip.written.get(ownership_key("azure", tag)), f"{driver_name} did not write {tag}"
    assert trip.written[ownership_key("azure", "managed_by")] == "platform"


def test_a_foreign_resource_is_still_refused() -> None:
    """The fix must not turn the ownership check into a rubber stamp.

    Reading a key nothing ever wrote fails closed by accident. Reading the right
    key has to keep failing closed on purpose.
    """
    driver, mgmt, _ = event_grid_tests._driver()
    spec = event_grid_tests._spec()
    assert driver.provision(spec).ok

    topic_name = next(iter(mgmt.topics.values))
    mgmt.topics.values[topic_name].tags = {"owner": "someone-else"}

    assert not driver.provision(spec).ok
