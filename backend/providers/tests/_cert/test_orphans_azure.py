"""Tests for the Azure orphan scanner (spec 43 §3.2, §6).

Azure's surfaces disagree about where ownership lives -- ARM tags, blob
metadata, a Service Bus ``userMetadata`` blob, and role assignments with no
metadata at all -- so most of these cases are about reading the right surface
rather than about the matching rule.
"""

from __future__ import annotations

import pytest

from _cert.campaign import Campaign
from _cert.orphans import CloudResource, OrphansFound
from _cert.orphans import azure as azure_orphans

CAMPAIGN = Campaign("cert2026q3")


class FakeAzureInventory:
    def __init__(self, planted: dict[str, list[CloudResource]] | None = None, fails: dict[str, str] | None = None):
        self._planted = planted or {}
        self._fails = fails or {}

    def _family(self, name: str) -> list[CloudResource]:
        if name in self._fails:
            raise PermissionError(self._fails[name])
        return self._planted.get(name, [])

    def postgres_flexible_servers(self):
        return self._family("postgres_flexible")

    def redis_caches(self):
        return self._family("redis")

    def service_bus_namespaces(self):
        return self._family("service_bus_namespace")

    def service_bus_entities(self):
        return self._family("service_bus_entity")

    def storage_accounts(self):
        return self._family("storage_account")

    def blob_containers(self):
        return self._family("blob_container")

    def managed_identities(self):
        return self._family("managed_identity")

    def role_assignments(self):
        return self._family("role_assignment")


def test_a_clean_resource_group_scans_clean():
    report = azure_orphans.scan(FakeAzureInventory(), CAMPAIGN)

    assert report.is_clean
    report.raise_if_dirty()


def test_a_surviving_flexible_server_is_found_by_its_arm_tag():
    inventory = FakeAzureInventory(
        {
            "postgres_flexible": [
                CloudResource(
                    identifier="astrolift-conflict-cert2026q3-happy-azure-records",
                    location="westus2",
                    tags={"astrolift-app": "cert2026q3-happy-azure", "astrolift-managed-by": "platform"},
                )
            ]
        }
    )

    report = azure_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["postgres_flexible"]
    assert report.orphans[0].location == "westus2"


def test_a_blob_container_is_found_by_its_metadata_spelling():
    """Containers carry metadata, not ARM tags, and the metadata uses
    ``astrolift_io_app``. Reading only the ARM spelling misses every one."""
    inventory = FakeAzureInventory(
        {
            "blob_container": [
                CloudResource(
                    identifier="stcert2026q3/archive",
                    tags={"astrolift_io_app": "cert2026q3-happy-azure"},
                )
            ]
        }
    )

    report = azure_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["blob_container"]


def test_a_service_bus_queue_is_found_through_its_user_metadata_blob():
    """Entities have no tag surface: the driver packs ownership into
    ``userMetadata`` as ``k=v;``. Treating that as untagged would leave every
    queue and topic invisible inside a namespace that survived."""
    blob = (
        "astrolift-managed-by=platform;astrolift-managed-service-id=ms-42;"
        "astrolift-app=cert2026q3-happy-azure;astrolift-env=cert"
    )

    parsed = azure_orphans.parse_user_metadata(blob)
    inventory = FakeAzureInventory(
        {"service_bus_entity": [CloudResource(identifier="sb-cert/queues/jobs", tags=parsed)]}
    )
    report = azure_orphans.scan(inventory, CAMPAIGN)

    assert parsed["astrolift-app"] == "cert2026q3-happy-azure"
    assert [o.service for o in report.orphans] == ["service_bus_entity"]


def test_a_truncated_user_metadata_blob_still_parses_what_survived():
    """The writer truncates at 1024 characters, so the tail pair can arrive
    without its value. Dropping the broken pair keeps the ownership keys, which
    the writer deliberately puts first."""
    truncated = "astrolift-managed-by=platform;astrolift-app=cert2026q3-happy-azure;astrolift-extra-note"

    parsed = azure_orphans.parse_user_metadata(truncated)

    assert parsed == {"astrolift-managed-by": "platform", "astrolift-app": "cert2026q3-happy-azure"}


def test_a_managed_identity_is_found_by_name_because_its_tags_carry_no_app():
    """``azure/identity_federated.py`` creates the UAMI with only
    ``astrolift-managed-by``, so the name is the only campaign handle."""
    inventory = FakeAzureInventory(
        {
            "managed_identity": [
                CloudResource(
                    identifier="cert2026q3-happy-azure-wi",
                    tags={"astrolift-managed-by": "platform"},
                )
            ]
        }
    )

    report = azure_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["managed_identity"]
    assert report.orphans[0].matched_on.startswith("name ")


def test_a_role_assignment_outliving_its_identity_is_reported():
    """Deleting a managed identity leaves its assignments behind as orphaned
    principal IDs. That is the 'no dangling IAM/role/grant' clause, and on Azure
    it is the one most likely to be missed."""
    inventory = FakeAzureInventory(
        {
            "role_assignment": [
                CloudResource(
                    identifier=(
                        "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Storage/"
                        "storageAccounts/stalinstall/blobServices/default/containers/"
                        "cert2026q3-happy-azure-archive -> 00000000-dead-beef"
                    )
                )
            ]
        }
    )

    report = azure_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["role_assignment"]


def test_the_platform_marker_matches_the_string_the_identity_driver_writes():
    """The role-assignment family filters on this description before matching
    anything, so a drift between the two silently empties that family."""
    from azure.identity_federated import _ASSIGNMENT_DESCRIPTION

    assert azure_orphans.PLATFORM_ASSIGNMENT_DESCRIPTION == _ASSIGNMENT_DESCRIPTION
    assert azure_orphans.is_platform_assignment(_ASSIGNMENT_DESCRIPTION)


def test_an_operators_own_role_assignment_is_not_platform_residue():
    """The campaign slug can appear in a scope a human granted deliberately.
    Reporting that invites someone to delete it."""
    assert not azure_orphans.is_platform_assignment("granted by hand during the cert2026q3 incident")
    assert not azure_orphans.is_platform_assignment("")


def test_another_tenants_resources_are_left_alone():
    inventory = FakeAzureInventory(
        {
            "redis": [
                CloudResource(
                    identifier="astrolift-conflict-checkout-production-cache",
                    tags={"astrolift-app": "checkout"},
                )
            ]
        }
    )

    report = azure_orphans.scan(inventory, CAMPAIGN)

    assert report.is_clean


def test_a_family_that_cannot_be_read_is_not_reported_as_clean():
    inventory = FakeAzureInventory(fails={"role_assignment": "AuthorizationFailed"})

    report = azure_orphans.scan(inventory, CAMPAIGN)

    with pytest.raises(OrphansFound, match="AuthorizationFailed"):
        report.raise_if_dirty()


def test_every_family_the_spec_names_is_actually_queried():
    report = azure_orphans.scan(FakeAzureInventory(), CAMPAIGN)

    assert set(report.scanned) == {
        "postgres_flexible",
        "redis",
        "service_bus_namespace",
        "service_bus_entity",
        "storage_account",
        "blob_container",
        "managed_identity",
        "role_assignment",
    }
