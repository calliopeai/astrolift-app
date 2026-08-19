"""Stamp the Astrolift identity envelope onto an existing Azure resource (#1365).

This is the cloud half of authorized adoption; the decision half is
``_sdk.azure_adoption``. It is deliberately **not** a driver: no
``ManagedServiceDriver`` method reaches it, and adding a call from one would
re-create the implicit adoption the ownership verifier exists to refuse.

**Why one executor instead of an ``adopt()`` on twenty-five drivers.** The
envelope is ARM tags for all but two Azure managed-resource drivers, and ARM
exposes a generic tag-merge at any resource scope. A driver adds nothing to
that write except its own copy of it, and twenty-five copies of a
safety-critical write is exactly the drift #1443 spent a PR removing. The two
exceptions are storage sub-resources whose envelope lives in *metadata* rather
than tags (the legacy blob-container driver and the classic file-share driver),
and they are handled here by the same code, keyed off the resource id, because
the decision is identical and only the write call differs.

**The envelope comes from the provision path.** ARM tags are built by
``azure.managed.tags.arm_tags_for`` -- the same function every driver's
provision calls -- so an adopted resource is tagged exactly as a
platform-created one and behaves normally afterwards. There is no second tag
vocabulary to keep in step. The metadata surfaces get the equivalent envelope
under their own key spelling, taken from the ``AzureOwnershipKeys`` table the
verifier reads back through.

**Adoption leaves its own mark.** ``astrolift-adopted`` / ``-adopted-at`` say
the platform took the resource over rather than created it, which teardown
guards read to demand a louder confirmation before deleting something an
operator built. The *actor* is not stamped: identifying a person inside a
customer's cloud account is a disclosure the audit record makes unnecessary.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from _sdk.azure_adoption import (
    AzureAdoptionPlan,
    AzureAdoptionRefused,
    plan_azure_adoption,
)
from _sdk.azure_ownership import (
    ARM_TAG_KEYS,
    BLOB_METADATA_KEYS,
    METADATA_KEYS,
    PLATFORM_MANAGED_BY,
    AzureOwner,
    AzureOwnershipKeys,
)
from azure.managed.tags import arm_tags_for

if TYPE_CHECKING:
    from collections.abc import Mapping

    from _sdk.managed_service import ProvisionSpec


class AzureAdoptionSurface(StrEnum):
    """Where a resource kind stores its ownership envelope."""

    ARM_TAGS = "arm_tags"
    BLOB_CONTAINER_METADATA = "blob_container_metadata"
    FILE_SHARE_METADATA = "file_share_metadata"


SURFACE_KEYS: dict[AzureAdoptionSurface, AzureOwnershipKeys] = {
    AzureAdoptionSurface.ARM_TAGS: ARM_TAG_KEYS,
    AzureAdoptionSurface.BLOB_CONTAINER_METADATA: BLOB_METADATA_KEYS,
    AzureAdoptionSurface.FILE_SHARE_METADATA: METADATA_KEYS,
}

_RESOURCE_ID_RE = re.compile(
    r"^/subscriptions/(?P<subscription>[^/]+)"
    r"/resourceGroups/(?P<resource_group>[^/]+)"
    r"/providers/(?P<namespace>[^/]+)/(?P<type>[^/]+)/(?P<name>[^/]+)"
    r"(?P<child>(?:/[^/]+)*)$",
    re.IGNORECASE,
)

_BLOB_CONTAINER_CHILD = re.compile(r"^/blobServices/default/containers/(?P<name>[^/]+)$", re.IGNORECASE)
_FILE_SHARE_CHILD = re.compile(r"^/fileServices/default/shares/(?P<name>[^/]+)$", re.IGNORECASE)


class AzureAdoptionError(RuntimeError):
    """The adoption could not be carried out against the cloud."""


@dataclass(frozen=True)
class AzureResourceRef:
    """A parsed ARM resource id plus the surface its envelope lives on."""

    resource_id: str
    subscription_id: str
    resource_group: str
    resource_type: str
    name: str
    surface: AzureAdoptionSurface
    #: Storage account name, for the two metadata surfaces; empty otherwise.
    account_name: str = ""

    @property
    def keys(self) -> AzureOwnershipKeys:
        return SURFACE_KEYS[self.surface]


def parse_resource_id(resource_id: str) -> AzureResourceRef:
    """Parse an ARM resource id, refusing anything this module cannot stamp.

    Refusing loudly matters more than tolerating: an unparsed id silently
    routed to the ARM tag surface would report a successful adoption of a blob
    container whose *metadata* -- the thing its driver reads -- never changed,
    and the operator would find teardown still refusing with no explanation.
    """
    match = _RESOURCE_ID_RE.match(resource_id.strip())
    if match is None:
        raise AzureAdoptionRefused(
            f"{resource_id!r} is not an Azure resource id; adoption needs the full "
            f"/subscriptions/<id>/resourceGroups/<rg>/providers/<ns>/<type>/<name> form",
        )
    child = match.group("child") or ""
    resource_type = f"{match.group('namespace')}/{match.group('type')}"
    common = {
        "resource_id": resource_id.strip(),
        "subscription_id": match.group("subscription"),
        "resource_group": match.group("resource_group"),
    }

    if not child:
        return AzureResourceRef(
            **common,
            resource_type=resource_type,
            name=match.group("name"),
            surface=AzureAdoptionSurface.ARM_TAGS,
        )

    account = match.group("name")
    container = _BLOB_CONTAINER_CHILD.match(child)
    if container is not None:
        return AzureResourceRef(
            **common,
            resource_type=f"{resource_type}/blobServices/containers",
            name=container.group("name"),
            surface=AzureAdoptionSurface.BLOB_CONTAINER_METADATA,
            account_name=account,
        )
    share = _FILE_SHARE_CHILD.match(child)
    if share is not None:
        return AzureResourceRef(
            **common,
            resource_type=f"{resource_type}/fileServices/shares",
            name=share.group("name"),
            surface=AzureAdoptionSurface.FILE_SHARE_METADATA,
            account_name=account,
        )

    raise AzureAdoptionRefused(
        f"cannot adopt {resource_id!r}: only top-level ARM resources, blob containers and "
        f"file shares carry an Astrolift ownership envelope",
    )


@dataclass(frozen=True)
class AzureAdoptionConfig:
    """Subscription plus optional injected clients.

    Same shape as every Azure driver's config: real clients are constructed
    lazily so importing this module never needs the Azure SDK, and tests inject
    fakes rather than patching at import time.
    """

    subscription_id: str
    resource_client: Any | None = None
    storage_client: Any | None = None


@dataclass(frozen=True)
class AzureAdoptionOutcome:
    """What happened, in the shape the audit record persists."""

    resource_id: str
    surface: AzureAdoptionSurface
    plan: AzureAdoptionPlan
    stamped: dict[str, str] = field(default_factory=dict)

    @property
    def classification(self) -> str:
        return str(self.plan.classification)


class AzureResourceAdopter:
    """Reads a resource's markers and merges the identity envelope onto it."""

    def __init__(self, *, config: AzureAdoptionConfig) -> None:
        self._config = config
        self._resources = config.resource_client
        self._storage = config.storage_client

    # ---- clients ----------------------------------------------------

    def _resource_client(self) -> Any:
        if self._resources is None:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.resource import ResourceManagementClient

            self._resources = ResourceManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=self._config.subscription_id,
            )
        return self._resources

    def _storage_client(self) -> Any:
        if self._storage is None:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.storage import StorageManagementClient

            self._storage = StorageManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=self._config.subscription_id,
            )
        return self._storage

    # ---- reads ------------------------------------------------------

    def read_markers(self, ref: AzureResourceRef) -> dict[str, str]:
        """The Astrolift-relevant markers the live resource carries right now.

        The whole map is returned, not a filtered one: the caller records what
        was there, and ``read_prior_ownership`` decides what counts.
        """
        try:
            if ref.surface is AzureAdoptionSurface.ARM_TAGS:
                body = self._resource_client().tags.get_at_scope(scope=ref.resource_id)
                properties = _attr(body, "properties")
                return _string_map(_attr(properties, "tags"))
            if ref.surface is AzureAdoptionSurface.BLOB_CONTAINER_METADATA:
                container = self._storage_client().blob_containers.get(
                    ref.resource_group,
                    ref.account_name,
                    ref.name,
                )
                return _string_map(_attr(container, "metadata"))
            share = self._storage_client().file_shares.get(
                ref.resource_group,
                ref.account_name,
                ref.name,
            )
            return _string_map(_attr(share, "metadata"))
        except Exception as exc:  # noqa: BLE001 -- surfaced verbatim to the operator
            raise AzureAdoptionError(f"reading ownership markers on {ref.resource_id}: {exc}") from exc

    # ---- writes -----------------------------------------------------

    def stamp(self, ref: AzureResourceRef, envelope: Mapping[str, str], *, existing: Mapping[str, str]) -> None:
        """Merge ``envelope`` onto the resource, leaving unrelated keys alone.

        Merge rather than replace on every surface. An operator's own cost
        centre or owner tags are not ours to drop, and the metadata surfaces
        have no merge verb of their own -- ``blob_containers.update`` replaces
        the whole map -- so the merge is done here from the markers already
        read.
        """
        try:
            if ref.surface is AzureAdoptionSurface.ARM_TAGS:
                self._resource_client().tags.begin_update_at_scope(
                    scope=ref.resource_id,
                    parameters={"operation": "Merge", "properties": {"tags": dict(envelope)}},
                ).result()
                return
            merged = {**dict(existing), **dict(envelope)}
            if ref.surface is AzureAdoptionSurface.BLOB_CONTAINER_METADATA:
                self._storage_client().blob_containers.update(
                    ref.resource_group,
                    ref.account_name,
                    ref.name,
                    blob_container={"metadata": merged},
                )
                return
            self._storage_client().file_shares.update(
                ref.resource_group,
                ref.account_name,
                ref.name,
                file_share={"metadata": merged},
            )
        except Exception as exc:  # noqa: BLE001 -- surfaced verbatim to the operator
            raise AzureAdoptionError(f"stamping the identity envelope on {ref.resource_id}: {exc}") from exc


