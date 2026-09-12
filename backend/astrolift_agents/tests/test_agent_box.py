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

    def delete_manifests(self, cluster_slug, namespace, refs):
        self.deleted.append([dict(r) for r in refs])
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
    spec = _spec(org, env_vars={"ASTROLIFT_AGENT_BOX": "0", "MY_VAR": "keep"})
    box = _box(org, environment_spec=spec)
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-abc")
    env = _env_of(job)

    assert env["ASTROLIFT_AGENT_BOX"]["value"] == "1"
    assert env["ASTROLIFT_AGENT_BOX_GUID"]["value"] == str(box.guid)
    assert env["MY_VAR"]["value"] == "keep"


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
def test_the_rendered_pod_reaps_itself_when_idle(org):
    """Run the manifest's own entrypoint and watch it end.

    Not a re-test of the SDK: the script here is pulled out of the Job the
    platform would apply, so this fails if the render ever stops threading
    the box's idle timeout into the container that gets deployed.
    """
    box = _box(org, idle_timeout_seconds=1, environment_spec=_spec(org))
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-idle")
    script = _container_of(job)["args"][0]

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
def test_a_never_reap_box_stays_up(org):
    """The explicit opt-out has to survive the same render path."""
    box = _box(org, idle_timeout_seconds=0, environment_spec=_spec(org))
    job = box_service.render_agent_box_job(box=box, image="i:1", namespace="ns", job_name="agent-box-never")
    script = _container_of(job)["args"][0]

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
