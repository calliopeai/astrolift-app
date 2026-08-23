"""Provision activity surfaces the driver's ``ready`` flag (#1038).

The ProvisionManagedServiceWorkflow skips its status()-poll readiness
wait when the provision result reports ``ready=True`` -- capability-only
services (Bedrock on-demand model access) provision no cloud resource
and have no backend ``state`` to poll. The seam that carries that signal
out of the driver and into the workflow is ``_provision_sync``'s result
dict; if it drops the ``ready`` key the workflow silently falls back to
polling, which false-fails capability-only provisioning. These tests pin
the passthrough in both directions.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_workflows.activities.managed_service_lifecycle import (
    _provision_sync,
)

pytestmark = pytest.mark.django_db


def _fake_driver_cls(ready_value: bool):
    class _FakeDriver:
        def __init__(self, *, config):  # noqa: ANN001 - test stub
            self._config = config

        def provision(self, spec):  # noqa: ANN001 - test stub
            return SimpleNamespace(
                ok=True,
                handle="model_endpoint/acme-api-prod-model",
                ready=ready_value,
                message="provisioned",
                errors=[],
            )

    return _FakeDriver


def _make_service(*, variant: str = "bedrock") -> ManagedService:
    org = Organization.objects.create(name="Acme", slug="acme-msr")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-msr")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo-msr",
    )
    # Seeded directly; the plugin row is scaffolding for this test.
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws-msr",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="aws-msr")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="aws-msr-cluster",
        name="AWS",
        provider_plugin=plugin,
        endpoint="https://example.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="MSR App",
        slug="msr-app",
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
        kind=ManagedService.Kind.MODEL_ENDPOINT,
        variant=variant,
        name="model",
        config={},
    )


def test_provision_sync_surfaces_ready_true():
    svc = _make_service()
    with (
        patch(
            "astrolift_drivers.registry.plugins.get",
            return_value=_fake_driver_cls(True),
        ),
        patch(
            "core.cluster_observability.managed_config_for",
            return_value={},
        ),
    ):
        result = _provision_sync(svc.pk)
    assert result["ok"] is True
    assert result["ready"] is True


def test_provision_sync_surfaces_ready_false():
    svc = _make_service()
    with (
        patch(
            "astrolift_drivers.registry.plugins.get",
            return_value=_fake_driver_cls(False),
        ),
        patch(
            "core.cluster_observability.managed_config_for",
            return_value={},
        ),
    ):
        result = _provision_sync(svc.pk)
    assert result["ok"] is True
    assert result["ready"] is False


def test_provision_sync_uses_manifest_restore_intent_instead_of_fresh_provision():
    calls = []

    class _RestoreDriver:
        def __init__(self, *, config):  # noqa: ANN001
            pass

        def provision(self, spec):  # pragma: no cover - the assertion is that this is not called
            raise AssertionError("fresh provision must not run for a restore declaration")

        def restore(self, snapshot, target):  # noqa: ANN001
            calls.append((snapshot, target))
            return SimpleNamespace(
                ok=True,
                handle="model_endpoint/restored",
                ready=True,
                message="restored",
                errors=[],
            )

    svc = _make_service()
    svc.lifecycle_policy = {
        "restore": {
            "snapshot_id": "snapshot-123",
            "source_handle": "model_endpoint/source",
            "created_at": "2026-08-23T00:00:00Z",
        }
    }
    svc.save(update_fields=["lifecycle_policy", "updated_at", "version"])
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=_RestoreDriver),
        patch("core.cluster_observability.managed_config_for", return_value={}),
    ):
        result = _provision_sync(svc.pk)

    assert result["handle"] == "model_endpoint/restored"
    assert calls[0][0].snapshot_id == "snapshot-123"
    assert calls[0][0].handle == "model_endpoint/source"
