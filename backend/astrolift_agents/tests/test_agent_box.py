"""The agent-box, end to end (#128).

``_sdk/agent_session.py`` already has its own tests for the keep-alive script.
These are about the half that was missing: that the platform actually builds a
pod out of it, that the env-spec secret packet reaches that pod, that a
forgotten box stops costing money, and that none of it is reachable across
tenants.

Where a claim can be checked for real it is: the idle-timeout test runs the
script *taken out of the rendered manifest* against a real tmux server, so it
proves the pod spec the platform emits reaps itself — not that a shell script
somewhere would.

Resolvers are exercised by direct invocation (the agents-test convention) with
the controllable permission resolver and a bound tenant context. The cluster,
its driver and its secret store are in-memory fakes; the database is real.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest
from _sdk.agent_session import SESSION_NAME, keepalive_script
from _sdk.k8s_naming import agent_namespace

from astrolift_agents.models import AgentBox, AgentEnvironmentSpec
from astrolift_agents.schema.mutations import AgentsMutation, EnsureAgentBoxInput
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.services import agent_box as box_service
from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp, Workload
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db

HAS_TMUX = shutil.which("tmux") is not None
tmux_required = pytest.mark.skipif(not HAS_TMUX, reason="tmux not installed")


# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------


@pytest.fixture
def info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org():
    return Organization.objects.create(name="Box Org", slug="box-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Box Other", slug="box-other")


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


class _ApplyResult:
    def __init__(self, ok=True, detail="apply failed"):
        self.ok = ok
        self._detail = detail

    def summary(self):
        return self._detail


class _FakeDriver:
    """Records what the platform tried to put on / take off the cluster."""

    #: What the apiserver returns in ``status`` for a box Job whose pod is
    #: up, verbatim from prd. There is no ``readyReplicas`` and the spec has
    #: no ``replicas`` because a Job has neither; a fake that invented them
    #: would go green against the bug in #133, which is what the old one did.
    RUNNING_JOB_STATUS = {
        "active": 1,
        "ready": 1,
        "startTime": "2026-08-19T01:10:49Z",
        "terminating": 0,
    }

    #: The same Job before the kubelet has started its pod.
    PENDING_JOB_STATUS = {"startTime": "2026-08-19T01:10:47Z"}

    def __init__(self, *, apply_ok=True, job_conditions=None, job_missing=False, job_status=None):
        self.applied: list[list[dict]] = []
        self.deleted: list[list[dict]] = []
        self.delete_policies: list[str | None] = []
        self.namespaces: list[str] = []
        self._apply_ok = apply_ok
        self._job_conditions = job_conditions or []
        self._job_missing = job_missing
        self.job_status = dict(self.RUNNING_JOB_STATUS if job_status is None else job_status)

    def ensure_namespace(self, cluster_slug, namespace, labels, annotations):
        self.namespaces.append(namespace)

    def apply_manifests(self, cluster_slug, namespace, manifests):
        self.applied.append([dict(m) for m in manifests])
        return _ApplyResult(ok=self._apply_ok)

    def delete_manifests(self, cluster_slug, namespace, refs, *, propagation_policy=None):
        self.deleted.append([dict(r) for r in refs])
        self.delete_policies.append(propagation_policy)
        return _ApplyResult(ok=True)

    def get_workload_status(self, cluster_slug, namespace, kind, name):
        if self._job_missing:
            raise RuntimeError("job not found")
        # Projected by the real SDK helper off a real Job object, so the
        # reaper here reads exactly what it reads against a cluster.
        from _sdk.cluster import workload_status_from_object

        return workload_status_from_object(
            kind,
            name,
            namespace,
            {
                "spec": {"backoffLimit": 0, "completions": 1, "parallelism": 1},
                "status": {
                    **self.job_status,
                    "conditions": list(self._job_conditions),
                },
            },
        )


class _FakeSecrets:
    def __init__(self, store=None):
        self.store = dict(store or {})

    def get(self, path):
        return self.store.get(path)

    def upsert(self, path, kvs):
        self.store[path] = dict(kvs)

    def delete(self, path):
        self.store.pop(path, None)


@pytest.fixture
def cluster(monkeypatch):
    """Wire the box runtime to an in-memory cluster + secret store.

    Returns the driver and the secret backend so a test can seed the store
    and inspect exactly which manifests were applied.
    """
    driver = _FakeDriver()
    secrets = _FakeSecrets()

    import astrolift_agents.services.agent_cluster as agent_cluster
    import core.app_deploy as app_deploy
    import core.cluster_management as cluster_management

    # Carries the auth-shaped attributes too, because the pod lookup that
    # stamps ``pod_name`` builds a real ``ClusterAuth`` out of the row.
    fake_cluster = SimpleNamespace(
        slug="fake-cluster",
        auth_method="kubeconfig",
        auth_config={},
        endpoint="https://k8s.example.net",
        ca_cert="",
        default_namespace_prefix="",
        is_active=True,
    )
    monkeypatch.setattr(agent_cluster, "resolve_agent_cluster", lambda _org: fake_cluster)
    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda _c, _cap: secrets)
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda _c: driver)
    monkeypatch.setattr(
        cluster_management,
        "_context_for_cluster",
        lambda _c: SimpleNamespace(slug="fake-cluster"),
    )
    return SimpleNamespace(driver=driver, secrets=secrets, swap_driver=None)


def _spec(org, *, slug="claude-dev", refs=None, env_vars=None, image="agent-claude:1"):
    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Claude Dev",
        slug=slug,
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        image_tag=image,
        secret_refs=refs or [],
        env_vars=env_vars or {},
    )


def _persistent_agent(org, *, slug="claude-box", run_mode=Workload.RunMode.PERSISTENT):
    team = Team.objects.create(organization=org, name=f"T-{slug}", slug=f"t-{slug}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name=slug.title(),
        slug=f"app-{slug}",
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name=slug.title(),
        slug=slug,
        kind=Workload.Kind.AGENT,
        run_mode=run_mode,
    )


def _box(org, **kwargs):
    defaults = {
        "name": "A Box",
        "slug": "a-box",
        "status": AgentBox.Status.PENDING,
    }
    defaults.update(kwargs)
    return AgentBox.objects.create(organization=org, **defaults)


def _ensure(info, org, with_tenant_org, **input_kwargs):
    with with_tenant_org(org):
        return AgentsMutation().ensure_agent_box(
            info,
            input=EnsureAgentBoxInput(**input_kwargs),
            org_id=str(org.guid),
        )


def _job_of(applied_batch):
    return next(m for m in applied_batch if m["kind"] == "Job")


def _container_of(job):
    return job["spec"]["template"]["spec"]["containers"][0]


def _env_of(job):
    return {e["name"]: e for e in _container_of(job)["env"]}


@pytest.mark.parametrize("with_agent", [False, True])
def test_explicit_image_reaches_the_box_job_and_survives_restart(org, cluster, with_agent):
    from astrolift_registry.models import Container

    image = "docker.io/calliopeai/astrolift-agent-claude-code:explicit"
    agent = _persistent_agent(org) if with_agent else None
    if agent:
        Container.objects.create(workload=agent, name="app", is_primary=True, image_ref="agent-default:old")
    kwargs = {"organization": org, "image": image, "agent_slug": agent.slug if agent else ""}
    first = box_service.ensure_agent_box(**kwargs)
    first.refresh_from_db()
    assert first.image == image
    assert first.environment_spec_id is None
    assert _container_of(_job_of(cluster.driver.applied[-1]))["image"] == image
    applied = len(cluster.driver.applied)
    assert box_service.ensure_agent_box(**kwargs).pk == first.pk
    assert len(cluster.driver.applied) == applied

    box_service.stop_agent_box(first)
    restarted = box_service.ensure_agent_box(**kwargs)
    assert restarted.pk == first.pk
    assert _container_of(_job_of(cluster.driver.applied[-1]))["image"] == image


def test_agent_box_restart_without_override_uses_current_workload_image(org, cluster):
    from astrolift_registry.models import Container

    agent = _persistent_agent(org)
    container = Container.objects.create(
        workload=agent, name="app", is_primary=True, image_ref="agent-default:old"
    )
    first = box_service.ensure_agent_box(organization=org, agent_slug=agent.slug)
    box_service.stop_agent_box(first)
    container.image_ref = "agent-default:new"
    container.save(update_fields=["image_ref", "updated_at", "version"])
    restarted = box_service.ensure_agent_box(organization=org, agent_slug=agent.slug)
    assert restarted.pk == first.pk
    assert _container_of(_job_of(cluster.driver.applied[-1]))["image"] == "agent-default:new"


def test_spec_box_restart_uses_current_spec_image(org, cluster):
    spec = _spec(org, image="agent-spec:old")
    first = box_service.ensure_agent_box(organization=org, environment_spec_slug=spec.slug)
    box_service.stop_agent_box(first)
    spec.image_tag = "agent-spec:new"
    spec.save(update_fields=["image_tag", "updated_at", "version"])
    restarted = box_service.ensure_agent_box(organization=org, environment_spec_slug=spec.slug)
    assert restarted.pk == first.pk
    assert _container_of(_job_of(cluster.driver.applied[-1]))["image"] == "agent-spec:new"


# ---------------------------------------------------------------------------
# The pod spec holds the container open
# ---------------------------------------------------------------------------


def test_the_rendered_container_comes_from_the_session_sdk(org):
    """The point of the ticket: the manifest builder calls ``container_spec``.

    Pinning the args against ``keepalive_script`` rather than against a copy
    of the text is what makes this a wiring test — a manifest that grew its
    own entrypoint would fail here.
    """
    from _sdk.agent_session import SessionSpec

    box = _box(org, idle_timeout_seconds=120, environment_spec=_spec(org))
    job = box_service.render_agent_box_job(
        box=box,
        image="agent-claude:1",
        namespace="ns",
        job_name="agent-box-abc",
    )
    container = _container_of(job)

    expected = keepalive_script(
        SessionSpec(image="agent-claude:1", session_name=SESSION_NAME, idle_timeout_seconds=120)
    )
    assert container["command"] == ["/bin/sh", "-lc"]
    assert container["args"] == [expected]
    # tmux refuses to attach without a terminal on the other end.
    assert container["stdin"] is True
    assert container["tty"] is True


def test_the_box_pod_is_not_restarted_when_the_session_ends(org):
    """A Deployment would undo the idle timeout.

    The keep-alive loop exiting has to be the end of the pod. Under a
    restarting controller the reaped box would come straight back and burn
    the node forever while looking healthy.
    """
    box = _box(org, environment_spec=_spec(org))
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-abc")

    assert job["kind"] == "Job"
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    assert job["spec"]["backoffLimit"] == 0


def test_the_box_carries_the_agent_sandbox_baseline(org):
    """A box runs the same untrusted code as a task, for longer (#1848)."""
    box = _box(org, environment_spec=_spec(org))
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-abc")
    pod = job["spec"]["template"]["spec"]
    container = _container_of(job)

    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["resources"]["limits"] == {"cpu": "4", "memory": "8Gi"}
    # The tmux attach path still needs a terminal.
    assert container["stdin"] is True
    assert container["tty"] is True


def test_a_non_root_spec_runs_the_box_as_the_agent_user(org):
    """#1855: the box honours the spec's mode like a task does."""
    from astrolift_dispatch.pod_hardening import AGENT_UID

    spec = _spec(org)
    spec.run_as_non_root = True
    spec.save()
    box = _box(org, environment_spec=spec)
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-abc")

    assert job["spec"]["template"]["spec"]["securityContext"]["runAsUser"] == AGENT_UID
    assert _container_of(job)["securityContext"]["capabilities"] == {"drop": ["ALL"]}


def test_a_non_root_box_that_asks_to_install_is_refused_before_the_cluster(org):
    from astrolift_dispatch.pod_hardening import NON_ROOT_INSTALL_CONFLICT

    spec = _spec(org)
    spec.run_as_non_root = True
    spec.allow_install = True
    spec.save()
    box = _box(org, environment_spec=spec)

    with pytest.raises(box_service.AgentBoxError, match="non-root"):
        box_service.start_agent_box(box)

    box.refresh_from_db()
    assert box.status == AgentBox.Status.FAILED
    assert NON_ROOT_INSTALL_CONFLICT in box.last_error


def test_the_box_starts_in_a_writable_workspace(org):
    """No image creates /workspace; without a volume a non-root box would
    start in a root-owned directory it cannot write."""
    box = _box(org, environment_spec=_spec(org))
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-abc")
    container = _container_of(job)

    assert container["volumeMounts"] == [{"name": "workspace", "mountPath": container["workingDir"]}]
    assert job["spec"]["template"]["spec"]["volumes"] == [{"name": "workspace", "emptyDir": {}}]


def test_the_box_carries_no_wall_clock_deadline(org):
    """A box is bounded by not being used, not by elapsed time.

    ``activeDeadlineSeconds`` is the agent-task cap; on a box it would kill a
    session someone was in the middle of.
    """
    box = _box(org, environment_spec=_spec(org))
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-abc")

    assert "activeDeadlineSeconds" not in job["spec"]


def test_the_platforms_own_env_cannot_be_shadowed_by_a_spec(org):
    """An env spec that sets a box variable must not win.

    The box's identity env is how anything inside the pod knows which box it
    is; letting operator config overwrite it would be a quiet lie.
    """
    spec = _spec(
        org,
        env_vars={"ASTROLIFT_AGENT_BOX": "0", "ASTROLIFT_AGENT_TMUX_REGISTRY": "/wrong", "MY_VAR": "keep"},
    )
    box = _box(org, environment_spec=spec)
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-abc")
    env = _env_of(job)

    assert env["ASTROLIFT_AGENT_BOX"]["value"] == "1"
    assert env["ASTROLIFT_AGENT_BOX_GUID"]["value"] == str(box.guid)
    assert env["MY_VAR"]["value"] == "keep"
    assert env["ASTROLIFT_AGENT_TMUX_REGISTRY"]["value"] == "/tmp/astrolift-agent-terminals"


def test_the_namespace_is_bounded_for_a_maximum_length_org_slug():
    """Never an f-string. Organization slugs allow 200 characters and a
    namespace allows 63 (#1379), so interpolating would emit an invalid
    namespace for a perfectly valid org."""
    long_org = Organization.objects.create(name="Long", slug="o" * 200)
    box = _box(long_org, slug="long-box")

    namespace = box_service.box_namespace(box)

    assert namespace == agent_namespace(long_org.slug)
    assert len(namespace) <= 63


# ---------------------------------------------------------------------------
# The idle timeout actually fires
# ---------------------------------------------------------------------------


@tmux_required
def test_the_rendered_pod_reaps_itself_when_idle(org, monkeypatch, tmp_path):
    """Run the manifest's own entrypoint and watch it end.

    Not a re-test of the SDK: the script here is pulled out of the Job the
    platform would apply, so this fails if the render ever stops threading
    the box's idle timeout into the container that gets deployed.
    """
    box = _box(org, idle_timeout_seconds=1, environment_spec=_spec(org))
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-idle")
    script = _container_of(job)["args"][0]

    monkeypatch.setenv("ASTROLIFT_AGENT_TMUX_REGISTRY", str(tmp_path / "terminals"))
    socket = "astrobox-render-idle"
    proc = subprocess.Popen(
        ["/bin/sh", "-c", script.replace("tmux ", f"tmux -L {socket} ")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # POLL_SECONDS is 5, so one idle detection cycle is comfortably
        # inside 30s; anything longer means the loop never fired.
        assert proc.wait(timeout=30) is not None
        still_there = subprocess.run(
            ["tmux", "-L", socket, "has-session", "-t", SESSION_NAME],
            capture_output=True,
            check=False,
        )
        assert still_there.returncode != 0
    finally:
        if proc.poll() is None:
            proc.kill()
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True, check=False)


@tmux_required
def test_a_never_reap_box_stays_up(org, monkeypatch, tmp_path):
    """The explicit opt-out has to survive the same render path."""
    box = _box(org, idle_timeout_seconds=0, environment_spec=_spec(org))
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-never")
    script = _container_of(job)["args"][0]

    monkeypatch.setenv("ASTROLIFT_AGENT_TMUX_REGISTRY", str(tmp_path / "terminals"))
    socket = "astrobox-render-never"
    proc = subprocess.Popen(
        ["/bin/sh", "-c", script.replace("tmux ", f"tmux -L {socket} ")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(8)
        assert proc.poll() is None
    finally:
        proc.kill()
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True, check=False)


def test_a_healthy_job_backed_box_reaches_running(org, cluster, pod_backend):
    """#133. The box was up, tmux was holding the session, and the row sat
    at ``provisioning`` indefinitely.

    ``observe_box`` decided RUNNING off ``ready_replicas`` / ``desired_replicas``,
    which a Job does not report — it reports ``active`` / ``ready``. Both
    mapped to 0, ``0 or 0`` was falsy, and the sweep returned "leave it
    alone" on every pass. ``astro box ls`` misreported, ``pod_name`` was
    never stamped because that write lives in this branch, and
    ``astro box attach`` waited out its five-minute timeout against a box
    that had been attachable the whole time.
    """
    pod_backend(_BoxPodBackend())
    box = _box(
        org,
        status=AgentBox.Status.PROVISIONING,
        external_id="agent-box-01a0179226c370ac",
        namespace="astrolift-agents-box-org",
    )
    assert cluster.driver.job_status == _FakeDriver.RUNNING_JOB_STATUS

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["running"] == 1
    assert box.status == AgentBox.Status.RUNNING.value
    assert box.pod_name == "agent-box-abc123-x9k2p"


def test_a_box_whose_pod_has_not_started_stays_provisioning(org, cluster, pod_backend):
    """The same lie pointing the other way.

    A Job exists before its pod does, and its ``parallelism`` is 1 from
    that instant. Reading "one pod wanted" as "one pod up" would report a
    box attachable while nothing was listening, so a client would dial a
    relay that is not there.
    """
    backend = pod_backend(_BoxPodBackend())
    cluster.driver.job_status = dict(_FakeDriver.PENDING_JOB_STATUS)
    box = _box(
        org,
        status=AgentBox.Status.PROVISIONING,
        external_id="agent-box-cold",
        namespace="astrolift-agents-box-org",
    )

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["running"] == 0
    assert box.status == AgentBox.Status.PROVISIONING.value
    assert box.pod_name == ""
    # Nothing was claimed about the box, so nothing was spent looking.
    assert backend.calls == []


def test_the_reaper_settles_a_box_whose_pod_has_gone(org, cluster, monkeypatch):
    """The control-plane half. The pod frees the node; this notices.

    A box left claiming RUNNING after its pod completed is the platform
    lying about what an operator can attach to, and it strands the
    plaintext-bearing Secret the pod was handed.
    """
    box = _box(
        org,
        status=AgentBox.Status.RUNNING,
        external_id="agent-box-gone",
        namespace="astrolift-agents-box-org",
        environment_spec=_spec(org),
    )
    cluster.driver._job_conditions = [{"type": "Complete", "status": "True"}]

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["expired"] == 1
    assert box.status == AgentBox.Status.EXPIRED.value
    assert box.ended_at is not None
    deleted_kinds = {ref["kind"] for batch in cluster.driver.deleted for ref in batch}
    assert deleted_kinds == {"Job", "Secret"}
    assert cluster.driver.delete_policies == ["Foreground"]


def test_the_reaper_leaves_a_box_it_cannot_observe_alone(org, cluster):
    """An unreachable cluster is not a death certificate."""
    box = _box(
        org,
        status=AgentBox.Status.RUNNING,
        external_id="agent-box-unknown",
        environment_spec=_spec(org),
    )
    cluster.driver._job_missing = True

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["errors"] == 1
    assert box.status == AgentBox.Status.RUNNING.value
    assert cluster.driver.deleted == []


# ---------------------------------------------------------------------------
# pod_name — the field that was declared, projected and never written (#129)
# ---------------------------------------------------------------------------


class _BoxPodBackend:
    def __init__(self, pods=None, *, explode=False):
        self.calls: list[dict] = []
        self._pods = list(pods if pods is not None else [_pod_info("agent-box-abc123-x9k2p")])
        self._explode = explode

    def list_pods(self, *, auth, namespace, app_slug):  # noqa: ARG002
        self.calls.append({"namespace": namespace, "app_slug": app_slug})
        if self._explode:
            raise RuntimeError("connection refused")
        return list(self._pods)


def _pod_info(name, *, ready=True):
    from datetime import UTC, datetime

    from _sdk.cluster import PodInfo

    return PodInfo(
        name=name,
        workload="agent-box",
        status="Running" if ready else "Terminating",
        phase="Running",
        ready=ready,
        restarts=0,
        age=datetime.now(UTC),
        node="ip-10-0-0-12",
        container_statuses=[],
    )


@pytest.fixture
def pod_backend():
    from core.cluster_observability import (
        reset_pod_backend_for_tests,
        set_pod_backend_for_tests,
    )

    def _install(backend):
        set_pod_backend_for_tests(backend)
        return backend

    yield _install
    reset_pod_backend_for_tests()


def test_the_reaper_stamps_the_running_boxs_pod_name(org, cluster, pod_backend):
    """``pod_name`` was declared, projected onto the GraphQL type and
    written by nothing, so ``astro box ls`` rendered a blank column and
    every attach fell through pod resolution even for a box that had
    been warm for an hour. The reaper is already reading this box's Job;
    the pod it finds is the answer.
    """
    backend = pod_backend(_BoxPodBackend())
    box = _box(
        org,
        status=AgentBox.Status.PROVISIONING,
        external_id="agent-box-warm",
        namespace="astrolift-agents-box-org",
    )

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["running"] == 1
    assert box.status == AgentBox.Status.RUNNING.value
    assert box.pod_name == "agent-box-abc123-x9k2p"
    # Keyed on the guid in the frozen namespace, matching the label the
    # box render sets and the selector the attach path resolves with.
    assert backend.calls == [
        {"namespace": "astrolift-agents-box-org", "app_slug": str(box.guid)},
    ]


def test_a_replaced_pod_re_stamps_on_the_next_sweep(org, cluster, pod_backend):
    """The stamp is an observation, not a fact frozen at spawn — a name
    that outlived its pod is worse than a blank one, because a client
    would dial it."""
    pod_backend(_BoxPodBackend([_pod_info("agent-box-second-pod")]))
    box = _box(
        org,
        status=AgentBox.Status.RUNNING,
        external_id="agent-box-warm",
        namespace="astrolift-agents-box-org",
        pod_name="agent-box-first-pod",
    )

    box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert box.pod_name == "agent-box-second-pod"


def test_a_terminating_leftover_never_wins_the_stamp(org, cluster, pod_backend):
    pod_backend(
        _BoxPodBackend(
            [
                _pod_info("agent-box-dying", ready=False),
                _pod_info("agent-box-serving"),
            ]
        )
    )
    box = _box(
        org,
        status=AgentBox.Status.PROVISIONING,
        external_id="agent-box-warm",
        namespace="astrolift-agents-box-org",
    )

    box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert box.pod_name == "agent-box-serving"


def test_a_failed_pod_lookup_is_not_a_verdict_about_the_box(org, cluster, pod_backend):
    """Same rule the rest of the sweep follows: an observability failure
    says nothing about whether the box is up. It must not block the
    RUNNING transition and must not invent a name."""
    pod_backend(_BoxPodBackend(explode=True))
    box = _box(
        org,
        status=AgentBox.Status.PROVISIONING,
        external_id="agent-box-warm",
        namespace="astrolift-agents-box-org",
    )

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["running"] == 1
    assert box.status == AgentBox.Status.RUNNING.value
    assert box.pod_name == ""


def test_restarting_a_settled_box_clears_the_dead_pod_name(org, cluster):
    """A settled box restarts under its existing slug, so the row it
    reuses still carries the pod from its previous incarnation. Left
    there, it is a dead pod a client would dial until the next sweep."""
    box = _box(
        org,
        status=AgentBox.Status.EXPIRED,
        external_id="agent-box-warm",
        namespace="astrolift-agents-box-org",
        pod_name="agent-box-from-last-time",
        environment_spec=_spec(org),
    )

    box_service.start_agent_box(box)

    box.refresh_from_db()
    assert box.status == AgentBox.Status.PROVISIONING.value
    assert box.pod_name == ""


# ---------------------------------------------------------------------------
# last_error on the FAILED path — the box's own cause of death (#131)
# ---------------------------------------------------------------------------


def _dead_pod_info(name="agent-box-dead-x1", *, reason="Error", exit_code=127):
    """A pod whose only container has terminated, the shape the reaper
    reads off a box Job that ran and died."""
    from datetime import UTC, datetime

    from _sdk.cluster import ContainerStatusInfo, PodInfo

    return PodInfo(
        name=name,
        workload="agent-box",
        status="Error",
        phase="Failed",
        ready=False,
        restarts=0,
        age=datetime.now(UTC),
        node="ip-10-0-0-12",
        container_statuses=[
            ContainerStatusInfo(
                name="agent",
                ready=False,
                restart_count=0,
                image="ghcr.io/acme/agent-claude-code:latest",
                state="terminated",
                terminated_reason=reason,
                terminated_exit_code=exit_code,
            )
        ],
    )


class _BoxLogBackend:
    def __init__(self, lines, *, explode=False):
        self.calls: list[dict] = []
        self._lines = list(lines)
        self._explode = explode

    async def stream(self, *, auth, namespace, pod_name, container, tail_lines, follow):  # noqa: ARG002
        self.calls.append({"pod_name": pod_name, "tail_lines": tail_lines, "follow": follow})
        if self._explode:
            raise RuntimeError("kubelet said no")
        from datetime import UTC, datetime

        from _sdk.cluster import PodLogLine

        # Honour ``tail_lines`` the way the kubelet does — the cap has to
        # bite at the driver, not after the whole stream is in memory.
        for message in self._lines[-tail_lines:] if tail_lines else self._lines:
            yield PodLogLine(
                pod_name=pod_name,
                container=container or "agent",
                timestamp=datetime.now(UTC),
                message=message,
                stream="stdout",
            )


@pytest.fixture
def log_backend():
    from core.cluster_observability import (
        reset_log_backend_for_tests,
        set_log_backend_for_tests,
    )

    def _install(backend):
        set_log_backend_for_tests(backend)
        return backend

    yield _install
    reset_log_backend_for_tests()


def _failed_box(org):
    return _box(
        org,
        status=AgentBox.Status.RUNNING,
        external_id="agent-box-dead",
        namespace="astrolift-agents-box-org",
        environment_spec=_spec(org),
    )


def test_a_failed_box_records_the_reason_the_exit_code_and_the_log_line(
    org, cluster, pod_backend, log_backend
):
    """The ticket, exactly. A box that came up and then hit
    ``tmux: not found`` settled FAILED with an empty ``last_error``, so
    the one line that explained it was only reachable by ``kubectl``.

    The assertion is on the content, not on the field being non-empty:
    the terminated reason, the exit code that identifies it, and the
    sentence the container actually printed.
    """
    pods = pod_backend(_BoxPodBackend([_dead_pod_info()]))
    logs = log_backend(_BoxLogBackend(["+ tmux new-session", "/bin/sh: 2: tmux: not found"]))
    box = _failed_box(org)
    cluster.driver._job_conditions = [{"type": "Failed", "status": "True"}]

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["failed"] == 1
    assert box.status == AgentBox.Status.FAILED.value
    assert "/bin/sh: 2: tmux: not found" in box.last_error
    assert "Error" in box.last_error
    assert "exit 127" in box.last_error
    # Read off the pod the sweep found, in the namespace frozen on the
    # row, and bounded at the driver rather than after the fact.
    assert pods.calls == [{"namespace": "astrolift-agents-box-org", "app_slug": str(box.guid)}]
    assert logs.calls == [
        {
            "pod_name": "agent-box-dead-x1",
            "tail_lines": box_service.BOX_FAILURE_LOG_LINES,
            "follow": False,
        }
    ]


def test_a_failed_log_read_still_records_the_reason_it_does_know(org, cluster, pod_backend, log_backend):
    """An observability failure is not a verdict and not an excuse for
    recording nothing. The terminated reason and exit code came off the
    pod read that already succeeded; losing the log tail must not lose
    them too."""
    pod_backend(_BoxPodBackend([_dead_pod_info(reason="OOMKilled", exit_code=137)]))
    log_backend(_BoxLogBackend([], explode=True))
    box = _failed_box(org)
    cluster.driver._job_conditions = [{"type": "Failed", "status": "True"}]

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["failed"] == 1
    assert summary["errors"] == 0
    assert box.status == AgentBox.Status.FAILED.value
    assert "OOMKilled" in box.last_error
    assert "exit 137" in box.last_error


def test_an_unreadable_pod_still_settles_the_box_and_says_so(org, cluster, pod_backend):
    """The floor of the degradation ladder. Nothing about the pod is
    readable, so the row says the cause could not be read — which is a
    different fact from 'it failed for no reason' and points the
    operator at the cluster rather than at the box."""
    pod_backend(_BoxPodBackend(explode=True))
    box = _failed_box(org)
    cluster.driver._job_conditions = [{"type": "Failed", "status": "True"}]

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["failed"] == 1
    assert box.status == AgentBox.Status.FAILED.value
    assert "could not be read" in box.last_error


def test_a_chatty_box_cannot_turn_last_error_into_a_log_archive(org, cluster, pod_backend, log_backend):
    """The field is rendered inline in ``astro box ls``; an unbounded
    tail is both a database problem and an unreadable one. The excerpt
    is capped and keeps its *tail*, because a container that dies on
    startup says why with the last thing it prints."""
    noise = [f"chatter line {i} " + "x" * 200 for i in range(200)]
    pod_backend(_BoxPodBackend([_dead_pod_info()]))
    log_backend(_BoxLogBackend([*noise, "/bin/sh: 2: tmux: not found"]))
    box = _failed_box(org)
    cluster.driver._job_conditions = [{"type": "Failed", "status": "True"}]

    box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert len(box.last_error) <= box_service.LAST_ERROR_MAX_CHARS
    # The cap kept the end of the output, so the death line survived and
    # the first of the chatter did not.
    assert box.last_error.endswith("/bin/sh: 2: tmux: not found")
    assert "chatter line 0 " not in box.last_error


def test_a_teardown_failure_is_recorded_alongside_the_cause_not_instead_of_it(
    org, cluster, pod_backend, log_backend, monkeypatch
):
    """Two independent facts: the box died of X, and its Job is still on
    the cluster. Before #131 the teardown result was the only one that
    could reach the row."""
    pod_backend(_BoxPodBackend([_dead_pod_info()]))
    log_backend(_BoxLogBackend(["/bin/sh: 2: tmux: not found"]))
    box = _failed_box(org)
    cluster.driver._job_conditions = [{"type": "Failed", "status": "True"}]

    def _explode(*_args, **_kwargs):
        raise RuntimeError("apiserver unreachable")

    monkeypatch.setattr(box_service, "_delete_box_objects", _explode)

    box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert "/bin/sh: 2: tmux: not found" in box.last_error
    assert "cluster teardown did not complete" in box.last_error


def test_an_expired_box_is_not_billed_for_a_post_mortem(org, cluster, pod_backend, log_backend):
    """The idle timeout firing is the normal case, not a failure, and it
    is the common one. The reaper must not spend a pod read and a log
    read on every box that reaps itself, nor write a cause onto a row
    that has none."""
    pods = pod_backend(_BoxPodBackend([_dead_pod_info()]))
    logs = log_backend(_BoxLogBackend(["irrelevant"]))
    box = _failed_box(org)
    cluster.driver._job_conditions = [{"type": "Complete", "status": "True"}]

    summary = box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert summary["expired"] == 1
    assert box.status == AgentBox.Status.EXPIRED.value
    assert box.last_error == ""
    assert pods.calls == []
    assert logs.calls == []


def test_the_recorded_cause_reaches_the_api_surface(org, cluster, pod_backend, log_backend):
    """``last_error`` is already projected onto ``AgentBoxType``, so the
    CLI's ``lastError=`` column and the Boxes tab pick this up with no
    further wiring. Asserted rather than assumed — the projection is the
    only reason this fix is visible to anyone."""
    from astrolift_agents.schema.types import agent_box_to_type

    pod_backend(_BoxPodBackend([_dead_pod_info()]))
    log_backend(_BoxLogBackend(["/bin/sh: 2: tmux: not found"]))
    box = _failed_box(org)
    cluster.driver._job_conditions = [{"type": "Failed", "status": "True"}]

    box_service.reap_agent_boxes()

    box.refresh_from_db()
    assert "/bin/sh: 2: tmux: not found" in agent_box_to_type(box).last_error


# ---------------------------------------------------------------------------
# The secret packet reaches the pod
# ---------------------------------------------------------------------------


def test_the_env_spec_secret_packet_lands_on_the_box(
    permission_resolver, info, org, with_tenant_org, cluster
):
    """ANTHROPIC_API_KEY, the whole reason the box is useful.

    The value belongs in a Secret applied alongside the Job; the pod spec
    gets a reference. A value in the manifest would be readable by anyone
    who can describe the Job.
    """
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    cluster.secrets.store["sm:anthropic"] = {"value": "sk-live-key"}
    _spec(org, refs=[{"uri": "sm:anthropic", "env_var": "ANTHROPIC_API_KEY"}])

    result = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")

    assert result.ok is True, result.errors
    applied = cluster.driver.applied[0]
    secret = next(m for m in applied if m["kind"] == "Secret")
    job = _job_of(applied)

    assert secret["stringData"]["ANTHROPIC_API_KEY"] == "sk-live-key"
    ref = _env_of(job)["ANTHROPIC_API_KEY"]["valueFrom"]["secretKeyRef"]
    assert ref["name"] == secret["metadata"]["name"]
    assert ref["key"] == "ANTHROPIC_API_KEY"
    assert "sk-live-key" not in str(job)


def test_a_box_whose_key_is_missing_refuses_to_start(
    permission_resolver, info, org, with_tenant_org, cluster
):
    """Better a failed button than a warm box that dies on first prompt."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org, refs=[{"uri": "sm:anthropic", "env_var": "ANTHROPIC_API_KEY"}])

    result = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")

    assert result.ok is False
    assert "ANTHROPIC_API_KEY" in result.errors[0].message
    assert cluster.driver.applied == []
    assert AgentBox.objects.get(organization=org).status == AgentBox.Status.FAILED.value


# ---------------------------------------------------------------------------
# ensureAgentBox — the button
# ---------------------------------------------------------------------------


def test_ensure_starts_a_box_and_returns_how_to_attach(
    permission_resolver, info, org, with_tenant_org, cluster
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)

    result = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")

    assert result.ok is True, result.errors
    assert result.data.status == AgentBox.Status.PROVISIONING.value
    assert result.data.attach_command == ["tmux", "new-session", "-A", "-s", "astrolift"]
    assert result.data.namespace == agent_namespace(org.slug)
    assert cluster.driver.namespaces == [agent_namespace(org.slug)]


def test_pressing_the_button_twice_attaches_rather_than_spending_a_second_node(
    permission_resolver, info, org, with_tenant_org, cluster
):
    """Ensure semantics. The IDE re-clicks; the org must not grow boxes."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)

    first = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")
    second = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")

    assert first.data.slug == second.data.slug
    assert AgentBox.objects.filter(organization=org).count() == 1
    # Only the first press applied anything to the cluster.
    assert len(cluster.driver.applied) == 1


def test_re_ensuring_a_reaped_box_restarts_it_under_the_same_address(
    permission_resolver, info, org, with_tenant_org, cluster
):
    """The slug is what an IDE remembers. Idle-reaping must not invalidate it."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)
    first = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")
    box = AgentBox.objects.get(slug=first.data.slug)
    box.status = AgentBox.Status.EXPIRED
    box.save()

    second = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")

    assert second.ok is True, second.errors
    assert second.data.slug == first.data.slug
    assert second.data.status == AgentBox.Status.PROVISIONING.value
    assert AgentBox.objects.filter(organization=org).count() == 1
    assert len(cluster.driver.applied) == 2


def test_re_ensuring_with_a_name_renames_the_box_it_returns(
    permission_resolver, info, org, with_tenant_org, cluster
):
    """``--name`` on a settled box used to be accepted and dropped.

    Every other field the caller can influence is re-pointed before the
    restart; ``name`` was left out of the same block, so the box came back
    under its old label and nothing said why.
    """
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)
    first = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev", name="Old label")
    box = AgentBox.objects.get(slug=first.data.slug)
    box.status = AgentBox.Status.EXPIRED
    box.save()

    second = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev", name="New label")

    assert second.ok is True, second.errors
    assert second.data.slug == first.data.slug
    assert second.data.name == "New label"


def test_re_ensuring_without_a_name_keeps_the_one_the_box_has(
    permission_resolver, info, org, with_tenant_org, cluster
):
    """``name`` is blank on most presses, so re-pointing it unconditionally
    would wipe the operator's label on the next restart."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)
    first = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev", name="Keep me")
    box = AgentBox.objects.get(slug=first.data.slug)
    box.status = AgentBox.Status.EXPIRED
    box.save()

    second = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")

    assert second.ok is True, second.errors
    assert second.data.name == "Keep me"


def test_ensure_is_denied_without_the_dispatch_grant(
    permission_resolver, info, org, with_tenant_org, cluster
):
    """Deny-by-default, and the gate runs before anything is created."""
    _spec(org)

    result = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert AgentBox.objects.count() == 0
    assert cluster.driver.applied == []


def test_ensure_needs_something_to_run(permission_resolver, info, org, with_tenant_org, cluster):
    permission_resolver.grant(Permission.AGENT_DISPATCH)

    result = _ensure(info, org, with_tenant_org)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert AgentBox.objects.count() == 0


def test_a_batch_agent_is_not_silently_turned_into_a_box(
    permission_resolver, info, org, with_tenant_org, cluster
):
    """The run mode is the gate.

    Boxing a ``once`` agent behind the operator's back would change what
    they configured into something that holds a node.
    """
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)
    _persistent_agent(org, slug="batch-bot", run_mode=Workload.RunMode.ONCE)

    result = _ensure(info, org, with_tenant_org, agent_slug="batch-bot", environment_spec_slug="claude-dev")

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert "persistent" in result.errors[0].message
    assert AgentBox.objects.count() == 0


def test_a_persistent_agent_backs_a_box(permission_resolver, info, org, with_tenant_org, cluster):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)
    workload = _persistent_agent(org, slug="claude-box")

    result = _ensure(info, org, with_tenant_org, agent_slug="claude-box", environment_spec_slug="claude-dev")

    assert result.ok is True, result.errors
    assert result.data.agent_slug == workload.slug


def test_a_negative_idle_timeout_is_refused_and_points_at_the_sentinel(
    permission_resolver, info, org, with_tenant_org, cluster
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)

    result = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev", idle_timeout_seconds=-1)

    assert result.ok is False
    assert "0" in result.errors[0].message
    assert AgentBox.objects.count() == 0


# ---------------------------------------------------------------------------
# destroyAgentBox
# ---------------------------------------------------------------------------


def test_destroy_kills_the_pod_and_soft_deletes_the_row(
    permission_resolver, info, org, with_tenant_org, cluster
):
    """Soft delete, per the platform rule — the row is history, not garbage."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)
    created = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")

    with with_tenant_org(org):
        result = AgentsMutation().destroy_agent_box(info, slug=created.data.slug)

    assert result.ok is True, result.errors
    assert not AgentBox.objects.filter(slug=created.data.slug).exists()
    row = AgentBox.all_objects.get(slug=created.data.slug)
    assert row.deleted_at is not None
    assert row.status == AgentBox.Status.STOPPED.value
    deleted_kinds = {ref["kind"] for batch in cluster.driver.deleted for ref in batch}
    assert deleted_kinds == {"Job", "Secret"}


@pytest.mark.parametrize("raises", [True, False], ids=["provider-exception", "provider-rejection"])
def test_destroy_failure_keeps_the_box_visible_and_can_be_retried(
    permission_resolver, info, org, with_tenant_org, cluster, monkeypatch, raises
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    box = _box(org, status=AgentBox.Status.RUNNING, external_id="agent-box-retry")
    delete = cluster.driver.delete_manifests

    def fail_delete(*args, **kwargs):
        if raises:
            raise RuntimeError("provider refused teardown")
        return _ApplyResult(ok=False, detail="provider refused teardown")

    monkeypatch.setattr(cluster.driver, "delete_manifests", fail_delete)
    with with_tenant_org(org):
        result = AgentsMutation().destroy_agent_box(info, slug=box.slug)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert "provider refused teardown" in result.errors[0].message
    box.refresh_from_db()
    assert AgentBox.objects.filter(pk=box.pk).exists()
    assert box.deleted_at is None
    assert box.ended_at is None
    assert box.status == AgentBox.Status.RUNNING.value
    assert "provider refused teardown" in box.last_error

    monkeypatch.setattr(cluster.driver, "delete_manifests", delete)
    with with_tenant_org(org):
        retried = AgentsMutation().destroy_agent_box(info, slug=box.slug)

    assert retried.ok is True, retried.errors
    box.refresh_from_db()
    assert box.deleted_at is not None
    assert box.status == AgentBox.Status.STOPPED.value
    assert not AgentBox.objects.filter(pk=box.pk).exists()


def test_destroy_is_denied_without_the_dispatch_grant(
    permission_resolver, info, org, with_tenant_org, cluster
):
    box = _box(org, status=AgentBox.Status.RUNNING, external_id="agent-box-x")

    with with_tenant_org(org):
        result = AgentsMutation().destroy_agent_box(info, slug=box.slug)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    box.refresh_from_db()
    assert box.deleted_at is None
    assert cluster.driver.deleted == []


# ---------------------------------------------------------------------------
# Tenancy — nothing here is reachable across orgs
# ---------------------------------------------------------------------------


def test_another_orgs_box_is_not_readable(permission_resolver, info, org, other_org, with_tenant_org):
    """``@tenant_scoped`` asserts a tenant exists; it does not filter. The
    resolver's own organization filter is what keeps this closed."""
    permission_resolver.grant(Permission.AGENT_READ)
    theirs = _box(other_org, slug="their-box", status=AgentBox.Status.RUNNING)

    with with_tenant_org(org):
        one = AgentsQuery().agent_box(info, slug=theirs.slug)
        listed = AgentsQuery().agent_boxes(info, org_id=str(org.guid))

    assert one is None
    assert listed == []


def test_another_orgs_box_cannot_be_destroyed(
    permission_resolver, info, org, other_org, with_tenant_org, cluster
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    theirs = _box(other_org, slug="their-box", status=AgentBox.Status.RUNNING, external_id="job-x")

    with with_tenant_org(org):
        result = AgentsMutation().destroy_agent_box(info, slug=theirs.slug)

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    theirs.refresh_from_db()
    assert theirs.deleted_at is None
    assert theirs.status == AgentBox.Status.RUNNING.value
    assert cluster.driver.deleted == []


def test_a_box_cannot_be_ensured_from_another_orgs_env_spec(
    permission_resolver, info, org, other_org, with_tenant_org, cluster
):
    """Otherwise a foreign slug would pull that org's secret refs into a pod
    running in this one."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(other_org, slug="their-spec")

    result = _ensure(info, org, with_tenant_org, environment_spec_slug="their-spec")

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    assert AgentBox.objects.count() == 0
    assert cluster.driver.applied == []


def test_a_box_cannot_be_ensured_from_another_orgs_agent(
    permission_resolver, info, org, other_org, with_tenant_org, cluster
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(org)
    _persistent_agent(other_org, slug="their-agent")

    result = _ensure(info, org, with_tenant_org, agent_slug="their-agent", environment_spec_slug="claude-dev")

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    assert AgentBox.objects.count() == 0


def test_ensuring_into_another_org_by_id_is_refused(
    permission_resolver, info, org, other_org, with_tenant_org, cluster
):
    """The org_id argument must agree with the caller's active tenant."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _spec(other_org, slug="their-spec")

    with with_tenant_org(org):
        result = AgentsMutation().ensure_agent_box(
            info,
            input=EnsureAgentBoxInput(environment_spec_slug="their-spec"),
            org_id=str(other_org.guid),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert AgentBox.objects.count() == 0


def test_the_list_shows_live_boxes_and_can_include_the_settled_ones(
    permission_resolver, info, org, with_tenant_org
):
    """A settled box is history; the default question is "what is warm"."""
    permission_resolver.grant(Permission.AGENT_READ)
    _box(org, slug="warm-box", status=AgentBox.Status.RUNNING)
    _box(org, slug="cold-box", status=AgentBox.Status.EXPIRED)

    with with_tenant_org(org):
        live = AgentsQuery().agent_boxes(info, org_id=str(org.guid))
        everything = AgentsQuery().agent_boxes(info, org_id=str(org.guid), include_ended=True)

    assert [b.slug for b in live] == ["warm-box"]
    assert {b.slug for b in everything} == {"warm-box", "cold-box"}


def test_managed_model_box_receives_identity_and_provider_environment(org, cluster, monkeypatch):
    from astrolift_dispatch.agent_model import ManagedModelWiring

    spec = _spec(org, env_vars={"AWS_REGION": "spec-region"})
    spec.managed_model = True
    spec.save(update_fields=["managed_model"])
    box = _box(org, environment_spec=spec)
    calls = []
    account = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": "agent-model", "namespace": box_service.box_namespace(box)},
    }

    def resolve(**kwargs):
        calls.append(kwargs)
        return ManagedModelWiring(
            env=[
                {"name": "CLAUDE_CODE_USE_BEDROCK", "value": "1"},
                {"name": "AWS_REGION", "value": "provider-region"},
            ],
            service_account="agent-model",
            service_account_manifest=account,
        )

    monkeypatch.setattr("astrolift_dispatch.agent_model.resolve_managed_model_wiring", resolve)
    box_service.start_agent_box(box)
    assert len(calls) == 1
    assert calls[0]["namespace"] == box_service.box_namespace(box)
    manifests = cluster.driver.applied[0]
    assert manifests[0] == account
    job = _job_of(manifests)
    assert job["spec"]["template"]["spec"]["serviceAccountName"] == "agent-model"
    env = {entry["name"]: entry.get("value") for entry in _container_of(job)["env"]}
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["AWS_REGION"] == "spec-region"
    box_service.stop_agent_box(box)
    assert cluster.driver.deleted
    assert all(ref["kind"] != "ServiceAccount" for batch in cluster.driver.deleted for ref in batch)


def test_managed_model_failure_does_not_create_an_uncredentialed_box(org, cluster, monkeypatch):
    from astrolift_dispatch.agent_model import ManagedModelError

    spec = _spec(org)
    spec.managed_model = True
    spec.save(update_fields=["managed_model"])
    box = _box(org, environment_spec=spec)

    def fail(**kwargs):
        raise ManagedModelError("managed model: identity is unavailable")

    monkeypatch.setattr("astrolift_dispatch.agent_model.resolve_managed_model_wiring", fail)
    with pytest.raises(box_service.AgentBoxError, match="identity is unavailable"):
        box_service.start_agent_box(box)
    box.refresh_from_db()
    assert box.status == AgentBox.Status.FAILED
    assert "identity is unavailable" in box.last_error
    assert cluster.driver.applied == []
    assert cluster.driver.namespaces == []


def test_subscription_box_does_not_request_a_managed_model_identity(org, cluster, monkeypatch):
    def unexpected(**kwargs):
        raise AssertionError("subscription boxes must not acquire managed-model identity")

    monkeypatch.setattr("astrolift_dispatch.agent_model.resolve_managed_model_wiring", unexpected)
    box_service.start_agent_box(_box(org, environment_spec=_spec(org)))
    job = _job_of(cluster.driver.applied[0])
    assert "serviceAccountName" not in job["spec"]["template"]["spec"]


# ---------------------------------------------------------------------------
# The box sets up the agent's workspace when its spec asks (#1877)
# ---------------------------------------------------------------------------

_BOX_GUID = "00000000-0000-4000-8000-000000001877"

#: sha256 of the canonical JSON of the bare box Job in the test below, taken
#: at origin/main before #1877. A spec that does not ask for its workspace
#: must leave the manifest byte-identical; a deliberate change to the bare box
#: updates this digest and says why.
#: Updated for #1874: box Job names now use the full guid hex instead of the
#: first 16 characters, because UUIDv7 prefixes are timestamps and collided.
_BARE_BOX_JOB_SHA256 = "36970ddf0d62a4dc63ad2caf5e8660cf3245a8588a77a8cc31ba646ccb400900"


def _canonical_sha256(manifest: dict) -> str:
    import hashlib
    import json

    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _workspace_spec(org, **kwargs):
    spec = _spec(org, **kwargs)
    spec.box_workspace = True
    spec.save(update_fields=["box_workspace", "updated_at", "version"])
    return spec


def _brief(org, *, slug, storage_key, snapshot=None, status=None):
    import hashlib

    from astrolift_agents.models import Brief

    return Brief.objects.create(
        organization=org,
        content_hash=hashlib.sha256(f"{org.slug}/{slug}/{storage_key}".encode()).hexdigest(),
        storage_key=storage_key,
        status=status or Brief.Status.READY,
        manifest_snapshot=(
            {
                "payload_sha256": "ab" * 32,
                "manifest_path": f"agents/{slug}/astrolift.toml",
                "requires_payload": True,
            }
            if snapshot is None
            else snapshot
        ),
    )


def _agent_with_payload(org, *, slug="claude-dev", storage_key=None, **brief_kwargs):
    agent = _persistent_agent(org, slug=slug)
    agent.brief = _brief(
        org,
        slug=slug,
        storage_key=f"{org.slug}/payloads/{slug}.zip" if storage_key is None else storage_key,
        **brief_kwargs,
    )
    agent.save(update_fields=["brief", "updated_at", "version"])
    return agent


@pytest.fixture
def minted(monkeypatch):
    """The blob store's URL signer, recording what each box asked it to sign."""
    import astrolift_pipelines.artifact_store as artifact_store

    calls: list[dict] = []

    def presigned_download_url(*, org, blob_key, expires_in=900):
        calls.append({"org": org.pk, "blob_key": blob_key, "expires_in": expires_in})
        return f"https://blobs.example.net/{blob_key}?sig=abc"

    monkeypatch.setattr(artifact_store, "presigned_download_url", presigned_download_url)
    return calls


def test_a_spec_that_does_not_ask_leaves_the_box_manifest_byte_identical(org, cluster):
    from _sdk.agent_session import WORKSPACE_SETUP_COMMAND

    box = _box(org, guid=_BOX_GUID, environment_spec=_spec(org))

    box_service.start_agent_box(box)

    job = _job_of(cluster.driver.applied[-1])
    container = _container_of(job)
    assert "readinessProbe" not in container
    assert WORKSPACE_SETUP_COMMAND not in container["args"][0]
    assert not {"ASTROLIFT_WORKSPACE", "ASTROLIFT_PAYLOAD_URL"} & set(_env_of(job))
    assert _canonical_sha256(job) == _BARE_BOX_JOB_SHA256


def test_a_workspace_box_sets_up_the_agents_payload_before_its_session(org, cluster, minted):
    from _sdk.agent_session import SessionSpec

    spec = _workspace_spec(org)
    agent = _agent_with_payload(org, slug=spec.slug)

    box = box_service.ensure_agent_box(organization=org, environment_spec_slug=spec.slug)

    job = _job_of(cluster.driver.applied[-1])
    container = _container_of(job)
    env = _env_of(job)
    storage_key = agent.brief.storage_key
    assert minted == [
        {"org": org.pk, "blob_key": storage_key, "expires_in": box_service.BOX_PAYLOAD_URL_TTL_SECONDS}
    ]
    assert env["ASTROLIFT_PAYLOAD_URL"]["value"] == f"https://blobs.example.net/{storage_key}?sig=abc"
    assert env["ASTROLIFT_PAYLOAD_HASH"]["value"] == "sha256:" + "ab" * 32
    assert env["ASTROLIFT_MANIFEST_PATH"]["value"] == "agents/claude-dev/astrolift.toml"
    # The setup builds the workspace where the session starts, so an attach
    # lands in it, and the IDE keeps the /workspace volume it expects.
    assert env["ASTROLIFT_WORKSPACE"]["value"] == container["workingDir"] == "/workspace"
    assert container["volumeMounts"] == [{"name": "workspace", "mountPath": "/workspace"}]
    assert container["args"] == [
        keepalive_script(
            SessionSpec(
                image=container["image"],
                session_name=SESSION_NAME,
                idle_timeout_seconds=box.idle_timeout_seconds,
                workspace_setup=True,
            )
        )
    ]
    assert container["readinessProbe"]["exec"]["command"] == ["tmux", "has-session", "-t", f"={SESSION_NAME}"]
    box.refresh_from_db()
    assert box.status == AgentBox.Status.PROVISIONING


def test_the_boxs_own_agent_supplies_the_payload(org, cluster, minted):
    """Ensured with an agent, the box is that agent in the spec's environment."""
    spec = _workspace_spec(org)
    _agent_with_payload(org, slug=spec.slug, storage_key="box-org/payloads/spec-agent.zip")
    chosen = _agent_with_payload(org, slug="claude-box", storage_key="box-org/payloads/box-agent.zip")

    box_service.ensure_agent_box(organization=org, environment_spec_slug=spec.slug, agent_slug=chosen.slug)

    assert [call["blob_key"] for call in minted] == ["box-org/payloads/box-agent.zip"]


def test_a_spec_cannot_redirect_the_workspace_setup(org, cluster, minted):
    spec = _workspace_spec(
        org,
        env_vars={"ASTROLIFT_WORKSPACE": "/etc", "ASTROLIFT_PAYLOAD_URL": "https://evil.example.net/p.zip"},
    )
    _agent_with_payload(org, slug=spec.slug)

    box_service.ensure_agent_box(organization=org, environment_spec_slug=spec.slug)

    env = _env_of(_job_of(cluster.driver.applied[-1]))
    assert env["ASTROLIFT_WORKSPACE"]["value"] == "/workspace"
    assert env["ASTROLIFT_PAYLOAD_URL"]["value"].startswith("https://blobs.example.net/")


def _second_agent_named(org, slug):
    """Another registered app in the same org with an agent of the same slug."""
    team = Team.objects.create(organization=org, name="Other team", slug="t-other")
    app = RegisteredApp.objects.create(
        organization=org, team=team, name="Other", slug="app-other", provisioning_status="ready"
    )
    return Workload.objects.create(
        registered_app=app,
        name=slug,
        slug=slug,
        kind=Workload.Kind.AGENT,
        run_mode=Workload.RunMode.PERSISTENT,
    )


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("no_agent", "no registered agent is named claude-dev"),
        ("another_orgs_agent", "no registered agent is named claude-dev"),
        ("two_agents", "more than one registered agent is named claude-dev"),
        ("no_brief", "agent claude-dev has no ready Agent Package Brief"),
        ("revoked_brief", "agent claude-dev has no ready Agent Package Brief"),
        ("no_bundle", "agent claude-dev has no payload bundle"),
    ],
)
def test_a_workspace_box_without_a_payload_refuses_to_start(org, other_org, cluster, minted, case, reason):
    """A box that came up without the workspace its spec asked for would look
    healthy and be wrong, so every missing link is the start's failure."""
    from astrolift_agents.models import Brief

    spec = _workspace_spec(org)
    if case == "another_orgs_agent":
        _agent_with_payload(other_org, slug=spec.slug)
    elif case == "two_agents":
        _agent_with_payload(org, slug=spec.slug)
        _second_agent_named(org, spec.slug)
    elif case == "no_brief":
        _persistent_agent(org, slug=spec.slug)
    elif case == "revoked_brief":
        _agent_with_payload(org, slug=spec.slug, status=Brief.Status.REVOKED)
    elif case == "no_bundle":
        _agent_with_payload(org, slug=spec.slug, storage_key="", snapshot={"requires_payload": False})

    with pytest.raises(box_service.AgentBoxError, match=reason):
        box_service.ensure_agent_box(organization=org, environment_spec_slug=spec.slug)

    box = AgentBox.objects.get(organization=org)
    assert box.status == AgentBox.Status.FAILED
    assert reason in box.last_error
    assert cluster.driver.applied == []
    assert minted == []


