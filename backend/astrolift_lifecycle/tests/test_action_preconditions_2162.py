# ruff: noqa: F811
"""Real PostgreSQL rows and grants protect the four quick actions before effects."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.schema.mutations import (
    DeploymentByIdInput,
    LifecycleMutation,
    RestartWorkloadInput,
    ScaleWorkloadInput,
)
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, subject, world  # noqa: F401
from astrolift_registry.models import Workload
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role, make_info

pytestmark = pytest.mark.django_db
ACTIONS = ("rollback", "redeploy", "restart", "scale")


@pytest.fixture
def targets(world):
    bind_role(
        world.user,
        permissions=[Permission.APP_ROLLBACK, Permission.APP_DEPLOY],
        kind="ORG",
        scope_id=world.org.pk,
        slug="actions-2162",
    )
    row = world.rows["medops"]["deployment"]
    Deployment.objects.filter(pk=row.pk).update(status="running", version=7, image_tag="current")
    row.refresh_from_db()
    workload = world.rows["medops"]["command_run"].workload
    Workload.objects.filter(pk=workload.pk).update(version=11, kind="deployment")
    workload.refresh_from_db()
    prior = Deployment.objects.create(
        registered_app=world.medops_app,
        app_environment=row.app_environment,
        status="superseded",
        image_tag="own-prior",
        config_snapshot={"own": True},
    )
    Deployment.objects.filter(pk=prior.pk).update(created_at=row.created_at - timedelta(minutes=1))
    AppEnvironment.objects.filter(pk=row.app_environment_id).update(version=19)
    type(world.medops_app).objects.filter(pk=world.medops_app.pk).update(version=31)
    return SimpleNamespace(deployment=row, workload=workload, prior=prior)


def invoke(world, targets, action, version=None):
    mut, info = LifecycleMutation(), make_info(world.user)
    kwargs = {} if version is None else {"if_match_version": version}
    if action == "rollback":
        return mut.rollback_deployment(
            info, input=DeploymentByIdInput(id=str(targets.deployment.guid)), **kwargs
        )
    if action == "redeploy":
        return mut.redeploy_app(info, input=DeploymentByIdInput(id=str(targets.deployment.guid)), **kwargs)
    if action == "restart":
        return mut.restart_astrolift_workload(
            info, input=RestartWorkloadInput(workload_id=str(targets.workload.guid)), **kwargs
        )
    return mut.scale_astrolift_workload(
        info, input=ScaleWorkloadInput(workload_id=str(targets.workload.guid), replicas=2), **kwargs
    )


def snapshot():
    from astrolift_operations.models import WorkflowRun

    return [
        list(model.all_objects.order_by("pk").values())
        for model in (Deployment, AppEnvironment, Workload, WorkflowRun)
    ]


@pytest.fixture
def effects(monkeypatch):
    effects = []
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.mutations.deployments._start_deploy_workflow_on_commit",
        lambda **kwargs: effects.append(("workflow", kwargs)),
    )

    class Driver:
        def patch_workload(self, *args):
            effects.append(("patch", args))
            return {"spec": {"replicas": 2}, "status": {"observedGeneration": 3, "readyReplicas": 2}}

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: Driver())
    return effects


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("requested", [0, 19, 31])
def test_stale_or_other_entity_version_refuses_all_writes_and_effects(
    world, targets, effects, action, requested
):
    before = snapshot()
    with subject(world):
        result = invoke(world, targets, action, requested)
    assert not result.ok
    error = result.errors[0]
    assert error.code == "VERSION_MISMATCH"
    assert error.current_version == (7 if action in {"rollback", "redeploy"} else 11)
    assert error.requested_version == requested
    assert effects == []
    assert snapshot() == before


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("versioned", [False, True])
def test_matching_target_version_and_omission_keep_permitted_actions(
    world, targets, effects, action, versioned
):
    before = targets.workload.version
    with subject(world):
        result = invoke(
            world, targets, action, (7 if action in {"rollback", "redeploy"} else 11) if versioned else None
        )
    assert result.ok, result.errors
    assert len(effects) == 1
    if action in {"restart", "scale"}:
        targets.workload.refresh_from_db()
        assert targets.workload.version == before + 1
    else:
        created = Deployment.objects.get(guid=str(result.data.id))
        assert created.app_environment_id == targets.deployment.app_environment_id
        assert created.image_tag == ("own-prior" if action == "rollback" else "current")
        assert result.data.version == created.version


@pytest.mark.parametrize("status", ["superseded", "pending", "failed", "rolled_back"])
def test_rollback_refuses_non_running_snapshot_even_with_matching_version(world, targets, effects, status):
    Deployment.objects.filter(pk=targets.deployment.pk).update(status=status)
    before = snapshot()
    with subject(world):
        result = invoke(world, targets, "rollback", 7)
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    assert effects == [] and snapshot() == before


def test_rollback_selects_prior_only_in_current_running_deployments_environment(world, targets, effects):
    other_env = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        name="staging",
        tenant_cluster=targets.deployment.app_environment.tenant_cluster,
    )
    other_prior = Deployment.objects.create(
        registered_app=world.medops_app,
        app_environment=other_env,
        status="superseded",
        image_tag="sibling-prior",
    )
    other_running = Deployment.objects.create(
        registered_app=world.medops_app,
        app_environment=other_env,
        status="running",
        image_tag="sibling-current",
    )
    with subject(world):
        result = invoke(world, targets, "rollback", 7)
    assert result.ok, result.errors
    created = Deployment.objects.get(guid=str(result.data.id))
    assert created.promoted_from_id == targets.prior.pk
    assert created.image_tag == "own-prior"
    assert created.app_environment_id != other_env.pk
    other_prior.refresh_from_db()
    other_running.refresh_from_db()
    assert other_prior.status == "superseded" and other_running.status == "running"


def test_rollback_has_no_prior_when_only_another_environment_has_a_superseded_row(world, targets, effects):
    targets.prior.soft_delete()
    other_env = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        name="staging",
        tenant_cluster=targets.deployment.app_environment.tenant_cluster,
    )
    Deployment.objects.create(
        registered_app=world.medops_app,
        app_environment=other_env,
        status="superseded",
        image_tag="sibling-prior",
    )
    before = snapshot()
    with subject(world):
        result = invoke(world, targets, "rollback", 7)
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    assert effects == [] and snapshot() == before


def test_rollback_refuses_older_running_row_when_environment_has_new_current_running(world, targets, effects):
    Deployment.objects.create(
        registered_app=world.medops_app,
        app_environment=targets.deployment.app_environment,
        status="running",
        image_tag="new-current",
    )
    before = snapshot()
    with subject(world):
        result = invoke(world, targets, "rollback", 7)
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    assert effects == [] and snapshot() == before


def test_redeploy_preserves_snapshot_environment_even_when_other_environment_is_newer(
    world, targets, effects
):
    other_env = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        name="staging",
        deploys_paused=True,
        tenant_cluster=targets.deployment.app_environment.tenant_cluster,
    )
    Deployment.objects.create(
        registered_app=world.medops_app,
        app_environment=other_env,
        status="running",
        image_tag="different-environment",
    )
    with subject(world):
        result = invoke(world, targets, "redeploy", 7)
    assert result.ok, result.errors
    created = Deployment.objects.get(guid=str(result.data.id))
    assert created.app_environment_id == targets.deployment.app_environment_id
    assert created.image_tag == "current"


@pytest.mark.parametrize("action", ["restart", "scale"])
def test_second_runtime_action_with_original_version_refuses_driver(world, targets, effects, action):
    with subject(world):
        first = invoke(world, targets, action, 11)
        second = invoke(world, targets, action, 11)
    assert first.ok
    assert not second.ok and second.errors[0].code == "VERSION_MISMATCH"
    assert second.errors[0].current_version == 12
    assert len(effects) == 1


@pytest.mark.parametrize("action", ["restart", "scale"])
def test_locked_primary_environment_facts_are_rechecked_after_admission(
    world, targets, effects, action, monkeypatch
):
    from contextlib import contextmanager

    from astrolift_identity.models import Policy
    from astrolift_lifecycle.schema.mutations import app_ops

    env = targets.deployment.app_environment
    AppEnvironment.objects.filter(pk=env.pk).update(name="staging")
    Policy.objects.create(
        organization=world.org,
        name="Production deny",
        slug="lock-policy-2162",
        effect="DENY",
        scope_level="ORG",
        action_pattern="app.deploy",
        resource_pattern={"env": ["production"]},
    )
    real_lock = app_ops.locked_workload

    @contextmanager
    def changed_target(guid):
        AppEnvironment.objects.filter(pk=env.pk).update(name="production")
        with real_lock(guid) as target:
            yield target

    monkeypatch.setattr(app_ops, "locked_workload", changed_target)
    with subject(world):
        result = invoke(world, targets, action, 11)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert effects == []
    targets.workload.refresh_from_db()
    assert targets.workload.version == 11


def test_redeploy_checks_fresh_locked_environment_pause_without_borrowing_source_version(
    world, targets, effects, monkeypatch
):
    from contextlib import contextmanager

    from astrolift_lifecycle.schema.mutations import deployments

    real_lock = deployments.locked_deployment

    @contextmanager
    def changed_target(guid):
        AppEnvironment.objects.filter(pk=targets.deployment.app_environment_id).update(deploys_paused=True)
        with real_lock(guid) as target:
            yield target

    monkeypatch.setattr(deployments, "locked_deployment", changed_target)
    with subject(world):
        result = invoke(world, targets, "redeploy", 7)
    assert not result.ok and result.errors[0].code == "PRECONDITION"
    assert effects == []
    targets.deployment.refresh_from_db()
    assert targets.deployment.version == 7
