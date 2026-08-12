"""The K8s Job spawner must surface the per-dispatch ad-hoc input to the pod
as the ``ASTROLIFT_TRIGGER_PAYLOAD`` env var (#930).

Both agent dispatch entry points accept an input payload — ``runAstroliftAgent``
ad-hoc ``trigger_payload`` and a trigger-bound webhook's ``input_mapping``-shaped
payload — frozen on ``AgentTask.dispatch_input`` at creation. Until #930 the
rendered Job never carried it, so the running agent couldn't read what triggered
it. These pin the last hop into the container:

  dispatch_input set   -> ASTROLIFT_TRIGGER_PAYLOAD (compact JSON) on the pod
  dispatch_input empty -> no such env var (unattended manual/cron/loop dispatch)

``dispatch_input_env_vars`` is pure (no DB); the spawn test drives the full
render+inject path with lightweight stand-ins (mirrors test_k8s_job_namespace).
"""

from __future__ import annotations

import json

import pytest

from astrolift_dispatch.brief_injector import (
    brief_env_vars,
    dispatch_input_env_vars,
    inject_brief_into_job_spec,
)
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner

ENV_NAME = "ASTROLIFT_TRIGGER_PAYLOAD"


# ---- dispatch_input_env_vars (pure) ----------------------------------


class _Task:
    def __init__(self, dispatch_input=None, brief_id=None):
        self.guid = "task-no-brief"
        self.dispatch_input = dispatch_input
        self.brief_id = brief_id
        self.callback_token_hash = ""


def test_env_var_carries_compact_json_payload():
    payload = {"pr": 42, "repo": "acme/x", "nested": {"a": [1, 2]}}
    env = dispatch_input_env_vars(_Task(dispatch_input=payload))

    assert len(env) == 1
    assert env[0]["name"] == ENV_NAME
    # Compact, separators-free JSON and a lossless round-trip.
    assert env[0]["value"] == json.dumps(payload, separators=(",", ":"))
    assert json.loads(env[0]["value"]) == payload


def test_no_env_var_when_dispatch_input_absent():
    assert dispatch_input_env_vars(_Task(dispatch_input=None)) == []
    # An empty mapping is indistinguishable from no payload -> emit nothing
    # (acceptance: empty/no input -> no spurious env var).
    assert dispatch_input_env_vars(_Task(dispatch_input={})) == []


def test_inject_adds_env_var_even_without_a_brief():
    """The dispatch-input env var is not gated on the Brief: a task with an
    ad-hoc input but no Brief must still get ASTROLIFT_TRIGGER_PAYLOAD."""
    job_spec = {"spec": {"template": {"spec": {"containers": [{"name": "agent", "env": []}]}}}}
    out = inject_brief_into_job_spec(job_spec, _Task(dispatch_input={"k": "v"}, brief_id=None))

    env = out["spec"]["template"]["spec"]["containers"][0]["env"]
    names = {e["name"]: e["value"] for e in env}
    assert names[ENV_NAME] == json.dumps({"k": "v"}, separators=(",", ":"))


# ---- full spawn render path ------------------------------------------


class _ApplyResult:
    ok = True

    def summary(self):  # pragma: no cover - not reached when ok
        return "ok"


class _RecordingDriver:
    def __init__(self):
        self.applied: list[dict] = []

    def ensure_namespace(self, cluster, name, labels, annotations):
        return None

    def apply_manifests(self, cluster, namespace, manifests):
        self.applied.extend(manifests)
        return _ApplyResult()


class _Ctx:
    slug = "astrolift-eks"


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


class _SpawnTask:
    guid = "abc12345-0000-0000-0000-000000000000"
    vnc_enabled = False
    environment_spec = None
    agent_definition = _Workload()
    brief_id = None
    brief = None

    def __init__(self, dispatch_input=None):
        self.dispatch_input = dispatch_input


