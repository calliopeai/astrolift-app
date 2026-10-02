"""Preview routing follows the actual FK and refuses retired/stale bindings."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
from astrolift_lifecycle.preview_targets import check_preview_target, preview_binding

pytestmark = pytest.mark.django_db


@pytest.fixture
def preview(app, env):
    # A canonical persisted namespace, deliberately without preview-name hints.
    env.name = "arbitrary-review-target"
    env.k8s_namespace = "isolated-preview-target"
    env.save()
    return PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=env,
        pr_number=42,
        branch="same-branch",
        status="running",
        hostname="reused.example.invalid",
        namespace=env.k8s_namespace,
    )


def check(preview, target):
    return check_preview_target(
        preview,
        environment_id=target.environment_id,
        preview_version=target.preview_version,
        environment_version=target.environment_version,
    )


def test_actual_fk_ignores_names_and_hostname(preview):
    state, target = preview_binding(preview)
    assert state == "available"
    assert target.environment_id == str(preview.app_environment.guid)
    assert target.environment_name == "arbitrary-review-target"
    assert target.app_id == str(preview.registered_app.guid)
    assert check(preview, target) == target
    preview.hostname = "production.example.invalid"
    assert preview_binding(preview) == (state, target)


@pytest.mark.parametrize("change", ["preview_version", "environment_version", "environment_id"])
def test_reviewed_binding_refuses_stale_versions_and_other_identity(preview, change):
    _, target = preview_binding(preview)
    altered = replace(target, **{change: str(uuid4()) if change == "environment_id" else 0})
    with pytest.raises(ValueError, match="unavailable or has changed"):
        check(preview, altered)


@pytest.mark.parametrize("retirement", ["status", "timestamp", "environment"])
def test_retirement_keeps_historical_identity_but_never_routes(preview, retirement):
    _, reviewed = preview_binding(preview)
    if retirement == "status":
        preview.status = "torn_down"
    elif retirement == "timestamp":
        preview.torn_down_at = timezone.now()
    else:
        preview.app_environment.deleted_at = timezone.now()
    state, historical = preview_binding(preview)
    assert state == "retired" and historical == reviewed
    with pytest.raises(ValueError, match="unavailable or has changed"):
        check(preview, reviewed)


@pytest.mark.parametrize("mapping", ["null", "namespace", "app_namespace", "inactive_cluster", "foreign_app"])
def test_unavailable_binding_never_uses_production_or_other_environment(preview, app, cluster, mapping):
    if mapping == "null":
        # A historical projection with an absent FK; today's DB keeps it required.
        preview.app_environment_id = None
    elif mapping == "namespace":
        preview.app_environment.k8s_namespace = "some-other-namespace"
    elif mapping == "app_namespace":
        from core.cluster_observability import namespace_for_app

        preview.namespace = preview.app_environment.k8s_namespace = namespace_for_app(app)
    elif mapping == "inactive_cluster":
        preview.app_environment.tenant_cluster.is_active = False
    else:
        preview.app_environment.registered_app_id = app.pk + 999
    assert preview_binding(preview) == ("unavailable", None)


def test_deleted_environment_name_replacement_never_retargets(preview, app, cluster):
    _, reviewed = preview_binding(preview)
    old_environment = preview.app_environment
    old_environment.soft_delete()
    replacement = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name=old_environment.name,
        k8s_namespace=old_environment.k8s_namespace,
        url=f"https://{preview.hostname}",
    )
    preview.refresh_from_db()
    state, target = preview_binding(preview)
    assert state == "retired"
    assert target.environment_id == reviewed.environment_id
    assert target.environment_id != str(replacement.guid)
    with pytest.raises(ValueError, match="unavailable or has changed"):
        check(preview, reviewed)
