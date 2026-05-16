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


@activity.defn(name="astrolift.app.mark_provisioning")
async def mark_app_provisioning(registered_app_id: int) -> None:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    if app.provisioning_status == RegisteredApp.ProvisioningStatus.PROVISIONING.value:
        return
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.PROVISIONING)


@activity.defn(name="astrolift.app.mark_ready")
async def mark_app_ready(registered_app_id: int) -> None:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.READY)


def _provision_namespace_sync(registered_app_id: int, app_environment_id: int | None) -> str:
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import AppDeployError, namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    app = RegisteredApp.all_objects.select_related("organization").get(pk=registered_app_id)
    cluster: TenantCluster | None = None
    if app_environment_id is not None:
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
    app.save(update_fields=["registry_repo_uri", "updated_at", "version"])
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


def _provision_managed_services_initial_sync(
    registered_app_id: int,
    app_environment_id: int,
) -> list[int]:
    from astrolift_drivers.registry import DriverNotFound, plugins
    from astrolift_services.models import ManagedService
    from core.app_deploy import AppDeployError
    from core.cluster_observability import _config_for  # type: ignore[attr-defined]

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
        # plugin_loader flattens managed-service drivers into the same
        # plugins.get() namespace via synthetic role names — see
        # astrolift_clusters/plugin_loader.py line ~50.
        variant = getattr(ms, "variant", "") or ""
        try:
            driver_cls = plugins.get(plugin_slug, f"managed:{ms.kind}:{variant}")
        except DriverNotFound:
            # Try the empty-variant default — some plugins register
            # ``managed:postgres:`` rather than ``managed:postgres:cnpg``.
            try:
                driver_cls = plugins.get(plugin_slug, f"managed:{ms.kind}:")
            except DriverNotFound as exc:
                raise AppDeployError(
                    f"cluster {cluster.slug}: plugin {plugin_slug!r} has no managed-service driver "
                    f"for kind={ms.kind!r} variant={variant!r}",
                ) from exc
        cfg = _config_for(plugin_slug, cluster)
        driver = driver_cls(config=cfg)
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


