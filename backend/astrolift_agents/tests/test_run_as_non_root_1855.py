"""Per-spec non-root mode for agent pods (#1855).

A spec either runs as root (the default: boot-time roster installs work) or
as the images' non-root ``agent`` user with every capability dropped. The two
modes exclude ``allow_install``; the conflict is refused at config time by
the mutations and again at spawn, because a manifest sync can set
``allow_install`` without going through them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from constance.test import override_config

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.schema.mutations import (
    AgentsMutation,
    CreateAgentEnvironmentSpecInput,
    UpdateAgentEnvironmentSpecInput,
)
from astrolift_dispatch.pod_hardening import AGENT_UID, NON_ROOT_INSTALL_CONFLICT
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner, _render_agent_job
from astrolift_dispatch.tests.test_k8s_job_vnc import _FakeWorkload
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org(db):
    return Organization.objects.create(name="NonRoot Org", slug="nonroot-org")


def _task(spec):
    return SimpleNamespace(
        guid="t-1855",
        vnc_enabled=False,
        environment_spec=spec,
        agent_definition=_FakeWorkload("ghcr.io/calliopeai/astrolift-agent-claude:1.2", 0),
    )


def _pod(spec):
    return _render_agent_job(
        job_name="agent-task-t1855",
        workload=_FakeWorkload("ghcr.io/calliopeai/astrolift-agent-claude:1.2", 0),
        namespace="ns",
        task=_task(spec),
    )["spec"]["template"]["spec"]


def _spec(**fields):
    base = {"image_tag": "", "runtime": "", "run_as_non_root": False, "allow_install": False}
    return SimpleNamespace(**{**base, **fields})


# ---- render ----------------------------------------------------------


def test_agent_uid_matches_the_image_contract():
    """astrolift-agents creates the ``agent`` user with this uid/gid; the two
    must move together or non-root pods cannot write their HOME."""
    assert AGENT_UID == 42042


def test_root_mode_keeps_default_identity_and_capabilities():
    pod = _pod(_spec())

    assert "runAsNonRoot" not in pod["securityContext"]
    assert "capabilities" not in pod["containers"][0]["securityContext"]


def test_non_root_mode_runs_as_agent_user_with_no_capabilities(db):
    pod = _pod(_spec(run_as_non_root=True))

    assert pod["securityContext"] == {
        "seccompProfile": {"type": "RuntimeDefault"},
        "runAsNonRoot": True,
        "runAsUser": AGENT_UID,
        "runAsGroup": AGENT_UID,
        "fsGroup": AGENT_UID,
    }
    container_security = pod["containers"][0]["securityContext"]
    assert container_security == {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
    assert pod["automountServiceAccountToken"] is False


def test_non_root_identity_follows_constance(db):
    """Installs running their own images, or clusters that mandate a uid
    range, set the identity once instead of patching the platform."""
    with override_config(AGENT_POD_UID=50123, AGENT_POD_GID=50124):
        security = _pod(_spec(run_as_non_root=True))["securityContext"]

    assert (security["runAsUser"], security["runAsGroup"], security["fsGroup"]) == (50123, 50124, 50124)


# ---- spawn -----------------------------------------------------------


def test_spawn_refuses_non_root_with_install_before_touching_the_cluster():
    """``cluster=object()`` has no driver; reaching it would raise."""
    spawner = K8sJobSpawner(cluster=object(), namespace="agents")

    result = spawner.spawn(_task(_spec(image_tag="acme/agent:1", run_as_non_root=True, allow_install=True)))

    assert result.ok is False
    assert result.external_id == ""
    assert result.error == NON_ROOT_INSTALL_CONFLICT


# ---- mutations -------------------------------------------------------


def _create(info, org, **fields):
    return AgentsMutation().create_agent_environment_spec(
        info,
        input=CreateAgentEnvironmentSpecInput(name="Spec", slug="spec", agent_type="claude", **fields),
        org_id=str(org.guid),
    )


def test_create_threads_non_root(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        result = _create(info, org, run_as_non_root=True)

    assert result.ok is True, result.errors
    assert result.data.run_as_non_root is True
    assert AgentEnvironmentSpec.objects.get(organization=org, slug="spec").run_as_non_root is True


def test_create_defaults_to_root(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        result = _create(info, org)

    assert result.ok is True, result.errors
    assert result.data.run_as_non_root is False


def test_create_refuses_non_root_with_install(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_CREATE)
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        result = _create(info, org, run_as_non_root=True, allow_install=True)

    assert result.ok is False
    assert NON_ROOT_INSTALL_CONFLICT in str(result.errors)
    assert not AgentEnvironmentSpec.objects.filter(organization=org, slug="spec").exists()


def test_update_refuses_turning_on_install_for_a_non_root_spec(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_UPDATE)
    spec = AgentEnvironmentSpec.objects.create(
        organization=org, name="Spec", slug="spec", agent_type="claude", run_as_non_root=True
    )
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        result = AgentsMutation().update_agent_environment_spec(
            info, slug="spec", input=UpdateAgentEnvironmentSpecInput(allow_install=True)
        )

    assert result.ok is False
    spec.refresh_from_db()
    assert spec.allow_install is False


def test_update_can_switch_modes_and_leaves_mode_alone_when_omitted(permission_resolver, info, org):
    permission_resolver.grant(Permission.AGENT_ENV_SPEC_UPDATE)
    spec = AgentEnvironmentSpec.objects.create(
        organization=org, name="Spec", slug="spec", agent_type="claude"
    )
    with _tenant_ctx(TenantContext(organization_id=org.id)):
        on = AgentsMutation().update_agent_environment_spec(
            info, slug="spec", input=UpdateAgentEnvironmentSpecInput(run_as_non_root=True)
        )
        untouched = AgentsMutation().update_agent_environment_spec(
            info, slug="spec", input=UpdateAgentEnvironmentSpecInput(name="Renamed")
        )

    assert on.ok is True and untouched.ok is True
    spec.refresh_from_db()
    assert spec.run_as_non_root is True
