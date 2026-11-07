import copy

import pytest

from nvke.lifecycle import cluster_ready, contains, declarative_ready, pool_ready


def healthy():
    node = {
        "id": "node-a",
        "nodePoolID": "pool-a",
        "nodeSetID": "set-a",
        "controlPlane": True,
        "conditions": {name: {"status": "True"} for name in ("provisioned", "kubernetesReady", "nodeAgentReady")},
    }
    pool = {
        "id": "pool-a",
        "currentNodeSetID": "set-a",
        "desiredNodeCount": 1,
        "conditions": {"provisioned": {"status": "True"}},
    }
    cluster = {"controlPlaneKubernetesVersion": "1.36.3", "conditions": {"provisioned": {"status": "True"}}}
    plane = {
        "config": {"desiredCount": 1},
        "status": {"nodeCount": 1, "readyNodeCount": 1, "kubernetesVersion": "1.36.3"},
    }
    return cluster, plane, [pool], [node]


def test_ready_requires_running_nodes_not_only_provisioned_cluster():
    args = healthy()
    assert cluster_ready(*args)
    args[3][0]["conditions"]["kubernetesReady"]["status"] = "Unknown"
    assert not cluster_ready(*args)


@pytest.mark.parametrize(
    "condition", ["deleting", "deleteRequested", "markedForRecycling", "controlPlaneRolloutRequired", "nodeAgentReady"]
)
def test_pending_replacement_or_unhealthy_agent_prevents_ready(condition):
    args = healthy()
    args[3][0]["conditions"][condition] = {"status": "False" if condition == "nodeAgentReady" else "True"}
    assert not cluster_ready(*args)


def test_control_plane_runtime_upgrade_and_missing_node_inventory_prevent_ready():
    cluster, plane, pools, nodes = healthy()
    plane["status"]["controlPlaneUpgrading"] = {"status": "True"}
    assert not cluster_ready(cluster, plane, pools, nodes)
    plane["status"].pop("controlPlaneUpgrading")
    assert not cluster_ready(cluster, plane, pools, [])
    plane["status"]["kubernetesVersion"] = "1.35.0"
    assert not cluster_ready(cluster, plane, pools, nodes)


def test_scale_to_zero_requires_known_zero_and_no_surviving_nodes():
    _, _, pools, nodes = healthy()
    pools[0]["desiredNodeCount"] = 0
    assert not pool_ready(pools[0], nodes)
    assert pool_ready(pools[0], [])
    pools[0].pop("desiredNodeCount")
    assert not pool_ready(pools[0], [])


def test_declarative_conditions_from_an_older_generation_do_not_establish_readiness():
    value = {
        "metadata": {"generation": 2},
        "spec": {},
        "status": {
            "conditions": [
                {"type": name, "status": "True", "observedGeneration": 1}
                for name in ("ControlPlaneReady", "ClusterReconciled", "NodePoolsReconciled")
            ]
        },
    }
    assert not declarative_ready(value)
    fresh = copy.deepcopy(value)
    for condition in fresh["status"]["conditions"]:
        condition["observedGeneration"] = 2
    assert declarative_ready(fresh)


def test_schema_state_comparison_never_equates_boolean_and_integer():
    assert contains({"desired": 1, "defaulted": "extra"}, {"desired": 1})
    assert not contains({"desired": True}, {"desired": 1})


@pytest.mark.parametrize("status", ["True", "Unknown", "", None])
def test_unknown_or_active_recycling_cannot_prove_idle(status):
    args = healthy()
    args[3][0]["conditions"]["markedForRecycling"] = {"status": status}
    assert not cluster_ready(*args)
