"""Reviewed collector admission. No generic prerequisite or external IAM mutation."""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from astrolift_clusters import agent_install
from astrolift_clusters.models import ClusterLogCollectorOperation, ProviderPlugin, TenantCluster
from astrolift_identity.api_tokens import get_current_api_token
from core.cluster_credentials import credential_for_cluster
from core.current_credential import current_dispatch_credential
from core.permissions import Permission, PermissionDenied
from core.tenancy import get_current_tenant

DURATION_SECONDS = 900
RETENTION_DAYS = {1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653}
TERMINAL = {"refused", "activated"}


class CollectorError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _policy():
    from aws._cloudwatch_collector import NAMESPACE, PIN, PROFILE, SERVICE_ACCOUNT

    image = getattr(settings, "ASTROLIFT_COLLECTOR_PROBE_IMAGE", "")
    if (
        not isinstance(image, str)
        or len(image) > 256
        or not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}", image)
    ):
        raise CollectorError(
            "PROBE_IMAGE_NOT_CONFIGURED", "Configure the approved digest-pinned collector probe image"
        )
    return {
        "revision": "collector-operation-v1",
        "namespace": NAMESPACE,
        "service_account": SERVICE_ACCOUNT,
        "chart": PIN,
        "profile": hashlib.sha256((PROFILE / "values-aws.yaml").read_bytes()).hexdigest(),
        "probe_image": image,
        "duration_seconds": DURATION_SECONDS,
        "chart_archive_url": "https://github.com/fluent/helm-charts/releases/download/"
        + PIN["name"]
        + "-"
        + PIN["version"]
        + "/"
        + PIN["name"]
        + "-"
        + PIN["version"]
        + ".tgz",
    }


def _binding(cluster):
    config = cluster.provider_config or {}
    return {key: config.get(key) for key in ("log_driver", "log_config")}


def _candidate(cluster):
    from aws.cloudwatch_collector import CollectorStage

    declared = credential_for_cluster(cluster).declared_account
    if not re.fullmatch(r"[0-9]{12}", declared or ""):
        raise CollectorError(
            "ACCOUNT_REQUIRED", "A declared AWS account is required for collector preparation"
        )
    if cluster.cloud_account_id and declared and cluster.cloud_account_id != declared:
        raise CollectorError("ACCOUNT_MISMATCH", "Verified and registered AWS accounts disagree")
    account = cluster.cloud_account_id or declared
    if not re.fullmatch(r"[0-9]{12}", account or ""):
        raise CollectorError(
            "ACCOUNT_REQUIRED", "A declared AWS account is required for collector preparation"
        )
    region = cluster.region or (cluster.provider_config or {}).get("region", "")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]", region):
        raise CollectorError("REGION_REQUIRED", "An explicit AWS region is required")
    partition = (
        "aws-cn" if region.startswith("cn-") else "aws-us-gov" if region.startswith("us-gov-") else "aws"
    )
    group = f"/astrolift/clusters/{cluster.guid}/pods"
    name = (cluster.provider_config or {}).get("cluster_name") or cluster.slug
    return CollectorStage(
        cluster_guid=str(cluster.guid),
        cluster_arn=f"arn:{partition}:eks:{region}:{account}:cluster/{name}",
        region=region,
        log_group=group,
        log_group_arn=f"arn:{partition}:logs:{region}:{account}:log-group:{group}",
        irsa_role_arn=f"arn:{partition}:iam::{account}:role/astrolift/astrolift-{cluster.guid}-fluent-bit",
        oidc_issuer="",
    )


