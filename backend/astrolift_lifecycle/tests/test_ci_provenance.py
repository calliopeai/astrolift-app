"""Tests for CI / VCS provenance on deployments (#166).

Per spec 14 §18 every Deployment row captures the actor kind +
commit + branch + CI run URL + provider. The fields are populated
by either the UI mutation (StartDeploymentInput) or the SCM webhook
path; both should round-trip through to the same DB columns.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    StartDeploymentInput,
)
from core.permissions import Permission

pytestmark = pytest.mark.django_db


def test_start_deployment_persists_ci_metadata(
    org,
    app,
    env,
    fake_info,
    permission_resolver,
    settings,
):
    """The StartDeploymentInput accepts every CI field as optional;
    when set, they land on the Deployment row verbatim."""
    # Disable Temporal — test is about DB persistence, not workflow
    # enqueueing. The kill switch lives on
    # ``settings.ASTROLIFT_TEMPORAL_ENABLED`` and short-circuits
    # ``start_workflow`` before any Temporal-server I/O.
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.APP_DEPLOY)

    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=org.id)):
        result = LifecycleMutation().start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="abc123",
                trigger_kind="ci",
                ci_actor_kind="github-action",
                commit_sha="deadbeef" * 5,
                branch="feat/awesome-thing",
                ci_run_url="https://github.com/acme/hello/actions/runs/42",
                ci_provider="github_actions",
            ),
        )

    assert result.ok, result.errors
    d = Deployment.objects.get(guid=str(result.data.id))
    assert d.ci_actor_kind == "github-action"
    assert d.commit_sha == "deadbeef" * 5
    assert d.branch == "feat/awesome-thing"
    assert d.ci_run_url == "https://github.com/acme/hello/actions/runs/42"
    assert d.ci_provider == "github_actions"


def test_manual_deployment_leaves_ci_metadata_empty(org, app, env, fake_info, permission_resolver, settings):
    """The fields are optional. A UI deploy that doesn't set them
    persists empty strings — DEFINITELY not nulls or 'N/A' — so
    queries don't have to coalesce."""
    # Disable Temporal — this test is about DB persistence, not
    # workflow enqueueing. Without this the resolver tries to dial
    # ``temporal-server`` and fails when the suite is run in
    # isolation (the sibling test in this module that explicitly
    # toggles the kill switch wasn't masking the bug — order
    # dependency would have hit any order eventually).
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.APP_DEPLOY)
    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=org.id)):
        result = LifecycleMutation().start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="abc123",
            ),
        )

    d = Deployment.objects.get(guid=str(result.data.id))
    assert d.ci_actor_kind == ""
    assert d.commit_sha == ""
    assert d.branch == ""
    assert d.ci_run_url == ""
    assert d.ci_provider == ""


# ---------------------------------------------------------------------------
# #722 — GitHub PR provenance + commit-author avatar
# ---------------------------------------------------------------------------


def test_start_deployment_persists_pr_provenance(org, app, env, fake_info, permission_resolver, settings):
    """PR-triggered deploys carry pr_number + commit_author_avatar_url
    on the input; both round-trip to the Deployment row verbatim."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.APP_DEPLOY)
    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=org.id)):
        result = LifecycleMutation().start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="abc123",
                pr_number=1234,
                commit_author="octocat",
                commit_author_avatar_url="https://avatars.githubusercontent.com/u/583231?v=4",
            ),
        )

    assert result.ok, result.errors
    d = Deployment.objects.get(guid=str(result.data.id))
    assert d.pr_number == 1234
    assert d.commit_author == "octocat"
    assert d.commit_author_avatar_url == "https://avatars.githubusercontent.com/u/583231?v=4"


def test_manual_deployment_leaves_pr_fields_zero(org, app, env, fake_info, permission_resolver, settings):
    """Manual UI deploys don't have a PR — pr_number defaults to 0
    (not NULL), avatar URL defaults to "" (FE renders a dash)."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.APP_DEPLOY)
    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=org.id)):
        result = LifecycleMutation().start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="abc123",
            ),
        )

    d = Deployment.objects.get(guid=str(result.data.id))
    assert d.pr_number == 0
    assert d.commit_author_avatar_url == ""


def test_deployment_to_type_derives_pr_url_when_repo_known(
    org, app, env, fake_info, permission_resolver, settings,
):
    """``deployment_to_type`` builds ``pr_url`` from the registered
    app's ``source_url`` + ``pr_number`` — matching the preview-env
    pattern. When ``pr_number == 0`` the URL stays empty so the FE
    doesn't render a broken link."""
    from astrolift_lifecycle.schema.types import deployment_to_type

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.APP_DEPLOY)
    app.source_url = "https://github.com/acme/hello"
    app.save(update_fields=["source_url"])

    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=org.id)):
        result = LifecycleMutation().start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="abc123",
                pr_number=42,
            ),
        )

    d = Deployment.objects.get(guid=str(result.data.id))
    t = deployment_to_type(d)
    assert t.pr_number == 42
    assert t.pr_url == "https://github.com/acme/hello/pull/42"

    # No PR → no URL even if the repo is known
    with tenant_context(TenantContext(organization_id=org.id)):
        manual = LifecycleMutation().start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="def456",
            ),
        )

    dm = Deployment.objects.get(guid=str(manual.data.id))
    tm = deployment_to_type(dm)
    assert tm.pr_number == 0
    assert tm.pr_url == ""
