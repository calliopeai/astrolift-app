"""Per-subscription app bindings; the model's operator credential is never copied."""

from copy import deepcopy

from django.db.models import F

from astrolift_registry.models import Workload
from astrolift_services.cluster_models import model_binding_prefix
from astrolift_services.models import ManagedServiceAttachment
from core.app_deploy import namespace_for_environment

LONG_RUNNING_KINDS = ("deployment", "statefulset", "agent", "workflow")
_BINDING_SUFFIXES = ("ENDPOINT_URL", "API_KEY", "DEPLOYMENT_NAME", "REGION", "API_STYLE", "AUTH_MODE")


def subscription_secret_name(row):
    return f"astrolift-model-{row.guid}"


def subscription_namespace(env):
    from _sdk.k8s_naming import app_namespace

    canonical = app_namespace(
        organization_slug=env.registered_app.organization.slug, app_slug=env.registered_app.slug
    )
    if namespace_for_environment(env) != canonical:
        raise ValueError("Custom or preview namespaces are not supported for shared model subscriptions.")
    return canonical


def coherent_subscriptions(env, *, applied_only=True):
    from astrolift_clusters.models import TenantCluster
    from astrolift_services.cluster_models import available_model_clusters, live_cluster_models
    from astrolift_services.model_admission import with_canonical_model_handle
    from astrolift_services.models import ManagedService

    if (
        env.deleted_at is not None
        or env.registered_app.deleted_at is not None
        or not available_model_clusters(
            TenantCluster.objects.filter(pk=env.tenant_cluster_id), env.registered_app.organization_id
        ).exists()
    ):
        return ManagedServiceAttachment.objects.none()
    models = live_cluster_models(ManagedService.objects.all(), env.registered_app.organization_id).filter(
        tenant_cluster_id=env.tenant_cluster_id
    )
    if applied_only:
        models = with_canonical_model_handle(models).filter(backend_ref=F("_canonical_model_handle"))
    rows = ManagedServiceAttachment.objects.filter(
        model_subscription=True, app_environment=env, desired_enabled=True, managed_service__in=models
    ).select_related(
        "managed_service__organization",
        "managed_service__tenant_cluster__provider_plugin",
        "app_environment__registered_app__organization",
    )
    if applied_only:
        rows = rows.filter(
            managed_service__status__in=("active", "updating", "failed"),
            managed_service__model_ready_provider_guid=F(
                "managed_service__tenant_cluster__provider_plugin__guid"
            ),
            managed_service__model_ready_backend_ref=F("managed_service__backend_ref"),
            managed_service__model_ready_auth_revision=F("managed_service__applied_subscription_revision"),
            managed_service__applied_subscription_revision__gte=F("desired_revision"),
            managed_service__model_ready_observed_at__isnull=False,
            managed_service__model_ready_generation__gt=0,
        )
    return rows.order_by("binding_alias", "guid")


def binding_values(row, secrets_backend):
    from _sdk.k8s_naming import cluster_model_namespace, cluster_model_resource_name
    from _sdk.secrets import resolve_secret_reference
    from k8s_native.managed.model_endpoint_vllm import PORT, _unpack_handle

    service = row.managed_service
    env = row.app_environment
    subscription_namespace(env)
    if (
        service.organization_id != env.registered_app.organization_id
        or service.tenant_cluster_id != env.tenant_cluster_id
    ):
        raise ValueError("Subscription owner or destination is no longer coherent.")
    if not coherent_subscriptions(env).filter(pk=row.pk).exists():
        raise ValueError("Subscription model readiness or recorded placement is no longer coherent.")
    expected_ref = f"services/{service.organization.guid}/{service.guid}/subscriptions/{row.guid}#api_key"
    if row.credential_ref != expected_ref:
        raise ValueError("Subscription credential identity is invalid.")
    handle = _unpack_handle(service.backend_ref)
    namespace = cluster_model_namespace(
        organization_id=str(service.organization.guid),
        cluster_id=str(service.tenant_cluster.guid),
        managed_service_id=str(service.guid),
    )
    name = cluster_model_resource_name(str(service.guid))
    if (handle.cluster_id, handle.namespace, handle.name) != (
        str(service.tenant_cluster.guid),
        namespace,
        name,
    ):
        raise ValueError("Subscription model handle disagrees with its owner.")
    key = resolve_secret_reference(secrets_backend, row.credential_ref)
    if not key:
        raise ValueError("Subscription credential is unavailable.")
    prefix = model_binding_prefix(row.binding_alias)
    values = (
        f"http://{name}.{namespace}.svc.cluster.local:{PORT}/v1",
        key,
        str((service.applied_config or {}).get("model", "")),
        "kubernetes",
        "openai",
        "api_key",
    )
    return {f"{prefix}{suffix}": value for suffix, value in zip(_BINDING_SUFFIXES, values, strict=True)}


