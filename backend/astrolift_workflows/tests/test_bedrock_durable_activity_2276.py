"""Real persisted service owners through fresh Bedrock lifecycle activities."""

from __future__ import annotations

from functools import wraps
from unittest.mock import patch

import pytest
from _sdk.cloud_credentials import CloudCredential
from aws.managed._base import ManagedServiceError
from aws.managed.model_endpoint_bedrock import AmazonBedrockConfig, AmazonBedrockDriver

from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_workflows.activities.managed_service_lifecycle import (
    _check_ready_sync,
    _deprovision_sync,
    _finalize_provision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_provision_managed_service_ready import _make_service
from providers.tests.aws.test_bedrock_throughput_2276 import ARN, MODEL, Logs, NativeBedrock

pytestmark = pytest.mark.django_db


@pytest.fixture
def activity_service():
    svc = _make_service()
    svc.config = {"model_id": MODEL, "model_units": 1, "provisioned_throughput": "OneMonth"}
    svc.save(update_fields=["config"])
    native = NativeBedrock()
    instances = []
    observed = []

    class FreshDriver(AmazonBedrockDriver):
        def __init__(self, *, config):
            super().__init__(config=config, bedrock_client=native, logs_client=Logs())
            instances.append(self)

        @wraps(AmazonBedrockDriver.status)
        def status(self, handle, **kwargs):
            observed.append(handle)
            return super().status(handle, **kwargs)

        def binding(self, handle, config=None):
            observed.append(handle)
            return super().binding(handle, config=config)

    cfg = AmazonBedrockConfig(
        region="us-east-1", credential=CloudCredential(cloud="aws", declared_account="123456789012")
    )
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=FreshDriver),
        patch("core.cluster_observability.managed_config_for", return_value=cfg),
    ):
        yield svc, native, instances, observed


def test_actual_fresh_activities_persist_arn_and_current_owner(activity_service):
    svc, native, instances, observed = activity_service
    provisioned = _provision_sync(svc.pk)
    assert provisioned["ok"] and not provisioned["ready"]
    assert ARN in provisioned["handle"] and len(provisioned["handle"]) <= 512
    assert _check_ready_sync(svc.pk, provisioned["handle"]) == "provisioning"
    native.row["status"] = "InService"
    assert _check_ready_sync(svc.pk, provisioned["handle"]) == "available"
    _finalize_provision_sync(svc.pk, provisioned["handle"])
    svc.refresh_from_db()
    assert svc.backend_ref == provisioned["handle"]
    binding = _managed_binding_for(svc)
    assert binding.env_vars["BEDROCK_MODEL_ID"].literal == ARN
    assert all(handle.organization_id == str(svc.registered_app.organization.guid) for handle in observed)
    assert all(handle.managed_service_id == str(svc.guid) for handle in observed)
    assert len(instances) >= 5 and all(not driver._records for driver in instances)
    assert len(native.creates) == 1
    assert _provision_sync(svc.pk)["handle"] == svc.backend_ref


def test_same_service_current_owner_retarget_cannot_admit_saved_handle(activity_service):
    svc, native, _instances, _observed = activity_service
    provisioned = _provision_sync(svc.pk)
    native.row["status"] = "InService"
    svc.backend_ref = provisioned["handle"]
    foreign = Organization.objects.create(name="Other", slug="other-bedrock-2276")
    team = Team.objects.create(organization=foreign, name="Other", slug="other-bedrock")
    project = Project.objects.create(organization=foreign, team=team, name="Other", slug="other-bedrock")
    svc.registered_app = RegisteredApp.objects.create(
        organization=foreign, team=team, project=project, name="Other", slug="other-bedrock"
    )
    cluster = TenantCluster.objects.create(
        organization=foreign,
        provider_plugin=svc.app_environment.tenant_cluster.provider_plugin,
        name="Other",
        slug="other-bedrock",
    )
    svc.app_environment = AppEnvironment.objects.create(
        registered_app=svc.registered_app, name="production", tenant_cluster=cluster
    )
    svc.save(update_fields=["backend_ref", "registered_app", "app_environment"])
    assert _check_ready_sync(svc.pk, svc.backend_ref) == "error"
    with pytest.raises(ManagedServiceError):
        _managed_binding_for(svc)
    assert not _update_sync(svc.pk)["ok"]
    assert not _deprovision_sync(svc.pk, delete_data=True, force_destroy=True)["ok"]
    assert len(native.creates) == 1 and not native.deletes


@pytest.mark.parametrize(
    "change",
    [{"model_id": "other.model"}, {"model_id": "", "size": "large"}, {"provisioned_throughput": None}],
)
def test_same_service_config_substitution_refuses_fresh_readiness_and_binding(activity_service, change):
    svc, native, _instances, _observed = activity_service
    provisioned = _provision_sync(svc.pk)
    native.row["status"] = "InService"
    svc.backend_ref = provisioned["handle"]
    svc.config = dict(svc.config, **change)
    svc.save(update_fields=["backend_ref", "config"])
    assert _check_ready_sync(svc.pk, svc.backend_ref) == "error"
    with pytest.raises(ManagedServiceError):
        _managed_binding_for(svc)
    assert not _provision_sync(svc.pk)["ok"]
    assert not _update_sync(svc.pk)["ok"]
    assert len(native.creates) == 1 and not native.deletes


def test_status_handle_only_driver_remains_compatible(activity_service, monkeypatch):
    from types import SimpleNamespace

    svc, _native, _instances, observed = activity_service
    provisioned = _provision_sync(svc.pk)

    def status_handle_only(self, handle):
        observed.append(handle)
        return SimpleNamespace(state="provisioning")

    monkeypatch.setattr(type(_instances[0]), "status", status_handle_only)
    assert _check_ready_sync(svc.pk, provisioned["handle"]) == "provisioning"
    assert observed[-1].organization_id == str(svc.registered_app.organization.guid)


def test_provider_type_error_is_not_retried_as_signature_fallback(activity_service, monkeypatch):
    svc, _native, instances, _observed = activity_service
    provisioned = _provision_sync(svc.pk)
    calls = []

    def broken(self, handle, config=None):
        calls.append(config)
        raise TypeError("synthetic provider implementation failure")

    monkeypatch.setattr(type(instances[0]), "status", broken)
    with pytest.raises(TypeError, match="synthetic provider implementation failure"):
        _check_ready_sync(svc.pk, provisioned["handle"])
    assert calls == [svc.config]
