"""
Constance-backed deploy-pipeline gate (#359).

The deploy pipeline kill-switch moved from ``config.features.Feature.
DEPLOY_PIPELINE`` (env-var only) to ``constance.config.DEPLOY_PIPELINE_
ENABLED`` so operators can pause rollouts at runtime via /app/admin/
constance/ without a redeploy — parity with TEMPORAL_ENABLED.

Precedence: Constance wins when set; the ``FEATURE_DEPLOY_PIPELINE``
env var only seeds the default at first boot. When Constance is
unavailable the resolver falls back to the env-backed
``is_enabled(Feature.DEPLOY_PIPELINE)`` so unit tests that don't load
Constance keep working.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    StartDeploymentInput,
    _deploy_pipeline_disabled,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _grant_deploy(resolver):
    resolver.grant(Permission.APP_DEPLOY)


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


@pytest.fixture
def _constance_flag(monkeypatch):
    """Set the Constance DEPLOY_PIPELINE_ENABLED value for the test.

    Constance's default backend is a Django model; rather than
    round-tripping a DB write we monkeypatch the import that the
    resolver consumes. This matches how _deploy_pipeline_disabled
    actually reads the flag (``from constance import config``).
    """
    from types import SimpleNamespace

    state = {"DEPLOY_PIPELINE_ENABLED": True}
    fake_module = SimpleNamespace(config=SimpleNamespace())

    def _setter(value: bool) -> None:
        state["DEPLOY_PIPELINE_ENABLED"] = value
        fake_module.config.DEPLOY_PIPELINE_ENABLED = value

    _setter(True)
    monkeypatch.setitem(__import__("sys").modules, "constance", fake_module)
    return _setter


def test_deploy_pipeline_disabled_helper_reads_constance(_constance_flag):
    _constance_flag(True)
    assert _deploy_pipeline_disabled() is False

    _constance_flag(False)
    assert _deploy_pipeline_disabled() is True


def test_start_deployment_refused_when_constance_flag_false(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder, _constance_flag
):
    """When DEPLOY_PIPELINE_ENABLED is False every start_deployment
    short-circuits with the PRECONDITION envelope. The workflow must
    not be enqueued."""
    _constance_flag(False)
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1.0.0",
                trigger_kind="manual",
            ),
        )

    assert result.ok is False
    assert result.data is None
    assert any("deploy pipeline is disabled" in (err.message or "") for err in result.errors)
    assert Deployment.objects.count() == 0
    assert temporal_recorder.starts == []


def test_start_deployment_succeeds_when_constance_flag_true(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder, _constance_flag
):
    """When the Constance flag is True the gate is a no-op and the
    deployment row + workflow enqueue proceed normally."""
    _constance_flag(True)
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1.0.0",
                trigger_kind="manual",
            ),
        )

    assert result.ok, result.errors
    assert Deployment.objects.count() == 1
    assert temporal_recorder.starts and temporal_recorder.starts[0][0] == "DeployAppWorkflow"


def test_deploy_pipeline_disabled_falls_back_to_env_when_constance_missing(monkeypatch):
    """If Constance cannot be imported (DB down, plugin disabled) the
    helper must fall back to the env-backed feature flag rather than
    crash the mutation envelope."""
    import sys

    monkeypatch.setitem(sys.modules, "constance", None)
    # config.features.is_enabled defaults FEATURE_DEPLOY_PIPELINE=True
    monkeypatch.delenv("FEATURE_DEPLOY_PIPELINE", raising=False)
    assert _deploy_pipeline_disabled() is False

    monkeypatch.setenv("FEATURE_DEPLOY_PIPELINE", "false")
    assert _deploy_pipeline_disabled() is True
