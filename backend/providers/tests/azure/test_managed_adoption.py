"""Authorized adoption: what it takes over, what it refuses, what it writes (#1365).

Adoption is the one operation allowed to write an identity envelope onto a
resource the platform cannot already prove it owns, so the interesting
assertions are all about restraint:

* it never runs off a driver lifecycle path -- that is asserted structurally at
  the bottom of this module, because a single call added later re-opens the
  hole #1443 and #1446 closed;
* it distinguishes an unmarked resource from a platform-created-but-unstamped
  one from one belonging to another managed service, and refuses the last
  unless the caller names the owner it is displacing;
* it writes exactly the envelope a provision would have written, so the
  resource is ordinary afterwards -- which is checked against ``arm_tags_for``
  rather than against a hand-written expectation, since a second tag
  vocabulary is the failure this shares a root with (#1431).
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from _sdk.azure_adoption import (
    AzureAdoptionClassification,
    AzureAdoptionRefused,
    plan_azure_adoption,
    read_prior_ownership,
)
from _sdk.azure_ownership import (
    ARM_TAG_KEYS,
    BLOB_METADATA_KEYS,
    METADATA_KEYS,
    AzureOperation,
    AzureOwner,
    AzureOwnershipError,
    verify_azure_ownership,
)
from _sdk.managed_service import ProvisionSpec
from azure.managed import adoption as adoption_module
from azure.managed.adoption import (
    AzureAdoptionConfig,
    AzureAdoptionError,
    AzureAdoptionSurface,
    AzureResourceAdopter,
    adopt_azure_resource,
    parse_resource_id,
)
from azure.managed.tags import arm_tags_for

SUBSCRIPTION = "00000000-1111-2222-3333-444444444444"
SERVER_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-platform"
    "/providers/Microsoft.DBforPostgreSQL/flexibleServers/astrolift-shop-prod"
)
CONTAINER_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-platform"
    "/providers/Microsoft.Storage/storageAccounts/astroliftshop"
    "/blobServices/default/containers/uploads"
)
SHARE_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-platform"
    "/providers/Microsoft.Storage/storageAccounts/astroliftshop"
    "/fileServices/default/shares/media"
)

OURS = "managed-service-guid-1"
THEIRS = "managed-service-guid-2"


def _spec(**overrides: Any) -> ProvisionSpec:
    base = {
        "organization_id": "org-guid",
        "organization_slug": "acme",
        "app_id": "app-guid",
        "app_slug": "shop",
        "environment_id": "env-guid",
        "environment_name": "production",
        "tenant_cluster_id": "cluster-guid",
        "service_handle_hint": "primary",
        "size": "small",
        "managed_service_id": OURS,
    }
    base.update(overrides)
    return ProvisionSpec(**base)  # type: ignore[arg-type]


# ---- fakes ----------------------------------------------------------


class FakeTags:
    def __init__(self, store: dict[str, dict[str, str]]) -> None:
        self._store = store
        self.merges: list[tuple[str, dict[str, str]]] = []

    def get_at_scope(self, *, scope: str) -> Any:
        if scope not in self._store:
            raise KeyError(f"no such scope {scope}")
        return {"properties": {"tags": dict(self._store[scope])}}

    def begin_update_at_scope(self, *, scope: str, parameters: dict[str, Any]) -> Any:
        assert parameters["operation"] == "Merge", "adoption must never replace an operator's own tags"
        merged = dict(parameters["properties"]["tags"])
        self.merges.append((scope, merged))
        self._store[scope] = {**self._store[scope], **merged}
        return _Poller()


class _Poller:
    def result(self) -> None:
        return None


class FakeResourceClient:
    def __init__(self, store: dict[str, dict[str, str]]) -> None:
        self.tags = FakeTags(store)


class _MetadataCollection:
    def __init__(self, store: dict[str, dict[str, str]]) -> None:
        self._store = store
        self.updates: list[dict[str, Any]] = []

    def get(self, resource_group: str, account: str, name: str) -> Any:
        return {"metadata": dict(self._store[f"{resource_group}/{account}/{name}"])}

    def _update(self, resource_group: str, account: str, name: str, metadata: dict[str, str]) -> None:
        key = f"{resource_group}/{account}/{name}"
        self.updates.append({"key": key, "metadata": metadata})
        self._store[key] = dict(metadata)


class FakeBlobContainers(_MetadataCollection):
    def update(self, resource_group: str, account: str, name: str, blob_container: dict[str, Any]) -> None:
        self._update(resource_group, account, name, dict(blob_container["metadata"]))


class FakeFileShares(_MetadataCollection):
    def update(self, resource_group: str, account: str, name: str, file_share: dict[str, Any]) -> None:
        self._update(resource_group, account, name, dict(file_share["metadata"]))


class FakeStorageClient:
    def __init__(self, containers: dict[str, dict[str, str]], shares: dict[str, dict[str, str]]) -> None:
        self.blob_containers = FakeBlobContainers(containers)
        self.file_shares = FakeFileShares(shares)


def _adopter(
    *,
    arm: dict[str, dict[str, str]] | None = None,
    containers: dict[str, dict[str, str]] | None = None,
    shares: dict[str, dict[str, str]] | None = None,
) -> tuple[AzureResourceAdopter, FakeResourceClient, FakeStorageClient]:
    resources = FakeResourceClient(arm if arm is not None else {})
    storage = FakeStorageClient(containers or {}, shares or {})
    adopter = AzureResourceAdopter(
        config=AzureAdoptionConfig(
            subscription_id=SUBSCRIPTION,
            resource_client=resources,
            storage_client=storage,
        ),
    )
    return adopter, resources, storage


# ---- classification -------------------------------------------------


def test_unmarked_resource_classifies_as_unmanaged() -> None:
    plan = plan_azure_adoption(
        {"owner": "customer", "cost-centre": "42"},
        AzureOwner(managed_service_id=OURS),
        resource=SERVER_ID,
    )
    assert plan.classification is AzureAdoptionClassification.UNMANAGED
    assert plan.prior.markers == {}
    assert not plan.displaces_owner


def test_platform_created_but_unstamped_is_its_own_case() -> None:
    """The #1443/#1446 blast radius, and the reason adoption is a migration path.

    A resource the platform made before its driver stamped an identity is not
    an operator's resource, and approving its adoption is a different act from
    approving the takeover of something Astrolift never touched. Recording both
    as "no id present" would collapse the distinction the issue asks for.
    """
    plan = plan_azure_adoption(
        {"astrolift-managed-by": "platform", "astrolift-org": "acme", "astrolift-app": "shop"},
        AzureOwner(managed_service_id=OURS),
        resource=SERVER_ID,
    )
    assert plan.classification is AzureAdoptionClassification.UNSTAMPED
    assert plan.prior.managed_by == "platform"
    assert plan.prior.managed_service_id == ""
    # The whole prior envelope is captured, not just the fields the verifier
    # reads -- "it was tagged for a different app" is only visible this way.
    assert plan.prior.markers["astrolift-app"] == "shop"


def test_foreign_owner_refuses_without_naming_the_owner() -> None:
    with pytest.raises(AzureAdoptionRefused) as refused:
        plan_azure_adoption(
            {"astrolift-managed-by": "platform", "astrolift-managed-service-id": THEIRS},
            AzureOwner(managed_service_id=OURS),
            resource=SERVER_ID,
        )
    assert THEIRS in str(refused.value)


def test_foreign_owner_refuses_a_wrong_acknowledgement() -> None:
    """A near-miss is a refusal, not a warning.

    Accepting "yes I acknowledge" in any form the caller likes would make the
    acknowledgement a checkbox. It has to be the identity being displaced,
    which the caller can only supply by having read the resource.
    """
    with pytest.raises(AzureAdoptionRefused):
        plan_azure_adoption(
            {"astrolift-managed-by": "platform", "astrolift-managed-service-id": THEIRS},
            AzureOwner(managed_service_id=OURS),
            resource=SERVER_ID,
            acknowledged_prior_owner="true",
        )


def test_foreign_owner_proceeds_when_the_owner_is_named() -> None:
    plan = plan_azure_adoption(
        {"astrolift-managed-by": "platform", "astrolift-managed-service-id": THEIRS},
        AzureOwner(managed_service_id=OURS),
        resource=SERVER_ID,
        acknowledged_prior_owner=THEIRS,
    )
    assert plan.classification is AzureAdoptionClassification.FOREIGN_OWNER
    assert plan.prior.managed_service_id == THEIRS


def test_conflicting_binding_under_a_matching_service_still_displaces() -> None:
    """Two bindings of one managed service are two resources.

    A same-id match with a different binding tag reads as "already ours" if the
    check stops at the id, which is the collision case restated one level down.
    """
    with pytest.raises(AzureAdoptionRefused):
        plan_azure_adoption(
            {
                "astrolift-managed-by": "platform",
                "astrolift-managed-service-id": OURS,
                "astrolift-binding": "binding-b",
            },
            AzureOwner(managed_service_id=OURS, binding_id="binding-a"),
            resource=SERVER_ID,
        )


def test_already_owned_is_a_noop_restamp() -> None:
    plan = plan_azure_adoption(
        {"astrolift-managed-by": "platform", "astrolift-managed-service-id": OURS},
        AzureOwner(managed_service_id=OURS),
        resource=SERVER_ID,
    )
    assert plan.classification is AzureAdoptionClassification.ALREADY_OWNED
    assert plan.is_noop


def test_adoption_without_a_caller_identity_is_refused() -> None:
    """Stamping an empty id would leave the resource exactly as unowned."""
    with pytest.raises(AzureAdoptionRefused):
        plan_azure_adoption({}, AzureOwner(), resource=SERVER_ID)


def test_prior_ownership_reads_every_surface_spelling() -> None:
    blob = read_prior_ownership(
        {"astrolift_io_managed_by": "platform", "astrolift_io_managed_service_id": THEIRS},
        BLOB_METADATA_KEYS,
    )
    assert blob.managed_service_id == THEIRS
    share = read_prior_ownership(
        {"astrolift_managed_by": "platform", "astrolift_managed_service_id": THEIRS},
        METADATA_KEYS,
    )
    assert share.managed_service_id == THEIRS


# ---- resource id parsing --------------------------------------------


@pytest.mark.parametrize(
    ("resource_id", "surface"),
    [
        (SERVER_ID, AzureAdoptionSurface.ARM_TAGS),
        (CONTAINER_ID, AzureAdoptionSurface.BLOB_CONTAINER_METADATA),
        (SHARE_ID, AzureAdoptionSurface.FILE_SHARE_METADATA),
    ],
)
def test_surface_follows_the_resource_id(resource_id: str, surface: AzureAdoptionSurface) -> None:
    assert parse_resource_id(resource_id).surface is surface


@pytest.mark.parametrize(
    "resource_id",
    [
        "astrolift-shop-prod",
        "/subscriptions/x/resourceGroups/rg",
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg/providers/Microsoft.Sql/servers/s/databases/d",
    ],
)
def test_unstampable_resource_ids_are_refused_not_guessed(resource_id: str) -> None:
    """Defaulting an unrecognised id to ARM tags would report a false success.

    A blob container adopted through the tag surface gets tags nothing reads;
    its driver checks *metadata*, so teardown keeps refusing and the operator
    has a green adoption record explaining nothing.
    """
    with pytest.raises(AzureAdoptionRefused):
        parse_resource_id(resource_id)


# ---- the write ------------------------------------------------------


def test_adoption_writes_the_same_envelope_a_provision_would() -> None:
    arm = {SERVER_ID: {"owner": "customer"}}
    adopter, resources, _ = _adopter(arm=arm)
    spec = _spec()

    outcome = adopt_azure_resource(
        adopter=adopter,
        resource_id=SERVER_ID,
        spec=spec,
        now=dt.datetime(2026, 8, 18, 9, 30, tzinfo=dt.UTC),
    )

    expected = arm_tags_for(spec)
    assert {key: outcome.stamped[key] for key in expected} == expected
    assert outcome.stamped["astrolift-adopted"] == "true"
    assert outcome.stamped["astrolift-adopted-at"] == "2026-08-18T09:30:00+00:00"
    # The operator's own tag survives: the write is a merge, and one ARM call.
    assert resources.tags._store[SERVER_ID]["owner"] == "customer"
    assert len(resources.tags.merges) == 1


def test_an_adopted_resource_passes_the_ownership_verifier_afterwards() -> None:
    """The point of the whole operation, asserted end to end.

    Adoption exists so a refused resource becomes a normal one. Checking the
    tag map by eye is not that; running the real verifier over the real live
    tags is.
    """
    arm = {SERVER_ID: {}}
    adopter, resources, _ = _adopter(arm=arm)
    spec = _spec()

    live_before = dict(resources.tags._store[SERVER_ID])
    with pytest.raises(AzureOwnershipError):
        verify_azure_ownership(
            live_before,
            AzureOwner(managed_service_id=OURS),
            operation=AzureOperation.DELETE,
            resource=SERVER_ID,
        )

    adopt_azure_resource(adopter=adopter, resource_id=SERVER_ID, spec=spec)

    verify_azure_ownership(
        resources.tags._store[SERVER_ID],
        AzureOwner(managed_service_id=OURS),
        operation=AzureOperation.DELETE,
        resource=SERVER_ID,
        keys=ARM_TAG_KEYS,
    )


def test_adoption_is_idempotent() -> None:
    arm = {SERVER_ID: {}}
    adopter, resources, _ = _adopter(arm=arm)
    spec = _spec()

    adopt_azure_resource(adopter=adopter, resource_id=SERVER_ID, spec=spec)
    second = adopt_azure_resource(adopter=adopter, resource_id=SERVER_ID, spec=spec)

    assert second.plan.classification is AzureAdoptionClassification.ALREADY_OWNED
    assert resources.tags._store[SERVER_ID]["astrolift-managed-service-id"] == OURS


def test_a_refused_adoption_writes_nothing() -> None:
    """A refusal that had already mutated the resource is the bug, not the message."""
    arm = {SERVER_ID: {"astrolift-managed-by": "platform", "astrolift-managed-service-id": THEIRS}}
    adopter, resources, _ = _adopter(arm=arm)

    with pytest.raises(AzureAdoptionRefused):
        adopt_azure_resource(adopter=adopter, resource_id=SERVER_ID, spec=_spec())

    assert resources.tags.merges == []
    assert resources.tags._store[SERVER_ID]["astrolift-managed-service-id"] == THEIRS


def test_blob_container_adoption_writes_metadata_not_tags() -> None:
    containers = {"rg-platform/astroliftshop/uploads": {"custom": "keep-me"}}
    adopter, resources, storage = _adopter(containers=containers)

    outcome = adopt_azure_resource(adopter=adopter, resource_id=CONTAINER_ID, spec=_spec())

    assert outcome.surface is AzureAdoptionSurface.BLOB_CONTAINER_METADATA
    written = storage.blob_containers.updates[0]["metadata"]
    assert written["astrolift_io_managed_service_id"] == OURS
    assert written["astrolift_io_managed_by"] == "platform"
    assert written["astrolift_io_adopted"] == "true"
    # ``blob_containers.update`` replaces the whole map, so the merge has to
    # happen here or an unrelated key is silently dropped.
    assert written["custom"] == "keep-me"
    assert resources.tags.merges == []


def test_file_share_adoption_uses_the_classic_metadata_spelling() -> None:
    shares = {"rg-platform/astroliftshop/media": {}}
    adopter, _, storage = _adopter(shares=shares)

    adopt_azure_resource(adopter=adopter, resource_id=SHARE_ID, spec=_spec())

    written = storage.file_shares.updates[0]["metadata"]
    assert written["astrolift_managed_service_id"] == OURS
    assert written["astrolift_managed_by"] == "platform"


def test_a_cloud_read_failure_is_an_error_not_a_silent_unmanaged_verdict() -> None:
    """An unreadable resource must never classify as "no markers found".

    That would turn a transient ARM failure into an approved takeover of
    something that may well belong to somebody else.
    """
    adopter, _, _ = _adopter(arm={})
    with pytest.raises(AzureAdoptionError):
        adopt_azure_resource(adopter=adopter, resource_id=SERVER_ID, spec=_spec())


# ---- structural: adoption stays unreachable from a driver -----------


DRIVER_DIR = Path(adoption_module.__file__).parent
ADOPTION_SYMBOLS = {
    "adopt_azure_resource",
    "AzureResourceAdopter",
    "plan_azure_adoption",
}


@pytest.mark.parametrize(
    "path",
    sorted(path for path in DRIVER_DIR.glob("*.py") if path.name not in {"__init__.py", "tags.py", "adoption.py"}),
    ids=lambda path: path.stem,
)
def test_no_driver_can_reach_the_adoption_path(path: Path) -> None:
    """#1365's hardest requirement: adoption is never implicit.

    Every other guarantee here is a runtime one and can be re-opened by a
    single call added in a later PR -- a driver that "helpfully" adopts on a
    provision collision restores exactly the behaviour ``adopt_existing`` had.
    A reviewer cannot see that by reading one driver, so it is pinned here.
    """
    tree = ast.parse(path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and "adoption" in (node.module or ""):
            imported |= {alias.name for alias in node.names}
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names if "adoption" in alias.name}
    assert not (imported & ADOPTION_SYMBOLS), (
        f"{path.name} reaches the adoption path; adoption must stay an explicitly "
        f"authorized control-plane operation, never something a driver can trigger"
    )