def _support(cluster, plugin, retention_days):
    if type(retention_days) is not int or retention_days not in RETENTION_DAYS:
        raise CollectorError("VALIDATION", "retentionDays must be a supported CloudWatch retention period")
    if not cluster.is_active or cluster.deleted_at or cluster.lifecycle != TenantCluster.Lifecycle.MANAGED:
        raise CollectorError("CLUSTER_UNAVAILABLE", "A live managed cluster is required")
    if not plugin.is_enabled or plugin.deleted_at or plugin.slug != "aws":
        raise CollectorError(
            "PROVIDER_UNSUPPORTED", "The collector supports an enabled AWS EKS provider only"
        )
    if cluster.auth_method != TenantCluster.AuthMethod.EXEC_PLUGIN:
        raise CollectorError("TRANSPORT_UNSUPPORTED", "Registered EKS exec-plugin authentication is required")
    from core.install_restrictions import cluster_scope_refusal

    # A ClusterRole and a node DaemonSet: beyond the minimal RBAC contract.
    if refusal := cluster_scope_refusal(cluster):
        raise CollectorError("WITHHELD", refusal)
    try:
        credential = credential_for_cluster(cluster)
        if credential.cloud != "aws" or credential.mode.value not in {"ambient", "aws_assume_role"}:
            raise ValueError
        candidate = _candidate(cluster)
    except CollectorError:
        raise
    except Exception:
        raise CollectorError(
            "CREDENTIAL_UNSUPPORTED", "The registered AWS credential cannot prepare this collector"
        ) from None
    binding = _binding(cluster)
    if binding["log_driver"] or binding["log_config"]:
        expected = candidate.candidate_reader_config()
        if binding != {"log_driver": expected["log_driver"], "log_config": expected["log_config"]}:
            raise CollectorError(
                "EXTERNAL_BACKEND_CONFIGURED", "Keep the existing external historical-log backend"
            )
        receipt = ClusterLogCollectorOperation.objects.filter(
            tenant_cluster=cluster, status="activated"
        ).first()
        if receipt is None or not hmac.compare_digest(
            receipt.activation_source_digest, _source(cluster, plugin, receipt.retention_days)
        ):
            raise CollectorError(
                "EXISTING_BINDING_UNPROVEN",
                "The existing owned reader binding has no collector activation receipt",
            )
    return candidate


def _source(cluster, plugin, retention_days):
    return agent_install._digest(
        [
            "collector-source-v1",
            str(cluster.guid),
            cluster.version,
            cluster.updated_at,
            cluster.organization_id,
            cluster.provider_plugin_id,
            cluster.slug,
            cluster.region,
            cluster.endpoint,
            cluster.ca_cert,
            cluster.auth_method,
            cluster.auth_config,
            cluster.provider_config,
            cluster.cloud_account_id,
            cluster.cloud_account_verified_at,
            cluster.is_active,
            cluster.deleted_at,
            cluster.lifecycle,
            str(plugin.guid),
            plugin.version,
            plugin.updated_at,
            plugin.slug,
            plugin.is_enabled,
            plugin.deleted_at,
            retention_days,
            _policy(),
        ]
    )


def _review_source(cluster, plugin, retention_days):
    candidate = _candidate(cluster)
    return agent_install._digest(
        [
            "collector-review-v1",
            str(cluster.guid),
            cluster.version,
            cluster.updated_at,
            str(plugin.guid),
            plugin.version,
            plugin.updated_at,
            retention_days,
            _policy(),
            {"driver": "cloudwatch_logs", "region": candidate.region, "group": candidate.log_group},
            bool(_binding(cluster)["log_driver"] or _binding(cluster)["log_config"]),
        ]
    )


def review_collector(cluster_id, retention_days=30):
    with transaction.atomic(), current_dispatch_credential(Permission.CLUSTER_MANAGE):
        cluster, _ = agent_install._visible_cluster(cluster_id, lock=True)
        plugin = ProviderPlugin.all_objects.select_for_update().get(pk=cluster.provider_plugin_id)
        try:
            candidate = _support(cluster, plugin, retention_days)
            policy = _policy()
        except CollectorError as exc:
            return {
                "cluster": cluster,
                "supported": False,
                "refusal_code": exc.code,
                "message": str(exc),
                "source": "",
                "policy": {},
                "reader_policy": {},
            }
        return {
            "cluster": cluster,
            "supported": True,
            "refusal_code": "",
            "message": "",
            "source": _review_source(cluster, plugin, retention_days),
            "policy": policy,
            "reader_policy": candidate.reader_policy(),
        }


