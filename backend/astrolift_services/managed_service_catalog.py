"""Cluster-scoped managed-service catalogue.

The provider SDK availability matrix owns product metadata while the live
plugin registry owns executable capability.  This module joins both sources so
GraphQL and mutations use the same answer: every installed driver is visible,
planned/stub entries remain discoverable, and only executable variants may be
provisioned.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Any

from _sdk.availability import MATRIX, ManagedServiceEntry

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


def list_catalog(plugin_slug: str, *, include_unprovisionable: bool = False) -> tuple[CatalogItem, ...]:
    """The managed-service catalogue for one provider plugin.

    By default ``planned`` rows are left out. They are roadmap metadata: no
    driver exists and none is being installed, so the entry documents an
    intention rather than a choice, and "planned" reads to most people as
    "available soon".

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
    metadata = _matrix_entries(plugin_slug)
    drivers = _driver_roles(plugin_slug)
    keys = sorted(set(metadata) | set(drivers))
    available_by_kind: dict[str, list[str]] = defaultdict(list)
    for kind, variant in keys:
        meta = metadata.get((kind, variant))
        if (kind, variant) in drivers and (meta is None or meta.status in {"ga", "preview", "experimental"}):
            available_by_kind[kind].append(variant)

    rows: list[CatalogItem] = []
    for kind, variant in keys:
        meta = metadata.get((kind, variant))
        driver_cls = drivers.get((kind, variant))
        status = meta.status if meta is not None else "experimental"
        available = driver_cls is not None and status in {"ga", "preview", "experimental"}
        if status == "deprecated":
            unavailable_reason = "This provider variant is deprecated or retired and cannot be provisioned."
        elif driver_cls is None:
            unavailable_reason = "Provider driver is not installed in this control plane."
        elif status == "planned":
            unavailable_reason = "Driver is a non-provisioning stub or planned capability."
        else:
            unavailable_reason = ""
        variants = available_by_kind.get(kind, [])
        configured_default = _DEFAULT_VARIANTS.get((plugin_slug, kind))
        is_default = available and (
            variant == configured_default or (configured_default is None and len(variants) == 1)
        )
        description = meta.description if meta is not None else "Installed third-party provider driver."
        if not include_unprovisionable and status == "planned":
            continue
        rows.append(
            CatalogItem(
                provider_plugin_slug=plugin_slug,
                kind=kind,
                variant=variant,
                display_name=description or f"{kind} / {variant}",
                description=description,
                status=status,
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
    rows = [row for row in list_catalog(plugin_slug, include_unprovisionable=True) if row.kind == kind]
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
