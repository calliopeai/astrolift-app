"""
Managed-service sizing abstraction + cost estimate contract
(#28, spec 11 §7 + §16).

Manifests declare service sizes from a small abstract vocabulary
(``small`` / ``medium`` / ``large`` / ``xlarge`` / ``custom``).
Each provider plugin maps these to its own native instance type.
This module owns:

* The vocabulary (a closed enum so a typo in a manifest fails
  validation up-front instead of mapping to nothing on one cloud).
* The mapping table — a registry of ``(plugin_slug, kind, size) →
  ManagedServiceSpec``. Plugins register their mappings at import
  time; orgs can override per-pair via a config dict.
* The cost estimate contract — an interface plugins implement
  optionally; the UI falls back to a catalog lookup when a plugin
  doesn't supply one.

Pure-Python: no Django or driver imports. The actual
``provision_managed_service`` activity calls into this to resolve
the spec, then hands the resulting native config to the plugin.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from enum import Enum


class Size(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    XLARGE = "xlarge"
    CUSTOM = "custom"


_KNOWN = {s.value for s in Size}


class SizeError(ValueError):
    """Bad size declaration in a manifest or override."""


def parse_size(value: str | None) -> Size:
    """Parse a manifest's ``size`` field. ``None`` / empty → MEDIUM
    (sensible default — neither too small to be useful nor too
    expensive on first deploy)."""
    if value is None or value == "":
        return Size.MEDIUM
    if value not in _KNOWN:
        raise SizeError(f"size {value!r} not one of {sorted(_KNOWN)}")
    return Size(value)


@dataclasses.dataclass(frozen=True, slots=True)
class ManagedServiceSpec:
    """The provider-native config the resolver hands the driver.

    ``native_class`` is the cloud-specific instance type
    (e.g. ``db.r6g.large`` for RDS). ``parameters`` carries any
    extra knobs the driver needs (storage GB, replicas, etc.).
    """

    plugin_slug: str
    kind: str
    size: Size
    native_class: str
    parameters: Mapping[str, str | int]

    def __post_init__(self) -> None:
        if not self.native_class and self.size != Size.CUSTOM:
            raise ValueError(f"native_class required for non-custom size; got {self.size}")


# ---- mapping registry ------------------------------------------------


_MAPPINGS: dict[tuple[str, str, Size], ManagedServiceSpec] = {}


def register_size_mapping(spec: ManagedServiceSpec) -> None:
    """Plugin packages call this at import time."""
    _MAPPINGS[(spec.plugin_slug, spec.kind, spec.size)] = spec


def lookup_size_mapping(*, plugin_slug: str, kind: str, size: Size) -> ManagedServiceSpec | None:
    return _MAPPINGS.get((plugin_slug, kind, size))


def clear_registry() -> None:
    """Test helper."""
    _MAPPINGS.clear()


def resolve_size(
    *,
    plugin_slug: str,
    kind: str,
    size: Size,
    custom_parameters: Mapping[str, str | int] | None = None,
    org_overrides: Mapping[tuple[str, str, Size], ManagedServiceSpec] | None = None,
) -> ManagedServiceSpec:
    """Map a (plugin, kind, size) to a concrete spec.

    Order:
      1. CUSTOM size → caller supplies ``custom_parameters``;
         module just validates and emits a spec with empty
         ``native_class`` (driver consumes the params directly).
      2. Org-level override (per-pair) wins over the registry.
      3. Plugin's registered mapping.
      4. None → :class:`SizeError` (caller turns into a deploy block).
    """
    if size == Size.CUSTOM:
        if not custom_parameters:
            raise SizeError(
                "size='custom' requires explicit parameters; "
                "manifest's [managed_services] block must include them"
            )
        return ManagedServiceSpec(
            plugin_slug=plugin_slug,
            kind=kind,
            size=Size.CUSTOM,
            native_class="",
            parameters=dict(custom_parameters),
        )

    if org_overrides and (plugin_slug, kind, size) in org_overrides:
        return org_overrides[(plugin_slug, kind, size)]

    spec = _MAPPINGS.get((plugin_slug, kind, size))
    if spec is None:
        raise SizeError(
            f"no size mapping for ({plugin_slug!r}, {kind!r}, "
            f"{size.value!r}); plugin must register one or org must "
            "configure an override"
        )
    return spec


# ---- cost estimate contract -----------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CostEstimate:
    """Result of a plugin's ``cost_estimate(spec) -> CostEstimate``.

    Currency is fixed at USD because every cloud bills in USD even
    when the operator's contract is in another currency — the
    conversion is a billing concern, not a sizing one.
    """

    monthly_usd_low: float
    monthly_usd_high: float
    notes: str = ""

    def __post_init__(self) -> None:
        if self.monthly_usd_low < 0 or self.monthly_usd_high < 0:
            raise ValueError("cost estimates must be non-negative")
        if self.monthly_usd_high < self.monthly_usd_low:
            raise ValueError("monthly_usd_high must be >= monthly_usd_low")


CostEstimator = Callable[[ManagedServiceSpec], CostEstimate]


_ESTIMATORS: dict[tuple[str, str], CostEstimator] = {}


def register_cost_estimator(*, plugin_slug: str, kind: str, fn: CostEstimator) -> None:
    _ESTIMATORS[(plugin_slug, kind)] = fn


def estimate_cost(spec: ManagedServiceSpec) -> CostEstimate | None:
    """Returns a CostEstimate when the plugin supplies an estimator;
    None otherwise. The UI falls back to a static catalog lookup
    in that case."""
    fn = _ESTIMATORS.get((spec.plugin_slug, spec.kind))
    if fn is None:
        return None
    return fn(spec)


def clear_cost_estimators() -> None:
    """Test helper."""
    _ESTIMATORS.clear()
