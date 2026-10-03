"""Revision-bound shared model lifecycle with independent credentials and app activation."""

import secrets
from contextlib import contextmanager

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from temporalio import activity

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_services.cluster_models import available_model_clusters, live_cluster_models
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from core.tenancy import TenantContext, tenant_context


@contextmanager
def locked_model(service_id, revision):
    with transaction.atomic():
        service = (
            ManagedService.objects.select_for_update(of=("self",))
            .select_related("organization")
            .get(pk=service_id)
        )
        if not service.organization_id or service.subscription_revision != revision:
            raise ValueError("Shared model revision is no longer current.")
        cluster = TenantCluster.objects.select_for_update().get(pk=service.tenant_cluster_id)
        provider = ProviderPlugin.objects.select_for_update().get(pk=cluster.provider_plugin_id)
        if (
            service.model_operation_cluster_guid != cluster.guid
            or service.model_operation_provider_guid != provider.guid
        ):
            raise ValueError("Shared model placement changed after acceptance.")
        cluster.provider_plugin = provider
        service.tenant_cluster = cluster
        if (
            not live_cluster_models(
                ManagedService.objects.filter(pk=service.pk), service.organization_id
            ).exists()
            or not available_model_clusters(
                TenantCluster.objects.filter(pk=cluster.pk), service.organization_id
            ).exists()
        ):
            raise ValueError("Shared model owner or transport is unavailable.")
        with tenant_context(TenantContext(organization_id=service.organization_id)):
            yield service, cluster


def resolved_driver(service, cluster):
    from k8s_native.managed.model_endpoint_vllm import VLLMDriver

    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from core.cluster_observability import managed_config_for

    resolved = resolve_managed_driver(
        cluster_plugin_slug=cluster.provider_plugin.slug, kind="model_endpoint", variant="vllm"
    )
    if not issubclass(resolved.driver_cls, VLLMDriver):
        raise ValueError("Shared placement requires the supported Python vLLM driver.")
    cfg = managed_config_for(resolved.plugin_slug, cluster, kind=service.kind, variant=service.variant)
    return resolved.driver_cls(config=cfg), cfg


def _apply_sync(service_id, revision, action, delete_data):
    from _sdk.managed_service import DeprovisionSpec, UpdateSpec

    from astrolift_workflows.activities.managed_service_lifecycle import (
        _assert_config_secret_refs_scoped,
        _cluster_model_placement,
        _run_managed_service_preflight,
        build_provision_spec,
    )

    with locked_model(service_id, revision) as (service, cluster):
        _assert_config_secret_refs_scoped(service, cluster)
        from astrolift_services.model_admission import canonical_model_handle

        if service.backend_ref and service.backend_ref != canonical_model_handle(service):
            raise ValueError("Shared model handle disagrees with its owner.")
        driver, cfg = resolved_driver(service, cluster)
        if action == "delete":
            if (
                service.attachments.filter(model_subscription=True)
                .exclude(desired_enabled=False, subscription_status="revoked")
                .exists()
            ):
                raise ValueError("Shared model still has unrevoked subscriptions.")
            if service.backend_ref:
                result = driver.deprovision(
                    DeprovisionSpec(
                        handle=service.backend_ref,
                        config=dict(service.config or {}),
                        managed_service_id=str(service.guid),
                    ),
                    delete_data=delete_data,
                )
                if not result.ok:
                    raise ValueError("Shared model deletion was not confirmed.")
            from astrolift_services.hf_connection import delete_model_token

            delete_model_token(service, cfg.secrets_backend)
            service.operation_completed_at = timezone.now()
            service.save(update_fields=["operation_completed_at", "updated_at", "version"])
            service.soft_delete()
            return "deleted"
        if action != "apply":
            raise ValueError("Shared model operation is unsupported.")
        placement = _cluster_model_placement(service, cluster=cluster)
        _run_managed_service_preflight(service, cluster)
        from k8s_native.managed.shared_model_runtime import shared_runtime

        shared_runtime(cfg.shared_runtimes, dict(service.config or {}), "python")
        from astrolift_services.hf_connection import materialize_model_token

        materialize_model_token(service, cfg.secrets_backend)
        for consumer in placement.consumers:
            path = consumer.credential_ref.partition("#")[0]
            current = cfg.secrets_backend.get(path)
            if current is None:
                cfg.secrets_backend.upsert(path, {"api_key": secrets.token_urlsafe(32)})
            elif not isinstance(current, dict) or not isinstance(current.get("api_key"), str):
                raise ValueError("Shared subscription credential is invalid.")
        if service.backend_ref:
            result = driver.update(
                UpdateSpec(
                    handle=service.backend_ref,
                    config=dict(service.config or {}),
                    managed_service_id=str(service.guid),
                    cluster_model=placement,
                )
            )
        else:
            result = driver.provision(build_provision_spec(service, cluster=cluster))
        if not result.ok or result.handle != canonical_model_handle(service):
            raise ValueError("Shared model apply was not confirmed.")
        service.backend_ref = result.handle
        service.save(update_fields=["backend_ref", "updated_at", "version"])
        return "applied"


