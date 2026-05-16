"""Tests for the live workload-ops mutations (#388).

Three layers exercised:

1. ``rollout_restart_workload`` — service-level happy path with a
   recording fake driver. Asserts the annotation patch shape and the
   read-back revision pull.
2. ``scale_workload`` — service-level bounds + happy path. Covers
   default ceiling (20), per-env override, scale-to-zero, and
   negative input.
3. ``restartAstroliftWorkload`` / ``scaleAstroliftWorkload`` mutation
   resolvers — permission gate, NOT_FOUND, VALIDATION envelope.

The cluster driver is faked by monkeypatching
``core.cluster_management._driver_for_cluster`` to return a recording
stand-in. Same shape the workflow tests use for the bring-into-mgmt
path; mirrors the spirit of the conftest's other fakes without
spinning up the provider-plugin registry."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    RestartWorkloadInput,
    ScaleWorkloadInput,
)
from astrolift_lifecycle.services.k8s_ops import (
    DEFAULT_MAX_REPLICAS,
    K8sOpError,
    resolve_replica_bounds,
    rollout_restart_workload,
    scale_workload,
)
from astrolift_registry.models import Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _err_code(result) -> str:
    """Read the first error code as a plain string.

    The envelope carries two flavours: ``MutationErrorType`` from
    ``gql_failure`` (code is already a string) and the ``ErrorCode``
    enum when the wrapper translated a raised ``PermissionDenied``.
    Normalise to string so tests don't have to discriminate."""
    code = result.errors[0].code
    return getattr(code, "value", code)


@dataclasses.dataclass
class _PatchCall:
    cluster_slug: str
    namespace: str
    kind: str
    name: str
    patch: dict[str, Any]


class _RecordingDriver:
    """Minimal stand-in for ClusterDriver — records each patch_workload
    call and returns a stubbed Deployment-status dict the service
    layer reads back. Behaviour switches are set per-test."""

    def __init__(self, *, response: dict[str, Any] | None = None, raises: Exception | None = None):
        self.calls: list[_PatchCall] = []
        self._response = response or {}
        self._raises = raises

    def patch_workload(
        self,
        cluster_slug: str,
        namespace: str,
        kind: str,
        name: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            _PatchCall(
                cluster_slug=cluster_slug,
                namespace=namespace,
                kind=kind,
                name=name,
                patch=patch,
            ),
        )
        if self._raises is not None:
            raise self._raises
        return self._response


@pytest.fixture
def workload(app):
    return Workload.objects.create(
        registered_app=app,
        name="Web",
        slug=app.slug,
        kind=Workload.Kind.DEPLOYMENT,
        replicas=1,
    )


@pytest.fixture
def install_driver(monkeypatch):
    """Returns a callable that installs ``driver`` in place of the
    cluster-management driver resolver. The patch hits both the
    ``core.cluster_management`` module export and the local import
    inside the k8s_ops service module."""

    def _install(driver: Any) -> None:
        def _resolve(cluster):  # noqa: ARG001 — signature parity
            return driver

        monkeypatch.setattr("core.cluster_management._driver_for_cluster", _resolve)

    return _install


# ---------------------------------------------------------------------------
# Service layer — rollout_restart_workload
# ---------------------------------------------------------------------------


