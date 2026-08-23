"""Cluster-scoped managed-service catalogue.

The provider SDK availability matrix owns product metadata while the live
plugin registry owns executable capability.  This module joins both sources so
GraphQL and mutations use the same answer: every installed driver is visible,
planned/stub entries remain discoverable, and only executable variants may be
provisioned.

Cluster-scoped, not plugin-scoped: what a cluster can book is its own plugin's
drivers *plus* the in-cluster ones, because every tenant cluster is a
Kubernetes cluster and the driver resolver reaches ``k8s_native`` from any of
them (#1484).  A catalogue narrower than the resolver would refuse a variant
the lifecycle would provision without complaint.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Any

from _sdk.availability import MATRIX, ManagedServiceEntry
from _sdk.coverage import OPT_IN_TIER

from astrolift_drivers.managed_resolution import IN_CLUSTER_PLUGIN
from astrolift_drivers.registry import plugins

_SIZE_OPTIONS = ("small", "medium", "large", "xlarge", "custom")
_SIZED_KINDS = {
    "postgres",
    "mysql",
    "mssql",
    "redis",
    "cache",
    "queue",
    "kv_store",
    "document_db",
    "search",
    "vector_index",
    "time_series",
    "model_endpoint",
    "event_stream",
    "faas",
    "filesystem",
    "graph_db",
    "wide_column",
    "warehouse",
}

# Providers with more than one executable variant for the same kind need an
# explicit default.  A unique executable variant is automatically the default.
# This table is intentionally provider-neutral infrastructure metadata, not UI
# branching; future serverless drivers can become the default in one line.
_DEFAULT_VARIANTS: dict[tuple[str, str], str] = {
    ("aws", "cache"): "elasticache_serverless_memcached",
    ("aws", "mysql"): "rds_mysql",
    ("aws", "postgres"): "rds",
    ("aws", "redis"): "elasticache_serverless_valkey",
    ("azure", "mssql"): "azure_sql_database",
    ("azure", "object_store"): "azure_blob",
    ("azure", "queue"): "azure_servicebus",
    ("k8s_native", "event_stream"): "kafka_strimzi",
    ("k8s_native", "object_store"): "seaweedfs_operator",
}


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogItem:
    provider_plugin_slug: str
    kind: str
    variant: str
    display_name: str
    description: str
    status: str
    tier: str
    """``default`` or ``extended``. Extended kinds are real and provisionable;
    they are outside the set Astrolift guarantees on every cloud, so they are
    opt-in rather than listed by default (#1470)."""

    available: bool
    unavailable_reason: str
    is_default_for_kind: bool
    size_options: tuple[str, ...]
    config_schema: dict[str, Any]
    binding_envs: tuple[str, ...]
    issue_url: str

    @property
    def id(self) -> str:
        return f"{self.provider_plugin_slug}:{self.kind}:{self.variant}"


class CatalogResolutionError(ValueError):
    def __init__(self, message: str, *, field: str = "variant") -> None:
        super().__init__(message)
        self.field = field


def _matrix_entries(plugin_slug: str) -> dict[tuple[str, str], ManagedServiceEntry]:
    return {
        (entry.kind, entry.variant): entry
        for entry in MATRIX.managed_services
        if entry.plugin_id == plugin_slug
    }


def _driver_roles(plugin_slug: str) -> dict[tuple[str, str], type | Any]:
    manifest = next((row for row in plugins.list() if row.plugin_id == plugin_slug), None)
    if manifest is None:
        return {}
    out: dict[tuple[str, str], type | Any] = {}
    for role, driver_cls in manifest.drivers.items():
        if not role.startswith("managed:"):
            continue
        _prefix, kind, variant = role.split(":", 2)
        out[(kind, variant)] = driver_cls
    return out


def _bookable_on(
    plugin_slug: str,
) -> tuple[
    dict[tuple[str, str], ManagedServiceEntry],
    dict[tuple[str, str], type | Any],
    set[tuple[str, str]],
]:
    """Metadata, drivers, and which of those a cluster on ``plugin_slug``
    borrows from ``k8s_native``.

    The catalogue is the offer surface in front of the runtime, so it has to
    offer what the runtime resolves. Since #1484 a cloud-hosted cluster reaches
    the in-cluster drivers, and a catalogue that did not say so would refuse a
    ``createManagedService`` for a variant the activity would have provisioned
    happily -- the gate disagreeing with the thing it gates is the shape of bug
    #1484 already was.

    Same order and same direction as
    ``astrolift_drivers.managed_resolution``: the cluster's own plugin owns any
    ``(kind, variant)`` it registers, and ``k8s_native`` borrows nothing back.
    """
    metadata = _matrix_entries(plugin_slug)
    drivers = _driver_roles(plugin_slug)
    # k8s_native borrows nothing back, and a plugin this control plane knows
    # nothing about -- a separately distributed third-party one -- keeps its
    # empty catalogue: ``resolve_variant`` reads that as "not on the contract
    # yet" and waves the request through, so filling it with in-cluster rows
    # would start refusing variants the plugin does implement.
    if plugin_slug == IN_CLUSTER_PLUGIN or not (metadata or drivers):
        return metadata, drivers, set()

    in_cluster_metadata = _matrix_entries(IN_CLUSTER_PLUGIN)
    borrowed = set()
    for key, driver_cls in _driver_roles(IN_CLUSTER_PLUGIN).items():
        if key in drivers:
            continue
        drivers[key] = driver_cls
        borrowed.add(key)
        if key not in metadata and key in in_cluster_metadata:
            metadata[key] = in_cluster_metadata[key]
    return metadata, drivers, borrowed


def _pure_contract_method(driver_cls: type | Any, method_name: str, default: Any) -> Any:
    """Read a driver contract method without constructing cloud SDK clients.

    ``config_schema`` and ``binding_schema`` are required pure protocol methods,
    but currently declared as instance methods.  ``@driver_op`` preserves the
    original function in ``__wrapped__``; calling it against an uninitialised
    instance avoids network/client construction while retaining one source of
    schema truth.  A third-party driver that violates purity degrades to the
    supplied default and is still listed.
    """

    try:
        method = getattr(driver_cls, method_name)
        raw = getattr(method, "__wrapped__", method)
        return raw(object.__new__(driver_cls))
    except Exception:  # noqa: BLE001 - catalogue discovery must stay readable
        return default


def _config_schema(driver_cls: type | Any | None, *, kind: str) -> dict[str, Any]:
    raw = _pure_contract_method(driver_cls, "config_schema", {}) if driver_cls else {}
    schema = dict(raw) if isinstance(raw, dict) else {}
    schema.setdefault("type", "object")
    properties = dict(schema.get("properties") or {})
    if kind in _SIZED_KINDS:
        properties.setdefault(
            "size",
            {
                "type": "string",
                "enum": list(_SIZE_OPTIONS),
                "default": "small",
                "description": "Portable Astrolift size preset; custom uses provider fields below.",
            },
        )
    schema["properties"] = properties
    return schema


def _binding_envs(driver_cls: type | Any | None, matrix: ManagedServiceEntry | None) -> tuple[str, ...]:
    keys = set(matrix.binding_envs if matrix else ())
    if driver_cls is not None:
        binding = _pure_contract_method(driver_cls, "binding_schema", None)
        keys.update((getattr(binding, "env_vars", {}) or {}).keys())
    return tuple(sorted(keys))


#: The tier a kind belongs to. ``OPT_IN_TIER`` is the canonical set and lives
#: in ``_sdk.coverage`` because the declared-gap guard already reads it: a kind
#: outside the guaranteed surface is not a portability defect there, and is not
#: a default catalogue row here. Imported rather than restated, because #1470
#: asked for one mechanism and a second frozenset would drift the moment either
#: side gained a kind. It carries nine, not the seven #1470 listed:
#: ``api_gateway`` and ``time_series`` were already cut for overlapping
#: capabilities the platform answers another way.
#:
#: ``cdn`` is the one to be careful with. The capability is not lost: it ships
#: with the ``static_site`` topology through
#: ``providers/aws/managed/cdn_cloudfront.py`` (#1010), so cutting the kind
#: removes a requestable resource and leaves the topology untouched.

TIER_DEFAULT = "default"
TIER_EXTENDED = "extended"


def kind_tier(kind: str) -> str:
    """Which tier a kind belongs to. One place, so nothing drifts."""
    return TIER_EXTENDED if kind in OPT_IN_TIER else TIER_DEFAULT


def list_catalog(
    plugin_slug: str,
    *,
    include_unprovisionable: bool = False,
    include_extended: bool = False,
) -> tuple[CatalogItem, ...]:
    """The managed-service catalogue for one provider plugin.

    By default ``planned`` rows are left out. They are roadmap metadata: no
    driver exists and none is being installed, so the entry documents an
    intention rather than a choice, and "planned" reads to most people as
    "available soon".

    Extended-tier kinds are left out too, and ``include_extended`` is a
    *separate* flag from ``include_unprovisionable`` on purpose. They are
    different axes and conflating them would be wrong in both directions: an
    extended kind is fully provisionable, and asking to see one should not also
    surface planned stubs that cannot be provisioned at all. One filter, two
    questions, which is what #1470 asked for when it said not to add a second
    mechanism.

    Rows whose driver is missing are deliberately still shown, as unavailable
    with the reason. Hiding those would mean a control plane whose plugin
    registry failed to load, through a bad entry point, an import error in a
    plugin, or a packaging regression, renders an *empty* catalogue rather than
    one full of "driver is not installed". A silently empty list gives the
    operator nothing to search for; the unavailable row names the fault.

    ``deprecated`` rows stay too. An operator may already be running one and
    needs to see it in the catalogue that describes their estate.

    ``include_unprovisionable`` returns everything, for callers that need to
    tell "not offered here" apart from "no such variant". ``resolve_variant``
    uses it so a request for a planned variant is refused with the reason
    rather than with "unknown variant", which reads like a typo.
    """
    metadata, drivers, borrowed = _bookable_on(plugin_slug)
    keys = sorted(set(metadata) | set(drivers))
    available_by_kind: dict[str, list[str]] = defaultdict(list)
    # The pool a lone variant becomes the default from. An in-cluster variant
    # borrowed onto a cloud that already ships its own must not turn a kind
    # that had one obvious answer into one that demands an explicit variant --
    # that would break every existing caller of resolve_variant that omits it.
    # A borrowed variant becomes the default only where the cloud offers none.
    native_by_kind: dict[str, list[str]] = defaultdict(list)
    for kind, variant in keys:
        meta = metadata.get((kind, variant))
        if (kind, variant) in drivers and (meta is None or meta.status in {"ga", "preview"}):
            available_by_kind[kind].append(variant)
            if (kind, variant) not in borrowed:
                native_by_kind[kind].append(variant)

    rows: list[CatalogItem] = []
    for kind, variant in keys:
        meta = metadata.get((kind, variant))
        driver_cls = drivers.get((kind, variant))
        status = meta.status if meta is not None else "experimental"
        # ``experimental`` means implemented but unsupported.  It stays
        # discoverable so existing estates and the reason for the gate remain
        # visible, but it is not provisionable without a future explicit
        # opt-in contract (#1417 decision 4).
        available = driver_cls is not None and (meta is None or status in {"ga", "preview"})
        # Order matters. ``planned`` is checked before the missing-driver case
        # because a planned row has no driver *by definition*, so testing for
        # the driver first claimed the install was incomplete on eight of the
        # nine planned rows in the matrix and left the planned message reachable
        # only by the one planned variant that happens to ship a stub. "Not
        # installed in this control plane" sends an operator to check their
        # plugin install for a variant nobody has written (#1470).
        if status == "deprecated":
            unavailable_reason = "This provider variant is deprecated or retired and cannot be provisioned."
        elif meta is not None and status == "experimental":
            unavailable_reason = (
                "This provider variant is experimental and unsupported; new provisioning is disabled."
            )
        elif status == "planned":
            if kind_tier(kind) == TIER_EXTENDED:
                # Roadmap metadata for a kind that has since been cut. Saying
                # "planned" here reads as "available soon" for something nobody
                # intends to build, which is the specific thing #1470 asked to
                # stop. The row stays for the record.
                unavailable_reason = (
                    f"{kind} sits outside the tier Astrolift guarantees across clouds, "
                    "and this variant is not planned. The row is kept for the record."
                )
            else:
                unavailable_reason = "Driver is a non-provisioning stub or planned capability."
        elif driver_cls is None:
            unavailable_reason = "Provider driver is not installed in this control plane."
        else:
            unavailable_reason = ""
        default_pool = native_by_kind.get(kind) or available_by_kind.get(kind, [])
        configured_default = _DEFAULT_VARIANTS.get((plugin_slug, kind))
        is_default = available and (
            variant == configured_default or (configured_default is None and default_pool == [variant])
        )
        description = meta.description if meta is not None else "Installed third-party provider driver."
        if not include_unprovisionable and status == "planned":
            continue
        tier = kind_tier(kind)
        if not include_extended and tier == TIER_EXTENDED:
            continue
        rows.append(
            CatalogItem(
                provider_plugin_slug=plugin_slug,
                kind=kind,
                variant=variant,
                display_name=description or f"{kind} / {variant}",
                description=description,
                status=status,
                tier=tier,
                available=available,
                unavailable_reason=unavailable_reason,
                is_default_for_kind=is_default,
                size_options=_SIZE_OPTIONS if kind in _SIZED_KINDS else (),
                config_schema=_config_schema(driver_cls, kind=kind),
                binding_envs=_binding_envs(driver_cls, meta),
                issue_url=meta.issue_url if meta is not None else "",
            )
        )
    return tuple(rows)


def resolve_variant(*, plugin_slug: str, kind: str, requested_variant: str | None) -> CatalogItem | None:
    """Resolve and validate a provision request against executable capability.

    Returns ``None`` for a provider absent from both the availability matrix and
    live registry.  That preserves compatibility for separately-distributed
    third-party plugins until they adopt the catalogue contract; known providers
    fail closed.
    """

    # The full list, including planned and driverless variants: an explicit
    # request for one should be refused with the reason it is unavailable
    # rather than with "does not support", which reads as a typo.
    #
    # ``include_extended`` for the same reason, and it is load-bearing. Cutting
    # a kind from the default tier is a statement about what the browse list
    # promises, not a removal: the driver still works and an author who names
    # ``warehouse`` outright is entitled to get it. Leaving it out here would
    # turn an opt-in kind into an unsupported one and break every manifest
    # already using it (#1470).
    rows = [
        row
        for row in list_catalog(plugin_slug, include_unprovisionable=True, include_extended=True)
        if row.kind == kind
    ]
    if not rows:
        known_plugin = any(entry.plugin_id == plugin_slug for entry in MATRIX.managed_services) or any(
            manifest.plugin_id == plugin_slug for manifest in plugins.list()
        )
        if known_plugin:
            raise CatalogResolutionError(
                f"provider {plugin_slug!r} does not support kind {kind!r}",
                field="kind",
            )
        return None
    if requested_variant:
        selected = next((row for row in rows if row.variant == requested_variant), None)
        if selected is None:
            raise CatalogResolutionError(
                f"provider {plugin_slug!r} does not support {kind!r} variant {requested_variant!r}"
            )
        if not selected.available:
            raise CatalogResolutionError(
                f"{kind!r} variant {requested_variant!r} is unavailable: {selected.unavailable_reason}"
            )
        return selected
    defaults = [row for row in rows if row.is_default_for_kind]
    if len(defaults) == 1:
        return defaults[0]
    available = [row.variant for row in rows if row.available]
    raise CatalogResolutionError(
        f"provider {plugin_slug!r} requires an explicit variant for kind {kind!r}; choose one of {available}"
    )


def validate_config(item: CatalogItem, config: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator

    errors = sorted(
        Draft202012Validator(item.config_schema).iter_errors(config), key=lambda row: list(row.path)
    )
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.path)
        where = f" at {path}" if path else ""
        raise CatalogResolutionError(
            f"invalid config{where}: {first.message}",
            field="config",
        )


__all__ = [
    "CatalogItem",
    "CatalogResolutionError",
    "list_catalog",
    "resolve_variant",
    "validate_config",
]
