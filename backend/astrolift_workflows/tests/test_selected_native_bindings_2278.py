"""Selected literal projection vs actual complete HTTP/PG/native-source union."""

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_services.models import ManagedService, ManagedServiceAttachment, ManagedServiceBinding
from astrolift_services.tests.test_gcp_app_identity_plan_2278 import plan
from astrolift_services.tests.test_gcp_app_identity_plan_2278 import world as source_world
from astrolift_workflows.activities.app_lifecycle import _bindings_secret_name
from astrolift_workflows.native_endpoint_configuration import _selected_endpoint_bindings

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch, client):
    return source_world.__wrapped__(monkeypatch, client)


def deployment(w):
    return Deployment.objects.create(registered_app=w.medops_app, app_environment=w.env, status="pending")


@pytest.mark.parametrize("selected", ["absent", "disabled"])
def test_sibling_desired_project_endpoint_stays_in_iam_union_but_has_no_selected_literals(world, selected):
    w = world
    sibling = AppEnvironment.objects.create(
        registered_app=w.medops_app, tenant_cluster=w.cluster, name="sibling"
    )
    ManagedServiceAttachment.objects.create(managed_service=w.service, app_environment=sibling)
    ManagedServiceBinding.objects.create(
        managed_service=w.service, env_key="MODEL_ROUTE_MARKER", env_value_ref="sibling-only", is_secret=False
    )
    if selected == "absent":
        w.attachment.soft_delete()
    else:
        ManagedServiceAttachment.objects.filter(pk=w.attachment.pk).update(desired_enabled=False)
    actual = plan(w)
    assert {row.service_guid for row in actual.snapshot.endpoints} == {str(w.service.guid)}
    assert actual.template.permissions and w.source.calls
    assert _selected_endpoint_bindings(deployment(w), actual.snapshot) == {}


@pytest.mark.parametrize("workloads", [[], ["web"]])
def test_selected_project_attachment_keeps_exact_literal_workload_delivery(world, workloads):
    w = world
    ManagedServiceAttachment.objects.filter(pk=w.attachment.pk).update(workload_names=workloads)
    ManagedServiceBinding.objects.create(
        managed_service=w.service, env_key="MODEL_ROUTE_MARKER", env_value_ref="selected", is_secret=False
    )
    actual = plan(w)
    result = _selected_endpoint_bindings(deployment(w), actual.snapshot)
    assert set(result) == {_bindings_secret_name(w.medops_app.slug, "web" if workloads else None)}
    assert next(iter(result.values()))["MODEL_ROUTE_MARKER"] == "selected"
    assert actual.template.permissions


def test_direct_scope_projection_excludes_sibling_override_and_preserves_selected_service(world):
    """Pure row-scope projection: the GUID list here is not an authority receipt."""
    w = world
    sibling = AppEnvironment.objects.create(
        registered_app=w.medops_app, tenant_cluster=w.cluster, name="sibling"
    )
    selected = ManagedService.objects.create(
        registered_app=w.medops_app, app_environment=w.env, kind="model_endpoint", name="a-selected"
    )
    other = ManagedService.objects.create(
        registered_app=w.medops_app, app_environment=sibling, kind="model_endpoint", name="z-sibling"
    )
    for service, value in ((selected, "selected"), (other, "sibling-override")):
        ManagedServiceBinding.objects.create(
            managed_service=service, env_key="MODEL_ROUTE_MARKER", env_value_ref=value, is_secret=False
        )
    projection_ids = SimpleNamespace(
        endpoints=tuple(SimpleNamespace(service_guid=str(row.guid)) for row in (selected, other))
    )
    result = _selected_endpoint_bindings(deployment(w), projection_ids)
    assert result == {_bindings_secret_name(w.medops_app.slug): {"MODEL_ROUTE_MARKER": "selected"}}
