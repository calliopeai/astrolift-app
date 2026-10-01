"""The reviewed constraint replacements retain live legacy and model uniqueness."""

import pytest
from django.db import IntegrityError, transaction

from astrolift_lifecycle.models import AppEnvironment
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *_: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *_: None))
    w = ScopeWorld("modelconstraints2213")
    w.cluster = make_cluster(w, "modelconstraints2213")
    w.env = AppEnvironment.objects.create(registered_app=w.medops_app, tenant_cluster=w.cluster, name="prod")
    return w


def test_owner_replacement_keeps_legacy_rows_and_rejects_missing_or_mixed_owners(world):
    app = ManagedService.objects.create(
        registered_app=world.medops_app, app_environment=world.env, kind="redis", name="app"
    )
    project = ManagedService.objects.create(
        project=world.medops_project, tenant_cluster=world.cluster, kind="redis", name="project"
    )
    shared = ManagedService.objects.create(
        organization=world.org,
        tenant_cluster=world.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="shared",
    )
    for invalid in (
        {},
        {"organization": world.org, "tenant_cluster": world.cluster},
        {
            "registered_app": world.medops_app,
            "app_environment": world.env,
            "project": world.medops_project,
            "tenant_cluster": world.cluster,
        },
    ):
        with pytest.raises(IntegrityError, match="msvc_exactly_one_owner_scope"), transaction.atomic():
            ManagedService.objects.create(kind="redis", name="invalid", **invalid)
    assert ManagedService.objects.get(pk=app.pk).app_environment_id == world.env.pk
    assert ManagedService.objects.get(pk=project.pk).project_id == world.medops_project.pk
    assert ManagedService.objects.get(pk=shared.pk).organization_id == world.org.pk


def test_legacy_and_named_subscription_live_uniqueness_and_soft_delete_reuse(world):
    legacy = ManagedService.objects.create(
        project=world.medops_project, tenant_cluster=world.cluster, kind="redis", name="legacy"
    )
    model = ManagedService.objects.create(
        organization=world.org,
        tenant_cluster=world.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="shared",
    )
    legacy_row = ManagedServiceAttachment.objects.create(managed_service=legacy, app_environment=world.env)
    with pytest.raises(IntegrityError, match="msvc_attachment_unique_app_env"), transaction.atomic():
        ManagedServiceAttachment.objects.create(managed_service=legacy, app_environment=world.env)
    aliases = []
    for alias in ("chat", "embed"):
        aliases.append(
            ManagedServiceAttachment.objects.create(
                managed_service=model,
                app_environment=world.env,
                model_subscription=True,
                binding_alias=alias,
                subscription_status="pending",
            )
        )
    other_model = ManagedService.objects.create(
        organization=world.org,
        tenant_cluster=world.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="other-shared",
    )
    with pytest.raises(IntegrityError, match="msvc_model_alias_unique_app_env"), transaction.atomic():
        ManagedServiceAttachment.objects.create(
            managed_service=other_model,
            app_environment=world.env,
            model_subscription=True,
            binding_alias="chat",
        )
    legacy_row.soft_delete()
    replacement = ManagedServiceAttachment.objects.create(managed_service=legacy, app_environment=world.env)
    aliases[0].soft_delete()
    named_replacement = ManagedServiceAttachment.objects.create(
        managed_service=model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        subscription_status="pending",
    )
    assert replacement.pk != legacy_row.pk and named_replacement.pk != aliases[0].pk
    assert ManagedServiceAttachment.all_objects.get(pk=legacy_row.pk).deleted_at is not None
    assert ManagedServiceAttachment.all_objects.get(pk=aliases[0].pk).deleted_at is not None
    assert ManagedServiceAttachment.objects.filter(managed_service=model).count() == 2
