"""
Activities for the app onboarding + deploy workflows.

These wrap the platform models behind ``@activity.defn`` so the
workflow definition stays free of Django imports (Temporal runs
workflows in a sandbox that disallows non-deterministic imports).

The bodies are deliberately thin: each activity does exactly one
durable thing. Provider-specific logic happens behind the
``astrolift_drivers`` interfaces, which we resolve at activity entry.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities")


def _managed_services_for_environment(app_environment):
    """App-private and project-shared services consumed by one app env."""

    from django.db.models import Q

    from astrolift_services.models import ManagedService

    return ManagedService.objects.filter(
        Q(
            registered_app=app_environment.registered_app,
            app_environment=app_environment,
        )
        | Q(
            attachments__app_environment=app_environment,
            attachments__deleted_at__isnull=True,
        ),
        deleted_at__isnull=True,
    ).distinct()


def _binding_workloads_for_service(service, app_environment) -> tuple[str, ...]:
    """Selectors for one consumer, with imperative rows staying universal."""
    if service.registered_app_id:
        values = list(getattr(service, "bind_workloads", None) or [])
    else:
        attachment = service.attachments.filter(
            app_environment=app_environment,
            deleted_at__isnull=True,
        ).first()
        values = list(getattr(attachment, "workload_names", None) or [])
    return tuple(values or ["*"])


def _binding_secret_refs_for_environment(app_environment) -> tuple[bool, dict[str, list[str]]]:
    """Return universal-secret presence plus per-workload secret references."""
    app_slug = app_environment.registered_app.slug
    universal = False
    by_workload: dict[str, list[str]] = {}
    for service in _managed_services_for_environment(app_environment).prefetch_related("attachments"):
        selectors = _binding_workloads_for_service(service, app_environment)
        if "*" in selectors:
            universal = True
            continue
        for workload in selectors:
            ref = _bindings_secret_name(app_slug, workload)
            by_workload.setdefault(workload, [])
            if ref not in by_workload[workload]:
                by_workload[workload].append(ref)
    return universal, by_workload


def _mark_app_provisioning_sync(registered_app_id: int) -> None:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    if app.provisioning_status == RegisteredApp.ProvisioningStatus.PROVISIONING.value:
        return
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.PROVISIONING)


@activity.defn(name="astrolift.app.mark_provisioning")
async def mark_app_provisioning(registered_app_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_app_provisioning_sync)(registered_app_id)


def _mark_app_ready_sync(registered_app_id: int) -> None:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.READY)


@activity.defn(name="astrolift.app.mark_ready")
async def mark_app_ready(registered_app_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_app_ready_sync)(registered_app_id)


def _provision_namespace_sync(registered_app_id: int, app_environment_id: int | None) -> str:
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import AppDeployError, namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    app = RegisteredApp.all_objects.select_related("organization").get(pk=registered_app_id)
    cluster: TenantCluster | None = None
    # Treat falsy ids (0, None) as "no explicit env" so the workflow can
    # pass a placeholder (0) without triggering a DoesNotExist lookup.
    if app_environment_id:
        env = AppEnvironment.all_objects.select_related("tenant_cluster").get(pk=app_environment_id)
        cluster = env.tenant_cluster
    if cluster is None:
        # Fall back: the app may have a single environment, in which case
        # we provision in that cluster. If there are zero or multiple
        # environments the caller must pass app_environment_id.
        envs = list(
            AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).select_related(
                "tenant_cluster",
            ),
        )
        if len(envs) != 1:
            raise AppDeployError(
                f"app {app.slug!r} has {len(envs)} environments; pass app_environment_id explicitly",
            )
        cluster = envs[0].tenant_cluster
    if cluster is None:
        raise AppDeployError(f"app {app.slug!r} env has no tenant_cluster bound")
    namespace = namespace_for_app(app)
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    labels = {
        "astrolift.io/managed-by": "astrolift",
        "astrolift.io/organization": app.organization.slug,
        "astrolift.io/app": app.slug,
    }
    annotations = {
        "astrolift.io/registered-app-id": str(app.pk),
    }
    driver.ensure_namespace(ctx.slug, namespace, labels, annotations)
    return namespace


@activity.defn(name="astrolift.app.provision_namespace")
async def provision_namespace(registered_app_id: int, app_environment_id: int | None = None) -> None:
    """Create the app's Kubernetes namespace on its bound cluster.

    Idempotent — ``driver.ensure_namespace`` server-side-applies labels +
    annotations so re-runs reconcile rather than fail. The labels carry
    organization + app slug so cluster-wide queries can scope to
    astrolift-managed namespaces.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    namespace = await sync_to_async(_provision_namespace_sync)(registered_app_id, app_environment_id)
    log.info(
        "provision_namespace ensured namespace=%s",
        namespace,
        extra={"registered_app_id": registered_app_id, "app_environment_id": app_environment_id},
    )


def _provision_registry_repo_sync(registered_app_id: int) -> str:
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import AppDeployError, driver_for_capability

    app = RegisteredApp.all_objects.select_related("organization").get(pk=registered_app_id)
    if app.registry_repo_uri:
        # Already provisioned — idempotent fast-path.
        return app.registry_repo_uri
    # Pick a cluster whose provider plugin registers a ``registry``
    # driver. The driver is plugin-scoped not cluster-scoped, but the
    # ImageRegistryDriver constructor takes the plugin config, so any
    # bound cluster works as long as the plugin is consistent.
    env = (
        AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True)
        .select_related("tenant_cluster__provider_plugin")
        .first()
    )
    if env is None or env.tenant_cluster is None:
        raise AppDeployError(
            f"app {app.slug!r} has no environment bound to a cluster — can't pick a registry plugin",
        )
    registry_driver = driver_for_capability(env.tenant_cluster, "registry")
    repo_name = f"{app.organization.slug}/{app.slug}"
    repo = registry_driver.ensure_repo(repo_name)
    app.registry_repo_uri = repo.uri

    # Provision (or refresh) the CI push role so the SCM provider's
    # runner can authenticate to the registry via OIDC — no long-lived
    # access keys.  The push code path already reads ``push_role_ref``
    # (see astrolift_scm/services/secrets.py: ASTROLIFT_PUSH_ROLE_ARN),
    # so populating the field here flips the CI workflow's
    # ``aws-actions/configure-aws-credentials`` step into OIDC mode on
    # the next ``pushAstroliftCiSecrets`` call.
    #
    # Skipped when:
    #  - the app has no source repo (manually-registered, no SCM)
    #  - the source kind isn't supported by the registry driver (today
    #    only github is wired in the AWS ECR driver — other SCMs raise
    #    UnsupportedOperationError, which we treat as "no-op for now"
    #    rather than failing the whole provision)
    if app.source_repo and hasattr(registry_driver, "ensure_ci_push_role"):
        try:
            from astrolift_scm.services.workflow_sync import github_repo_numeric_ids

            push_role = registry_driver.ensure_ci_push_role(
                repo=repo_name,
                scm_provider=app.source_kind,
                scm_repo_full_name=app.source_repo,
                scm_repo_numeric_ids=github_repo_numeric_ids(app),
            )
            app.push_role_ref = push_role.role_ref
        except Exception as exc:  # noqa: BLE001
            # Don't fail the whole provision on push-role failure —
            # the app can still be deployed via long-lived access keys
            # (legacy) and operators can re-run reprovision to retry.
            log.warning(
                "ensure_ci_push_role failed for %s: %s",
                app.slug,
                exc,
                extra={"registered_app_id": registered_app_id},
            )

    save_fields = ["registry_repo_uri", "updated_at", "version"]
    if app.push_role_ref:
        save_fields.insert(1, "push_role_ref")
    app.save(update_fields=save_fields)
    return repo.uri


