"""Read-only Nitro prerequisite evidence; configuration readiness is not attestation."""

from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

WORKSPACE_BYTES = 8 * 1024**3
AVAILABLE_BYTES = 1024**3
NODE_LABEL = "eks.amazonaws.com/nitro-sandbox"


def check(code: str, state: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {"code": code, "state": state, "message": message, "evidence": evidence}


def state_of(checks: list[dict[str, Any]]) -> str:
    states = {row["state"] for row in checks}
    return "not_ready" if "not_ready" in states else "unknown" if "unknown" in states else "ready"


def effective_role_matches(identity: dict[str, Any], role_arn: str) -> bool:
    # IAM role names are unique within an account, including roles with paths.
    role = re.fullmatch(r"arn:aws:iam::([0-9]{12}):role/(?:[^/]+/)*([^/]+)", role_arn)
    assumed = re.fullmatch(r"arn:aws:sts::([0-9]{12}):assumed-role/([^/]+)/[^/]+", identity.get("Arn", ""))
    return bool(role and assumed and identity.get("Account") == role[1] == assumed[1] and role[2] == assumed[2])


def workspace_readiness(path: str, *, mountinfo: str | None = None, stat: Any = None) -> dict[str, Any]:
    try:
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_dir() or not os.access(resolved, os.W_OK | os.X_OK):
            return check("workspace", "not_ready", "The confidential workspace must be writable.")
        text = Path("/proc/self/mountinfo").read_text() if mountinfo is None else mountinfo
        candidates = []
        for line in text.splitlines():
            before, after = line.split(" - ", 1)
            mount = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), before.split()[4])
            if resolved == Path(mount) or Path(mount) in resolved.parents:
                candidates.append((len(mount), after.split()[0]))
        filesystem = max(candidates)[1] if candidates else "unknown"
        capacity = os.statvfs(resolved) if stat is None else stat
        total, available = capacity.f_blocks * capacity.f_frsize, capacity.f_bavail * capacity.f_frsize
        ready = filesystem == "tmpfs" and total >= WORKSPACE_BYTES and available >= AVAILABLE_BYTES
        return check(
            "workspace",
            "ready" if ready else "not_ready",
            "Confidential preparation requires an 8 GiB memory workspace with at least 1 GiB available.",
            filesystem=filesystem,
            total_bytes=total,
            available_bytes=available,
            required_bytes=WORKSPACE_BYTES,
            required_available_bytes=AVAILABLE_BYTES,
        )
    except (OSError, ValueError, IndexError):
        return check("workspace", "unknown", "Memory workspace filesystem and capacity could not be verified.")


def platform_checks(
    cluster: dict[str, Any],
    runtimes: list[dict[str, Any]] | None,
    nodes: list[dict[str, Any]] | None,
    instances: list[dict[str, Any]] | None,
    mode: str,
) -> list[dict[str, Any]]:
    version = str(cluster.get("version", ""))
    match = re.fullmatch(r"1\.([0-9]+)(?:\.[0-9]+)?", version)
    checks = [
        check(
            "eks_version",
            "ready" if match and int(match[1]) >= 36 else "not_ready" if match else "unknown",
            "Nitro Sandboxes require EKS 1.36 or later.",
            observed_version=version,
        ),
        check(
            "cluster_active",
            "ready" if cluster.get("status") == "ACTIVE" else "not_ready" if cluster.get("status") else "unknown",
            "The EKS cluster must be active.",
            observed_status=cluster.get("status", "unknown"),
        ),
    ]
    runtime = mode + "-sandbox"
    configured = runtimes is not None and any(
        row.get("metadata", {}).get("name") == runtime
        and row.get("handler") == runtime
        and not row.get("metadata", {}).get("deletionTimestamp")
        for row in runtimes
    )
    checks.append(
        check(
            "runtime_class",
            "ready" if configured else "unknown" if runtimes is None else "not_ready",
            "The matching AWS RuntimeClass must be installed and readable.",
            runtime_class=runtime,
        )
    )
    automatic = cluster.get("computeConfig", {}).get("enabled", False if cluster.get("arn") else None)
    if automatic is True:
        checks.append(
            check(
                "node_preparation",
                "ready",
                "EKS Auto Mode provisions compatible sandbox nodes.",
                path="auto_mode",
            )
        )
    elif automatic is None:
        checks.append(
            check(
                "node_preparation",
                "unknown",
                "The EKS compute preparation path could not be verified.",
                path="unverified",
            )
        )
    else:
        prepared = []
        by_id = {row.get("InstanceId"): row for row in instances or []}
        for node in nodes or []:
            status, spec, meta = node.get("status", {}), node.get("spec", {}), node.get("metadata", {})
            info = status.get("nodeInfo", {})
            kernel = re.match(r"([0-9]+)\.([0-9]+)\.([0-9]+)", info.get("kernelVersion", ""))
            instance_id = str(spec.get("providerID", "")).rsplit("/", 1)[-1]
            instance = by_id.get(instance_id, {})
            try:
                slots = int(status.get("allocatable", {}).get(NODE_LABEL, "0"))
            except (TypeError, ValueError):
                slots = 0
            if (
                meta.get("labels", {}).get(NODE_LABEL) == "true"
                and not meta.get("deletionTimestamp")
                and not spec.get("unschedulable")
                and slots > 0
                and any(
                    row.get("type") == "Ready" and row.get("status") == "True" for row in status.get("conditions", [])
                )
                and kernel
                and tuple(map(int, kernel.groups())) >= (6, 18, 51)
                and re.match(r"containerd://([2-9]|[1-9][0-9]+)\.", info.get("containerRuntimeVersion", ""))
                and instance.get("EnclaveOptions", {}).get("Enabled") is True
                and instance.get("State", {}).get("Name") == "running"
            ):
                prepared.append(instance_id)
        checks.append(
            check(
                "node_preparation",
                "ready" if prepared else "unknown" if nodes is None or instances is None else "not_ready",
                "Prepared nodes need Ready status, sandbox slots, valid kernel/containerd and enclave-enabled EC2.",
                path="prepared_nodes",
                prepared_instance_ids=prepared,
            )
        )
    return checks