def binding_secret(row, secrets_backend):
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": subscription_secret_name(row),
            "namespace": subscription_namespace(row.app_environment),
            "labels": {
                "astrolift.dev/managed-by": "astrolift",
                "astrolift.dev/app": row.app_environment.registered_app.slug,
                "astrolift.dev/environment": row.app_environment.name,
                "astrolift.io/model-subscription": str(row.guid),
            },
        },
        "type": "Opaque",
        "stringData": binding_values(row, secrets_backend),
    }


def validate_destination(env, alias):
    """Refuse ambiguous key precedence and unsupported runtime templates before accepting."""
    subscription_namespace(env)
    workloads = list(Workload.objects.filter(registered_app=env.registered_app))
    if not workloads or any(row.kind not in LONG_RUNNING_KINDS for row in workloads):
        raise ValueError(
            "Shared model subscriptions currently require existing long-running Deployment or StatefulSet workloads."
        )
    from astrolift_manifest.env_edit import read_app_env

    prefix = model_binding_prefix(alias)
    try:
        keys = read_app_env(env.registered_app.manifest_raw).keys()
    except (TypeError, ValueError):
        raise ValueError("Stored app manifest cannot be safely checked for binding conflicts.") from None
    if any(key.startswith(prefix) for key in keys):
        raise ValueError("The subscription binding prefix conflicts with an app environment variable.")


def _template(resource):
    return (resource.get("spec") or {}).get("template") or {}


def _owned_workload(resource, env, workload):
    meta = resource.get("metadata") or {}
    labels = meta.get("labels") or {}
    pod_labels = (_template(resource).get("metadata") or {}).get("labels") or {}
    expected = {
        "astrolift.dev/app": env.registered_app.slug,
        "astrolift.dev/environment": env.name,
        "astrolift.dev/workload": workload.name,
    }
    return all(labels.get(key) == value and pod_labels.get(key) == value for key, value in expected.items())


def _check_env_from_prefix(refs, prefix, driver, cluster_id, namespace):
    """Inspect bounded key names only; never overwrite another source's binding."""
    if len(refs) > 64:
        raise ValueError("Subscription destination has too many environment sources to validate.")
    for ref in refs:
        source = ref.get("secretRef") or ref.get("configMapRef")
        if not isinstance(source, dict) or not isinstance(source.get("name"), str):
            raise ValueError("Subscription destination environment source is unsupported.")
        kind = "v1/Secret" if ref.get("secretRef") else "v1/ConfigMap"
        resource = driver.get_manifest(cluster_id, namespace, kind, source["name"])
        if resource is None:
            if source.get("optional") is True:
                continue
            raise ValueError("Subscription destination environment source is unavailable.")
        keys = (
            set(resource.get("data") or {})
            | set(resource.get("stringData") or {})
            | set(resource.get("binaryData") or {})
        )
        ref_prefix = ref.get("prefix", "")
        if not isinstance(ref_prefix, str) or any((ref_prefix + key).startswith(prefix) for key in keys):
            raise ValueError("Subscription binding prefix conflicts with an existing environment source.")


