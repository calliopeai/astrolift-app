"""Agent tasks reach model providers through the cluster's Zentinelle gateway (#1851).

Real Postgres. Zentinelle's HTTP API is a fake that follows the per-run key
contract (calliopeai/zentinelle#400) and records every request; the cluster
driver and the secret store are recording fakes. The spawner, the stop and
finish paths and the input-wait reservation are the real code.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets as pysecrets
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests
from constance.test import override_config
from django.db import connection as db_connection
from django.test import override_settings
from django.utils import timezone

import core.app_deploy as app_deploy
import core.cluster_management as cluster_management
from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Brief
from astrolift_agents.runtime_catalog import RUNTIME_NAMES
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_dispatch import model_gateway
from astrolift_dispatch.agent_secrets import task_secret_name
from astrolift_dispatch.model_gateway import (
    GATEWAY_KEY_ENV,
    RESERVED_ENV_NAMES,
    RUNTIME_PROVIDERS,
    UNSUPPORTED_RUNTIMES,
)
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner
from astrolift_dispatch.spawners.local_docker import LocalDockerSpawner
from astrolift_identity.models import Organization, Team
from astrolift_operations import zentinelle_connect
from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
from astrolift_registry.models import RegisteredApp, Workload
from core.secrets import encrypt_at_rest

pytestmark = pytest.mark.django_db

BASE = "https://zentinelle.test"
API = "/api/zentinelle/v1/astrolift"
NAMESPACE = "astrolift-agents-gw1851"
TASK_GUID = uuid.UUID("0199a1b2-c3d4-7e5f-8a6b-1c2d3e4f5a6b")
BRIEF_GUID = uuid.UUID("0199a1b2-c3d4-7e5f-8a6b-000000001851")
AGENT_ID = f"astrolift-task-{TASK_GUID.hex}"
GATEWAY = "http://zentinelle-gateway.astrolift-system.svc:8742"
STORE = {
    "agents/claude-dev/anthropic": {"value": "sk-ant-stored-provider-key"},
    "agents/claude-dev/openai": {"value": "sk-openai-stored-provider-key"},
    "agents/claude-dev/github": {"value": "ghp_stored_github_token"},
}


# ---- fakes --------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body
        self.headers: dict[str, str] = {}
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeZentinelle:
    """The per-run agent key endpoints of one Zentinelle (calliopeai/zentinelle#400)."""

    def __init__(self, events: list) -> None:
        self.events = events
        self.calls: list[SimpleNamespace] = []
        self.installs: dict[str, str] = {}
        self.minted: dict[str, str] = {}
        self.revoked: list[str] = []
        self.answers: dict[tuple[str, str], list] = {}

    def install(self, organization) -> str:
        credential = "sk_astroinst_" + pysecrets.token_hex(16)
        self.installs[credential] = organization.slug
        return credential

    def answer(self, method: str, path: str, *responses) -> None:
        """Queue answers for ``method path``, served before the contract applies."""
        self.answers.setdefault((method, API + path), []).extend(responses)

    def paths(self) -> list[tuple[str, str]]:
        return [(call.method, call.path) for call in self.calls]

    def __call__(self, method, url, *, json=None, headers=None, timeout=None, allow_redirects=True):
        headers = dict(headers or {})
        path = url[len(BASE) :]
        token = headers.get("Authorization", "").removeprefix("Bearer ")
        self.calls.append(
            SimpleNamespace(
                method=method,
                path=path,
                json=json,
                install=self.installs.get(token),
                timeout=timeout,
                redirects=allow_redirects,
            )
        )
        self.events.append(("zentinelle", method, path))
        queued = self.answers.get((method, path))
        if queued:
            answer = queued.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        if token not in self.installs:
            return FakeResponse(401, {"detail": "Invalid Astrolift install credential"})
        now = timezone.now()
        if (method, path) == ("POST", f"{API}/agents"):
            key = "sk_agent_" + pysecrets.token_urlsafe(24)
            self.minted[json["agent_id"]] = key
            expires = (now + timedelta(seconds=json["ttl_seconds"])).isoformat()
            return FakeResponse(
                201, {"agent": {"agent_id": json["agent_id"], "expires_at": expires}, "api_key": key}
            )
        if method == "POST" and path.endswith("/renew"):
            expires = (now + timedelta(seconds=json["ttl_seconds"])).isoformat()
            return FakeResponse(200, {"agent": {"expires_at": expires}})
        if method == "DELETE" and path.startswith(f"{API}/agents/"):
            self.revoked.append(path.rsplit("/", 1)[-1])
            return FakeResponse(204)
        return FakeResponse(404)


class _Result:
    def __init__(self, ok=True, detail="apply failed"):
        self.ok = ok
        self._detail = detail

    def summary(self):
        return self._detail


class FakeDriver:
    def __init__(self, events: list) -> None:
        self.events = events
        self.applied: list[list[dict]] = []
        self.deleted: list[list[dict]] = []
        self.apply_error = None

    def ensure_namespace(self, cluster_slug, namespace, labels, annotations):
        pass

    def apply_manifests(self, cluster_slug, namespace, manifests):
        batch = json.loads(json.dumps(manifests))
        self.applied.append(batch)
        self.events.append(("apply", [m["kind"] for m in batch]))
        if self.apply_error is not None:
            return _Result(ok=False, detail=self.apply_error(batch))
        return _Result()

    def delete_manifests(self, cluster_slug, namespace, refs, *, propagation_policy=None):
        self.deleted.append([dict(r) for r in refs])
        self.events.append(("delete", [r["kind"] for r in refs]))
        return _Result()


class FakeSecrets:
    def __init__(self) -> None:
        self.store = json.loads(json.dumps(STORE))
        self.reads: list[str] = []

    def get(self, path):
        self.reads.append(path)
        return self.store.get(path)


# ---- fixtures -----------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture(autouse=True)
def _fixed_callback_key(monkeypatch):
    """The callback credential is random per spawn; fixed here so manifests are comparable."""
    # brief_injector's own reference only: patching the stdlib module would fix
    # every other random value too, the fake Zentinelle's keys among them.
    monkeypatch.setattr(
        "astrolift_dispatch.brief_injector.secrets",
        SimpleNamespace(token_urlsafe=lambda n=32: "fixed-callback"),
    )


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(model_gateway.time, "sleep", lambda seconds: None)


@pytest.fixture
def events() -> list:
    return []


@pytest.fixture
def zentinelle(monkeypatch, events) -> FakeZentinelle:
    fake = FakeZentinelle(events)
    monkeypatch.setattr(zentinelle_connect.requests, "request", fake)
    return fake


@pytest.fixture
def driver(monkeypatch, events) -> FakeDriver:
    fake = FakeDriver(events)
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda cluster: fake)
    monkeypatch.setattr(
        cluster_management, "_context_for_cluster", lambda cluster: SimpleNamespace(slug="kind")
    )
    return fake


