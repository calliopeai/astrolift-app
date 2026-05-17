"""GraphQL types for RegisteredApp, Workload, Container, Template."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftSecurityPolicy")
class SecurityPolicyType:
    """Resolved supply-chain policy for an app (#313).

    Mirrors ``RegisteredApp.security_policy_resolved`` — every field
    is present even when the underlying ``security_policy`` JSON blob
    is empty, because callers always want the *effective* gate
    settings (platform defaults filled in for unspecified knobs).

    ``block_on_high_cve_threshold`` is nullable: None means "no
    count-based block on high-severity CVEs"; a non-null integer N
    means "block when the high-CVE count is >= N".
    """

    block_on_critical_cves: bool
    block_on_missing_signature: bool
    block_on_high_cve_threshold: int | None


@strawberry.type(name="AstroliftRegisteredApp")
class RegisteredAppType:
    id: GUID
    slug: str
    name: str
    description: str

    organization_slug: str
    team_slug: str
    # ``project_slug`` is empty when the app is unassigned (#391).
    # ``project_id`` / ``project_name`` / ``team_id`` / ``team_name``
    # are the shape the Settings "Assign project" card consumes — flat
    # scalars rather than a nested AstroliftProject type so the
    # registry schema doesn't have to import / re-export the identity
    # type and risk the cross-app re-decoration class of bug.
    project_slug: str
    project_id: GUID | None
    project_name: str
    team_id: GUID | None
    team_name: str

    source_kind: str
    source_repo: str
    source_url: str
    manifest_path: str
    default_branch: str

    manifest_hash: str
    raw_manifest: str
    raw_manifest_staged: str
    last_synced_hash: str
    manifest_sync_state: str

    registry_repo_uri: str
    # ECR-side coordinates surfaced for the Settings page's CI-setup
    # section (#382). ``ecr_repo_uri`` mirrors ``registry_repo_uri``;
    # the alias is the value an operator pastes into the
    # ``ASTROLIFT_ECR_URI`` GitHub Actions secret. ``ecr_push_role_arn``
    # is the IRSA-bound IAM role the workflow assumes via OIDC and is
    # populated by the cluster-bootstrap path (#309). Stays empty
    # until provisioning lands the role ARN.
    ecr_repo_uri: str
    ecr_push_role_arn: str
    k8s_namespace: str
    subdomain: str
    is_active: bool
    provisioning_status: str
    provisioning_error: str
    deploy_token_last_4: str

    log_retention_days: int
    preview_max_active: int
    preview_enabled: bool
    trigger_mode: str
    cron_expression: str
    cron_paused: bool
    deploy_branch: str
    preview_screenshot_url: str

    # Approval policy (#291). ``approver_team_id`` is the team's GUID
    # (or null when no team gate is set); ``approver_user_ids`` are
    # Django user PKs as strings (matching ``AstroliftUser.id``).
    requires_approval: bool
    approver_team_id: GUID | None
    approver_user_ids: list[str]
    minimum_approvals: int

    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None
    # Last operator-initiated "Resync from source" timestamp (#386).
    # Null until the operator has clicked the button for the first
    # time; surfaces on the Settings page as relative time.
    last_resync_at: dt.datetime | None

    # Source-host webhook state (#385). ``installed_at`` is null
    # until the operator clicks "Install webhook" on the Settings
    # page; FE renders it as a green "installed · 5m ago" chip vs
    # an amber "not installed" chip.
    source_webhook_installed_at: dt.datetime | None

    # Supply-chain policy (#313). Always populated — the resolver
    # reads ``security_policy_resolved`` so platform defaults are
    # surfaced for any unspecified knob. The supply-chain Settings
    # card (#307) reads this directly to seed the form.
    security_policy: SecurityPolicyType


@strawberry.type(name="AstroliftAppTeamAccess")
class AppTeamAccessType:
    """A team's access grant to an app.

    Joins ``RegisteredApp`` and ``Team`` with an ``access_level`` of
    ``viewer`` / ``deployer`` / ``owner``. Returned by
    ``astroliftAppTeamAccesses(appSlug)`` so the FE can render the
    Teams card on the app-detail page.

    ``is_home`` flags the row that mirrors ``RegisteredApp.team`` —
    the app's primary / home team. Frontend uses this to render the
    home-team affordance separately and to refuse revoking the
    home-team grant directly (callers must move the app to a
    different home team first).
    """

    id: GUID
    app_id: GUID
    app_slug: str
    team_id: GUID
    team_slug: str
    team_name: str
    access_level: str
    is_home: bool
    created_at: dt.datetime
    updated_at: dt.datetime


def app_team_access_to_type(access, *, home_team_id: int) -> AppTeamAccessType:
    return AppTeamAccessType(
        id=GUID(str(access.guid)),
        app_id=GUID(str(access.registered_app.guid)),
        app_slug=access.registered_app.slug,
        team_id=GUID(str(access.team.guid)),
        team_slug=access.team.slug,
        team_name=access.team.name,
        access_level=access.access_level,
        is_home=access.team_id == home_team_id,
        created_at=access.created_at,
        updated_at=access.updated_at,
    )


@strawberry.type(name="AstroliftWorkload")
class WorkloadType:
    id: GUID
    slug: str
    name: str
    kind: str
    is_public: bool
    schedule: str
    # CronJob concurrency policy (#427): ``forbid`` | ``queue`` |
    # ``replace``. Only meaningful when ``kind == "cronjob"``; always
    # present so the FE doesn't have to branch on null. Defaults to
    # ``forbid`` for non-cronjob rows so the badge component never
    # renders garbage.
    concurrency_policy: str
    replicas: int
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    hpa_min_replicas: int | None
    hpa_max_replicas: int | None
    hpa_target_cpu_pct: int
    storage_class: str
    storage_size: str
    registered_app_slug: str
    # The DNS name in-cluster callers use to reach this workload's
    # ClusterIP Service — ``<workloadSlug>.<namespace>.svc.cluster.local``
    # (#429). Same shape kubernetes' default DNS surfaces; the
    # ``namespace`` half mirrors ``namespace_for_app`` (explicit
    # ``app.k8s_namespace`` override → renderer default
    # ``<org>-<app>``). Empty string when neither the workload nor
    # the app has enough state to compute one (e.g. an app row
    # without an organization, which only happens in malformed
    # fixtures).
    in_cluster_service_fqdn: str


@strawberry.type(name="AstroliftContainer")
class ContainerType:
    id: GUID
    name: str
    is_primary: bool
    image_ref: str
    dockerfile_path: str
    build_context: str
    port: int
    command: list[str]
    args: list[str]
    env: JSON
    healthcheck_kind: str
    healthcheck_value: str
    healthcheck_port: int | None
    workload_slug: str


def app_to_type(app) -> RegisteredAppType:
    from astrolift_manifest.sync_state import (
        SyncSnapshot,
        classify_state,
    )

    sync_state = classify_state(
        SyncSnapshot(
            db_hash=app.manifest_hash or "",
            repo_hash=_repo_hash_for(app),
            last_synced_hash=app.last_synced_hash or "",
        )
    )
    project = app.project if app.project_id else None
    return RegisteredAppType(
        id=GUID(str(app.guid)),
        slug=app.slug,
        name=app.name,
        description=app.description or "",
        organization_slug=app.organization.slug,
        team_slug=app.team.slug,
        project_slug=project.slug if project else "",
        project_id=GUID(str(project.guid)) if project else None,
        project_name=project.name if project else "",
        team_id=GUID(str(app.team.guid)),
        team_name=app.team.name,
        source_kind=app.source_kind,
        source_repo=app.source_repo,
        source_url=app.source_url,
        manifest_path=app.manifest_path,
        default_branch=app.default_branch,
        manifest_hash=app.manifest_hash,
        raw_manifest=app.manifest_raw or "",
        raw_manifest_staged=app.manifest_raw_staged or "",
        last_synced_hash=app.last_synced_hash or "",
        manifest_sync_state=sync_state.value,
        registry_repo_uri=app.registry_repo_uri,
        ecr_repo_uri=app.registry_repo_uri,
        ecr_push_role_arn=app.push_role_ref or "",
        k8s_namespace=app.k8s_namespace,
        subdomain=app.subdomain,
        is_active=app.is_active,
        provisioning_status=app.provisioning_status,
        provisioning_error=app.provisioning_error,
        deploy_token_last_4=app.deploy_token_last_4,
        log_retention_days=app.log_retention_days,
        preview_max_active=app.preview_max_active,
        preview_enabled=app.preview_enabled,
        trigger_mode=app.trigger_mode,
        cron_expression=app.cron_expression or "",
        cron_paused=bool(app.cron_paused),
        deploy_branch=app.deploy_branch,
        preview_screenshot_url=app.preview_screenshot_url or "",
        requires_approval=bool(app.requires_approval),
        approver_team_id=(GUID(str(app.approver_team.guid)) if app.approver_team_id else None),
        approver_user_ids=[str(uid) for uid in app.approver_users.values_list("pk", flat=True)],
        minimum_approvals=int(app.minimum_approvals or 1),
        created_at=app.created_at,
        updated_at=app.updated_at,
        deleted_at=app.deleted_at,
        last_resync_at=app.last_resync_at,
        source_webhook_installed_at=app.source_webhook_installed_at,
        security_policy=_security_policy_to_type(app),
    )


def _security_policy_to_type(app) -> SecurityPolicyType:
    resolved = app.security_policy_resolved
    threshold = resolved["block_on_high_cve_threshold"]
    return SecurityPolicyType(
        block_on_critical_cves=bool(resolved["block_on_critical_cves"]),
        block_on_missing_signature=bool(resolved["block_on_missing_signature"]),
        block_on_high_cve_threshold=int(threshold) if threshold is not None else None,
    )


def _namespace_for_workload(workload) -> str:
    """Resolve the namespace for ``workload.registered_app``.

    Mirrors :func:`core.cluster_observability.namespace_for_app`
    (explicit row-level override → renderer default
    ``<orgSlug>-<appSlug>``) but stays local to avoid pulling
    ``core.cluster_observability`` (and its driver-registry imports)
    into the registry schema module."""
    app = workload.registered_app
    explicit = (getattr(app, "k8s_namespace", "") or "").strip()
    if explicit:
        return explicit
    org_slug = (getattr(getattr(app, "organization", None), "slug", "") or "").strip()
    if not org_slug:
        return ""
    return f"{org_slug}-{app.slug}"


def _in_cluster_service_fqdn(workload) -> str:
    """``<workloadSlug>.<namespace>.svc.cluster.local`` — empty when
    we can't build a namespace half (no org on the app, etc.)."""
    namespace = _namespace_for_workload(workload)
    if not namespace or not workload.slug:
        return ""
    return f"{workload.slug}.{namespace}.svc.cluster.local"


def workload_to_type(workload) -> WorkloadType:
    return WorkloadType(
        id=GUID(str(workload.guid)),
        slug=workload.slug,
        name=workload.name,
        kind=workload.kind,
        is_public=workload.is_public,
        schedule=workload.schedule or "",
        concurrency_policy=workload.concurrency_policy or "forbid",
        replicas=workload.replicas,
        cpu_request=workload.cpu_request or "",
        cpu_limit=workload.cpu_limit or "",
        memory_request=workload.memory_request or "",
        memory_limit=workload.memory_limit or "",
        hpa_min_replicas=workload.hpa_min_replicas,
        hpa_max_replicas=workload.hpa_max_replicas,
        hpa_target_cpu_pct=workload.hpa_target_cpu_pct,
        storage_class=workload.storage_class or "",
        storage_size=workload.storage_size or "",
        registered_app_slug=workload.registered_app.slug,
        in_cluster_service_fqdn=_in_cluster_service_fqdn(workload),
    )


def _repo_hash_for(app) -> str:
    """Best-known repo-side manifest hash. The DB doesn't track the
    repo-side hash on the app row directly; the SCM sync workflow
    populates `last_synced_hash` after a successful pull. Until a
    real fetch lands, use last_synced_hash as the floor — it's the
    last hash we know was on the repo. The `syncManifestFromRepo`
    mutation refreshes this."""
    return app.last_synced_hash or app.manifest_hash or ""


@strawberry.type(name="AstroliftRenderedManifest")
class RenderedManifestType:
    """The output of running the platform renderer against the
    stored TOML for a given (app, environment) pair.

    ``resources`` is a JSON list of Kubernetes resource dicts
    (apiVersion/kind/metadata/spec). The ``error`` field carries a
    human-readable message when parsing or normalization failed —
    callers render either the resources or the error, never both.

    ``error_line`` and ``error_column`` are 1-based source positions
    that point to the offending TOML line so editors can show a red
    squiggle. Both are ``None`` when the position couldn't be
    located (e.g. a missing required key has no source position)."""

    app_slug: str
    environment_name: str
    image_tag: str
    namespace: str
    resources: JSON
    error: str | None
    error_path: str | None
    error_line: int | None
    error_column: int | None


def container_to_type(container) -> ContainerType:
    return ContainerType(
        id=GUID(str(container.guid)),
        name=container.name,
        is_primary=container.is_primary,
        image_ref=container.image_ref or "",
        dockerfile_path=container.dockerfile_path,
        build_context=container.build_context,
        port=container.port,
        command=list(container.command or []),
        args=list(container.args or []),
        env=container.env or {},
        healthcheck_kind=container.healthcheck_kind,
        healthcheck_value=container.healthcheck_value or "",
        healthcheck_port=container.healthcheck_port,
        workload_slug=container.workload.slug,
    )