def test_a_payload_that_cannot_be_delivered_refuses_to_start(org, cluster, monkeypatch):
    from _sdk.blob_store import BlobStoreNotConfiguredError

    import astrolift_pipelines.artifact_store as artifact_store

    def unavailable(**_kwargs):
        raise BlobStoreNotConfiguredError("no blob store is configured for this install")

    monkeypatch.setattr(artifact_store, "presigned_download_url", unavailable)
    spec = _workspace_spec(org)
    _agent_with_payload(org, slug=spec.slug)

    with pytest.raises(
        box_service.AgentBoxError, match="could not deliver agent claude-dev's payload to the box"
    ):
        box_service.ensure_agent_box(organization=org, environment_spec_slug=spec.slug)

    assert "no blob store is configured" in AgentBox.objects.get(organization=org).last_error
    assert cluster.driver.applied == []


def test_the_refusal_reaches_the_ensure_caller_as_a_result(
    permission_resolver, info, org, with_tenant_org, cluster, minted
):
    """The IDE's button gets the sentence, not a 500."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    _workspace_spec(org)

    result = _ensure(info, org, with_tenant_org, environment_spec_slug="claude-dev")

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert "no registered agent is named claude-dev" in result.errors[0].message


# ---------------------------------------------------------------------------
# A box behind the Zentinelle gateway (#1851)
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway(cluster, org, monkeypatch):
    """The in-memory cluster as a registered TenantCluster whose gateway runs,
    an organization connected to a fake Zentinelle, and the install flag on."""
    import uuid

    from constance.test import override_config
    from django.utils import timezone

    import astrolift_agents.services.agent_cluster as agent_cluster
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_dispatch import model_gateway
    from astrolift_dispatch.tests.test_model_gateway_1851 import FakeZentinelle
    from astrolift_operations import zentinelle_connect
    from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
    from core.secrets import encrypt_at_rest

    events: list = []
    zentinelle = FakeZentinelle(events)
    monkeypatch.setattr(zentinelle_connect.requests, "request", zentinelle)
    monkeypatch.setattr(model_gateway.time, "sleep", lambda seconds: None)
    monkeypatch.setattr("core.cluster_observability.list_app_pods", lambda **kwargs: [])
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="k8s", slug="k8s_native", capabilities_manifest={}, config_schema={})]
    )
    row = TenantCluster.objects.create(
        organization=org,
        slug=f"kind-{uuid.uuid4().hex[:6]}",
        name="kind",
        provider_plugin=plugin,
        provider_config={},
        region="local",
        endpoint="https://kind.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    monkeypatch.setattr(agent_cluster, "resolve_agent_cluster", lambda _org: row)
    sealed = encrypt_at_rest(zentinelle.install(org).encode("utf-8"))
    connection = ZentinelleConnection.objects.create(
        organization=org,
        base_url="https://zentinelle.test",
        credential_backend_kind=sealed.backend_kind,
        credential_ciphertext=sealed.backend_ref,
        connected_at=timezone.now(),
    )
    ZentinelleClusterGateway.objects.create(
        connection=connection,
        cluster=row,
        zentinelle_cluster_id=str(row.guid),
        gateway_enabled=True,
        gateway_deployed=True,
    )
    cluster.secrets.store.update(
        {
            "agents/claude-dev/anthropic": {"value": "sk-ant-stored-provider-key"},
            "agents/claude-dev/github": {"value": "ghp_stored_github_token"},
        }
    )
    with override_config(ZENTINELLE_GATEWAY_ENABLED=True):
        yield SimpleNamespace(zentinelle=zentinelle, driver=cluster.driver, connection=connection)


_GATEWAY_API = "/api/zentinelle/v1/astrolift"


def _gateway_spec(org, **kwargs):
    spec = _spec(
        org,
        refs=[
            {"uri": "agents/claude-dev/anthropic", "env_var": "ANTHROPIC_API_KEY"},
            {"uri": "agents/claude-dev/github", "env_var": "GITHUB_TOKEN"},
        ],
        env_vars={"ANTHROPIC_BASE_URL": "https://api.anthropic.com", "LOG_LEVEL": "debug"},
        **kwargs,
    )
    spec.model_gateway = True
    spec.save(update_fields=["model_gateway", "updated_at", "version"])
    return spec


def _box_agent_id(box):
    return f"astrolift-box-{str(box.guid).replace('-', '')}"


def test_a_gateway_box_gets_its_own_key_and_no_provider_key(org, gateway):
    from django.utils import timezone

    from astrolift_dispatch.model_gateway import GATEWAY_KEY_ENV, RESERVED_ENV_NAMES

    box = _box(org, environment_spec=_gateway_spec(org))

    box_service.start_agent_box(box)

    [mint] = gateway.zentinelle.calls
    assert (mint.method, mint.path, mint.install) == ("POST", f"{_GATEWAY_API}/agents", org.slug)
    assert mint.json == {
        "agent_id": _box_agent_id(box),
        "ttl_seconds": 3600 + 900,
        "name": f"claude-dev box {box.slug}",
        "deployment_id": "claude-dev",
    }
    key = gateway.zentinelle.minted[_box_agent_id(box)]
    [batch] = gateway.driver.applied
    secret = next(m for m in batch if m["kind"] == "Secret")
    assert secret["stringData"] == {GATEWAY_KEY_ENV: key, "GITHUB_TOKEN": "ghp_stored_github_token"}
    job = _job_of(batch)
    env = _env_of(job)
    assert env["ANTHROPIC_BASE_URL"]["value"] == "http://zentinelle-gateway.astrolift-system.svc:8742"
    assert env[GATEWAY_KEY_ENV]["valueFrom"]["secretKeyRef"] == {
        "name": box_service.box_secret_name(box_service.box_job_name(box)),
        "key": GATEWAY_KEY_ENV,
    }
    assert set(env) & RESERVED_ENV_NAMES == {"ANTHROPIC_BASE_URL", GATEWAY_KEY_ENV}
    assert env["LOG_LEVEL"]["value"] == "debug"
    assert env[box_service.BOX_ENV_GUID]["value"] == str(box.guid)
    assert key not in str(job)
    assert "sk-ant-stored-provider-key" not in str(batch)
    box.refresh_from_db()
    assert box.status == AgentBox.Status.PROVISIONING
    assert box.model_gateway_agent_id == _box_agent_id(box)
    remaining = (box.model_gateway_expires_at - timezone.now()).total_seconds()
    assert 4500 - 60 < remaining <= 4500


def test_a_never_reaped_box_gets_a_day_long_key_window(org, gateway):
    box = _box(org, environment_spec=_gateway_spec(org), idle_timeout_seconds=0)

    box_service.start_agent_box(box)

    assert gateway.zentinelle.calls[0].json["ttl_seconds"] == 24 * 3600 + 900


def test_a_box_the_gateway_refuses_fails_before_anything_is_applied(org, gateway):
    from astrolift_operations.models import ZentinelleClusterGateway

    ZentinelleClusterGateway.objects.update(gateway_deployed=False)
    box = _box(org, environment_spec=_gateway_spec(org))

    with pytest.raises(box_service.AgentBoxError, match="is not deployed"):
        box_service.start_agent_box(box)

    box.refresh_from_db()
    assert box.status == AgentBox.Status.FAILED
    assert "sends its model traffic through the Zentinelle gateway" in box.last_error
    assert gateway.driver.applied == []
    assert gateway.zentinelle.calls == []


def test_a_box_whose_key_cannot_be_minted_fails_and_holds_nothing(org, gateway):
    from astrolift_dispatch.tests.test_model_gateway_1851 import FakeResponse

    gateway.zentinelle.answer("POST", "/agents", FakeResponse(503, {"detail": "maintenance"}))
    box = _box(org, environment_spec=_gateway_spec(org))

    with pytest.raises(box_service.AgentBoxError, match="did not mint"):
        box_service.start_agent_box(box)

    box.refresh_from_db()
    assert box.status == AgentBox.Status.FAILED
    assert "(503: maintenance)" in box.last_error
    assert gateway.driver.applied == []
    assert gateway.zentinelle.paths() == [
        ("POST", f"{_GATEWAY_API}/agents"),
        ("DELETE", f"{_GATEWAY_API}/agents/{_box_agent_id(box)}"),
    ]


def test_a_box_whose_apply_fails_revokes_its_key(org, gateway, monkeypatch):
    monkeypatch.setattr(gateway.driver, "_apply_ok", False)
    box = _box(org, environment_spec=_gateway_spec(org))

    with pytest.raises(box_service.AgentBoxError):
        box_service.start_agent_box(box)

    assert gateway.zentinelle.revoked == [_box_agent_id(box)]
    box.refresh_from_db()
    assert box.status == AgentBox.Status.FAILED


def test_stopping_a_box_revokes_its_key(org, gateway):
    box = _box(org, environment_spec=_gateway_spec(org))
    box_service.start_agent_box(box)
    gateway.zentinelle.calls.clear()

    box_service.stop_agent_box(box)

    assert gateway.zentinelle.paths() == [("DELETE", f"{_GATEWAY_API}/agents/{_box_agent_id(box)}")]
    assert {ref["kind"] for ref in gateway.driver.deleted[-1]} == {"Job", "Secret"}


def test_a_destroy_that_cannot_tear_down_still_revokes_the_key(org, gateway, monkeypatch):
    box = _box(org, environment_spec=_gateway_spec(org))
    box_service.start_agent_box(box)

    def refuse(*args, **kwargs):
        raise RuntimeError("provider refused teardown")

    monkeypatch.setattr(gateway.driver, "delete_manifests", refuse)
    with pytest.raises(box_service.AgentBoxError, match="provider refused teardown"):
        box_service.destroy_agent_box(box)

    assert gateway.zentinelle.revoked == [_box_agent_id(box)]


def test_the_reaper_renews_a_live_boxs_key_once_half_its_window_is_left(org, gateway):
    from datetime import timedelta

    from django.utils import timezone

    box = _box(org, environment_spec=_gateway_spec(org))
    box_service.start_agent_box(box)
    gateway.zentinelle.calls.clear()

    box_service.reap_agent_boxes()
    assert gateway.zentinelle.calls == []

    AgentBox.objects.filter(pk=box.pk).update(model_gateway_expires_at=timezone.now() + timedelta(minutes=30))
    box_service.reap_agent_boxes()

    [renew] = gateway.zentinelle.calls
    assert (renew.method, renew.path) == ("POST", f"{_GATEWAY_API}/agents/{_box_agent_id(box)}/renew")
    assert renew.json == {"ttl_seconds": 4500}
    box.refresh_from_db()
    assert box.status == AgentBox.Status.RUNNING
    assert (box.model_gateway_expires_at - timezone.now()).total_seconds() > 4400


def test_a_failed_renewal_waits_for_the_next_sweep(org, gateway):
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_dispatch.tests.test_model_gateway_1851 import FakeResponse

    box = _box(org, environment_spec=_gateway_spec(org))
    box_service.start_agent_box(box)
    soon = timezone.now() + timedelta(minutes=10)
    AgentBox.objects.filter(pk=box.pk).update(model_gateway_expires_at=soon)
    gateway.zentinelle.answer("POST", f"/agents/{_box_agent_id(box)}/renew", FakeResponse(500))

    summary = box_service.reap_agent_boxes()

    assert summary["errors"] == 0
    box.refresh_from_db()
    assert box.model_gateway_expires_at == soon
    box_service.reap_agent_boxes()
    box.refresh_from_db()
    assert box.model_gateway_expires_at > soon


def test_a_box_that_ended_is_revoked_not_renewed(org, gateway):
    from datetime import timedelta

    from django.utils import timezone

    box = _box(org, environment_spec=_gateway_spec(org))
    box_service.start_agent_box(box)
    AgentBox.objects.filter(pk=box.pk).update(model_gateway_expires_at=timezone.now() + timedelta(minutes=5))
    gateway.driver._job_conditions = [{"type": "Complete", "status": "True"}]
    gateway.zentinelle.calls.clear()

    box_service.reap_agent_boxes()

    assert gateway.zentinelle.paths() == [("DELETE", f"{_GATEWAY_API}/agents/{_box_agent_id(box)}")]
    box.refresh_from_db()
    assert box.status == AgentBox.Status.EXPIRED


def test_a_box_restarted_without_the_gateway_drops_its_old_key(org, gateway):
    spec = _gateway_spec(org)
    box = _box(org, environment_spec=spec)
    box_service.start_agent_box(box)
    box_service.stop_agent_box(box)
    spec.model_gateway = False
    spec.save(update_fields=["model_gateway", "updated_at", "version"])
    gateway.zentinelle.calls.clear()

    box_service.start_agent_box(box)
    box_service.stop_agent_box(box)
    box_service.reap_agent_boxes()

    assert gateway.zentinelle.calls == []
    box.refresh_from_db()
    assert (box.model_gateway_agent_id, box.model_gateway_expires_at) == ("", None)


def test_a_spec_that_does_not_ask_leaves_the_box_byte_identical_with_the_gateway_running(org, gateway):
    box = _box(org, guid=_BOX_GUID, environment_spec=_spec(org))

    box_service.start_agent_box(box)
    box_service.stop_agent_box(box)

    assert gateway.zentinelle.calls == []
    assert _canonical_sha256(_job_of(gateway.driver.applied[-1])) == _BARE_BOX_JOB_SHA256


def test_a_gateway_box_records_the_connection_that_minted_its_key(org, gateway):
    box = _box(org, environment_spec=_gateway_spec(org))

    box_service.start_agent_box(box)

    box.refresh_from_db()
    assert box.model_gateway_connection == gateway.connection
    assert box.model_gateway_lifetime_ends_at is not None


def test_one_boxs_failing_renewal_does_not_end_the_sweep(org, gateway, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone

    import astrolift_dispatch.model_gateway as model_gateway

    first = _box(org, environment_spec=_gateway_spec(org), slug="first-box")
    second = _box(org, environment_spec=_gateway_spec(org, slug="claude-dev-2"), slug="second-box")
    for box in (first, second):
        box_service.start_agent_box(box)
    AgentBox.objects.update(model_gateway_expires_at=timezone.now() + timedelta(minutes=10))
    renew = model_gateway.renew_run_key
    renewed = []

    def flaky(*, connection_id, agent_id, ttl_seconds):
        if agent_id == _box_agent_id(first):
            raise RuntimeError("database connection lost")
        renewed.append(agent_id)
        return renew(connection_id=connection_id, agent_id=agent_id, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(model_gateway, "renew_run_key", flaky)

    summary = box_service.reap_agent_boxes()

    assert summary["evaluated"] == 2
    assert renewed == [_box_agent_id(second)]
    second.refresh_from_db()
    assert (second.model_gateway_expires_at - timezone.now()).total_seconds() > 4400


def test_the_reaper_stops_renewing_at_the_keys_lifetime_end(org, gateway):
    from datetime import timedelta

    from django.utils import timezone

    # Less than half of the box's 75-minute window: due for renewal at once,
    # were it not for the lifetime end.
    gateway.zentinelle.lifetime = timedelta(minutes=30)
    box = _box(org, environment_spec=_gateway_spec(org))
    box_service.start_agent_box(box)
    box.refresh_from_db()
    # Zentinelle's lifetime cap comes before the box's window.
    assert box.model_gateway_expires_at == box.model_gateway_lifetime_ends_at
    gateway.zentinelle.calls.clear()

    box_service.reap_agent_boxes()
    box_service.reap_agent_boxes()

    assert gateway.zentinelle.calls == []
    # And an expired key is not renewed either: only a restart mints a new one.
    AgentBox.objects.filter(pk=box.pk).update(
        model_gateway_expires_at=timezone.now() - timedelta(seconds=1),
        model_gateway_lifetime_ends_at=timezone.now() + timedelta(days=1),
    )
    box_service.reap_agent_boxes()
    assert gateway.zentinelle.calls == []


def test_a_renewal_that_reaches_the_lifetime_end_says_so(org, gateway, monkeypatch):
    import logging
    from datetime import timedelta

    from django.utils import timezone

    box = _box(org, environment_spec=_gateway_spec(org))
    box_service.start_agent_box(box)
    agent = gateway.zentinelle.agents[_box_agent_id(box)]
    agent.lifetime_ends_at = timezone.now() + timedelta(minutes=50)
    AgentBox.objects.filter(pk=box.pk).update(
        model_gateway_expires_at=timezone.now() + timedelta(minutes=10),
        model_gateway_lifetime_ends_at=agent.lifetime_ends_at,
    )
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    logging.getLogger("astrolift_agents.services.agent_box").addHandler(handler)
    try:
        box_service.reap_agent_boxes()
    finally:
        logging.getLogger("astrolift_agents.services.agent_box").removeHandler(handler)

    box.refresh_from_db()
    assert box.model_gateway_expires_at == agent.lifetime_ends_at
    assert any("reaches Zentinelle's key lifetime" in record.getMessage() for record in records)


def test_another_providers_key_refuses_the_box_before_anything_is_minted(org, gateway):
    spec = _gateway_spec(org)
    spec.env_vars = {**spec.env_vars, "GROQ_API_KEY": "gsk-1"}
    spec.save(update_fields=["env_vars", "updated_at", "version"])
    box = _box(org, environment_spec=spec)

    with pytest.raises(box_service.AgentBoxError, match="GROQ_API_KEY"):
        box_service.start_agent_box(box)

    box.refresh_from_db()
    assert box.status == AgentBox.Status.FAILED
    assert "another model provider's credential or endpoint" in box.last_error
    assert gateway.zentinelle.calls == []
    assert gateway.driver.applied == []


def test_a_box_whose_agent_an_administrator_stopped_fails_readably(org, gateway):
    box = _box(org, environment_spec=_gateway_spec(org))
    box_service.start_agent_box(box)
    box_service.stop_agent_box(box)
    gateway.zentinelle.stop(_box_agent_id(box))
    gateway.zentinelle.calls.clear()

    with pytest.raises(box_service.AgentBoxError, match="stays stopped"):
        box_service.start_agent_box(box)

    box.refresh_from_db()
    assert box.status == AgentBox.Status.FAILED
    assert "stopped in Zentinelle by someone other than this install" in box.last_error
    assert gateway.zentinelle.paths() == [("POST", f"{_GATEWAY_API}/agents")]


def test_a_box_key_is_revoked_only_through_the_install_that_minted_it(org, gateway):
    from astrolift_dispatch.tests.test_model_gateway_1851 import _connect

    box = _box(org, environment_spec=_gateway_spec(org))
    box_service.start_agent_box(box)
    # The organization disconnects and connects again: a new install.
    gateway.connection.soft_delete()
    _connect(gateway.zentinelle, org)
    gateway.zentinelle.calls.clear()

    box_service.stop_agent_box(box)

    assert gateway.zentinelle.calls == []
    assert gateway.zentinelle.agents[_box_agent_id(box)].status == "active"
