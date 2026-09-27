"""The platform's record of a handle proves ownership only while it is unique (#2086).

A GCP resource made before its driver stamped the managed-service id has one
piece of ownership evidence left: the ``backend_ref`` on the row that
provisioned it. Two live rows recording one handle is the cross-tenant
collision #2086 describes, so the lifecycle tells the driver whether the
record is exclusive, and the driver refuses to act on the record when it is
not. These tests pin what "exclusive" means and that every activity passes it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
    build_provision_spec,
)

pytestmark = pytest.mark.django_db

HANDLE = "graph_db/shared-graph/triage-prod-knowledge"


class _Recorder:
    """A driver that records every spec it is handed and reports success."""

    specs: list[Any]

    def __init__(self, *, config: Any) -> None:
        self._config = config

    def provision(self, spec: Any) -> Any:
        type(self).specs.append(spec)
        return SimpleNamespace(ok=True, handle=HANDLE, ready=True, message="", errors=[])

    def update(self, spec: Any) -> Any:
        type(self).specs.append(spec)
        return SimpleNamespace(ok=True, handle=spec.handle, message="", errors=[], retryable=False)

    def snapshot(self, handle: Any) -> Any:
        type(self).specs.append(handle)
        return SimpleNamespace(snapshot_id="backup-1", handle=handle.handle, created_at="")

    def deprovision(self, spec: Any, *, delete_data: bool = False, force_destroy: bool = False) -> Any:
        type(self).specs.append(spec)
        return SimpleNamespace(ok=True, handle=spec.handle, message="", errors=[], retryable=False)

    def binding(self, handle: Any) -> Any:
        type(self).specs.append(handle)
        return SimpleNamespace(env_vars={}, iam_grants=[], volumes=[])


class _Other(_Recorder):
    """A different driver for the same kind, with its own handle namespace."""


def _drivers(_plugin: str, role: str) -> type[_Recorder]:
    return _Other if role.endswith(":other") else _Recorder


def _config_for(_plugin: str, cluster: Any, **_kwargs: Any) -> Any:
    provider_config = cluster.provider_config or {}
    if provider_config.get("broken"):
        raise RuntimeError(f"cluster {cluster.slug}: managed config does not build")
    return SimpleNamespace(project_id=provider_config.get("project_id", ""))


@pytest.fixture(autouse=True)
def _resolution():
    _Recorder.specs = []
    _Other.specs = []
    with (
        patch("astrolift_drivers.registry.plugins.get", side_effect=_drivers),
        patch("core.cluster_observability.managed_config_for", side_effect=_config_for),
    ):
        yield


def _service(
    *,
    org_slug: str,
    project_id: str = "shared-project",
    plugin_slug: str = "gcp",
    backend_ref: str = HANDLE,
    variant: str = "spanner_graph",
) -> ManagedService:
    org = Organization.objects.create(name=org_slug, slug=org_slug)
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{org_slug}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{org_slug}")
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name=plugin_slug, slug=plugin_slug, plugin_version="0.0.1")],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"{org_slug}-cluster",
        name="Cluster",
        provider_plugin=ProviderPlugin.objects.get(slug=plugin_slug),
        endpoint="https://cluster.invalid",
        provider_config={"project_id": project_id},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Triage",
        slug="triage",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="prod", tenant_cluster=cluster)
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.GRAPH_DB,
        variant=variant,
        name="knowledge",
        config={},
        backend_ref=backend_ref,
        status=ManagedService.Status.ACTIVE,
    )


def _exclusive_on_update(svc: ManagedService) -> bool:
    _Recorder.specs = []
    _update_sync(svc.pk)
    (spec,) = _Recorder.specs
    return spec.recorded_handle_exclusive


def test_a_handle_no_other_live_service_records_is_exclusive() -> None:
    svc = _service(org_slug="acme-2086")
    assert _exclusive_on_update(svc) is True


def test_two_orgs_recording_one_handle_in_one_project_prove_nothing() -> None:
    victim = _service(org_slug="acme-2086")
    squatter = _service(org_slug="globex-2086")

    assert _exclusive_on_update(victim) is False
    assert _exclusive_on_update(squatter) is False


def test_the_same_handle_in_another_project_names_another_resource() -> None:
    first = _service(org_slug="acme-2086", project_id="acme-project")
    _service(org_slug="globex-2086", project_id="globex-project")

    assert _exclusive_on_update(first) is True


def test_a_soft_deleted_row_does_not_contest_the_record() -> None:
    svc = _service(org_slug="acme-2086")
    former = _service(org_slug="globex-2086")
    former.soft_delete()

    assert _exclusive_on_update(svc) is True


def test_a_row_another_driver_serves_does_not_contest_the_record() -> None:
    svc = _service(org_slug="acme-2086")
    _service(org_slug="globex-2086", variant="other")

    assert _exclusive_on_update(svc) is True


def test_a_row_that_cannot_be_placed_counts_against_the_record() -> None:
    svc = _service(org_slug="acme-2086")
    other = _service(org_slug="globex-2086")
    cluster = other.app_environment.tenant_cluster
    cluster.provider_config = {**cluster.provider_config, "broken": True}
    cluster.save()

    assert _exclusive_on_update(svc) is False


def test_exclusivity_is_not_established_outside_gcp() -> None:
    svc = _service(org_slug="acme-2086", plugin_slug="aws")
    assert _exclusive_on_update(svc) is False


def test_build_provision_spec_carries_the_recorded_handle() -> None:
    svc = _service(org_slug="acme-2086")
    fresh = _service(org_slug="globex-2086", backend_ref="")

    assert build_provision_spec(svc, cluster=svc.app_environment.tenant_cluster).recorded_handle == HANDLE
    assert build_provision_spec(fresh, cluster=fresh.app_environment.tenant_cluster).recorded_handle == ""


def test_reprovision_passes_the_record_and_its_exclusivity() -> None:
    svc = _service(org_slug="acme-2086")
    _provision_sync(svc.pk)
    (spec,) = _Recorder.specs
    assert spec.recorded_handle == HANDLE
    assert spec.recorded_handle_exclusive is True

    _service(org_slug="globex-2086")
    _Recorder.specs = []
    _provision_sync(svc.pk)
    (spec,) = _Recorder.specs
    assert spec.recorded_handle == HANDLE
    assert spec.recorded_handle_exclusive is False


def test_teardown_passes_exclusivity_to_the_snapshot_and_the_deprovision() -> None:
    svc = _service(org_slug="acme-2086")
    _deprovision_sync(svc.pk, False, False)

    snapshot, deprovision = _Recorder.specs
    assert snapshot.recorded_handle_exclusive is True
    assert deprovision.recorded_handle_exclusive is True

    _service(org_slug="globex-2086")
    _Recorder.specs = []
    _deprovision_sync(svc.pk, False, False)
    snapshot, deprovision = _Recorder.specs
    assert snapshot.recorded_handle_exclusive is False
    assert deprovision.recorded_handle_exclusive is False


def test_binding_passes_exclusivity() -> None:
    svc = _service(org_slug="acme-2086")
    _managed_binding_for(svc)
    (handle,) = _Recorder.specs
    assert handle.recorded_handle_exclusive is True
