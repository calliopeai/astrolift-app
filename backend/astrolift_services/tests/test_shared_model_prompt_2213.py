"""Shared-owner prompt admission and real bounded relay transport (#2213)."""

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.utils import timezone
from graphql import GraphQLError

from astrolift_clusters import agent_test_jobs as jobs
from astrolift_clusters.models import TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_services.model_prompt import shared_agent_test_target
from astrolift_services.models import ManagedService
from astrolift_services.schema.shared_model_prompt import (
    SharedModelPromptMutations,
    SharedModelPromptQuery,
    TestSharedModelEndpointInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user
from providers._sdk.k8s_naming import cluster_model_namespace, cluster_model_resource_name, dns_label

pytestmark = pytest.mark.django_db
PROMPT = "Return the words shared model regression."


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *_: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *_: None))
    w = ScopeWorld("sharedprompt2213")
    w.user = make_user("sharedprompt2213")
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    w.cluster = make_cluster(w, "sharedprompt2213")
    w.agent_key = "shared-prompt-controlled-agent-key"
    w.cluster.agent_key_hash = hashlib.sha256(w.agent_key.encode()).hexdigest()
    w.cluster.lifecycle = TenantCluster.Lifecycle.MANAGED
    w.cluster.region = "us-west-2"
    w.cluster.last_heartbeat_at = timezone.now()
    w.cluster.provider_config = {"vllm_agent_test": {"namespace": "astrolift-system"}}
    w.cluster.save()
    w.model = ManagedService.objects.create(
        organization=w.org,
        tenant_cluster=w.cluster,
        kind="model_endpoint",
        variant="vllm",
        name="shared-prompt-model",
        status="active",
        config={"model": "Qwen/Qwen3-0.6B", "model_revision": "a" * 40, "task": "generate"},
        applied_config={"model": "Qwen/Qwen3-0.6B", "model_revision": "a" * 40, "replicas": 1},
        subscription_revision=2,
        applied_subscription_revision=2,
        model_ready_observed_at=timezone.now(),
        model_ready_generation=3,
        model_ready_auth_revision=2,
    )
    w.namespace = cluster_model_namespace(
        organization_id=str(w.org.guid),
        cluster_id=str(w.cluster.guid),
        managed_service_id=str(w.model.guid),
    )
    w.resource = cluster_model_resource_name(str(w.model.guid))
    w.model.backend_ref = f"model_endpoint/{w.cluster.guid}/{w.namespace}/{w.resource}"
    w.model.save()
    return w


@pytest.fixture
def redis_cache():
    location = (
        os.environ.get("ASTROLIFT_TEST_REDIS_URL")
        or os.environ.get("DJANGO_CACHE_URL")
        or settings.CACHES["default"].get("LOCATION")
    )
    assert location, "Configure the isolated actual Redis endpoint."
    configuration = {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": location,
        "KEY_PREFIX": "shared-prompt2213-" + uuid4().hex,
        "OPTIONS": {"socket_timeout": 2, "socket_connect_timeout": 2},
    }
    with override_settings(CACHES={"default": configuration}):
        from django.core.cache import caches

        backend = caches["default"]
        client = backend._cache.get_client(write=True)
        assert client.ping()
        yield
        keys = list(client.scan_iter(match=backend.make_key("*")))
        if keys:
            client.delete(*keys)


def grant(w, kind="ORG", permission=Permission.CLUSTER_UPDATE):
    owner = {"ORG": w.org, "TEAM": w.medops, "PROJECT": w.medops_project, "APP": w.medops_app}[kind]
    return bind_role(w.user, permissions=[permission], kind=kind, scope_id=owner.pk, slug="sharedprompt")


@contextmanager
def caller(w, token=None):
    marker = set_current_api_token(token) if token is not None else None
    try:
        with tenant_context(
            TenantContext(organization_id=w.org.pk, team_id=w.medops.pk, actor_user_id=w.user.pk)
        ):
            yield
    finally:
        if marker is not None:
            reset_current_api_token(marker)


def arguments(w, **overrides):
    return {
        "id": GUID(str(w.model.guid)),
        "expected_cluster_id": GUID(str(w.cluster.guid)),
        "expected_provider_id": GUID(str(w.cluster.provider_plugin.guid)),
        "expected_version": w.model.version,
    } | overrides


