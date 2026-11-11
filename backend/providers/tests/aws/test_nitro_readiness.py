"""Readiness requires observed prerequisites, never inferred attestation."""

import json
import shutil
from copy import deepcopy
from types import SimpleNamespace

import pytest

from aws.nitro_readiness import NODE_LABEL, effective_role_matches, platform_checks, state_of, workspace_readiness

RUNTIMES = [
    {"metadata": {"name": mode + "-sandbox"}, "handler": mode + "-sandbox"} for mode in ("isolated", "confidential")
]


@pytest.mark.parametrize("automatic", [True, False])
def test_old_eks_version_cannot_be_bypassed_by_runtime_or_manual_nodes(automatic):
    values = platform_checks(
        {"version": "1.34", "status": "ACTIVE", "computeConfig": {"enabled": automatic}}, RUNTIMES, [], [], "isolated"
    )
    assert state_of(values) == "not_ready"
    assert next(row for row in values if row["code"] == "eks_version")["state"] == "not_ready"


def test_auto_mode_ready_to_provision_without_existing_nodes():
    assert (
        state_of(
            platform_checks(
                {"version": "1.36", "status": "ACTIVE", "computeConfig": {"enabled": True}},
                RUNTIMES,
                None,
                None,
                "isolated",
            )
        )
        == "ready"
    )
    assert (
        state_of(
            platform_checks(
                {"version": "1.37", "status": "ACTIVE", "computeConfig": {"enabled": True}},
                None,
                None,
                None,
                "isolated",
            )
        )
        == "unknown"
    )


def test_manual_nodes_require_all_observed_preparation():
    node = {
        "metadata": {"labels": {NODE_LABEL: "true"}},
        "spec": {"providerID": "aws:///us-west-2a/i-123"},
        "status": {
            "allocatable": {NODE_LABEL: "8"},
            "conditions": [{"type": "Ready", "status": "True"}],
            "nodeInfo": {"kernelVersion": "6.18.51-120", "containerRuntimeVersion": "containerd://2.0.4"},
        },
    }
    instance = {"InstanceId": "i-123", "EnclaveOptions": {"Enabled": True}, "State": {"Name": "running"}}
    cluster = {"version": "1.36", "status": "ACTIVE", "computeConfig": {"enabled": False}}
    assert state_of(platform_checks(cluster, RUNTIMES, [node], [instance], "confidential")) == "ready"
    for value in ("slots", "kernel", "containerd", "ready", "enclave"):
        changed, parent = deepcopy(node), deepcopy(instance)
        if value == "slots":
            changed["status"]["allocatable"][NODE_LABEL] = "0"
        elif value == "kernel":
            changed["status"]["nodeInfo"]["kernelVersion"] = "6.18.50"
        elif value == "containerd":
            changed["status"]["nodeInfo"]["containerRuntimeVersion"] = "containerd://1.7"
        elif value == "ready":
            changed["status"]["conditions"][0]["status"] = "False"
        else:
            parent["EnclaveOptions"]["Enabled"] = False
        assert state_of(platform_checks(cluster, RUNTIMES, [changed], [parent], "confidential")) == "not_ready"


@pytest.mark.parametrize(
    "principal,account,expected",
    [
        ("arn:aws:sts::123456789012:assumed-role/controller/api", "123456789012", True),
        ("arn:aws:sts::123456789012:assumed-role/controller/worker", "123456789012", True),
        ("arn:aws:sts::123456789012:assumed-role/other/api", "123456789012", False),
        ("arn:aws:iam::123456789012:user/controller", "123456789012", False),
        ("arn:aws:sts::999999999999:assumed-role/controller/api", "999999999999", False),
    ],
)
def test_effective_provider_role_must_match_not_just_account(principal, account, expected):
    assert (
        effective_role_matches({"Account": account, "Arn": principal}, "arn:aws:iam::123456789012:role/path/controller")
        is expected
    )


