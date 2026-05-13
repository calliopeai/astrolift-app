"""
Drift detection comparator (#12, spec 07 §7).

Pure-Python diff logic. ``DriftDetectionWorkflow`` consults this
to compare a live-cluster snapshot against the platform DB's
source of truth and emit a structured diff payload.

Pairs with the existing modules:
  * #4 direct_apply.manifest_set_sha256 — overall snapshot
    fingerprint. If hashes match, fast-path: no drift.
  * #2 delivery dispatcher — drift detection runs only on
    direct_api / hybrid clusters (GitOps clusters' source of
    truth IS the repo, so drift detection runs against the repo
    elsewhere).

The actual k8s GET calls live in ``ClusterDriver.get`` (#11);
this module is the policy + diff layer.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from enum import StrEnum


class DriftKind(StrEnum):
    IMAGE_TAG = "image_tag"
    REPLICAS = "replicas"
    ENV_LITERAL = "env_literal"
    RESOURCES = "resources"
    INGRESS_HOSTNAME = "ingress_hostname"
    ANNOTATIONS = "annotations"
    LABELS = "labels"


@dataclasses.dataclass(frozen=True, slots=True)
class DriftDiff:
    """One field that drifted. ``expected`` is what the platform DB
    says; ``actual`` is what the live cluster has."""

    kind: DriftKind
    field_path: str
    expected: object
    actual: object


@dataclasses.dataclass(frozen=True, slots=True)
class WorkloadExpected:
    """Projection of the platform DB's source of truth for one
    workload."""

    name: str
    image_tag: str
    replicas: int
    env_literals: Mapping[str, str]
    resources: Mapping[str, str]
    """{'cpu_request': '100m', 'memory_limit': '512Mi', ...}"""
    annotations: Mapping[str, str]
    labels: Mapping[str, str]


@dataclasses.dataclass(frozen=True, slots=True)
class WorkloadObserved:
    """Projection of the live cluster's actual state."""

    name: str
    image_tag: str
    replicas: int
    env_literals: Mapping[str, str]
    resources: Mapping[str, str]
    annotations: Mapping[str, str]
    labels: Mapping[str, str]
    hpa_active: bool = False
    """When True, the workload has an HPA — the comparator skips
    the replicas check (HPA legitimately overrides the static
    replica count). Spec 07 §7."""


def compare_workload(
    *,
    expected: WorkloadExpected,
    observed: WorkloadObserved,
) -> tuple[DriftDiff, ...]:
    """Field-by-field diff. Returns every drift, not just the
    first — operator sees the full set in one go."""
    drifts: list[DriftDiff] = []

    if expected.image_tag != observed.image_tag:
        drifts.append(
            DriftDiff(
                kind=DriftKind.IMAGE_TAG,
                field_path=f"workload[{expected.name}].image_tag",
                expected=expected.image_tag,
                actual=observed.image_tag,
            )
        )

    # Spec rule: skip replicas check when HPA is active. Otherwise
    # the comparator would flag drift every time the HPA scaled.
    if not observed.hpa_active and expected.replicas != observed.replicas:
        drifts.append(
            DriftDiff(
                kind=DriftKind.REPLICAS,
                field_path=f"workload[{expected.name}].replicas",
                expected=expected.replicas,
                actual=observed.replicas,
            )
        )

    drifts.extend(
        _diff_dict(
            kind=DriftKind.ENV_LITERAL,
            path=f"workload[{expected.name}].env",
            expected=expected.env_literals,
            actual=observed.env_literals,
        )
    )
    drifts.extend(
        _diff_dict(
            kind=DriftKind.RESOURCES,
            path=f"workload[{expected.name}].resources",
            expected=expected.resources,
            actual=observed.resources,
        )
    )
    drifts.extend(
        _diff_dict(
            kind=DriftKind.ANNOTATIONS,
            path=f"workload[{expected.name}].annotations",
            expected=expected.annotations,
            actual=observed.annotations,
        )
    )
    drifts.extend(
        _diff_dict(
            kind=DriftKind.LABELS,
            path=f"workload[{expected.name}].labels",
            expected=expected.labels,
            actual=observed.labels,
        )
    )

    return tuple(drifts)


def compare_ingress(
    *,
    expected_hostname: str,
    observed_hostname: str,
    rule_id: int,
) -> tuple[DriftDiff, ...]:
    """Ingress hostname diff. Separate from workload because
    ingress lives on a different k8s object (Ingress vs Deployment)."""
    if expected_hostname != observed_hostname:
        return (
            DriftDiff(
                kind=DriftKind.INGRESS_HOSTNAME,
                field_path=f"ingress[{rule_id}].hostname",
                expected=expected_hostname,
                actual=observed_hostname,
            ),
        )
    return ()


def _diff_dict(
    *,
    kind: DriftKind,
    path: str,
    expected: Mapping[str, str],
    actual: Mapping[str, str],
) -> list[DriftDiff]:
    """Compare two flat dicts. Reports added, removed, and changed
    keys distinctly so the UI can render the diff cleanly."""
    out: list[DriftDiff] = []
    expected_keys = set(expected)
    actual_keys = set(actual)

    for k in sorted(expected_keys & actual_keys):
        if expected[k] != actual[k]:
            out.append(
                DriftDiff(
                    kind=kind,
                    field_path=f"{path}.{k}",
                    expected=expected[k],
                    actual=actual[k],
                )
            )
    for k in sorted(expected_keys - actual_keys):
        out.append(
            DriftDiff(
                kind=kind,
                field_path=f"{path}.{k}",
                expected=expected[k],
                actual=None,
            )
        )
    for k in sorted(actual_keys - expected_keys):
        out.append(
            DriftDiff(
                kind=kind,
                field_path=f"{path}.{k}",
                expected=None,
                actual=actual[k],
            )
        )
    return out


# ---- aggregation ---------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DriftReport:
    """Summary the workflow emits as the ``DRIFT_DETECTED`` event
    payload."""

    deployment_id: int
    drifts: tuple[DriftDiff, ...]
    auto_correct: bool

    @property
    def is_drifted(self) -> bool:
        return bool(self.drifts)

    @property
    def kinds(self) -> frozenset[DriftKind]:
        return frozenset(d.kind for d in self.drifts)


def build_report(
    *,
    deployment_id: int,
    workload_diffs: Sequence[DriftDiff],
    ingress_diffs: Sequence[DriftDiff] = (),
    auto_correct: bool = False,
) -> DriftReport:
    """Assemble the report. ``auto_correct`` is the env's setting;
    when True AND drifts exist, the workflow enqueues a
    RedeployWorkflow on top of emitting the event."""
    return DriftReport(
        deployment_id=deployment_id,
        drifts=tuple(workload_diffs) + tuple(ingress_diffs),
        auto_correct=auto_correct,
    )


def should_redeploy(report: DriftReport) -> bool:
    """``True`` iff the workflow should kick a RedeployWorkflow."""
    return report.is_drifted and report.auto_correct