def test_rollout_restart_patches_annotation_with_current_timestamp(
    workload,
    env,
    install_driver,
):
    """Happy path: the patch carries the kubectl restartedAt annotation
    plus the astrolift restarted-by marker, and the read-back revision
    comes from the driver's status.observedGeneration."""
    driver = _RecordingDriver(response={"status": {"observedGeneration": 7}})
    install_driver(driver)

    result = rollout_restart_workload(workload)

    assert result.ok is True
    assert result.new_revision == 7
    assert len(driver.calls) == 1
    call = driver.calls[0]
    assert call.kind == "Deployment"
    assert call.name == workload.slug
    annotations = call.patch["spec"]["template"]["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/restartedAt" in annotations
    # Stamp should parse as ISO-8601 with a timezone.
    from datetime import datetime

    parsed = datetime.fromisoformat(annotations["kubectl.kubernetes.io/restartedAt"])
    assert parsed.tzinfo is not None
    assert annotations["astrolift.io/restarted-by"] == "platform-controls"


def test_rollout_restart_without_environment_raises_precondition(
    workload,
    install_driver,
):
    """A workload with no active env can't be scoped to a cluster; the
    service surfaces PRECONDITION rather than guessing a namespace."""
    install_driver(_RecordingDriver())
    with pytest.raises(K8sOpError) as excinfo:
        rollout_restart_workload(workload)
    assert excinfo.value.code == "PRECONDITION"
    assert "no active environment" in excinfo.value.message


# ---------------------------------------------------------------------------
# Service layer — scale_workload
# ---------------------------------------------------------------------------


def test_scale_happy_path_patches_spec_replicas(workload, env, install_driver):
    driver = _RecordingDriver(
        response={"spec": {"replicas": 5}, "status": {"readyReplicas": 3}},
    )
    install_driver(driver)

    result = scale_workload(workload, 5)

    assert result.ok is True
    assert result.current_replicas == 5
    assert result.ready_replicas == 3
    assert len(driver.calls) == 1
    assert driver.calls[0].patch == {"spec": {"replicas": 5}}


def test_scale_to_zero_is_allowed(workload, env, install_driver):
    """Scale-to-zero is a legitimate off-hours / quiesce case."""
    driver = _RecordingDriver(response={"spec": {"replicas": 0}})
    install_driver(driver)
    result = scale_workload(workload, 0)
    assert result.ok is True
    assert result.current_replicas == 0


def test_scale_below_bound_raises_validation(workload, env, install_driver):
    install_driver(_RecordingDriver())
    with pytest.raises(K8sOpError) as excinfo:
        scale_workload(workload, -1)
    assert excinfo.value.code == "VALIDATION"
    assert "outside bounds [0, " in excinfo.value.message


def test_scale_above_env_policy_raises_validation_with_bounds(
    workload,
    env,
    install_driver,
):
    """When the env carries a tighter ``max_replicas`` policy, it wins
    over the platform default. The error message echoes the effective
    bounds so the operator sees the cap they're hitting."""
    env.deploy_config = {"max_replicas": 10}
    env.save(update_fields=["deploy_config"])

    install_driver(_RecordingDriver())
    with pytest.raises(K8sOpError) as excinfo:
        scale_workload(workload, 15)
    assert excinfo.value.code == "VALIDATION"
    assert "[0, 10]" in excinfo.value.message


def test_scale_above_default_ceiling_raises_validation(workload, env, install_driver):
    install_driver(_RecordingDriver())
    with pytest.raises(K8sOpError) as excinfo:
        scale_workload(workload, DEFAULT_MAX_REPLICAS + 1)
    assert excinfo.value.code == "VALIDATION"
    assert f"[0, {DEFAULT_MAX_REPLICAS}]" in excinfo.value.message


def test_resolve_replica_bounds_respects_env_override(env):
    env.deploy_config = {"max_replicas": 4}
    assert resolve_replica_bounds(env) == (0, 4)


def test_resolve_replica_bounds_ignores_nonsense_override(env):
    """Negative / non-int overrides fall back to the platform default."""
    env.deploy_config = {"max_replicas": "loads"}
    assert resolve_replica_bounds(env) == (0, DEFAULT_MAX_REPLICAS)
    env.deploy_config = {"max_replicas": -2}
    assert resolve_replica_bounds(env) == (0, DEFAULT_MAX_REPLICAS)


# ---------------------------------------------------------------------------
# Mutation envelope
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_info(actor):
    request = SimpleNamespace(user=actor)
    context = SimpleNamespace(request=request)
    return SimpleNamespace(context=context)


def _grant_deploy(resolver):
    resolver.grant(Permission.APP_DEPLOY)


def test_restart_mutation_happy_path(
    workload,
    env,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    _grant_deploy(permission_resolver)
    install_driver(_RecordingDriver(response={"status": {"observedGeneration": 4}}))
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.restart_astrolift_workload(
            fake_info,
            input=RestartWorkloadInput(workload_id=str(workload.guid)),
        )

    assert result.ok, result.errors
    assert result.data.workload_id == str(workload.guid)
    assert result.data.new_revision == 4


def test_restart_mutation_permission_denied_for_read_only_actor(
    workload,
    env,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    # APP_READ only — no APP_DEPLOY grant.
    permission_resolver.grant(Permission.APP_READ)
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.restart_astrolift_workload(
            fake_info,
            input=RestartWorkloadInput(workload_id=str(workload.guid)),
        )

    assert result.ok is False
    assert _err_code(result) == "PERMISSION_DENIED"


def test_scale_mutation_happy_path(
    workload,
    env,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    _grant_deploy(permission_resolver)
    driver = _RecordingDriver(
        response={"spec": {"replicas": 5}, "status": {"readyReplicas": 2}},
    )
    install_driver(driver)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.scale_astrolift_workload(
            fake_info,
            input=ScaleWorkloadInput(workload_id=str(workload.guid), replicas=5),
        )

    assert result.ok, result.errors
    assert result.data.desired_replicas == 5
    assert result.data.ready_replicas == 2
    assert driver.calls[0].patch == {"spec": {"replicas": 5}}


def test_scale_mutation_below_bound_returns_validation(
    workload,
    env,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    _grant_deploy(permission_resolver)
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.scale_astrolift_workload(
            fake_info,
            input=ScaleWorkloadInput(workload_id=str(workload.guid), replicas=-1),
        )

    assert result.ok is False
    assert _err_code(result) == "VALIDATION"
    assert "outside bounds" in result.errors[0].message


def test_scale_mutation_above_env_policy_returns_validation(
    workload,
    env,
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    _grant_deploy(permission_resolver)
    env.deploy_config = {"max_replicas": 10}
    env.save(update_fields=["deploy_config"])
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.scale_astrolift_workload(
            fake_info,
            input=ScaleWorkloadInput(workload_id=str(workload.guid), replicas=15),
        )

    assert result.ok is False
    assert _err_code(result) == "VALIDATION"
    assert "[0, 10]" in result.errors[0].message


def test_scale_mutation_unknown_workload_returns_not_found(
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    _grant_deploy(permission_resolver)
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    bogus = "00000000-0000-0000-0000-000000000000"
    with _tenant_for(org, actor):
        result = mut.scale_astrolift_workload(
            fake_info,
            input=ScaleWorkloadInput(workload_id=bogus, replicas=2),
        )

    assert result.ok is False
    assert _err_code(result) == "NOT_FOUND"


def test_restart_mutation_unknown_workload_returns_not_found(
    org,
    actor,
    fake_info,
    permission_resolver,
    install_driver,
):
    _grant_deploy(permission_resolver)
    install_driver(_RecordingDriver())
    mut = LifecycleMutation()

    bogus = "00000000-0000-0000-0000-000000000000"
    with _tenant_for(org, actor):
        result = mut.restart_astrolift_workload(
            fake_info,
            input=RestartWorkloadInput(workload_id=bogus),
        )

    assert result.ok is False
    assert _err_code(result) == "NOT_FOUND"
