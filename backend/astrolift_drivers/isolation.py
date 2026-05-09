"""
Managed-service isolation modes (#16, spec 11 §4).

A managed service binding gets one of two isolation modes:

* **shared** — multiple tenants share a single backing instance,
  isolated by per-app DB / user / namespace inside it. Cheap,
  fast to provision.
* **dedicated** — per-app backing instance. Higher cost, higher
  isolation; required for some compliance shapes.

Resolution chain (highest priority wins, but mode must be supported
by the variant):

  1. Manifest opt-in: ``[[managed_services]] isolation="dedicated"``
  2. Org policy override (compliance: 'always dedicated')
  3. Variant default

Pure-Python module. The activity that calls into a driver consults
this to know which provisioning path to take.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import Enum


class Isolation(str, Enum):
    SHARED = "shared"
    DEDICATED = "dedicated"


SOURCE_MANIFEST = "manifest"
SOURCE_ORG_POLICY = "org_policy"
SOURCE_VARIANT_DEFAULT = "variant_default"


class IsolationError(ValueError):
    """The requested isolation mode isn't supported by the variant
    or violates org policy."""


@dataclasses.dataclass(frozen=True, slots=True)
class VariantSupport:
    """Each plugin variant declares which isolation modes it
    supports and which is its default. ``allowed_modes`` is the
    closed set; ``default`` must be a member."""

    plugin_slug: str
    variant: str
    allowed_modes: frozenset[Isolation]
    default: Isolation

    def __post_init__(self) -> None:
        if not self.allowed_modes:
            raise ValueError(
                f"variant {self.plugin_slug}/{self.variant} declares no "
                "allowed isolation modes"
            )
        if self.default not in self.allowed_modes:
            raise ValueError(
                f"variant {self.plugin_slug}/{self.variant} default "
                f"{self.default} not in allowed_modes"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class IsolationDecision:
    mode: Isolation
    source: str  # one of SOURCE_*
    variant: VariantSupport


def parse_mode(value: str | None) -> Isolation | None:
    """Parse a manifest's ``isolation`` field. ``None`` / empty →
    None (caller falls back to org/variant default — DON'T default
    to SHARED here; the variant might not support it)."""
    if value is None or value == "":
        return None
    try:
        return Isolation(value)
    except ValueError as exc:
        raise IsolationError(
            f"isolation {value!r} not one of {[m.value for m in Isolation]}"
        ) from exc


def resolve(
    *,
    manifest_choice: Isolation | None,
    org_policy: Mapping[str, Isolation] | None,
    org_policy_kind_key: str,
    variant: VariantSupport,
) -> IsolationDecision:
    """Decide the isolation mode for a binding.

    ``org_policy`` maps service kind → required mode (e.g.
    ``{"postgres": Isolation.DEDICATED}`` for a HIPAA org). The
    org policy is **a hard floor** — it always wins over manifest
    when set and only requires opt-out via a separate compliance
    review (out of scope for this module).
    """
    # 1. Org policy is a hard floor.
    if org_policy and org_policy_kind_key in org_policy:
        required = org_policy[org_policy_kind_key]
        if required not in variant.allowed_modes:
            raise IsolationError(
                f"org policy requires isolation={required.value!r} for "
                f"kind {org_policy_kind_key!r} but variant "
                f"{variant.plugin_slug}/{variant.variant} doesn't support it; "
                "pick a different variant or update the policy"
            )
        return IsolationDecision(
            mode=required, source=SOURCE_ORG_POLICY, variant=variant,
        )

    # 2. Manifest opt-in.
    if manifest_choice is not None:
        if manifest_choice not in variant.allowed_modes:
            raise IsolationError(
                f"manifest requests isolation={manifest_choice.value!r} but "
                f"variant {variant.plugin_slug}/{variant.variant} only "
                f"supports {sorted(m.value for m in variant.allowed_modes)}"
            )
        return IsolationDecision(
            mode=manifest_choice, source=SOURCE_MANIFEST, variant=variant,
        )

    # 3. Variant default.
    return IsolationDecision(
        mode=variant.default, source=SOURCE_VARIANT_DEFAULT, variant=variant,
    )
