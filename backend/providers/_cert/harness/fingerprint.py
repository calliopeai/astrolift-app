"""What "end state identical" means, concretely (spec 43 §0.1 step 7).

REPRODUCE has never run for any cell on any cloud. Written as a loop counter it
never will mean anything either: running the cycle twice and seeing green twice
proves the cycle is repeatable, not that it is *idempotent*, and the defect the
step exists to catch -- non-idempotent teardown, already found by hand on AWS --
hides in the gap between those two. A teardown that releases a resource but not
its name lets the second run go green while provisioning something different.

So the comparison, not the second run, is the deliverable. This module says what
is compared.

**Two fingerprints per cycle, not one.** The obvious reading of "end state" is
the state after teardown, and that half is here: the app is gone from the API
and the cloud carries nothing owned by the campaign. But a cycle that ends clean
both times can still have built something different in the middle, and that is
precisely the non-idempotent-teardown symptom -- run 2's database is
``…-records-2`` because run 1's name was never released. So the cycle is also
fingerprinted at its high-water mark, after VERIFY-UP, and both are compared.

**The cloud half comes from the orphan scanner.** At VERIFY-CLEAN its output is
a list of defects; at VERIFY-UP the identical call is a census of what the
campaign owns, and the type is named for its primary use. Reusing it is what
gives the comparison cloud-side resource *names*, which no platform API exposes:
``ManagedServiceType`` carries name, kind, variant and status but not
``ManagedService.backend_ref``, the provider-side handle the drivers actually
write. Names are where a released-but-not-freed resource shows up, so a
comparison without them cannot see the defect it exists for.

**What is compared**

* the workloads, by name and ready replica count
* the managed services, by binding name, kind, variant and status
* every campaign-owned cloud resource at the high-water mark, as
  ``family:identifier``
* after teardown: whether the app is still visible to the API, and the
  campaign-owned cloud resources that survived (empty in a passing run)
* after teardown: which resource families the scan *read*. Two clean scans that
  read different families are not the same result -- one of them was partly
  blind -- and comparing only the leftovers would call them equal.

**What is deliberately excluded, and why.** Each of these differs between two
correct runs by construction, and comparing it would make REPRODUCE permanently
red, which is indistinguishable from not running it.

* deployment, workflow and resource ids, and every timestamp
* endpoints, hostnames and ARN suffixes: an RDS endpoint carries a token minted
  per creation, while the instance identifier it is derived from does not. The
  identifier is what the census reports and what is compared.
* pod names, which carry a ReplicaSet hash

The exclusions are a projection, not a filter: a fingerprint is built from named
fields, so a field added to an observation later stays out of the comparison
until somebody puts it in on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from _cert.harness.platform import ManagedServiceObservation, WorkloadObservation
    from _cert.orphans.model import ScanReport


def census(report: ScanReport) -> tuple[str, ...]:
    """The campaign-owned resources a scan saw, as sorted ``family:identifier``.

    Sorted because two clouds' list APIs make no ordering promise and an
    ordering difference is not an end-state difference.
    """
    return tuple(sorted(f"{orphan.service}:{orphan.identifier}" for orphan in report.orphans))


@dataclass(frozen=True)
class Fingerprint:
    """One cycle's comparable end state."""

    up_workloads: tuple[tuple[str, int], ...] = ()
    up_services: tuple[tuple[str, str, str, str], ...] = ()
    up_cloud_resources: tuple[str, ...] = ()
    clean_app_visible: bool = True
    clean_cloud_resources: tuple[str, ...] = ()
    clean_families_read: tuple[str, ...] = ()

    @staticmethod
    def up_projection(
        workloads: Iterable[WorkloadObservation],
        services: Iterable[ManagedServiceObservation],
        report: ScanReport | None,
    ) -> dict:
        return {
            "up_workloads": tuple(sorted((w.name, w.ready_replicas) for w in workloads)),
            "up_services": tuple(sorted((s.name, s.kind, s.variant, s.status) for s in services)),
            "up_cloud_resources": census(report) if report is not None else (),
        }

    @staticmethod
    def clean_projection(app_visible: bool, report: ScanReport | None) -> dict:
        return {
            "clean_app_visible": app_visible,
            "clean_cloud_resources": census(report) if report is not None else (),
            "clean_families_read": (
                tuple(sorted(set(report.scanned) - {error.service for error in report.errors}))
                if report is not None
                else ()
            ),
        }


@dataclass(frozen=True)
class Difference:
    what: str
    first: str
    second: str
    meaning: str

    def __str__(self) -> str:
        return f"{self.what}: run 1 {self.first}, run 2 {self.second} -- {self.meaning}"


def compare(first: Fingerprint, second: Fingerprint) -> tuple[Difference, ...]:
    """Every way the two runs did not end in the same state.

    Empty means REPRODUCE passed. Each difference says what it means, because
    the whole reason this step exists is that "run 2 differs" is not by itself
    something anybody can act on.
    """
    differences: list[Difference] = []

    if first.up_workloads != second.up_workloads:
        differences.append(
            Difference(
                "workloads at VERIFY-UP",
                _render(first.up_workloads),
                _render(second.up_workloads),
                "the second run did not converge to the same running shape, so the cycle depends on "
                "state the first run left behind",
            )
        )

    if first.up_services != second.up_services:
        differences.append(
            Difference(
                "managed services at VERIFY-UP",
                _render(first.up_services),
                _render(second.up_services),
                "the same manifest booked a different set of bindings, which means provisioning read "
                "residue from the first run",
            )
        )

    added, removed = _diff(first.up_cloud_resources, second.up_cloud_resources)
    if added or removed:
        differences.append(
            Difference(
                "cloud resources at VERIFY-UP",
                _render(removed) or "(nothing extra)",
                _render(added) or "(nothing extra)",
                "the second run provisioned resources under different names. This is the "
                "non-idempotent-teardown signature: the first teardown released the resource but not "
                "its name, so the driver picked a new one",
            )
        )

    if first.clean_app_visible != second.clean_app_visible:
        differences.append(
            Difference(
                "app visible after TEARDOWN",
                str(first.clean_app_visible),
                str(second.clean_app_visible),
                "teardown removed the app from the API in one run and not the other, so deregister is order-dependent",
            )
        )

    added, removed = _diff(first.clean_cloud_resources, second.clean_cloud_resources)
    if added or removed:
        differences.append(
            Difference(
                "cloud residue after TEARDOWN",
                _render(removed) or "(none)",
                _render(added) or "(none)",
                "the two teardowns left different residue, so what survives depends on what the "
                "previous run did -- the definition of non-idempotent",
            )
        )

    if first.clean_families_read != second.clean_families_read:
        differences.append(
            Difference(
                "resource families successfully scanned",
                _render(first.clean_families_read),
                _render(second.clean_families_read),
                "one of the two scans was partly blind, so the two clean results are not comparable "
                "and neither certifies the other",
            )
        )

    return tuple(differences)


def _diff(first: Sequence[str], second: Sequence[str]) -> tuple[list[str], list[str]]:
    """(only in second, only in first)."""
    return sorted(set(second) - set(first)), sorted(set(first) - set(second))


def _render(values: Iterable) -> str:
    rendered = [v if isinstance(v, str) else "/".join(str(part) for part in v) for v in values]
    return ", ".join(rendered)
