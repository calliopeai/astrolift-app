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


def test_manual_deployment_leaves_ci_metadata_empty(org, app, env, fake_info, permission_resolver):
    """The fields are optional. A UI deploy that doesn't set them
    persists empty strings — DEFINITELY not nulls or 'N/A' — so
    queries don't have to coalesce."""
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