def _pre_flight_sync(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import AppDeployError, cluster_for_deployment

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


@activity.defn(name="astrolift.deploy.mark_deploying")
async def mark_deploying(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment

    d = Deployment.all_objects.get(pk=deployment_id)
    d.transition_to(Deployment.Status.DEPLOYING)


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
        from astrolift_services.models import AppSecretBundleRef, ManagedService

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
        has_bindings = ManagedService.objects.filter(
            registered_app=app,
            app_environment=env,
            deleted_at__isnull=True,
        ).exists()
        env_from = list(bundle_secret_names)
        if has_bindings:
            env_from.append(_bindings_secret_name(app.slug))

        return manifest, app, env, d, env_from

    manifest, app, env, d, env_from = await sync_to_async(_gather)()

    namespace = app.k8s_namespace or f"{app.organization.slug}-{app.slug}"
    resources = _render(
        manifest,
        namespace=namespace,
        image_tag=d.image_tag or "latest",
        image_repository=app.registry_repo_uri or app.slug,
        environment_name=env.name,
        env_from_secret_refs=env_from,
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

    log.info(
        "render_manifests produced %d resource(s) with envFrom=%s",
        len(resources),
        env_from,
        extra={"deployment_id": deployment_id},
    )
    return {"resources": resources, "env_from_secret_refs": env_from}


def _render_app_ingresses_and_tls(
    deployment_id: int,
    namespace: str,
    manifest,
) -> list[dict[str, Any]]:
    """Emit one ``Ingress`` per active CustomDomain + a TLS ``Secret``
    for every BYO cert. Skips domains in non-serving states
    (validating, failed, not_requested) — we never want traffic
    routed at a hostname whose cert isn't usable yet.

    For ACTIVE-state domains on platform-managed zones the cert lives
    in the cloud's cert manager (ACM, Google-managed cert, Azure cert)
    and is referenced by annotation/spec at the cluster's ingress
    controller; we leave the Ingress' ``tls.secretName`` pointing at
    the conventional ``<app>-<host>-tls`` name and let the controller
    populate it via cert-manager when present. For ACTIVE on external
    zones we annotate with ``cert-manager.io/cluster-issuer`` so the
    cluster's cert-manager does the HTTP-01 dance on first apply. For
    BYO we emit the TLS Secret inline from the operator-supplied PEM.
    """
    import base64

    from astrolift_lifecycle.models import CustomDomain, Deployment

    d = Deployment.all_objects.select_related(
        "registered_app",
        "app_environment",
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
    return out


def _bindings_secret_name(app_slug: str) -> str:
    """Synthetic k8s Secret name for the per-app managed-service
    connection envelope. Kept in one helper so the producer (in
    ``update_secrets``) and the consumer (``render_manifests``) can
    never drift apart.
    """
    return f"astrolift-bindings-{app_slug}"


def _apply_manifests_sync(deployment_id: int) -> dict[str, list[str]]:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import (
        AppDeployError,
        driver_for_deployment,
        render_resources_for_deployment,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster",
    ).get(pk=deployment_id)
    driver, ctx, namespace = driver_for_deployment(d)
    resources = render_resources_for_deployment(d)
    if not resources:
        raise AppDeployError(
            f"manifest for app {d.registered_app.slug!r} rendered to zero resources — "
            "check the workloads/services block in astrolift.toml",
        )
    result = driver.apply_manifests(ctx.slug, namespace, resources)
    if not result.ok:
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

    Uses server-side apply (idempotent) via the cluster driver. Returns
    the ``ApplyResult`` shape ({created/updated/unchanged}) so the
    workflow event log carries which resources actually changed for an
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
        ManagedService,
        ManagedServiceBinding,
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
    # One k8s Secret per (app, env) named ``astrolift-bindings-<slug>``
    # carrying the connection envelope for every active ManagedService
    # bound to this app+env. Resolves the driver-side ValueRefs (literal
    # or secret_ref) into raw values so workloads see a flat env-var
    # surface — they don't need to know whether DATABASE_PASSWORD came
    # from Secrets Manager or was inlined.
    services = list(
        ManagedService.objects.filter(
            registered_app=d.registered_app,
            app_environment=d.app_environment,
            deleted_at__isnull=True,
        ).order_by("kind", "name"),
    )
    if services:
        bindings_data: dict[str, str] = {}
        seen_keys: set[str] = set()
        secrets_backend = driver_for_capability(
            d.app_environment.tenant_cluster,
            "secrets",
        )
        for svc in services:
            for binding in ManagedServiceBinding.objects.filter(
                managed_service=svc,
                deleted_at__isnull=True,
            ).order_by("env_key"):
                env_key = binding.env_key
                if env_key in seen_keys:
                    # Last writer wins per spec 05 §10. We keep declared
                    # order: services iterate sorted by (kind, name),
                    # bindings inside a service sorted by env_key.
                    pass
                seen_keys.add(env_key)
                raw_value: str
                if binding.is_secret:
                    # env_value_ref is a secrets-backend reference (ARN
                    # or path); resolve via the cluster's secrets driver.
                    resolved = secrets_backend.get(binding.env_value_ref)
                    if resolved is None:
                        raise AppDeployError(
                            f"binding {svc.kind}/{svc.name}#{env_key} "
                            f"references missing secret "
                            f"{binding.env_value_ref!r}",
                        )
                    # ``get`` returns a dict for bundles; for a single
                    # binding we expect either a single-key dict or a
                    # str-stringifiable value. Take the value verbatim
                    # if it's a string; otherwise pick the first value.
                    if isinstance(resolved, dict):
                        if not resolved:
                            raise AppDeployError(
                                f"binding {env_key} resolved to an empty secret",
                            )
                        raw_value = str(next(iter(resolved.values())))
                    else:
                        raw_value = str(resolved)
                else:
                    raw_value = binding.env_value_ref
                bindings_data[env_key] = base64.b64encode(
                    raw_value.encode("utf-8"),
                ).decode("ascii")

        bindings_secret_name = f"astrolift-bindings-{d.registered_app.slug}"
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

    if not resources:
        return 0
    result = cluster_driver.apply_manifests(ctx.slug, namespace, resources)
    if not result.ok:
        raise AppDeployError(
            f"update_secrets apply failed for deployment {deployment_id}: " + "; ".join(result.errors),
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
    # Per-app custom domains pointing at this env. CustomDomain has
    # registered_app + app_environment FKs.
    for cd in CustomDomain.objects.filter(
        registered_app=d.registered_app,
        app_environment=d.app_environment,
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
async def wait_dns(deployment_id: int, timeout_seconds: int = 120) -> int:
    """Wait for the app's hostnames to resolve via DNS.

    Covers the managed-domain hostname + every CustomDomain bound to
    this (app, env). Apps without any domains exit immediately. Uses
    stdlib ``socket.gethostbyname`` against the local resolver — same
    view a browser hitting the load balancer will get. Loop polls
    every 5s up to ``timeout_seconds``.
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


@activity.defn(name="astrolift.deploy.mark_running")
async def mark_running(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment

    d = Deployment.all_objects.get(pk=deployment_id)
    d.transition_to(Deployment.Status.RUNNING)


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
