"""Private provider-native infrastructure actual adapters; no product cost API.

Project attribution is unproven. No collector or public resolver calls this seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import date


@dataclass(frozen=True)
class BillingActualLineItem:
    """Private native infrastructure result, not a project-attribution receipt.

    ``managed_service_guid`` is a raw provider tag/label value, possibly empty
    or unknown. It does not prove current ownership or project attribution.
    ``amount_cents`` and ``currency`` preserve the native adapter result;
    ``provider`` and ``service`` are optional provider hints, not ownership proof.
    No collector, snapshot writer, cost panel or public API consumes these rows.
    """

    managed_service_guid: str
    amount_cents: int
    currency: str = "USD"
    provider: str = ""
    service: str = ""


@dataclass(frozen=True)
class BillingActualsUnavailable:
    """Private adapter refusal: credentials, native export or API unavailable.

    No active collector handles this result. It must not become a synthetic
    monetary zero or a public claim of complete/project-attributed cost data.
    """

    reason: str
    """One of: 'unauthenticated' | 'api_error' | 'not_enabled' |
    'unsupported'. Free-text detail in ``message``."""

    message: str = ""


BillingActualsResult = list[BillingActualLineItem] | BillingActualsUnavailable


class BillingActualsClient(Protocol):
    """Private, unused native-cloud actual adapter protocol.

    An operator-authorized future caller would query the native provider API
    over the inclusive ``start`` / exclusive ``end`` window and preserve raw
    tag/label values and currency. Missing tags remain unknown. This grouping
    does not establish a project's ownership, completeness or invoice amount.
    No production resolver, schedule or activity currently invokes this seam;
    exposing it requires a separately reviewed attribution/authority contract.
    Failures return ``BillingActualsUnavailable`` rather than inferred prices.
    """

    def query_actuals_by_service(
        self,
        *,
        start: date,
        end: date,
        currency: str = "USD",
    ) -> BillingActualsResult: ...
