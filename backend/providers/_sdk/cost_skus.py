"""Component-wise SKU selection for cost estimates (#1318).

A cloud pricing catalog answers "what does service X cost?" with
hundreds of rows: one per engine, edition, machine series, redundancy
mode, storage class and region. Summing every row that merely belongs
to the right service and region mixes unrelated products and yields a
confident, materially wrong number.

This module is the cloud-agnostic half of the fix. A driver declares
what it is actually buying as a list of :class:`SkuComponent` — one
per billable dimension (vCPU-hours, RAM GiB-hours, storage GB-month,
license, backup, egress) — and :func:`select_line_items` resolves each
component against *exactly one* catalog row.

Both failure modes are refusals, never guesses:

* zero matching rows — returning a $0 line would render a paid
  database as free, which is worse than no estimate at all;
* more than one matching row — that is the over-counting bug in
  miniature, and picking "the first" or "the cheapest" silently
  encodes an assumption the operator never made.

The selector is shared rather than per-cloud because the catalogs
differ only in field names. GCP's Cloud Billing Catalog SKU and
Azure's Retail Prices row both carry an id, a human description, a
unit price in a currency, a usage unit, region scoping and a handful
of classification facets; each cloud projects its rows into
:class:`CatalogPrice` and reuses the matching, fail-closed and
provenance logic here. AWS's Pricing API filters server-side but
returns products in the same shape and can adopt the same projection
when its estimator is made SKU-aware.

No price ever lives in this module. Callers pass rows they just
fetched from the live pricing API — estimates come from the cloud's
API, never from a hard-coded table (see ``_sdk/cost.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from _sdk.cost import CostLineItem

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Refusal reasons. They reuse ``CostEstimateUnavailable.reason``
# vocabulary so a driver can pass them straight through.
REASON_SKU_NOT_FOUND = "sku_not_found"
REASON_AMBIGUOUS_SKU = "ambiguous_sku"

# How many candidate SKU ids to name in an ambiguity message. Enough
# for an operator to see the shape of the collision without pasting a
# hundred ids into a UI tooltip.
_AMBIGUITY_SAMPLE = 5


@dataclass(frozen=True)
class CatalogPrice:
    """One normalized price row from a cloud's pricing catalog.

    ``regions`` empty means the row is not region-scoped (global).
    ``attributes`` holds the cloud's own classification facets
    (GCP: ``resource_family`` / ``resource_group`` / ``usage_type``;
    Azure: ``product_name`` / ``sku_name`` / ``meter_name``) so a
    component can constrain on structured fields instead of guessing
    from prose.
    """

    sku_id: str
    description: str
    unit_price: float
    currency: str
    usage_unit: str
    regions: tuple[str, ...] = ()
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SkuComponent:
    """One billable dimension of a resource, plus how to find its row.

    ``require`` / ``exclude`` are case-insensitive substring tokens
    matched against the catalog description; ``attributes`` are exact
    (case-insensitive) equality checks against
    :attr:`CatalogPrice.attributes`. A row must satisfy all three to
    be a candidate.

    ``quantity`` is already in the row's billing unit: the plan
    builder multiplies out vCPU-hours and GiB-months, because only it
    knows what the caller asked for.
    """

    key: str
    label: str
    quantity: float
    quantity_unit: str
    require: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SkuPlan:
    """The full set of components one resource is billed on."""

    components: tuple[SkuComponent, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkuSelectionError:
    """A component could not be resolved to exactly one catalog row."""

    reason: str
    message: str
    component_key: str = ""


def select_line_items(
    *,
    plan: SkuPlan,
    prices: Iterable[CatalogPrice],
    region: str,
    currency: str,
) -> list[CostLineItem] | SkuSelectionError:
    """Resolve every component in ``plan`` against ``prices``.

    Returns the line items in plan order, or the first refusal. The
    whole estimate is abandoned on the first unresolved component:
    a partial estimate reads as a total and understates the bill.
    """
    rows = [row for row in prices if _priced(row, currency=currency) and _in_region(row, region=region)]
    line_items: list[CostLineItem] = []
    for component in plan.components:
        candidates = [row for row in rows if _matches(row, component=component)]
        if not candidates:
            return SkuSelectionError(
                reason=REASON_SKU_NOT_FOUND,
                component_key=component.key,
                message=(
                    f"no {currency} SKU in region {region!r} matched the "
                    f"{component.key!r} component of the estimate "
                    f"(require={list(component.require)}, "
                    f"exclude={list(component.exclude)}, "
                    f"attributes={dict(component.attributes)})"
                ),
            )
        distinct = {row.sku_id: row for row in candidates}
        if len(distinct) > 1:
            sample = sorted(distinct)[:_AMBIGUITY_SAMPLE]
            return SkuSelectionError(
                reason=REASON_AMBIGUOUS_SKU,
                component_key=component.key,
                message=(
                    f"{len(distinct)} SKUs matched the {component.key!r} "
                    f"component in region {region!r} ({', '.join(sample)}"
                    f"{', …' if len(distinct) > len(sample) else ''}); "
                    "narrow the request (edition, machine series, storage "
                    "type) rather than summing unrelated SKUs"
                ),
            )
        row = candidates[0]
        line_items.append(
            CostLineItem(
                label=(
                    f"{component.label} — {component.quantity:g} "
                    f"{component.quantity_unit} @ "
                    f"{row.unit_price:.6f} {row.currency}/{row.usage_unit or 'unit'}"
                ),
                sku=row.sku_id,
                monthly_amount=round(row.unit_price * component.quantity, 4),
                currency=row.currency,
                notes=f"{row.description} [{row.sku_id}]",
            )
        )
    return line_items


def _priced(row: CatalogPrice, *, currency: str) -> bool:
    """A row is usable only if it quotes a positive price in the
    requested currency.

    Catalogs carry 0.00 rows for free tiers and promotional SKUs. They
    match the same descriptions as the paid rows, so accepting one
    would produce a $0 line for a resource that does bill.
    """
    if row.currency != currency:
        return False
    return row.unit_price > 0.0


def _in_region(row: CatalogPrice, *, region: str) -> bool:
    if not row.regions:
        return True
    return region in row.regions or "global" in row.regions


def _matches(row: CatalogPrice, *, component: SkuComponent) -> bool:
    haystack = row.description.casefold()
    if any(token.casefold() not in haystack for token in component.require):
        return False
    if any(token.casefold() in haystack for token in component.exclude):
        return False
    for key, value in component.attributes.items():
        actual = row.attributes.get(key, "")
        if actual.casefold() != value.casefold():
            return False
    return True