@activity.defn(name="astrolift.app.provision_registry_repo")
async def provision_registry_repo(registered_app_id: int) -> str:
    """Create the app's container registry repository.

    Calls the provider plugin's ``ImageRegistryDriver.ensure_repo`` with
    ``<org-slug>/<app-slug>`` and persists the returned URI onto
    ``RegisteredApp.registry_repo_uri`` so subsequent renders use it as
    the image repository. Idempotent — re-runs return the existing URI.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    uri = await sync_to_async(_provision_registry_repo_sync)(registered_app_id)
    log.info("provision_registry_repo uri=%s", uri, extra={"registered_app_id": registered_app_id})
    return uri


def _resync_manifest_for_deploy_sync(deployment_id: int) -> dict:
    from astrolift_lifecycle.models import Deployment
    from astrolift_registry.services.manifest_sync import resync_app_manifest_from_repo

    deployment = Deployment.objects.select_related("registered_app").get(pk=deployment_id)
    app = deployment.registered_app
    result = resync_app_manifest_from_repo(app)

    # Record the outcome on the deployment (#1553). Without this the only
    # trace of a refused resync is a worker log line, so a deploy that
    # rendered a stale manifest looks identical to one that rendered a
    # fresh one. Written here rather than in the workflow because the
    # workflow sandbox cannot touch the ORM.
    deployment.manifest_resync_status = result.status
    deployment.manifest_resync_error = result.error or ""
    # "version" belongs in update_fields: BaseCoreModel.save() increments it
    # on every save, and leaving it out computes the bump then discards it.
    deployment.save(
        update_fields=[
            "manifest_resync_status",
            "manifest_resync_error",
            "updated_at",
            "version",
        ]
    )

    return {
        "status": result.status,
        "error": result.error or "",
        "app_slug": app.slug,
        # Zero workloads after a refused resync is the shape of the bug in
        # #1553: the deploy would go green having deployed nothing. The
        # workflow uses this to fail pre-flight loudly instead.
        "workload_count": app.workloads.filter(deleted_at__isnull=True).count(),
    }


@activity.defn(name="astrolift.app.resync_manifest_for_deploy")
async def resync_manifest_for_deploy(deployment_id: int) -> dict:
    """Refresh the app's manifest from its source repo before render (#1535).

    The deploy path renders from the DB registration, which nothing
    refreshed automatically — a repo manifest fixed after registration was
    never picked up until an operator clicked the Settings resync button,
    so 'fix the TOML and push' deploys silently kept the stale config.

    Best-effort by contract: ``resync_app_manifest_from_repo`` never
    raises. ``applied``/``in_sync`` proceed with fresh state; ``diverged``
    keeps the operator's staged draft (clobbering it is the destructive
    sync's job, not a deploy's); ``fetch_failed`` logs loudly and the
    deploy proceeds with the last-known-good registration.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    outcome = await sync_to_async(_resync_manifest_for_deploy_sync)(deployment_id)
    if outcome["status"] in ("applied", "in_sync"):
        log.info(
            "resync_manifest_for_deploy app=%s status=%s",
            outcome["app_slug"],
            outcome["status"],
            extra={"deployment_id": deployment_id},
        )
    else:
        log.warning(
            "resync_manifest_for_deploy app=%s status=%s error=%s — deploying with the stored manifest",
            outcome["app_slug"],
            outcome["status"],
            outcome["error"],
            extra={"deployment_id": deployment_id},
        )
    return outcome


def _provision_managed_services_initial_sync(
    registered_app_id: int,
    app_environment_id: int,
) -> list[int]:
    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from astrolift_drivers.registry import DriverNotFound
    from astrolift_services.models import ManagedService
    from core.app_deploy import AppDeployError
    from core.cluster_observability import managed_config_for

    rows = list(
        ManagedService.objects.filter(
            registered_app_id=registered_app_id,
            app_environment_id=app_environment_id,
            deleted_at__isnull=True,
        ).select_related("app_environment__tenant_cluster__provider_plugin"),
    )
    if not rows:
        return []
    provisioned: list[int] = []
    for ms in rows:
        cluster = ms.app_environment.tenant_cluster
        if cluster is None:
            raise AppDeployError(
                f"managed service {ms.pk} env has no tenant_cluster bound",
            )
        plugin_slug = cluster.provider_plugin.slug
        variant = getattr(ms, "variant", "") or ""
        try:
            resolved = resolve_managed_driver(
                cluster_plugin_slug=plugin_slug,
                kind=ms.kind,
                variant=variant,
            )
        except DriverNotFound as exc:
            raise AppDeployError(f"cluster {cluster.slug}: {exc}") from exc
        cfg = managed_config_for(resolved.plugin_slug, cluster, kind=ms.kind, variant=variant)
        driver = resolved.driver_cls(config=cfg)
        # ManagedServiceDriver.provision takes a ProvisionSpec dataclass
        # built from the ManagedService row's stored spec dict. Drivers
        # return a ProvisionResult with the external reference id, which
        # we persist for later observability + teardown.
        spec_payload = getattr(ms, "spec", {}) or {}
        result = driver.provision(spec=spec_payload)
        ref = getattr(result, "ref", "") or getattr(result, "id", "") or getattr(result, "name", "")
        if ref:
            ms.backend_ref = str(ref)
            ms.save(update_fields=["backend_ref", "updated_at", "version"])
        provisioned.append(ms.pk)
    return provisioned


@activity.defn(name="astrolift.app.provision_managed_services_initial")
async def provision_managed_services_initial(
    registered_app_id: int,
    app_environment_id: int,
) -> list[int]:
    """Provision the app's bound managed services (DBs, caches, queues).

    For each ``ManagedService`` row attached to (app, env), resolve the
    plugin-scoped driver (``(kind, variant)`` → ``ManagedServiceDriver``),
    call ``provision(name, spec)``, and persist the returned backend
    reference for later observability. Apps with no managed services
    no-op cleanly. The cluster's ``provider_plugin`` determines which
    driver runs.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    ids = await sync_to_async(_provision_managed_services_initial_sync)(
        registered_app_id,
        app_environment_id,
    )
    log.info(
        "provision_managed_services_initial provisioned %d service(s)",
        len(ids),
        extra={"registered_app_id": registered_app_id, "app_environment_id": app_environment_id},
    )
    return ids


_PODLESS_WORKLOAD_KINDS = {"static_site", "faas"}


def _has_podless_workload(app) -> bool:
    """True when the app's manifest declares a pod-less workload (#1010, #987).

    A pod-less workload (``static_site`` → S3 + CDN, ``faas`` → cloud-run
    Lambda) renders to zero K8s resources — its backing resources are managed
    services, not pods — so the pre-flight zero-resources gate must not treat
    it as an empty manifest. Best-effort: a manifest that won't parse falls
    through to the normal gate."""
    try:
        from astrolift_manifest.normalize import NormalizationDefaults, normalize
        from astrolift_manifest.parser import parse_raw

        manifest = normalize(parse_raw(app.manifest_raw or ""), defaults=NormalizationDefaults())
    except Exception:  # noqa: BLE001 — unparseable manifest: defer to the normal gate
        return False
    return any(getattr(w, "kind", "") in _PODLESS_WORKLOAD_KINDS for w in manifest.workloads)


def _pre_flight_sync(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import (
        AppDeployError,
        cluster_for_deployment,
        render_resources_for_deployment,
    )

    d = Deployment.all_objects.select_related("registered_app__organization", "app_environment").get(
        pk=deployment_id,
    )
    if not d.image_tag:
        raise AppDeployError(f"deployment {deployment_id} has no image_tag set")
    app = d.registered_app
    if not (app.manifest_raw or "").strip():
        raise AppDeployError(
            f"app {app.slug!r} has no saved manifest — open the Manifest tab or re-run the registration wizard",
        )
    # Probe the cluster is bound + reachable shape; the actual auth
    # check happens at apply time when the driver opens a real client.
    cluster = cluster_for_deployment(d)
    if cluster.lifecycle != cluster.Lifecycle.MANAGED.value:
        raise AppDeployError(
            f"cluster {cluster.slug!r} is in lifecycle {cluster.lifecycle!r}, not managed — "
            "bring it into management before deploying",
        )
    # Catch the empty-[[workloads]] case here so operators see a clear
    # pre-deploy error rather than an opaque apply failure later (#359).
    # Exception: a pod-less-only app (static_site #1010 → S3 + CloudFront,
    # faas #987 → cloud-run Lambda) legitimately renders zero K8s resources —
    # its "resources" are managed services provisioned by the topology's deploy
    # activities, not pods. Only a manifest with NO pod-less workload AND zero
    # rendered resources is an error (the truly-empty [[workloads]] case).
    resources = render_resources_for_deployment(d)
    if not resources and not _has_podless_workload(app):
        raise AppDeployError(
            "manifest renders to zero Kubernetes resources — declare at least one workload in [[workloads]]",
        )


@activity.defn(name="astrolift.deploy.pre_flight")
async def pre_flight(deployment_id: int) -> None:
    """Read-only validation gate before any cluster mutation.

    Catches the obvious "you can't deploy because X" cases up front so
    operators get a clear pre-deploy error rather than an opaque apply
    failure. Validates: deployment has an image_tag, app has a saved
    manifest, env is bound to a cluster, and the cluster is in the
    ``managed`` lifecycle.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_pre_flight_sync)(deployment_id)


