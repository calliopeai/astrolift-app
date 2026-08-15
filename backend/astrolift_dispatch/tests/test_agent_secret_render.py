"""Agent secret resolution + K8s materialization in the dispatch path (#1173).

The env-spec's ``env_vars`` land on the pod as plain env; its
``secret_refs`` are resolved from the install's secret store at spawn and
materialized into a per-task K8s Secret the pod mounts via ``secretKeyRef``
(values never touch the pod spec). Preflight collects EVERY missing/empty
ref and fails the spawn with one readable error.

Pure helpers are exercised directly; the full spawn drives the render +
resolve + apply path with lightweight stand-ins (mirrors
test_dispatch_input_env / test_k8s_job_namespace).
"""

from __future__ import annotations

import pytest

from astrolift_dispatch.agent_secrets import (
    AgentSecretResolutionError,
    build_task_secret_manifest,
    normalize_secret_refs,
    resolve_task_secret_manifest,
    task_secret_name,
)
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner, _render_agent_job

# ---- fakes -----------------------------------------------------------------


class _FakeSecrets:
    """In-memory SecretsBackend stand-in. ``store`` maps uri -> {"value": v}."""

    def __init__(self, store=None, *, get_raises_for=None):
        self.store = dict(store or {})
        self.upserts: list[tuple[str, dict]] = []
        self.deletes: list[str] = []
        self._get_raises_for = set(get_raises_for or ())

    def get(self, path):
        if path in self._get_raises_for:
            raise RuntimeError(f"boom reading {path}")
        return self.store.get(path)

    def upsert(self, path, kvs):
        self.upserts.append((path, dict(kvs)))
        self.store[path] = dict(kvs)

    def delete(self, path):
        self.deletes.append(path)
        self.store.pop(path, None)


class _PrimaryQS:
    def __init__(self, container):
        self._c = container

    def filter(self, **_):
        return self

    def first(self):
        return self._c


class _Container:
    image_ref = "docker.io/library/busybox:latest"
    port = 0
    command = []
    args = []


class _Workload:
    containers = _PrimaryQS(_Container())


class _Spec:
    def __init__(self, *, env_vars=None, secret_refs=None, slug="claude-dev"):
        self.env_vars = env_vars or {}
        self.secret_refs = secret_refs or []
        self.slug = slug
        self.image_tag = "docker.io/calliopeai/agent:pin"
        self.runtime = ""


class _Task:
    def __init__(self, *, spec=None, guid="abc12345-0000-0000-0000-000000000000"):
        self.guid = guid
        self.vnc_enabled = False
        self.environment_spec = spec
        self.agent_definition = _Workload()
        self.brief_id = None
        self.brief = None
        self.dispatch_input = None


# ---- normalize_secret_refs -------------------------------------------------


def test_normalize_skips_malformed_and_trims():
    refs = normalize_secret_refs(
        [
            {"uri": " sm:a ", "env_var": " TOKEN_A "},
            {"uri": "", "env_var": "NO_URI"},
            {"env_var": "NO_URI2"},
            "not-a-dict",
            {"uri": "sm:b", "env_var": "TOKEN_B"},
        ]
    )
    assert refs == [
        {"uri": "sm:a", "env_var": "TOKEN_A"},
        {"uri": "sm:b", "env_var": "TOKEN_B"},
    ]


# ---- _render_agent_job env ------------------------------------------------


def _container_env(spec):
    job = _render_agent_job(
        job_name="agent-task-t1",
        workload=_Workload(),
        namespace="astrolift-agents-acme",
        task=_Task(spec=spec),
    )
    return job["spec"]["template"]["spec"]["containers"][0]["env"]


def test_render_injects_plain_env_vars():
    env = _container_env(_Spec(env_vars={"LOG_LEVEL": "debug", "REGION": "us-west-2"}))
    by_name = {e["name"]: e for e in env}
    assert by_name["LOG_LEVEL"]["value"] == "debug"
    assert by_name["REGION"]["value"] == "us-west-2"
    # A plain env var carries no valueFrom.
    assert "valueFrom" not in by_name["LOG_LEVEL"]


