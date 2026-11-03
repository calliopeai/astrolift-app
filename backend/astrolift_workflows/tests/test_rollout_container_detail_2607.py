"""A failed rollout names the container that failed (#2607).

The workload conditions name the Deployment only. The reason the deploy
records, and that app show and the deploy.failed event display, should also
name the container that is not ready and what it is stuck on. The formatting
is pure and runs without Temporal or a database. The poll test stubs the ORM
and the driver.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest import mock

import pytest

from astrolift_workflows.rollout_detail import container_detail, container_problem


def _container(name, *, ready=False, state="running", waiting="", terminated="", exit_code=None, restarts=()):
    return SimpleNamespace(
        name=name,
        ready=ready,
        state=state,
        waiting_reason=waiting,
        terminated_reason=terminated,
        terminated_exit_code=exit_code,
        last_restart_reasons=list(restarts),
    )


def _pod(workload, *containers):
    return SimpleNamespace(workload=workload, container_statuses=list(containers))


def test_waiting_container_names_its_reason():
    assert (
        container_problem(_container("agent", state="waiting", waiting="CrashLoopBackOff"))
        == "CrashLoopBackOff"
    )


def test_terminated_container_keeps_exit_code():
    c = _container("agent", state="terminated", terminated="Error", exit_code=127)
    assert container_problem(c) == "Error (exit 127)"


def test_running_but_not_ready_points_at_readiness_probe():
    assert container_problem(_container("agent")) == "running, not ready (readiness probe not passing)"


def test_running_but_not_ready_after_probe_restart_shows_last_restart():
    c = _container("agent", restarts=["Unhealthy"])
    assert container_problem(c) == "running, not ready; last restart Unhealthy"


def test_detail_names_only_the_sidecar_that_is_not_ready():
    pods = [
        _pod(
            "web",
            _container("app", ready=True, state="running"),
            _container("agent", ready=False, state="running"),
        )
    ]
    assert container_detail(pods, "web") == (
        "; container agent: running, not ready (readiness probe not passing)"
    )


def test_detail_is_empty_when_every_container_is_ready():
    pods = [_pod("web", _container("app", ready=True))]
    assert container_detail(pods, "web") == ""


def test_detail_skips_pods_of_another_workload():
    pods = [_pod("worker", _container("agent", state="waiting", waiting="CrashLoopBackOff"))]
    assert container_detail(pods, "web") == ""


def test_detail_keeps_pods_without_a_workload_label():
    pods = [_pod("", _container("agent", state="waiting", waiting="ImagePullBackOff"))]
    assert container_detail(pods, "web") == "; container agent: ImagePullBackOff"


def test_detail_caps_the_clauses_and_counts_the_rest():
    containers = [_container(f"c{i}", state="waiting", waiting="Pending") for i in range(5)]
    detail = container_detail([_pod("web", *containers)], "web")
    assert detail == (
        "; container c0: Pending; container c1: Pending; container c2: Pending; 2 more not ready"
    )


def test_detail_is_one_line():
    pods = [_pod("web", _container("agent", state="waiting", waiting="CrashLoopBackOff"))]
    assert "\n" not in container_detail(pods, "web")


@pytest.fixture
def poll_env(monkeypatch):
    """Stub the ORM, the driver and the pod list for ``_poll_rollout_sync``."""
    import astrolift_lifecycle.models as lifecycle_models
    import astrolift_lifecycle.run_history as run_history
    import core.app_deploy as app_deploy
    import core.cluster_observability as observability
    from astrolift_workflows.activities import app_lifecycle

    # observed_phase writes a DeploymentLog row, which needs a real deployment.
    monkeypatch.setattr(run_history, "phase", lambda *_a, **_k: contextlib.nullcontext())

    d = mock.MagicMock()
    d.pk = 42
    d.registered_app.slug = "billing"
    d.app_environment.tenant_cluster = "cluster-row"
    fake_deployment = mock.MagicMock()
    fake_deployment.all_objects.select_related.return_value.get.return_value = d
    monkeypatch.setattr(lifecycle_models, "Deployment", fake_deployment)

    driver = mock.MagicMock()
    driver.poll_rollout.return_value = SimpleNamespace(
        success=False, message="Progressing=False", timed_out=False
    )
    ctx = SimpleNamespace(slug="cluster-slug")
    monkeypatch.setattr(app_deploy, "driver_for_deployment", lambda _d: (driver, ctx, "ns-billing"))
    monkeypatch.setattr(app_deploy, "render_resources_for_deployment", lambda _d: [])
    monkeypatch.setattr(
        app_deploy,
        "workloads_from_resources",
        lambda _r: [("Deployment", "web")],
    )
    pods = [_pod("web", _container("agent", state="waiting", waiting="CrashLoopBackOff"))]
    list_pods = mock.MagicMock(return_value=pods)
    monkeypatch.setattr(observability, "list_app_pods", list_pods)
    return app_lifecycle, list_pods


@pytest.mark.django_db
def test_failed_rollout_reason_names_the_container(poll_env):
    from core.app_deploy import AppDeployError

    app_lifecycle, list_pods = poll_env
    with pytest.raises(AppDeployError) as exc:
        app_lifecycle._poll_rollout_sync(42, 600)
    message = str(exc.value)
    assert message == (
        "rollout Deployment/web in ns-billing failed: Progressing=False; container agent: CrashLoopBackOff"
    )
    list_pods.assert_called_once_with(cluster="cluster-row", namespace="ns-billing", app_slug="billing")


@pytest.mark.django_db
def test_failed_rollout_keeps_generic_reason_when_pods_cannot_be_read(poll_env, monkeypatch):
    import core.cluster_observability as observability
    from core.app_deploy import AppDeployError

    app_lifecycle, _ = poll_env
    monkeypatch.setattr(
        observability, "list_app_pods", mock.MagicMock(side_effect=RuntimeError("apiserver down"))
    )
    with pytest.raises(AppDeployError) as exc:
        app_lifecycle._poll_rollout_sync(42, 600)
    assert str(exc.value) == "rollout Deployment/web in ns-billing failed: Progressing=False"