def apply_destination_binding(row, cluster_driver, secrets_backend):
    """Conditional full-object apply preserves fixed/HPA replica ownership and refuses replacements."""
    env = row.app_environment
    namespace = subscription_namespace(env)
    cid = str(env.tenant_cluster.guid)
    name = subscription_secret_name(row)
    prior = cluster_driver.get_manifest(cid, namespace, "v1/Secret", name)
    if prior is not None and ((prior.get("metadata") or {}).get("labels") or {}).get(
        "astrolift.io/model-subscription"
    ) != str(row.guid):
        raise ValueError("Subscription binding Secret belongs to another resource.")
    workloads = list(Workload.objects.filter(registered_app=env.registered_app))
    if not workloads or any(workload.kind not in LONG_RUNNING_KINDS for workload in workloads):
        raise ValueError("Subscription destination workloads are unsupported or unavailable.")
    prepared = []
    for workload in workloads:
        kind = "apps/v1/StatefulSet" if workload.kind == "statefulset" else "apps/v1/Deployment"
        current = cluster_driver.get_manifest(cid, namespace, kind, workload.name)
        if current is None or not _owned_workload(current, env, workload):
            raise ValueError(
                "Subscription destination workload is unavailable or belongs to another environment."
            )
        meta = current.get("metadata") or {}
        if not meta.get("uid") or not meta.get("resourceVersion"):
            raise ValueError("Subscription workload observation lacks identity preconditions.")
        manifest = deepcopy(current)
        manifest.pop("status", None)
        manifest["metadata"].pop("managedFields", None)
        manifest["metadata"].pop("creationTimestamp", None)
        if (
            kind == "apps/v1/Deployment"
            and (meta.get("annotations") or {}).get("astrolift.dev/replica-owner") == "hpa"
        ):
            manifest["spec"].pop("replicas", None)
        template = _template(manifest)
        spec = template.setdefault("spec", {})
        containers = spec.get("containers") or []
        if not containers:
            raise ValueError("Subscription destination has no confirmed containers.")
        for container in containers:
            prefix = model_binding_prefix(row.binding_alias)
            if row.desired_enabled and any(
                str(entry.get("name") or "").startswith(prefix) for entry in container.get("env", ())
            ):
                raise ValueError("Subscription binding prefix conflicts with a workload variable.")
            refs = [
                ref
                for ref in container.get("envFrom", ())
                if (ref.get("secretRef") or {}).get("name") != name
            ]
            if row.desired_enabled:
                _check_env_from_prefix(refs, prefix, cluster_driver, cid, namespace)
                refs.append({"secretRef": {"name": name}})
            container["envFrom"] = refs
        template.setdefault("metadata", {}).setdefault("annotations", {})[
            f"astrolift.io/model-binding-{row.guid}"
        ] = str(row.desired_revision)
        prepared.append(manifest)
    if row.desired_enabled:
        secret = binding_secret(row, secrets_backend)
        if prior is not None:
            meta = prior.get("metadata") or {}
            if not meta.get("uid") or not meta.get("resourceVersion"):
                raise ValueError("Subscription Secret observation lacks identity preconditions.")
            secret["metadata"].update({key: meta[key] for key in ("uid", "resourceVersion")})
        result = cluster_driver.apply_manifests(cid, namespace, [secret], create_only=prior is None)
        if not result.ok:
            raise ValueError("Subscription binding Secret could not be applied.")
    for manifest in prepared:
        result = cluster_driver.apply_manifests(cid, namespace, [manifest])
        if not result.ok:
            raise ValueError("Subscription workload binding could not be applied.")
    if not row.desired_enabled and prior is not None:
        meta = prior.get("metadata") or {}
        if not meta.get("uid") or not meta.get("resourceVersion"):
            raise ValueError("Subscription Secret observation lacks identity preconditions.")
        stub = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "uid": meta["uid"],
                "resourceVersion": meta["resourceVersion"],
            },
        }
        if not cluster_driver.delete_manifests(cid, namespace, [stub]).ok:
            raise ValueError("Subscription binding Secret could not be removed.")


