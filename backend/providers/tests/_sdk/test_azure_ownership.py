"""The Azure ownership gate must fail closed on absence, not just on conflict (#1365)."""

from __future__ import annotations

import pytest

from _sdk.azure_ownership import (
    ARM_TAG_KEYS,
    METADATA_KEYS,
    AzureOperation,
    AzureOwner,
    AzureOwnershipError,
    owner_of,
    verify_azure_ownership,
)
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec

MUTATIONS = [
    AzureOperation.PROVISION,
    AzureOperation.UPDATE,
    AzureOperation.SNAPSHOT,
    AzureOperation.RESTORE,
    AzureOperation.DELETE,
]

OURS = AzureOwner(managed_service_id="ms-1", binding_id="bind-1")


def _tags(**overrides: str) -> dict[str, str]:
    tags = {
        ARM_TAG_KEYS.managed_by: "platform",
        ARM_TAG_KEYS.managed_service_id: "ms-1",
        ARM_TAG_KEYS.binding: "bind-1",
    }
    tags.update(overrides)
    return tags


def _verify(tags: dict[str, str] | None, operation: AzureOperation, expected: AzureOwner = OURS) -> None:
    verify_azure_ownership(tags, expected, operation=operation, resource="the resource")


@pytest.mark.parametrize("operation", [*MUTATIONS, AzureOperation.INSPECT])
def test_untagged_resource_is_refused_for_every_operation(operation: AzureOperation) -> None:
    """A resource with no platform marker is somebody else's, including on reads."""

    with pytest.raises(AzureOwnershipError, match="carries no Astrolift astrolift-managed-by=platform"):
        _verify({"owner": "terraform"}, operation)

    with pytest.raises(AzureOwnershipError):
        _verify({}, operation)

    with pytest.raises(AzureOwnershipError):
        _verify(None, operation)


@pytest.mark.parametrize("operation", MUTATIONS)
def test_platform_resource_without_an_identity_marker_is_never_adopted(operation: AzureOperation) -> None:
    """The historical hole: absence read as consent.

    ``if existing_id and existing_id != mine`` passed here, so a platform
    resource whose id tag never landed was mutated by whichever row happened to
    derive the same name. Adoption is a separate authorized operation, so the
    answer is refusal.
    """

    tags = _tags()
    del tags[ARM_TAG_KEYS.managed_service_id]

    with pytest.raises(AzureOwnershipError, match="carries no astrolift-managed-service-id marker"):
        _verify(tags, operation)


def test_an_unidentified_marker_still_reads_on_the_non_destructive_path() -> None:
    """A status poll holding only a handle must not fail a healthy row."""

    tags = _tags()
    del tags[ARM_TAG_KEYS.managed_service_id]

    _verify(tags, AzureOperation.INSPECT, AzureOwner())


@pytest.mark.parametrize("operation", MUTATIONS)
def test_a_caller_that_cannot_name_the_owner_cannot_mutate(operation: AzureOperation) -> None:
    with pytest.raises(AzureOwnershipError, match="supplied no managed-service identity"):
        _verify(_tags(), operation, AzureOwner())


@pytest.mark.parametrize("operation", [*MUTATIONS, AzureOperation.INSPECT])
def test_a_conflicting_managed_service_id_is_refused(operation: AzureOperation) -> None:
    with pytest.raises(AzureOwnershipError, match="belongs to managed service ms-2, not ms-1"):
        _verify(_tags(**{ARM_TAG_KEYS.managed_service_id: "ms-2"}), operation)


@pytest.mark.parametrize("operation", [*MUTATIONS, AzureOperation.INSPECT])
def test_a_conflicting_binding_is_refused_even_when_the_service_id_agrees(operation: AzureOperation) -> None:
    """Two markers that disagree describe a resource nobody should touch."""

    with pytest.raises(AzureOwnershipError, match="belongs to binding bind-2, not bind-1"):
        _verify(_tags(**{ARM_TAG_KEYS.binding: "bind-2"}), operation)


@pytest.mark.parametrize("operation", [*MUTATIONS, AzureOperation.INSPECT])
def test_the_same_owner_reconciles_and_stays_idempotent(operation: AzureOperation) -> None:
    """A Temporal retry replays the same identity against the same tags."""

    for _ in range(3):
        _verify(_tags(), operation)


def test_a_binding_the_resource_never_recorded_does_not_block_its_own_owner() -> None:
    """``binding_id`` is unpopulated on most rows, so absence cannot mean conflict
    once the managed-service id has already pinned the identity."""

    tags = _tags()
    del tags[ARM_TAG_KEYS.binding]

    _verify(tags, AzureOperation.DELETE)


def test_the_legacy_underscore_id_spelling_still_identifies_our_own_resource() -> None:
    """File-share and blob-container metadata keys are a different vocabulary."""

    metadata = {
        METADATA_KEYS.managed_by: "platform",
        METADATA_KEYS.managed_service_id: "ms-1",
    }
    verify_azure_ownership(
        metadata,
        OURS,
        operation=AzureOperation.DELETE,
        resource="the share",
        keys=METADATA_KEYS,
    )

    with pytest.raises(AzureOwnershipError, match="belongs to managed service ms-9, not ms-1"):
        verify_azure_ownership(
            {**metadata, METADATA_KEYS.managed_service_id: "ms-9"},
            OURS,
            operation=AzureOperation.DELETE,
            resource="the share",
            keys=METADATA_KEYS,
        )


def test_arm_keys_are_not_accepted_on_the_metadata_surface() -> None:
    """Reading a spelling the write path never produced is how a driver decides
    it does not own its own resource."""

    with pytest.raises(AzureOwnershipError, match="carries no Astrolift astrolift_managed_by=platform"):
        verify_azure_ownership(
            _tags(),
            OURS,
            operation=AzureOperation.DELETE,
            resource="the share",
            keys=METADATA_KEYS,
        )


def test_every_spec_type_carries_the_identity_the_gate_reads() -> None:
    """One call site shape has to serve provision, update, teardown, and reads."""

    provision = ProvisionSpec(
        organization_id="org",
        organization_slug="org",
        app_id="app",
        app_slug="app",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="hint",
        size="small",
        binding_id="bind-1",
        managed_service_id="ms-1",
    )
    specs = [
        provision,
        UpdateSpec(handle="h", binding_id="bind-1", managed_service_id="ms-1"),
        DeprovisionSpec(handle="h", binding_id="bind-1", managed_service_id="ms-1"),
        ServiceHandle(handle="h", binding_id="bind-1", managed_service_id="ms-1"),
    ]
    assert [owner_of(spec) for spec in specs] == [OURS] * len(specs)


def test_an_operation_string_names_the_refusal() -> None:
    """The message is what an operator sees on a stuck row, so it has to say
    which operation was refused and against which resource."""

    with pytest.raises(AzureOwnershipError) as caught:
        verify_azure_ownership(
            _tags(**{ARM_TAG_KEYS.managed_service_id: "ms-2"}),
            OURS,
            operation=AzureOperation.DELETE,
            resource="Cosmos account astrolift-nosql-1",
        )
    assert "refusing to delete Cosmos account astrolift-nosql-1" in str(caught.value)