@pytest.mark.parametrize(
    "filesystem,total,free,expected",
    [
        ("tmpfs", 8 * 1024**3, 2 * 1024**3, "ready"),
        ("tmpfs", 64 * 1024**2, 64 * 1024**2, "not_ready"),
        ("ext4", 16 * 1024**3, 16 * 1024**3, "not_ready"),
        ("tmpfs", 8 * 1024**3, 100 * 1024**2, "not_ready"),
    ],
)
def test_memory_workspace_checks_filesystem_declared_budget_and_free_space(tmp_path, filesystem, total, free, expected):
    mount = f"1 0 0:1 / {tmp_path} rw - {filesystem} tmpfs rw"
    stat = SimpleNamespace(f_blocks=total // 4096, f_bavail=free // 4096, f_frsize=4096)
    result = workspace_readiness(str(tmp_path), mountinfo=mount, stat=stat)
    assert result["state"] == expected
    assert result["evidence"]["total_bytes"] == total


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    from aws import nitro_readiness
    from aws.nitro_confidential import ConfidentialConfig

    (tmp_path / "SHA256SUM").write_text("verified synthetic manifest")
    skopeo = tmp_path / "skopeo"
    skopeo.write_text("synthetic executable")
    skopeo.chmod(0o700)
    config = ConfidentialConfig(
        registry="123456789012.dkr.ecr.us-west-2.amazonaws.com/runs",
        key_admin_arn="arn:aws:iam::123456789012:role/controller",
        runtime_role_arn="arn:aws:iam::123456789012:role/guest",
        runtime_directory=tmp_path,
        service_account="confidential-runner",
        temporary_root=str(tmp_path),
        skopeo=str(skopeo),
    )
    state = {
        "version": "1.36",
        "role": "controller",
        "namespace": "astrolift-agents-org",
        "mutable": False,
        "runtimes": deepcopy(RUNTIMES),
        "calls": [],
    }

    def record(operation, **kw):
        state["calls"].append((operation, kw))

    def describe(**kw):
        record("describe_cluster", **kw)
        if state.get("deny_eks"):
            raise PermissionError("PRIVATE-DIAGNOSTIC-MARKER")
        return {
            "cluster": {
                "arn": "arn:aws:eks:us-west-2:123456789012:cluster/test",
                "version": state["version"],
                "status": "ACTIVE",
                "computeConfig": {"enabled": True},
            }
        }

    def associations(**kw):
        record("list_pod_identity_associations", **kw)
        assert kw["namespace"] == "astrolift-agents-org"
        return {"associations": [{"associationId": "owned"}]}

    def association(**kw):
        record("describe_pod_identity_association", **kw)
        return {
            "association": {
                "roleArn": config.runtime_role_arn,
                "namespace": state["namespace"],
                "serviceAccount": config.service_account,
                "clusterName": "test",
            }
        }

    def identity():
        record("get_caller_identity")
        return {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/" + state["role"] + "/api"}

    def repositories(**kw):
        record("describe_repositories", **kw)
        return {
            "repositories": [
                {"repositoryUri": config.registry, "imageTagMutability": "MUTABLE" if state["mutable"] else "IMMUTABLE"}
            ]
        }

    def listing(**kw):
        record("kube_list", **kw)
        assert kw["request_timeout"] == (3, 5)
        return state["runtimes"]

    clients = {
        "eks": SimpleNamespace(
            describe_cluster=describe,
            list_pod_identity_associations=associations,
            describe_pod_identity_association=association,
        ),
        "sts": SimpleNamespace(get_caller_identity=identity),
        "ecr": SimpleNamespace(describe_repositories=repositories),
    }
    driver = SimpleNamespace(
        _config=SimpleNamespace(region="us-west-2", cluster_name="test"),
        _nitro_client=lambda service: clients[service],
        _k8s=lambda cluster: SimpleNamespace(list=listing),
    )
    monkeypatch.setattr("aws.nitro_confidential.verified_tools", lambda *args: ())
    monkeypatch.setattr("aws.nitro_skopeo.verify_keyprovider", lambda *args: None)
    monkeypatch.setattr(
        nitro_readiness, "workspace_readiness", lambda path: nitro_readiness.check("workspace", "ready", "memory")
    )
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/openssl")
    return driver, config, state


def test_confidential_readiness_records_safe_source_and_only_read_operations(inventory):
    from aws.nitro_readiness import collect_readiness

    driver, config, state = inventory
    result = collect_readiness(driver, "alias", "astrolift-agents-org", config)
    assert result["modes"]["confidential"]["state"] == "ready", result
    assert result["attestation_verified"] is False
    assert result["source"]["effective_principal_arn"].endswith("controller/api")
    assert len(result["source"]["configuration_fingerprint"]) == 64
    assert all(not name.startswith(("create", "put", "apply", "delete")) for name, _ in state["calls"])


@pytest.mark.parametrize(
    "change", ["old_version", "foreign_role", "foreign_namespace", "mutable_registry", "missing_runtime"]
)
def test_any_missing_prerequisite_refuses_readiness(inventory, change):
    from aws.nitro_readiness import collect_readiness

    driver, config, state = inventory
    if change == "old_version":
        state["version"] = "1.34"
    elif change == "foreign_role":
        state["role"] = "foreign"
    elif change == "foreign_namespace":
        state["namespace"] = "control-plane"
    elif change == "mutable_registry":
        state["mutable"] = True
    else:
        state["runtimes"] = []
    assert (
        collect_readiness(driver, "alias", "astrolift-agents-org", config)["modes"]["confidential"]["state"]
        == "not_ready"
    )


def test_unreadable_eks_is_unknown_and_diagnostics_do_not_escape(inventory):
    from aws.nitro_readiness import collect_readiness

    driver, config, state = inventory
    state["deny_eks"] = True
    result = collect_readiness(driver, "alias", "astrolift-agents-org", config)
    assert result["modes"]["confidential"]["state"] == "unknown"
    assert "PRIVATE-DIAGNOSTIC-MARKER" not in json.dumps(result)


def test_missing_keyprovider_support_refuses_confidential_readiness(inventory, monkeypatch):
    from aws.nitro_readiness import collect_readiness

    def unsupported(*args):
        raise ValueError("PRIVATE-DIAGNOSTIC-MARKER")

    monkeypatch.setattr("aws.nitro_skopeo.verify_keyprovider", unsupported)
    driver, config, _ = inventory
    result = collect_readiness(driver, "alias", "astrolift-agents-org", config)
    assert result["modes"]["confidential"]["state"] == "not_ready"
    assert result["modes"]["isolated"]["state"] == "ready"
    check = next(row for row in result["modes"]["confidential"]["checks"] if row["code"] == "image_encryption")
    assert check["state"] == "not_ready"
    assert "PRIVATE-DIAGNOSTIC-MARKER" not in json.dumps(result)
