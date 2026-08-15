from __future__ import annotations

from unittest.mock import patch

import pytest
from _sdk.managed_service import UpdateResult

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_workflows.activities.managed_service_lifecycle import (
    ManagedServicePreflightError,
    _finalize_update_sync,
    _mark_failed_sync,
    _update_sync,
)

pytestmark = pytest.mark.django_db


def _service() -> ManagedService:
    org = Organization.objects.create(name="Update Org", slug="update-org")
    team = Team.objects.create(organization=org, name="Update Team", slug="update-team")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Update Project",
        slug="update-project",
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Update Provider",
                slug="update-provider",
                version="1.0.0",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    plugin = ProviderPlugin.objects.get(slug="update-provider")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="update-cluster",
        name="Update Cluster",
        provider_plugin=plugin,
        endpoint="https://example.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Update App",
        slug="update-app",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        variant="rds",
        name="primary",
        config={"size": "large", "backup_days": 14},
        applied_config={"size": "medium", "backup_days": 7},
        status=ManagedService.Status.UPDATING,
        backend_ref="postgres/primary",
        operation_kind="update",
    )


def test_update_sync_calls_provider_update_with_desired_state():
    svc = _service()
    captured = {}

    class Driver:
        def __init__(self, *, config):
            captured["driver_config"] = config

        def update(self, spec):
            captured["spec"] = spec
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="updated",
            )

    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=Driver),
        patch("core.cluster_observability.managed_config_for", return_value={"region": "us-east-1"}),
    ):
        result = _update_sync(svc.pk)

    assert result["ok"] is True
    assert captured["driver_config"] == {"region": "us-east-1"}
    assert captured["spec"].handle == "postgres/primary"
    assert captured["spec"].size == "large"
    assert captured["spec"].config == {"size": "large", "backup_days": 14}


def test_update_sync_surfaces_nonretryable_driver_refusal():
    svc = _service()

    class Driver:
        def __init__(self, *, config):
            pass

        def update(self, spec):
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message="storage shrink is immutable",
                errors=["immutable_field"],
                retryable=False,
            )

    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=Driver),
        patch("core.cluster_observability.managed_config_for", return_value={}),
    ):
        result = _update_sync(svc.pk)
    assert result == {
        "ok": False,
        "handle": "postgres/primary",
        "message": "storage shrink is immutable",
        "errors": ["immutable_field"],
        "retryable": False,
    }


def test_finalize_update_advances_applied_config_and_resyncs_bindings():
    svc = _service()
    with patch(
        "astrolift_workflows.activities.managed_service_lifecycle._sync_binding_rows"
    ) as sync_bindings:
        _finalize_update_sync(svc.pk, "postgres/primary-v2")
    svc.refresh_from_db()
    assert svc.backend_ref == "postgres/primary-v2"
    assert svc.status == ManagedService.Status.ACTIVE
    assert svc.status_error == ""
    assert svc.applied_config == svc.config
    assert svc.operation_completed_at is not None
    sync_bindings.assert_called_once()


def test_failed_update_keeps_last_applied_config():
    svc = _service()
    _mark_failed_sync(svc.pk, "provider rejected update")
    svc.refresh_from_db()
    assert svc.status == ManagedService.Status.FAILED
    assert svc.config == {"size": "large", "backup_days": 14}
    assert svc.applied_config == {"size": "medium", "backup_days": 7}
    assert svc.operation_completed_at is not None


def test_update_preflight_blocks_driver_when_required_crd_is_missing():
    svc = _service()
    svc.kind = ManagedService.Kind.SEARCH
    svc.variant = "opensearch_operator"
    svc.save(update_fields=["kind", "variant", "updated_at", "version"])
    required = [
        "opensearchclusters.opensearch.org",
        "opensearchusers.opensearch.org",
        "opensearchroles.opensearch.org",
    ]
    payload = {
        "kubernetes_version": "1.30.7",
        "installed_crds": required,
        "operator_versions": {"opensearch-operator": "3.0.2"},
    }

    with (
        patch("core.cluster_management.probe_cluster_capabilities_dispatch", return_value=payload),
        patch("astrolift_drivers.registry.plugins.get") as get_driver,
        pytest.raises(ManagedServicePreflightError, match="missing_crd"),
    ):
        _update_sync(svc.pk)

    get_driver.assert_not_called()
    svc.app_environment.tenant_cluster.refresh_from_db()
    assert svc.app_environment.tenant_cluster.capabilities == payload
