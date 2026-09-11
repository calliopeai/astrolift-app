"""
Operator mutations for the cluster fleet.

Gated by ``cluster.register`` / ``cluster.update`` /
``cluster.unregister`` / ``cluster.manage`` /
``provider_plugin.configure``. The register/update/unregister
mutations are in-database registrations that the provider drivers
consume on the next reconcile / probe pass. The manage mutations
(``bringClusterIntoManagement`` / ``refreshClusterManagement``)
kick a Temporal workflow that talks to the real cluster.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets
from collections.abc import Callable, Mapping

import strawberry
from django.db import transaction
from django.db.models import Q
from strawberry.types import Info

from astrolift_clusters.ingress_modes import IngressMode
from astrolift_clusters.models import (
    ClusterBootstrapRun,
    ManagedDomain,
    ProviderPlugin,
    ProviderPluginConfig,
    TenantCluster,
)
from astrolift_clusters.schema.types import (
    ManagedDomainType,
    TenantClusterType,
    cluster_to_type,
    domain_to_type,
)
from astrolift_graphql import GUID, MutationErrorType, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from astrolift_operations import observability_profile
from astrolift_workflows.client import signal_workflow, start_workflow
from astrolift_workflows.inputs import (
    Actor,
    BringClusterIntoManagementInput,
    DecommissionClusterInput,
    DeprovisionManagedDomainInput,
    InstallClusterPrereqsInput,
    ProvisionManagedDomainInput,
)
from core.decorators import tenant_scoped
from core.events import Event
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

logger = logging.getLogger(__name__)

JSON = strawberry.scalars.JSON


# The per-cluster observability driver bundle rides in ``provider_config``
# under these key pairs; the read paths that consume it
# (``core.cluster_log_query``, ``astrolift_observability.trace_client``)
# log-and-skip anything malformed. So a typo'd key persists fine and only
# surfaces weeks later as an empty Logs tab with nothing pointing at the
# cause. Refuse it at the write boundary instead.
_OBSERVABILITY_DRIVER_KEYS: tuple[tuple[str, str, Callable[..., object]], ...] = (
    ("log_driver", "log_config", observability_profile.validate_log_driver_config),
    (
        "metrics_driver",
        "metrics_config",
        observability_profile.validate_metrics_driver_config,
    ),
    ("trace_driver", "trace_config", observability_profile.validate_trace_driver_config),
)


def _observability_config_issues(provider_config: object) -> list[str]:
    """Every observability misconfiguration in ``provider_config``.

    An absent driver key is not an issue — a cluster with no aggregator
    wired is the normal case and the read paths already render the "live
    tail only" empty state for it. All issues are collected rather than
    raising on the first so the operator fixes one round-trip's worth at
    a time instead of playing whack-a-mole.
    """
    if not isinstance(provider_config, Mapping):
        return []
    issues: list[str] = []
    for driver_key, config_key, validate in _OBSERVABILITY_DRIVER_KEYS:
        driver = provider_config.get(driver_key)
        if driver is None or (isinstance(driver, str) and not driver.strip()):
            continue
        try:
            # Lowercased to match how the read paths normalize the kind,
            # so validation can't pass a driver they will later skip.
            validate(
                driver=str(driver).strip().lower(),
                config=provider_config.get(config_key) or {},
            )
        except observability_profile.ObservabilityProfileError as exc:
            issues.append(f"providerConfig.{driver_key}: {exc}")
    return issues


def _observability_failure(issues: list[str]) -> MutationResultType[None]:
    """Multi-error envelope — one ``errors`` entry per misconfiguration."""
    return MutationResultType(
        ok=False,
        data=None,
        errors=[
            MutationErrorType(
                code=ErrorCode.VALIDATION.value,
                message=issue,
                field="providerConfig",
            )
            for issue in issues
        ],
    )


def _decommission_workflow_id(cluster_guid: str) -> str:
    return f"DecommissionClusterWorkflow-{cluster_guid}"


def _bring_workflow_id(cluster_guid: str) -> str:
    """Workflow id pattern — re-firing the same cluster joins the
    existing run rather than spawning a parallel one."""
    return f"BringClusterIntoManagement-{cluster_guid}"


def _first_dns_cluster():
    """The TenantCluster whose DnsDriver hosts managed-domain operations.

    Managed domains are org-level, not cluster-level, so zone workflows
    pick a cluster only as the vehicle for cloud credentials: the first
    active cluster visible to the tenant.
    """
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    return (
        TenantCluster.objects.filter(
            Q(organization_id=org_id) | Q(organization_id__isnull=True),
            deleted_at__isnull=True,
        )
        .order_by("pk")
        .first()
    )


def _actor_from_request(info: Info) -> Actor:
    """Same shape as ``astrolift_lifecycle.schema.mutations``. Inlined
    rather than imported to keep the lifecycle/cluster apps free of
    cross-app schema imports."""
    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) if request else None
    if user is not None and getattr(user, "is_authenticated", False):
        return Actor(
            kind="user",
            user_id=user.pk,
            display=getattr(user, "username", "") or "",
        )
    tenant = get_current_tenant()
    if tenant and tenant.actor_user_id:
        return Actor(kind="user", user_id=tenant.actor_user_id, display="")
    return Actor(kind="system", display="system")


def _kick_bring_into_management(
    *,
    cluster: TenantCluster,
    actor: Actor,
    force_preflight: bool,
) -> None:
    """Flip the row to ``managing`` (so the UI shows the spinner
    immediately) + enqueue the workflow.

    The workflow's first activity re-asserts ``managing`` to cover
    the worker-crash case where the row was already flipped by this
    helper; that's fine because ``mark_managing`` is idempotent.
    """
    cluster.lifecycle = TenantCluster.Lifecycle.MANAGING.value
    cluster.last_management_error = ""
    cluster.save(
        update_fields=[
            "lifecycle",
            "last_management_error",
            "updated_at",
            "version",
        ]
    )
    start_workflow(
        "BringClusterIntoManagementWorkflow",
        args=[
            BringClusterIntoManagementInput(
                cluster_id=cluster.pk,
                actor=actor,
                force_preflight=force_preflight,
            )
        ],
        workflow_id=_bring_workflow_id(str(cluster.guid)),
    )


def _kick_decommission_cluster(
    *,
    cluster: TenantCluster,
    actor: Actor,
    delete_cloud_infra: bool = False,
) -> None:
    """Flip the row to ``decommissioning`` + enqueue the workflow.

    The workflow re-asserts the state on entry, so this synchronous
    flip is just the UI-spinner shortcut (matching the bring-into-mgmt
    pattern). The drained-check activity runs first and can flip the
    row to ``error`` if app envs are still bound — which is why this
    helper does NOT save until the workflow itself has confirmed the
    drained state. Operators see the same spinner-then-error UX that
    bring-into-management uses.

    ``delete_cloud_infra`` is the operator's opt-in for the destructive
    half of decommission — when True the workflow additionally calls
    the driver's ``teardown_cluster`` after RBAC removal.
    """
    cluster.lifecycle = TenantCluster.Lifecycle.DECOMMISSIONING.value
    cluster.last_management_error = ""
    cluster.save(
        update_fields=[
            "lifecycle",
            "last_management_error",
            "updated_at",
            "version",
        ]
    )
    start_workflow(
        "DecommissionClusterWorkflow",
        args=[
            DecommissionClusterInput(
                cluster_id=cluster.pk,
                actor=actor,
                delete_cloud_infra=delete_cloud_infra,
            ),
        ],
        workflow_id=_decommission_workflow_id(str(cluster.guid)),
    )


@strawberry.input
class RegisterTenantClusterInput:
    slug: str
    name: str
    provider_plugin_slug: str
    auth_method: str  # kubeconfig | exec_plugin | service_account_token
    region: str | None = None
    endpoint: str | None = None
    ca_cert: str | None = None
    auth_config: JSON | None = None
    provider_config: JSON | None = None
    ingress_class: str | None = None
    organization_scoped: bool = True


@strawberry.input
class UpdateTenantClusterInput:
    id: GUID
    is_active: bool | None = None
    region: str | None = None
    endpoint: str | None = None
    ingress_class: str | None = None
    # None means "leave it": flipping a cluster to shared_ingress re-groups
    # load balancers that are already serving traffic, so it has to be an
    # explicit act rather than a side effect of any other update (#1537).
    ingress_mode: str | None = None
    alb_auth_config: JSON | None = strawberry.UNSET
    # Write-only: the read side comes back redacted on TenantClusterType
    # because this carries the oauth2-proxy cookie secret (#1616).
    oidc_auth_config: JSON | None = strawberry.UNSET


@strawberry.input
class ReconcileClusterIngressesInput:
    cluster_id: GUID


@strawberry.input
class UnregisterTenantClusterInput:
    id: GUID


@strawberry.input
class CreateManagedDomainInput:
    zone: str
    dns_driver: str
    default_for: str = "none"  # tenant_apps | preview_envs | both | none
    is_wildcard_managed: bool = False
    dns_config: JSON | None = None
    organization_scoped: bool = True


@strawberry.input
class UpdateManagedDomainInput:
    id: GUID
    default_for: str | None = None
    is_wildcard_managed: bool | None = None
    dns_config: JSON | None = None


@strawberry.input
class SoftDeleteManagedDomainInput:
    id: GUID


@strawberry.input
class ConfigureProviderPluginInput:
    plugin_slug: str
    config: JSON
    organization_scoped: bool = True


@strawberry.input
class IssueClusterAgentKeyInput:
    """``issueClusterAgentKey`` mutation input (#808).

    Issues (or rotates) the scoped key the in-cluster keep-alive agent
    signs its heartbeat with. ``cluster_id`` is the cluster the key is
    bound to. ``interval_seconds`` lets the operator tune the pulse
    cadence; omitted leaves the existing cadence (default 30s)."""

    cluster_id: GUID
    interval_seconds: int | None = None


@strawberry.type
class _ClusterAgentKeyIssuedPayload:
    """Return shape for ``issueClusterAgentKey``.

    ``agent_key`` is the raw scoped key — surfaced EXACTLY ONCE, here,
    at issuance; only its SHA-256 persists on the cluster row. The
    operator pastes it into the agent's Secret. ``rotated`` is True when
    this replaced a previously-issued key (so the UI can warn that the
    old agent will start 401ing)."""

    cluster_id: GUID
    agent_key: str
    interval_seconds: int
    heartbeat_url: str
    rotated: bool


@strawberry.input
class BringClusterIntoManagementInputType:
    cluster_id: GUID


@strawberry.input
class DecommissionClusterInputType:
    cluster_id: GUID
    # Explicit opt-in for the destructive half of decommission. False
    # (default) only lifts the platform RBAC and leaves the underlying
    # EKS/GKE/AKS cluster running. True ALSO calls the driver's
    # teardown_cluster to delete the cloud-managed cluster.
    # UI surfaces this as a separate "danger zone" checkbox.
    delete_cloud_infra: bool = False


@strawberry.input
class RefreshClusterManagementInputType:
    cluster_id: GUID
    force_preflight: bool = False


@strawberry.input
class RecordClusterBootstrapRunInput:
    """CLI-submitted bootstrap outcome (#319).

    The CLI calls this after ``astro cluster bootstrap`` settles
    (success or failure). ``cluster_slug`` keys the row to the right
    cluster — the CLI knows the slug it was invoked against and never
    holds an integer PK. ``status`` is the post-run summary
    (``succeeded`` or ``failed``); ``installed_releases`` is a free-
    form list of ``{name, version}`` (and optional ``status``) entries
    captured from the helm transcript. ``host_info`` is a free-form
    JSON blob — OS, arch, kubectl/helm versions — kept for
    debuggability without forcing the CLI version that wrote it to be
    in lock-step with the control plane."""

    cluster_slug: str
    status: str
    chart_version: str
    installed_releases: JSON
    cli_version: str
    host_info: JSON
    error_message: str | None = None
    started_at: dt.datetime
    ended_at: dt.datetime


@strawberry.type
class _BootstrapRunRecordedPayload:
    """Minimal return shape — the CLI only needs the id back so it can
    reference the run in subsequent log lines / future re-uploads."""

    id: GUID


@strawberry.input
class BootstrapOptionOverride:
    """One operator-set option value to flow into the install workflow."""

    component_key: str
    option_key: str
    value: str


@strawberry.input
class InstallClusterPrereqsInputType:
    """``installClusterPrereqs`` mutation input (#66).

    The operator submits the cluster + which components they chose
    + the per-option overrides as a flat triple list (component_key,
    option_key, value). The mutation re-shapes the triples into the
    nested dict the workflow consumes.
    """

    cluster_id: GUID
    selected_components: list[str]
    option_overrides: list[BootstrapOptionOverride] = strawberry.field(
        default_factory=list,
    )


@strawberry.input
class DeployClusterAgentInput:
    """``deployClusterAgent`` mutation input (#873).

    ``cluster_id`` is the cluster the keep-alive agent Deployment is
    applied to. The agent reads its credentials from the pre-created
    ``astrolift-agent`` Secret (created from the snippet surfaced by
    ``issueClusterAgentKey``), so no key material rides on this input."""

    cluster_id: GUID


@strawberry.type
class ReconcileClusterIngressesResult:
    """Outcome of re-applying the ALB auth gate across a cluster's
    managed-subdomain Ingresses (#851).

    ``reconciled_count`` is the number of Ingresses patched;
    ``skipped_count`` is the number of bound app environments that had
    no managed-subdomain Ingress to patch (not yet deployed, or removed
    out-of-band); ``errors`` carries per-namespace / per-Ingress failure
    strings so a single unreachable namespace doesn't fail the whole
    operation."""

    reconciled_count: int
    skipped_count: int
    errors: list[str]


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


@strawberry.type
class ProvisionManagedDomainPayload:
    zone: str
    workflow_id: str
    nameservers: list[str]
    message: str


@strawberry.type
class RevalidateManagedDomainPayload:
    zone: str
    signaled: bool
    message: str


@strawberry.type
class ReissueManagedDomainCertPayload:
    zone: str
    signaled: bool
    message: str


@strawberry.type
class _ProviderPluginConfigPayload:
    plugin_slug: str
    organization_scoped: bool


def _has_auth_gate(ingress_class: str, cluster) -> bool:
    """Whether apps on this cluster render behind an auth gate.

    Each class reads a different config and ignores the other: ALB uses the
    Cognito annotations from ``alb_auth_config`` (core/app_deploy.py), every
    other class uses the oauth2-proxy annotations from ``oidc_auth_config``,
    which the renderer emits only when the config carries all three of
    discovery_url, client_id and auth_proxy_host.
    """
    if ingress_class == "alb":
        return bool(cluster.alb_auth_config)
    config = cluster.oidc_auth_config or {}
    return all(config.get(k) for k in ("discovery_url", "client_id", "auth_proxy_host"))


@strawberry.type
class ClustersMutation:
    @strawberry.field
    @mutation_audit(action="cluster.register")
    @require_permission(Permission.CLUSTER_REGISTER)
    @tenant_scoped()
    def register_tenant_cluster(
        self, info: Info, input: RegisterTenantClusterInput
    ) -> MutationResultType[TenantClusterType]:
        plugin = ProviderPlugin.objects.filter(slug=input.provider_plugin_slug).first()
        if plugin is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"plugin {input.provider_plugin_slug!r} not registered",
                field="providerPluginSlug",
            )
        if input.auth_method not in {"kubeconfig", "exec_plugin", "service_account_token"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "authMethod must be kubeconfig | exec_plugin | service_account_token",
                field="authMethod",
            )
        if TenantCluster.objects.filter(slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"cluster slug {input.slug!r} already registered",
                field="slug",
            )
        issues = _observability_config_issues(input.provider_config)
        if issues:
            return _observability_failure(issues)

        org = None
        if input.organization_scoped:
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            org = Organization.objects.filter(pk=org_id).first() if org_id else None

        cluster = TenantCluster.objects.create(
            organization=org,
            provider_plugin=plugin,
            slug=input.slug,
            name=input.name,
            auth_method=input.auth_method,
            region=input.region or "",
            endpoint=input.endpoint or "",
            ca_cert=input.ca_cert or "",
            auth_config=input.auth_config or {},
            provider_config=input.provider_config or {},
            ingress_class=input.ingress_class or "nginx",
        )
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(
        action="cluster.issue_agent_key",
        target=lambda root, info, input: ("cluster", str(input.cluster_id)),
    )
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def issue_cluster_agent_key(
        self, info: Info, input: IssueClusterAgentKeyInput
    ) -> MutationResultType[_ClusterAgentKeyIssuedPayload]:
        """Issue (or rotate) the in-cluster keep-alive agent key (#808).

        Generates a 256-bit scoped key, stores only its SHA-256, and
        returns the plaintext exactly once so the operator can install
        it in the agent's Secret. Re-running rotates the key — the old
        one stops authenticating immediately.

        Tenant-scoped: the lookup is constrained to the caller's org (or
        a platform-shared cluster), so a tenant can never mint an agent
        credential for another tenant's cluster — an out-of-scope guid
        reads as NOT_FOUND, identical to a guid that doesn't exist.
        """

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"cluster {input.cluster_id!r} not found",
                field="clusterId",
            )

        rotated = bool(cluster.agent_key_hash)
        raw_key = secrets.token_hex(32)  # 256-bit, 64 hex chars
        cluster.agent_key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        update_fields = ["agent_key_hash", "updated_at", "version"]
        if input.interval_seconds is not None:
            # Clamp to the AC ceiling (<=60s) and a sane floor so a typo
            # can't make the agent hot-loop or look perpetually offline.
            cluster.heartbeat_interval_seconds = max(5, min(int(input.interval_seconds), 60))
            update_fields.append("heartbeat_interval_seconds")
        cluster.save(update_fields=update_fields)

        from django.conf import settings

        # APP_BASE_URL is the platform's external origin (used elsewhere
        # for the GitHub-App manifest callback URL). Empty in local dev,
        # in which case we return the path-only form — the agent's
        # install snippet templates the host in regardless.
        base = (getattr(settings, "APP_BASE_URL", "") or "").rstrip("/")
        heartbeat_path = f"/api/clusters/v1/{cluster.guid}/heartbeat/"
        return gql_success(
            _ClusterAgentKeyIssuedPayload(
                cluster_id=GUID(str(cluster.guid)),
                agent_key=raw_key,
                interval_seconds=cluster.heartbeat_interval_seconds,
                heartbeat_url=f"{base}{heartbeat_path}" if base else heartbeat_path,
                rotated=rotated,
            )
        )

    @strawberry.field
    @mutation_audit(
        action="cluster.deploy_agent",
        target=lambda root, info, input: ("cluster", str(input.cluster_id)),
    )
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def deploy_cluster_agent(
        self, info: Info, input: DeployClusterAgentInput
    ) -> MutationResultType[TenantClusterType]:
        """Deploy the in-cluster keep-alive agent to a cluster (#873).

        Applies the agent's Namespace + Deployment manifests via the
        cluster's driver (server-side apply, idempotent — re-running
        converges the Deployment). The agent reads ``heartbeat_url`` and
        ``agent_key`` from the ``astrolift-agent`` Secret, which this
        mutation does NOT create: the raw key is surfaced exactly once at
        ``issueClusterAgentKey`` and never persisted, so the control
        plane has no key to put in the Secret. The operator applies the
        Secret from the install snippet first; this then lands the
        Deployment that references it.

        Gated on ``cluster.manage`` — same actor who issues the agent key.
        Tenant-scoped: the lookup is constrained to the caller's org (or
        a platform-shared cluster), so an out-of-scope guid reads as
        NOT_FOUND, identical to a guid that doesn't exist.
        """

        from core.cluster_management import ClusterManagementError, deploy_agent_dispatch

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"cluster {input.cluster_id!r} not found",
                field="clusterId",
            )
        if not cluster.agent_key_hash:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "issue an agent key first before deploying the agent",
                field="clusterId",
            )
        if not cluster.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cluster is inactive — re-activate before deploying the agent",
            )

        # Apply the Namespace + Deployment via the cluster driver. Two
        # failure shapes are persisted to last_management_error (so the
        # settings card surfaces them) and returned as INTERNAL: the
        # driver couldn't be built / doesn't support apply_manifests
        # (ClusterManagementError), or the apply itself reported per-
        # manifest errors (ApplyResult.ok is False).
        try:
            result = deploy_agent_dispatch(cluster=cluster)
        except ClusterManagementError as exc:
            cluster.last_management_error = str(exc)
            cluster.save(update_fields=["last_management_error", "updated_at", "version"])
            return gql_failure(ErrorCode.INTERNAL.value, str(exc))

        if not result.ok:
            message = "agent deploy failed: " + "; ".join(str(e) for e in result.errors)
            cluster.last_management_error = message
            cluster.save(update_fields=["last_management_error", "updated_at", "version"])
            return gql_failure(ErrorCode.INTERNAL.value, message)

        cluster.last_management_error = ""
        cluster.save(update_fields=["last_management_error", "updated_at", "version"])
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.update")
    @require_permission(Permission.CLUSTER_UPDATE)
    @tenant_scoped()
    def update_tenant_cluster(
        self, info: Info, input: UpdateTenantClusterInput
    ) -> MutationResultType[TenantClusterType]:
        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.id),
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found")
        if input.is_active is not None:
            cluster.is_active = input.is_active
        if input.region is not None:
            cluster.region = input.region
        if input.endpoint is not None:
            cluster.endpoint = input.endpoint
        auth_config_changed = input.alb_auth_config is not strawberry.UNSET
        oidc_changed = input.oidc_auth_config is not strawberry.UNSET

        # Refuse a class flip that would take the auth gate away as a side
        # effect (#1616). Each ingress class reads its own config, so moving
        # between them silently drops the old gate and renders nothing in its
        # place: the apps go public with no warning and no log line.
        #
        # This guards the implicit case only. An operator who nulls the config
        # for the current class is asking to remove the gate, and
        # reconcile_cluster_ingresses documents that as supported.
        class_changing = input.ingress_class is not None and input.ingress_class != cluster.ingress_class
        had_gate = _has_auth_gate(cluster.ingress_class, cluster)
        if input.ingress_class is not None:
            cluster.ingress_class = input.ingress_class
        if input.ingress_mode is not None:
            if input.ingress_mode not in {m.value for m in IngressMode}:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"ingressMode must be one of "
                    f"{sorted(m.value for m in IngressMode)}, got {input.ingress_mode!r}",
                    field="ingressMode",
                )
            cluster.ingress_mode = input.ingress_mode
        if auth_config_changed:
            cluster.alb_auth_config = input.alb_auth_config
        if oidc_changed:
            cluster.oidc_auth_config = input.oidc_auth_config
        if class_changing and had_gate and not _has_auth_gate(cluster.ingress_class, cluster):
            needs = "oidcAuthConfig" if cluster.ingress_class != "alb" else "albAuthConfig"
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"changing ingressClass to {cluster.ingress_class!r} would remove the "
                f"authentication gate from every app on this cluster, because that class "
                f"reads {needs} and none is set. Set {needs} in the same call, or clear "
                f"the current gate explicitly first if going public is intended.",
                field="ingressClass",
            )
        cluster.save()

        # GitOps round-trip (#853): when the operator changes the auth
        # gate, mirror it back into every bound app's astrolift.toml
        # so the repo (source of truth) doesn't drift from the DB. An
        # ingress_class flip counts as a change even when neither config
        # was touched: the class decides which gate is in force, so the
        # manifest's [ingress.auth] describes a different gate after it
        # (#1539). This
        # is best-effort and must never block the UI save — a missing
        # source connection is a graceful skip, and any SCM failure is
        # swallowed here and surfaced only in the logs.
        if auth_config_changed or oidc_changed or class_changing:
            try:
                from astrolift_clusters.services.toml_writeback import (
                    write_auth_config_for_cluster,
                )

                actor = getattr(info.context, "user", None)
                write_auth_config_for_cluster(cluster, actor)
            except Exception:  # noqa: BLE001 — write-back never blocks the save
                logger.exception(
                    "auth config TOML write-back failed for cluster %s",
                    cluster.slug,
                )

        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.reconcile_ingresses")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def reconcile_cluster_ingresses(
        self, info: Info, input: ReconcileClusterIngressesInput
    ) -> MutationResultType[ReconcileClusterIngressesResult]:
        """Re-apply the cluster's edge auth gate across every
        managed-subdomain Ingress on it (#851, #1539).

        The operator sets ``albAuthConfig`` / ``oidcAuthConfig`` via
        ``updateTenantCluster``; that only changes what the *next* deploy
        renders. This mutation pushes the change onto the live Ingresses
        now — patching the auth annotations on every Ingress labelled
        ``astrolift.dev/managed-subdomain=true`` so the controller
        reconciles on its next sync. Clearing the config strips the
        annotations, leaving the apps public; that is the supported way
        to take a gate off deliberately.

        Which annotations get patched follows the cluster's ingress
        class: the ``alb.ingress.kubernetes.io/auth-*`` keys on ALB, the
        ``nginx.ingress.kubernetes.io/auth-*`` keys pointing at the
        central auth host on every other class. This used to refuse
        anything but ALB, which left the central-auth path with no way
        to push a gate onto running Ingresses at all.

        Per-namespace failures surface in ``errors`` without aborting the
        sweep; the envelope stays ``ok=true`` so the operator sees
        partial progress plus the specific namespaces that couldn't be
        reached."""

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        from core.ingress_reconcile import reconcile_cluster_ingresses

        result = reconcile_cluster_ingresses(cluster)
        return gql_success(
            ReconcileClusterIngressesResult(
                reconciled_count=int(result["reconciled"]),
                skipped_count=int(result["skipped"]),
                errors=list(result["errors"]),
            )
        )

    @strawberry.field
    @mutation_audit(action="cluster.bring_into_management")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def bring_cluster_into_management(
        self, info: Info, input: BringClusterIntoManagementInputType
    ) -> MutationResultType[TenantClusterType]:
        """Operator-driven transition from ``registered`` to ``managed``
        (or ``error`` on failure). Returns the row in ``managing`` state
        so the UI can poll for completion. Idempotent — re-running
        against a managing/managed row no-ops the lifecycle flip and
        joins the in-flight workflow."""

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        if not cluster.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cluster is inactive — re-activate before bringing into management",
            )
        if cluster.lifecycle == TenantCluster.Lifecycle.MANAGING.value:
            # Already in flight — surface the current state without
            # re-kicking the workflow (the existing run picks up the
            # same workflow id anyway, but skipping the DB write
            # keeps the row's updated_at stable for the UI).
            return gql_success(cluster_to_type(cluster))

        actor = _actor_from_request(info)
        with transaction.atomic():
            _kick_bring_into_management(
                cluster=cluster,
                actor=actor,
                force_preflight=True,
            )
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.refresh_management")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def refresh_cluster_management(
        self, info: Info, input: RefreshClusterManagementInputType
    ) -> MutationResultType[TenantClusterType]:
        """Same workflow as ``bringClusterIntoManagement`` but accepts
        already-managed rows — the operator hits this when prereqs
        change out-of-band (cert-manager upgraded, ingress controller
        swapped) and wants the capabilities snapshot refreshed.

        ``forcePreflight=true`` re-runs the Job; default false skips
        it for a fast probe + RBAC reconcile."""

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        if not cluster.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cluster is inactive — re-activate before refreshing management",
            )

        actor = _actor_from_request(info)
        with transaction.atomic():
            _kick_bring_into_management(
                cluster=cluster,
                actor=actor,
                force_preflight=bool(input.force_preflight),
            )
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.decommission")
    @require_permission(Permission.CLUSTER_UNREGISTER)
    @tenant_scoped()
    def decommission_cluster(
        self, info: Info, input: DecommissionClusterInputType
    ) -> MutationResultType[TenantClusterType]:
        """Lift the platform RBAC and flip the cluster to ``decommissioned``.

        Refuses when active app environments are still bound — the
        workflow's drained-check activity surfaces the bound count in
        ``last_management_error`` and flips the row to ``error`` so the
        operator can address it. Migrate or delete bound environments
        first (see ``MigrateAppWorkflow``).

        Once decommissioned, the cluster is excluded from the
        active-cluster picker. Re-onboarding goes through
        ``bringClusterIntoManagement`` against a fresh cluster row;
        decommissioned rows are kept for audit only.
        """

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        if cluster.lifecycle == TenantCluster.Lifecycle.DECOMMISSIONED.value:
            # Already terminal — surface the row without re-firing.
            return gql_success(cluster_to_type(cluster))
        if cluster.lifecycle == TenantCluster.Lifecycle.DECOMMISSIONING.value:
            # Already in flight — join the existing run.
            return gql_success(cluster_to_type(cluster))
        if cluster.lifecycle not in (
            TenantCluster.Lifecycle.MANAGED.value,
            TenantCluster.Lifecycle.ERROR.value,
        ):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"cluster lifecycle is {cluster.lifecycle!r}; only managed or error clusters can be decommissioned",
            )

        actor = _actor_from_request(info)
        with transaction.atomic():
            _kick_decommission_cluster(
                cluster=cluster,
                actor=actor,
                delete_cloud_infra=bool(input.delete_cloud_infra),
            )
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.install_prereqs")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def install_cluster_prereqs(
        self, info: Info, input: InstallClusterPrereqsInputType
    ) -> MutationResultType[TenantClusterType]:
        """Apply the operator's bootstrap-recipe selection to the
        cluster (#66). Fires ``InstallClusterPrereqsWorkflow``.

        The mutation collects the operator's chosen components + per-
        option overrides; the workflow renders one Flux ``HelmRelease``
        per chosen component into ``astrolift-system`` and the
        cluster's Flux controller reconciles. Idempotent — re-running
        with a different selection converges the in-cluster state.

        Returns the cluster row immediately; the in-flight workflow
        state and individual HelmRelease ``status`` subresources flow
        into the cluster-status tab.
        """

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "cluster not found",
                field="clusterId",
            )
        if not cluster.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "cluster is inactive — re-activate before installing prereqs",
            )

        # Reshape the flat triple list into the nested dict the
        # workflow consumes.
        overrides: dict[str, dict[str, str]] = {}
        for o in input.option_overrides or []:
            overrides.setdefault(o.component_key, {})[o.option_key] = o.value

        actor = _actor_from_request(info)
        start_workflow(
            "InstallClusterPrereqsWorkflow",
            args=[
                InstallClusterPrereqsInput(
                    cluster_id=cluster.pk,
                    actor=actor,
                    selected_components=tuple(input.selected_components),
                    option_overrides=overrides,
                ),
            ],
            workflow_id=f"InstallClusterPrereqsWorkflow-{cluster.guid}",
        )
        return gql_success(cluster_to_type(cluster))

    @strawberry.field
    @mutation_audit(action="cluster.record_bootstrap_run")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def record_cluster_bootstrap_run(
        self, info: Info, input: RecordClusterBootstrapRunInput
    ) -> MutationResultType[_BootstrapRunRecordedPayload]:
        """Record an ``astro cluster bootstrap`` outcome (#319).

        The CLI fires this after the helm install/upgrade pass settles,
        so the control plane has a durable record of how each cluster
        was last brought up. Two surfaces consume the result:

        * the ``ClusterBootstrapRun`` row drives the "Last bootstrap"
          card on ``/clusters/[slug]`` (and the per-cluster history list);
        * the ``cluster.bootstrap_run`` event emitted in the same
          transaction feeds ``astroliftEvents`` queries + the webhook
          fan-out for the audit trail.

        Permission gate is ``cluster.manage`` — the operator who can
        bring a cluster into management is the same actor whose CLI is
        reporting bootstrap outcomes. Failure to record must not fail
        the CLI's bootstrap path, so the CLI catches errors from this
        mutation and prints a warning rather than exiting non-zero —
        but the gate stays strict on the control-plane side so a
        random un-privileged session can't seed false history.
        """
        status = (input.status or "").strip().lower()
        if status not in {
            ClusterBootstrapRun.Status.SUCCEEDED.value,
            ClusterBootstrapRun.Status.FAILED.value,
        }:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "status must be 'succeeded' or 'failed'",
                field="status",
            )

        if not input.cluster_slug:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "clusterSlug is required",
                field="clusterSlug",
            )

        # installed_releases comes off the wire as scalar JSON; the
        # model column is a JSONField(default=list), so anything but a
        # list is a schema-level lie we should refuse rather than
        # coerce.
        releases = input.installed_releases
        if releases is None:
            releases = []
        if not isinstance(releases, list):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "installedReleases must be a JSON array",
                field="installedReleases",
            )

        host_info = input.host_info
        if host_info is None:
            host_info = {}
        if not isinstance(host_info, dict):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "hostInfo must be a JSON object",
                field="hostInfo",
            )

        if input.ended_at < input.started_at:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "endedAt must be at or after startedAt",
                field="endedAt",
            )

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            slug=input.cluster_slug,
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"cluster {input.cluster_slug!r} not found",
                field="clusterSlug",
            )

        request = getattr(info.context, "request", None)
        user = getattr(request, "user", None) if request else None
        triggered_by = user if (user is not None and getattr(user, "is_authenticated", False)) else None

        with transaction.atomic():
            run = ClusterBootstrapRun.objects.create(
                tenant_cluster=cluster,
                triggered_by=triggered_by,
                status=status,
                chart_version=input.chart_version or "",
                installed_releases=releases,
                cli_version=input.cli_version or "",
                host_info=host_info,
                error_message=input.error_message or "",
                started_at=input.started_at,
                ended_at=input.ended_at,
            )

            # Emit the audit event in the same txn so a successful
            # mutation always has its event row, and a rolled-back
            # mutation never leaves a phantom event behind. The
            # ``astroliftEvents`` query, the webhook fan-out, and the
            # in-app activity feed all key off this row.
            Event.emit(
                event_type="cluster.bootstrap_run",
                payload={
                    "cluster_slug": cluster.slug,
                    "cluster_id": str(cluster.guid),
                    "status": run.status,
                    "chart_version": run.chart_version,
                    "installed_releases": run.installed_releases,
                    "cli_version": run.cli_version,
                    "host_info": run.host_info,
                    "error_message": run.error_message,
                    "started_at": run.started_at.isoformat(),
                    "ended_at": run.ended_at.isoformat(),
                },
                resource_kind="TenantCluster",
                resource_id=str(cluster.guid),
                actor_user_id=triggered_by.pk if triggered_by else None,
            )

        return gql_success(_BootstrapRunRecordedPayload(id=GUID(str(run.guid))))

    @strawberry.field
    @mutation_audit(action="cluster.unregister")
    @require_permission(Permission.CLUSTER_UNREGISTER)
    @tenant_scoped()
    def unregister_tenant_cluster(
        self, info: Info, input: UnregisterTenantClusterInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.id),
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found")

        # Refuse if active apps still target this cluster.
        from astrolift_registry.models import RegisteredApp

        in_use = RegisteredApp.objects.filter(default_tenant_cluster=cluster, is_active=True).count()
        if in_use:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"{in_use} active app(s) still target this cluster; reassign first",
            )
        cluster.soft_delete()
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- ManagedDomain ----------------------------------------------

    @strawberry.field
    @mutation_audit(action="domain.create")
    @require_permission(Permission.PROVIDER_PLUGIN_CONFIGURE)
    @tenant_scoped()
    def create_managed_domain(
        self, info: Info, input: CreateManagedDomainInput
    ) -> MutationResultType[ManagedDomainType]:
        if ManagedDomain.objects.filter(zone=input.zone, deleted_at__isnull=True).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"zone {input.zone!r} already registered",
                field="zone",
            )
        if input.default_for not in {"tenant_apps", "preview_envs", "both", "none"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "defaultFor must be tenant_apps | preview_envs | both | none",
                field="defaultFor",
            )

        org = None
        if input.organization_scoped:
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            org = Organization.objects.filter(pk=org_id).first() if org_id else None

        domain = ManagedDomain.objects.create(
            organization=org,
            zone=input.zone,
            dns_driver=input.dns_driver,
            default_for=input.default_for,
            is_wildcard_managed=input.is_wildcard_managed,
            dns_config=input.dns_config or {},
        )

        # Registering a zone without provisioning it is a dead end the
        # operator cannot see (#1673): the row exists but no hosted zone,
        # no NS records to delegate to, no cert. Kick off the provisioning
        # workflow against the first cluster whose driver can host the
        # zone; the row's provision_state / provision_nameservers fill in
        # as it runs and the UI surfaces them.
        cluster = _first_dns_cluster()
        if cluster is not None:
            start_workflow(
                "ProvisionManagedDomainWorkflow",
                args=[
                    ProvisionManagedDomainInput(
                        cluster_id=cluster.pk,
                        zone=input.zone,
                        is_platform_managed_zone=True,
                        actor=_actor_from_request(info),
                    ),
                ],
                workflow_id=(
                    f"ProvisionManagedDomainWorkflow-{cluster.guid}-" f"{input.zone.replace('.', '-')}"
                ),
            )
        return gql_success(domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="domain.update")
    @require_permission(Permission.PROVIDER_PLUGIN_CONFIGURE)
    @tenant_scoped()
    def update_managed_domain(
        self, info: Info, input: UpdateManagedDomainInput
    ) -> MutationResultType[ManagedDomainType]:
        tenant = get_current_tenant()
        domain = ManagedDomain.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.id),
        ).first()
        if domain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "domain not found")
        if input.default_for is not None:
            domain.default_for = input.default_for
        if input.is_wildcard_managed is not None:
            domain.is_wildcard_managed = input.is_wildcard_managed
        if input.dns_config is not None:
            domain.dns_config = input.dns_config
        domain.save()
        return gql_success(domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="domain.delete")
    @require_permission(Permission.PROVIDER_PLUGIN_CONFIGURE)
    @tenant_scoped()
    def soft_delete_managed_domain(
        self, info: Info, input: SoftDeleteManagedDomainInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        domain = ManagedDomain.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.id),
        ).first()
        if domain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "domain not found")
        domain.soft_delete()

        # The cloud resources must die with the row: a delete that only
        # soft-deletes leaves the hosted zone billing monthly and the
        # wildcard cert orphaned (found live: myastrolift.net survived
        # its own deletion). Fire-and-forget; the activity is idempotent.
        cluster = _first_dns_cluster()
        if cluster is not None:
            start_workflow(
                "DeprovisionManagedDomainWorkflow",
                args=[
                    DeprovisionManagedDomainInput(
                        cluster_id=cluster.pk,
                        zone=domain.zone,
                        actor=_actor_from_request(info),
                    ),
                ],
                workflow_id=(
                    f"DeprovisionManagedDomainWorkflow-{cluster.guid}-" f"{domain.zone.replace('.', '-')}"
                ),
            )
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    @strawberry.mutation
    @mutation_audit(action="cluster.managed_domain.provision")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def provision_managed_domain(
        self,
        info: Info,
        cluster_id: GUID,
        zone: str,
        is_platform_managed_zone: bool = False,
    ) -> MutationResultType[ProvisionManagedDomainPayload]:
        """Start the two-step domain provisioning workflow (#781).

        If is_platform_managed_zone=True: creates the DNS zone and returns
        the NS records for registrar delegation.
        Either way: requests the wildcard cert and returns CNAME validation
        records the operator must add to their zone.

        Workflow polls every 30s for cert issuance. Once issued: registers
        the ManagedDomain row and marks it active. Use revalidateManagedDomain
        to force an immediate check; reissueManagedDomainCert to delete and
        re-request the cert (sometimes forces ACM/GCP/Azure to pick up
        recently-added validation records).
        """
        if not zone or "." not in zone:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "zone must be a valid domain name (non-empty, contains a dot)",
                field="zone",
            )

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")

        workflow_id = f"ProvisionManagedDomainWorkflow-{cluster.guid}-{zone.replace('.', '-')}"
        actor = _actor_from_request(info)
        start_workflow(
            "ProvisionManagedDomainWorkflow",
            args=[
                ProvisionManagedDomainInput(
                    cluster_id=cluster.pk,
                    zone=zone,
                    is_platform_managed_zone=is_platform_managed_zone,
                    actor=actor,
                ),
            ],
            workflow_id=workflow_id,
        )
        return gql_success(
            ProvisionManagedDomainPayload(
                zone=zone,
                workflow_id=workflow_id,
                nameservers=[],
                message=(
                    "Provisioning started. Check the Managed Domains page for"
                    " NS records (if platform-managed) and certificate validation records."
                ),
            )
        )

    @strawberry.mutation
    @mutation_audit(action="cluster.managed_domain.revalidate")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def revalidate_managed_domain(
        self,
        info: Info,
        cluster_id: GUID,
        zone: str,
    ) -> MutationResultType[RevalidateManagedDomainPayload]:
        """Signal the running ProvisionManagedDomainWorkflow to check cert
        issuance immediately without waiting for the next 30s poll tick.
        Use after adding validation CNAME records to your DNS zone.
        """
        if not zone or "." not in zone:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "zone must be a valid domain name (non-empty, contains a dot)",
                field="zone",
            )

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")

        workflow_id = f"ProvisionManagedDomainWorkflow-{cluster.guid}-{zone.replace('.', '-')}"
        signaled = signal_workflow(workflow_id, "revalidate")
        restarted = False
        if not signaled:
            # A failed NS gate closes the original run, so a signal cannot
            # reach it. Starting the same idempotent workflow again gives the
            # operator's DNS change a real feedback path without requiring a
            # second domain row or manual cleanup.
            restarted = start_workflow(
                "ProvisionManagedDomainWorkflow",
                args=[
                    ProvisionManagedDomainInput(
                        cluster_id=cluster.pk,
                        zone=zone,
                        is_platform_managed_zone=True,
                        actor=_actor_from_request(info),
                    )
                ],
                workflow_id=workflow_id,
            ).enqueued
        return gql_success(
            RevalidateManagedDomainPayload(
                zone=zone,
                signaled=signaled,
                message=(
                    "Revalidation signal sent — the workflow will check cert"
                    " issuance on the next activity slot."
                    if signaled
                    else (
                        "A new provisioning run was started and will re-check"
                        " public DNS delegation."
                        if restarted
                        else "Temporal is disabled or the workflow was not found; no signal sent."
                    )
                ),
            )
        )

    @strawberry.mutation
    @mutation_audit(action="cluster.managed_domain.reissue_cert")
    @require_permission(Permission.CLUSTER_MANAGE)
    @tenant_scoped()
    def reissue_managed_domain_cert(
        self,
        info: Info,
        cluster_id: GUID,
        zone: str,
    ) -> MutationResultType[ReissueManagedDomainCertPayload]:
        """Signal the running workflow to delete the current cert and request
        a fresh one. This resets the validation CNAME records — re-add them
        after calling this. Sometimes forces ACM/GCP/Azure to pick up
        recently-delegated zones.
        """
        if not zone or "." not in zone:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "zone must be a valid domain name (non-empty, contains a dot)",
                field="zone",
            )

        tenant = get_current_tenant()
        cluster = TenantCluster.objects.filter(
            Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True),
            guid=str(cluster_id),
            deleted_at__isnull=True,
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")

        workflow_id = f"ProvisionManagedDomainWorkflow-{cluster.guid}-{zone.replace('.', '-')}"
        signaled = signal_workflow(workflow_id, "reissue")
        return gql_success(
            ReissueManagedDomainCertPayload(
                zone=zone,
                signaled=signaled,
                message=(
                    "Reissue signal sent — the workflow will delete and re-request"
                    " the cert. Re-add the new validation CNAME records when they appear."
                    if signaled
                    else "Temporal is disabled or the workflow was not found; no signal sent."
                ),
            )
        )

    # ---- ProviderPlugin config -------------------------------------

    @strawberry.field
    @mutation_audit(action="provider_plugin.configure")
    @require_permission(Permission.PROVIDER_PLUGIN_CONFIGURE)
    @tenant_scoped()
    def configure_provider_plugin(
        self, info: Info, input: ConfigureProviderPluginInput
    ) -> MutationResultType[_ProviderPluginConfigPayload]:
        plugin = ProviderPlugin.objects.filter(slug=input.plugin_slug).first()
        if plugin is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"plugin {input.plugin_slug!r} not registered",
                field="pluginSlug",
            )

        # Validate against the plugin's JSON-schema if one is declared.
        schema = plugin.config_schema or {}
        if schema:
            try:
                import jsonschema

                jsonschema.validate(input.config, schema)
            except Exception as exc:  # noqa: BLE001
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"config failed schema validation: {exc}",
                    field="config",
                )

        org = None
        if input.organization_scoped:
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            org = Organization.objects.filter(pk=org_id).first() if org_id else None

        ProviderPluginConfig.objects.update_or_create(
            organization=org,
            provider_plugin=plugin,
            defaults={"config": input.config},
        )
        return gql_success(
            _ProviderPluginConfigPayload(
                plugin_slug=plugin.slug,
                organization_scoped=input.organization_scoped,
            )
        )