def _mark_deploying_sync(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment

    d = Deployment.all_objects.select_related("registered_app__organization", "app_environment").get(
        pk=deployment_id
    )
    d.transition_to(Deployment.Status.DEPLOYING)
    # Best-effort GitHub reflection AFTER the transition commits (#1124).
    # reflect_* already swallows every error; the extra guard here means a
    # bug in the reflection path can still never fail the deploy activity.
    try:
        from astrolift_lifecycle.github_reflection import reflect_deploy_started

        reflect_deploy_started(d)
    except Exception:  # noqa: BLE001
        log.warning("github reflect_deploy_started errored for deploy %s", deployment_id, exc_info=True)


@activity.defn(name="astrolift.deploy.mark_deploying")
async def mark_deploying(deployment_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_deploying_sync)(deployment_id)


@activity.defn(name="astrolift.deploy.render_manifests")
async def render_manifests(deployment_id: int) -> dict[str, Any]:
    """Build the list of Kubernetes resource dicts for ``apply_manifests``.

    Reads the Deployment row, normalizes the registered-app's stored
    manifest, and runs the pure renderer in
    ``astrolift_manifest.render``. The rendered output is returned as
    ``{"resources": [...]}`` so the workflow has a single-key Temporal
    payload (stable across renderer evolution) and so ``apply_manifests``
    can pick up the same shape unchanged.

    Auto-injects ``envFrom: secretRef:`` for every active
    ``AppSecretBundleRef`` plus the synthesized
    ``astrolift-bindings-<app-slug>`` Secret holding managed-service
    connection envelopes — so workloads get DATABASE_URL / REDIS_URL /
    etc. without the operator having to wire each var by hand.
    """
    from asgiref.sync import sync_to_async

    from astrolift_lifecycle.models import Deployment
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.parser import parse_raw
    from astrolift_manifest.render import render_manifests as _render

    activity.heartbeat()

    def _gather():
        from astrolift_services.models import AppSecretBundleRef

        d = Deployment.all_objects.select_related("registered_app", "app_environment").get(pk=deployment_id)
        app = d.registered_app
        env = d.app_environment
        # Render off the stored TOML — never re-fetch from the repo at
        # apply time so deployments are reproducible after force-pushes.
        manifest = normalize(parse_raw(app.manifest_raw), defaults=NormalizationDefaults())

        # Build the envFrom list: operator-authored bundles first
        # (predictable, debuggable, lexicographic), then the platform-
        # synthesized bindings Secret so binding keys can shadow a
        # bundle on intentional collisions (e.g., operator overrides
        # DATABASE_URL).
        bundle_secret_names = sorted(
            AppSecretBundleRef.objects.filter(
                registered_app=app,
                app_environment=env,
                deleted_at__isnull=True,
            ).values_list("secret_bundle__slug", flat=True),
        )
        has_bindings, workload_env_from = _binding_secret_refs_for_environment(env)
        env_from = list(bundle_secret_names)
        if has_bindings:
            env_from.append(_bindings_secret_name(app.slug))

        # Resolve the namespace here, not after the await (#1577).
        # namespace_for_app reads app.organization.slug whenever
        # k8s_namespace is unpinned, which is every app the platform
        # creates -- only imported agents set it. That is a lazy FK
        # load, and Django refuses one from the event loop. Computing
        # it inside _gather keeps the ORM access in the sync context
        # and, unlike widening select_related, cannot be undone by a
        # later edit to the query above.
        from core.app_deploy import namespace_for_app

        return manifest, app, env, d, env_from, workload_env_from, namespace_for_app(app)

    manifest, app, env, d, env_from, workload_env_from, namespace = await sync_to_async(_gather)()

    resources = _render(
        manifest,
        app_slug=app.slug,
        namespace=namespace,
        image_tag=d.image_tag or "latest",
        image_repository=app.registry_repo_uri or app.slug,
        image_digest=d.image_digest,
        environment_name=env.name,
        env_from_secret_refs=env_from,
        workload_env_from_secret_refs=workload_env_from,
    )

    # Fold in CustomDomain Ingress + TLS Secret resources (#397). Each
    # validated + active/byo domain gets its own Ingress so per-host
    # TLS strategy choices stay isolated — auto-issued ACM lives on the
    # cluster's cert-manager / cloud cert manager, BYO PEM ships
    # inline as a kubernetes.io/tls Secret.
    ingress_resources = await sync_to_async(
        _render_app_ingresses_and_tls,
    )(d.pk, namespace, manifest)
    if ingress_resources:
        resources = sorted(
            [*resources, *ingress_resources],
            key=lambda r: (r.get("kind", ""), r["metadata"]["name"]),
        )

    # Fold in the app's NetworkPolicy, when it has opted into one (#1599).
    # Same shape as the Ingress fold-in above, and the same reason it is a
    # list: an app with no policy contributes nothing and needs no special
    # case here.
    #
    # `render_app_network_policy` returns empty unless
    # `app.network_policy["enabled"]` is set, which is every app today. The
    # policy is deny-by-default, so emitting one for an app that never had it
    # cuts every egress nobody declared -- opting in has to be deliberate.
    policy_resources = await sync_to_async(_render_app_network_policy)(d.pk, namespace)
    if policy_resources:
        resources = sorted(
            [*resources, *policy_resources],
            key=lambda r: (r.get("kind", ""), r["metadata"]["name"]),
        )

    log.info(
        "render_manifests produced %d resource(s) with envFrom=%s",
        len(resources),
        env_from,
        extra={"deployment_id": deployment_id},
    )
    return {
        "resources": resources,
        "env_from_secret_refs": env_from,
        "workload_env_from_secret_refs": workload_env_from,
    }


def _render_app_network_policy(deployment_id: int, namespace: str) -> list[dict]:
    """Sync half of the NetworkPolicy fold-in (#1599).

    Its own function so the FK read happens in the sync context -- the same
    reason `_render_app_ingresses_and_tls` is one, and the same trap #1577
    was about: Django refuses a lazy FK load from the event loop.
    """
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.network_policy import render_app_network_policy

    d = Deployment.all_objects.select_related("registered_app").get(pk=deployment_id)
    return render_app_network_policy(d.registered_app, namespace=namespace)


def _render_app_ingresses_and_tls(
    deployment_id: int,
    namespace: str,
    manifest,
) -> list[dict[str, Any]]:
    """Emit Ingress resources for both CustomDomains and the platform-
    assigned managed subdomain, plus TLS Secrets for BYO certs.

    **CustomDomains** — one Ingress per validated + active custom
    domain. Skips domains in non-serving cert states. BYO certs ship
    as inline ``kubernetes.io/tls`` Secrets; platform-managed zones
    (ACM / GCM / Azure) reference the cert by annotation.

    **Managed subdomain** — every ``AppEnvironment`` with a
    ``managed_domain`` gets an Ingress for its platform-assigned
    hostname (``{app}.{org}.{base-zone}``). This ensures external-dns
    and the cloud LB controller (e.g. AWS LBC) converge on the right
    DNS record + TLS cert automatically — no operator action required.
    For ALB clusters the wildcard ACM cert ARN comes from
    ``managed_domain.dns_config["certificate_arn"]``. For other
    ingress classes (nginx, traefik) cert-manager handles issuance.
    """
    import base64

    from astrolift_lifecycle.models import CustomDomain, Deployment

    d = Deployment.all_objects.select_related(
        "registered_app",
        "registered_app__organization",
        "app_environment",
        "app_environment__managed_domain",
        "app_environment__tenant_cluster",
    ).get(pk=deployment_id)

    # Find the public-facing workload + its port. If the app has no
    # deployment workload with an exposed port we can't route to it,
    # so no ingress.
    primary_workload = next(
        (w for w in manifest.workloads if w.kind == "deployment"),
        None,
    )
    if primary_workload is None or not primary_workload.containers:
        return []
    primary_container = next(
        (c for c in primary_workload.containers if c.is_primary),
        primary_workload.containers[0],
    )
    if primary_container.port <= 0:
        return []
    backend_port = int(primary_container.port)
    backend_service = primary_workload.name

    # Operator pause is per-environment, evaluated once per render and
    # reflected on every Ingress this deploy emits. We keep emitting
    # the Ingress (don't skip it) so operators see the paused state
    # via ``kubectl get ingress -L astrolift.dev/ingress-state``
    # instead of a 404, and so the controller serves a clear 503 with
    # an Astrolift-branded message instead of the app.
    ingress_paused = bool(getattr(d.app_environment, "ingress_paused", False))
    ingress_state_label = "paused" if ingress_paused else "live"

    # Edge auth state for this cluster's custom domains (#1621). Computed
    # once per render because it is a function of the cluster + hostname,
    # not of the deployment.
    #
    # The custom-domain Ingresses below deliberately carry NO auth
    # annotations, even on a cluster whose managed subdomains are gated.
    # The central auth host's session cookie is scoped to the parent zone
    # of the auth host, and a response from ``auth.<base-zone>`` cannot set
    # a cookie for an unrelated registrable domain -- a browser rule, not
    # an oauth2-proxy flag. Stamping the annotations here would send every
    # request to the auth host, succeed, come back with still no cookie for
    # this host, and loop: "no gate" would become "infinite redirect".
    #
    # So the gap is recorded rather than papered over. Each Ingress carries
    # ``astrolift.dev/edge-auth`` so ``kubectl get ingress -L
    # astrolift.dev/edge-auth`` shows it, and the same value is on
    # ``AppDomainType.edge_auth_state`` for the UI. Gating an external
    # domain for real needs a first-party ``/oauth2/*`` endpoint on the
    # domain itself; see custom_domain_edge_auth_state.
    from core.app_deploy import custom_domain_edge_auth_state

    edge_cluster = (
        d.app_environment.tenant_cluster
        if d.app_environment and d.app_environment.tenant_cluster_id
        else None
    )

    out: list[dict[str, Any]] = []
    domains = CustomDomain.objects.filter(
        registered_app=d.registered_app,
        deleted_at__isnull=True,
        is_active=True,
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
    )
    for cd in domains:
        state = cd.certificate_state
        if state not in (
            CustomDomain.CertificateState.ACTIVE,
            CustomDomain.CertificateState.BYO,
        ):
            continue
        host_slug = cd.hostname.replace(".", "-")
        secret_name = f"{d.registered_app.slug}-{host_slug}-tls"
        ingress_name = f"{d.registered_app.slug}-{host_slug}"
        annotations: dict[str, str] = {}

        # #1631: a domain that has opted in gets a **first-party** gate --
        # `auth-signin` points at this hostname's own `/oauth2/start`, not
        # at the central auth host.
        #
        # That difference is the whole issue. Pointing `auth-signin` at the
        # auth host is the obvious-looking fix and produces an infinite
        # redirect, because the session cookie is scoped to the auth host's
        # parent zone and a response from there cannot set a cookie for an
        # unrelated registrable domain. Pointed at this host, the cookie the
        # proxy sets is first-party and the gate holds.
        #
        # `auth-url` stays a sub-request against the same hostname, so a
        # request that already has the cookie never leaves the domain.
        if (
            custom_domain_edge_auth_state(
                edge_cluster,
                cd.hostname,
                opted_in=bool(getattr(cd, "edge_auth_enabled", False)),
            )
            == "gated"
        ):
            annotations["nginx.ingress.kubernetes.io/auth-url"] = f"https://{cd.hostname}/oauth2/auth"
            annotations["nginx.ingress.kubernetes.io/auth-signin"] = (
                f"https://{cd.hostname}/oauth2/start?rd=$escaped_request_uri"
            )

        if ingress_paused:
            # nginx-ingress-controller honors server-snippet to inject
            # raw nginx config into the per-host server block; a bare
            # 503 short-circuits before reaching the upstream service.
            # The TLS Secret + handshake stay intact so the cert isn't
            # invalidated and resuming is a no-op render away.
            annotations["nginx.ingress.kubernetes.io/server-snippet"] = (
                'return 503 "Astrolift: app is paused";'
            )

        if state == CustomDomain.CertificateState.BYO:
            # Split the stored bundle into cert chain + private key on
            # the LAST ``END CERTIFICATE`` marker. The upload mutation
            # validated both halves are PEM-looking; the renderer
            # ships them as a ``kubernetes.io/tls`` Secret.
            bundle = cd.byo_certificate_pem or ""
            end_marker = "-----END CERTIFICATE-----"
            idx = bundle.rfind(end_marker)
            if idx == -1:
                # Stored bundle is corrupted — skip rather than emit a
                # broken secret. The cert-state UI shows BYO + the
                # next recheck will surface the issue.
                continue
            chain = bundle[: idx + len(end_marker)].strip() + "\n"
            key = bundle[idx + len(end_marker) :].lstrip()
            out.append(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "kubernetes.io/tls",
                    "metadata": {
                        "name": secret_name,
                        "namespace": namespace,
                        "labels": {
                            "astrolift.dev/app": d.registered_app.slug,
                            "astrolift.dev/custom-domain": cd.hostname,
                            "astrolift.dev/cert-source": "byo",
                        },
                    },
                    "data": {
                        "tls.crt": base64.b64encode(chain.encode("utf-8")).decode("ascii"),
                        "tls.key": base64.b64encode(key.encode("utf-8")).decode("ascii"),
                    },
                }
            )
        elif state == CustomDomain.CertificateState.ACTIVE:
            if not cd.is_platform_managed_zone:
                # External zone, auto-issued: nudge cert-manager to
                # solve HTTP-01 on the first apply. (No-op if the
                # cluster's cert-manager already owns the Secret.)
                annotations["cert-manager.io/cluster-issuer"] = "letsencrypt-prod"

        out.append(
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "Ingress",
                "metadata": {
                    "name": ingress_name,
                    "namespace": namespace,
                    "annotations": annotations,
                    "labels": {
                        "astrolift.dev/app": d.registered_app.slug,
                        "astrolift.dev/custom-domain": cd.hostname,
                        "astrolift.dev/cert-source": (
                            "byo" if state == CustomDomain.CertificateState.BYO else "auto"
                        ),
                        "astrolift.dev/ingress-state": ingress_state_label,
                        "astrolift.dev/edge-auth": custom_domain_edge_auth_state(
                            edge_cluster,
                            cd.hostname,
                            opted_in=bool(getattr(cd, "edge_auth_enabled", False)),
                        ),
                    },
                },
                "spec": {
                    "tls": [{"hosts": [cd.hostname], "secretName": secret_name}],
                    "rules": [
                        {
                            "host": cd.hostname,
                            "http": {
                                "paths": [
                                    {
                                        "path": "/",
                                        "pathType": "Prefix",
                                        "backend": {
                                            "service": {
                                                "name": backend_service,
                                                "port": {"number": backend_port},
                                            },
                                        },
                                    },
                                ],
                            },
                        },
                    ],
                },
            }
        )

    # --- Managed subdomain Ingress ---
    # AppEnvironment.managed_domain provides the platform-assigned
    # hostname. Emit an Ingress for it unconditionally so external-dns
    # + the cluster's LB controller create the DNS record and attach
    # the TLS cert on every deploy without operator involvement.
    # Wildcard ACM cert ARN for ALB: managed_domain.dns_config["certificate_arn"].
    env = d.app_environment
    managed_domain = env.managed_domain if env and env.managed_domain_id else None
    cluster = env.tenant_cluster if env and env.tenant_cluster_id else None
    if managed_domain is not None and cluster is not None:
        from astrolift_manifest.hostname import HostnameInputs, compute_hostnames

        org_slug = d.registered_app.organization.slug if d.registered_app.organization_id else ""
        if org_slug:
            computed = compute_hostnames(
                manifest,
                HostnameInputs(
                    app_slug=d.registered_app.slug,
                    org_slug=org_slug,
                    base_zone=managed_domain.zone,
                ),
            )
            cert_arn: str | None = (
                managed_domain.dns_config.get("certificate_arn") if managed_domain.dns_config else None
            )
            if computed:
                if cluster.ingress_class == "alb":
                    from core.app_deploy import (
                        cognito_auth_for_cluster,
                        shared_ingress_annotations,
                    )
                    from providers.aws.ingress_alb import ALBConfig, ALBIngressDriver

                    # Same gate core.app_deploy renders. Omitting it here
                    # made an app's authentication depend on which render
                    # path ran -- the exact hazard the ALB-group comment
                    # below already warns about (#1539).
                    alb_cfg = ALBConfig(
                        region=cluster.region or "us-east-1",
                        certificate_arn=cert_arn,
                        cognito_auth=cognito_auth_for_cluster(cluster),
                    )
                    driver = ALBIngressDriver(config=alb_cfg)
                    tls_strategy = "acm_dns_validated" if cert_arn else "letsencrypt"
                    # Same grouping the other managed-subdomain renderer
                    # applies. Both have to stamp it or a shared-mode
                    # cluster ends up with the app in two ALB groups
                    # depending on which render path ran.
                    group_annotations = shared_ingress_annotations(
                        cluster,
                        org_slug=org_slug,
                        app_slug=d.registered_app.slug,
                    )
                    # One Ingress per workload; all hostnames for that
                    # workload go into its rules list (ALB LBC handles
                    # multi-host Ingress natively with one listener).
                    by_workload: dict[str, list[str]] = {}
                    for wh in computed:
                        by_workload.setdefault(wh.workload_slug, []).append(wh.hostname)
                    for workload_slug, hostnames in by_workload.items():
                        for rendered in driver.render_ingress(
                            app=d.registered_app.slug,
                            workload=workload_slug,
                            hostnames=hostnames,
                            tls_strategy=tls_strategy,
                        ):
                            rendered.setdefault("metadata", {})["namespace"] = namespace
                            rendered["metadata"].setdefault("labels", {})[
                                "astrolift.dev/managed-subdomain"
                            ] = "true"
                            rendered["metadata"]["labels"]["astrolift.dev/ingress-state"] = (
                                ingress_state_label
                            )
                            if group_annotations:
                                rendered["metadata"].setdefault("annotations", {}).update(
                                    group_annotations,
                                )
                            out.append(rendered)
                else:
                    # Generic Ingress for nginx, traefik, etc. All
                    # managed hostnames share one Ingress + one cert
                    # Secret (wildcard covers them all).
                    from core.app_deploy import oidc_auth_for_cluster
                    from providers.k8s_native.ingress import nginx_auth_annotations

                    all_hostnames = [wh.hostname for wh in computed]
                    managed_annotations: dict[str, str] = {}
                    # Route through the cluster's central auth host, same
                    # as K8sIngressDriver does on the other render path.
                    # These hostnames all sit under the auth host's
                    # parent zone, so the session cookie already covers
                    # them and no per-app registration is involved.
                    oidc_auth = oidc_auth_for_cluster(cluster)
                    if oidc_auth is not None:
                        managed_annotations.update(nginx_auth_annotations(oidc_auth))
                    if ingress_paused:
                        managed_annotations["nginx.ingress.kubernetes.io/server-snippet"] = (
                            'return 503 "Astrolift: app is paused";'
                        )
                    if not cert_arn:
                        # No pre-provisioned cert — let cert-manager
                        # issue via the cluster's ACME issuer.
                        managed_annotations["cert-manager.io/cluster-issuer"] = "letsencrypt-prod"
                    out.append(
                        {
                            "apiVersion": "networking.k8s.io/v1",
                            "kind": "Ingress",
                            "metadata": {
                                "name": f"{d.registered_app.slug}-managed",
                                "namespace": namespace,
                                "annotations": managed_annotations,
                                "labels": {
                                    "astrolift.dev/app": d.registered_app.slug,
                                    "astrolift.dev/managed-subdomain": "true",
                                    "astrolift.dev/ingress-state": ingress_state_label,
                                },
                            },
                            "spec": {
                                "ingressClassName": cluster.ingress_class,
                                "tls": [
                                    {
                                        "hosts": all_hostnames,
                                        "secretName": f"{d.registered_app.slug}-managed-tls",
                                    }
                                ],
                                "rules": [
                                    {
                                        "host": h,
                                        "http": {
                                            "paths": [
                                                {
                                                    "path": "/",
                                                    "pathType": "Prefix",
                                                    "backend": {
                                                        "service": {
                                                            "name": backend_service,
                                                            "port": {"number": backend_port},
                                                        },
                                                    },
                                                },
                                            ],
                                        },
                                    }
                                    for h in all_hostnames
                                ],
                            },
                        }
                    )

    return out


