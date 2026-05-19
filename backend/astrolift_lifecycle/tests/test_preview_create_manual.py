"""Tests for the ``create_preview_environment`` mutation (#751).

Covers:
  - happy path: fresh manual preview lands with ``is_manual=True``,
    ``pr_number=None``, branch-derived hostname/namespace + a
    BuildPreviewWorkflow start recorded on the temporal recorder
  - idempotent re-fire on the same ``(app, branch)`` returns the
    existing row rather than colliding on the unique index
  - rejection when ``preview_enabled`` is off on the app
  - rejection when the app has no default tenant cluster
  - rejection when ``branch`` is empty / non-slugifiable
  - permission denied without ``app.deploy``
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
from astrolift_lifecycle.schema.mutations import (
    CreatePreviewEnvironmentInput,
    LifecycleMutation,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _bind_default_cluster(app, cluster):
    """Mutations resolve the deploy target via ``default_tenant_cluster``;
    fixture-level apps don't bind one by default, so the tests opt in.
    """
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster", "updated_at", "version"])
    return app


def test_create_manual_preview_happy_path(
    org, app, cluster, actor, fake_info, permission_resolver, temporal_recorder
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    _bind_default_cluster(app, cluster)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(
                app_slug=app.slug,
                branch="feature/new-thing",
            ),
        )

    assert result.ok, result.errors
    assert result.data.branch == "feature/new-thing"
    assert result.data.pr_number == 0  # serializer coerces None → 0 via type
    assert result.data.is_manual is True
    # PreviewEnvironmentType has ``pr_number: int``; verify the underlying
    # row is actually null.
    preview = PreviewEnvironment.objects.get(registered_app=app, branch="feature/new-thing")
    assert preview.is_manual is True
    assert preview.pr_number is None
    assert preview.app_environment_id is not None
    # Branch slug normalization: '/' → '-'
    assert "feature-new-thing" in preview.namespace
    assert "feature-new-thing" in preview.hostname
    # AppEnvironment was carved out with the default name pattern.
    env = AppEnvironment.objects.get(pk=preview.app_environment_id)
    assert env.name == "preview-feature-new-thing"
    # BuildPreviewWorkflow start was recorded.
    kinds = {name for name, _, _ in temporal_recorder.starts}
    assert "BuildPreviewWorkflow" in kinds


def test_create_manual_preview_is_idempotent_on_branch(
    org, app, cluster, actor, fake_info, permission_resolver, temporal_recorder
):
    """Re-running on the same ``(app, branch)`` returns the existing
    row — mirrors ``addAppDomain``'s idempotent re-add behavior."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    _bind_default_cluster(app, cluster)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        first = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="feat-x"),
        )
        second = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="feat-x"),
        )

    assert first.ok and second.ok
    assert first.data.id == second.data.id
    # Only one PreviewEnvironment row exists for this (app, branch).
    assert (
        PreviewEnvironment.objects.filter(
            registered_app=app, branch="feat-x", deleted_at__isnull=True
        ).count()
        == 1
    )


def test_create_manual_preview_rejects_when_previews_disabled(
    org, app, cluster, actor, fake_info, permission_resolver, temporal_recorder
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    _bind_default_cluster(app, cluster)
    app.preview_enabled = False
    app.save(update_fields=["preview_enabled", "updated_at", "version"])
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="feat-y"),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "preview environments disabled" in result.errors[0].message


def test_create_manual_preview_rejects_when_no_default_cluster(
    org, app, actor, fake_info, permission_resolver, temporal_recorder
):
    """App with no default_tenant_cluster has no deploy target — bail
    rather than land an orphan row."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="feat-z"),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "default tenant cluster" in result.errors[0].message


def test_create_manual_preview_rejects_empty_branch(
    org, app, cluster, actor, fake_info, permission_resolver, temporal_recorder
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    _bind_default_cluster(app, cluster)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="   "),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


def test_create_manual_preview_rejects_non_slugifiable_branch(
    org, app, cluster, actor, fake_info, permission_resolver, temporal_recorder
):
    """A branch that contains no ``[a-z0-9]`` chars (e.g., punctuation
    only) can't produce a k8s-safe namespace fragment."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    _bind_default_cluster(app, cluster)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="///"),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


def test_create_manual_preview_not_found(org, actor, fake_info, permission_resolver, temporal_recorder):
    permission_resolver.grant(Permission.APP_DEPLOY)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(app_slug="nope", branch="feat"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_create_manual_preview_requires_permission(
    org, app, cluster, actor, fake_info, permission_resolver, temporal_recorder
):
    """Without ``app.deploy`` the request must be denied."""
    _bind_default_cluster(app, cluster)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="feat-perm"),
        )
    assert not result.ok
    # No row should have been created.
    assert not PreviewEnvironment.objects.filter(
        registered_app=app, branch="feat-perm", deleted_at__isnull=True
    ).exists()


def test_create_manual_preview_custom_environment_name(
    org, app, cluster, actor, fake_info, permission_resolver, temporal_recorder
):
    """Explicit ``environment_name`` lets operators run parallel
    previews on the same branch with distinct config carves."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    _bind_default_cluster(app, cluster)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(
                app_slug=app.slug,
                branch="feat-named",
                environment_name="preview-perf",
            ),
        )

    assert result.ok, result.errors
    preview = PreviewEnvironment.objects.get(registered_app=app, branch="feat-named")
    env = AppEnvironment.objects.get(pk=preview.app_environment_id)
    assert env.name == "preview-perf"
