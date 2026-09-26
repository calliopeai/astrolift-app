"""Tests for the ``testModelEndpoint`` mutation (#2064).

A test prompt never reaches the model from the control plane -- it is
relayed through the cluster's in-cluster keep-alive agent over the same
heartbeat channel the agent already uses (#808). These tests cover the
mutation's own gates (permission, tenant scoping, validation,
preconditions, rate limit, conflict) and one real end-to-end round trip
through the ``agent_test_jobs`` cache store, standing in for the agent
with direct calls to ``dispatch_pending`` / ``record_result``.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_clusters import agent_test_jobs
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations import ServicesMutation, TestModelEndpointInput
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None, META={})))


def _ctx(org):
    # actor_user_id=org.id (not a real user row, just a stand-in unique id):
    # the rate limiter's cache bucket is keyed by caller id, and the real
    # cache backend persists across test functions in this file. Every
    # test's org PK is fresh, so this keeps each test's budget isolated
    # without clearing a cache that other concurrent sessions may share.
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=org.id))


def _scaffold(*, live_agent: bool = False, agent_test_configured: bool = False, slug_suffix: str = ""):
    """``slug_suffix`` keeps a second scaffold in the same test (e.g. a
    sibling org for a cross-tenant check) from colliding on slug uniqueness;
    left blank, callers that only ever scaffold once get the fixed slugs
    ``test_full_round_trip_dispatch_and_result`` asserts the resolved
    in-cluster URL against.
    """
    suffix = f"-{slug_suffix}" if slug_suffix else ""
    org = Organization.objects.create(name="Acme", slug=f"acme-test-prompt{suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-test-prompt{suffix}")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug=f"demo-test-prompt{suffix}"
    )
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="K8s Native", slug="k8s-native-tp", capabilities_manifest={}, config_schema={})],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="k8s-native-tp")
    provider_config: dict = {}
    if agent_test_configured:
        provider_config["vllm_agent_test"] = {"namespace": "astrolift-system"}
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"local-test-prompt{suffix}",
        name="Local",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
        provider_config=provider_config,
        heartbeat_interval_seconds=5,
        last_heartbeat_at=timezone.now() if live_agent else None,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Test Prompt App",
        slug=f"test-prompt-app{suffix}",
        provisioning_status="ready",
        manifest_raw='astrolift_version = 1\nname = "tp"\n',
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return org, app, env, cluster


def _vllm_service(app, env, **overrides):
    defaults = {
        "registered_app": app,
        "app_environment": env,
        "kind": ManagedService.Kind.MODEL_ENDPOINT,
        "variant": "vllm",
        "name": "llm",
        "status": ManagedService.Status.ACTIVE,
        "config": {"model": "Qwen/Qwen3-8B"},
    }
    defaults.update(overrides)
    return ManagedService.objects.create(**defaults)


def _input(svc, prompt: str = "hello") -> TestModelEndpointInput:
    return TestModelEndpointInput(managed_service_id=GUID(str(svc.guid)), prompt=prompt)


# ---- permission + tenancy ----------------------------------------------


def test_requires_permission(permission_resolver):
    """Requests the fixture but grants nothing -- a deterministic deny,
    regardless of what an earlier test in the same session left behind
    (the fixture only installs a resolver for the test that asks for it)."""
    org, app, env, cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_cross_org_managed_service_not_found(permission_resolver):
    """A caller scoped to org A cannot test org B's managed service -- the
    org-scoped lookup makes it read as NOT_FOUND, identical to a bogus
    guid (#1183 leak class)."""
    org_a, _, _, _ = _scaffold()
    _org_b, app_b, env_b, _cluster_b = _scaffold(live_agent=True, agent_test_configured=True, slug_suffix="b")
    svc_b = _vllm_service(app_b, env_b)
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org_a):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc_b))
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_unknown_guid_not_found(permission_resolver):
    org, _, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(
            _info(),
            TestModelEndpointInput(
                managed_service_id=GUID("00000000-0000-0000-0000-000000000001"), prompt="hi"
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- validation ----------------------------------------------------------


def test_empty_prompt_is_validation_error(permission_resolver):
    org, app, env, _cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc, prompt="   "))
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "prompt"


def test_overlong_prompt_is_validation_error(permission_resolver):
    org, app, env, _cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(
            _info(), _input(svc, prompt="x" * (agent_test_jobs.MAX_PROMPT_CHARS + 1))
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "prompt"


def test_non_vllm_variant_refused(permission_resolver):
    org, app, env, _cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env, variant="kserve")
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


def test_non_active_status_refused(permission_resolver):
    org, app, env, _cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env, status=ManagedService.Status.PROVISIONING)
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_missing_model_config_refused(permission_resolver):
    org, app, env, _cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env, config={})
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "model" in result.errors[0].message


# ---- agent reachability preconditions ------------------------------------


def test_no_heartbeat_ever_reports_no_agent(permission_resolver):
    org, app, env, _cluster = _scaffold(live_agent=False, agent_test_configured=True)
    svc = _vllm_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "no connected agent" in result.errors[0].message


def test_offline_agent_reports_no_agent(permission_resolver):
    org, app, env, cluster = _scaffold(live_agent=True, agent_test_configured=True)
    cluster.last_heartbeat_at = timezone.now() - __import__("datetime").timedelta(hours=2)
    cluster.save(update_fields=["last_heartbeat_at"])
    svc = _vllm_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "no connected agent" in result.errors[0].message