def _bindings_secret_name(app_slug: str, workload_name: str = "") -> str:
    """Synthetic k8s Secret name for the per-app managed-service
    connection envelope. Kept in one helper so the producer (in
    ``update_secrets``) and the consumer (``render_manifests``) can
    never drift apart.
    """
    from _sdk.k8s_naming import dns_label

    return dns_label("astrolift", "bindings", app_slug, workload_name or None)


def _apply_manifests_sync(deployment_id: int) -> dict[str, list[str]]:
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.direct_apply import DryRunFailed, apply_with_dry_run
    from core.app_deploy import (
        AppDeployError,
        driver_for_deployment,
        render_resources_for_deployment,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster",
        "app_environment__managed_domain",
    ).get(pk=deployment_id)
    driver, ctx, namespace = driver_for_deployment(d)
    resources = render_resources_for_deployment(d)
    if not resources:
        raise AppDeployError(
            f"manifest for app {d.registered_app.slug!r} rendered to zero resources — "
            "check the workloads/services block in astrolift.toml",
        )
    try:
        result = apply_with_dry_run(driver, ctx.slug, namespace, resources)
    except DryRunFailed as exc:
        # Gate, not a retry: the apiserver rejected the set on a server-side
        # dry-run, so nothing was mutated. Without it the driver's per-object
        # loop applies everything it can and reports the rejection only
        # afterwards, leaving the namespace half-updated.
        raise AppDeployError(
            f"dry-run rejected the manifest set for deployment {deployment_id}: " + "; ".join(exc.errors),
        ) from exc
    if result.errors:
        # ``ApplyResult.errors`` carries the driver's legacy ``Kind/name: exc``
        # strings (#603) so the message keeps the format the workflow log + UI
        # already render.
        raise AppDeployError(
            f"apply_manifests failed for deployment {deployment_id}: " + "; ".join(result.errors),
        )
    return {
        "created": list(result.created),
        "updated": list(result.updated),
        "unchanged": list(result.unchanged),
    }


