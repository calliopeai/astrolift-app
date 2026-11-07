"""Readiness predicates distinguish accepted configuration from running state."""

from __future__ import annotations

from typing import Any


def contains(actual: Any, desired: Any) -> bool:
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            key in actual and contains(actual[key], value) for key, value in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(desired)
            and all(contains(a, b) for a, b in zip(actual, desired, strict=True))
        )
    return type(actual) is type(desired) and actual == desired


def condition(value: dict[str, Any], name: str) -> bool:
    return bool(value.get("conditions", value).get(name, {}).get("status") == "True")


def inactive(value: dict[str, Any], name: str) -> bool:
    entry = value.get("conditions", value).get(name)
    return bool(entry is None or entry.get("status") == "False")


def node_ready(node: dict[str, Any]) -> bool:
    return all(condition(node, name) for name in ("provisioned", "kubernetesReady", "nodeAgentReady")) and all(
        inactive(node, name)
        for name in (
            "deleting",
            "deleteRequested",
            "rebooting",
            "markedForRecycling",
            "markedForRepair",
            "controlPlaneRolloutRequired",
        )
    )


def pool_ready(pool: dict[str, Any], nodes: list[dict[str, Any]]) -> bool:
    desired = pool.get("desiredNodeCount")
    if type(desired) is not int or desired < 0 or not inactive(pool, "deleting"):
        return False
    members = [node for node in nodes if node.get("nodePoolID") == pool.get("id")]
    return (
        len(members) == desired
        and all(node_ready(node) for node in members)
        and all(node.get("nodeSetID") == pool.get("currentNodeSetID") for node in members)
        and (desired == 0 or condition(pool, "provisioned"))
    )


def cluster_ready(
    cluster: dict[str, Any], plane: dict[str, Any], pools: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> bool:
    config, status = plane.get("config", {}), plane.get("status", {})
    desired = config.get("desiredCount")
    return (
        condition(cluster, "provisioned")
        and inactive(cluster, "deleting")
        and inactive(cluster, "provisioning")
        and inactive(cluster, "updateFailed")
        and not cluster.get("conditions", {}).get("updateInProgress")
        and type(desired) is int
        and desired > 0
        and status.get("nodeCount") == desired
        and status.get("readyNodeCount") == desired
        and status.get("kubernetesVersion") == cluster.get("controlPlaneKubernetesVersion")
        and inactive(status, "controlPlaneUpgrading")
        and inactive(status, "cpNodesRecycling")
        and len([node for node in nodes if node.get("controlPlane")]) == desired
        and all(node_ready(node) for node in nodes if node.get("controlPlane"))
        and all(pool_ready(pool, nodes) for pool in pools)
    )


def declarative_ready(resource: dict[str, Any]) -> bool:
    generation = resource.get("metadata", {}).get("generation")
    if type(generation) is not int:
        return False
    status = resource.get("status", {})
    conditions = {c.get("type"): c for c in status.get("conditions", [])}
    for name in ("ControlPlaneReady", "ClusterReconciled", "NodePoolsReconciled"):
        value = conditions.get(name, {})
        if value.get("status") != "True" or value.get("observedGeneration") != generation:
            return False
    desired = resource.get("spec", {}).get("nodePools", [])
    observed = {pool.get("name"): pool for pool in status.get("nodePools", [])}
    for pool in desired:
        actual = observed.get(pool.get("name"), {})
        if any(actual.get(field) != pool.get("replicas") for field in ("replicas", "readyReplicas", "updatedReplicas")):
            return False
        if not any(
            c.get("type") == "Ready" and c.get("status") == "True" and c.get("observedGeneration") == generation
            for c in actual.get("conditions", [])
        ):
            return False
    return set(observed) == {pool.get("name") for pool in desired}