def _spawn_and_capture(monkeypatch, dispatch_input):
    driver = _RecordingDriver()
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    # Leave the snapshot injector as identity — non-VNC task, no blob store.
    import astrolift_dispatch.snapshot_injector as snapshot_injector

    monkeypatch.setattr(snapshot_injector, "inject_snapshot_into_job_spec", lambda m, t: m)

    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-acme")
    result = spawner.spawn(_SpawnTask(dispatch_input=dispatch_input))
    assert result.ok, result.error
    assert driver.applied, "no manifest was applied"
    container = driver.applied[0]["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e["value"] for e in container.get("env", [])}


def test_spawned_job_includes_trigger_payload_env(monkeypatch):
    payload = {"pr": 7, "action": "opened"}
    env = _spawn_and_capture(monkeypatch, payload)
    assert env.get(ENV_NAME) == json.dumps(payload, separators=(",", ":"))


def test_spawned_job_omits_env_for_unattended_dispatch(monkeypatch):
    env = _spawn_and_capture(monkeypatch, None)
    assert ENV_NAME not in env


# ---- brief_env_vars: one-shot (pre-injected) wiring ------------------
#
# The in-pod astrolift_runner runs one-shot only when AGENT_PROMPT is set;
# otherwise it idles in perpetual listener mode and the dispatched agent
# never processes its batch. brief_env_vars must emit AGENT_SYSTEM (the
# assembled brief system prompt) + a non-empty AGENT_PROMPT alongside the
# existing ASTROLIFT_* identity vars.


class _Brief:
    def __init__(self, system_prompt="", env_vars=None):
        self.guid = "brief-1"
        self.content_hash = "deadbeef"
        self.manifest_snapshot = {"system_prompt": system_prompt}
        if env_vars is not None:
            self.manifest_snapshot["env_vars"] = env_vars


class _TaskWithBrief:
    def __init__(self, system_prompt="", dispatch_input=None, env_vars=None):
        self.guid = "task-1"
        self.brief_id = "brief-1"
        self.brief = _Brief(system_prompt=system_prompt, env_vars=env_vars)
        self.dispatch_input = dispatch_input
        self.callback_token_hash = ""


def test_brief_env_sets_oneshot_agent_prompt_and_system():
    env = {e["name"]: e["value"] for e in brief_env_vars(_TaskWithBrief(system_prompt="You triage bugs."))}
    assert env["AGENT_SYSTEM"] == "You triage bugs."
    assert env["AGENT_PROMPT"]  # non-empty -> runner selects pre-injected one-shot
    assert "ASTROLIFT_BRIEF_ID" in env  # existing identity contract preserved
    assert env["AGENT_CALLBACK_URL"].endswith("/api/dispatch/v1/agents/task-1/callback/")
    assert env["ASTROLIFT_CLUSTER_KEY"].startswith("alft_cb_")


def test_kickoff_prompt_threads_trigger_payload():
    env = {
        e["name"]: e["value"]
        for e in brief_env_vars(_TaskWithBrief(dispatch_input={"mode": "backfill", "batches": 3}))
    }
    assert "backfill" in env["AGENT_PROMPT"]
    assert '"batches":3' in env["AGENT_PROMPT"]


def test_no_brief_no_env():
    task = _Task(dispatch_input=None, brief_id=None)
    env = {entry["name"]: entry["value"] for entry in brief_env_vars(task)}
    assert env["AGENT_CALLBACK_URL"].endswith("/api/dispatch/v1/agents/task-no-brief/callback/")
    assert "AGENT_PROMPT" not in env


def test_callback_url_requires_absolute_platform_url(settings):
    settings.PLATFORM_API_URL = ""

    with pytest.raises(RuntimeError, match="PLATFORM_API_URL"):
        brief_env_vars(_Task(dispatch_input=None, brief_id=None))


def test_manifest_environment_vars_are_emitted():
    # The manifest [environment] non-secret keys (assembled into
    # manifest_snapshot["env_vars"]) must reach the container — the agent's tools
    # read them at runtime (e.g. EMR_SERVICE_BASE_URL). Without this the agent
    # boots with its brief but no service config.
    env = {
        e["name"]: e["value"]
        for e in brief_env_vars(
            _TaskWithBrief(
                system_prompt="triage",
                env_vars={
                    "EMR_SERVICE_BASE_URL": "https://emr-internal.example",
                    "MAX_TICKETS_PER_RUN": 10,
                    "UNSET": None,
                },
            )
        )
    }
    assert env["EMR_SERVICE_BASE_URL"] == "https://emr-internal.example"
    assert env["MAX_TICKETS_PER_RUN"] == "10"  # coerced to string for K8s
    assert env["UNSET"] == ""  # None -> empty string, never the literal "None"
    # identity + one-shot contract still intact alongside the config vars
    assert env["AGENT_SYSTEM"] == "triage" and env["AGENT_PROMPT"]


def test_manifest_environment_cannot_override_dispatcher_callback_or_auth():
    entries = brief_env_vars(
        _TaskWithBrief(
            system_prompt="triage",
            env_vars={
                "AGENT_CALLBACK_URL": "https://attacker.invalid/collect",
                "ASTROLIFT_CLUSTER_KEY": "attacker-controlled",
                "SAFE_USER_VALUE": "kept",
            },
        )
    )
    by_name = {entry["name"]: entry["value"] for entry in entries}

    assert by_name["AGENT_CALLBACK_URL"].endswith("/api/dispatch/v1/agents/task-1/callback/")
    assert by_name["ASTROLIFT_CLUSTER_KEY"].startswith("alft_cb_")
    assert by_name["SAFE_USER_VALUE"] == "kept"
    assert [entry["name"] for entry in entries].count("AGENT_CALLBACK_URL") == 1


def test_no_manifest_env_vars_when_absent():
    # A brief without an env_vars snapshot key emits only the identity + one-shot
    # vars — no crash, no stray keys.
    env = {e["name"]: e["value"] for e in brief_env_vars(_TaskWithBrief(system_prompt="x"))}
    assert "AGENT_SYSTEM" in env and "ASTROLIFT_BRIEF_ID" in env