@activity.defn(name="astrolift.deploy.apply_manifests")
async def apply_manifests(deployment_id: int) -> dict[str, list[str]]:
    """Apply the rendered manifest set to the deployment's cluster.

    Uses server-side apply (idempotent) via the cluster driver, gated on a
    server-side dry-run of the whole set (spec 07 §4) so a rejected object
    fails the deploy before any object is mutated. Returns the
    ``ApplyResult`` shape ({created/updated/unchanged}) so the workflow
    event log carries which resources actually changed for an
    operator-facing diff view.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_apply_manifests_sync)(deployment_id)
    log.info(
        "apply_manifests created=%d updated=%d unchanged=%d",
        len(summary["created"]),
        len(summary["updated"]),
        len(summary["unchanged"]),
        extra={"deployment_id": deployment_id},
    )
    return summary


def _update_secrets_sync(deployment_id: int) -> int:
    import base64

    from astrolift_lifecycle.models import Deployment
    from astrolift_services.models import (
        AppSecretBundleRef,
        ManagedServiceBinding,
        ManagedServiceVolumeBinding,
    )
    from core.app_deploy import (
        AppDeployError,
        driver_for_capability,
        driver_for_deployment,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster__provider_plugin",
    ).get(pk=deployment_id)
    cluster_driver, ctx, namespace = driver_for_deployment(d)

    refs = list(
        AppSecretBundleRef.objects.filter(
            registered_app=d.registered_app,
            app_environment=d.app_environment,
            deleted_at__isnull=True,
        ).select_related("secret_bundle"),
    )

    resources: list[dict[str, Any]] = []

    # ---- operator-authored secret bundles --------------------------
    if refs:
        secrets_backend = driver_for_capability(
            d.app_environment.tenant_cluster,
            "secrets",
        )
        for ref in refs:
            bundle = ref.secret_bundle
            kvs = secrets_backend.get(bundle.backend_ref)
            if kvs is None:
                raise AppDeployError(
                    f"secret bundle {bundle.slug!r} backend_ref "
                    f"{bundle.backend_ref!r} not found in secrets backend",
                )
            prefix = (ref.prefix or "").strip()
            data: dict[str, str] = {}
            for k, v in kvs.items():
                full_key = f"{prefix}{k}" if prefix else k
                data[full_key] = base64.b64encode(
                    str(v).encode("utf-8"),
                ).decode("ascii")
            resources.append(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": bundle.slug,
                        "namespace": namespace,
                        "labels": {
                            "astrolift.io/managed-by": "astrolift",
                            "astrolift.io/secret-bundle": bundle.slug,
                        },
                    },
                    "type": "Opaque",
                    "data": data,
                },
            )

    # ---- synthesized managed-service bindings Secret --------------
    # One universal Secret plus selector-scoped Secrets where the manifest
    # names particular workloads. Resolves the driver-side ValueRefs (literal
    # or secret_ref) into raw values so workloads see a flat env-var
    # surface — they don't need to know whether DATABASE_PASSWORD came
    # from Secrets Manager or was inlined.
    services = list(
        _managed_services_for_environment(d.app_environment).order_by("kind", "name"),
    )
    if services:
        bindings_by_secret: dict[str, dict[str, str]] = {}
        secrets_backend = driver_for_capability(
            d.app_environment.tenant_cluster,
            "secrets",
        )
        for svc in services:
            selectors = _binding_workloads_for_service(svc, d.app_environment)
            secret_names = (
                [_bindings_secret_name(d.registered_app.slug)]
                if "*" in selectors
                else [_bindings_secret_name(d.registered_app.slug, name) for name in selectors]
            )
            for secret_name in secret_names:
                bindings_by_secret.setdefault(secret_name, {})
            for binding in ManagedServiceBinding.objects.filter(
                managed_service=svc,
                deleted_at__isnull=True,
            ).order_by("env_key"):
                env_key = binding.env_key
                raw_value: str
                if binding.is_secret:
                    # env_value_ref is a secrets-backend reference (ARN
                    # or path); resolve via the cluster's secrets driver.
                    from _sdk.secrets import SecretReferenceError, resolve_secret_reference

                    try:
                        raw_value = resolve_secret_reference(
                            secrets_backend,
                            binding.env_value_ref,
                        )
                    except SecretReferenceError as exc:
                        raise AppDeployError(
                            f"binding {svc.kind}/{svc.name}#{env_key} has an invalid or ambiguous secret reference",
                        ) from exc
                    if raw_value is None:
                        raise AppDeployError(
                            f"binding {svc.kind}/{svc.name}#{env_key} "
                            f"references missing secret "
                            f"{binding.env_value_ref!r}",
                        )
                    if not raw_value:
                        raise AppDeployError(
                            f"binding {svc.kind}/{svc.name}#{env_key} resolved to an empty secret value",
                        )
                else:
                    raw_value = binding.env_value_ref
                encoded = base64.b64encode(
                    raw_value.encode("utf-8"),
                ).decode("ascii")
                for secret_name in secret_names:
                    # Last writer wins per spec 05 §10, independently within
                    # each selector scope.
                    bindings_by_secret[secret_name][env_key] = encoded

        for bindings_secret_name, bindings_data in sorted(bindings_by_secret.items()):
            resources.append(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": bindings_secret_name,
                        "namespace": namespace,
                        "labels": {
                            "astrolift.io/managed-by": "astrolift",
                            "astrolift.io/bindings-for": d.registered_app.slug,
                        },
                        "annotations": {
                            "astrolift.io/binding-count": str(len(bindings_data)),
                        },
                    },
                    "type": "Opaque",
                    "data": bindings_data,
                },
            )

        from astrolift_services.filesystem_bindings import (
            FilesystemBindingError,
            resolve_binding_secret_manifests,
        )

        volume_bindings = list(
            ManagedServiceVolumeBinding.objects.filter(
                managed_service__in=services,
                managed_service__status__in=["active", "updating"],
                deleted_at__isnull=True,
            )
            .select_related("managed_service")
            .order_by("managed_service__name", "name"),
        )
        try:
            resources.extend(
                resolve_binding_secret_manifests(
                    volume_bindings,
                    secrets_backend=secrets_backend,
                    namespace=namespace,
                    consumer_key=str(d.app_environment.guid),
                ),
            )
        except FilesystemBindingError as exc:
            raise AppDeployError(str(exc)) from exc

    if not resources:
        return 0
    result = cluster_driver.apply_manifests(ctx.slug, namespace, resources)
    if not result.ok:
        # ``result.errors`` is ``list[ApplyError]`` (#603); ``.summary()``
        # preserves the legacy ``Kind/name: exc`` string list.
        raise AppDeployError(
            f"update_secrets apply failed for deployment {deployment_id}: " + "; ".join(result.summary()),
        )
    return len(resources)


@activity.defn(name="astrolift.deploy.update_secrets")
async def update_secrets(deployment_id: int) -> int:
    """Materialize the app's secret bundles into Kubernetes Secrets.

    Each ``AppSecretBundleRef`` for (app, env) becomes one k8s Secret
    named after the SecretBundle's slug, with values fetched from the
    platform secrets backend driver and base64-encoded. Apps with no
    secret bundles configured no-op cleanly.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    n = await sync_to_async(_update_secrets_sync)(deployment_id)
    log.info("update_secrets materialized %d bundle(s)", n, extra={"deployment_id": deployment_id})
    return n


