"""#1034 — managed-service deprovision must be idempotent for an
already-gone backend resource.

Live repro: staticsite's object_store row (a deleted S3 bucket) failed the
driver's empty step with ``NoSuchBucket`` → the activity raised → the row never
finalized → the app sat ``tearing_down`` forever. The deprovision activity must
treat a not-found driver error (raised OR reported as ``ok=False``) as success
so the row finalizes and a re-trigger converges.

Real Postgres; the driver resolution is patched so we exercise the activity's
not-found handling without a live cloud.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from _sdk.managed_service import DeprovisionResult

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _finalize_sync,
    _signals_already_gone,
    deprovision_managed_service,
)

pytestmark = pytest.mark.django_db


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme-depro")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-depro")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-depro")
    # bulk_create bypasses BaseCoreModel.save (ProviderPlugin.version is a
    # CharField, which the base's int version-bump would choke on).
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws-depro",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="aws-depro")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="aws-prod-depro",
        name="AWS Prod",
        provider_plugin=plugin,
        endpoint="https://example.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Static",
        slug="static-depro",
        provisioning_status="tearing_down",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.OBJECT_STORE,
        name="assets",
        status=ManagedService.Status.DEPROVISIONING,
        # Must be non-empty or the activity short-circuits before the driver.
        backend_ref="object_store/acme-static-assets",
    )
    return org, app, env, svc


class _RaisingNotFoundDriver:
    """Driver whose deprovision RAISES because the resource is already gone."""

    def __init__(self, *, config) -> None:  # noqa: ANN001
        self._config = config

    def deprovision(self, spec, *, delete_data=False, force_destroy=False):  # noqa: ANN001
        from aws._errors import NotFoundError

        raise NotFoundError("NoSuchBucket: The specified bucket does not exist")


class _ReportingNotFoundDriver:
    """Driver that REPORTS the not-found as a failed result (the live S3
    path: ``empty failed: NoSuchBucket: ...`` with ok=False)."""

    def __init__(self, *, config) -> None:  # noqa: ANN001
        self._config = config

    def deprovision(self, spec, *, delete_data=False, force_destroy=False):  # noqa: ANN001
        return DeprovisionResult(
            ok=False,
            handle=spec.handle,
            message="empty failed: NoSuchBucket: The specified bucket does not exist",
            errors=["NoSuchBucket: The specified bucket does not exist"],
        )


def _patch_driver(driver_cls):
    return (
        patch(
            "astrolift_drivers.registry.plugins.get",
            return_value=driver_cls,
        ),
        patch(
            "core.cluster_observability.managed_config_for",
            return_value={},
        ),
    )


def test_signals_already_gone_markers():
    assert _signals_already_gone("NoSuchBucket: ...")
    assert _signals_already_gone("NotFoundError", "boom")
    assert _signals_already_gone("bucket does not exist")
    assert not _signals_already_gone("AccessDenied: not authorized")
    assert not _signals_already_gone("InternalServerError")


def test_deprovision_treats_raised_not_found_as_success():
    """A driver that RAISES NotFoundError (resource already gone) yields a
    soft-success so the workflow finalizes instead of re-raising (#1034)."""
    _, _, _, svc = _scaffold()
    p_get, p_cfg = _patch_driver(_RaisingNotFoundDriver)
    with p_get, p_cfg:
        result = _deprovision_sync(svc.pk, delete_data=True, force_destroy=False)
    assert result["ok"] is True, result


def test_deprovision_treats_reported_not_found_as_success():
    """The live S3 path: driver returns ok=False with a NoSuchBucket message;
    the activity coerces it to success so teardown converges (#1034)."""
    _, _, _, svc = _scaffold()
    p_get, p_cfg = _patch_driver(_ReportingNotFoundDriver)
    with p_get, p_cfg:
        result = _deprovision_sync(svc.pk, delete_data=True, force_destroy=False)
    assert result["ok"] is True, result
    assert not result["errors"]


def test_real_error_still_fails():
    """A genuine non-not-found driver failure must still surface ok=False so
    the activity can classify it without swallowing the error."""

    class _AccessDeniedDriver:
        def __init__(self, *, config) -> None:  # noqa: ANN001
            pass

        def deprovision(self, spec, *, delete_data=False, force_destroy=False):  # noqa: ANN001
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message="AccessDenied: not authorized to delete bucket",
                errors=["AccessDenied"],
                retryable=False,
            )

    _, _, _, svc = _scaffold()
    p_get, p_cfg = _patch_driver(_AccessDeniedDriver)
    with p_get, p_cfg:
        result = _deprovision_sync(svc.pk, delete_data=True, force_destroy=False)
    assert result["ok"] is False
    assert result["retryable"] is False


def test_retryable_in_progress_result_survives_activity_adapter():
    class _InProgressDriver:
        def __init__(self, *, config) -> None:  # noqa: ANN001
            pass

        def deprovision(self, spec, *, delete_data=False, force_destroy=False):  # noqa: ANN001
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message="cloud delete still in progress",
                errors=["deletion_in_progress"],
                retryable=True,
            )

    _, _, _, svc = _scaffold()
    p_get, p_cfg = _patch_driver(_InProgressDriver)
    with p_get, p_cfg:
        result = _deprovision_sync(svc.pk, delete_data=True, force_destroy=False)

    assert result["ok"] is False
    assert result["retryable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable,non_retryable", [(True, False), (False, True)])
async def test_activity_marks_only_permanent_failures_non_retryable(
    retryable,
    non_retryable,
):
    from temporalio.exceptions import ApplicationError

    result = {
        "ok": False,
        "message": "not done",
        "errors": ["test"],
        "handle": "x/y",
        "retryable": retryable,
    }
    with (
        patch("astrolift_workflows.activities.managed_service_lifecycle.activity.heartbeat"),
        patch(
            "astrolift_workflows.activities.managed_service_lifecycle._deprovision_sync",
            return_value=result,
        ),
        pytest.raises(ApplicationError) as raised,
    ):
        await deprovision_managed_service(1, False, False)

    assert raised.value.non_retryable is non_retryable


def test_finalize_converges_and_is_idempotent():
    """After the not-found deprovision returns ok=True, finalize soft-deletes
    the row; a re-run (re-trigger) over the already-finalized row no-ops."""
    _, _, _, svc = _scaffold()
    p_get, p_cfg = _patch_driver(_ReportingNotFoundDriver)
    with p_get, p_cfg:
        result = _deprovision_sync(svc.pk, delete_data=True, force_destroy=False)
    assert result["ok"] is True

    _finalize_sync(svc.pk)
    svc.refresh_from_db()
    assert svc.deleted_at is not None, "row must finalize (soft-delete)"
    finalized_at = svc.deleted_at

    # Idempotent re-run: a resumed teardown re-running finalize is a no-op.
    _finalize_sync(svc.pk)
    svc.refresh_from_db()
    assert svc.deleted_at == finalized_at
