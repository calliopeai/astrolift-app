"""The shape of an orphan scan, and the rule that it fails loud (spec 43 §6).

VERIFY-CLEAN is the campaign's signature acceptance: teardown is only real when
it leaves nothing behind. A scanner that returns a count satisfies the letter of
that and none of it, because a count is read once, by the person who already
believes the teardown worked.

So the report here has two failure modes and both raise.

*Leftovers.* Anything still carrying the campaign's identity after teardown is a
defect in a deprovision path, listed one per line with the handle that found it.

*A family that could not be queried.* A scan that skipped Cloud SQL because the
API returned a permission error is not a clean scan, it is an unknown one.
Swallowing that is how an orphan scan comes to lie, so an errored family fails
exactly like a leftover does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from _cert.campaign import Campaign, match

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence


class OrphansFound(AssertionError):
    """Teardown left something behind, or a resource family could not be read.

    ``AssertionError`` because this is the campaign's acceptance check failing,
    and because a harness step that catches broad ``Exception`` around cloud
    calls should not accidentally swallow it.
    """


@dataclass(frozen=True)
class CloudResource:
    """One thing an inventory found, reduced to what ownership needs.

    ``tags`` is whatever key/value surface the resource carries -- ARM tags, GCP
    labels, blob metadata, or a parsed Service Bus ``userMetadata`` blob. An
    empty mapping is normal: several surfaces have no tag support at all, which
    is why ``identifier`` is matched too.
    """

    identifier: str
    location: str = ""
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Orphan:
    cloud: str
    service: str
    identifier: str
    location: str
    matched_on: str
    """The handle that proved ownership: a tag, or the resource name. A
    name-only match means the campaign tag never reached the resource, which is
    itself a finding."""

    def __str__(self) -> str:
        where = f" in {self.location}" if self.location else ""
        return f"{self.cloud}/{self.service}: {self.identifier}{where} (matched on {self.matched_on})"


@dataclass(frozen=True)
class ScanError:
    service: str
    error: str

    def __str__(self) -> str:
        return f"{self.service}: {self.error}"


#: One resource family: the service label a report prints, and the call that
#: lists it. The callable takes no arguments; live adapters close over their
#: client and project or resource group.
Family = tuple[str, "Callable[[], Iterable[CloudResource]]"]


@dataclass(frozen=True)
class ScanReport:
    cloud: str
    campaign: str
    scanned: tuple[str, ...]
    orphans: tuple[Orphan, ...] = ()
    errors: tuple[ScanError, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.orphans and not self.errors

    def raise_if_dirty(self) -> None:
        """Fail loud, itemized. Called by every harness step that runs a scan."""
        if self.is_clean:
            return
        lines = [
            f"orphan scan of {self.cloud} for campaign {self.campaign!r} is not clean "
            f"(queried: {', '.join(self.scanned) or 'nothing'})"
        ]
        if self.orphans:
            lines.append(f"{len(self.orphans)} resource(s) survived teardown:")
            lines += [f"  - {orphan}" for orphan in self.orphans]
            lines.append(
                "  each one is a defect in a deprovision path, not cleanup work: "
                "file it against the driver and fix it there"
            )
        if self.errors:
            lines.append(f"{len(self.errors)} resource family/families could not be read:")
            lines += [f"  - {error}" for error in self.errors]
            lines.append("  an unread family is an unknown result, not a clean one")
        raise OrphansFound("\n".join(lines))


def scan_families(
    *,
    cloud: str,
    campaign: Campaign,
    families: Sequence[Family],
) -> ScanReport:
    """Run every family and collect what belongs to ``campaign``.

    Each family is isolated: one that raises becomes a ``ScanError`` instead of
    aborting the scan, so a single denied API does not hide the leftovers the
    other families would have found.
    """
    orphans: list[Orphan] = []
    errors: list[ScanError] = []
    for service, list_resources in families:
        try:
            resources = list(list_resources())
        except Exception as exc:
            errors.append(ScanError(service=service, error=f"{type(exc).__name__}: {exc}"))
            continue
        for resource in resources:
            matched_on = match(campaign, cloud, tags=resource.tags, name=resource.identifier)
            if matched_on is None:
                continue
            orphans.append(
                Orphan(
                    cloud=cloud,
                    service=service,
                    identifier=resource.identifier,
                    location=resource.location,
                    matched_on=matched_on,
                )
            )
    return ScanReport(
        cloud=cloud,
        campaign=campaign.slug,
        scanned=tuple(service for service, _ in families),
        orphans=tuple(orphans),
        errors=tuple(errors),
    )