def _wait_dns_sync(deployment_id: int, timeout_seconds: int) -> int:
    import socket
    import time

    from astrolift_lifecycle.models import CustomDomain, Deployment
    from core.app_deploy import AppDeployError

    d = Deployment.all_objects.select_related("registered_app", "app_environment").get(pk=deployment_id)
    hostnames: list[str] = []
    # AppEnvironment.managed_domain — the platform-issued subdomain.
    md = d.app_environment.managed_domain
    if md is not None:
        host = getattr(md, "hostname", "") or getattr(md, "fqdn", "")
        if host:
            hostnames.append(host)
    # Per-app custom domains (CustomDomain is app-scoped, not env-scoped).
    for cd in CustomDomain.objects.filter(
        registered_app=d.registered_app,
        deleted_at__isnull=True,
    ):
        host = getattr(cd, "hostname", "")
        if host:
            hostnames.append(host)

    if not hostnames:
        return 0

    deadline = time.monotonic() + timeout_seconds
    pending = list(hostnames)
    while pending and time.monotonic() < deadline:
        still: list[str] = []
        for host in pending:
            try:
                socket.gethostbyname(host)
            except OSError:
                still.append(host)
        pending = still
        if pending:
            time.sleep(5)
    if pending:
        raise AppDeployError(
            f"DNS did not propagate within {timeout_seconds}s for: {', '.join(pending)}",
        )
    return len(hostnames)


@activity.defn(name="astrolift.deploy.wait_dns")
async def wait_dns(deployment_id: int, timeout_seconds: int = 300) -> int:
    """Wait for the app's hostnames to resolve via DNS.

    Covers the managed-domain hostname + every CustomDomain bound to
    this (app, env). Apps without any domains exit immediately. Uses
    stdlib ``socket.gethostbyname`` against the local resolver — same
    view a browser hitting the load balancer will get. Loop polls
    every 5s up to ``timeout_seconds``.

    Default raised to 300s because Route53 propagation + new managed-
    domain setup commonly exceeds 2 min on first deploy (#359).
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    n = await sync_to_async(_wait_dns_sync)(deployment_id, timeout_seconds)
    log.info("wait_dns resolved %d hostname(s)", n, extra={"deployment_id": deployment_id})
    return n


def _poll_rollout_sync(deployment_id: int, timeout_seconds: int) -> bool:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import (
        AppDeployError,
        driver_for_deployment,
        render_resources_for_deployment,
        workloads_from_resources,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster",
    ).get(pk=deployment_id)
    driver, ctx, namespace = driver_for_deployment(d)
    resources = render_resources_for_deployment(d)
    workloads = workloads_from_resources(resources)
    if not workloads:
        # Pure-service manifests (no Deployments/StatefulSets/DaemonSets)
        # are trivially "rolled out" the moment apply_manifests returns.
        return True
    for kind, name in workloads:
        result = driver.poll_rollout(ctx.slug, namespace, kind, name, timeout_seconds)
        if not result.success:
            raise AppDeployError(
                f"rollout {kind}/{name} in {namespace} failed: {result.message}"
                + (" (timed out)" if result.timed_out else ""),
            )
    return True


@activity.defn(name="astrolift.deploy.poll_rollout")
async def poll_rollout(deployment_id: int, timeout_seconds: int = 600) -> bool:
    """Wait for every Deployment / StatefulSet / DaemonSet in the
    rendered manifest set to roll out successfully.

    Driven by the cluster driver's ``poll_rollout`` which ticks at least
    every 15s. Activity timeout in the workflow caps the overall window;
    ``timeout_seconds`` is the per-workload cap (default 10 min, matches
    the ClusterDriver protocol default).
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_rollout_sync)(deployment_id, timeout_seconds)


def _health_check_sync(deployment_id: int) -> bool:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import (
        AppDeployError,
        driver_for_deployment,
        render_resources_for_deployment,
        workloads_from_resources,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster",
    ).get(pk=deployment_id)
    driver, ctx, namespace = driver_for_deployment(d)
    resources = render_resources_for_deployment(d)
    workloads = workloads_from_resources(resources)
    if not workloads:
        return True
    for kind, name in workloads:
        status = driver.get_workload_status(ctx.slug, namespace, kind, name)
        if status.ready_replicas < status.desired_replicas:
            raise AppDeployError(
                f"workload {kind}/{name} in {namespace} not healthy: "
                f"ready={status.ready_replicas} desired={status.desired_replicas}",
            )
    return True