def adopt_azure_resource(
    *,
    adopter: AzureResourceAdopter,
    resource_id: str,
    spec: ProvisionSpec,
    acknowledged_prior_owner: str = "",
    now: dt.datetime | None = None,
) -> AzureAdoptionOutcome:
    """Bring one existing Azure resource under ``spec``'s managed service.

    ``spec`` is the same ``ProvisionSpec`` the provision path would build for
    this managed service, so the envelope written here is byte-for-byte the one
    a fresh provision would have written, plus the adoption markers.
    """
    ref = parse_resource_id(resource_id)
    markers = adopter.read_markers(ref)
    plan = plan_azure_adoption(
        markers,
        AzureOwner(managed_service_id=spec.managed_service_id, binding_id=spec.binding_id),
        resource=ref.resource_id,
        keys=ref.keys,
        acknowledged_prior_owner=acknowledged_prior_owner,
    )

    stamped_at = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0).isoformat()
    envelope = _envelope_for(ref, spec, stamped_at)
    adopter.stamp(ref, envelope, existing=markers)
    return AzureAdoptionOutcome(
        resource_id=ref.resource_id,
        surface=ref.surface,
        plan=plan,
        stamped=envelope,
    )


def _envelope_for(ref: AzureResourceRef, spec: ProvisionSpec, stamped_at: str) -> dict[str, str]:
    if ref.surface is AzureAdoptionSurface.ARM_TAGS:
        return {
            **arm_tags_for(spec),
            ref.keys.adopted: "true",
            ref.keys.adopted_at: stamped_at,
        }
    keys = ref.keys
    envelope = {
        keys.managed_by: PLATFORM_MANAGED_BY,
        keys.managed_service_id: spec.managed_service_id,
        keys.adopted: "true",
        keys.adopted_at: stamped_at,
    }
    if spec.binding_id:
        envelope[keys.binding] = spec.binding_id
    return envelope


def _attr(obj: object, name: str) -> Any:
    """One attribute off either a generated Azure model or a plain body."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _string_map(value: object) -> dict[str, str]:
    if not value:
        return {}
    return {str(key): str(item) for key, item in dict(value).items()}  # type: ignore[call-overload]
