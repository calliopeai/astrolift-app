# ruff: noqa: F811
"""Prompt readiness and dispatch retain actual live owner and credential gates."""

import json
import os
import uuid
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.utils import timezone
from graphql import GraphQLError

from astrolift_clusters import agent_test_jobs
from astrolift_graphql import GUID
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations import ServicesMutation, TestModelEndpointInput
from astrolift_services.schema.queries import ServicesQuery
from astrolift_services.tests.test_scoped_gates_2106 import no_search, subject, world  # noqa: F401
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster

pytestmark = pytest.mark.django_db
READ = "query($id:GUID!){ astroliftModelPromptReadiness(id:$id){state eligible maxPromptChars maxOutputTokens promptsPerMinute maxWaitSeconds} }"
SEND = 'mutation($id:GUID!){ testModelEndpoint(input:{managedServiceId:$id,prompt:"A concrete local regression prompt"}){ok errors{code field} data{status reply}} }'


def setup(w, kind="APP", owner=None, permissions=None):
    owner = owner or {"APP": w.medops_app, "TEAM": w.medops, "PROJECT": w.medops_project, "ORG": w.org}[kind]
    bind_role(
        w.user,
        permissions=permissions or [Permission.APP_READ, Permission.APP_UPDATE],
        kind=kind,
        scope_id=owner.pk,
        slug="prompt",
    )
    w.cluster.last_heartbeat_at = timezone.now()
    w.cluster.provider_config = {"vllm_agent_test": {"namespace": "astrolift-system"}}
    w.cluster.save()
    row = w.medops_private
    row.kind = ManagedService.Kind.MODEL_ENDPOINT
    row.variant = "vllm"
    row.status = ManagedService.Status.ACTIVE
    row.config = {"model": "Qwen/Qwen3-8B"}
    row.save()
    return row


def info(w):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=w.user, META={})))


def read(w, row):
    return ServicesQuery().astrolift_model_prompt_readiness(info(w), GUID(str(row.guid)))


def send(w, row):
    return ServicesMutation().test_model_endpoint(
        info(w),
        TestModelEndpointInput(
            managed_service_id=GUID(str(row.guid)), prompt="A concrete local regression prompt"
        ),
    )


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT", "ORG"])
def test_actual_owner_authorizes_advisory_read_without_dispatch_or_cache(world, kind, monkeypatch):
    row = setup(world, kind)
    monkeypatch.setattr(agent_test_jobs, "enqueue", lambda **kwargs: pytest.fail("read dispatched"))
    monkeypatch.setattr(
        agent_test_jobs, "check_rate_limit", lambda *_: pytest.fail("read consumed rate budget")
    )
    with subject(world):
        metadata = read(world, row)
    assert metadata.state.value == "ready" and metadata.eligible
    assert metadata.max_prompt_chars == 4000
    assert metadata.max_output_tokens == 128
    assert metadata.prompts_per_minute == 6
    assert 20 <= metadata.max_wait_seconds <= 60


@pytest.mark.parametrize(
    "change,expected",
    [
        ("unsupported", "unsupported"),
        ("inactive-service", "inactive"),
        ("inactive-cluster", "unavailable"),
        ("no-heartbeat", "unknown_heartbeat"),
        ("stale-heartbeat", "stale_heartbeat"),
        ("no-relay", "unconfigured_relay"),
        ("malformed-relay", "unconfigured_relay"),
        ("no-model", "unconfigured_model"),
    ],
)
def test_actual_readiness_states_also_refuse_invocation_before_rate_cache(
    world, change, expected, monkeypatch
):
    row = setup(world)
    if change == "unsupported":
        row.variant = "bedrock"
        row.save()
    elif change == "inactive-service":
        row.status = "provisioning"
        row.save()
    elif change == "inactive-cluster":
        world.cluster.is_active = False
        world.cluster.save()
    elif change == "no-heartbeat":
        world.cluster.last_heartbeat_at = None
        world.cluster.save()
    elif change == "stale-heartbeat":
        from datetime import timedelta

        world.cluster.last_heartbeat_at -= timedelta(hours=2)
        world.cluster.save()
    elif change == "no-relay":
        world.cluster.provider_config = {}
        world.cluster.save()
    elif change == "malformed-relay":
        world.cluster.provider_config = {"vllm_agent_test": "not-a-config"}
        world.cluster.save()
    else:
        row.config = {}
        row.save()
    monkeypatch.setattr(
        agent_test_jobs, "check_rate_limit", lambda *_: pytest.fail("ineligible request consumed cache")
    )
    with subject(world):
        metadata = read(world, row)
        outcome = send(world, row)
    assert metadata.state.value == expected and not metadata.eligible
    assert not outcome.ok
    assert outcome.errors[0].code in ("VALIDATION", "PRECONDITION")


