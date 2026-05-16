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
    ingress_paused: bool
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
    # CI / VCS provenance (#166)
    ci_actor_kind: str
    commit_sha: str
    branch: str
    ci_run_url: str
    ci_provider: str


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
        ingress_paused=env.ingress_paused,
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
        ci_actor_kind=d.ci_actor_kind or "",
        commit_sha=d.commit_sha or "",
        branch=d.branch or "",
        ci_run_url=d.ci_run_url or "",
        ci_provider=d.ci_provider or "",
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


@strawberry.type(name="AstroliftScheduledJobRun")
class ScheduledJobRunType:
    id: GUID
    registered_app_slug: str
    environment_name: str
    workload_slug: str
    k8s_job_name: str
    status: str
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    duration_seconds: int | None
    exit_code: int | None
    log_excerpt: str
    created_at: dt.datetime


@strawberry.type(name="AstroliftCommandRun")
class CommandRunType:
    id: GUID
    registered_app_slug: str
    workload_slug: str | None
    invoked_by_username: str | None
    command: JSON
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    exit_code: int | None
    log_excerpt: str
    created_at: dt.datetime


def scheduled_job_run_to_type(r) -> ScheduledJobRunType:
    return ScheduledJobRunType(
        id=GUID(str(r.guid)),
        registered_app_slug=r.workload.registered_app.slug,
        environment_name=r.app_environment.name,
        workload_slug=r.workload.slug,
        k8s_job_name=r.k8s_job_name or "",
        status=r.status,
        started_at=r.started_at,
        ended_at=r.ended_at,
        duration_seconds=r.duration_seconds,
        exit_code=r.exit_code,
        log_excerpt=r.log_excerpt or "",
        created_at=r.created_at,
    )


def command_run_to_type(r) -> CommandRunType:
    return CommandRunType(
        id=GUID(str(r.guid)),
        registered_app_slug=r.registered_app.slug,
        workload_slug=r.workload.slug if r.workload_id else None,
        invoked_by_username=r.invoked_by.username if r.invoked_by_id else None,
        command=r.command or [],
        started_at=r.started_at,
        ended_at=r.ended_at,
        exit_code=r.exit_code,
        log_excerpt=r.log_excerpt or "",
        created_at=r.created_at,
    )


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


@strawberry.type(name="AstroliftAppDomainRequiredRecord")
class AppDomainRequiredRecordType:
    """One DNS record the operator must add (or that the platform
    will create on their behalf when the parent zone is managed).

    ``propagated`` flips True once the validation workflow sees the
    record at the authoritative nameserver. The UI renders a per-row
    status badge.
    """

    kind: str
    name: str
    value: str
    ttl: int
    propagated: bool
    last_checked_at: str | None
    message: str


@strawberry.type(name="AstroliftAppDomain")
class AppDomainType:
    """A custom domain bound to a registered app. ``cert_state``
    reflects the current ACME / cloud-cert validation state."""

    id: GUID
    hostname: str
    cert_state: str
    """pending | validating | validated | failed (mirrors
    ``CustomDomain.ValidationStatus``)."""

    validation_method: str
    validation_token: str
    last_checked_at: dt.datetime | None
    is_active: bool
    registered_app_slug: str
    created_at: dt.datetime

    # ---- handshake surface (#397) ---------------------------------

    txt_challenge_token: str
    expected_cname_target: str
    required_dns_records: list[AppDomainRequiredRecordType]
    is_platform_managed_zone: bool
    last_validation_error: str

    # ---- cert lifecycle (#397 unhappy-path surface) ----------------
    # cert_state above is the DNS validation status. The fields below
    # are the certificate's own state — separate axis so the UI can
    # render "DNS validated but cert issuance failed" distinctly.

    certificate_state: str
    """not_requested | issuing | active | failed | byo. Mirrors
    ``CustomDomain.CertificateState``. Surfaces post-validation cert
    progression so the UI can render a spinner while ACME / cloud-cert
    is provisioning and an actionable error + BYO upload affordance
    when issuance fails."""

    last_certificate_error: str
    byo_certificate_uploaded_at: dt.datetime | None


def app_domain_to_type(d) -> AppDomainType:
    return AppDomainType(
        id=GUID(str(d.guid)),
        hostname=d.hostname,
        cert_state=d.validation_status,
        validation_method=d.validation_method,
        validation_token=d.validation_value or "",
        last_checked_at=d.last_checked_at or d.updated_at,
        is_active=d.is_active,
        registered_app_slug=d.registered_app.slug,
        created_at=d.created_at,
        txt_challenge_token=d.txt_challenge_token or "",
        expected_cname_target=d.expected_cname_target or "",
        required_dns_records=[
            AppDomainRequiredRecordType(
                kind=r.get("kind", ""),
                name=r.get("name", ""),
                value=r.get("value", ""),
                ttl=int(r.get("ttl", 300)),
                propagated=bool(r.get("propagated", False)),
                last_checked_at=r.get("last_checked_at"),
                message=r.get("message", ""),
            )
            for r in (d.required_dns_records or [])
        ],
        is_platform_managed_zone=bool(d.is_platform_managed_zone),
        last_validation_error=d.last_validation_error or "",
        certificate_state=d.certificate_state or "not_requested",
        last_certificate_error=d.last_certificate_error or "",
        byo_certificate_uploaded_at=d.byo_certificate_uploaded_at,
    )


