"""Per-cluster task opt-in and fresh Nitro prerequisite review; no provisioning."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.conf import settings

MODES = {"isolated", "confidential"}


def task_modes(cluster):
    configured = getattr(cluster, "provider_config", None)
    block = configured.get("nitro_sandboxes", {}) if isinstance(configured, dict) else {}
    values = block.get("task_modes", []) if isinstance(block, dict) else []
    return (
        sorted(set(values))
        if isinstance(values, list) and all(isinstance(value, str) and value in MODES for value in values)
        else []
    )


def confidential_config(cluster):
    from aws.nitro_confidential import ConfidentialConfig

    try:
        values = dict(json.loads(settings.NITRO_CONFIDENTIAL_TASKS_CONFIG)[str(cluster.guid)])
        values["runtime_directory"] = Path(values.get("runtime_directory", "/opt/astrolift/nitro"))
        result = ConfidentialConfig(**values)
        result.validate()
        return result
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError("This cluster has no valid operator confidential-task configuration") from exc


def namespace_for_cluster(cluster):
    from _sdk.k8s_naming import agent_namespace

    from core.tenancy import get_current_tenant

    organization = cluster.organization
    if organization is None:
        tenant = get_current_tenant()
        from astrolift_identity.models import Organization

        organization = (
            Organization.objects.filter(pk=tenant.organization_id).first() if tenant is not None else None
        )
    if organization is None:
        raise ValueError("Select the task organization before reviewing a shared cluster")
    return agent_namespace(organization.slug)


def review(cluster, *, namespace=None):
    from django.utils import timezone

    from core.cluster_management import _driver_for_cluster

    namespace = namespace or namespace_for_cluster(cluster)
    try:
        config = confidential_config(cluster)
    except ValueError:
        config = None
    if cluster.provider_plugin.slug != "aws":
        report = {"checked_at": timezone.now().isoformat(), "source": {"namespace": namespace}, "modes": {}}
    else:
        try:
            driver = _driver_for_cluster(cluster)
            probe = getattr(driver, "probe_nitro_readiness", None)
            if not callable(probe):
                raise ValueError("provider readiness unavailable")
            from astrolift_clusters import nitro_preparation

            preparation = nitro_preparation.readiness()
            report = probe(
                cluster.slug, namespace, config, **({"preparation": preparation} if preparation else {})
            )
        except Exception:
            report = {
                "checked_at": timezone.now().isoformat(),
                "source": {"namespace": namespace},
                "modes": {
                    mode: {
                        "state": "unknown",
                        "checks": [
                            {
                                "code": "provider_probe",
                                "state": "unknown",
                                "message": "The provider readiness probe could not complete.",
                                "evidence": {},
                            }
                        ],
                    }
                    for mode in sorted(MODES)
                },
            }
    source = report["source"]
    report.update(
        cluster_id=str(cluster.guid),
        version=cluster.version,
        enabled_modes=task_modes(cluster),
        source_token=source_token(cluster, namespace, source),
        namespace=namespace,
    )
    if not cluster.is_active or cluster.lifecycle in {"decommissioning", "decommissioned"}:
        for value in report["modes"].values():
            value["state"] = "not_ready"
            value["checks"].append(
                {
                    "code": "cluster_available",
                    "state": "not_ready",
                    "message": "The cluster must be active and not retiring.",
                    "evidence": {},
                }
            )
    return report


def source_token(cluster, namespace, source):
    binding = {
        "cluster_id": str(cluster.guid),
        "version": cluster.version,
        "namespace": namespace,
        "cluster_arn": source.get("cluster_arn"),
        "configuration_fingerprint": source.get("configuration_fingerprint"),
        "key_admin_arn": source.get("key_admin_arn"),
        "runtime_role_arn": source.get("runtime_role_arn"),
        "tool_manifest_sha256": source.get("tool_manifest_sha256"),
    }
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


def require_task_mode(cluster, mode, namespace):
    from astrolift_dispatch.pod_hardening import AgentRuntimeClassError

    if mode not in task_modes(cluster):
        raise AgentRuntimeClassError(
            f"Nitro {mode} tasks are not enabled for this cluster; review Nitro task readiness in cluster settings"
        )
    report = review(cluster, namespace=namespace)
    value = report["modes"].get(mode, {})
    if value.get("state") != "ready":
        codes = ", ".join(row["code"] for row in value.get("checks", []) if row["state"] != "ready")
        raise AgentRuntimeClassError(
            f"Nitro {mode} task prerequisites are not ready on this process: {codes or 'unsupported provider'}"
        )