def test_render_ignores_plain_env_that_shadows_dispatcher_control():
    env = _container_env(
        _Spec(env_vars={"AGENT_CALLBACK_URL": "https://attacker.invalid", "SAFE_USER_VALUE": "kept"})
    )
    by_name = {entry["name"]: entry for entry in env}

    assert "AGENT_CALLBACK_URL" not in by_name
    assert by_name["SAFE_USER_VALUE"]["value"] == "kept"


def test_render_emits_secret_key_refs_pointing_at_per_task_secret():
    env = _container_env(_Spec(secret_refs=[{"uri": "sm:gh", "env_var": "GITHUB_TOKEN"}]))
    by_name = {e["name"]: e for e in env}
    ref = by_name["GITHUB_TOKEN"]["valueFrom"]["secretKeyRef"]
    assert ref["name"] == task_secret_name("agent-task-t1")
    assert ref["key"] == "GITHUB_TOKEN"
    # The reference names a value but never carries one.
    assert "value" not in by_name["GITHUB_TOKEN"]


def test_render_no_spec_leaves_env_empty():
    job = _render_agent_job(
        job_name="agent-task-t1",
        workload=_Workload(),
        namespace="astrolift-agents-acme",
        task=_Task(spec=None),
    )
    assert job["spec"]["template"]["spec"]["containers"][0]["env"] == []


# ---- build_task_secret_manifest -------------------------------------------


def test_secret_manifest_shape_and_labels():
    manifest = build_task_secret_manifest(
        secret_name="agent-task-t1-secrets",
        namespace="astrolift-agents-acme",
        task_guid="guid-1",
        resolved={"GITHUB_TOKEN": "ghp_x"},
    )
    assert manifest["kind"] == "Secret"
    assert manifest["metadata"]["name"] == "agent-task-t1-secrets"
    assert manifest["metadata"]["namespace"] == "astrolift-agents-acme"
    assert manifest["metadata"]["labels"]["astrolift.dev/task-id"] == "guid-1"
    assert manifest["metadata"]["labels"]["astrolift.dev/managed-by"] == "astrolift-agents"
    # Values live in stringData (K8s base64-encodes), keyed by env var.
    assert manifest["stringData"] == {"GITHUB_TOKEN": "ghp_x"}


# ---- resolve_task_secret_manifest (preflight) -----------------------------


def _patch_backend(monkeypatch, backend):
    import core.app_deploy as app_deploy

    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda _c, _cap: backend)


def test_resolve_none_when_no_refs(monkeypatch):
    # No refs → no backend call, no manifest.
    assert (
        resolve_task_secret_manifest(
            cluster=object(),
            spec=_Spec(secret_refs=[]),
            secret_name="agent-task-t1-secrets",
            namespace="ns",
            task_guid="g",
        )
        is None
    )


def test_resolve_materializes_all_present(monkeypatch):
    backend = _FakeSecrets({"sm:a": {"value": "AAA"}, "sm:b": {"value": "BBB"}})
    _patch_backend(monkeypatch, backend)
    manifest = resolve_task_secret_manifest(
        cluster=object(),
        spec=_Spec(
            secret_refs=[
                {"uri": "sm:a", "env_var": "TOKEN_A"},
                {"uri": "sm:b", "env_var": "TOKEN_B"},
            ]
        ),
        secret_name="agent-task-t1-secrets",
        namespace="ns",
        task_guid="g",
    )
    assert manifest["stringData"] == {"TOKEN_A": "AAA", "TOKEN_B": "BBB"}


def test_resolve_materializes_selected_bundle_field(monkeypatch):
    backend = _FakeSecrets(
        {
            "managed/object": {
                "accessKey": "ACCESS",
                "secretKey": "SECRET",
            },
        },
    )
    _patch_backend(monkeypatch, backend)

    manifest = resolve_task_secret_manifest(
        cluster=object(),
        spec=_Spec(
            secret_refs=[
                {
                    "uri": "managed/object#secretKey",
                    "env_var": "AWS_SECRET_ACCESS_KEY",
                },
            ],
        ),
        secret_name="agent-task-t1-secrets",
        namespace="ns",
        task_guid="g",
    )

    assert manifest["stringData"] == {"AWS_SECRET_ACCESS_KEY": "SECRET"}