def destination_ready(row, cluster_driver):
    env = row.app_environment
    cid, namespace = str(env.tenant_cluster.guid), subscription_namespace(env)
    workloads = list(Workload.objects.filter(registered_app=env.registered_app))
    if not workloads or any(workload.kind not in LONG_RUNNING_KINDS for workload in workloads):
        return False
    for workload in workloads:
        kind = "apps/v1/StatefulSet" if workload.kind == "statefulset" else "apps/v1/Deployment"
        resource = cluster_driver.get_manifest(cid, namespace, kind, workload.name)
        if resource is None or not _owned_workload(resource, env, workload):
            return False
        meta, status = resource.get("metadata") or {}, resource.get("status") or {}
        generation = meta.get("generation")
        if type(generation) is not int or generation <= 0 or status.get("observedGeneration") != generation:
            return False
        template = _template(resource)
        if ((template.get("metadata") or {}).get("annotations") or {}).get(
            f"astrolift.io/model-binding-{row.guid}"
        ) != str(row.desired_revision):
            return False
        containers = (template.get("spec") or {}).get("containers") or []
        if not containers or any(
            any(
                (ref.get("secretRef") or {}).get("name") == subscription_secret_name(row)
                for ref in container.get("envFrom", ())
            )
            != row.desired_enabled
            for container in containers
        ):
            return False
        desired = (resource.get("spec") or {}).get("replicas")
        if (
            type(desired) is not int
            or desired < 0
            or any(status.get(key, 0) != desired for key in ("replicas", "readyReplicas", "updatedReplicas"))
            or status.get("unavailableReplicas", 0) != 0
        ):
            return False
        if kind == "apps/v1/Deployment" and status.get("availableReplicas", 0) != desired:
            return False
        if kind == "apps/v1/StatefulSet" and (
            not status.get("currentRevision") or status.get("currentRevision") != status.get("updateRevision")
        ):
            return False
        if desired == 0:
            continue
        expected_labels = {
            "astrolift.dev/app": env.registered_app.slug,
            "astrolift.dev/environment": env.name,
            "astrolift.dev/workload": workload.name,
        }
        pods = [
            pod
            for pod in cluster_driver.list_manifests(cid, namespace, "v1/Pod")
            if all(
                ((pod.get("metadata") or {}).get("labels") or {}).get(key) == value
                for key, value in expected_labels.items()
            )
        ]
        if len(pods) != desired:
            return False
        for pod in pods:
            pod_meta = pod.get("metadata") or {}
            pod_status = pod.get("status") or {}
            if (
                pod_meta.get("deletionTimestamp")
                or pod_status.get("phase") != "Running"
                or not any(
                    c.get("type") == "Ready" and c.get("status") == "True"
                    for c in pod_status.get("conditions", ())
                )
            ):
                return False
            if (pod_meta.get("annotations") or {}).get(f"astrolift.io/model-binding-{row.guid}") != str(
                row.desired_revision
            ):
                return False
            pod_containers = (pod.get("spec") or {}).get("containers") or []
            if not pod_containers or any(
                any(
                    (ref.get("secretRef") or {}).get("name") == subscription_secret_name(row)
                    for ref in container.get("envFrom", ())
                )
                != row.desired_enabled
                for container in pod_containers
            ):
                return False
            owners = [
                owner for owner in pod_meta.get("ownerReferences", ()) if owner.get("controller") is True
            ]
            if len(owners) != 1 or not owners[0].get("uid") or not meta.get("uid"):
                return False
            owner = owners[0]
            if kind == "apps/v1/StatefulSet":
                if owner.get("kind") != "StatefulSet" or owner["uid"] != meta["uid"]:
                    return False
            else:
                if owner.get("kind") != "ReplicaSet" or not owner.get("name"):
                    return False
                replica_set = cluster_driver.get_manifest(cid, namespace, "apps/v1/ReplicaSet", owner["name"])
                rs_meta = (replica_set or {}).get("metadata") or {}
                rs_owners = [
                    ref for ref in rs_meta.get("ownerReferences", ()) if ref.get("controller") is True
                ]
                if (
                    rs_meta.get("uid") != owner["uid"]
                    or len(rs_owners) != 1
                    or rs_owners[0].get("kind") != "Deployment"
                    or rs_owners[0].get("uid") != meta["uid"]
                ):
                    return False
    return True


def stamp_binding_revisions(resources, env):
    revisions = {
        f"astrolift.io/model-binding-{row.guid}": str(row.desired_revision)
        for row in coherent_subscriptions(env)
    }
    if not revisions:
        return
    for resource in resources:
        if resource.get("kind") in ("Deployment", "StatefulSet"):
            _template(resource).setdefault("metadata", {}).setdefault("annotations", {}).update(revisions)
