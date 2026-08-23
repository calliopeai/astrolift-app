"""Cross-cloud coverage view over the availability matrix (#21).

Astrolift's promise is that an app moves between clouds. A managed-service
kind that is executable on one cloud and absent on another quietly breaks
that promise: an ``astrolift.toml`` naming the kind is pinned to whichever
cloud ships it, and nothing tells the author. Answering "can this app run on
Azure?" today means reading ``availability.py`` entry by entry.

This module turns the flat matrix into that answer: per kind, which of the
public clouds have an executable variant and which do not. ``k8s_native``
stays a visible column because an in-cluster variant is a real portability
answer, but it is not a *cloud* column -- a kind that only k8s_native ships
is not a per-cloud gap, it is a deliberately portable choice.

Pure functions over ``MATRIX``; no I/O, no registry loading. The declared-gap
ledger in ``coverage_gaps`` consumes ``gaps()``.
"""

from __future__ import annotations

from dataclasses import dataclass

from _sdk.availability import MATRIX, AvailabilityMatrix

EXECUTABLE_STATUSES = frozenset({"ga", "preview"})
"""Statuses a binding can actually be provisioned from. ``experimental`` is
implemented but unsupported and gated from new provisioning; ``planned`` is
roadmap metadata and ``deprecated`` is on its way out. None of those statuses
satisfies capability negotiation or counts as portable coverage."""

CLOUDS: tuple[str, ...] = ("aws", "gcp", "azure")
"""Portability is judged across the public clouds. These are the columns a
gap can be declared against."""

IN_CLUSTER = "k8s_native"

#: Kinds deliberately outside the default catalogue. Their drivers stay in the
#: tree and keep working for anyone who opts in; they are not part of the
#: surface Astrolift guarantees across clouds, so their coverage holes are not
#: tracked as work.
#:
#: Two are worth explaining, because in both cases the capability is not lost,
#: it simply is not a resource a manifest books.
#:
#: ``cdn`` -- CloudFront ships with the ``static_site`` topology, so a CDN
#: arrives with the thing that needs one.
#:
#: ``api_gateway`` -- Astrolift already terminates ingress and routes to
#: workloads. A bookable gateway alongside that is a second answer to a
#: question the platform has already answered.
#:
#: ``time_series`` overlaps ``observability`` almost entirely; two of its three
#: variants are managed Prometheus, which is what ``kube_prometheus_stack``
#: already provides in-cluster.
OPT_IN_TIER: frozenset[str] = frozenset(
    {
        "api_gateway",
        "cdn",
        "database_proxy",
        "mq",
        "sms",
        "stream",
        "time_series",
        "warehouse",
        "wide_column",
    },
)

COLUMNS: tuple[str, ...] = (*CLOUDS, IN_CLUSTER)


@dataclass(frozen=True)
class CoverageCell:
    """One (kind, plugin) square of the matrix."""

    plugin_id: str
    executable: tuple[str, ...]
    planned: tuple[str, ...]
    deprecated: tuple[str, ...]

    @property
    def is_executable(self) -> bool:
        return bool(self.executable)


@dataclass(frozen=True)
class KindCoverage:
    """One row: how a single kind is covered across ``COLUMNS``."""

    kind: str
    cells: tuple[CoverageCell, ...]
    """One cell per entry in ``COLUMNS``, in that order."""

    def cell(self, plugin_id: str) -> CoverageCell:
        for cell in self.cells:
            if cell.plugin_id == plugin_id:
                return cell
        raise KeyError(plugin_id)

    @property
    def covered_clouds(self) -> tuple[str, ...]:
        return tuple(c for c in CLOUDS if self.cell(c).is_executable)

    @property
    def missing_clouds(self) -> tuple[str, ...]:
        return tuple(c for c in CLOUDS if not self.cell(c).is_executable)

    @property
    def is_cloud_portable(self) -> bool:
        """Executable on every public cloud."""
        return not self.missing_clouds

    @property
    def is_portable(self) -> bool:
        """Reachable on every cloud, by a managed variant or an in-cluster one.

        We run the cluster, so an in-cluster variant is parity everywhere at
        once. That makes it a portability answer and not a consolation: a kind
        with one is reachable wherever Astrolift is installed, whether or not
        the underlying cloud sells a managed equivalent.

        This is a claim about the runtime, and for a while it was only a claim:
        driver lookup was scoped to the cluster's own plugin, so an app on
        EKS/GKE/AKS could not book an in-cluster variant at all and this
        returned True for kinds nobody could actually provision (#1484). The
        lookup now falls back to ``k8s_native``
        (``astrolift_drivers.managed_resolution``), and
        ``tests/_sdk/test_coverage_runtime.py`` resolves a real driver for
        every kind that is portable only by way of its in-cluster variant, so
        the rule and the runtime cannot drift apart again in silence.
        """
        return self.is_cloud_portable or self.cell(IN_CLUSTER).is_executable


def coverage(matrix: AvailabilityMatrix = MATRIX) -> tuple[KindCoverage, ...]:
    """Every kind in the matrix, kind-sorted, with a cell per column."""
    rows: list[KindCoverage] = []
    for kind in sorted({m.kind for m in matrix.managed_services}):
        entries = [m for m in matrix.managed_services if m.kind == kind]
        cells = tuple(
            CoverageCell(
                plugin_id=plugin_id,
                executable=tuple(
                    sorted(m.variant for m in entries if m.plugin_id == plugin_id and m.status in EXECUTABLE_STATUSES)
                ),
                planned=tuple(sorted(m.variant for m in entries if m.plugin_id == plugin_id and m.status == "planned")),
                deprecated=tuple(
                    sorted(m.variant for m in entries if m.plugin_id == plugin_id and m.status == "deprecated")
                ),
            )
            for plugin_id in COLUMNS
        )
        rows.append(KindCoverage(kind=kind, cells=cells))
    return tuple(rows)


def gaps(matrix: AvailabilityMatrix = MATRIX) -> tuple[tuple[str, str], ...]:
    """Every (kind, cloud) a portable app can trip over, sorted.

    A gap needs a cloud that already proves the kind is a cloud-managed
    shape. A kind nobody ships on any cloud is a catalogue entry, not a
    portability trap, so it yields no gaps.

    Two further exclusions, both because a gap is meant to name something an
    app can trip over rather than an asymmetry in the table:

    * A kind with an in-cluster variant is reachable on every cloud already,
      so its missing managed variants are polish. See :attr:`is_portable`.
    * A kind in :data:`OPT_IN_TIER` is opt-in. Its driver still works;
      it is simply not part of the surface Astrolift guarantees across clouds,
      so a hole in it is a choice rather than a defect.
    """
    return tuple(
        (row.kind, cloud)
        for row in coverage(matrix)
        if row.covered_clouds and not row.is_portable and row.kind not in OPT_IN_TIER
        for cloud in row.missing_clouds
    )
