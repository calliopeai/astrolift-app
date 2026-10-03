"""Disposable PostgreSQL and actual installed SDK -> native Kubernetes HTTP proof."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from django.contrib.auth import get_user_model
from django.test import Client
from kubernetes.client import ApiClient, Configuration

from astrolift_clusters import agent_install as install
from astrolift_clusters.models import ClusterAgentInstall, ProviderPlugin, TenantCluster
from astrolift_clusters.tests import test_auth_user_lock_wait_2225 as role_cases
from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member, Organization
from core.permissions import Permission

real_role_resolver = role_cases.real_role_resolver

pytestmark = pytest.mark.django_db(transaction=True)


class ApiServer:
    """Discovery, create-only and UID/RV conditional SSA; no Secret body logging."""

    resources = {
        "/api/v1": [
            ("Namespace", "namespaces", False),
            ("Secret", "secrets", True),
            ("ServiceAccount", "serviceaccounts", True),
        ],
        "/apis/apps/v1": [("Deployment", "deployments", True)],
        "/apis/rbac.authorization.k8s.io/v1": [
            ("ClusterRole", "clusterroles", False),
            ("ClusterRoleBinding", "clusterrolebindings", False),
        ],
    }

    def __init__(self):
        self.objects, self.calls = {}, []
        self.before_write = None
        self.fail_kind = None
        self.lose_kind = None
        self.fail_delete = False
        self.before_delete = None
        self.counter = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, code, data):
                encoded = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def error(self, code):
                self.reply(
                    code,
                    {
                        "kind": "Status",
                        "apiVersion": "v1",
                        "status": "Failure",
                        "reason": "NotFound" if code == 404 else "Conflict",
                        "message": "disposable boundary refusal",
                        "code": code,
                    },
                )

            def do_GET(self):
                path = urlsplit(self.path).path
                owner.calls.append(("GET", path, self.headers.get("Authorization")))
                if path == "/version":
                    return self.reply(200, {"major": "1", "minor": "30", "gitVersion": "v1.30.0"})
                if path == "/api":
                    return self.reply(200, {"kind": "APIVersions", "versions": ["v1"]})
                if path == "/apis":
                    return self.reply(
                        200,
                        {
                            "kind": "APIGroupList",
                            "groups": [
                                {
                                    "name": g,
                                    "versions": [{"groupVersion": f"{g}/v1", "version": "v1"}],
                                    "preferredVersion": {"groupVersion": f"{g}/v1", "version": "v1"},
                                }
                                for g in ["apps", "rbac.authorization.k8s.io"]
                            ],
                        },
                    )
                if path in owner.resources:
                    gv = path.removeprefix("/apis/").removeprefix("/api/")
                    return self.reply(
                        200,
                        {
                            "kind": "APIResourceList",
                            "groupVersion": gv,
                            "resources": [
                                {
                                    "name": plural,
                                    "kind": kind,
                                    "namespaced": ns,
                                    "verbs": ["get", "create", "patch", "delete"],
                                }
                                for kind, plural, ns in owner.resources[path]
                            ],
                        },
                    )
                if path in owner.objects:
                    return self.reply(200, owner.objects[path])
                self.error(404)

            def write(self, method):
                path = urlsplit(self.path).path
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.calls.append((method, path, self.headers.get("Authorization")))
                if owner.before_write:
                    action, owner.before_write = owner.before_write, None
                    action(body)
                if body["kind"] == owner.fail_kind:
                    return self.reply(503, {"kind": "Status", "code": 503, "message": "disposable outage"})
                target = f"{path}/{body['metadata']['name']}" if method == "POST" else path
                current = owner.objects.get(target)
                if method == "POST" and current is not None:
                    return self.error(409)
                if method == "PATCH" and (
                    current is None
                    or body["metadata"].get("uid") != current["metadata"]["uid"]
                    or body["metadata"].get("resourceVersion") != current["metadata"]["resourceVersion"]
                ):
                    return self.error(409)
                owner.counter += 1
                obj = copy.deepcopy(body)
                obj["metadata"].update(
                    uid=current["metadata"]["uid"] if current else str(uuid4()),
                    resourceVersion=str(owner.counter),
                )
                owner.objects[target] = obj
                if obj["kind"] == owner.lose_kind:
                    owner.lose_kind = None
                    return self.reply(
                        503, {"kind": "Status", "code": 503, "message": "reply lost after accepted write"}
                    )
                self.reply(201 if method == "POST" else 200, obj)

            def do_POST(self):
                self.write("POST")

            def do_PATCH(self):
                self.write("PATCH")

            def do_DELETE(self):
                path = urlsplit(self.path).path
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.calls.append(("DELETE", path, self.headers.get("Authorization")))
                if owner.before_delete:
                    action, owner.before_delete = owner.before_delete, None
                    action()
                if owner.fail_delete:
                    return self.reply(
                        503, {"kind": "Status", "code": 503, "message": "disposable cleanup outage"}
                    )
                current = owner.objects.get(path)
                if current is None:
                    return self.error(404)
                if any(current["metadata"].get(k) != v for k, v in body.get("preconditions", {}).items()):
                    return self.error(409)
                del owner.objects[path]
                self.reply(200, {"kind": "Status", "apiVersion": "v1", "status": "Success"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get_kind(self, kind):
        return next(obj for obj in self.objects.values() if obj["kind"] == kind)


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


@pytest.fixture
def world(permission_resolver, monkeypatch, settings):
    settings.APP_BASE_URL = "https://control-plane.invalid"
    monkeypatch.setattr("constance.settings.DATABASE_CACHE_BACKEND", None)
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    server = ApiServer()
    org = Organization.objects.create(name="Disposable install", slug=f"install-{uuid4()}")
    actor = get_user_model().objects.create_user(username=f"install-{uuid4()}", is_superuser=True)
    Member.objects.create(user=actor, scope_kind=Member.ScopeKind.ORG, scope_id=org.pk)
    plugin, _ = ProviderPlugin.objects.get_or_create(slug="aws", defaults={"name": "AWS"})
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=plugin,
        slug=f"install-{uuid4()}",
        name="Disposable",
        endpoint=server.url,
        region="us-west-2",
        auth_method="exec_plugin",
        auth_config={"cluster_name": "disposable-eks"},
        agent_key_hash=hashlib.sha256(b"previous-disposable-key").hexdigest(),
    )
    issued = mint_token()
    token = ApiToken.objects.create(
        user=actor,
        organization=org,
        name="Disposable",
        token_hash=issued.token_hash,
        scopes=["manage:clusters"],
    )
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    config = Configuration()
    config.host = server.url
    config.api_key = {"authorization": "disposable-cloud-identity"}
    config.api_key_prefix = {"authorization": "Bearer"}
    # Installed cloud driver and dynamic transport are actual; only cloud SDK
    # discovery/auth boundaries are replaced, never HTTP/database behavior.
    from aws.cluster_eks import EKSClusterDriver, EKSConfig

    eks = SimpleNamespace(
        describe_cluster=lambda **kw: {
            "cluster": {
                "endpoint": server.url,
                "certificateAuthority": {"data": ""},
                "arn": "arn:aws:eks:us-west-2:000000000000:cluster/disposable-eks",
            }
        }
    )
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="disposable-eks"),
        eks_client=eks,
        sts_client=object(),
        ec2_client=object(),
        k8s_client_factory=lambda **kw: KubernetesDynamicClient.from_api_client(api_client=ApiClient(config)),
    )
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda current: driver)
    from astrolift_workflows import client as workflow_client

    original_start = workflow_client.start_workflow_once
    original_recover = workflow_client.recover_workflow_once
    monkeypatch.setattr("astrolift_workflows.client.recover_workflow_once", lambda *a, **kw: None)
    starts = []

    def start(name, args, **kw):
        starts.append((name, args, kw))
        return SimpleNamespace(run_id="disposable-run")

    monkeypatch.setattr("astrolift_workflows.client.start_workflow_once", start)
    client = Client(HTTP_AUTHORIZATION=f"Bearer {issued.plaintext}")
    yield SimpleNamespace(
        org=org,
        actor=actor,
        plugin=plugin,
        cluster=cluster,
        token=token,
        server=server,
        permission=permission_resolver,
        client=client,
        starts=starts,
        original_start=original_start,
        original_recover=original_recover,
    )
    server.close()


FIELDS = "id clusterId requestId status secretConfirmed deploymentConfirmed heartbeatConfirmed retryable workflowId errorCode errorMessage createdAt updatedAt"


def gql(world, query, variables):
    response = world.client.post(
        "/app/gql/config/",
        json.dumps({"query": query, "variables": variables}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()


def reserve(world, **overrides):
    source = overrides.get("expectedSource")
    if source is None:
        source = gql(
            world,
            "query($id:GUID!){astroliftClusterAgentInstallReview(clusterId:$id){source}}",
            {"id": str(world.cluster.guid)},
        )["data"]["astroliftClusterAgentInstallReview"]["source"]
    variables = {
        "clusterId": str(world.cluster.guid),
        "expectedVersion": world.cluster.version,
        "expectedSource": source,
        "requestId": str(uuid4()),
        "intervalSeconds": 17,
        **overrides,
    }
    result = gql(
        world,
        f"mutation($input: InstallClusterAgentInput!) {{ installClusterAgent(input:$input) {{ ok errors {{code message}} data {{{FIELDS}}} }} }}",
        {"input": variables},
    )
    assert "errors" not in result, result
    return result["data"]["installClusterAgent"], variables


def row_for(result):
    return ClusterAgentInstall.objects.get(guid=result["data"]["id"])


def pulse(world, raw="previous-disposable-key", body=None):
    return Client().post(
        f"/api/clusters/v1/{world.cluster.guid}/heartbeat/",
        json.dumps(body or {}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )


def staged_key(world):
    return base64.b64decode(
        next(
            obj
            for obj in reversed(list(world.server.objects.values()))
            if obj["kind"] == "Secret"
            and (obj["metadata"].get("labels") or {}).get("astrolift.io/install-guid")
        )["data"]["agent_key"]
    ).decode()


def test_actual_contract_install_and_heartbeat_activation(world):
    review = gql(
        world,
        "query($id:GUID!){astroliftClusterAgentInstallReview(clusterId:$id){clusterId version}}",
        {"id": str(world.cluster.guid)},
    )
    assert review["data"]["astroliftClusterAgentInstallReview"]["version"] == world.cluster.version
    result, original = reserve(world)
    assert result["ok"] and result["data"]["status"] == "QUEUED"
    row = row_for(result)
    assert install.install_attempt(row.pk, 0) == "awaiting_heartbeat"
    row.refresh_from_db()
    raw = staged_key(world)
    assert raw.encode() not in bytes(row.credential_ciphertext)
    assert raw not in json.dumps(result) and raw not in json.dumps(world.starts)
    assert row.secret_uid and row.deployment_confirmed and row.heartbeat_confirmed_at is None
    assert pulse(world).status_code == 200
    world.cluster.refresh_from_db()
    assert world.cluster.version == original["expectedVersion"]  # telemetry is not source authority
    assert pulse(world, raw).status_code == 200
    assert pulse(world).status_code == 401
    row.refresh_from_db()
    assert row.status == "succeeded" and row.heartbeat_confirmed_at
    # Recovery uses the original review tuple despite activation advancing version.
    replay, _ = reserve(world, **original)
    assert replay["ok"] and replay["data"]["status"] == "SUCCEEDED"
    assert len(world.starts) == 1
    state = gql(
        world,
        "query($id:GUID!){astroliftClusterAgentInstall(installId:$id){" + FIELDS + "}}",
        {"id": str(row.guid)},
    )
    assert state["data"]["astroliftClusterAgentInstall"]["heartbeatConfirmed"] is True


@pytest.mark.parametrize("change", ["version", "uuid", "interval-reuse"])
def test_admission_refusals_have_no_provider_or_credential_effect(world, change):
    if change == "version":
        result, _ = reserve(world, expectedVersion=world.cluster.version - 1)
    elif change == "uuid":
        result, _ = reserve(world, requestId="not-a-uuid")
    else:
        accepted, original = reserve(world)
        result, _ = reserve(world, **{**original, "intervalSeconds": 18})
        assert row_for(accepted).credential_hash
    assert result["ok"] is False
    assert not world.server.calls
    assert pulse(world).status_code == 200


@pytest.mark.parametrize("field", ["endpoint", "auth_config", "provider_config", "is_active"])
def test_partial_source_aba_refuses_before_native_effect(world, field):
    result, _ = reserve(world)
    before = getattr(world.cluster, field)
    setattr(
        world.cluster,
        field,
        not before
        if field == "is_active"
        else {"different": True}
        if isinstance(before, dict)
        else "http://different.invalid",
    )
    world.cluster.save(update_fields=[field])
    setattr(world.cluster, field, before)
    world.cluster.save(update_fields=[field])
    assert install.install_attempt(row_for(result).pk, 0) == "refused"
    assert not world.server.calls
    assert pulse(world).status_code == 200


@pytest.mark.parametrize(
    "failure", ["Secret", "ServiceAccount", "ClusterRole", "ClusterRoleBinding", "Deployment"]
)
def test_provider_failure_never_invalidates_current_credential(world, failure):
    result, _ = reserve(world)
    world.server.fail_kind = failure
    row = row_for(result)
    assert install.install_attempt(row.pk, 0) == "uncertain"
    row.refresh_from_db()
    assert row.secret_uid == "" if failure == "Secret" else bool(row.secret_uid)
    assert pulse(world).status_code == 200
    assert not row.heartbeat_confirmed_at
    world.server.fail_kind = None
    assert install.install_attempt(row.pk, 0) == "awaiting_heartbeat"
    assert pulse(world, staged_key(world)).status_code == 200


@pytest.mark.parametrize("lost", ["Secret", "Deployment"])
def test_ambiguous_create_recovers_same_staged_key(world, lost):
    result, _ = reserve(world)
    row = row_for(result)
    original_hash = row.credential_hash
    world.server.lose_kind = lost
    assert install.install_attempt(row.pk, 0) == "uncertain"
    assert pulse(world).status_code == 200
    assert install.install_attempt(row.pk, 0) == "awaiting_heartbeat"
    row.refresh_from_db()
    assert row.credential_hash == original_hash
    assert len([o for o in world.server.objects.values() if o["kind"] == "Secret"]) == 1


@pytest.mark.parametrize(
    "tamper",
    ["secret-uid", "secret-key", "deployment-uid", "deployment-ref", "permission", "token", "source"],
)
def test_activation_requires_current_original_authority_and_exact_objects(world, tamper):
    result, _ = reserve(world)
    row = row_for(result)
    assert install.install_attempt(row.pk, 0) == "awaiting_heartbeat"
    raw = staged_key(world)
    if tamper == "secret-uid":
        world.server.get_kind("Secret")["metadata"]["uid"] = str(uuid4())
    elif tamper == "secret-key":
        world.server.get_kind("Secret")["data"]["agent_key"] = base64.b64encode(b"foreign").decode()
    elif tamper == "deployment-uid":
        world.server.get_kind("Deployment")["metadata"]["uid"] = str(uuid4())
    elif tamper == "deployment-ref":
        world.server.get_kind("Deployment")["spec"]["template"]["spec"]["containers"][0]["env"][0][
            "valueFrom"
        ]["secretKeyRef"]["name"] = "foreign"
    elif tamper == "permission":
        world.permission.deny(Permission.CLUSTER_MANAGE)
    elif tamper == "token":
        world.token.is_revoked = True
        world.token.save(update_fields=["is_revoked"])
    else:
        world.cluster.auth_config = {"cluster_name": "changed"}
        world.cluster.save(update_fields=["auth_config"])
    assert pulse(world, raw).status_code == 401
    assert pulse(world).status_code == 200


def test_malformed_staged_pulse_does_not_activate(world):
    result, _ = reserve(world)
    assert install.install_attempt(row_for(result).pk, 0) == "awaiting_heartbeat"
    raw = staged_key(world)
    response = Client().post(
        f"/api/clusters/v1/{world.cluster.guid}/heartbeat/",
        "{",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    assert response.status_code == 400
    assert pulse(world).status_code == 200


def test_checkpointed_secret_replacement_is_never_adopted_on_retry(world):
    result, _ = reserve(world)
    row = row_for(result)
    world.server.fail_kind = "Deployment"
    assert install.install_attempt(row.pk, 0) == "uncertain"
    row.refresh_from_db()
    uid = row.secret_uid
    world.server.get_kind("Secret")["metadata"]["uid"] = str(uuid4())
    world.server.fail_kind = None
    assert install.install_attempt(row.pk, 0) == "refused"
    row.refresh_from_db()
    assert row.secret_uid == uid
    assert pulse(world).status_code == 200


def test_permission_withdrawn_between_provider_effects_stops_later_writes(world):
    result, _ = reserve(world)
    world.server.before_write = lambda body: world.permission.deny(Permission.CLUSTER_MANAGE)
    assert install.install_attempt(row_for(result).pk, 0) == "refused"
    assert not any(o["kind"] == "Secret" for o in world.server.objects.values())
    assert pulse(world).status_code == 200


@pytest.mark.parametrize("recovery", ["normal", "provider-uncertain", "lost-dispatch-reply"])
async def test_real_temporal_dispatch_contains_only_operation_identity(
    world, temporal_env, monkeypatch, recovery
):
    from asgiref.sync import sync_to_async

    from astrolift_workflows import client as workflow_client
    from astrolift_workflows.activities.install_cluster_agent import install_cluster_agent_attempt
    from astrolift_workflows.workflows.install_cluster_agent import InstallClusterAgentWorkflow
    from core.testing.temporal import temporal_worker

    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
    monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(workflow_client, "_task_queue", lambda: "astrolift-test")
    monkeypatch.setattr(workflow_client, "start_workflow_once", world.original_start)
    monkeypatch.setattr(workflow_client, "recover_workflow_once", world.original_recover)
    if recovery == "provider-uncertain":
        world.server.fail_kind = "Deployment"
    elif recovery == "lost-dispatch-reply":

        def uncertain_start(*args, **kwargs):
            world.original_start(*args, **kwargs)
            raise RuntimeError("disposable reply loss")

        monkeypatch.setattr(workflow_client, "start_workflow_once", uncertain_start)
    async with temporal_worker(
        temporal_env, workflows=[InstallClusterAgentWorkflow], activities=[install_cluster_agent_attempt]
    ):
        result, original = await sync_to_async(reserve)(world)
        row = await sync_to_async(row_for)(result)
        handle = temporal_env.client.get_workflow_handle(row.workflow_id, run_id=row.workflow_run_id)
        outcome = await handle.result()
        if recovery == "provider-uncertain":
            assert outcome == "uncertain"
            assert (await sync_to_async(pulse)(world)).status_code == 200
            world.server.fail_kind = None
            resumed, _ = await sync_to_async(reserve)(world, **original)
            row = await sync_to_async(row_for)(resumed)
            assert row.generation == 1
            handle = temporal_env.client.get_workflow_handle(row.workflow_id, run_id=row.workflow_run_id)
            assert await handle.result() == "awaiting_heartbeat"
            calls = len(world.server.calls)
            assert await sync_to_async(install.install_attempt)(row.pk, 0) == "awaiting_heartbeat"
            assert len(world.server.calls) == calls
        else:
            assert outcome == "awaiting_heartbeat"
        monkeypatch.setattr(workflow_client, "start_workflow_once", world.original_start)
        history = await handle.fetch_history()
        started = history.events[0].workflow_execution_started_event_attributes
        decoded = await temporal_env.client.data_converter.decode(started.input.payloads)
        assert decoded == [row.pk, row.generation]
        raw = staged_key(world)
        assert raw.encode() not in history.to_json().encode()
        assert (await sync_to_async(pulse)(world, raw)).status_code == 200
        replay, _ = await sync_to_async(reserve)(world, **original)
        assert replay["data"]["status"] == "SUCCEEDED"


@pytest.mark.parametrize("changed", ["source", "permission", "token", "membership", "role"])
def test_waiting_worker_rechecks_source_and_authority_after_canonical_lock(world, changed, request):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection, connections, transaction

    from astrolift_identity.models import Member

    binding = None
    if changed == "role":
        from core.tests.utils.scope_world import bind_role

        request.getfixturevalue("real_role_resolver")
        world.actor.is_superuser = False
        world.actor.save(update_fields=["is_superuser"])
        binding = bind_role(
            world.actor,
            permissions=[Permission.CLUSTER_MANAGE],
            kind="ORG",
            scope_id=world.org.pk,
            slug="disposable-agent-install-role",
        )
    accepted, _ = reserve(world)
    row = row_for(accepted)
    attempted = threading.Event()

    def worker():
        def observed(execute, sql, params, many, context):
            if "astrolift_clusters_tenantcluster" in sql and "FOR UPDATE" in sql:
                attempted.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(observed):
                return install.install_attempt(row.pk, 0)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            TenantCluster.objects.select_for_update().get(pk=world.cluster.pk)
            pending = pool.submit(worker)
            assert attempted.wait(10), "Installation did not attempt canonical cluster lock"
            if changed == "source":
                world.cluster.auth_config = {"cluster_name": "changed-during-wait"}
                world.cluster.save(update_fields=["auth_config"])
            elif changed == "permission":
                world.permission.deny(Permission.CLUSTER_MANAGE)
            elif changed == "token":
                world.token.is_revoked = True
                world.token.save(update_fields=["is_revoked"])
            elif changed == "role":
                binding.soft_delete()
            else:
                Member.objects.filter(user=world.actor, scope_id=world.org.pk).update(is_active=False)
        assert pending.result(timeout=15) == "refused"
    assert not world.server.calls
    assert pulse(world).status_code == 200


def test_waiting_old_heartbeat_cannot_overwrite_activation_or_receive_jobs(world):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection, connections, transaction

    accepted, _ = reserve(world)
    assert install.install_attempt(row_for(accepted).pk, 0) == "awaiting_heartbeat"
    raw = staged_key(world)
    attempted = threading.Event()

    def old_pulse():
        def observed(execute, sql, params, many, context):
            if "astrolift_clusters_tenantcluster" in sql and "FOR UPDATE" in sql:
                attempted.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(observed):
                return pulse(world, body={"node_count": 999})
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            TenantCluster.objects.select_for_update().get(pk=world.cluster.pk)
            pending = pool.submit(old_pulse)
            assert attempted.wait(10), "Heartbeat did not attempt canonical cluster lock"
            assert pulse(world, raw, body={"node_count": 7}).status_code == 200
        refused = pending.result(timeout=15)
    assert refused.status_code == 401 and "test_job" not in refused.json()
    world.cluster.refresh_from_db()
    assert world.cluster.last_heartbeat_payload == {"node_count": 7}


@pytest.mark.parametrize("changed", ["scopes", "user", "organization", "team", "expiry"])
def test_original_bearer_ceiling_cannot_be_retargeted_or_withdrawn(world, changed):
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_identity.models import Team

    accepted, _ = reserve(world)
    if changed == "scopes":
        world.token.scopes = ["read:clusters"]
    elif changed == "user":
        world.token.user = get_user_model().objects.create_user(username=f"retarget-{uuid4()}")
    elif changed == "organization":
        world.token.organization = Organization.objects.create(name="Foreign", slug=f"foreign-{uuid4()}")
    elif changed == "team":
        world.token.team = Team.objects.create(
            organization=world.org, name="Narrow team", slug=f"team-{uuid4()}"
        )
    else:
        world.token.expires_at = timezone.now() - timedelta(seconds=1)
    world.token.save()
    assert install.install_attempt(row_for(accepted).pk, 0) == "refused"
    assert not world.server.calls
    assert pulse(world).status_code == 200


def test_provider_plugin_partial_aba_and_stale_worker_generation_refuse(world):
    accepted, _ = reserve(world)
    row = row_for(accepted)
    world.plugin.is_enabled = False
    world.plugin.save(update_fields=["is_enabled"])
    world.plugin.is_enabled = True
    world.plugin.save(update_fields=["is_enabled"])
    assert install.install_attempt(row.pk, 0) == "refused"
    assert not world.server.calls
    assert install.install_attempt(row.pk, -1) == "refused"


def test_reserved_secret_name_with_foreign_contents_is_never_overwritten(world):
    accepted, _ = reserve(world)
    row = row_for(accepted)
    world.server.objects[f"/api/v1/namespaces/astrolift-system/secrets/{row.secret_name}"] = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": row.secret_name,
            "namespace": "astrolift-system",
            "uid": "foreign-secret",
            "resourceVersion": "1",
        },
        "type": "Opaque",
        "immutable": True,
        "data": {"agent_key": base64.b64encode(b"foreign").decode()},
    }
    assert install.install_attempt(row.pk, 0) == "refused"
    assert world.server.get_kind("Secret")["metadata"]["uid"] == "foreign-secret"
    assert not any(
        method in {"POST", "PATCH"} and "/secrets" in path for method, path, _ in world.server.calls
    )
    assert pulse(world).status_code == 200


def test_later_deploy_preserves_recorded_secret_and_rejects_replacement(world):
    from core.cluster_management import ClusterManagementError, deploy_agent_dispatch

    accepted, _ = reserve(world)
    assert install.install_attempt(row_for(accepted).pk, 0) == "awaiting_heartbeat"
    assert pulse(world, staged_key(world)).status_code == 200
    world.cluster.refresh_from_db()
    assert deploy_agent_dispatch(cluster=world.cluster).ok
    world.server.get_kind("Secret")["metadata"]["uid"] = str(uuid4())
    before = len(world.server.calls)
    with pytest.raises(ClusterManagementError, match="Recorded agent Secret changed"):
        deploy_agent_dispatch(cluster=world.cluster)
    assert not any(method in {"POST", "PATCH"} for method, _, _ in world.server.calls[before:])


@pytest.mark.parametrize("model", ["cluster", "plugin"])
@pytest.mark.parametrize("partial", [True, False])
def test_stale_source_instances_cannot_reuse_an_observed_review_counter(world, model, partial):
    cls = TenantCluster if model == "cluster" else ProviderPlugin
    target = world.cluster if model == "cluster" else world.plugin
    first = cls.objects.get(pk=target.pk)
    stale = cls.objects.get(pk=target.pk)
    field = "auth_config" if model == "cluster" else "is_enabled"
    original = getattr(first, field)
    setattr(first, field, {"changed": True} if model == "cluster" else False)
    first.save(update_fields=[field] if partial else None)
    reviewed = first.version
    setattr(stale, field, original)
    stale.save(update_fields=[field] if partial else None)
    persisted = cls.objects.get(pk=target.pk)
    assert persisted.version == reviewed + 1
    assert persisted.updated_at > first.updated_at
    if model == "cluster":
        refused, _ = reserve(world, expectedVersion=reviewed)
        assert refused["ok"] is False and not world.server.calls


@pytest.mark.parametrize("model", ["cluster", "plugin"])
def test_concurrent_stale_source_writes_increment_the_persisted_counter(world, model):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections

    cls = TenantCluster if model == "cluster" else ProviderPlugin
    target = world.cluster if model == "cluster" else world.plugin
    first = cls.objects.get(pk=target.pk)
    second = cls.objects.get(pk=target.pk)
    field = "auth_config" if model == "cluster" else "capabilities_manifest"
    first_value, second_value = {"literal": 1}, {"literal": 2}
    barrier = threading.Barrier(2)

    def save(row, value):
        try:
            setattr(row, field, value)
            barrier.wait(timeout=10)
            row.save(update_fields=[field])
            return row.version
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(save, first, first_value), pool.submit(save, second, second_value)
        observed = {a.result(timeout=15), b.result(timeout=15)}
    assert observed == {target.version + 1, target.version + 2}
    assert cls.objects.get(pk=target.pk).version == target.version + 2


def test_telemetry_updates_preserve_both_persisted_source_markers(world):
    before_version, before_time = world.cluster.version, world.cluster.updated_at
    assert (
        pulse(
            world, body={"node_count": 3, "auth_config": {"cluster_name": "injected"}, "is_active": False}
        ).status_code
        == 200
    )
    world.cluster.refresh_from_db()
    assert world.cluster.version == before_version and world.cluster.updated_at == before_time
    assert world.cluster.auth_config == {"cluster_name": "disposable-eks"} and world.cluster.is_active
    assert world.cluster.last_heartbeat_payload == {"node_count": 3}


@pytest.mark.parametrize("write", ["issueClusterAgentKey", "deployClusterAgent"])
def test_legacy_writes_refuse_pending_server_owned_installation(world, write):
    accepted, _ = reserve(world)
    old_hash = world.cluster.agent_key_hash
    mutation = "mutation($id:GUID!){" + write + "(input:{clusterId:$id}){ok errors{code message}}}"
    response = gql(world, mutation, {"id": str(world.cluster.guid)})
    assert response["data"][write]["ok"] is False
    assert response["data"][write]["errors"][0]["code"] == "PRECONDITION"
    world.cluster.refresh_from_db()
    assert world.cluster.agent_key_hash == old_hash and not world.server.calls
    assert row_for(accepted).status == "queued"


def test_periodic_reconcile_skips_pending_installs_without_changing_review(world):
    from astrolift_workflows.activities.cron_deploy import _reconcile_agent_deployments_sync

    accepted, _ = reserve(world)
    version = world.cluster.version
    summary = _reconcile_agent_deployments_sync()
    assert summary.skipped_count >= 1 and summary.reconciled_count == 0
    assert not world.server.calls
    world.cluster.refresh_from_db()
    assert world.cluster.version == version and row_for(accepted).status == "queued"


@pytest.mark.parametrize("change", ["slug", "enabled-aba", "schema", "provider-retarget"])
def test_public_review_tuple_binds_provider_source_before_reservation(world, change):
    reviewed = gql(
        world,
        "query($id:GUID!){astroliftClusterAgentInstallReview(clusterId:$id){version source}}",
        {"id": str(world.cluster.guid)},
    )["data"]["astroliftClusterAgentInstallReview"]
    if change == "slug":
        world.plugin.slug = "different-provider"
        world.plugin.save(update_fields=["slug"])
    elif change == "enabled-aba":
        world.plugin.is_enabled = False
        world.plugin.save(update_fields=["is_enabled"])
        world.plugin.is_enabled = True
        world.plugin.save(update_fields=["is_enabled"])
    elif change == "schema":
        world.plugin.config_schema = {"changed": True}
        world.plugin.save(update_fields=["config_schema"])
    else:
        world.cluster.provider_plugin = ProviderPlugin.objects.create(name="Other", slug="other-disposable")
        world.cluster.save(update_fields=["provider_plugin"])
    refused, _ = reserve(world, expectedVersion=reviewed["version"], expectedSource=reviewed["source"])
    assert refused["ok"] is False
    assert not ClusterAgentInstall.objects.exists() and not world.server.calls


@pytest.mark.parametrize(
    "owner", ["foreign", "missing", "wrong-cluster", "platform", "astrolift-control-plane"]
)
def test_existing_namespace_requires_actual_platform_ownership(world, owner):
    labels = {} if owner == "missing" else {"astrolift.io/managed-by": owner}
    if owner == "wrong-cluster":
        labels = {"astrolift.io/managed-by": "platform", "astrolift.io/cluster-guid": str(uuid4())}
    path = "/api/v1/namespaces/astrolift-system"
    original = {
        "kind": "Namespace",
        "apiVersion": "v1",
        "metadata": {
            "name": "astrolift-system",
            "uid": str(uuid4()),
            "resourceVersion": "1",
            "labels": labels,
        },
    }
    world.server.objects[path] = copy.deepcopy(original)
    accepted, _ = reserve(world)
    outcome = install.install_attempt(row_for(accepted).pk, 0)
    assert outcome == (
        "awaiting_heartbeat" if owner in {"platform", "astrolift-control-plane"} else "refused"
    )
    assert world.server.objects[path] == original
    if outcome == "refused":
        assert not any(method != "GET" for method, _, _ in world.server.calls)


def existing_agent(world):
    """An existing legacy agent and its fixed Secret remain working while staged."""
    from core.cluster_management import build_agent_manifests

    namespace = build_agent_manifests(world.cluster)[0]
    namespace["metadata"].update(uid=str(uuid4()), resourceVersion="1")
    world.server.objects["/api/v1/namespaces/astrolift-system"] = namespace
    previous = next(obj for obj in build_agent_manifests(world.cluster) if obj["kind"] == "Deployment")
    previous["metadata"].update(uid=str(uuid4()), resourceVersion="1")
    world.server.objects["/apis/apps/v1/namespaces/astrolift-system/deployments/astrolift-agent"] = previous
    old_secret = {
        "kind": "Secret",
        "apiVersion": "v1",
        "metadata": {
            "name": "astrolift-agent",
            "namespace": "astrolift-system",
            "uid": str(uuid4()),
            "resourceVersion": "1",
        },
        "type": "Opaque",
        "data": {"agent_key": base64.b64encode(b"previous-disposable-key").decode()},
    }
    world.server.objects["/api/v1/namespaces/astrolift-system/secrets/astrolift-agent"] = old_secret
    return copy.deepcopy(previous), copy.deepcopy(old_secret)


@pytest.mark.parametrize("failure", ["partial", "permission", "source"])
def test_candidate_preserves_working_previous_deployment_until_valid_activation(world, failure):
    previous, old_secret = existing_agent(world)
    accepted, _ = reserve(world)
    row = row_for(accepted)
    if failure == "partial":
        world.server.fail_kind = "Deployment"
        assert install.install_attempt(row.pk, 0) == "uncertain"
    else:
        assert install.install_attempt(row.pk, 0) == "awaiting_heartbeat"
        if failure == "permission":
            world.permission.deny(Permission.CLUSTER_MANAGE)
        else:
            world.cluster.auth_config = {"cluster_name": "changed"}
            world.cluster.save(update_fields=["auth_config"])
        assert pulse(world, staged_key(world)).status_code == 401
    assert (
        world.server.objects["/apis/apps/v1/namespaces/astrolift-system/deployments/astrolift-agent"]
        == previous
    )
    assert world.server.objects["/api/v1/namespaces/astrolift-system/secrets/astrolift-agent"] == old_secret
    assert pulse(world).status_code == 200
    assert not any(
        method in {"PATCH", "DELETE"} and path.endswith("/deployments/astrolift-agent")
        for method, path, _ in world.server.calls
    )


@pytest.mark.parametrize("cleanup", ["confirmed", "outage", "replacement"])
def test_retirement_is_uid_conditional_and_independent_of_activation(world, cleanup):
    previous, old_secret = existing_agent(world)
    accepted, original = reserve(world)
    row = row_for(accepted)
    assert install.install_attempt(row.pk, 0) == "awaiting_heartbeat"
    row.refresh_from_db()
    assert row.previous_deployment_uid == previous["metadata"]["uid"]
    old_path = "/apis/apps/v1/namespaces/astrolift-system/deployments/astrolift-agent"
    if cleanup == "outage":
        world.server.fail_delete = True
    elif cleanup == "replacement":
        world.server.before_delete = lambda: world.server.objects[old_path]["metadata"].update(
            uid="foreign-replacement", resourceVersion="999"
        )
    raw = staged_key(world)
    assert pulse(world, raw).status_code == 200
    assert pulse(world).status_code == 401
    row.refresh_from_db()
    world.cluster.refresh_from_db()
    assert row.status == "succeeded" and row.heartbeat_confirmed_at
    assert world.cluster.agent_deployment_name == row.deployment_name
    assert (
        world.cluster.agent_deployment_uid
        == row.manifest_identities[f"Deployment/{row.deployment_name}"]["uid"]
    )
    assert world.server.objects["/api/v1/namespaces/astrolift-system/secrets/astrolift-agent"] == old_secret
    if cleanup == "confirmed":
        assert row.previous_deployment_retired and old_path not in world.server.objects
    else:
        assert not row.previous_deployment_retired and row.error_code == "RETIREMENT_UNCONFIRMED"
        assert old_path in world.server.objects
        if cleanup == "outage":
            world.server.fail_delete = False
            replay, _ = reserve(world, **original)
            assert replay["data"]["status"] == "SUCCEEDED" and replay["data"]["errorCode"] == ""
            row.refresh_from_db()
            assert row.previous_deployment_retired
        else:
            assert world.server.objects[old_path]["metadata"]["uid"] == "foreign-replacement"


def test_recorded_namespace_replacement_refuses_retry(world):
    accepted, _ = reserve(world)
    row = row_for(accepted)
    world.server.fail_kind = "Deployment"
    assert install.install_attempt(row.pk, 0) == "uncertain"
    world.server.get_kind("Namespace")["metadata"]["uid"] = "foreign-namespace"
    world.server.fail_kind = None
    assert install.install_attempt(row.pk, 0) == "refused"
    assert pulse(world).status_code == 200


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_staged_heartbeat_json_never_activates(world, invalid):
    accepted, _ = reserve(world)
    assert install.install_attempt(row_for(accepted).pk, 0) == "awaiting_heartbeat"
    raw = staged_key(world)
    response = Client().post(
        f"/api/clusters/v1/{world.cluster.guid}/heartbeat/",
        '{"cpu_utilization":' + invalid + "}",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    assert response.status_code == 400 and pulse(world).status_code == 200


@pytest.mark.parametrize("invalid", ["", "unknown-proof", "é" * 64])
def test_missing_or_unknown_provider_review_proof_has_no_effect(world, invalid):
    refused, _ = reserve(world, expectedSource=invalid)
    assert refused["ok"] is False and refused["errors"][0]["code"] == "PRECONDITION"
    assert not world.server.calls and not ClusterAgentInstall.objects.exists()


@pytest.mark.parametrize("admin", [False, True])
def test_shared_cluster_requires_current_platform_operator_and_admin_ceiling(world, admin):
    world.cluster.organization = None
    world.cluster.save(update_fields=["organization"])
    if not admin:
        response = gql(
            world,
            "query($id:GUID!){astroliftClusterAgentInstallReview(clusterId:$id){version source}}",
            {"id": str(world.cluster.guid)},
        )
        assert response.get("errors") and not world.server.calls
        return
    world.token.scopes = ["admin"]
    world.token.save(update_fields=["scopes"])
    accepted, _ = reserve(world)
    world.token.scopes = ["manage:clusters"]
    world.token.save(update_fields=["scopes"])
    assert install.install_attempt(row_for(accepted).pk, 0) == "refused" and not world.server.calls


def test_operation_query_does_not_retarget_another_actor_or_organization(world):
    accepted, _ = reserve(world)
    raw = mint_token()
    other = get_user_model().objects.create_user(username=f"other-install-{uuid4()}", is_superuser=True)
    from astrolift_identity.models import Member

    Member.objects.create(user=other, scope_kind=Member.ScopeKind.ORG, scope_id=world.org.pk)
    ApiToken.objects.create(
        user=other,
        organization=world.org,
        name="Other",
        token_hash=raw.token_hash,
        scopes=["manage:clusters"],
    )
    world.client = Client(HTTP_AUTHORIZATION=f"Bearer {raw.plaintext}")
    result = gql(
        world,
        "query($id:GUID!){astroliftClusterAgentInstall(installId:$id){id status}}",
        {"id": accepted["data"]["id"]},
    )
    assert result.get("errors") and not world.server.calls


def test_withdrawn_original_bearer_releases_lease_without_revival(world):
    previous, _old_secret = existing_agent(world)
    accepted, original = reserve(world)
    old = row_for(accepted)
    assert install.install_attempt(old.pk, 0) == "awaiting_heartbeat"
    rejected_key = staged_key(world)
    world.token.is_revoked = True
    world.token.save(update_fields=["is_revoked"])
    assert pulse(world, rejected_key).status_code == 401
    old.refresh_from_db()
    assert old.status == "refused" and pulse(world).status_code == 200
    assert (
        world.server.objects["/apis/apps/v1/namespaces/astrolift-system/deployments/astrolift-agent"]
        == previous
    )
    fresh = mint_token()
    ApiToken.objects.create(
        user=world.actor,
        organization=world.org,
        name="Replacement",
        token_hash=fresh.token_hash,
        scopes=["manage:clusters"],
    )
    world.client = Client(HTTP_AUTHORIZATION=f"Bearer {fresh.plaintext}")
    replay, _ = reserve(world, **original)
    assert replay["ok"] is False  # the original request tuple is never widened
    replacement, _ = reserve(world)
    assert replacement["ok"] and replacement["data"]["id"] != accepted["data"]["id"]
    assert install.install_attempt(row_for(replacement).pk, 0) == "awaiting_heartbeat"
    assert pulse(world, staged_key(world)).status_code == 200


def test_fresh_review_releases_only_an_invalid_pending_source_lease(world):
    accepted, _ = reserve(world)
    old = row_for(accepted)
    assert install.install_attempt(old.pk, 0) == "awaiting_heartbeat"
    world.cluster.auth_config = {"cluster_name": "changed-and-reviewed"}
    world.cluster.save(update_fields=["auth_config"])
    replacement, _ = reserve(world)
    old.refresh_from_db()
    assert old.status == "refused" and replacement["ok"]
    assert pulse(world).status_code == 200


def test_valid_pending_operation_remains_exclusive_on_fresh_uuid(world):
    accepted, _ = reserve(world)
    refused, _ = reserve(world)
    assert refused["ok"] is False and refused["errors"][0]["code"] == "PRECONDITION"
    assert row_for(accepted).status == "queued" and ClusterAgentInstall.objects.count() == 1
    assert not world.server.calls


def test_concurrent_actor_uuid_cannot_retarget_another_cluster(world, monkeypatch):
    """Different canonical cluster/provider locks still share actor UUID admission."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    plugin = ProviderPlugin.objects.create(slug=f"disposable-{uuid4()}", name="Disposable")
    other = TenantCluster.objects.create(
        organization=world.org,
        provider_plugin=plugin,
        slug=f"other-{uuid4()}",
        name="Disposable other",
        endpoint=world.server.url,
        region="us-west-2",
        auth_method="exec_plugin",
        auth_config={"cluster_name": "disposable-eks"},
    )
    worlds = [world, SimpleNamespace(**{**vars(world), "cluster": other})]
    sources = [
        gql(
            item,
            "query($id:GUID!){astroliftClusterAgentInstallReview(clusterId:$id){source}}",
            {"id": str(item.cluster.guid)},
        )["data"]["astroliftClusterAgentInstallReview"]["source"]
        for item in worlds
    ]
    barrier = threading.Barrier(2)
    encrypt = install.encrypt_at_rest

    def synchronized_encrypt(raw):
        barrier.wait(timeout=10)
        return encrypt(raw)

    monkeypatch.setattr(install, "encrypt_at_rest", synchronized_encrypt)
    request_id = str(uuid4())

    def submit(index):
        close_old_connections()
        try:
            return reserve(worlds[index], requestId=request_id, expectedSource=sources[index])[0]
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, [0, 1]))
    assert sorted(item["ok"] for item in results) == [False, True]
    assert next(item for item in results if not item["ok"])["errors"][0]["code"] == "PRECONDITION"
    assert ClusterAgentInstall.objects.filter(request_id=request_id).count() == 1
    assert not world.server.calls


@pytest.mark.parametrize("phase", ["install", "heartbeat"])
def test_transport_initialization_failure_preserves_current_key_and_masks_diagnostics(
    world, monkeypatch, caplog, phase
):
    result, _ = reserve(world)
    row = row_for(result)
    if phase == "heartbeat":
        assert install.install_attempt(row.pk, 0) == "awaiting_heartbeat"
        raw = staged_key(world)
    diagnostic = "registered-credential-detail-must-not-escape"

    def unavailable(_cluster):
        raise RuntimeError(diagnostic)

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", unavailable)
    if phase == "install":
        assert install.install_attempt(row.pk, 0) == "uncertain"
    else:
        assert pulse(world, raw).status_code == 401
    row.refresh_from_db()
    assert row.status == ("uncertain" if phase == "install" else "awaiting_heartbeat")
    assert row.error_code == ("PROVIDER_UNCERTAIN" if phase == "install" else "HEARTBEAT_UNCONFIRMED")
    assert not row.heartbeat_confirmed_at
    assert diagnostic not in row.error_message and diagnostic not in caplog.text
    assert pulse(world).status_code == 200
