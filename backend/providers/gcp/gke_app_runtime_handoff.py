"""Private current runtime targets from protected apply evidence, not authority."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gcp.gke_app_apply import _NAME, CompiledAppPlan, GKEAppApply, ObservedResource, PlacementAcceptance, _guid, _sha
from gcp.gke_identity_observation import GKEObservationError, _checkpoint
from gcp.gke_identity_runtime import GKEIdentityRuntimeObserver, RuntimeObservation, WorkloadTarget

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any


@dataclass(frozen=True)
class RuntimeHandoff:
    journal_id: str
    journal_version: int
    operation_id: str
    generation: int
    plan: CompiledAppPlan
    service_account_name: str
    controllers: tuple[ObservedResource, ...]

    def __post_init__(self) -> None:
        _guid(self.journal_id)
        _guid(self.operation_id)
        if (
            type(self.journal_version) is not int
            or self.journal_version < 1
            or type(self.generation) is not int
            or self.generation < 1
            or type(self.plan) is not CompiledAppPlan
            or self.plan.placement is None
            or not isinstance(self.service_account_name, str)
            or not _NAME.fullmatch(self.service_account_name)
            or self.operation_id != self.plan.execution.operation_id
            or type(self.controllers) is not tuple
            or not 1 <= len(self.controllers) <= 16
            or any(type(r) is not ObservedResource for r in self.controllers)
            or len({r.resource.path for r in self.controllers}) != len(self.controllers)
            or {r.resource.path for r in self.controllers}
            != {r.path for r in self.plan.resources if r.kind in ("Deployment", "StatefulSet", "DaemonSet")}
            or len({r.uid for r in self.controllers}) != len(self.controllers)
        ):
            raise GKEObservationError("INVALID_RUNTIME_HANDOFF")
        for row in self.controllers:
            if (
                row.resource not in self.plan.resources
                or row.native_projection_sha256 != row.resource.projection_sha256
            ):
                raise GKEObservationError("RUNTIME_CONFIGURATION_UNOBSERVED")
            _sha(row.native_template_sha256)
            _sha(row.native_placement_sha256)


@dataclass(frozen=True)
class RuntimeTargets:
    configuration_observed: bool
    reason: str
    targets: tuple[WorkloadTarget, ...] = ()


def check_placement(placement: PlacementAcceptance, context: Any, cluster: Any, pools: tuple[str, ...]) -> None:
    if (
        placement.cluster_resource != context.cluster_resource
        or placement.native_cluster_id != cluster.id
        or placement.autopilot != bool(cluster.autopilot.enabled)
        or not set(placement.allowed_node_pools) <= set(pools)
    ):
        raise GKEObservationError("ACCEPTED_PLACEMENT_UNAVAILABLE")


class AcceptedPlacementRuntimeObserver(GKEIdentityRuntimeObserver):
    def __init__(self, *args: Any, acceptance: PlacementAcceptance, **kwargs: Any) -> None:
        self.acceptance = acceptance
        super().__init__(*args, **kwargs)

    def _cluster(self, checkpoint: Callable[[], None]) -> tuple[Any, tuple[str, ...]]:
        cluster, pools = self._source._cluster(checkpoint, accepted_node_pools=self.acceptance.allowed_node_pools)
        check_placement(self.acceptance, self.context, cluster, pools)
        return cluster, pools

    def _pool_snapshot(
        self,
        cluster: Any,
        pools: tuple[str, ...],
        checkpoint: Callable[[], None],
        *,
        accepted_node_pools: tuple[str, ...] | None = None,
    ) -> tuple[Any, ...]:
        if accepted_node_pools is not None and accepted_node_pools != self.acceptance.allowed_node_pools:
            raise GKEObservationError("ACCEPTED_PLACEMENT_UNAVAILABLE")
        return super()._pool_snapshot(
            cluster, pools, checkpoint, accepted_node_pools=self.acceptance.allowed_node_pools
        )


def current_runtime_targets(
    driver: GKEAppApply, handoff: RuntimeHandoff, *, checkpoint: Callable[[], None]
) -> RuntimeTargets:
    """Caller must freshly bind every callback to the actual committed handoff."""
    if type(driver) is not GKEAppApply or type(handoff) is not RuntimeHandoff or not callable(checkpoint):
        raise GKEObservationError("CURRENT_RUNTIME_ADMISSION_REQUIRED")
    _checkpoint(checkpoint)
    acceptance = handoff.plan.placement
    assert acceptance is not None  # Exact frozen type validates this before native work.
    adapter = driver._admit(checkpoint, acceptance)
    targets = []
    for recorded in handoff.controllers:
        native = adapter.request(recorded.resource)
        observed = driver._observe(native, recorded.resource, recorded.uid)
        assert type(recorded.generation) is int
        if (
            observed.generation != recorded.generation
            or observed.native_template_sha256 != recorded.native_template_sha256
            or observed.native_placement_sha256 != recorded.native_placement_sha256
        ):
            raise GKEObservationError("RUNTIME_ORIGINAL_CONTROLLER_CHANGED")
        count = recorded.resource.replicas
        if recorded.resource.kind == "DaemonSet":
            status = native.get("status", {})
            if type(status.get("observedGeneration")) is not int or status["observedGeneration"] != recorded.generation:
                driver._admit(checkpoint, acceptance)
                _checkpoint(checkpoint)
                return RuntimeTargets(True, "DAEMON_GENERATION_PENDING")
            count = status.get("desiredNumberScheduled")
            if count is None or (type(count) is int and count == 0):
                driver._admit(checkpoint, acceptance)
                _checkpoint(checkpoint)
                return RuntimeTargets(True, "DAEMON_COUNT_PENDING")
            if type(count) is not int or not 1 <= count <= 256:
                raise GKEObservationError("DAEMON_COUNT_UNVERIFIED")
        assert type(count) is int
        targets.append(
            WorkloadTarget(
                recorded.resource.namespace,
                recorded.resource.kind,
                recorded.resource.name,
                recorded.uid,
                handoff.service_account_name,
                recorded.generation,
                json.dumps(native["spec"]["selector"], sort_keys=True, separators=(",", ":")),
                recorded.native_template_sha256,
                recorded.native_placement_sha256,
                count,
                acceptance.allowed_node_pools,
            )
        )
        _checkpoint(checkpoint)
    driver._admit(checkpoint, acceptance)
    _checkpoint(checkpoint)
    return RuntimeTargets(True, "RUNTIME_TARGETS_OBSERVED", tuple(targets))


def observe_handoff_runtime(
    driver: GKEAppApply, handoff: RuntimeHandoff, *, checkpoint: Callable[[], None]
) -> RuntimeObservation:
    current = current_runtime_targets(driver, handoff, checkpoint=checkpoint)
    if not current.targets:
        return RuntimeObservation(current.configuration_observed, False, current.reason)
    assert handoff.plan.placement is not None
    with AcceptedPlacementRuntimeObserver(
        driver.context,
        current.targets,
        acceptance=handoff.plan.placement,
        clients=driver.observer._clients,
    ) as observer:
        observer._source._credentials = driver.observer._credentials
        result = observer.observe(checkpoint=checkpoint)
    _checkpoint(checkpoint)
    return result