def collect_readiness(
    driver: Any, cluster: str, namespace: str, config: Any = None, preparation: dict[str, Any] | None = None
) -> dict[str, Any]:
    """``preparation`` replaces this process's workspace check when an on-demand task prepares images."""
    import hashlib
    import json
    import shutil
    import tempfile
    from datetime import UTC, datetime

    from aws.nitro_confidential import verified_tools
    from aws.nitro_sandboxes import RUNTIME_KIND
    from aws.nitro_skopeo import verify_keyprovider

    source: dict[str, Any] = {
        "region": driver._config.region,
        "eks_cluster_name": driver._config.cluster_name,
        "namespace": namespace,
        "configuration_scope": "current_process",
    }
    cluster_info: dict[str, Any] = {}
    try:
        cluster_info = driver._nitro_client("eks").describe_cluster(name=driver._config.cluster_name)["cluster"]
        source["cluster_arn"] = cluster_info["arn"]
    except Exception:
        pass
    runtimes: list[dict[str, Any]] | None = None
    nodes: list[dict[str, Any]] | None = None
    instances: list[dict[str, Any]] | None = None
    with suppress(Exception):
        runtimes = driver._k8s(cluster).list(kind=RUNTIME_KIND, request_timeout=(3, 5))
    if cluster_info.get("computeConfig", {}).get("enabled") is not True:
        try:
            nodes = driver._k8s(cluster).list(kind="v1/Node", request_timeout=(3, 5))
            ids = sorted(
                {
                    str(row.get("spec", {}).get("providerID", "")).rsplit("/", 1)[-1]
                    for row in nodes
                    if row.get("metadata", {}).get("labels", {}).get(NODE_LABEL) == "true"
                }
            )
            ids = [value for value in ids if re.fullmatch(r"i-[0-9a-f]+", value)]
            instances = []
            # EC2 permits at most 1000 explicit IDs per request; keep probe reads bounded.
            if len(ids) > 100:
                raise ValueError("manual sandbox inventory exceeds the bounded probe")
            if ids:
                result = driver._nitro_client("ec2").describe_instances(InstanceIds=ids)
                if result.get("NextToken"):
                    raise ValueError("manual sandbox inventory is incomplete")
                instances = [item for reservation in result["Reservations"] for item in reservation["Instances"]]
        except Exception:
            instances = None
    modes: dict[str, dict[str, Any]] = {
        mode: {"checks": platform_checks(cluster_info, runtimes, nodes, instances, mode)}
        for mode in ("isolated", "confidential")
    }
    extra = modes["confidential"]["checks"]
    if config is None:
        extra.append(
            check(
                "operator_configuration",
                "not_ready",
                "Configure this cluster on both API and workers before enabling confidential tasks.",
            )
        )
    else:
        try:
            config.validate()
            source.update(
                configuration_fingerprint=hashlib.sha256(
                    json.dumps(
                        {key: str(value) if isinstance(value, Path) else value for key, value in vars(config).items()},
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
                key_admin_arn=config.key_admin_arn,
                runtime_role_arn=config.runtime_role_arn,
                service_account=config.service_account,
            )
            extra.append(
                check(
                    "operator_configuration",
                    "ready",
                    "This process has valid per-cluster operator configuration.",
                )
            )
            extra.append(preparation or workspace_readiness(config.temporary_root))
            try:
                if (
                    not Path(config.skopeo).is_file()
                    or not os.access(config.skopeo, os.X_OK)
                    or not shutil.which("openssl")
                ):
                    raise ValueError("runtime tools unavailable")
                with tempfile.TemporaryDirectory(prefix="nitro-readiness-", dir=config.temporary_root) as work:
                    verified_tools(config.runtime_directory, Path(work), dict(os.environ))
                    source["tool_manifest_sha256"] = hashlib.sha256(
                        (config.runtime_directory / "SHA256SUM").read_bytes()
                    ).hexdigest()
                extra.append(
                    check(
                        "signed_tools",
                        "ready",
                        "Installed generator and keyprovider match the AWS-signed checksums.",
                    )
                )
            except Exception:
                extra.append(
                    check(
                        "signed_tools",
                        "not_ready",
                        "Install the verified AWS tools, skopeo and openssl on API and workers.",
                    )
                )
            try:
                verify_keyprovider(config.skopeo, config.temporary_root)
                extra.append(
                    check(
                        "image_encryption",
                        "ready",
                        "Installed Skopeo completed an offline keyprovider encryption/decryption check.",
                    )
                )
            except Exception:
                extra.append(
                    check(
                        "image_encryption",
                        "not_ready",
                        "Install Skopeo with keyprovider encryption/decryption support on API and workers.",
                    )
                )
            try:
                identity = driver._nitro_client("sts").get_caller_identity()
                source.update(account_id=identity["Account"], effective_principal_arn=identity["Arn"])
                match = effective_role_matches(identity, config.key_admin_arn)
                account, region = config.registry.split(".")[0], config.registry.split(".")[3]
                cluster_arn = cluster_info.get("arn", "").split(":")
                match = match and identity["Account"] == account and region == driver._config.region
                identity_state = (
                    "unknown"
                    if match and len(cluster_arn) != 6
                    else ("ready" if match and cluster_arn[4] == account and cluster_arn[3] == region else "not_ready")
                )
                extra.append(
                    check(
                        "effective_identity",
                        identity_state,
                        "The effective provider role and EKS/ECR account/region must match the key administrator.",
                    )
                )
            except Exception:
                extra.append(
                    check(
                        "effective_identity",
                        "unknown",
                        "The effective provider identity could not be verified.",
                    )
                )
            try:
                response = driver._nitro_client("eks").list_pod_identity_associations(
                    clusterName=driver._config.cluster_name,
                    namespace=namespace,
                    serviceAccount=config.service_account,
                )
                rows = response.get("associations", [])
                valid = len(rows) == 1 and not response.get("nextToken")
                if valid:
                    association = driver._nitro_client("eks").describe_pod_identity_association(
                        clusterName=driver._config.cluster_name, associationId=rows[0]["associationId"]
                    )["association"]
                    valid = (
                        association.get("roleArn") == config.runtime_role_arn
                        and association.get("namespace") == namespace
                        and association.get("serviceAccount") == config.service_account
                        and association.get("clusterName") == driver._config.cluster_name
                    )
                extra.append(
                    check(
                        "pod_identity",
                        "ready" if valid else "not_ready",
                        "The agent namespace/service account needs the exact runtime-role association.",
                    )
                )
            except Exception:
                extra.append(
                    check(
                        "pod_identity",
                        "unknown",
                        "The target namespace Pod Identity association could not be read.",
                    )
                )
            try:
                response = driver._nitro_client("ecr").describe_repositories(
                    registryId=config.registry.split(".")[0],
                    repositoryNames=[config.registry.split("/", 1)[1]],
                )
                rows = response["repositories"]
                valid = (
                    len(rows) == 1
                    and rows[0].get("repositoryUri") == config.registry
                    and rows[0].get("imageTagMutability") == "IMMUTABLE"
                )
                extra.append(
                    check(
                        "registry",
                        "ready" if valid else "not_ready",
                        "The dedicated private run repository must exist with immutable tags.",
                    )
                )
            except Exception:
                extra.append(check("registry", "unknown", "The private run repository could not be verified."))
        except (ValueError, TypeError, AttributeError):
            extra.append(
                check(
                    "operator_configuration",
                    "not_ready",
                    "This cluster's operator configuration is invalid on this process.",
                )
            )
    for row in modes.values():
        row["state"] = state_of(row["checks"])
    return {
        "version": 1,
        "checked_at": datetime.now(UTC).isoformat(),
        "source": source,
        "modes": modes,
        "attestation_verified": False,
    }
