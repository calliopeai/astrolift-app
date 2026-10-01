"""Old worker INSERT shapes remain valid after the real shared-model migrations."""

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db


@pytest.fixture
def old_writer(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *_: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *_: None))
    world = ScopeWorld("modelrolling2213")
    world.cluster = make_cluster(world, "modelrolling2213")
    world.environment = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="prod"
    )
    applied = MigrationRecorder(connection).applied_migrations()
    for name in (
        "0028_cluster_model_subscriptions",
        "0029_applied_model_subscription_revision",
        "0030_shared_model_readiness_observations",
        "0031_shared_model_operation_placement",
        "0032_shared_model_observed_handle",
    ):
        assert ("astrolift_services", name) in applied
    old_state = MigrationLoader(connection).project_state(
        [("astrolift_services", "0027_appsecretmetadata_follow_matching_app_wide_scope")]
    )
    world.old_service = old_state.apps.get_model("astrolift_services", "ManagedService")
    world.old_attachment = old_state.apps.get_model("astrolift_services", "ManagedServiceAttachment")
    return world


def assert_legacy_service(row):
    assert row.organization_id is None
    assert row.subscription_revision == row.applied_subscription_revision == 0
    assert row.model_ready_backend_ref == ""
    assert row.model_ready_observed_at is row.model_ready_generation is row.model_ready_auth_revision is None
    assert (
        row.model_operation_cluster_guid
        is row.model_operation_provider_guid
        is row.model_ready_provider_guid
        is None
    )


@pytest.mark.parametrize("owner", ["app", "project"])
def test_pre_upgrade_service_insert_omits_new_columns_and_keeps_legacy_ownership(old_writer, owner):
    w = old_writer
    fields = (
        {"registered_app_id": w.medops_app.pk, "app_environment_id": w.environment.pk}
        if owner == "app"
        else {"project_id": w.medops_project.pk, "tenant_cluster_id": w.cluster.pk}
    )
    statements = []

    def capture(execute, sql, params, many, context):
        if sql.startswith("INSERT INTO"):
            statements.append(sql.split("VALUES", 1)[0])
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        old = w.old_service.objects.create(kind="redis", name="legacy", variant="redis", **fields)
    assert statements and all(
        column not in statements[0]
        for column in ("organization_id", "subscription_revision", "model_ready_backend_ref")
    )
    row = ManagedService.objects.get(pk=old.pk)
    assert_legacy_service(row)
    assert row.registered_app_id == fields.get("registered_app_id")
    assert row.project_id == fields.get("project_id")


@pytest.mark.parametrize("consumer", ["environment", "recipe"])
def test_pre_upgrade_attachment_insert_preserves_legacy_active_binding(old_writer, consumer):
    w = old_writer
    # Isolate the old attachment writer: service setup must not mask a missing
    # attachment default with the earlier service INSERT failure.
    old_service = ManagedService.objects.create(
        project_id=w.medops_project.pk,
        tenant_cluster_id=w.cluster.pk,
        kind="redis",
        name="legacy",
        variant="redis",
    )
    if consumer == "environment":
        target = {"app_environment_id": w.environment.pk}
    else:
        recipe = AgentEnvironmentSpec.objects.create(
            organization=w.org, name="Legacy recipe", slug="legacy-model-rolling", agent_type="claude"
        )
        target = {"agent_environment_spec_id": recipe.pk}
    statements = []

    def capture(execute, sql, params, many, context):
        if sql.startswith("INSERT INTO"):
            statements.append(sql.split("VALUES", 1)[0])
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        old = w.old_attachment.objects.create(managed_service_id=old_service.pk, **target)
    assert statements and all(
        column not in statements[0]
        for column in ("model_subscription", "desired_revision", "binding_alias", "subscription_status")
    )
    row = ManagedServiceAttachment.objects.get(pk=old.pk)
    assert row.model_subscription is False and row.desired_enabled is True
    assert row.subscription_status == "active"
    assert row.binding_alias == row.credential_ref == row.reconcile_error == ""
    assert row.desired_revision == row.applied_revision == 0
    assert row.reconcile_started_at is row.reconciled_at is None
    assert row.app_environment_id == target.get("app_environment_id")
    assert row.agent_environment_spec_id == target.get("agent_environment_spec_id")