@activity.defn(name="astrolift.deploy.health_check")
async def health_check(deployment_id: int) -> bool:
    """Post-rollout readiness check.

    Cross-verifies what ``poll_rollout`` saw — every workload's
    ``ready_replicas`` should equal its ``desired_replicas`` at this
    point. Catches edge cases where rollout returns success but a pod
    flapped in the brief window before we sampled.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_health_check_sync)(deployment_id)


def _mark_running_sync(deployment_id: int) -> None:
    from django.db import transaction

    from astrolift_lifecycle.models import Deployment

    # Temporal activities are at-least-once, so this must be atomic AND
    # idempotent. A newly-live deploy supersedes the prior live one for
    # the same (app, env): RUNNING means "successfully live", not "in
    # progress", so leaving old running rows in place floods the Active
    # tab with every historical deploy.
    #
    # The supersede scan and the self-transition run inside one
    # transaction with select_for_update on the target AND the sibling
    # running rows, so two deploys racing to running contend on the prior
    # live row instead of both ending RUNNING. Net guarantee: after this
    # activity exactly one RUNNING deploy exists for (app, env) — the
    # target — regardless of retries or races.
    with transaction.atomic():
        d = Deployment.all_objects.select_for_update().get(pk=deployment_id)

        # Lock + re-scan the sibling running rows inside the same
        # transaction so a concurrent deploy serializes on the prior-live
        # row rather than both superseding a stale snapshot.
        prior_running = (
            Deployment.all_objects.select_for_update()
            .filter(
                registered_app_id=d.registered_app_id,
                app_environment_id=d.app_environment_id,
                status=Deployment.Status.RUNNING.value,
            )
            .exclude(pk=d.pk)
        )
        for prior in prior_running:
            # Guard: only supersede rows still RUNNING. The filter already
            # restricts to RUNNING, but stay defensive so an already-
            # terminal / already-superseded sibling is skipped, never
            # re-transitioned (SUPERSEDED has no outgoing transitions).
            if prior.status == Deployment.Status.RUNNING.value:
                prior.transition_to(Deployment.Status.SUPERSEDED)

        # Idempotent no-op on a retry: RUNNING is not a legal
        # self-transition, so only advance a row that isn't already live.
        # The supersede invariant above still holds on the re-run.
        if d.status != Deployment.Status.RUNNING.value:
            d.transition_to(Deployment.Status.RUNNING)

        # Record what this deploy actually put live (#1603). Every other
        # write of config_snapshot copies it forward from a prior or source
        # deployment, so the field propagated its `{}` default forever and
        # `build_config_drift` compared against nothing: its guard reads
        # `if snapshot_hash and current_hash and ...`, which an empty
        # snapshot can never satisfy. The manifest-hash and image-tag halves
        # of the drift banner have therefore never fired, silently -- it
        # fails closed, so the operator sees no banner rather than a wrong
        # one, which is why nobody noticed.
        #
        # Here, not at deploy creation: the snapshot has to describe what
        # went live. A deploy that fails must leave the previous snapshot
        # standing, or drift would be measured against config that never
        # ran.
        _write_config_snapshot(d)

    # Best-effort GitHub reflection, OUTSIDE the transaction so a slow
    # GitHub API never holds the select_for_update row locks (#1124).
    try:
        from astrolift_lifecycle.github_reflection import reflect_deploy_succeeded

        reflect_deploy_succeeded(d)
    except Exception:  # noqa: BLE001
        log.warning("github reflect_deploy_succeeded errored for deploy %s", deployment_id, exc_info=True)


def _write_config_snapshot(deployment) -> None:
    """Snapshot the config this deploy put live, on the deployment row.

    ``manifest_hash`` is the *app's* hash -- the normalised astrolift.toml
    via ``astrolift_manifest.normalize`` -- and deliberately not the rendered
    manifest-set hash. ``direct_apply`` documents why: the drift banner
    compares this key against ``RegisteredApp.manifest_hash``, so storing the
    hash of rendered k8s objects here would make the banner fire on every app
    forever.

    Merged onto whatever the row already carries rather than replacing it, so
    a snapshot copied forward from a prior deploy keeps any keys this does
    not own.
    """
    app = deployment.registered_app
    snapshot = dict(deployment.config_snapshot or {})
    snapshot["manifest_hash"] = (getattr(app, "manifest_hash", "") or "").strip()
    snapshot["image_tag"] = (deployment.image_tag or "").strip()
    deployment.config_snapshot = snapshot
    deployment.save(update_fields=["config_snapshot", "updated_at", "version"])


@activity.defn(name="astrolift.deploy.mark_running")
async def mark_running(deployment_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_running_sync)(deployment_id)


def _mark_failed_sync(deployment_id: int, reason: str) -> None:
    from astrolift_lifecycle.models import Deployment

    d = Deployment.all_objects.get(pk=deployment_id)
    # Idempotent: a deployment already in a terminal state (e.g. aborted
    # then failed) shouldn't raise on a redundant transition.
    if d.status == Deployment.Status.FAILED:
        return
    log.warning("deploy %s marked failed: %s", deployment_id, (reason or "")[:500])
    # #1093: persist WHY on the row — pre-pipeline refusals (pre_flight)
    # and exhausted-retry failures were flipping to FAILED with an empty
    # aborted_reason, leaving nothing for the history sidebar. A reason
    # already written (e.g. an operator reject) wins over the workflow's.
    if reason and not d.aborted_reason:
        d.aborted_reason = reason
        d.save(update_fields=["aborted_reason", "updated_at", "version"])
    d.transition_to(Deployment.Status.FAILED)
    # Best-effort GitHub reflection AFTER the transition commits (#1124).
    try:
        from astrolift_lifecycle.github_reflection import reflect_deploy_failed

        reflect_deploy_failed(d, reason)
    except Exception:  # noqa: BLE001
        log.warning("github reflect_deploy_failed errored for deploy %s", deployment_id, exc_info=True)


@activity.defn(name="astrolift.deploy.mark_failed")
async def mark_failed(deployment_id: int, reason: str = "") -> None:
    """Terminal-failure transition for a deploy that can't complete
    (rollout timed out / failed, or an activity exhausted its retries).

    Lets ``DeployAppWorkflow`` exit cleanly instead of looping a poll
    forever — the leak + worker-saturation source in #1004.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_failed_sync)(deployment_id, reason)


# ---- Preview lifecycle (build + teardown) -------------------------


def _mark_preview_building_sync(preview_environment_id: int) -> None:
    from astrolift_lifecycle.models import PreviewEnvironment

    p = PreviewEnvironment.objects.get(pk=preview_environment_id)
    if p.status in (
        PreviewEnvironment.Status.RUNNING,
        PreviewEnvironment.Status.FAILED,
    ):
        p.status = PreviewEnvironment.Status.BUILDING
        p.save(update_fields=["status", "updated_at", "version"])


@activity.defn(name="astrolift.preview.mark_building")
async def mark_preview_building(preview_environment_id: int) -> None:
    """Flip PreviewEnvironment to BUILDING.

    Idempotent — already-BUILDING rows are left untouched; RUNNING/FAILED
    rows re-enter BUILDING so a re-trigger doesn't get stuck in a
    terminal state.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_preview_building_sync)(preview_environment_id)


def _mark_preview_running_sync(preview_environment_id: int) -> None:
    from django.utils import timezone

    from astrolift_lifecycle.models import PreviewEnvironment

    p = PreviewEnvironment.objects.get(pk=preview_environment_id)
    p.status = PreviewEnvironment.Status.RUNNING
    p.last_deployed_at = timezone.now()
    p.save(update_fields=["status", "last_deployed_at", "updated_at", "version"])


@activity.defn(name="astrolift.preview.mark_running")
async def mark_preview_running(preview_environment_id: int) -> None:
    """Flip PreviewEnvironment to RUNNING and stamp last_deployed_at."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_preview_running_sync)(preview_environment_id)


def _mark_preview_failed_sync(preview_environment_id: int, reason: str) -> None:
    from astrolift_lifecycle.models import PreviewEnvironment

    p = PreviewEnvironment.objects.get(pk=preview_environment_id)
    p.status = PreviewEnvironment.Status.FAILED
    p.save(update_fields=["status", "updated_at", "version"])
    log.warning(
        "mark_preview_failed preview_environment_id=%s reason=%s",
        preview_environment_id,
        reason,
    )


@activity.defn(name="astrolift.preview.mark_failed")
async def mark_preview_failed(preview_environment_id: int, reason: str = "") -> None:
    """Flip PreviewEnvironment to FAILED."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_preview_failed_sync)(preview_environment_id, reason)


def _provision_preview_namespace_sync(preview_environment_id: int) -> str:
    """Ensure the preview env's dedicated namespace exists on its cluster.

    The namespace name is stored in ``PreviewEnvironment.namespace`` at
    row-creation time.  Using the stored name (rather than re-computing it
    here) keeps the workflow idempotent across retries even if the naming
    convention changes in flight.
    """
    from astrolift_lifecycle.models import PreviewEnvironment
    from core.app_deploy import AppDeployError
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    p = PreviewEnvironment.objects.select_related(
        "app_environment__tenant_cluster__provider_plugin",
        "registered_app__organization",
    ).get(pk=preview_environment_id)
    cluster = p.app_environment.tenant_cluster
    if cluster is None:
        raise AppDeployError(
            f"preview {p.pk} env has no tenant_cluster bound — cannot provision namespace",
        )
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    labels = {
        "astrolift-managed": "true",
        "app-slug": p.registered_app.slug,
        "preview-pr": str(p.pr_number),
    }
    driver.ensure_namespace(ctx.slug, p.namespace, labels, {})
    return p.namespace


@activity.defn(name="astrolift.preview.provision_managed_services")
async def provision_preview_managed_services_activity(preview_environment_id: int) -> dict:
    """Attach and slice the previewed environment's managed services (#1578).

    The step `preview_build.BUILD_ORDER` has named since it was written and
    nothing performed: `PROVISION_MANAGED_SERVICES`. Without it a preview
    `AppEnvironment` owns zero `ManagedService` rows and inherits none, and
    the deploy render only synthesizes the bindings Secret when that set is
    non-empty -- so a preview workload boots with no DB / redis / queue
    envelope at all.

    Returns the outcome as a dict rather than raising on a partial result:
    a preview whose redis could not be sliced should still get its postgres,
    and the caller decides whether the remainder is fatal.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_provision_preview_managed_services_sync, thread_sensitive=False)(
        preview_environment_id
    )


def _provision_preview_managed_services_sync(preview_environment_id: int) -> dict:
    from astrolift_lifecycle.models import PreviewEnvironment
    from astrolift_lifecycle.services.preview_service_provisioning import (
        provision_preview_managed_services,
    )

    preview = (
        PreviewEnvironment.objects.select_related("app_environment")
        .filter(pk=preview_environment_id, deleted_at__isnull=True)
        .first()
    )
    if preview is None or preview.app_environment is None:
        return {"attached": [], "sliced": [], "shared_unsliced": [], "skipped": [], "errors": {}}

    outcome = provision_preview_managed_services(preview.app_environment)
    return {
        "attached": list(outcome.attached),
        "sliced": list(outcome.sliced),
        "shared_unsliced": list(outcome.shared_unsliced),
        "skipped": list(outcome.skipped),
        "errors": dict(outcome.errors),
    }