def _observe_sync(service_id, revision):
    from _sdk.k8s_naming import cluster_model_namespace, cluster_model_resource_name
    from _sdk.managed_service import ServiceHandle
    from k8s_native.managed.shared_model_runtime import AUTH_REVISION

    with locked_model(service_id, revision) as (service, cluster):
        from astrolift_services.model_admission import canonical_model_handle

        if service.backend_ref != canonical_model_handle(service):
            return "failed"
        driver, cfg = resolved_driver(service, cluster)
        if not service.backend_ref:
            return "pending"
        result = driver.status(ServiceHandle(service.backend_ref, managed_service_id=str(service.guid)))
        if result.state in ("error", "deprovisioned"):
            return "failed"
        if result.state != "available":
            return "pending"
        namespace = cluster_model_namespace(
            organization_id=str(service.organization.guid),
            cluster_id=str(cluster.guid),
            managed_service_id=str(service.guid),
        )
        name = cluster_model_resource_name(str(service.guid))
        resource = cfg.cluster_driver.get_manifest(str(cluster.guid), namespace, "apps/v1/Deployment", name)
        meta = (resource or {}).get("metadata") or {}
        labels, annotations = meta.get("labels") or {}, meta.get("annotations") or {}
        generation = meta.get("generation")
        if (
            labels.get("astrolift.io/managed-service-id") != str(service.guid)
            or annotations.get(AUTH_REVISION) != str(revision)
            or type(generation) is not int
            or generation < 1
            or (resource.get("status") or {}).get("observedGeneration") != generation
        ):
            return "pending"
        service.applied_config = dict(service.config or {})
        service.applied_subscription_revision = revision
        service.model_ready_observed_at = timezone.now()
        service.model_ready_generation = generation
        service.model_ready_auth_revision = revision
        service.model_ready_provider_guid = cluster.provider_plugin.guid
        service.model_ready_backend_ref = service.backend_ref
        service.save(
            update_fields=[
                "applied_config",
                "applied_subscription_revision",
                "model_ready_observed_at",
                "model_ready_generation",
                "model_ready_auth_revision",
                "model_ready_provider_guid",
                "model_ready_backend_ref",
                "updated_at",
                "version",
            ]
        )
        return "ready"


def _locked_subscriptions(service):
    from astrolift_registry.models import RegisteredApp

    # Sorted locks share the request-side app -> environment -> attachment order.
    ids = list(
        service.attachments.filter(model_subscription=True)
        .exclude(subscription_status="revoked", desired_enabled=False)
        .values_list("app_environment__registered_app_id", "app_environment_id", "pk")
    )
    list(
        RegisteredApp.objects.select_for_update()
        .filter(pk__in=sorted({app for app, _, _ in ids}))
        .order_by("pk")
    )
    from astrolift_lifecycle.models import AppEnvironment

    list(
        AppEnvironment.objects.select_for_update()
        .filter(pk__in=sorted({env for _, env, _ in ids}))
        .order_by("pk")
    )
    rows = list(
        ManagedServiceAttachment.objects.select_for_update(of=("self",))
        .filter(pk__in=[pk for _, _, pk in ids])
        .select_related(
            "managed_service__organization",
            "managed_service__tenant_cluster",
            "app_environment__registered_app__organization",
            "app_environment__tenant_cluster",
        )
        .order_by("pk")
    )
    from astrolift_registry.scopes import live_app_owners

    coherent_apps = set(
        live_app_owners(
            RegisteredApp.objects.filter(
                organization_id=service.organization_id, pk__in=[app for app, _, _ in ids]
            )
        ).values_list("pk", flat=True)
    )
    for row in rows:
        env = row.app_environment
        if (
            env is None
            or env.deleted_at is not None
            or env.registered_app_id not in coherent_apps
            or env.tenant_cluster_id != service.tenant_cluster_id
        ):
            raise ValueError("Shared subscription destination is no longer live and coherent.")
    return rows