@pytest.mark.parametrize(
    "condition",
    [
        "deleted-service",
        "deleted-app",
        "deleted-project",
        "deleted-team",
        "foreign-project",
        "incoherent-team",
        "deleted-env",
        "foreign-env",
        "deleted-cluster",
        "foreign-cluster",
    ],
)
def test_org_grant_cannot_read_retired_or_incoherent_facts_or_dispatch(world, condition, monkeypatch):
    row = setup(world, "ORG")
    if condition == "deleted-service":
        row.soft_delete()
    elif condition == "deleted-app":
        world.medops_app.soft_delete()
    elif condition == "deleted-project":
        world.medops_project.soft_delete()
    elif condition == "deleted-team":
        world.medops.soft_delete()
    elif condition == "foreign-project":
        world.medops_app.project = ScopeWorld("foreign-prompt").medops_project
        world.medops_app.save()
    elif condition == "incoherent-team":
        world.medops_app.team = world.platform
        world.medops_app.save()
    elif condition == "deleted-env":
        world.medops_env.soft_delete()
    elif condition == "foreign-env":
        row.app_environment = world.platform_env
        row.save()
    elif condition == "deleted-cluster":
        world.cluster.soft_delete()
    else:
        foreign = ScopeWorld("foreign-cluster-prompt")
        world.medops_env.tenant_cluster = make_cluster(foreign, "foreign-prompt")
        world.medops_env.save()
    monkeypatch.setattr(
        "astrolift_services.schema.queries.prompt_readiness",
        lambda *_: pytest.fail("invalid owner read readiness facts"),
    )
    monkeypatch.setattr(
        "astrolift_services.schema.mutations.managed_services.prompt_readiness",
        lambda *_: pytest.fail("invalid owner read invocation facts"),
    )
    monkeypatch.setattr(
        agent_test_jobs, "check_rate_limit", lambda *_: pytest.fail("invalid owner accessed cache")
    )
    with subject(world):
        try:
            assert read(world, row) is None
        except GraphQLError as error:
            assert error.extensions["code"] == "PERMISSION_DENIED"
        assert not send(world, row).ok


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT"])
def test_actual_owner_environment_policy_denies_before_facts_or_cache(world, kind, monkeypatch):
    row = setup(world, "ORG")
    owner = {"APP": world.medops_app, "TEAM": world.medops, "PROJECT": world.medops_project}[kind]
    Policy.objects.create(
        organization=world.org,
        name="No production prompt",
        slug="no-prompt",
        effect="DENY",
        scope_level=kind,
        scope_id=owner.pk,
        action_pattern="app.update",
        resource_pattern={"env": ["production"]},
    )
    monkeypatch.setattr(
        "astrolift_services.schema.queries.prompt_readiness",
        lambda *_: pytest.fail("denied query inspected facts"),
    )
    monkeypatch.setattr(
        agent_test_jobs, "check_rate_limit", lambda *_: pytest.fail("denied invocation accessed cache")
    )
    with subject(world):
        with pytest.raises(GraphQLError) as denied:
            read(world, row)
        assert denied.value.extensions["code"] == "PERMISSION_DENIED"
        assert send(world, row).errors[0].code == "PERMISSION_DENIED"


@pytest.mark.parametrize("version", [None, "0.2.0", "0.3.0", "custom-unverified"])
def test_reported_version_is_not_a_relay_capability_guarantee(world, version):
    row = setup(world)
    world.cluster.last_heartbeat_payload = {"agent_version": version}
    world.cluster.save()
    with subject(world):
        assert read(world, row).eligible


