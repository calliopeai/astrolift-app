"""GraphQL types for RegisteredApp, Workload, Container, Template."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftRegisteredApp")
class RegisteredAppType:
    id: GUID
    slug: str
    name: str
    description: str

    organization_slug: str
    team_slug: str
    project_slug: str

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
    deploy_branch: str
    preview_screenshot_url: str

    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None


@strawberry.type(name="AstroliftWorkload")
class WorkloadType:
    id: GUID
    slug: str
    name: str
    kind: str
    is_public: bool
    schedule: str
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

    sync_state = classify_state(SyncSnapshot(
        db_hash=app.manifest_hash or "",
        repo_hash=_repo_hash_for(app),
        last_synced_hash=app.last_synced_hash or "",
    ))
    return RegisteredAppType(
        id=GUID(str(app.guid)),
        slug=app.slug,
        name=app.name,
        description=app.description or "",
        organization_slug=app.organization.slug,
        team_slug=app.team.slug,
        project_slug=app.project.slug,
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
        deploy_branch=app.deploy_branch,
        preview_screenshot_url=app.preview_screenshot_url or "",
        created_at=app.created_at,
        updated_at=app.updated_at,
        deleted_at=app.deleted_at,
    )


def workload_to_type(workload) -> WorkloadType:
    return WorkloadType(
        id=GUID(str(workload.guid)),
        slug=workload.slug,
        name=workload.name,
        kind=workload.kind,
        is_public=workload.is_public,
        schedule=workload.schedule or "",
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
