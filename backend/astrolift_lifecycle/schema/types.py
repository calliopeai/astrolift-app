"""GraphQL types for AppEnvironment, Deployment, DeploymentLog,
PreviewEnvironment, IngressRule, CustomDomain."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftAppEnvironment")
class AppEnvironmentType:
    id: GUID
    name: str
    url: str
    deploys_paused: bool
    required_approvals: int
    registered_app_slug: str
    cluster_slug: str | None
    domain_zone: str | None
    created_at: dt.datetime


@strawberry.type(name="AstroliftDeployment")
class DeploymentType:
    id: GUID
    registered_app_slug: str
    environment_name: str
    workload_slug: str | None
    trigger_kind: str
    status: str
    image_tag: str
    image_digest: str
    cluster_revision: str
    approvals_required: int
    approvals_received: int
    started_at: dt.datetime | None
    succeeded_at: dt.datetime | None
    failed_at: dt.datetime | None
    ended_at: dt.datetime | None
    duration_seconds: int | None
    created_at: dt.datetime


@strawberry.type(name="AstroliftDeploymentLogEntry")
class DeploymentLogEntryType:
    id: GUID
    deployment_id: str
    status: str
    message: str
    detail: JSON
    occurred_at: dt.datetime


@strawberry.type(name="AstroliftPreviewEnvironment")
class PreviewEnvironmentType:
    id: GUID
    registered_app_slug: str
    pr_number: int
    branch: str
    commit_sha: str
    status: str
    hostname: str
    namespace: str
    last_deployed_at: dt.datetime | None
    torn_down_at: dt.datetime | None


def app_env_to_type(env) -> AppEnvironmentType:
    return AppEnvironmentType(
        id=GUID(str(env.guid)),
        name=env.name,
        url=env.url or "",
        deploys_paused=env.deploys_paused,
        required_approvals=env.required_approvals,
        registered_app_slug=env.registered_app.slug,
        cluster_slug=env.tenant_cluster.slug if env.tenant_cluster_id else None,
        domain_zone=env.managed_domain.zone if env.managed_domain_id else None,
        created_at=env.created_at,
    )


def deployment_to_type(d) -> DeploymentType:
    return DeploymentType(
        id=GUID(str(d.guid)),
        registered_app_slug=d.registered_app.slug,
        environment_name=d.app_environment.name,
        workload_slug=d.workload.slug if d.workload_id else None,
        trigger_kind=d.trigger_kind,
        status=d.status,
        image_tag=d.image_tag or "",
        image_digest=d.image_digest or "",
        cluster_revision=d.cluster_revision or "",
        approvals_required=d.approvals_required,
        approvals_received=d.approvals_received,
        started_at=d.started_at,
        succeeded_at=d.succeeded_at,
        failed_at=d.failed_at,
        ended_at=d.ended_at,
        duration_seconds=d.duration_seconds,
        created_at=d.created_at,
    )


def deployment_log_to_type(entry) -> DeploymentLogEntryType:
    return DeploymentLogEntryType(
        id=GUID(str(entry.guid)),
        deployment_id=str(entry.deployment_id),
        status=entry.status,
        message=entry.message or "",
        detail=entry.detail or {},
        occurred_at=entry.occurred_at,
    )


@strawberry.type(name="AstroliftDeploymentMetrics")
class DeploymentMetricsType:
    window_days: int
    total: int
    succeeded: int
    failed: int
    rolled_back: int
    in_flight: int
    success_rate: float  # 0.0–1.0; -1 when total==0
    mean_duration_seconds: float | None
    p95_duration_seconds: float | None


@strawberry.type(name="AstroliftAppHealthSummary")
class AppHealthSummaryType:
    app_slug: str
    app_name: str
    environment_count: int
    latest_deployment_status: str | None
    latest_image_tag: str
    last_deployed_at: dt.datetime | None
    has_recent_failure: bool


def preview_to_type(p) -> PreviewEnvironmentType:
    return PreviewEnvironmentType(
        id=GUID(str(p.guid)),
        registered_app_slug=p.registered_app.slug,
        pr_number=p.pr_number,
        branch=p.branch,
        commit_sha=p.commit_sha or "",
        status=p.status,
        hostname=p.hostname,
        namespace=p.namespace,
        last_deployed_at=p.last_deployed_at,
        torn_down_at=p.torn_down_at,
    )