def test_resolve_lists_every_missing_ref(monkeypatch):
    # sm:a present; sm:b absent; sm:c empty; sm:d read raises — the error
    # must list ALL three unresolvable ones (b, c, d), not just the first.
    backend = _FakeSecrets(
        {"sm:a": {"value": "AAA"}, "sm:c": {"value": ""}},
        get_raises_for=("sm:d",),
    )
    _patch_backend(monkeypatch, backend)
    with pytest.raises(AgentSecretResolutionError) as ei:
        resolve_task_secret_manifest(
            cluster=object(),
            spec=_Spec(
                slug="claude-dev",
                secret_refs=[
                    {"uri": "sm:a", "env_var": "TOKEN_A"},
                    {"uri": "sm:b", "env_var": "TOKEN_B"},
                    {"uri": "sm:c", "env_var": "TOKEN_C"},
                    {"uri": "sm:d", "env_var": "TOKEN_D"},
                ],
            ),
            secret_name="s",
            namespace="ns",
            task_guid="g",
        )
    msg = str(ei.value)
    assert "TOKEN_B" in msg and "TOKEN_C" in msg and "TOKEN_D" in msg
    assert "TOKEN_A" not in msg
    assert ei.value.spec_slug == "claude-dev"


def test_resolve_no_secrets_driver_lists_all(monkeypatch):
    import core.app_deploy as app_deploy

    def _raise(_c, _cap):
        raise app_deploy.AppDeployError("plugin registers no 'secrets' driver")

    monkeypatch.setattr(app_deploy, "driver_for_capability", _raise)
    with pytest.raises(AgentSecretResolutionError) as ei:
        resolve_task_secret_manifest(
            cluster=object(),
            spec=_Spec(secret_refs=[{"uri": "sm:a", "env_var": "TOKEN_A"}]),
            secret_name="s",
            namespace="ns",
            task_guid="g",
        )
    assert "TOKEN_A" in str(ei.value)


# ---- full spawn path ------------------------------------------------------


class _ApplyResult:
    ok = True

    def summary(self):  # pragma: no cover
        return "ok"


class _RecordingDriver:
    def __init__(self):
        self.applied: list[dict] = []

    def ensure_namespace(self, *_a, **_k):
        return None

    def apply_manifests(self, cluster, namespace, manifests):
        self.applied.extend(manifests)
        return _ApplyResult()


class _Ctx:
    slug = "astrolift-eks"


def _spawn(monkeypatch, spec, backend):
    driver = _RecordingDriver()
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    import astrolift_dispatch.snapshot_injector as si

    monkeypatch.setattr(si, "inject_snapshot_into_job_spec", lambda m, t: m)
    _patch_backend(monkeypatch, backend)

    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-acme")
    result = spawner.spawn(_Task(spec=spec))
    return result, driver


def test_spawn_applies_secret_then_job_with_resolved_values(monkeypatch):
    spec = _Spec(
        env_vars={"LOG_LEVEL": "debug"},
        secret_refs=[{"uri": "sm:gh", "env_var": "GITHUB_TOKEN"}],
    )
    backend = _FakeSecrets({"sm:gh": {"value": "ghp_secret"}})
    result, driver = _spawn(monkeypatch, spec, backend)

    assert result.ok, result.error
    # Secret applied before the Job.
    kinds = [m["kind"] for m in driver.applied]
    assert kinds == ["Secret", "Job"]
    secret, job = driver.applied
    assert secret["stringData"] == {"GITHUB_TOKEN": "ghp_secret"}

    env = {e["name"]: e for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["LOG_LEVEL"]["value"] == "debug"
    assert env["GITHUB_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "agent-task-abc123450000-secrets"
    # The Job manifest never carries the plaintext value.
    assert "ghp_secret" not in str(job)


def test_spawn_without_refs_applies_job_only(monkeypatch):
    result, driver = _spawn(monkeypatch, _Spec(env_vars={"X": "1"}), _FakeSecrets())
    assert result.ok, result.error
    assert [m["kind"] for m in driver.applied] == ["Job"]


def test_spawn_fails_when_ref_missing_and_applies_nothing(monkeypatch):
    spec = _Spec(secret_refs=[{"uri": "sm:gh", "env_var": "GITHUB_TOKEN"}])
    result, driver = _spawn(monkeypatch, spec, _FakeSecrets())  # empty store
    assert result.ok is False
    assert "GITHUB_TOKEN" in result.error
    # Preflight failed before any apply — no dangling Secret or Job.
    assert driver.applied == []