@pytest.mark.parametrize(
    "authority",
    [
        "owner",
        "sibling",
        "team-ceiling",
        "read-ceiling",
        "read-only-role",
        "foreign-org",
        "inactive-user",
        "deleted-org",
        "inactive-member",
    ],
)
def test_http_rechecks_role_bearer_and_revocation_before_any_job(world, authority, monkeypatch):
    row = setup(
        world,
        "ORG" if authority in ("team-ceiling", "read-ceiling") else "APP",
        owner=world.platform_app if authority == "sibling" else None,
        permissions=[Permission.APP_READ] if authority == "read-only-role" else None,
    )
    membership = Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    if authority == "deleted-org":
        world.org.soft_delete()
    if authority == "inactive-member":
        membership.soft_delete()
    minted = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="prompt-http",
        token_hash=minted.token_hash,
        scopes=["read:apps"] if authority == "read-ceiling" else ["admin"],
        team=world.platform if authority == "team-ceiling" else None,
    )
    if authority == "foreign-org":
        foreign = ScopeWorld("foreign-http-prompt")
        Member.objects.create(user=world.user, scope_kind="ORG", scope_id=foreign.org.pk)
        token.organization = foreign.org
        token.save()
    if authority == "inactive-user":
        world.user.is_active = False
        world.user.save()
    monkeypatch.setattr(
        agent_test_jobs, "check_rate_limit", lambda *_: pytest.fail("refused HTTP call accessed cache")
    )
    client = Client()

    def call(query):
        return client.post(
            "/app/gql/config/",
            data=json.dumps({"query": query, "variables": {"id": str(row.guid)}}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
            HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
        )

    response = call(READ)
    if authority == "owner":
        assert (
            response.status_code == 200
            and response.json()["data"]["astroliftModelPromptReadiness"]["eligible"]
        )
        token.is_revoked = True
        token.save()
        response = call(READ)
    assert response.status_code in (200, 401, 403)
    if response.status_code == 200:
        assert response.json()["errors"][0]["extensions"]["code"] == "PERMISSION_DENIED"
    outcome = call(SEND)
    assert outcome.status_code in (200, 401, 403)
    if outcome.status_code == 200:
        assert not outcome.json()["data"]["testModelEndpoint"]["ok"]


def test_authorized_prompt_roundtrip_uses_only_persisted_service_target(world, monkeypatch):
    row = setup(world)

    def agent_reply(job_id, **kwargs):
        dispatched = agent_test_jobs.dispatch_pending(str(world.cluster.guid))
        assert dispatched["managed_service_guid"] == str(row.guid)
        assert dispatched["model"] == "Qwen/Qwen3-8B"
        assert dispatched["base_url"].endswith(".svc.cluster.local:8000/v1")
        assert dispatched["secret_namespace"] in dispatched["base_url"]
        assert dispatched["max_tokens"] == 128
        agent_test_jobs.record_result(
            cluster_guid=str(world.cluster.guid),
            job_id=job_id,
            ok=True,
            reply="Controlled agent transport reply",
            total_tokens=7,
        )
        return agent_test_jobs.get_job(job_id)

    monkeypatch.setattr(agent_test_jobs, "await_result", agent_reply)
    with subject(world):
        outcome = send(world, row)
    assert outcome.ok and outcome.data.status == "succeeded"
    assert outcome.data.reply == "Controlled agent transport reply"
    assert outcome.data.total_tokens == 7


@pytest.mark.parametrize("failure_at", ["rate", "enqueue", "poll"])
def test_actual_owner_http_cache_unavailability_is_a_failure_envelope(world, monkeypatch, failure_at):
    row = setup(world)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="prompt-unavailable-http",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )
    unavailable = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
    location = (
        os.environ.get("ASTROLIFT_TEST_REDIS_URL")
        or os.environ.get("DJANGO_CACHE_URL")
        or settings.CACHES["default"].get("LOCATION")
    )
    assert location, "Real Redis is required; configure DJANGO_CACHE_URL or ASTROLIFT_TEST_REDIS_URL"
    available = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": location,
            "KEY_PREFIX": "relay-http-boundary-" + uuid.uuid4().hex,
            "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
        }
    }
    stages = []
    admitted = []
    with override_settings(CACHES=available):
        with ExitStack() as failures:
            if failure_at == "rate":
                failures.enter_context(override_settings(CACHES=unavailable))
            elif failure_at == "enqueue":
                original = agent_test_jobs.check_rate_limit

                def consume_budget_then_lose_cache(user_id):
                    original(user_id)
                    stages.append("rate")
                    failures.enter_context(override_settings(CACHES=unavailable))

                monkeypatch.setattr(agent_test_jobs, "check_rate_limit", consume_budget_then_lose_cache)
            else:
                original = agent_test_jobs.enqueue

                def admit_then_lose_cache(**kwargs):
                    job_id = original(**kwargs)
                    admitted.append(job_id)
                    stages.append("enqueue")
                    failures.enter_context(override_settings(CACHES=unavailable))
                    return job_id

                monkeypatch.setattr(agent_test_jobs, "enqueue", admit_then_lose_cache)
            response = Client().post(
                "/app/gql/config/",
                data=json.dumps(
                    {
                        "query": SEND.replace("errors{code field}", "errors{code field message}"),
                        "variables": {"id": str(row.guid)},
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
            )
        if failure_at == "poll":
            # Result transport uncertainty does not cancel an admitted job.
            assert agent_test_jobs.current_job_id(str(world.cluster.guid)) == admitted[0]
            assert agent_test_jobs.get_job(admitted[0])["status"] == agent_test_jobs.PENDING
            assert stages == ["enqueue"]
        elif failure_at == "enqueue":
            assert stages == ["rate"]
            assert agent_test_jobs.current_job_id(str(world.cluster.guid)) is None
    assert response.status_code == 200
    envelope = response.json()["data"]["testModelEndpoint"]
    assert not envelope["ok"] and envelope["data"] is None
    assert envelope["errors"][0]["code"] == "PRECONDITION"
    assert envelope["errors"][0]["message"] == (
        "model test relay result is unavailable"
        if failure_at == "poll"
        else "model test relay is unavailable"
    )
