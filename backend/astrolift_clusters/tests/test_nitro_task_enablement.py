"""Opt-in binds reviewed provider prerequisites to the visible cluster's version."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters import nitro
from astrolift_clusters.schema.nitro import ConfigureNitroTasksInput, NitroTasksMutation, NitroTasksQuery
from astrolift_clusters.tests.test_cluster_auth_users_2131 import _cluster, _info
from astrolift_dispatch.pod_hardening import AgentRuntimeClassError
from astrolift_graphql import GUID
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(permission_resolver, monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    cluster, org = _cluster()
    actor = get_user_model().objects.create_user(username="nitro-enable-review")
    state = {"mode_state": "ready", "fingerprint": "configuration-one", "calls": []}

    def probe(slug, namespace, config):
        state["calls"].append((slug, namespace))
        return {
            "checked_at": timezone.now().isoformat(),
            "source": {
                "cluster_arn": "arn:cluster",
                "namespace": namespace,
                "configuration_fingerprint": state["fingerprint"],
                "effective_principal_arn": "arn:session",
            },
            "modes": {
                mode: {
                    "state": state["mode_state"],
                    "checks": [
                        {
                            "code": "runtime_class",
                            "state": state["mode_state"],
                            "message": "Runtime readiness",
                            "evidence": {},
                        }
                    ],
                }
                for mode in ["isolated", "confidential"]
            },
        }

    monkeypatch.setattr(
        "core.cluster_management._driver_for_cluster",
        lambda cluster: SimpleNamespace(probe_nitro_readiness=probe),
    )
    return SimpleNamespace(
        cluster=cluster, org=org, actor=actor, state=state, permissions=permission_resolver
    )


def context(s):
    return tenant_context(TenantContext(organization_id=s.org.pk, actor_user_id=s.actor.pk))


def observed(s):
    return NitroTasksQuery().astrolift_nitro_task_readiness(_info(s.actor), GUID(str(s.cluster.guid))).data


def change(s, review, modes):
    return NitroTasksMutation().configure_nitro_tasks(
        _info(s.actor),
        ConfigureNitroTasksInput(
            cluster_id=GUID(str(s.cluster.guid)),
            expected_version=review.version,
            expected_source=review.source_token,
            task_modes=modes,
        ),
    )


def test_per_cluster_opt_in_persists_and_does_not_change_global_runtime(setup, settings):
    s = setup
    settings.AGENT_RUNTIME_CLASS = ""
    with context(s):
        before = observed(s)
        assert not any(mode.enabled for mode in before.modes)
        assert before.namespace.startswith("astrolift-agents-")
        result = change(s, before, ["isolated", "confidential"])
        assert result.ok
        s.cluster.refresh_from_db()
        assert nitro.task_modes(s.cluster) == ["confidential", "isolated"]
        assert settings.AGENT_RUNTIME_CLASS == ""
        assert result.data.source_token == observed(s).source_token


@pytest.mark.parametrize("kind", ["version", "configuration", "not_ready", "unknown"])
def test_enable_refuses_stale_or_unready_review_without_writes(setup, kind):
    s = setup
    with context(s):
        before = observed(s)
        if kind == "version":
            s.cluster.name = "changed"
            s.cluster.save()
        elif kind == "configuration":
            s.state["fingerprint"] = "configuration-two"
        else:
            s.state["mode_state"] = kind
        original = deepcopy(s.cluster.provider_config)
        result = change(s, before, ["confidential"])
        assert not result.ok
        s.cluster.refresh_from_db()
        assert s.cluster.provider_config == original


def test_disable_remains_possible_when_prerequisites_disappear(setup):
    s = setup
    with context(s):
        assert change(s, observed(s), ["confidential"]).ok
        s.state["mode_state"] = "unknown"
        assert change(s, observed(s), []).ok
        s.cluster.refresh_from_db()
        assert nitro.task_modes(s.cluster) == []


def test_dispatch_refuses_disabled_before_provider_and_rechecks_worker(setup):
    s = setup
    s.state["calls"].clear()
    with context(s):
        with pytest.raises(AgentRuntimeClassError, match="not enabled"):
            nitro.require_task_mode(s.cluster, "confidential", "agent-target")
        assert not s.state["calls"]
        assert change(s, observed(s), ["confidential"]).ok
        s.cluster.refresh_from_db()
        s.state["mode_state"] = "not_ready"
        with pytest.raises(AgentRuntimeClassError, match="not ready on this process"):
            nitro.require_task_mode(s.cluster, "confidential", "agent-target")
        assert s.state["calls"][-1][1] == "agent-target"


def test_permission_and_shared_cluster_boundaries(setup):
    s = setup
    with context(s):
        s.permissions.deny(Permission.CLUSTER_MANAGE)
        with pytest.raises(PermissionDenied):
            observed(s)
        s.permissions.grant(Permission.CLUSTER_MANAGE)
        s.cluster.organization = None
        s.cluster.save()
        with pytest.raises(PermissionDenied):
            observed(s)


def test_other_tenant_cannot_review_or_enable_cluster(setup):
    from astrolift_identity.models import Organization

    s = setup
    other = Organization.objects.create(name="Other Nitro", slug="nitro-other")
    with tenant_context(TenantContext(organization_id=other.pk, actor_user_id=s.actor.pk)):
        result = NitroTasksQuery().astrolift_nitro_task_readiness(_info(s.actor), GUID(str(s.cluster.guid)))
        assert not result.ok
        result = change(s, SimpleNamespace(version=s.cluster.version, source_token="untrusted"), ["isolated"])
        assert not result.ok
    assert not s.state["calls"]
    s.cluster.refresh_from_db()
    assert not nitro.task_modes(s.cluster)


@pytest.mark.parametrize("ceiling", ["team", "other_organization"])
def test_bearer_ceiling_refuses_before_provider_review(setup, ceiling):
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token

    s = setup
    token = SimpleNamespace(
        team_id=123 if ceiling == "team" else None,
        organization_id=s.org.pk if ceiling == "team" else s.org.pk + 1000000,
    )
    marker = set_current_api_token(token)
    try:
        with context(s), pytest.raises(PermissionDenied, match="bearer token"):
            observed(s)
        with context(s):
            result = change(
                s, SimpleNamespace(version=s.cluster.version, source_token="untrusted"), ["confidential"]
            )
            assert not result.ok
            assert any("bearer token" in error.message for error in result.errors)
    finally:
        reset_current_api_token(marker)
    assert not s.state["calls"]