def _rollout_confirmed(service, cluster, revision):
    from astrolift_services.model_admission import canonical_model_handle

    return (
        service.applied_subscription_revision == revision
        and service.model_ready_auth_revision == revision
        and service.model_ready_observed_at is not None
        and type(service.model_ready_generation) is int
        and service.model_ready_generation > 0
        and service.model_ready_provider_guid == cluster.provider_plugin.guid
        and service.model_ready_backend_ref == service.backend_ref
        and service.backend_ref == canonical_model_handle(service)
    )


def _activate_sync(service_id, revision):
    from astrolift_services.model_subscriptions import apply_destination_binding

    with locked_model(service_id, revision) as (service, cluster):
        if not _rollout_confirmed(service, cluster, revision):
            raise ValueError("Shared model credential rollout is not confirmed.")
        _, cfg = resolved_driver(service, cluster)
        for row in _locked_subscriptions(service):
            apply_destination_binding(row, cfg.cluster_driver, cfg.secrets_backend)
        return True


def _finish_sync(service_id, revision):
    from astrolift_services.model_subscriptions import destination_ready

    with locked_model(service_id, revision) as (service, cluster):
        if not _rollout_confirmed(service, cluster, revision):
            return False
        _, cfg = resolved_driver(service, cluster)
        rows = _locked_subscriptions(service)
        if any(not destination_ready(row, cfg.cluster_driver) for row in rows):
            return False
        for row in rows:
            if not row.desired_enabled:
                delete = getattr(cfg.secrets_backend, "delete", None)
                if not callable(delete):
                    raise ValueError("Subscription credential deletion is unsupported.")
                delete(row.credential_ref.partition("#")[0])
            row.subscription_status = "active" if row.desired_enabled else "revoked"
            row.applied_revision = row.desired_revision
            row.reconcile_error = ""
            row.reconciled_at = timezone.now()
            row.save(
                update_fields=[
                    "subscription_status",
                    "applied_revision",
                    "reconcile_error",
                    "reconciled_at",
                    "updated_at",
                    "version",
                ]
            )
        service.status = ManagedService.Status.ACTIVE
        service.status_error = ""
        service.operation_completed_at = timezone.now()
        service.save(
            update_fields=["status", "status_error", "operation_completed_at", "updated_at", "version"]
        )
        return True


def _failed_sync(service_id, revision):
    with transaction.atomic():
        row = (
            ManagedService.objects.select_for_update()
            .filter(pk=service_id, subscription_revision=revision)
            .first()
        )
        if row is None:
            return
        row.status = ManagedService.Status.FAILED
        row.status_error = "Shared model reconciliation failed; retry or operator review is required."
        row.operation_completed_at = timezone.now()
        row.save(update_fields=["status", "status_error", "operation_completed_at", "updated_at", "version"])
        ManagedServiceAttachment.objects.filter(
            managed_service=row,
            model_subscription=True,
            subscription_status__in=("pending", "revoking", "failed"),
        ).update(
            subscription_status="failed",
            reconcile_error="Shared model reconciliation failed; retry or operator review is required.",
            version=F("version") + 1,
            updated_at=timezone.now(),
        )


async def _activity(fn, *args):
    import asyncio

    from _sdk._telemetry import heartbeat_sink
    from asgiref.sync import sync_to_async
    from temporalio.exceptions import ApplicationError

    activity.heartbeat()
    loop = asyncio.get_running_loop()
    try:
        with heartbeat_sink(lambda detail: loop.call_soon_threadsafe(activity.heartbeat, detail)):
            return await sync_to_async(fn)(*args)
    except Exception as exc:
        # Never serialize backend values or infrastructure tracebacks into history/API.
        raise ApplicationError(
            "Shared model reconciliation is unavailable.",
            non_retryable=isinstance(exc, (TypeError, ValueError)),
        ) from None


@activity.defn(name="astrolift.shared_model.apply")
async def apply_shared_model(service_id: int, revision: int, action: str, delete_data: bool) -> str:
    return await _activity(_apply_sync, service_id, revision, action, delete_data)


@activity.defn(name="astrolift.shared_model.observe")
async def observe_shared_model(service_id: int, revision: int) -> str:
    return await _activity(_observe_sync, service_id, revision)


@activity.defn(name="astrolift.shared_model.activate")
async def activate_shared_model_subscriptions(service_id: int, revision: int) -> bool:
    return await _activity(_activate_sync, service_id, revision)


@activity.defn(name="astrolift.shared_model.finish")
async def finish_shared_model_reconcile(service_id: int, revision: int) -> bool:
    return await _activity(_finish_sync, service_id, revision)


@activity.defn(name="astrolift.shared_model.failed")
async def fail_shared_model_reconcile(service_id: int, revision: int) -> None:
    await _activity(_failed_sync, service_id, revision)