@pytest.fixture
def store(monkeypatch) -> FakeSecrets:
    fake = FakeSecrets()
    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda cluster, capability: fake)
    return fake


@pytest.fixture
def logs():
    """Every record at DEBUG and up, including the app loggers that do not propagate."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    loggers = [logging.getLogger()] + [
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger) and not logger.propagate
    ]
    levels = [(logger, logger.level) for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    yield records
    for logger, level in levels:
        logger.removeHandler(handler)
        logger.setLevel(level)


def _org(slug):
    return Organization.objects.create(name=slug.title(), slug=slug)


@pytest.fixture
def org():
    return _org("gw-acme")


@pytest.fixture
def other_org():
    return _org("gw-globex")


@pytest.fixture
def plugin():
    [row] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="k8s", slug="k8s_native", capabilities_manifest={}, config_schema={})]
    )
    return row


def _cluster(plugin, organization, slug="kind"):
    return TenantCluster.objects.create(
        organization=organization,
        slug=f"{slug}-{uuid.uuid4().hex[:6]}",
        name=slug,
        provider_plugin=plugin,
        provider_config={},
        region="local",
        endpoint="https://kind.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


@pytest.fixture
def cluster(plugin, org):
    return _cluster(plugin, org)


def _connect(zentinelle, organization):
    sealed = encrypt_at_rest(zentinelle.install(organization).encode("utf-8"))
    return ZentinelleConnection.objects.create(
        organization=organization,
        base_url=BASE,
        status=ZentinelleConnection.Status.CONNECTED,
        tenant_ids=["tenant-a"],
        credential_backend_kind=sealed.backend_kind,
        credential_ciphertext=sealed.backend_ref,
        connected_at=timezone.now(),
    )


def _register(connection, cluster, *, enabled=True, deployed=True):
    return ZentinelleClusterGateway.objects.create(
        connection=connection,
        cluster=cluster,
        zentinelle_cluster_id=str(cluster.guid),
        gateway_enabled=enabled,
        gateway_deployed=deployed,
    )


@pytest.fixture
def connection(zentinelle, org, cluster):
    connection = _connect(zentinelle, org)
    _register(connection, cluster)
    return connection


def _agent(organization, slug="claude-reviewer"):
    existing = Workload.objects.filter(slug=slug, registered_app__organization=organization).first()
    if existing is not None:
        return existing
    team = Team.objects.create(organization=organization, name=f"T {slug}", slug=f"t-{slug}")
    app = RegisteredApp.objects.create(
        organization=organization,
        team=team,
        name=slug.title(),
        slug=f"app-{organization.slug}-{slug}",
        provisioning_status="ready",
    )
    return Workload.objects.create(registered_app=app, name=slug.title(), slug=slug, kind=Workload.Kind.AGENT)


def _spec(organization, **fields):
    values = {
        "name": "Claude Dev",
        "slug": "claude-dev",
        "agent_type": "claude",
        "runtime": "claude",
        "image_tag": "docker.io/calliopeai/astrolift-agent-claude:1.0",
        "env_vars": {"LOG_LEVEL": "debug"},
        "secret_refs": [
            {"uri": "agents/claude-dev/anthropic", "env_var": "ANTHROPIC_API_KEY"},
            {"uri": "agents/claude-dev/github", "env_var": "GITHUB_TOKEN"},
        ],
    }
    values.update(fields)
    return AgentEnvironmentSpec.objects.create(organization=organization, **values)


def _task(organization, spec, *, guid=TASK_GUID, brief_env=None):
    brief = Brief.objects.create(
        organization=organization,
        guid=BRIEF_GUID if guid == TASK_GUID else uuid.uuid4(),
        content_hash=hashlib.sha256(f"{organization.slug}/{guid}".encode()).hexdigest(),
        status=Brief.Status.READY,
        manifest_snapshot={"system_prompt": "Review the change.", "env_vars": brief_env or {}},
    )
    return AgentTask.objects.create(
        organization=organization,
        agent_definition=_agent(organization),
        environment_spec=spec,
        brief=brief,
        guid=guid,
        timeout_seconds=600,
    )


def _spawn(cluster, task):
    with override_settings(PLATFORM_API_URL="https://astro.example.net"):
        return K8sJobSpawner(cluster, NAMESPACE).spawn(task)


def _job_name(task):
    return f"agent-task-{task.guid.hex}"


def _one(batch, kind):
    [manifest] = [m for m in batch if m["kind"] == kind]
    return manifest


def _env(job):
    return {entry["name"]: entry for entry in job["spec"]["template"]["spec"]["containers"][0]["env"]}


def _stored_in(needle: str) -> list[str]:
    """Every table holding ``needle`` in any column, as text, hex or base64."""
    forms = [needle, needle.encode().hex(), base64.b64encode(needle.encode()).decode()]
    hits = []
    with db_connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        for (table,) in cursor.fetchall():
            for form in forms:
                cursor.execute(f'SELECT 1 FROM "{table}" t WHERE strpos(t::text, %s) > 0 LIMIT 1', [form])
                if cursor.fetchone():
                    hits.append(table)
                    break
    return hits


# ---- the run gets a key, never a provider key ------------------------------


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_a_gateway_task_gets_its_own_key_and_no_provider_key(
    org, cluster, connection, zentinelle, driver, store, logs
):
    spec = _spec(
        org,
        model_gateway=True,
        env_vars={
            "LOG_LEVEL": "debug",
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_CUSTOM_HEADERS": "X-Zentinelle-Key: sk_agent_someone-elses",
        },
    )
    task = _task(org, spec, brief_env={"OPENAI_API_KEY": "sk-openai-in-the-brief", "REPO": "acme/api"})

    result = _spawn(cluster, task)

    assert result.ok, result.error
    [mint] = zentinelle.calls
    assert (mint.method, mint.path, mint.install) == ("POST", f"{API}/agents", org.slug)
    assert mint.json == {
        "agent_id": AGENT_ID,
        "ttl_seconds": 600 + 900,
        "name": f"claude-reviewer task {TASK_GUID}",
        "deployment_id": "claude-reviewer",
    }
    assert mint.redirects is False
    key = zentinelle.minted[AGENT_ID]

    [batch] = driver.applied
    secret = _one(batch, "Secret")
    assert secret["metadata"]["name"] == task_secret_name(_job_name(task))
    assert secret["stringData"] == {GATEWAY_KEY_ENV: key, "GITHUB_TOKEN": "ghp_stored_github_token"}

    job = _one(batch, "Job")
    env = _env(job)
    assert env["ANTHROPIC_BASE_URL"] == {"name": "ANTHROPIC_BASE_URL", "value": GATEWAY}
    assert env[GATEWAY_KEY_ENV] == {
        "name": GATEWAY_KEY_ENV,
        "valueFrom": {"secretKeyRef": {"name": task_secret_name(_job_name(task)), "key": GATEWAY_KEY_ENV}},
    }
    # The claude runtime calls Anthropic only; nothing reserved survives from
    # the spec or the Brief, and what is not reserved is untouched.
    assert set(env) & RESERVED_ENV_NAMES == {"ANTHROPIC_BASE_URL", GATEWAY_KEY_ENV}
    assert env["LOG_LEVEL"]["value"] == "debug"
    assert env["REPO"]["value"] == "acme/api"
    assert env["GITHUB_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "GITHUB_TOKEN"
    assert [e["name"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]][-2:] == [
        "ANTHROPIC_BASE_URL",
        GATEWAY_KEY_ENV,
    ]

    # The provider key was never even read, and appears nowhere.
    assert "agents/claude-dev/anthropic" not in store.reads
    rendered = json.dumps(batch)
    for provider_value in ("sk-ant-stored-provider-key", "sk-openai-in-the-brief", "sk_agent_someone-elses"):
        assert provider_value not in rendered
    # The key is in the Secret and nowhere else: not the Job, the database or a log line.
    assert key not in json.dumps(job)
    assert _stored_in(key) == []
    assert not any(key in record.getMessage() or key in repr(record.__dict__) for record in logs)

    task.refresh_from_db()
    assert task.model_gateway_agent_id == AGENT_ID


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
@pytest.mark.parametrize(
    ("runtime", "agent_type", "expected"),
    [
        ("codex", "codex", {"OPENAI_BASE_URL": f"{GATEWAY}/v1"}),
        ("goose", "claude", {"OPENAI_BASE_URL": f"{GATEWAY}/v1"}),
        ("opencode", "claude", {"ANTHROPIC_BASE_URL": GATEWAY, "OPENAI_BASE_URL": f"{GATEWAY}/v1"}),
        ("", "codex", {"OPENAI_BASE_URL": f"{GATEWAY}/v1"}),
        ("", "claude", {"ANTHROPIC_BASE_URL": GATEWAY}),
    ],
)
def test_only_the_runtimes_providers_are_pointed_at_the_gateway(
    org, cluster, connection, zentinelle, driver, store, runtime, agent_type, expected
):
    spec = _spec(org, model_gateway=True, runtime=runtime, agent_type=agent_type)

    assert _spawn(cluster, _task(org, spec)).ok

    env = _env(_one(driver.applied[0], "Job"))
    base_urls = {name: entry["value"] for name, entry in env.items() if name.endswith("_BASE_URL")}
    assert base_urls == expected


def test_every_catalog_runtime_is_either_wired_or_refused():
    assert set(RUNTIME_PROVIDERS) | set(UNSUPPORTED_RUNTIMES) == set(RUNTIME_NAMES)
    assert not set(RUNTIME_PROVIDERS) & set(UNSUPPORTED_RUNTIMES)


def test_the_provider_keys_the_runners_read_are_reserved():
    # astrolift-agents README, "Environment contract".
    assert {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY", "LLM_API_KEY"} <= (
        RESERVED_ENV_NAMES
    )


# ---- fail closed ------------------------------------------------------------


def _refused(cluster, task, zentinelle, driver, store, *, phrase):
    result = _spawn(cluster, task)
    assert result.ok is False
    assert phrase in result.error
    assert zentinelle.calls == []
    assert driver.applied == []
    assert store.reads == []
    task.refresh_from_db()
    assert task.model_gateway_agent_id == ""


def test_the_install_flag_off_refuses_the_run(org, cluster, connection, zentinelle, driver, store):
    task = _task(org, _spec(org, model_gateway=True))
    _refused(
        cluster, task, zentinelle, driver, store, phrase="off for this install (ZENTINELLE_GATEWAY_ENABLED)"
    )


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_an_organization_without_a_connection_is_refused(org, cluster, zentinelle, driver, store):
    task = _task(org, _spec(org, model_gateway=True))
    _refused(
        cluster,
        task,
        zentinelle,
        driver,
        store,
        phrase=f"organization {org.slug} is not connected to Zentinelle",
    )


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_a_revoked_connection_is_refused(org, cluster, connection, zentinelle, driver, store):
    ZentinelleConnection.objects.filter(pk=connection.pk).update(status=ZentinelleConnection.Status.REVOKED)
    task = _task(org, _spec(org, model_gateway=True))
    _refused(cluster, task, zentinelle, driver, store, phrase="no longer accepts organization")


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
@pytest.mark.parametrize(
    ("change", "phrase"),
    [
        ({"gateway_enabled": False}, "is turned off"),
        ({"gateway_deployed": False}, "is not deployed"),
        ({"deleted_at": timezone.now()}, "runs no Zentinelle gateway"),
    ],
)
def test_a_cluster_whose_gateway_is_not_running_is_refused(
    org, cluster, connection, zentinelle, driver, store, change, phrase
):
    ZentinelleClusterGateway.all_objects.filter(cluster=cluster).update(**change)
    task = _task(org, _spec(org, model_gateway=True))
    _refused(cluster, task, zentinelle, driver, store, phrase=phrase)


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
@pytest.mark.parametrize(
    ("runtime", "phrase"),
    [
        ("openhands", "openhands cannot send the gateway key"),
        ("mistral", "reaches Mistral"),
        ("deepseek", "reaches DeepSeek"),
    ],
)
def test_a_runtime_that_cannot_use_the_gateway_is_refused(
    org, cluster, connection, zentinelle, driver, store, runtime, phrase
):
    task = _task(org, _spec(org, model_gateway=True, runtime=runtime))
    _refused(cluster, task, zentinelle, driver, store, phrase=phrase)


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_managed_model_and_the_gateway_are_exclusive(
    org, cluster, connection, zentinelle, driver, store, monkeypatch
):
    def no_identity(**kwargs):
        raise AssertionError("a refused run must not mint a cloud identity")

    monkeypatch.setattr("astrolift_dispatch.agent_model.resolve_managed_model_wiring", no_identity)
    task = _task(org, _spec(org, model_gateway=True, managed_model=True))
    _refused(cluster, task, zentinelle, driver, store, phrase="does not proxy Bedrock or Vertex")


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
@pytest.mark.parametrize(
    ("answer", "phrase"),
    [
        (FakeResponse(500, {"detail": "database is down"}), "(500: database is down)"),
        (FakeResponse(409, {"error": "agent_id_taken"}), "(409: agent_id_taken)"),
        (FakeResponse(404), "cannot mint per-run agent keys"),
        (FakeResponse(201, {"agent": {}}), "carried no agent key"),
        (FakeResponse(201, {"api_key": "sk-ant-not-an-agent-key"}), "carried no agent key"),
        (requests.ConnectionError("connection refused"), "could not reach Zentinelle"),
        (FakeResponse(302, {}), "with a redirect"),
    ],
)
def test_a_failed_mint_fails_the_run_and_never_falls_back(
    org, cluster, connection, zentinelle, driver, store, answer, phrase
):
    zentinelle.answer("POST", "/agents", answer)
    task = _task(org, _spec(org, model_gateway=True))

    result = _spawn(cluster, task)

    assert result.ok is False
    assert "did not mint the run's gateway key, so it was not started" in result.error
    assert phrase in result.error
    assert driver.applied == []
    # A lost answer may still have minted a key: revoked on the way out.
    assert zentinelle.paths() == [("POST", f"{API}/agents"), ("DELETE", f"{API}/agents/{AGENT_ID}")]


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_a_refused_install_credential_marks_the_connection_revoked(
    org, cluster, connection, zentinelle, driver, store
):
    zentinelle.installs.clear()
    task = _task(org, _spec(org, model_gateway=True))

    result = _spawn(cluster, task)

    assert result.ok is False
    assert "Disconnect, then connect again" in result.error
    assert driver.applied == []
    connection.refresh_from_db()
    assert connection.status == ZentinelleConnection.Status.REVOKED
    assert zentinelle.paths() == [("POST", f"{API}/agents")]


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_a_failed_apply_revokes_the_key_and_keeps_it_out_of_the_error(
    org, cluster, connection, zentinelle, driver, store, logs
):
    driver.apply_error = lambda batch: f"Secret rejected: {json.dumps(_one(batch, 'Secret')['stringData'])}"
    task = _task(org, _spec(org, model_gateway=True))

    result = _spawn(cluster, task)

    key = zentinelle.minted[AGENT_ID]
    assert result.ok is False
    assert key not in result.error
    assert "[redacted]" in result.error
    assert zentinelle.revoked == [AGENT_ID]
    assert not any(key in record.getMessage() for record in logs)


# ---- revoked when the run ends ----------------------------------------------


@pytest.fixture
def spawned(org, cluster, connection, zentinelle, driver, store):
    with override_config(ZENTINELLE_GATEWAY_ENABLED=True):
        task = _task(org, _spec(org, model_gateway=True))
        assert _spawn(cluster, task).ok
    AgentTask.objects.filter(pk=task.pk).update(external_id=_job_name(task))
    task.refresh_from_db()
    zentinelle.calls.clear()
    driver.events.clear()
    return task


def test_stop_kills_the_job_then_revokes_the_key(spawned, cluster, zentinelle, events):
    K8sJobSpawner(cluster, NAMESPACE).stop(spawned.external_id)

    assert events == [
        ("delete", ["Job", "Secret"]),
        ("zentinelle", "DELETE", f"{API}/agents/{AGENT_ID}"),
    ]
    [revoke] = zentinelle.calls
    assert revoke.install == spawned.organization.slug
    assert revoke.timeout == zentinelle_connect.AGENT_KEY_REVOKE_TIMEOUT_SECONDS


def test_finishing_revokes_the_key(spawned, cluster, zentinelle):
    K8sJobSpawner(cluster, NAMESPACE).cleanup_task_secret(spawned.external_id)

    assert zentinelle.revoked == [AGENT_ID]


def test_a_failed_kill_still_revokes_the_key(spawned, cluster, zentinelle, driver):
    def refuse(*args, **kwargs):
        raise RuntimeError("apiserver unreachable")

    driver.delete_manifests = refuse

    with pytest.raises(RuntimeError, match="apiserver unreachable"):
        K8sJobSpawner(cluster, NAMESPACE).stop(spawned.external_id)
    assert zentinelle.revoked == [AGENT_ID]


def test_revocation_retries_and_never_fails_the_stop(spawned, cluster, zentinelle):
    path = f"/agents/{AGENT_ID}"
    zentinelle.answer("DELETE", path, FakeResponse(503), requests.Timeout("slow"))

    K8sJobSpawner(cluster, NAMESPACE).stop(spawned.external_id)

    assert zentinelle.paths() == [("DELETE", API + path)] * 3
    assert zentinelle.revoked == [AGENT_ID]


def test_a_revocation_that_keeps_failing_is_left_to_the_expiry(spawned, cluster, zentinelle, logs):
    path = f"/agents/{AGENT_ID}"
    zentinelle.answer("DELETE", path, *[FakeResponse(500)] * 3)

    K8sJobSpawner(cluster, NAMESPACE).stop(spawned.external_id)

    assert len(zentinelle.calls) == model_gateway.REVOKE_ATTEMPTS
    assert any("stops at its expiry" in record.getMessage() for record in logs)


@pytest.mark.parametrize("status", [404, 401])
def test_an_agent_zentinelle_no_longer_has_counts_as_revoked(spawned, cluster, zentinelle, status):
    zentinelle.answer("DELETE", f"/agents/{AGENT_ID}", FakeResponse(status))

    K8sJobSpawner(cluster, NAMESPACE).stop(spawned.external_id)

    assert len(zentinelle.calls) == 1


def test_a_disconnected_organization_has_nothing_left_to_revoke(spawned, cluster, zentinelle, connection):
    connection.soft_delete()

    K8sJobSpawner(cluster, NAMESPACE).stop(spawned.external_id)

    assert zentinelle.calls == []


# ---- the input-wait deadline carries the key with it -------------------------


def _reserve(task, monkeypatch):
    from astrolift_agents.services import task_target
    from astrolift_agents.services.task_timeout import reserve_input_wait

    reserved = []
    monkeypatch.setattr(
        task_target,
        "spawner_for_task",
        lambda t: SimpleNamespace(reserve_input_wait=lambda t, seconds: reserved.append(seconds)),
    )
    AgentTask.objects.filter(pk=task.pk).update(
        status=AgentTask.Status.RUNNING, started_at=timezone.now(), dispatch_target={"version": 1}
    )
    task.refresh_from_db()
    reserve_input_wait(task, ([SimpleNamespace(request={"id": "q1"})],))
    return reserved


def test_a_question_renews_the_key_to_cover_the_longer_deadline(spawned, zentinelle, monkeypatch):
    from astrolift_agents.services.task_timeout import INPUT_WAIT_BUDGET_SECONDS

    assert _reserve(spawned, monkeypatch) == [INPUT_WAIT_BUDGET_SECONDS]

    [renew] = zentinelle.calls
    assert (renew.method, renew.path) == ("POST", f"{API}/agents/{AGENT_ID}/renew")
    assert renew.json == {"ttl_seconds": 600 + 900 + INPUT_WAIT_BUDGET_SECONDS}
    spawned.refresh_from_db()
    assert spawned.input_wait_budget_seconds == INPUT_WAIT_BUDGET_SECONDS


def test_a_failed_renewal_refuses_the_question(spawned, zentinelle, monkeypatch):
    zentinelle.answer("POST", f"/agents/{AGENT_ID}/renew", FakeResponse(500))

    with pytest.raises(model_gateway.ModelGatewayError, match="did not renew"):
        _reserve(spawned, monkeypatch)
    spawned.refresh_from_db()
    assert spawned.input_wait_budget_seconds == 0


# ---- tenancy ---------------------------------------------------------------------


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_a_gateway_registered_by_another_organization_is_not_used(
    org, other_org, plugin, zentinelle, driver, store
):
    shared = _cluster(plugin, None, slug="shared")
    _connect(zentinelle, org)
    _register(_connect(zentinelle, other_org), shared)
    task = _task(org, _spec(org, model_gateway=True))

    _refused(
        shared,
        task,
        zentinelle,
        driver,
        store,
        phrase="registered through another organization's Zentinelle connection",
    )


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_another_organizations_connection_is_never_used(org, other_org, cluster, zentinelle, driver, store):
    _register(_connect(zentinelle, other_org), cluster)
    task = _task(org, _spec(org, model_gateway=True))

    _refused(cluster, task, zentinelle, driver, store, phrase=f"organization {org.slug} is not connected")


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_each_organizations_keys_are_minted_and_revoked_with_its_own_install(
    org, other_org, plugin, zentinelle, driver, store
):
    clusters = {}
    for organization in (org, other_org):
        clusters[organization.slug] = _cluster(plugin, organization)
        _register(_connect(zentinelle, organization), clusters[organization.slug])
    task_b = _task(other_org, _spec(other_org, model_gateway=True), guid=uuid.uuid4())

    assert _spawn(clusters[other_org.slug], task_b).ok
    K8sJobSpawner(clusters[other_org.slug], NAMESPACE).stop(_job_name(task_b))

    assert [(call.method, call.install) for call in zentinelle.calls] == [
        ("POST", other_org.slug),
        ("DELETE", other_org.slug),
    ]


# ---- opt-out is unchanged -------------------------------------------------------------

#: sha256 of the canonical JSON of the manifests an opt-out task applies in
#: the fixture below, taken at origin/main (bd808822) with the same fixture.
#: Turning the gateway on for the install, connecting the organization and
#: deploying the cluster's gateway must not change a task whose spec does not
#: ask for it. A deliberate change to the task manifests updates this digest
#: and says why.
_OPT_OUT_BATCH_SHA256 = "94d8e2984ada006c73de269e9cbaefbf3c4a4104e394985e2607d2183bf6fa19"


def _canonical_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_a_spec_that_does_not_ask_is_spawned_exactly_as_before(
    org, cluster, connection, zentinelle, driver, store
):
    task = _task(org, _spec(org))

    assert _spawn(cluster, task).ok
    K8sJobSpawner(cluster, NAMESPACE).stop(_job_name(task))
    K8sJobSpawner(cluster, NAMESPACE).cleanup_task_secret(_job_name(task))

    assert zentinelle.calls == []
    [batch] = driver.applied
    assert "agents/claude-dev/anthropic" in store.reads
    assert _one(batch, "Secret")["stringData"]["ANTHROPIC_API_KEY"] == "sk-ant-stored-provider-key"
    assert _canonical_sha256(batch) == _OPT_OUT_BATCH_SHA256


def test_local_docker_refuses_a_gateway_spec(org, monkeypatch):
    def no_docker(*args, **kwargs):
        raise AssertionError("nothing may start without the gateway")

    monkeypatch.setattr("astrolift_dispatch.spawners.local_docker.subprocess.run", no_docker)
    task = _task(org, _spec(org, model_gateway=True))

    result = LocalDockerSpawner().spawn(task)

    assert result.ok is False
    assert "local Docker dispatch cannot use it" in result.error