def read(w, **overrides):
    return SharedModelPromptQuery().astrolift_shared_model_prompt_readiness(
        make_info(w.user), **arguments(w, **overrides)
    )


def send(w, *, prompt=PROMPT, **overrides):
    args = arguments(w, **overrides)
    args["managed_service_id"] = args.pop("id")
    return SharedModelPromptMutations().test_shared_model_endpoint(
        make_info(w.user), TestSharedModelEndpointInput(**args, prompt=prompt)
    )


def no_relay(monkeypatch):
    for name in ("check_rate_limit", "enqueue", "await_result"):
        monkeypatch.setattr(
            jobs, name, lambda *_a, **_k: pytest.fail("Refused read reached relay cache/queue.")
        )


def state(result):
    return str(getattr(result.state, "value", result.state))


def test_actual_owner_read_is_advisory_and_exposes_only_limits(world, monkeypatch):
    grant(world)
    no_relay(monkeypatch)
    with caller(world):
        result = read(world)
    assert result.eligible and state(result) == "ready"
    assert result.max_prompt_chars == jobs.MAX_PROMPT_CHARS
    assert result.max_output_tokens == jobs.MAX_TOKENS
    assert result.prompts_per_minute == jobs.RATE_LIMIT_PER_MINUTE
    assert 20 <= result.max_wait_seconds <= 60
    assert world.model.registered_app_id is world.model.project_id is world.model.app_environment_id is None
    assert not any(
        hasattr(result, key) for key in ("base_url", "api_key", "secret_name", "config", "namespace")
    )


@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "APP"])
def test_descendant_cluster_grants_never_authorize_owner_prompt(world, monkeypatch, kind):
    grant(world, kind)
    no_relay(monkeypatch)
    with caller(world):
        with pytest.raises(GraphQLError) as denied:
            read(world)
        assert denied.value.extensions["code"] == "PERMISSION_DENIED"
        result = send(world)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"


@pytest.mark.parametrize("ceiling", ["read-clusters", "team", "foreign", "revoked", "expired"])
def test_actual_owner_grant_retains_live_credential_ceilings(world, monkeypatch, ceiling):
    grant(world)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="shared prompt credential",
        token_hash=uuid4().hex,
        scopes=["read:clusters"] if ceiling == "read-clusters" else ["admin"],
        team=world.medops if ceiling == "team" else None,
        is_revoked=ceiling == "revoked",
        expires_at=timezone.now() - timedelta(seconds=1) if ceiling == "expired" else None,
    )
    if ceiling == "foreign":
        token.organization = ScopeWorld("sharedpromptforeign").org
        token.save()
    no_relay(monkeypatch)
    with caller(world, token):
        with pytest.raises(GraphQLError):
            read(world)
        assert not send(world).ok


