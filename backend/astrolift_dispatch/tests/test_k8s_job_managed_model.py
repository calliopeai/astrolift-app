"""Managed-model wiring in the K8s Job spawner.

When the spec's ``managed_model`` switch is on, ``spawn()`` mints/reuses the
cluster's model workload identity, applies an annotated ServiceAccount, sets
``serviceAccountName`` on the Job, and injects the provider's model env
BEFORE the spec's own env (so a spec override wins). When off, the render is
byte-identical to before. A provider that can't wire it fails the spawn with
one surfaced error and applies nothing.
"""

from __future__ import annotations

from astrolift_dispatch.agent_model import AGENT_MODEL_ROLE_ANNOTATION, AGENT_MODEL_SERVICE_ACCOUNT
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner, _render_agent_job

# ---- fakes -----------------------------------------------------------------


class _ApplyResult:
    ok = True

    def summary(self):  # pragma: no cover - not reached when ok
        return "ok"


class _ManagedDriver:
    """Fake cluster driver carrying both the managed-model surface
    (agent_model_env / ensure_agent_model_identity) and the apply surface."""

    def __init__(self, *, env=None, role="arn:aws:iam::123456789012:role/model", identity_fail=None):
        self.env = env or {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": "us-west-2",
            "ANTHROPIC_MODEL": "sonnet",
            "ANTHROPIC_SMALL_FAST_MODEL": "haiku",
        }
        self.role = role
        self.identity_fail = identity_fail
        self.applied: list[dict] = []

    def ensure_agent_model_identity(self, *, namespace, service_account, provider_config):
        if self.identity_fail is not None:
            raise RuntimeError(self.identity_fail)
        return self.role

    def agent_model_env(self, *, region, provider_config):
        return dict(self.env)

    def ensure_namespace(self, *_a, **_k):
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


class _Spec:
    def __init__(self, *, managed_model=False, env_vars=None, secret_refs=None):
        self.managed_model = managed_model
        self.env_vars = env_vars or {}
        self.secret_refs = secret_refs or []
        self.slug = "claude-managed"
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


class _Cluster:
    provider_config = {"account_id": "123456789012"}
    region = "us-west-2"


def _spawn(monkeypatch, spec, driver):
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    import astrolift_dispatch.brief_injector as bi
    import astrolift_dispatch.snapshot_injector as si

    monkeypatch.setattr(bi, "inject_brief_into_job_spec", lambda m, t: m)
    monkeypatch.setattr(si, "inject_snapshot_into_job_spec", lambda m, t: m)

    spawner = K8sJobSpawner(cluster=_Cluster(), namespace="astrolift-agents-acme")
    return spawner.spawn(_Task(spec=spec)), driver


def _pod_spec(job: dict) -> dict:
    return job["spec"]["template"]["spec"]


# ---- render-level (managed env + SA on the manifest) -----------------------


def test_render_prepends_model_env_and_sets_service_account():
    job = _render_agent_job(
        job_name="agent-task-t1",
        workload=_Workload(),
        namespace="astrolift-agents-acme",
        task=_Task(spec=_Spec(managed_model=True, env_vars={"LOG_LEVEL": "debug"})),
        service_account=AGENT_MODEL_SERVICE_ACCOUNT,
        model_env=[
            {"name": "CLAUDE_CODE_USE_BEDROCK", "value": "1"},
            {"name": "AWS_REGION", "value": "us-west-2"},
        ],
    )
    assert _pod_spec(job)["serviceAccountName"] == AGENT_MODEL_SERVICE_ACCOUNT
    env = _pod_spec(job)["containers"][0]["env"]
    names = [e["name"] for e in env]
    # Model env leads; the spec's own env follows.
    assert names[:2] == ["CLAUDE_CODE_USE_BEDROCK", "AWS_REGION"]
    assert "LOG_LEVEL" in names


def test_render_without_service_account_omits_key():
    job = _render_agent_job(
        job_name="agent-task-t1",
        workload=_Workload(),
        namespace="astrolift-agents-acme",
        task=_Task(spec=_Spec(managed_model=False)),
    )
    assert "serviceAccountName" not in _pod_spec(job)


# ---- full spawn ------------------------------------------------------------


def test_spawn_managed_model_applies_sa_then_job(monkeypatch):
    driver = _ManagedDriver()
    result, driver = _spawn(monkeypatch, _Spec(managed_model=True, env_vars={"LOG_LEVEL": "debug"}), driver)
    assert result.ok, result.error

    kinds = [m["kind"] for m in driver.applied]
    assert kinds == ["ServiceAccount", "Job"]

    sa, job = driver.applied
    assert sa["metadata"]["name"] == AGENT_MODEL_SERVICE_ACCOUNT
    assert (
        sa["metadata"]["annotations"][AGENT_MODEL_ROLE_ANNOTATION] == "arn:aws:iam::123456789012:role/model"
    )

    assert _pod_spec(job)["serviceAccountName"] == AGENT_MODEL_SERVICE_ACCOUNT
    env = {e["name"]: e["value"] for e in _pod_spec(job)["containers"][0]["env"]}
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["ANTHROPIC_MODEL"] == "sonnet"
    assert env["LOG_LEVEL"] == "debug"


def test_spawn_without_managed_model_is_unchanged(monkeypatch):
    driver = _ManagedDriver()
    result, driver = _spawn(monkeypatch, _Spec(managed_model=False, env_vars={"LOG_LEVEL": "debug"}), driver)
    assert result.ok, result.error
    # No ServiceAccount, no injected model env, no serviceAccountName.
    assert [m["kind"] for m in driver.applied] == ["Job"]
    job = driver.applied[0]
    assert "serviceAccountName" not in _pod_spec(job)
    names = [e["name"] for e in _pod_spec(job)["containers"][0]["env"]]
    assert names == ["LOG_LEVEL"]
    assert "CLAUDE_CODE_USE_BEDROCK" not in names


def test_spawn_managed_model_spec_env_overrides_injected(monkeypatch):
    # A spec env var of the same name as an injected one must win — it is
    # placed AFTER the model env, and k8s takes the later duplicate.
    driver = _ManagedDriver()
    result, driver = _spawn(
        monkeypatch,
        _Spec(managed_model=True, env_vars={"AWS_REGION": "eu-west-1"}),
        driver,
    )
    assert result.ok, result.error
    job = driver.applied[-1]
    env = _pod_spec(job)["containers"][0]["env"]
    region_entries = [e for e in env if e["name"] == "AWS_REGION"]
    # Both present; the spec's override is the last occurrence.
    assert [e["value"] for e in region_entries] == ["us-west-2", "eu-west-1"]


def test_spawn_managed_model_provider_failure_surfaces_and_applies_nothing(monkeypatch):
    driver = _ManagedDriver(identity_fail="managed model is not supported on this cluster provider")
    result, driver = _spawn(monkeypatch, _Spec(managed_model=True), driver)
    assert result.ok is False
    assert "not supported" in result.error
    # Failed before any apply — no dangling SA or Job.
    assert driver.applied == []