@strawberry.type(name="AstroliftDeployToken")
class DeployTokenType:
    """Bearer credential bound to one app, scoped narrowly. The
    plaintext token is only returned on creation/rotation — at any
    other time, only ``last_4`` is exposed."""

    id: GUID
    name: str
    last_4: str
    scopes: list[str]
    expires_at: dt.datetime | None
    last_used_at: dt.datetime | None
    is_revoked: bool
    last_rotated_at: dt.datetime | None
    registered_app_slug: str
    created_at: dt.datetime


def deploy_token_to_type(t) -> DeployTokenType:
    return DeployTokenType(
        id=GUID(str(t.guid)),
        name=t.name,
        last_4=t.token_last_4 or "",
        scopes=list(t.scopes or []),
        expires_at=t.expires_at,
        last_used_at=t.last_used_at,
        is_revoked=t.is_revoked,
        last_rotated_at=t.last_rotated_at,
        registered_app_slug=t.registered_app.slug,
        created_at=t.created_at,
    )


# ---- Pod state (runtime cluster) ------------------------------------


@strawberry.type(name="AstroliftContainerStatus")
class ContainerStatusType:
    """One container slot's runtime state.

    ``state`` is ``running`` | ``waiting`` | ``terminated`` |
    ``unknown``. ``waiting_reason`` / ``terminated_reason`` carry
    the K8s reason string — that's where actionable diagnostics
    live (``CrashLoopBackOff``, ``ImagePullBackOff``, …)."""

    name: str
    ready: bool
    restarts: int
    image: str
    state: str
    waiting_reason: str
    terminated_reason: str


@strawberry.type(name="AstroliftAppPod")
class AppPodType:
    """A single pod from the runtime cluster, namespace-scoped to
    the app.

    ``status`` is the rolled-up surface status — the worst of the
    raw ``phase`` and any container waiting/terminated reasons.
    ``phase`` is preserved separately so the UI can show both when
    they diverge (e.g. phase=Running but a sidecar is in
    CrashLoopBackOff)."""

    name: str
    workload: str
    status: str
    phase: str
    ready: bool
    restarts: int
    age: dt.datetime | None
    node: str
    container_statuses: list[ContainerStatusType]


def container_status_to_type(c) -> ContainerStatusType:
    return ContainerStatusType(
        name=c.name,
        ready=c.ready,
        restarts=c.restart_count,
        image=c.image,
        state=c.state,
        waiting_reason=c.waiting_reason,
        terminated_reason=c.terminated_reason,
    )


def pod_info_to_type(p) -> AppPodType:
    return AppPodType(
        name=p.name,
        workload=p.workload,
        status=p.status,
        phase=p.phase,
        ready=p.ready,
        restarts=p.restarts,
        age=p.age,
        node=p.node,
        container_statuses=[container_status_to_type(c) for c in p.container_statuses],
    )


@strawberry.type(name="AstroliftAppLogLine")
class AppLogLineType:
    """One log line from a pod/container in the runtime cluster.

    ``stream`` is ``stdout`` | ``stderr``. The default
    kubernetes-client API doesn't separate the two on the wire — all
    container output arrives interleaved — so the streaming backend
    flags everything as ``stdout`` unless an alternative backend
    (test fakes, future per-container stderr support) emits
    otherwise."""

    pod_name: str
    container: str
    timestamp: dt.datetime
    message: str
    stream: str


# ---------------------------------------------------------------------------
# #377 — observability cards (DNS / TLS / Workload identity)
# ---------------------------------------------------------------------------
#
# These types mirror the SDK dataclasses
# (``_sdk.dns.DnsRecord``, ``_sdk.tls.CertificateInfo``,
# ``_sdk.identity.IdentityBinding``) — see ``astrolift-providers/_sdk/``.
# The strawberry layer auto-converts snake_case → camelCase so the FE
# sees ``propagationStatus`` etc. ``not_after`` is a string (ISO-8601)
# rather than a datetime so the schema doesn't depend on the cloud
# returning timezone-aware values.


@strawberry.type(name="AstroliftAppDnsRecord")
class AppDnsRecordType:
    name: str
    type: str
    value: str
    ttl: int
    propagation_status: str


@strawberry.type(name="AstroliftAppCertificate")
class AppCertificateType:
    id: str
    hostname: str
    issuer: str
    not_after: str
    days_until_expiry: int
    renewal_status: str


@strawberry.type(name="AstroliftAppIdentityBinding")
class AppIdentityBindingType:
    kind: str
    role_arn_or_principal: str
    trust_policy_summary: str
    last_used_at: str | None


def dns_record_to_type(r) -> AppDnsRecordType:
    return AppDnsRecordType(
        name=r.name,
        type=r.type,
        value=r.value,
        ttl=r.ttl,
        propagation_status=r.propagation_status,
    )


def certificate_info_to_type(c) -> AppCertificateType:
    return AppCertificateType(
        id=c.id,
        hostname=c.hostname,
        issuer=c.issuer,
        not_after=c.not_after,
        days_until_expiry=c.days_until_expiry,
        renewal_status=c.renewal_status,
    )


def identity_binding_to_type(b) -> AppIdentityBindingType:
    return AppIdentityBindingType(
        kind=b.kind,
        role_arn_or_principal=b.role_arn_or_principal,
        trust_policy_summary=b.trust_policy_summary,
        last_used_at=b.last_used_at,
    )