@activity.defn(name="astrolift.preview.provision_namespace")
async def provision_preview_namespace(preview_environment_id: int) -> str:
    """Ensure the preview environment's Kubernetes namespace exists.

    Idempotent — ``driver.ensure_namespace`` is a server-side apply, so
    running this on a pre-existing namespace is safe and a no-op from the
    cluster's perspective.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    namespace = await sync_to_async(_provision_preview_namespace_sync)(preview_environment_id)
    log.info(
        "provision_preview_namespace namespace=%s",
        namespace,
        extra={"preview_environment_id": preview_environment_id},
    )
    return namespace


# ---- Preview teardown (Phase 3) -----------------------------------


def _mark_preview_torn_down_sync(preview_environment_id: int) -> None:
    from django.utils import timezone

    from astrolift_lifecycle.models import PreviewEnvironment

    p = PreviewEnvironment.all_objects.get(pk=preview_environment_id)
    p.status = PreviewEnvironment.Status.TORN_DOWN
    p.torn_down_at = timezone.now()
    p.save(update_fields=["status", "torn_down_at", "updated_at", "version"])


@activity.defn(name="astrolift.preview.mark_torn_down")
async def mark_preview_torn_down(preview_environment_id: int) -> None:
    """Flip the PreviewEnvironment row to TORN_DOWN + stamp torn_down_at."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_preview_torn_down_sync)(preview_environment_id)


def _delete_preview_namespace_sync(preview_environment_id: int) -> str:
    from astrolift_lifecycle.models import PreviewEnvironment
    from core.app_deploy import AppDeployError
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    p = PreviewEnvironment.all_objects.select_related(
        "app_environment__tenant_cluster__provider_plugin",
    ).get(pk=preview_environment_id)
    cluster = p.app_environment.tenant_cluster
    if cluster is None:
        raise AppDeployError(
            f"preview {p.pk} env has no tenant_cluster bound — cannot delete namespace",
        )
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    # delete_namespace cascades all the namespaced resources k8s knows
    # about (Deployments, Services, ConfigMaps, Secrets, PVCs, Ingresses).
    # Pass wait=True so we don't return until the namespace is actually
    # gone — operators see a clean teardown rather than a "torn-down"
    # marker that lingers as a Terminating namespace.
    driver.delete_namespace(ctx.slug, p.namespace, wait=True)
    return p.namespace


@activity.defn(name="astrolift.preview.delete_namespace")
async def delete_preview_namespace(preview_environment_id: int) -> str:
    """Delete the preview's Kubernetes namespace.

    Cascades all namespaced resources. ``wait=True`` blocks until the
    namespace is fully removed so the workflow's terminal event reflects
    a clean cluster state rather than an in-flight Terminating phase.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    namespace = await sync_to_async(_delete_preview_namespace_sync)(preview_environment_id)
    log.info(
        "delete_preview_namespace removed namespace=%s",
        namespace,
        extra={"preview_environment_id": preview_environment_id},
    )
    return namespace


def _load_preview_teardown_state_sync(preview_environment_id: int) -> dict:
    from astrolift_lifecycle.models import PreviewEnvironment

    p = PreviewEnvironment.all_objects.get(pk=preview_environment_id)
    return {
        "preview_id": p.pk,
        "status": p.status,
        "torn_down_at_unix": (int(p.torn_down_at.timestamp()) if p.torn_down_at else None),
    }


@activity.defn(name="astrolift.preview.teardown_state")
async def load_preview_teardown_state(preview_environment_id: int) -> dict:
    """Project the row into ``preview_teardown.PreviewTeardownState``.

    The teardown policy is Django-free (the workflow sandbox imports
    it), so it can't read a row itself — this is the join that lets
    ``teardown_steps_to_run`` decide what still needs doing. Reads
    through ``all_objects`` because a closed PR's preview may already
    be soft-deleted, and a soft-deleted preview still owns cluster
    resources until this workflow finishes.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_load_preview_teardown_state_sync)(preview_environment_id)


def _emit_preview_torn_down_event_sync(preview_environment_id: int) -> None:
    from astrolift_lifecycle.models import PreviewEnvironment
    from core.events import Event

    p = PreviewEnvironment.all_objects.select_related("registered_app").get(
        pk=preview_environment_id,
    )
    Event.emit(
        "preview_env.torn_down",
        payload={
            "preview_environment_guid": str(p.guid),
            "pr_number": p.pr_number,
            "branch": p.branch,
            "hostname": p.hostname,
            "namespace": p.namespace,
        },
        resource_kind="preview_environment",
        resource_id=str(p.guid),
        organization_id=p.registered_app.organization_id,
        registered_app_id=p.registered_app_id,
    )


@activity.defn(name="astrolift.preview.emit_torn_down_event")
async def emit_preview_torn_down_event(preview_environment_id: int) -> None:
    """Emit the ``preview_env.torn_down`` event (teardown step 5).

    Separate from ``mark_preview_torn_down`` so a re-fired teardown
    can re-emit the event without touching the timestamp the first
    run stamped -- which is exactly the convergence branch the policy's
    ``teardown_steps_to_run`` returns for an inconsistent row.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_emit_preview_torn_down_event_sync)(preview_environment_id)


def _create_rollback_deployment_sync(deployment_id: int) -> int:
    """Sync core of ``create_rollback_deployment``. Exposed as a
    plain function so unit tests can call it directly — sync_to_async
    in pytest-django lands in a different DB connection that the
    transactional rollback doesn't see.
    """
    from astrolift_lifecycle.models import Deployment

    current = Deployment.all_objects.select_related("registered_app", "app_environment").get(pk=deployment_id)
    # Find the prior running deployment in the same env (older
    # than the current row). Pick the most recent terminal-OK one.
    prior = (
        Deployment.objects.filter(
            registered_app=current.registered_app,
            app_environment=current.app_environment,
            deleted_at__isnull=True,
            created_at__lt=current.created_at,
            status__in=[
                Deployment.Status.RUNNING.value,
                Deployment.Status.SUPERSEDED.value,
            ],
        )
        .order_by("-created_at")
        .first()
    )
    if prior is None:
        raise RuntimeError(
            f"deployment {deployment_id} has no prior running revision in this env to roll back to"
        )
    new_deploy = Deployment.objects.create(
        registered_app=current.registered_app,
        app_environment=current.app_environment,
        workload_id=prior.workload_id,
        triggered_by_user_id=current.triggered_by_user_id,
        trigger_kind=Deployment.TriggerKind.ROLLBACK.value,
        status=Deployment.Status.PENDING.value,
        image_tag=prior.image_tag,
        image_digest=prior.image_digest,
        config_snapshot=prior.config_snapshot,
        approvals_required=0,
        approvals_received=0,
        ci_actor_kind="rollback",
        commit_sha=prior.commit_sha,
        branch=prior.branch,
        ci_provider=prior.ci_provider,
    )
    # Current row was the bad deploy — mark it superseded so the
    # state machine shows the lineage.
    try:
        current.transition_to(Deployment.Status.SUPERSEDED)
    except ValueError:
        # Already in a terminal state — no-op.
        pass
    return new_deploy.pk


@activity.defn(name="astrolift.deploy.create_rollback_deployment")
async def create_rollback_deployment(deployment_id: int) -> int:
    """Resolve the prior ``running`` deployment in the same env and
    create a new ``rollback`` deployment row that copies its image
    tag + config snapshot. Returns the new deployment id; the
    workflow then runs the normal apply path against it.
    """
    from asgiref.sync import sync_to_async

    return await sync_to_async(_create_rollback_deployment_sync)(deployment_id)


def _create_promotion_deployment_sync(
    source_deployment_id: int,
    target_app_environment_id: int,
) -> int:
    """Sync core of ``create_promotion_deployment``. See
    ``_create_rollback_deployment_sync`` for the rationale."""
    from astrolift_lifecycle.models import AppEnvironment, Deployment

    source = Deployment.all_objects.select_related("registered_app", "app_environment").get(
        pk=source_deployment_id
    )
    target_env = AppEnvironment.objects.get(pk=target_app_environment_id)
    if target_env.registered_app_id != source.registered_app_id:
        raise RuntimeError("promotion target must belong to the same app as the source")
    if target_env.deploys_paused:
        raise RuntimeError(f"target environment {target_env.name!r} has deploys paused")

    initial_status = (
        Deployment.Status.PENDING_APPROVAL.value
        if target_env.required_approvals > 0
        else Deployment.Status.PENDING.value
    )
    new_deploy = Deployment.objects.create(
        registered_app=source.registered_app,
        app_environment=target_env,
        workload_id=source.workload_id,
        triggered_by_user_id=source.triggered_by_user_id,
        trigger_kind=Deployment.TriggerKind.PROMOTION.value,
        status=initial_status,
        image_tag=source.image_tag,
        image_digest=source.image_digest,
        config_snapshot=source.config_snapshot,
        promoted_from=source,
        approvals_required=target_env.required_approvals,
        approvals_received=0,
        ci_actor_kind=source.ci_actor_kind or "promotion",
        commit_sha=source.commit_sha,
        branch=source.branch,
        ci_provider=source.ci_provider,
    )
    return new_deploy.pk


@activity.defn(name="astrolift.deploy.create_promotion_deployment")
async def create_promotion_deployment(
    source_deployment_id: int,
    target_app_environment_id: int,
) -> int:
    """Move an image tag from source env → target env on the same
    app. The new row stamps ``promoted_from`` so the lineage is
    queryable. Approvals on the target env are honoured by setting
    the same initial status logic the deploy mutation uses.
    """
    from asgiref.sync import sync_to_async

    return await sync_to_async(_create_promotion_deployment_sync)(
        source_deployment_id, target_app_environment_id
    )