@pytest.mark.parametrize(
    "ceiling", ["read-clusters", "team", "revoked", "inactive-user", "retired-membership"]
)
def test_actual_http_bearer_admission_and_revocation(world, monkeypatch, ceiling):
    grant(world)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="shared prompt HTTP",
        token_hash=minted.token_hash,
        scopes=["read:clusters"] if ceiling == "read-clusters" else ["admin"],
        team=world.medops if ceiling == "team" else None,
    )
    variables = {
        "id": str(world.model.guid),
        "cluster": str(world.cluster.guid),
        "provider": str(world.cluster.provider_plugin.guid),
        "version": world.model.version,
    }
    query = """query($id:GUID!,$cluster:GUID!,$provider:GUID!,$version:Int!){
        astroliftSharedModelPromptReadiness(id:$id,expectedClusterId:$cluster,
        expectedProviderId:$provider,expectedVersion:$version){eligible state maxOutputTokens}}
    """
    mutation = """mutation($id:GUID!,$cluster:GUID!,$provider:GUID!,$version:Int!){
        testSharedModelEndpoint(input:{managedServiceId:$id,expectedClusterId:$cluster,
        expectedProviderId:$provider,expectedVersion:$version,prompt:"Explicit local refusal regression"}){
        ok data{status} errors{code message field currentVersion}}}
    """
    no_relay(monkeypatch)
    client = Client()

    def call(document):
        return client.post(
            "/app/gql/config/",
            data=json.dumps({"query": document, "variables": variables}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
        )

    if ceiling not in {"read-clusters", "team"}:
        initial = call(query)
        assert initial.status_code == 200
        assert initial.json()["data"]["astroliftSharedModelPromptReadiness"]["eligible"]
    if ceiling == "revoked":
        token.is_revoked = True
        token.save()
    elif ceiling == "inactive-user":
        world.user.is_active = False
        world.user.save()
    elif ceiling == "retired-membership":
        Member.objects.get(user=world.user, scope_kind="ORG", scope_id=world.org.pk).soft_delete()
    refused = call(query)
    assert refused.status_code in {200, 401, 403}
    if refused.status_code == 200:
        assert refused.json()["errors"][0]["extensions"]["code"] == "PERMISSION_DENIED"
    refused_write = call(mutation)
    assert refused_write.status_code in {200, 401, 403}
    if refused_write.status_code == 200:
        assert (
            "errors" not in refused_write.json()
        ), "Handled refusal must satisfy the complete public envelope."
        envelope = refused_write.json()["data"]["testSharedModelEndpoint"]
        assert not envelope["ok"] and envelope["errors"][0]["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("fact", ["region", "environment"])
def test_actual_cluster_region_and_no_environment_policy_are_checked(world, monkeypatch, fact):
    grant(world)
    Policy.objects.create(
        organization=world.org,
        name="No shared prompt",
        effect="DENY",
        action_pattern="cluster.update",
        resource_pattern={"region": "us-west-2"} if fact == "region" else {},
        conditions=[] if fact == "region" else [{"kind": "env_match", "env_in": ["production"]}],
    )
    no_relay(monkeypatch)
    with caller(world):
        with pytest.raises(GraphQLError) as denied:
            read(world)
        assert denied.value.extensions["code"] == "PERMISSION_DENIED"
        assert send(world).errors[0].code == "PERMISSION_DENIED"


def test_nonmatching_region_policy_does_not_borrow_an_unknown_region(world, monkeypatch):
    grant(world)
    Policy.objects.create(
        organization=world.org,
        name="East region refused",
        effect="DENY",
        action_pattern="cluster.update",
        resource_pattern={"region": "us-east-1"},
        conditions=[],
    )
    no_relay(monkeypatch)
    with caller(world):
        assert read(world).eligible


def test_mutation_rechecks_actual_region_after_advisory_permission_before_queue(world, monkeypatch):
    from astrolift_services.schema import shared_model_prompt

    grant(world)
    Policy.objects.create(
        organization=world.org,
        name="New east placement refused",
        effect="DENY",
        action_pattern="cluster.update",
        resource_pattern={"region": "us-east-1"},
        conditions=[],
    )
    no_relay(monkeypatch)
    with caller(world):
        assert read(world).eligible
    original = shared_model_prompt._target

    def changed_target(*args, **kwargs):
        if kwargs.get("lock"):
            TenantCluster.objects.filter(pk=world.cluster.pk).update(region="us-east-1")
        return original(*args, **kwargs)

    monkeypatch.setattr(shared_model_prompt, "_target", changed_target)
    with caller(world):
        result = send(world)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"


@pytest.mark.parametrize("field", ["id", "expected_cluster_id", "expected_provider_id", "expected_version"])
def test_missing_or_changed_immutable_target_fails_before_queue(world, monkeypatch, field):
    grant(world)
    no_relay(monkeypatch)
    change = {field: world.model.version + 1 if field == "expected_version" else GUID(str(uuid4()))}
    with caller(world):
        try:
            result = read(world, **change)
            assert result is None or not result.eligible
        except GraphQLError as refused:
            assert refused.extensions["code"] in {"NOT_FOUND", "PRECONDITION"}
        result = send(world, **change)
    assert not result.ok and result.errors[0].code in {"NOT_FOUND", "PRECONDITION"}


@pytest.mark.parametrize(
    "change", ["model", "org", "cluster", "provider", "foreign-cluster", "foreign-model"]
)
def test_retired_or_foreign_ancestry_is_not_visible_to_org_owner(world, monkeypatch, change):
    grant(world)
    if change == "model":
        world.model.soft_delete()
    elif change == "org":
        world.org.soft_delete()
    elif change == "cluster":
        world.cluster.soft_delete()
    elif change == "provider":
        world.cluster.provider_plugin.soft_delete()
    elif change == "foreign-cluster":
        world.cluster.organization = ScopeWorld("sharedpromptforeigncluster").org
        world.cluster.save()
    else:
        world.model.organization = ScopeWorld("sharedpromptforeignmodel").org
        world.model.save()
    no_relay(monkeypatch)
    with caller(world):
        try:
            result = read(world)
            assert result is None
        except GraphQLError as refused:
            assert refused.extensions["code"] in {"NOT_FOUND", "PERMISSION_DENIED"}
        assert not send(world).ok


@pytest.mark.parametrize(
    "change,expected",
    [
        ("inactive-model", "inactive"),
        ("inactive-cluster", "unavailable"),
        ("unmanaged", "unavailable"),
        ("disabled-provider", "unavailable"),
        ("unknown-heartbeat", "unknown_heartbeat"),
        ("stale-heartbeat", "stale_heartbeat"),
        ("missing-relay", "unconfigured_relay"),
        ("missing-model", "unconfigured_model"),
        ("pending-auth", "inactive"),
        ("missing-observation", "inactive"),
        ("missing-generation", "inactive"),
        ("zero-generation", "inactive"),
        ("old-pod-auth", "inactive"),
        ("missing-applied-config", "inactive"),
        ("stopped-applied-replicas", "inactive"),
        ("malformed-applied-replicas", "inactive"),
        ("unsupported-task", "unsupported"),
    ],
)
def test_readiness_refusals_do_not_consume_budget_or_dispatch(world, monkeypatch, change, expected):
    grant(world)
    if change == "inactive-model":
        world.model.status = "provisioning"
        world.model.save()
    elif change == "missing-model":
        world.model.config = {}
        world.model.save()
    elif change == "pending-auth":
        world.model.applied_subscription_revision = 1
        world.model.save()
    elif change in {"missing-observation", "missing-generation", "zero-generation", "old-pod-auth"}:
        field, value = {
            "missing-observation": ("model_ready_observed_at", None),
            "missing-generation": ("model_ready_generation", None),
            "zero-generation": ("model_ready_generation", 0),
            "old-pod-auth": ("model_ready_auth_revision", 1),
        }[change]
        setattr(world.model, field, value)
        world.model.save()
    elif change in {"missing-applied-config", "stopped-applied-replicas", "malformed-applied-replicas"}:
        world.model.applied_config = {
            "missing-applied-config": None,
            "stopped-applied-replicas": {"replicas": 0},
            "malformed-applied-replicas": {"replicas": True},
        }[change]
        world.model.save()
    elif change == "unsupported-task":
        world.model.config = {**world.model.config, "task": "embed"}
        world.model.save()
    elif change == "disabled-provider":
        world.cluster.provider_plugin.is_enabled = False
        world.cluster.provider_plugin.save()
    else:
        if change == "inactive-cluster":
            world.cluster.is_active = False
        elif change == "unmanaged":
            world.cluster.lifecycle = TenantCluster.Lifecycle.REGISTERED
        elif change == "unknown-heartbeat":
            world.cluster.last_heartbeat_at = None
        elif change == "stale-heartbeat":
            world.cluster.last_heartbeat_at = timezone.now() - timedelta(hours=2)
        elif change == "missing-relay":
            world.cluster.provider_config = {}
        world.cluster.save()
    no_relay(monkeypatch)
    with caller(world):
        result = read(world)
        outcome = send(world)
    assert not result.eligible and state(result) == expected
    assert not outcome.ok and outcome.errors[0].code in {"PRECONDITION", "VALIDATION"}


def test_shared_relay_target_is_pure_canonical_model_identity(world):
    target = shared_agent_test_target(world.model)
    assert target.base_url == f"http://{world.resource}.{world.namespace}.svc.cluster.local:8000/v1"
    assert target.api_key_secret_namespace == world.namespace
    assert target.api_key_secret_name == dns_label(world.resource, "vllm", max_length=63)
    assert target.api_key_secret_key == "api_key"
    assert target.model == world.model.config["model"]
    assert world.medops_app.slug not in target.base_url and world.medops_project.slug not in target.base_url


def test_revoked_role_cannot_continue_previously_ready_prompt(world, monkeypatch):
    role = grant(world)
    no_relay(monkeypatch)
    with caller(world):
        assert read(world).eligible
    role.soft_delete()
    with caller(world), pytest.raises(GraphQLError):
        read(world)
    with caller(world):
        assert send(world).errors[0].code == "PERMISSION_DENIED"


@pytest.mark.parametrize("prompt", ["", "   ", "x" * (jobs.MAX_PROMPT_CHARS + 1)])
def test_invalid_prompt_never_reaches_relay(world, monkeypatch, prompt):
    grant(world)
    no_relay(monkeypatch)
    with caller(world):
        result = send(world, prompt=prompt)
    assert not result.ok and result.errors[0].code == "VALIDATION"


@pytest.mark.parametrize("model_status", [200, 503])
def test_real_redis_heartbeat_and_result_roundtrip_uses_actual_bounded_http_completion(
    world, monkeypatch, redis_cache, model_status
):
    grant(world)
    requests = []
    operator_key = "controlled-local-operator-key"

    class Completion(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.headers.get("Authorization") != "Bearer " + operator_key:
                self.send_error(401)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), payload))
            if model_status == 503:
                self.send_error(503, "Controlled model unavailable")
                return
            reply = "Observed local completion: " + payload["messages"][0]["content"]
            body = json.dumps(
                {"choices": [{"message": {"content": reply}}], "usage": {"total_tokens": 9}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Completion)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_wait = jobs.await_result
    observed_jobs = []

    def drive_relay(job_id, **kwargs):
        client = Client()
        auth = {"HTTP_AUTHORIZATION": "Bearer " + world.agent_key}
        heartbeat = client.post(
            f"/api/clusters/v1/{world.cluster.guid}/heartbeat/",
            data="{}",
            content_type="application/json",
            **auth,
        )
        assert heartbeat.status_code == 200
        job = heartbeat.json()["test_job"]
        observed_jobs.append(job)
        target = shared_agent_test_target(world.model)
        assert job["job_id"] == job_id and job["base_url"] == target.base_url
        assert job["secret_namespace"] == target.api_key_secret_namespace
        assert job["secret_name"] == target.api_key_secret_name and job["secret_key"] == "api_key"
        assert "result_url" not in job and operator_key not in json.dumps(job)
        assert job["max_tokens"] == jobs.MAX_TOKENS and job["prompt"] == PROMPT
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": job["model"],
                    "messages": [{"role": "user", "content": job["prompt"]}],
                    "max_tokens": job["max_tokens"],
                }
            ).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + operator_key},
        )
        try:
            with urlopen(request, timeout=2) as response:
                completion = json.load(response)
            report = {
                "ok": True,
                "reply": completion["choices"][0]["message"]["content"],
                "total_tokens": completion["usage"]["total_tokens"],
            }
        except HTTPError as error:
            report = {"ok": False, "error": f"Observed model HTTP {error.code}"}
            error.close()
        result = client.post(
            f"/api/clusters/v1/{world.cluster.guid}/model-test-result/",
            data=json.dumps({"job_id": job_id, **report}),
            content_type="application/json",
            **auth,
        )
        assert result.status_code == 200 and result.json() == {"ok": True}
        return original_wait(job_id, poll_seconds=0.01, **kwargs)

    monkeypatch.setattr(jobs, "await_result", drive_relay)
    try:
        with caller(world):
            outcome = send(world)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert outcome.ok
    if model_status == 200:
        assert outcome.data.status == "succeeded"
        assert outcome.data.reply == "Observed local completion: " + PROMPT
        assert outcome.data.total_tokens == 9
    else:
        assert outcome.data.status == "failed"
        assert outcome.data.reply == "" and outcome.data.total_tokens is None
        assert outcome.data.error == "Observed model HTTP 503"
    assert len(observed_jobs) == len(requests) == 1
    assert requests[0][0] == "/v1/chat/completions"
    assert requests[0][1] == "Bearer " + operator_key
    assert jobs.current_job_id(str(world.cluster.guid)) is None
