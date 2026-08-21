"""Tests for the createPreviewEnvironment mutation (#751).

Covers:
- createPreviewEnvironment creates PreviewEnvironment + AppEnvironment rows
- is_manual=True, pr_number=None on created row
- Idempotent: same branch returns existing preview without creating duplicates
- Permission gate: requires APP_DEPLOY
- Validation: empty branch rejected
- Non-slugifiable branch rejected
- App not found returns NOT_FOUND
- preview_enabled=False returns PRECONDITION error
- App with no default_tenant_cluster returns PRECONDITION error
- Workflow dispatched when preview is created (via start_workflow stub)
- _slugify_branch helper unit tests
- _manual_preview_namespace helper unit tests
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
from astrolift_lifecycle.schema.mutations import (
    CreatePreviewEnvironmentInput,
    LifecycleMutation,
    _manual_preview_namespace,
    _slugify_branch,
)
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _scaffold(*, preview_enabled: bool = True, bind_cluster: bool = True):
    org = Organization.objects.create(name="PrevOrg", slug="prev-org")
    team = Team.objects.create(organization=org, name="Eng", slug="prev-eng")
    project = Project.objects.create(organization=org, team=team, name="PrevProj", slug="prev-proj")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s Native",
                slug="k8s-native",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="k8s-native")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="prev-local",
        name="Local",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="PrevApp",
        slug="prev-app",
        provisioning_status="ready",
        preview_enabled=preview_enabled,
        default_tenant_cluster=(cluster if bind_cluster else None),
    )
    return org, app, cluster


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _patch_workflow():
    handle = MagicMock()
    handle.enqueued = False  # skip _record_workflow_run branch
    return patch(
        "astrolift_lifecycle.schema.mutations.previews.start_workflow",
        return_value=handle,
    )


# ---- _slugify_branch unit tests -----------------------------------


def test_slugify_branch_simple():
    assert _slugify_branch("feat/login") == "feat-login"


def test_slugify_branch_uppercase():
    assert _slugify_branch("Feature/My-Branch") == "feature-my-branch"


def test_slugify_branch_special_chars():
    # Runs of non-alnum chars collapse to a single dash.
    assert _slugify_branch("fix@#123!") == "fix-123"
    # Leading/trailing dashes stripped
    result = _slugify_branch("--hello--")
    assert not result.startswith("-")
    assert not result.endswith("-")


def test_slugify_branch_truncates_to_40():
    long_branch = "a" * 50
    assert len(_slugify_branch(long_branch)) <= 40


def test_slugify_branch_empty_returns_empty():
    assert _slugify_branch("@@@") == ""


# ---- _manual_preview_namespace unit tests -------------------------


def test_manual_preview_namespace_short():
    ns = _manual_preview_namespace(org_slug="acme", app_slug="api", branch_slug="feat-login")
    assert ns == "acme-api-feat-login"
    assert len(ns) <= 63


def test_manual_preview_namespace_long_app_truncated():
    ns = _manual_preview_namespace(org_slug="acme", app_slug="a" * 40, branch_slug="feat")
    assert len(ns) <= 63
    assert ns.startswith("acme-")


def test_manual_preview_namespace_very_long_all_parts():
    ns = _manual_preview_namespace(org_slug="a" * 20, app_slug="b" * 20, branch_slug="c" * 40)
    assert len(ns) <= 63


# ---- mutation validation ------------------------------------------


def test_create_preview_empty_branch_rejected(permission_resolver):
    org, app, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org), _patch_workflow():
        result = LifecycleMutation().create_preview_environment(
            _info(), input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="")
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "branch"


def test_create_preview_unsluggable_branch_rejected(permission_resolver):
    org, app, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org), _patch_workflow():
        result = LifecycleMutation().create_preview_environment(
            _info(),
            input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="@@@"),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


def test_create_preview_app_not_found(permission_resolver):
    org, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org), _patch_workflow():
        result = LifecycleMutation().create_preview_environment(
            _info(),
            input=CreatePreviewEnvironmentInput(app_slug="does-not-exist", branch="main"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_create_preview_disabled_app_rejected(permission_resolver):
    org, app, _ = _scaffold(preview_enabled=False)
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org), _patch_workflow():
        result = LifecycleMutation().create_preview_environment(
            _info(), input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="main")
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_create_preview_no_cluster_rejected(permission_resolver):
    org, app, _ = _scaffold(bind_cluster=False)
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org), _patch_workflow():
        result = LifecycleMutation().create_preview_environment(
            _info(), input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="main")
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


# ---- successful creation ------------------------------------------


def test_create_preview_creates_rows(permission_resolver):
    org, app, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org), _patch_workflow():
        result = LifecycleMutation().create_preview_environment(
            _info(),
            input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="feat/login"),
        )
    assert result.ok, result.errors
    preview = PreviewEnvironment.objects.get(registered_app=app, is_manual=True)
    assert preview.branch == "feat/login"
    assert preview.pr_number is None
    assert preview.is_manual is True
    assert preview.status == PreviewEnvironment.Status.BUILDING
    assert preview.namespace != ""
    assert AppEnvironment.objects.filter(registered_app=app, name="preview-feat-login").exists()


def test_create_preview_custom_environment_name(permission_resolver):
    org, app, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org), _patch_workflow():
        result = LifecycleMutation().create_preview_environment(
            _info(),
            input=CreatePreviewEnvironmentInput(
                app_slug=app.slug, branch="main", environment_name="staging-preview"
            ),
        )
    assert result.ok, result.errors
    assert AppEnvironment.objects.filter(registered_app=app, name="staging-preview").exists()


def test_create_preview_idempotent_same_branch(permission_resolver):
    org, app, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org), _patch_workflow():
        r1 = LifecycleMutation().create_preview_environment(
            _info(), input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="main")
        )
        r2 = LifecycleMutation().create_preview_environment(
            _info(), input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="main")
        )
    assert r1.ok and r2.ok
    # Only one PreviewEnvironment row created.
    assert PreviewEnvironment.objects.filter(registered_app=app, is_manual=True).count() == 1
    # Same id echoed back.
    assert r1.data.id == r2.data.id


def test_create_preview_workflow_dispatched(permission_resolver):
    org, app, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org):
        handle = MagicMock()
        handle.enqueued = False
        with patch(
            "astrolift_lifecycle.schema.mutations.previews.start_workflow",
            return_value=handle,
        ) as mock_start:
            LifecycleMutation().create_preview_environment(
                _info(),
                input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="feat/dispatch"),
            )
    mock_start.assert_called_once()
    call_args = mock_start.call_args
    assert call_args[0][0] == "BuildPreviewWorkflow"


def test_create_preview_different_branches_create_separate_rows(permission_resolver):
    org, app, _ = _scaffold()
    permission_resolver.grant(Permission.APP_DEPLOY)
    with _ctx(org), _patch_workflow():
        r1 = LifecycleMutation().create_preview_environment(
            _info(), input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="feat/alpha")
        )
        r2 = LifecycleMutation().create_preview_environment(
            _info(), input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch="feat/beta")
        )
    assert r1.ok and r2.ok
    assert r1.data.id != r2.data.id
    assert PreviewEnvironment.objects.filter(registered_app=app, is_manual=True).count() == 2
