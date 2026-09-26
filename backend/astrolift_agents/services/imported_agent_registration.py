"""Persist a reviewed, runnable imported Agent Package.

Import conversion is pure and previewable. This service is the explicit write
boundary: it creates/reconciles a direct-upload RegisteredApp, agent Workload,
immutable Brief, primary Container, and AgentEnvironmentSpec in one tenant-
scoped transaction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from _sdk.k8s_naming import app_namespace
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from astrolift_agents.models import AgentEnvironmentSpec, Brief
from astrolift_agents.services.agent_package import compatibility_snapshot, validate_agent_package
from astrolift_clusters.models import TenantCluster
from astrolift_dispatch.agent_secrets import SecretRefNamespaceError, assert_org_scoped_secret_ref
from astrolift_registry.models import Container, RegisteredApp, Workload


class ImportedAgentRegistrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImportedAgentRegistration:
    app: RegisteredApp
    workload: Workload
    brief: Brief
    environment_spec: AgentEnvironmentSpec
    created: bool


def _package_hash(organization, package: dict) -> str:
    body = json.dumps(
        {"organization": str(organization.guid), "package": package},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _agent_slug(organization, package: dict, requested: str) -> str:
    value = slugify(requested or str((package.get("agent") or {}).get("name") or ""))[:128]
    if not value:
        raise ImportedAgentRegistrationError("a non-empty agent slug is required")
    conflicting_workload = Workload.objects.filter(
        registered_app__organization=organization,
        slug=value,
        kind=Workload.Kind.AGENT,
        deleted_at__isnull=True,
    ).first()
    conflicting_app = RegisteredApp.objects.filter(
        organization=organization,
        slug=value,
        deleted_at__isnull=True,
    ).first()
    rows = [
        conflicting_workload.registered_app_id if conflicting_workload else None,
        conflicting_app.pk if conflicting_app else None,
    ]
    active_ids = {row for row in rows if row is not None}
    if len(active_ids) > 1 or (
        conflicting_app is not None and conflicting_app.source_kind != RegisteredApp.SourceKind.DIRECT_UPLOAD
    ):
        raise ImportedAgentRegistrationError(f"agent slug {value!r} is already in use")
    return value


@transaction.atomic
def persist_imported_agent_package(*, project, package: dict, slug: str = "") -> ImportedAgentRegistration:
    package = validate_agent_package(package)
    if (package.get("delivery") or {}).get("payload_required"):
        raise ImportedAgentRegistrationError(
            "imported package requires source bytes; register it from a repository instead"
        )
    image = str((package.get("runtime") or {}).get("image") or "").strip()
    if not image:
        raise ImportedAgentRegistrationError("runtime.image is required before an imported agent can run")
    organization = project.organization
    effective_slug = _agent_slug(organization, package, slug)
    name = str((package.get("agent") or {}).get("name") or effective_slug).strip()
    timeout = int((package.get("execution") or {}).get("timeout_seconds") or 300)
    if timeout < 1 or timeout > 604800:
        raise ImportedAgentRegistrationError("execution.timeout_seconds must be between 1 and 604800")
    for ref in (package.get("environment") or {}).get("secret_refs") or []:
        try:
            assert_org_scoped_secret_ref(ref["uri"], organization=organization)
        except SecretRefNamespaceError as exc:
            raise ImportedAgentRegistrationError(str(exc)) from exc

    content_hash = _package_hash(organization, package)
    source_repo = f"import://{organization.guid}/{content_hash[:32]}"
    manifest_path = str((package.get("source") or {}).get("manifest_path") or "imports/agent.json")
    app = RegisteredApp.objects.filter(
        organization=organization,
        slug=effective_slug,
        deleted_at__isnull=True,
    ).first()
    created = app is None
    previous_team_id = app.team_id if app is not None else None
    if app is None:
        from astrolift_registry.namespaces import namespace_refusal

        refusal = namespace_refusal(
            app_namespace(organization_slug=organization.slug, app_slug=effective_slug),
            organization_id=organization.pk,
        )
        if refusal is None:
            from astrolift_registry.hostname_claims import hostname_label_refusal

            refusal = hostname_label_refusal(effective_slug, organization=organization)
        if refusal is not None:
            raise ImportedAgentRegistrationError(refusal)
        app = RegisteredApp(
            organization=organization,
            team=project.team,
            project=project,
            name=name,
            slug=effective_slug,
            source_kind=RegisteredApp.SourceKind.DIRECT_UPLOAD,
            k8s_namespace=app_namespace(
                organization_slug=organization.slug,
                app_slug=effective_slug,
            ),
            subdomain=effective_slug,
            build_mode=RegisteredApp.BuildMode.NONE,
            trigger_mode=RegisteredApp.TriggerMode.MANUAL,
        )
    elif app.source_kind != RegisteredApp.SourceKind.DIRECT_UPLOAD:
        raise ImportedAgentRegistrationError(f"agent slug {effective_slug!r} is not an imported agent")
    app.name = name
    app.project = project
    app.team = project.team
    app.source_repo = source_repo
    app.manifest_path = manifest_path[:255]
    app.manifest_raw = json.dumps(package, sort_keys=True)
    app.manifest_normalized = package
    app.manifest_hash = content_hash
    app.last_synced_hash = content_hash
    app.provisioning_status = RegisteredApp.ProvisioningStatus.READY
    app.last_resync_at = timezone.now()
    app.default_tenant_cluster = (
        TenantCluster.objects.filter(
            Q(organization=organization) | Q(organization__isnull=True),
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        )
        .order_by("pk")
        .first()
    )
    app.save()
    from astrolift_registry.schema.mutations.helpers import (
        _downgrade_to_deployer,
        _ensure_owner_access,
    )

    if previous_team_id is not None and previous_team_id != app.team_id:
        _downgrade_to_deployer(app, previous_team_id)
    _ensure_owner_access(app, app.team_id)

    brief = Brief.objects.filter(
        organization=organization,
        content_hash=content_hash,
    ).first()
    if brief is None:
        environment = package.get("environment") or {}
        brief = Brief.objects.create(
            organization=organization,
            registered_app=app,
            content_hash=content_hash,
            status=Brief.Status.READY,
            manifest_snapshot={**compatibility_snapshot(package), "payload_storage_ready": True},
            secrets_refs=environment.get("secret_refs") or [],
            context={
                "source": "imported_agent_spec",
                "format": (package.get("imports") or [{}])[0].get("format", ""),
            },
            ttl_seconds=0,
            assembled_at=timezone.now(),
        )

    workload = Workload.objects.filter(
        registered_app=app,
        slug=effective_slug,
        deleted_at__isnull=True,
    ).first()
    if workload is None:
        workload = Workload(registered_app=app, slug=effective_slug)
    workload.name = name
    workload.kind = Workload.Kind.AGENT
    workload.run_family = Workload.RunFamily.TASK
    workload.run_mode = Workload.RunMode.ONCE
    workload.tool_timeout_seconds = timeout
    workload.brief = brief
    workload.save()

    # Hostname ledger (#2012) -- same call site the #1930 label check
    # covers. An agent workload is never public (dispatched, not served),
    # so this claims nothing today; wired in for whenever that changes and
    # for parity with every other place a workload is persisted.
    from astrolift_registry.hostname_claims import sync_workload_hostname_claims

    sync_workload_hostname_claims(app)

    container = workload.containers.filter(is_primary=True, deleted_at__isnull=True).first()
    if container is None:
        container = Container(workload=workload, name=effective_slug, is_primary=True)
    container.image_ref = image
    container.env = (package.get("environment") or {}).get("values") or {}
    container.save()

    spec = AgentEnvironmentSpec.objects.filter(
        organization=organization,
        slug=effective_slug,
        deleted_at__isnull=True,
    ).first()
    if spec is None:
        spec = AgentEnvironmentSpec(organization=organization, slug=effective_slug)
    spec.name = f"{name} runtime"
    spec.agent_type = (
        AgentEnvironmentSpec.AgentType.CODEX
        if "codex" in image.lower()
        else AgentEnvironmentSpec.AgentType.CLAUDE
    )
    spec.image_tag = image
    spec.env_vars = (package.get("environment") or {}).get("values") or {}
    spec.secret_refs = (package.get("environment") or {}).get("secret_refs") or []
    spec.config_repo = ""
    spec.config_manifest_path = manifest_path
    spec.save()
    return ImportedAgentRegistration(app, workload, brief, spec, created)


__all__ = [
    "ImportedAgentRegistration",
    "ImportedAgentRegistrationError",
    "persist_imported_agent_package",
]
