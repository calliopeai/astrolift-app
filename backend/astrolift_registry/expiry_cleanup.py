"""Exact native cleanup receipts for a temporary app's retained namespaces."""

from __future__ import annotations

from django.db import transaction

RUNTIME_KINDS = frozenset(
    {
        "Deployment",
        "StatefulSet",
        "DaemonSet",
        "Job",
        "CronJob",
        "Service",
        "Ingress",
        "HorizontalPodAutoscaler",
        "PodMonitor",
        "HTTPRoute",
        "SecurityPolicy",
        "ReferenceGrant",
        "Certificate",
    }
)


def _kind(ref):
    version, kind = ref["apiVersion"], ref["kind"]
    return f"{version}/{kind}" if "/" in version else kind


def _proof(value):
    meta = value.get("metadata") or {}
    if not meta.get("uid") or not meta.get("resourceVersion"):
        raise RuntimeError("EXPIRY_NATIVE_IDENTITY_UNCONFIRMED")
    return {
        "apiVersion": value["apiVersion"],
        "kind": value["kind"],
        "metadata": {
            key: meta[key] for key in ("name", "namespace", "uid", "resourceVersion") if key in meta
        },
    }


def _owned_runtime(app, namespace, ref, actual):
    meta = actual.get("metadata") or {}
    labels = meta.get("labels") or {}
    expected_labels = ref.get("labels") or {}
    owner_keys = (
        "astrolift.dev/app",
        "astrolift.io/app",
        "astrolift.io/app-id",
        "astrolift.io/app-env-secrets",
        "astrolift.io/bindings-for",
    )
    if not any(
        expected_labels.get(key) in (app.slug, str(app.guid)) and labels.get(key) == expected_labels[key]
        for key in owner_keys
    ) or meta.get("ownerReferences"):
        raise RuntimeError("EXPIRY_RUNTIME_OWNER_CHANGED")
    if ref["metadata"]["namespace"] != namespace and labels.get("astrolift.dev/namespace") != namespace:
        raise RuntimeError("EXPIRY_RUNTIME_NAMESPACE_CHANGED")
    return _proof(actual)


def _namespace(app, env, driver, slug, namespace, expected=None):
    from astrolift_lifecycle.models import AppEnvironment
    from core.app_deploy import namespace_for_environment

    # A retained name cannot be deleted after another app has claimed it.
    others = (
        AppEnvironment.all_objects.filter(tenant_cluster_id=env.tenant_cluster_id)
        .exclude(registered_app_id=app.pk)
        .select_related("registered_app__organization")
    )
    for other in others:
        if (
            other.deleted_at is None or other.registered_app.data_retained_until is not None
        ) and namespace_for_environment(other) == namespace:
            raise RuntimeError("EXPIRY_NAMESPACE_SHARED")
    native = driver.get_manifest(slug, None, "Namespace", namespace)
    if native is None:
        if driver.get_namespace(slug, namespace) is not None:
            raise RuntimeError("EXPIRY_NAMESPACE_IDENTITY_UNAVAILABLE")
        return None
    meta = native.get("metadata") or {}
    labels, annotations = meta.get("labels") or {}, meta.get("annotations") or {}
    legacy = (
        labels.get("astrolift.io/managed-by") == "astrolift"
        and labels.get("astrolift.io/organization") == app.organization.slug
        and labels.get("astrolift.io/app") == app.slug
        and annotations.get("astrolift.io/registered-app-id") == str(app.pk)
    )
    guid_owned = (
        labels.get("astrolift.io/managed-by") == "platform"
        and labels.get("astrolift.io/organization-id") == str(app.organization.guid)
        and labels.get("astrolift.io/app-id") == str(app.guid)
        and labels.get("astrolift.io/cluster-id") == str(env.tenant_cluster.guid)
    )
    if not (legacy or guid_owned) or meta.get("ownerReferences"):
        raise RuntimeError("EXPIRY_NAMESPACE_OWNER_CHANGED")
    proof = _proof(native)
    if expected and proof["metadata"]["uid"] != expected["metadata"]["uid"]:
        raise RuntimeError("EXPIRY_NAMESPACE_REPLACED")
    return proof