def _locked(row_id):
    candidate = ClusterLogCollectorOperation.objects.filter(pk=row_id).values("tenant_cluster_id").first()
    if candidate is None:
        raise CollectorError("NOT_FOUND", "Collector operation not found")
    cluster = TenantCluster.all_objects.select_for_update().get(pk=candidate["tenant_cluster_id"])
    plugin = ProviderPlugin.all_objects.select_for_update().get(pk=cluster.provider_plugin_id)
    row = ClusterLogCollectorOperation.objects.select_for_update().get(pk=row_id)
    return row, cluster, plugin


def _authorize(row, cluster, plugin):
    cluster.refresh_from_db()
    plugin.refresh_from_db()
    with agent_install._actor(row) as user:
        agent_install._caller_gate(cluster, user)
        if cluster.organization_id is None and row.credential_ceiling["token_id"] is not None:
            if "admin" not in row.credential_ceiling["scopes"]:
                raise CollectorError(
                    "PERMISSION_DENIED", "Original credential does not cover shared cluster installation"
                )
    expected = row.activation_source_digest if row.status == "activated" else row.source_digest
    if not hmac.compare_digest(_source(cluster, plugin, row.retention_days), expected):
        raise CollectorError(
            "SOURCE_CHANGED", "Reviewed collector source changed; the existing reader remains active"
        )
    if row.status != "activated" and timezone.now() >= row.deadline:
        raise CollectorError(
            "DEADLINE_EXCEEDED", "Collector operation deadline passed; existing reader remains active"
        )
    if (
        row.deleted_at
        or not cluster.is_active
        or cluster.deleted_at
        or not plugin.is_enabled
        or plugin.deleted_at
    ):
        raise CollectorError("SOURCE_CHANGED", "Collector operation source is unavailable")


def reserve_collector(*, cluster_id, request_id, expected_version, expected_source, retention_days=30):
    try:
        request_uuid = UUID(request_id)
        if str(request_uuid) != request_id or not request_uuid.int:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise CollectorError("VALIDATION", "requestId must be a canonical nonzero UUID") from None
    tenant = get_current_tenant()
    with transaction.atomic(), current_dispatch_credential(Permission.CLUSTER_MANAGE):
        cluster, user = agent_install._visible_cluster(cluster_id, lock=True)
        token = get_current_api_token()
        requested = [
            str(cluster.guid),
            expected_version,
            expected_source,
            retention_days,
            token.pk if token else None,
            str(token.guid) if token else None,
        ]
        request_digest = agent_install._digest(requested)
        existing = (
            ClusterLogCollectorOperation.all_objects.select_for_update()
            .filter(organization_id=tenant.organization_id, actor=user, request_id=request_uuid)
            .first()
        )
        if existing:
            if existing.deleted_at or existing.request_digest != request_digest:
                raise CollectorError(
                    "REQUEST_CHANGED", "requestId belongs to a different or deleted collector operation"
                )
            if not existing.terminal:
                transaction.on_commit(lambda: dispatch_collector(existing.pk))
            return existing
        plugin = ProviderPlugin.all_objects.select_for_update().get(pk=cluster.provider_plugin_id)
        _support(cluster, plugin, retention_days)
        policy = _policy()
        source = _source(cluster, plugin, retention_days)
        if (
            cluster.version != expected_version
            or not isinstance(expected_source, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_source)
            or not hmac.compare_digest(_review_source(cluster, plugin, retention_days), expected_source)
        ):
            raise CollectorError(
                "SOURCE_CHANGED", "Reviewed cluster, retention or collector policy changed; review again"
            )
        pending = (
            ClusterLogCollectorOperation.objects.select_for_update()
            .filter(tenant_cluster=cluster)
            .exclude(status__in=TERMINAL)
        )
        for row in pending:
            try:
                _authorize(row, cluster, plugin)
            except (CollectorError, agent_install.AgentInstallError, PermissionDenied):
                row.status = "refused"
                row.error_code = "SOURCE_CHANGED"
                row.error_message = (
                    "Original collector authority or source was withdrawn; existing reader remains active"
                )
                row.save()
        if (
            ClusterLogCollectorOperation.objects.filter(tenant_cluster=cluster)
            .exclude(status__in=TERMINAL)
            .exists()
        ):
            raise CollectorError("OPERATION_PENDING", "Resume the original pending collector request")
        if ClusterLogCollectorOperation.objects.filter(tenant_cluster=cluster, status="activated").exists():
            raise CollectorError(
                "ALREADY_ACTIVATED",
                "Inspect the existing collector activation receipt; this is not a health check",
            )
        now = timezone.now()
        try:
            with transaction.atomic():
                row = ClusterLogCollectorOperation.objects.create(
                    tenant_cluster=cluster,
                    organization_id=tenant.organization_id,
                    actor=user,
                    request_id=request_uuid,
                    request_digest=request_digest,
                    expected_version=expected_version,
                    expected_source=expected_source,
                    source_digest=source,
                    binding_digest=agent_install._digest(_binding(cluster)),
                    credential_ceiling=agent_install._ceiling(),
                    retention_days=retention_days,
                    probe_image=policy["probe_image"],
                    policy_digest=agent_install._digest(policy),
                    query_since=now - timedelta(seconds=30),
                    query_until=now + timedelta(seconds=DURATION_SECONDS),
                    deadline=now + timedelta(seconds=DURATION_SECONDS),
                    created_by=user,
                    updated_by=user,
                )
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
            if constraint not in {"collector_actor_request_unique", "collector_one_live_operation"}:
                raise
            raise CollectorError(
                "OPERATION_PENDING", "An original collector request is already reserved"
            ) from None
        transaction.on_commit(lambda: dispatch_collector(row.pk))
        return row


