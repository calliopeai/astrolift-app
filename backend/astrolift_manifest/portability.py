"""
Portability mode declaration + validation (#51, spec 20 §1-2).

A manifest declares one of three portability modes; this module
parses + validates the declaration. The variant *resolution* is
``astrolift_drivers/variant_resolution.py`` (#55); this module is
the up-front validity check that runs before any resolution.

Modes:

* **auto** (default) — platform picks concrete variants based on
  cluster plugin capabilities.
* **portable** — like auto but rejects any block with a
  cloud-specific variant. The app declares 'I want to be deployable
  anywhere'; pinning a single Postgres-flavored RDS variant
  breaks that promise.
* **pinned** — every service kind / storage class / ingress
  feature is pinned to a specific variant. Bound cluster must
  expose that exact variant or deploy fails.

Implicit downgrade rules: a manifest with at least one ``variant``
pin is implicitly:
  - ``pinned`` if every variant-able block has a pin,
  - ``auto`` (with warnings) if some are pinned and some abstract,
  - ``portable`` is rejected outright — pinning while declaring
    portable is a contradiction.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Sequence
from enum import Enum


class PortabilityMode(str, Enum):
    AUTO = "auto"
    PORTABLE = "portable"
    PINNED = "pinned"


PIN_RE = re.compile(r"^[a-z][a-z0-9-]*/[a-z][a-z0-9-]*$")


class PortabilityViolation(ValueError):
    """A manifest violates its declared portability mode."""

    def __init__(self, *, code: str, message: str, kinds: Sequence[str] = ()):
        self.code = code
        self.kinds = tuple(kinds)
        super().__init__(message)


@dataclasses.dataclass(frozen=True, slots=True)
class BlockPinSnapshot:
    """The minimum projection of a manifest block this module needs.

    Caller maps each variant-able block (managed_services entry,
    ingress, etc.) to one of these.
    """

    kind: str
    """The manifest's kind label (postgres, redis, ingress, ...)."""

    name: str = ""
    """Optional block name for error messages (e.g. 'main_db')."""

    variant_pin: str = ""
    """``"<plugin>/<variant>"`` if pinned, empty otherwise."""

    has_portable_variant: bool = True
    """Set False by the caller when the only variants the platform
    knows about for this kind are cloud-specific (no portable
    fallback). The caller computes this from the plugin catalog;
    this module just consumes the boolean."""


@dataclasses.dataclass(frozen=True, slots=True)
class ValidationReport:
    """Output of :func:`validate`."""

    declared_mode: PortabilityMode
    effective_mode: PortabilityMode
    warnings: tuple[str, ...]
    """Non-fatal observations the operator should see (e.g.
    'manifest declared auto but pins 2/5 blocks — implicit hybrid')."""


def parse_pin(pin: str) -> tuple[str, str] | None:
    """``"<plugin>/<variant>"`` → (plugin, variant). Empty / malformed → None."""
    if not pin or not PIN_RE.match(pin):
        return None
    plugin, _, variant = pin.partition("/")
    return plugin, variant


def parse_mode(value: str | None) -> PortabilityMode:
    """Parse a manifest's ``portability`` field. ``None`` / empty →
    AUTO (default per spec 20)."""
    if value is None or value == "":
        return PortabilityMode.AUTO
    try:
        return PortabilityMode(value)
    except ValueError as exc:
        raise PortabilityViolation(
            code="invalid_portability_mode",
            message=(f"portability {value!r} is not one of " f"{[m.value for m in PortabilityMode]}"),
        ) from exc


def validate(
    *,
    declared: PortabilityMode,
    blocks: Iterable[BlockPinSnapshot],
) -> ValidationReport:
    """Apply the rules. Raises :class:`PortabilityViolation` on
    violations; returns a :class:`ValidationReport` with the
    effective (possibly-downgraded) mode and any non-fatal warnings.

    Order of checks:
      1. Validate any present pins are syntactically well-formed.
      2. Apply mode-specific rules:
         - PORTABLE rejects pins (contradiction).
         - PORTABLE rejects blocks with no portable variant.
         - PINNED requires every block to have a pin.
      3. AUTO: warn on hybrid (mix of pinned + abstract).
    """
    blocks_list = list(blocks)

    # 1. Pin syntax check
    bad_pins: list[str] = []
    for b in blocks_list:
        if b.variant_pin and parse_pin(b.variant_pin) is None:
            bad_pins.append(f"{b.kind}/{b.name}")
    if bad_pins:
        raise PortabilityViolation(
            code="invalid_pin_syntax",
            message=(f"variant pin must be '<plugin>/<variant>'; " f"malformed in: {bad_pins}"),
            kinds=bad_pins,
        )

    pinned_blocks = [b for b in blocks_list if b.variant_pin]
    abstract_blocks = [b for b in blocks_list if not b.variant_pin]
    cloud_only = [b for b in abstract_blocks if not b.has_portable_variant]

    warnings: list[str] = []
    effective = declared

    if declared == PortabilityMode.PORTABLE:
        if pinned_blocks:
            raise PortabilityViolation(
                code="portable_with_pins",
                message=(
                    "manifest declares portability='portable' but pins "
                    f"{len(pinned_blocks)} block(s); pinning a "
                    "cloud-specific variant breaks the portable contract"
                ),
                kinds=[f"{b.kind}/{b.name}" for b in pinned_blocks],
            )
        if cloud_only:
            raise PortabilityViolation(
                code="portable_no_fallback",
                message=(
                    "manifest declares portability='portable' but "
                    f"{len(cloud_only)} kind(s) have no portable variant "
                    "in the plugin catalog"
                ),
                kinds=[f"{b.kind}/{b.name}" for b in cloud_only],
            )
        return ValidationReport(
            declared_mode=declared,
            effective_mode=PortabilityMode.PORTABLE,
            warnings=tuple(warnings),
        )

    if declared == PortabilityMode.PINNED:
        unpinned = [b for b in blocks_list if not b.variant_pin]
        if unpinned:
            raise PortabilityViolation(
                code="pinned_missing_pins",
                message=(
                    "manifest declares portability='pinned' but "
                    f"{len(unpinned)} block(s) have no variant pin"
                ),
                kinds=[f"{b.kind}/{b.name}" for b in unpinned],
            )
        return ValidationReport(
            declared_mode=declared,
            effective_mode=PortabilityMode.PINNED,
            warnings=tuple(warnings),
        )

    # AUTO: implicit downgrade signaling.
    if pinned_blocks and abstract_blocks:
        warnings.append(
            f"manifest declared 'auto' but pins {len(pinned_blocks)}/"
            f"{len(blocks_list)} block(s); deploys remain valid but the "
            "manifest is no longer fully portable"
        )
    elif pinned_blocks and not abstract_blocks:
        # Every block pinned — operator probably meant pinned.
        warnings.append(
            "manifest declared 'auto' but every block is pinned — "
            "consider declaring portability='pinned' for clarity"
        )

    return ValidationReport(
        declared_mode=PortabilityMode.AUTO,
        effective_mode=PortabilityMode.AUTO,
        warnings=tuple(warnings),
    )