def retire_runtime(app_id):
    """Remove exact app runtime refs; retain namespace, PVCs and managed services."""
    from astrolift_lifecycle.domain_app_route import require_unbound_app
    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_registry.models import RegisteredApp
    from astrolift_services.model_retirement import locked_retirement_apps
    from core.app_deploy import namespace_for_environment, render_resources_for_deployment
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    app = RegisteredApp.all_objects.select_related("organization").get(pk=app_id)
    with locked_retirement_apps(RegisteredApp.all_objects.filter(pk=app.pk)):
        pass
    require_unbound_app(app.pk)
    if app.expiry_fired_at is None or app.delete_data_on_expiry:
        raise RuntimeError("EXPIRY_RETENTION_NOT_ACTIVE")
    envs = list(
        AppEnvironment.all_objects.filter(registered_app_id=app.pk).select_related(
            "tenant_cluster__provider_plugin"
        )
    )
    journal = list(app.expiry_cleanup or [])
    if not journal:
        for env in envs:
            if env.tenant_cluster_id is None:
                continue
            driver, ctx = _driver_for_cluster(env.tenant_cluster), _context_for_cluster(env.tenant_cluster)
            namespace = namespace_for_environment(env)
            deploy = (
                Deployment.all_objects.filter(registered_app_id=app.pk, app_environment_id=env.pk)
                .order_by("-created_at", "-pk")
                .first()
            )
            proof = _namespace(app, env, driver, ctx.slug, namespace)
            if proof is None:
                if deploy is not None:
                    raise RuntimeError("EXPIRY_NAMESPACE_UNOBSERVED")
                continue
            resources = (
                render_resources_for_deployment(deploy, include_managed_filesystems=False) if deploy else []
            )
            from astrolift_registry.models import Workload
            from astrolift_workflows.activities.app_lifecycle import (
                _app_env_secret_name,
                _bindings_secret_name,
            )

            secret_names = {
                (_app_env_secret_name(app.slug, env.name), "astrolift.io/app-env-secrets"),
                (_bindings_secret_name(app.slug), "astrolift.io/bindings-for"),
                *(
                    (_bindings_secret_name(app.slug, name), "astrolift.io/bindings-for")
                    for name in Workload.all_objects.filter(registered_app_id=app.pk).values_list(
                        "name", flat=True
                    )
                ),
            }
            resources.extend(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": name, "namespace": namespace, "labels": {label: app.slug}},
                }
                for name, label in sorted(secret_names)
            )
            refs = []
            for resource in resources:
                if resource.get("kind") not in RUNTIME_KINDS and resource.get("kind") != "Secret":
                    continue
                meta = resource.get("metadata") or {}
                if not meta.get("name"):
                    raise RuntimeError("EXPIRY_RUNTIME_REFERENCE_INVALID")
                ref = {
                    "apiVersion": resource["apiVersion"],
                    "kind": resource["kind"],
                    "metadata": {"name": meta["name"], "namespace": meta.get("namespace", namespace)},
                    "labels": dict(meta.get("labels") or {}),
                }
                actual = driver.get_manifest(ctx.slug, ref["metadata"]["namespace"], _kind(ref), meta["name"])
                ref["native"] = _owned_runtime(app, namespace, ref, actual) if actual else None
                refs.append(ref)
            journal.append(
                {
                    "environment_id": env.pk,
                    "cluster_id": env.tenant_cluster_id,
                    "namespace": proof,
                    "runtime": refs,
                }
            )
        # Commit the original namespace identity before any native delete.
        with transaction.atomic():
            row = RegisteredApp.all_objects.select_for_update().get(pk=app.pk)
            if row.expiry_cleanup:
                journal = row.expiry_cleanup
            else:
                row.expiry_cleanup = journal
                row.save(update_fields=["expiry_cleanup", "updated_at", "version"])
    environments = {env.pk: env for env in envs}
    deleted = []
    for receipt in journal:
        env = environments.get(receipt["environment_id"])
        if env is None or env.tenant_cluster_id != receipt["cluster_id"]:
            raise RuntimeError("EXPIRY_ENVIRONMENT_CHANGED")
        driver, ctx = _driver_for_cluster(env.tenant_cluster), _context_for_cluster(env.tenant_cluster)
        namespace = receipt["namespace"]["metadata"]["name"]
        # Shared-edge refs can remain after the app namespace disappears.
        _namespace(app, env, driver, ctx.slug, namespace, receipt["namespace"])
        for ref in receipt["runtime"]:
            target_namespace = ref["metadata"]["namespace"]
            actual = driver.get_manifest(ctx.slug, target_namespace, _kind(ref), ref["metadata"]["name"])
            if actual is None:
                continue
            proof = _owned_runtime(app, namespace, ref, actual)
            if not ref["native"] or proof["metadata"]["uid"] != ref["native"]["metadata"]["uid"]:
                raise RuntimeError("EXPIRY_RUNTIME_REPLACED")
            result = driver.delete_manifests(ctx.slug, target_namespace, [proof])
            if result is None or not result.ok:
                raise RuntimeError("EXPIRY_RUNTIME_DELETE_UNCONFIRMED")
            deleted.append(f"{target_namespace}/{ref['kind']}/{proof['metadata']['name']}")
    return deleted


def purge_namespaces(app_id):
    """Delete only original namespaces, after every managed service is gone."""
    from astrolift_lifecycle.domain_app_route import require_unbound_app
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from astrolift_services.model_retirement import locked_retirement_apps
    from astrolift_services.models import ManagedService
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    app = RegisteredApp.all_objects.select_related("organization").get(pk=app_id)
    with locked_retirement_apps(RegisteredApp.all_objects.filter(pk=app.pk)):
        pass
    require_unbound_app(app.pk)
    if app.deleted_at is None or app.data_retained_until is None:
        raise RuntimeError("EXPIRY_PURGE_NOT_ADMITTED")
    from django.utils import timezone

    if (
        app.data_retained_until > timezone.now()
        or ManagedService.objects.filter(registered_app_id=app.pk).exists()
    ):
        raise RuntimeError("EXPIRY_DATA_STILL_RETAINED")
    for receipt in app.expiry_cleanup or []:
        env = AppEnvironment.all_objects.select_related("tenant_cluster__provider_plugin").get(
            pk=receipt["environment_id"], registered_app_id=app.pk, tenant_cluster_id=receipt["cluster_id"]
        )
        driver, ctx = _driver_for_cluster(env.tenant_cluster), _context_for_cluster(env.tenant_cluster)
        namespace = receipt["namespace"]["metadata"]["name"]
        proof = _namespace(app, env, driver, ctx.slug, namespace, receipt["namespace"])
        if proof is None:
            continue
        result = driver.delete_manifests(ctx.slug, namespace, [proof])
        if result is None or not result.ok:
            raise RuntimeError("EXPIRY_NAMESPACE_DELETE_UNCONFIRMED")
        if driver.get_namespace(ctx.slug, namespace) is not None:
            raise RuntimeError("EXPIRY_NAMESPACE_DELETE_PENDING")