def test_agent_test_not_configured_refused(permission_resolver):
    org, app, env, _cluster = _scaffold(live_agent=True, agent_test_configured=False)
    svc = _vllm_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "NetworkPolicy" in result.errors[0].message


# ---- rate limit + conflict (await_result patched to skip the real wait) --


def test_rate_limited_after_budget(permission_resolver, monkeypatch):
    org, app, env, cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    monkeypatch.setattr(agent_test_jobs, "await_result", lambda *a, **k: None)
    from django.core.cache import cache

    cache.clear()
    with _ctx(org):
        for _ in range(agent_test_jobs.RATE_LIMIT_PER_MINUTE):
            result = ServicesMutation().test_model_endpoint(_info(), _input(svc))
            assert result.ok, result.errors
            # Each call must clear its own in-flight slot so only the rate
            # limiter (not the one-job-per-cluster conflict guard) is under
            # test here -- the conflict guard has its own test below.
            job_id = agent_test_jobs.current_job_id(str(cluster.guid))
            assert job_id is not None
            agent_test_jobs.record_result(cluster_guid=str(cluster.guid), job_id=job_id, ok=True)
        limited = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert not limited.ok
    assert limited.errors[0].code == "RATE_LIMITED"


def test_conflict_when_a_job_is_already_in_flight(permission_resolver, monkeypatch):
    org, app, env, cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    # Simulate the agent never responding: await_result gives up immediately
    # (patched), which leaves the cluster's "current job" slot occupied
    # until its own TTL -- exactly the state a slow/offline agent leaves.
    monkeypatch.setattr(agent_test_jobs, "await_result", lambda *a, **k: None)
    with _ctx(org):
        first = ServicesMutation().test_model_endpoint(_info(), _input(svc))
        assert first.ok
        assert first.data.status == "timed_out"
        second = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert not second.ok
    assert second.errors[0].code == "CONFLICT"


# ---- full round trip through the real cache job store --------------------


@pytest.mark.django_db(transaction=True)
def test_full_round_trip_dispatch_and_result(permission_resolver, monkeypatch):
    """Real ``agent_test_jobs`` store, no mocking: the mutation enqueues,
    blocks in ``await_result``; a background thread stands in for the
    agent by calling the exact functions the heartbeat view and the
    result view call. ``wait_cap_seconds`` is shrunk so the test does not
    have to wait out the real (>=35s) production floor.

    ``transaction=True`` (a real, committed transaction rather than the
    default wrap-and-roll-back) is required here: the mutation itself
    runs on a background thread with its own DB connection, which would
    not see this test's fixture rows at all under the default isolation.
    """
    org, app, env, cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    monkeypatch.setattr(agent_test_jobs, "wait_cap_seconds", lambda *_a, **_k: 3.0)

    outcome: dict = {}

    def _run():
        with _ctx(org):
            outcome["result"] = ServicesMutation().test_model_endpoint(_info(), _input(svc))

    thread = threading.Thread(target=_run)
    thread.start()

    deadline = time.monotonic() + 3.0
    dispatched = None
    while time.monotonic() < deadline:
        dispatched = agent_test_jobs.dispatch_pending(str(cluster.guid))
        if dispatched is not None:
            break
        time.sleep(0.02)
    assert dispatched is not None, "mutation never enqueued a job for the agent to dispatch"
    assert dispatched["prompt"] == "hello"
    assert dispatched["model"] == "Qwen/Qwen3-8B"
    assert (
        dispatched["base_url"]
        == "http://test-prompt-app-production-llm.acme-test-prompt-test-prompt-app.svc.cluster.local:8000/v1"
    )
    assert dispatched["secret_key"] == "api_key"

    agent_test_jobs.record_result(
        cluster_guid=str(cluster.guid),
        job_id=dispatched["job_id"],
        ok=True,
        reply="Hello! How can I help?",
        latency_ms=842,
        prompt_tokens=5,
        completion_tokens=7,
        total_tokens=12,
    )
    thread.join(timeout=5)
    assert not thread.is_alive()

    result = outcome["result"]
    assert result.ok, result.errors
    assert result.data.status == "succeeded"
    assert result.data.reply == "Hello! How can I help?"
    assert result.data.latency_ms == 842
    assert result.data.prompt_tokens == 5
    assert result.data.completion_tokens == 7
    assert result.data.total_tokens == 12

    svc.refresh_from_db()
    assert svc.last_action_kind == "test_prompt"
    assert svc.last_action_at is not None

    # The slot is freed as soon as the result lands -- a follow-up test
    # does not have to wait out the full TTL.
    assert agent_test_jobs.get_job(dispatched["job_id"])["status"] == agent_test_jobs.SUCCEEDED


def test_timed_out_when_agent_never_responds(permission_resolver, monkeypatch):
    org, app, env, _cluster = _scaffold(live_agent=True, agent_test_configured=True)
    svc = _vllm_service(app, env)
    permission_resolver.grant(Permission.APP_UPDATE)
    monkeypatch.setattr(agent_test_jobs, "wait_cap_seconds", lambda *_a, **_k: 0.2)
    with _ctx(org):
        result = ServicesMutation().test_model_endpoint(_info(), _input(svc))
    assert result.ok
    assert result.data.status == "timed_out"
    assert result.data.error
