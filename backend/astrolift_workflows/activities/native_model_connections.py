"""Revision-bound native connection grants/bindings; no model lifecycle driver."""

from contextlib import contextmanager

from django.db import transaction
from django.utils import timezone
from temporalio import activity

from astrolift_services.models import ManagedService
from astrolift_services.native_model_connections import is_bedrock_connection, native_identity, verify_source
from astrolift_workflows.activities.shared_model_reconcile import (
    _activity,
    _failed_sync,
    _locked_subscriptions,
    locked_model,
)


@contextmanager
def locked_native_model(service_id, revision):
    from astrolift_identity.models import Organization

    with transaction.atomic():
        owner = ManagedService.objects.values_list("organization_id", flat=True).get(pk=service_id)
        # Serialize shared app grant unions with native intent changes in this
        # organization; the existing revision/placement locks remain mandatory.
        Organization.objects.select_for_update().get(pk=owner)
        with locked_model(service_id, revision) as (service, cluster):
            if not is_bedrock_connection(service):
                raise ValueError("This operation is not a native model connection.")
            native = native_identity(service, cleanup=True)
            expected = f"model_endpoint/bedrock-connection/{service.guid}/{native['source_fingerprint']}"
            if service.backend_ref != expected:
                raise ValueError("Native recorded identity is unavailable.")
            yield service, cluster


def _destination_driver(cluster):
    from core.cluster_observability import _driver_for_cluster

    return _driver_for_cluster(cluster)


def _identities(rows, *, cleanup=False):
    from astrolift_workflows.activities.workload_identity import _ensure_workload_identity_sync

    result = {}
    # One union for every app, across all its coherent environments. A selected
    # removal never needs the removed source to remain catalogue-readable.
    for row in rows:
        env = row.app_environment
        app_id = env.registered_app_id
        if app_id not in result:
            result[app_id] = _ensure_workload_identity_sync(app_id, env.pk, reconcile_empty=cleanup)
    return result


def _preflight_service_accounts(rows, cluster, driver):
    from aws.bedrock_catalogue import _partition

    from astrolift_services.model_subscriptions import _native_service_account
    from core.app_deploy import _config_for_capability, workload_identity_role_name

    cfg = _config_for_capability("aws", cluster, "identity")
    path = cfg.role_path.strip("/")
    for row in rows:
        if not row.desired_enabled:
            continue
        app = row.app_environment.registered_app
        name = workload_identity_role_name(app)
        arn = f"arn:{_partition(cfg.region)}:iam::{cfg.account_id}:role/{path + '/' if path else ''}{name}"
        _native_service_account(
            row, driver, {"role": name, "role_arn": arn, "annotation": {"eks.amazonaws.com/role-arn": arn}}
        )


def _apply_sync(service_id, revision):
    from astrolift_services.model_subscriptions import apply_destination_binding

    with locked_native_model(service_id, revision) as (service, cluster):
        rows = _locked_subscriptions(service)
        if any(row.credential_ref for row in rows):
            raise ValueError("Native subscriptions cannot carry copied credentials.")
        cleanup = any(not row.desired_enabled for row in rows)
        source_available = True
        if any(row.desired_enabled for row in rows) or not rows:
            try:
                verify_source(service)
            except ValueError:
                source_available = False
                if not cleanup:
                    raise
        driver = _destination_driver(cluster)
        if source_available:
            _preflight_service_accounts(rows, cluster, driver)
        identities = _identities(rows, cleanup=cleanup)
        for row in rows:
            identity = identities[row.app_environment.registered_app_id]
            if row.desired_enabled and service.pk in identity["unavailable_native_services"]:
                continue
            apply_destination_binding(
                row, driver, None, native_identity=identities[row.app_environment.registered_app_id]
            )
        return (
            "cleanup_pending_source_unavailable"
            if any(
                row.desired_enabled
                and service.pk
                in identities[row.app_environment.registered_app_id]["unavailable_native_services"]
                for row in rows
            )
            else "configured"
        )


def _finish_sync(service_id, revision):
    from astrolift_services.model_subscriptions import destination_ready

    with locked_native_model(service_id, revision) as (service, cluster):
        rows = _locked_subscriptions(service)
        cleanup = any(not row.desired_enabled for row in rows)
        if any(row.desired_enabled for row in rows) or not rows:
            try:
                verify_source(service)
            except ValueError:
                if not cleanup:
                    raise
        identities = _identities(rows, cleanup=cleanup)
        driver = _destination_driver(cluster)
        available_rows = [
            row
            for row in rows
            if not row.desired_enabled
            or service.pk
            not in identities[row.app_environment.registered_app_id]["unavailable_native_services"]
        ]
        if any(
            not destination_ready(
                row, driver, native_identity=identities[row.app_environment.registered_app_id]
            )
            for row in available_rows
        ):
            return False
        unavailable = False
        for row in rows:
            if row not in available_rows:
                unavailable = True
                row.subscription_status = "failed"
                row.reconcile_error = "Native source admission is unavailable; its owned grants were removed."
                row.save(update_fields=["subscription_status", "reconcile_error", "updated_at", "version"])
                continue
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
        if not unavailable:
            service.applied_config = dict(service.config)
            service.applied_subscription_revision = revision
        service.status = "failed" if unavailable else "active"
        service.status_error = (
            "Revocation was observed; retained native source admission is unavailable." if unavailable else ""
        )
        service.operation_completed_at = timezone.now()
        service.save(
            update_fields=[
                "applied_config",
                "applied_subscription_revision",
                "status",
                "status_error",
                "operation_completed_at",
                "updated_at",
                "version",
            ]
        )
        return True


@activity.defn(name="astrolift.native_model_connection.apply")
async def apply_native_model_connection(service_id: int, revision: int) -> str:
    return await _activity(_apply_sync, service_id, revision)


@activity.defn(name="astrolift.native_model_connection.finish")
async def finish_native_model_connection(service_id: int, revision: int) -> bool:
    return await _activity(_finish_sync, service_id, revision)


@activity.defn(name="astrolift.native_model_connection.fail")
async def fail_native_model_connection(service_id: int, revision: int) -> None:
    await _activity(_failed_sync, service_id, revision)
