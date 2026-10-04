"""Legacy app/project ownership does not bypass installation hosting authority."""

import json

import pytest

from astrolift_graphql import GUID
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken
from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations import ServicesMutation
from astrolift_services.schema.mutations.types import (
    AdoptManagedResourceInput,
    DeprovisionManagedServiceInput,
    ProvisionManagedServiceInput,
    ProvisionProjectManagedServiceInput,
    ReprovisionManagedServiceInput,
    UpdateManagedServiceInput,
)
from astrolift_services.tests.model_hosting_helpers import promote_host_operator
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_model_hosting_sources import world as world_fixture
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role, make_info

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    w = world_fixture.__wrapped__(monkeypatch)
    bind_role(
        w.user,
        permissions=[Permission.APP_UPDATE, Permission.PROJECT_UPDATE, Permission.MANAGED_SERVICE_ADOPT],
        kind="ORG",
        scope_id=w.org.pk,
        slug="legacy-hosting-org-owner",
    )
    w.calls = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow", lambda *args, **kwargs: w.calls.append(args)
    )
    # These tests isolate authority before catalog validation and external effects.
    monkeypatch.setattr("astrolift_services.managed_service_catalog.resolve_variant", lambda **kwargs: None)
    return w


def service(w, owner, kind="model_endpoint"):
    values = {
        "kind": kind,
        "name": "legacy",
        "variant": "vllm",
        "status": "active",
        "config": {"model": "literal/repo"},
    }
    if owner == "app":
        values.update(registered_app=w.medops_app, app_environment=w.env)
    else:
        values.update(project=w.medops_project, tenant_cluster=w.cluster)
    return ManagedService.objects.create(**values)


@pytest.mark.parametrize("owner", ["app", "project"])
@pytest.mark.parametrize("action", ["provision", "update", "reprovision", "deprovision", "adopt"])
def test_all_legacy_model_writes_refuse_fully_granted_ordinary_owner(world, owner, action, monkeypatch):
    row = service(world, owner)
    guid = GUID(str(row.guid))
    original = (row.name, row.status, row.version, dict(row.config))
    monkeypatch.setattr(
        "astrolift_services.managed_resource_adoption.adopt_managed_resource",
        lambda **kwargs: pytest.fail("ordinary owner reached provider adoption"),
    )
    mutations = ServicesMutation()
    prefix = "project_" if owner == "project" else ""
    if action == "provision":
        if owner == "app":
            value = ProvisionManagedServiceInput(
                app_slug=world.medops_app.slug,
                environment_name=world.env.name,
                kind="model_endpoint",
                name="new-model",
            )
        else:
            value = ProvisionProjectManagedServiceInput(
                project_id=GUID(str(world.medops_project.guid)),
                cluster_id=GUID(str(world.cluster.guid)),
                kind="model_endpoint",
                name="new-model",
            )
    elif action == "update":
        value = UpdateManagedServiceInput(id=guid, name="changed")
    elif action == "reprovision":
        value = ReprovisionManagedServiceInput(managed_service_id=guid)
    elif action == "deprovision":
        value = DeprovisionManagedServiceInput(id=guid)
    else:
        value = AdoptManagedResourceInput(id=guid, resource_id="literal-resource", reason="reviewed")
        prefix = ""
    call = getattr(
        mutations, f"{action}_{prefix}managed_service" if action != "adopt" else "adopt_managed_resource"
    )
    with subject(world):
        result = call(make_info(world.user), input=value)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result.errors
    row.refresh_from_db()
    assert (row.name, row.status, row.version, row.config) == original
    assert not world.calls and ManagedService.objects.count() == 2


@pytest.mark.parametrize("owner", ["app", "project"])
@pytest.mark.parametrize("kind,operator", [("redis", False), ("model_endpoint", True)])
def test_naming_existing_resource_keeps_nonmodel_owner_permissions(world, owner, kind, operator):
    row = service(world, owner, kind)
    if operator:
        promote_host_operator(world)
    call = getattr(ServicesMutation(), f"update_{'project_' if owner == 'project' else ''}managed_service")
    with subject(world):
        result = call(
            make_info(world.user), input=UpdateManagedServiceInput(id=GUID(str(row.guid)), name="renamed")
        )
    assert result.ok, result.errors
    row.refresh_from_db()
    assert row.name == "renamed" and row.config == {"model": "literal/repo"} and not world.calls


def test_actual_http_legacy_model_provision_is_denied_without_rows_or_dispatch(world, client):
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Legacy HTTP",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )
    response = client.post(
        "/app/gql/config/",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {minted.plaintext}",
        HTTP_X_ASTROLIFT_ORG=str(world.org.guid),
        data=json.dumps(
            {
                "query": "mutation($input:ProvisionManagedServiceInput!){provisionManagedService(input:$input){ok errors{code message} data{id}}}",
                "variables": {
                    "input": {
                        "appSlug": world.medops_app.slug,
                        "environmentName": world.env.name,
                        "kind": "model_endpoint",
                    }
                },
            }
        ),
    )
    payload = response.json()
    assert response.status_code == 200 and not payload.get("errors"), payload
    result = payload["data"]["provisionManagedService"]
    assert not result["ok"] and result["errors"][0]["code"] == "PERMISSION_DENIED"
    assert not world.calls and ManagedService.objects.count() == 1


@pytest.mark.parametrize("owner", ["app", "project"])
def test_legacy_operator_provision_retains_exact_app_or_project_owner(world, owner):
    promote_host_operator(world)
    if owner == "app":
        value = ProvisionManagedServiceInput(
            app_slug=world.medops_app.slug,
            environment_name=world.env.name,
            kind="model_endpoint",
            name="new-operator-model",
        )
    else:
        value = ProvisionProjectManagedServiceInput(
            project_id=GUID(str(world.medops_project.guid)),
            cluster_id=GUID(str(world.cluster.guid)),
            kind="model_endpoint",
            name="new-operator-model",
        )
    call = getattr(ServicesMutation(), f"provision_{'project_' if owner == 'project' else ''}managed_service")
    with subject(world):
        result = call(make_info(world.user), input=value)
    assert result.ok, result.errors
    row = ManagedService.objects.get(guid=result.data.id)
    assert row.organization_id is None and row.kind == "model_endpoint"
    assert row.registered_app_id == (world.medops_app.pk if owner == "app" else None)
    assert row.project_id == (world.medops_project.pk if owner == "project" else None)
    assert row.effective_cluster.pk == world.cluster.pk
    assert len(world.calls) == 1 and world.calls[0][0] == "ProvisionManagedServiceWorkflow"