def read_collector(operation_id):
    tenant = get_current_tenant()
    if tenant is None:
        raise CollectorError("PERMISSION_DENIED", "An authenticated organization actor is required")
    row = ClusterLogCollectorOperation.objects.filter(
        guid=str(operation_id), organization_id=tenant.organization_id, actor_id=tenant.actor_user_id
    ).first()
    if row is None:
        raise CollectorError("NOT_FOUND", "Collector operation not found")
    with transaction.atomic(), current_dispatch_credential(Permission.CLUSTER_MANAGE):
        cluster, _ = agent_install._visible_cluster(row.tenant_cluster.guid, lock=True)
        with agent_install._actor(row) as user:
            agent_install._caller_gate(cluster, user)
    return row


def dispatch_collector(row_id):
    from astrolift_workflows.client import (
        describe_workflow_instance,
        recover_workflow_once,
        start_workflow_once,
    )

    with transaction.atomic():
        row, cluster, plugin = _locked(row_id)
        if row.terminal:
            return row
        try:
            _authorize(row, cluster, plugin)
        except (CollectorError, agent_install.AgentInstallError, PermissionDenied):
            row.status = "refused"
            row.error_code = "SOURCE_CHANGED"
            row.error_message = (
                "Original collector authority or reviewed source changed; existing reader remains active"
            )
            row.save()
            return row
        args = [row.pk, row.generation]
        try:
            handle = recover_workflow_once(
                "InstallClusterLogCollectorWorkflow", args, workflow_id=row.workflow_id
            )
            if handle is not None:
                if row.workflow_run_id and row.workflow_run_id != handle.run_id:
                    raise RuntimeError
                description = describe_workflow_instance(row.workflow_id, run_id=handle.run_id)
                if (
                    description is None
                    or description["run_id"] != handle.run_id
                    or description["workflow_type"] != "InstallClusterLogCollectorWorkflow"
                ):
                    raise RuntimeError
                if description["status"] == "RUNNING":
                    row.workflow_run_id = handle.run_id
                    row.save()
                    return row
                if not description.get("closed_at") or description["status"] not in {
                    "COMPLETED",
                    "FAILED",
                    "TIMED_OUT",
                    "CANCELED",
                    "TERMINATED",
                }:
                    raise RuntimeError
                row.generation += 1
                row.workflow_run_id = ""
                row.status = "queued"
                row.save()
                args = [row.pk, row.generation]
            elif row.workflow_run_id:
                raise RuntimeError
            handle = start_workflow_once(
                "InstallClusterLogCollectorWorkflow", args, workflow_id=row.workflow_id
            )
            row.workflow_run_id = handle.run_id
            row.error_code = ""
            row.error_message = ""
        except Exception:
            row.status = "uncertain"
            row.error_code = "DISPATCH_UNCERTAIN"
            row.error_message = "Collector dispatch could not be confirmed; resume the original request"
        row.save()
        return row
