"""Tests for the execute_agent_stage / cancel_agent_stage activity primitives.

Like ``test_rollback_promote_activities``, these exercise the data-side
``_sync`` helpers the activity orchestrates rather than running a Temporal
worker. The dispatch backend (``get_spawner``) is stubbed with a fake spawner
so the tests pin the AgentTask lifecycle (create -> queue -> provision -> run
-> terminal), the Brief linkage, and the cancellation semantics without a live
cluster.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Brief, Skill
from astrolift_agents.services import brief_assembler
from astrolift_dispatch.spawners import registry as spawner_registry
from astrolift_dispatch.spawners.base import SpawnResult, TaskStatus
from astrolift_workflows.activities import agent_stage

pytestmark = pytest.mark.django_db


# ---- fakes + fixtures --------------------------------------------------


class _FakeSpawner:
    """In-memory ContainerSpawner stand-in driven by the test.

    ``status_sequence`` is consumed one entry per ``status()`` call; the last
    entry repeats once exhausted so a poll loop converges.
    """

    def __init__(
        self,
        *,
        spawn_result: SpawnResult,
        status_sequence: list[TaskStatus],
        stop_error: Exception | None = None,
    ):
        self._spawn_result = spawn_result
        self._status_sequence = list(status_sequence)
        self.spawned_task = None
        self.stopped_ids: list[str] = []
        self.cleaned_secret_ids: list[str] = []
        self._stop_error = stop_error

    def spawn(self, task) -> SpawnResult:
        self.spawned_task = task
        return self._spawn_result

    def status(self, external_id: str) -> TaskStatus:
        if len(self._status_sequence) > 1:
            return self._status_sequence.pop(0)
        return self._status_sequence[0]

    def stop(self, external_id: str) -> None:
        self.stopped_ids.append(external_id)
        if self._stop_error is not None:
            raise self._stop_error

    def cleanup_task_secret(self, external_id: str) -> None:
        self.cleaned_secret_ids.append(external_id)


@pytest.fixture
def patch_spawner(monkeypatch):
    """Install a fake spawner; return a setter the test calls with its fake."""

    holder: dict[str, _FakeSpawner] = {}

    def _install(fake: _FakeSpawner) -> _FakeSpawner:
        monkeypatch.setattr(
            spawner_registry,
            "get_spawner",
            lambda backend, **kw: fake,
        )
        holder["fake"] = fake
        return fake

    return _install


@pytest.fixture
def env_spec(org):
    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Claude Dev",
        slug="claude-dev",
        image_tag="ecr.example/agent:latest",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
    )


@pytest.fixture
def skill(org):
    return Skill.objects.create(
        organization=org,
        name="Reviewer",
        slug="reviewer",
        content="You are a code reviewer. Be thorough.",
        is_active=True,
    )


def _params(org, **overrides):
    base = {
        "org_slug": org.slug,
        "output_key": "stage_0",
        "context": {"run": "abc"},
        "timeout_seconds": 1800,
    }
    base.update(overrides)
    return base


# ---- task creation + queueing -----------------------------------------


def test_create_agent_task_queues_in_draft_then_queued(org, env_spec):
    """The create helper builds an AgentTask, links the env spec, and lands
    it in QUEUED — no Brief when the env spec has no config_repo."""
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))

    task = AgentTask.objects.get(pk=task_pk)
    assert task.status == AgentTask.Status.QUEUED
    assert task.queued_at is not None
    assert task.environment_spec_id == env_spec.id
    assert task.timeout_seconds == 1800
    # No config_repo on the env spec -> no Brief assembled, no network call.
    assert task.brief_id is None


def test_create_agent_task_unknown_org_raises():
    with pytest.raises(RuntimeError, match="not found"):
        agent_stage._create_agent_task_sync({"org_slug": "nope", "output_key": "k"})


def test_create_agent_task_unknown_skill_raises(org):
    with pytest.raises(RuntimeError, match="skill"):
        agent_stage._create_agent_task_sync(_params(org, skill_slug="ghost"))


def test_create_agent_task_prefers_org_skill_over_global(org, monkeypatch):
    """A global skill and an org skill share a slug — the org-owned one wins
    and its content becomes the system prompt folded into the Brief."""
    Skill.objects.create(organization=None, name="Global Rev", slug="rev", content="global", is_active=True)
    org_skill = Skill.objects.create(
        organization=org, name="Org Rev", slug="rev", content="org-specific prompt", is_active=True
    )
    spec = AgentEnvironmentSpec.objects.create(
        organization=org,
        name="With Repo",
        slug="with-repo",
        image_tag="img:1",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        config_repo="acme/agent-config",
    )

    captured = {}

    def _fake_assemble(*, organization, config_repo, config_branch, context, ttl_seconds, manifest_path=""):
        captured["context"] = context
        return Brief.objects.create(
            organization=organization,
            content_hash="deadbeef" * 8,
            manifest_snapshot={},
            status=Brief.Status.READY,
        )

    monkeypatch.setattr(brief_assembler, "assemble_agent_brief", _fake_assemble)
    monkeypatch.setattr(agent_stage, "assemble_agent_brief", _fake_assemble, raising=False)

    task_pk = agent_stage._create_agent_task_sync(
        _params(org, skill_slug="rev", environment_spec_slug=spec.slug)
    )
    task = AgentTask.objects.select_related("brief").get(pk=task_pk)

    assert task.brief is not None
    assert task.brief.manifest_snapshot["system_prompt"] == "org-specific prompt"
    assert task.brief.manifest_snapshot["skill_slug"] == "rev"
    # output_key + provided context were folded into the Brief context.
    assert captured["context"]["output_key"] == "stage_0"
    assert captured["context"]["run"] == "abc"
    # the org-owned skill won, not the global one
    assert task.brief.manifest_snapshot["system_prompt"] != "global"
    del org_skill  # silence unused in some linters


# ---- spawn + poll lifecycle -------------------------------------------


def test_spawn_advances_to_provisioning_and_records_external_id(org, env_spec, cluster, patch_spawner):
    fake = patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-abc123"),
            status_sequence=[TaskStatus(running=True)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))

    spawn = agent_stage._spawn_agent_task_sync(task_pk)

    assert spawn["ok"] is True
    assert spawn["external_id"] == "agent-task-abc123"
    assert fake.spawned_task.pk == task_pk
    task = AgentTask.objects.get(pk=task_pk)
    assert task.status == AgentTask.Status.PROVISIONING
    assert task.external_id == "agent-task-abc123"
    assert task.pod_name == "agent-task-abc123"


def test_spawn_failure_marks_task_failed(org, env_spec, cluster, patch_spawner):
    patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="x", ok=False, error="quota exceeded"),
            status_sequence=[TaskStatus()],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))

    spawn = agent_stage._spawn_agent_task_sync(task_pk)

    assert spawn["ok"] is False
    task = AgentTask.objects.get(pk=task_pk)
    assert task.status == AgentTask.Status.FAILED
    assert task.failure["message"] == "spawn failed: quota exceeded"


def test_spawn_exception_marks_task_failed_instead_of_stranding_provisioning(
    org, env_spec, cluster, patch_spawner
):
    class _ExplodingSpawner(_FakeSpawner):
        def spawn(self, task) -> SpawnResult:
            raise RuntimeError("renderer exploded")

    patch_spawner(
        _ExplodingSpawner(
            spawn_result=SpawnResult(external_id="unused"),
            status_sequence=[TaskStatus()],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))

    spawn = agent_stage._spawn_agent_task_sync(task_pk)

    task = AgentTask.objects.get(pk=task_pk)
    assert spawn["ok"] is False
    assert "renderer exploded" in spawn["error"]
    assert task.status == AgentTask.Status.FAILED
    assert "renderer exploded" in task.failure["message"]


def test_cancel_during_spawn_deletes_just_created_job(org, env_spec, cluster, patch_spawner):
    """Cancellation between the Kubernetes create and external-id save must
    not leave the newly created Job running or resurrect the cancelled row."""

    class _CancelDuringSpawn(_FakeSpawner):
        def spawn(self, task) -> SpawnResult:
            AgentTask.objects.get(pk=task.pk).transition_to(AgentTask.Status.CANCELLED)
            return super().spawn(task)

    fake = patch_spawner(
        _CancelDuringSpawn(
            spawn_result=SpawnResult(external_id="agent-task-raced"),
            status_sequence=[TaskStatus(running=True)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))

    spawn = agent_stage._spawn_agent_task_sync(task_pk)

    task = AgentTask.objects.get(pk=task_pk)
    assert spawn["ok"] is False
    assert "cancelled" in spawn["error"]
    assert task.status == AgentTask.Status.CANCELLED
    assert task.external_id == ""
    assert fake.stopped_ids == ["agent-task-raced"]


def test_spawn_without_managed_cluster_terminalizes_task(org, env_spec, patch_spawner):
    """No cluster must produce an inspectable FAILED task, not an immortal queue row."""
    patch_spawner(_FakeSpawner(spawn_result=SpawnResult(external_id="x"), status_sequence=[TaskStatus()]))
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))

    spawn = agent_stage._spawn_agent_task_sync(task_pk)

    task = AgentTask.objects.get(pk=task_pk)
    assert spawn["ok"] is False
    assert "no managed cluster" in spawn["error"]
    assert task.status == AgentTask.Status.FAILED


def _shared_managed_cluster(provider_plugin):
    """A platform-shared managed cluster (organization is NULL) — how installs
    register the EKS the org's apps deploy onto via AppEnvironment.tenant_cluster."""
    from astrolift_clusters.models import TenantCluster

    return TenantCluster.objects.create(
        organization=None,
        name="shared-platform",
        slug="shared-platform",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://shared.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def test_resolve_cluster_falls_back_to_shared_platform_cluster(org, provider_plugin):
    """The org owns no cluster, but a shared (org=NULL) managed cluster exists —
    agent dispatch must resolve onto it (the real prod situation that left Once
    tasks stuck in QUEUED). Regression for the org-equality-only filter."""
    shared = _shared_managed_cluster(provider_plugin)
    assert agent_stage._resolve_managed_cluster(org).pk == shared.pk


def test_resolve_cluster_prefers_org_owned_over_shared(org, provider_plugin, cluster):
    """When the org owns a managed cluster AND a shared one exists, the org-owned
    one wins (it isn't shadowed by the shared platform cluster)."""
    _shared_managed_cluster(provider_plugin)
    assert agent_stage._resolve_managed_cluster(org).pk == cluster.pk


def test_poll_running_then_success_walks_to_completed(org, env_spec, cluster, patch_spawner):
    """A PROVISIONING task observed running transitions to RUNNING; a later
    succeeded status walks it to COMPLETED and records the result."""
    fake = patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-run"),
            status_sequence=[
                TaskStatus(running=True),
                TaskStatus(succeeded=True, exit_code=0),
            ],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)

    first = agent_stage._poll_agent_task_sync(task_pk)
    assert first["terminal"] is False
    assert AgentTask.objects.get(pk=task_pk).status == AgentTask.Status.RUNNING

    second = agent_stage._poll_agent_task_sync(task_pk)
    assert second["terminal"] is True
    task = AgentTask.objects.get(pk=task_pk)
    assert task.status == AgentTask.Status.COMPLETED
    assert task.result == {"exit_code": 0}
    assert task.ended_at is not None
    assert fake.cleaned_secret_ids == ["agent-task-run"]


def test_poll_fast_success_from_provisioning_steps_through_running(org, env_spec, cluster, patch_spawner):
    """Container finished before we ever saw it running: success observed
    while still PROVISIONING must still reach COMPLETED (via RUNNING)."""
    patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-fast"),
            status_sequence=[TaskStatus(succeeded=True, exit_code=0)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)  # -> PROVISIONING

    result = agent_stage._poll_agent_task_sync(task_pk)

    assert result["terminal"] is True
    assert AgentTask.objects.get(pk=task_pk).status == AgentTask.Status.COMPLETED


class _FakePod:
    def __init__(self, status: str) -> None:
        self.status = status


def test_poll_fails_fast_on_imagepullbackoff(org, env_spec, cluster, patch_spawner, monkeypatch):
    """A pod wedged in ImagePullBackOff never yields a Job Complete/Failed
    condition, so the spawner reports running forever. The pod-health gate
    must fail the task rather than leave it RUNNING indefinitely (the 31-min
    hang from a missing -vnc image tag)."""
    fake = patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-stuck"),
            status_sequence=[TaskStatus(running=True)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)

    monkeypatch.setattr(
        "core.cluster_observability.list_app_pods",
        lambda **kwargs: [_FakePod("ImagePullBackOff")],
    )

    result = agent_stage._poll_agent_task_sync(task_pk)
    assert result["terminal"] is True
    task = AgentTask.objects.get(pk=task_pk)
    assert task.status == AgentTask.Status.FAILED
    assert "ImagePullBackOff" in (task.failure or {}).get("message", "")
    assert fake.stopped_ids == ["agent-task-stuck"]


def test_poll_does_not_fail_on_transient_pod_state(org, env_spec, cluster, patch_spawner, monkeypatch):
    """A benign startup state (ContainerCreating) is NOT fatal — the task
    stays RUNNING and gets another poll, so a slow image pull isn't killed."""
    patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-boot"),
            status_sequence=[TaskStatus(running=True)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)

    monkeypatch.setattr(
        "core.cluster_observability.list_app_pods",
        lambda **kwargs: [_FakePod("ContainerCreating")],
    )

    result = agent_stage._poll_agent_task_sync(task_pk)
    assert result["terminal"] is False
    assert AgentTask.objects.get(pk=task_pk).status == AgentTask.Status.RUNNING


def test_poll_failure_marks_failed_with_details(org, env_spec, cluster, patch_spawner):
    patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-fail"),
            status_sequence=[TaskStatus(failed=True, exit_code=137, error_message="OOMKilled")],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)

    result = agent_stage._poll_agent_task_sync(task_pk)

    assert result["terminal"] is True
    task = AgentTask.objects.get(pk=task_pk)
    assert task.status == AgentTask.Status.FAILED
    assert task.failure["exit_code"] == 137
    assert task.failure["message"] == "OOMKilled"


def test_poll_terminal_task_cleans_secret_without_deleting_log_history(org, env_spec, cluster, patch_spawner):
    """A callback-terminal task drops its temporary Secret but keeps its Job."""
    fake = patch_spawner(
        _FakeSpawner(spawn_result=SpawnResult(external_id="x"), status_sequence=[TaskStatus()])
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    task = AgentTask.objects.get(pk=task_pk)
    task.transition_to(AgentTask.Status.PROVISIONING)
    task.external_id = "agent-task-callback"
    task.namespace = "astrolift-agents-acme-test"
    task.save(update_fields=["external_id", "namespace", "updated_at", "version"])
    task.transition_to(AgentTask.Status.RUNNING)
    task.transition_to(AgentTask.Status.COMPLETED)

    result = agent_stage._poll_agent_task_sync(task_pk)
    assert result == {"status": AgentTask.Status.COMPLETED, "terminal": True}
    assert fake.cleaned_secret_ids == ["agent-task-callback"]
    assert fake.stopped_ids == []


# ---- outcome payload ---------------------------------------------------


def test_load_outcome_returns_output_key_from_brief(org, monkeypatch):
    spec = AgentEnvironmentSpec.objects.create(
        organization=org,
        name="R",
        slug="r",
        image_tag="img:1",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        config_repo="acme/cfg",
    )

    def _fake_assemble(*, organization, config_repo, config_branch, context, ttl_seconds, manifest_path=""):
        return Brief.objects.create(
            organization=organization,
            content_hash="abc" + "0" * 61,
            manifest_snapshot={},
            context=context,
            status=Brief.Status.READY,
        )

    monkeypatch.setattr(agent_stage, "assemble_agent_brief", _fake_assemble, raising=False)
    monkeypatch.setattr(brief_assembler, "assemble_agent_brief", _fake_assemble)

    task_pk = agent_stage._create_agent_task_sync(
        _params(org, environment_spec_slug=spec.slug, output_key="merge_step")
    )

    outcome = agent_stage._load_task_outcome_sync(task_pk)
    assert outcome["output_key"] == "merge_step"
    assert outcome["status"] == AgentTask.Status.QUEUED


# ---- cancellation ------------------------------------------------------


def test_cancel_queued_task_stops_and_cancels(org, env_spec, cluster, patch_spawner):
    fake = patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-cncl"),
            status_sequence=[TaskStatus(running=True)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)  # PROVISIONING + external_id set
    task = AgentTask.objects.get(pk=task_pk)

    agent_stage._cancel_agent_task_sync(str(task.guid))

    task.refresh_from_db()
    assert task.status == AgentTask.Status.CANCELLED
    assert fake.stopped_ids == ["agent-task-cncl"]


def test_cancel_running_task_stops_and_cancels(org, env_spec, cluster, patch_spawner):
    """A running task is a real operator kill: stop its Job and settle it as
    CANCELLED instead of rejecting the mutation or misreporting FAILED."""
    fake = patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-runc"),
            status_sequence=[TaskStatus(running=True)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)
    task = AgentTask.objects.get(pk=task_pk)
    task.transition_to(AgentTask.Status.RUNNING)

    agent_stage._cancel_agent_task_sync(str(task.guid))

    task.refresh_from_db()
    assert task.status == AgentTask.Status.CANCELLED
    assert fake.stopped_ids == ["agent-task-runc"]


def test_poll_timeout_stops_job_and_marks_timed_out(org, env_spec, cluster, patch_spawner):
    fake = patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-timeout"),
            status_sequence=[TaskStatus(running=True)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(
        _params(org, environment_spec_slug=env_spec.slug, timeout_seconds=30)
    )
    agent_stage._spawn_agent_task_sync(task_pk)
    task = AgentTask.objects.get(pk=task_pk)
    task.provisioning_at = timezone.now() - timedelta(seconds=31)
    task.save(update_fields=["provisioning_at", "updated_at", "version"])

    result = agent_stage._poll_agent_task_sync(task_pk)

    task.refresh_from_db()
    assert result == {"status": AgentTask.Status.TIMED_OUT, "terminal": True}
    assert task.status == AgentTask.Status.TIMED_OUT
    assert task.failure == {"message": "agent exceeded its 30s timeout"}
    assert fake.stopped_ids == ["agent-task-timeout"]


def test_poll_times_out_task_stuck_before_external_id(org, env_spec, cluster, patch_spawner):
    """A lost spawn worker must not leave a provisioning row immortal."""
    fake = patch_spawner(
        _FakeSpawner(spawn_result=SpawnResult(external_id="unused"), status_sequence=[TaskStatus()])
    )
    task_pk = agent_stage._create_agent_task_sync(
        _params(org, environment_spec_slug=env_spec.slug, timeout_seconds=30)
    )
    task = AgentTask.objects.get(pk=task_pk)
    task.transition_to(AgentTask.Status.PROVISIONING)
    task.provisioning_at = timezone.now() - timedelta(seconds=31)
    task.save(update_fields=["provisioning_at", "updated_at", "version"])

    result = agent_stage._poll_agent_task_sync(task_pk)

    task.refresh_from_db()
    assert result == {"status": AgentTask.Status.TIMED_OUT, "terminal": True}
    assert task.status == AgentTask.Status.TIMED_OUT
    assert task.external_id == ""
    assert fake.stopped_ids == []


def test_failed_container_stop_does_not_claim_task_was_cancelled(org, env_spec, cluster, patch_spawner):
    fake = patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-delete-fails"),
            status_sequence=[TaskStatus(running=True)],
            stop_error=RuntimeError("kubernetes delete denied"),
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)
    task = AgentTask.objects.get(pk=task_pk)

    result = agent_stage._cancel_agent_task_sync(str(task.guid))

    task.refresh_from_db()
    assert result == {
        "ok": False,
        "status": AgentTask.Status.PROVISIONING,
        "error": "kubernetes delete denied",
    }
    assert task.status == AgentTask.Status.PROVISIONING
    assert fake.stopped_ids == ["agent-task-delete-fails"]


def test_cancel_terminal_task_is_noop(org, env_spec, cluster, patch_spawner):
    fake = patch_spawner(
        _FakeSpawner(spawn_result=SpawnResult(external_id="x"), status_sequence=[TaskStatus()])
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    task = AgentTask.objects.get(pk=task_pk)
    task.transition_to(AgentTask.Status.CANCELLED)

    agent_stage._cancel_agent_task_sync(str(task.guid))

    task.refresh_from_db()
    assert task.status == AgentTask.Status.CANCELLED
    # terminal task -> spawner.stop never called
    assert fake.stopped_ids == []


def test_cancel_unknown_task_is_noop(org, patch_spawner):
    patch_spawner(_FakeSpawner(spawn_result=SpawnResult(external_id="x"), status_sequence=[TaskStatus()]))
    # Must not raise.
    agent_stage._cancel_agent_task_sync("00000000-0000-0000-0000-000000000000")


# ---- cancel signal capture (#1217) -------------------------------------


def test_cancel_records_signal_interaction(org, env_spec, cluster, patch_spawner):
    """Cancelling a live task records a SIGNAL 'cancel' interaction attributed
    to the task — the P3 map's Signals hub source. The stop signal to the
    agent's container is the one genuine control-plane signal to an AgentTask."""
    from astrolift_agents.models import AgentInteraction

    patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-sig"),
            status_sequence=[TaskStatus(running=True)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)  # PROVISIONING + external_id set
    task = AgentTask.objects.get(pk=task_pk)

    agent_stage._cancel_agent_task_sync(str(task.guid))

    row = AgentInteraction.objects.get(agent_task=task, kind=AgentInteraction.Kind.SIGNAL)
    assert row.name == "cancel"
    assert row.status == "ok"
    assert row.organization_id == org.id
    assert row.detail["external_id"] == "agent-task-sig"
    assert row.detail["from_status"] == AgentTask.Status.PROVISIONING


def test_cancel_signal_capture_failure_does_not_break_cancel(
    org, env_spec, cluster, patch_spawner, monkeypatch
):
    """Signal capture is defensive: a failure recording the interaction must
    never stop the cancellation from completing."""
    from astrolift_agents import models as agent_models
    from astrolift_agents.models import AgentInteraction

    patch_spawner(
        _FakeSpawner(
            spawn_result=SpawnResult(external_id="agent-task-sigf"),
            status_sequence=[TaskStatus(running=True)],
        )
    )
    task_pk = agent_stage._create_agent_task_sync(_params(org, environment_spec_slug=env_spec.slug))
    agent_stage._spawn_agent_task_sync(task_pk)
    task = AgentTask.objects.get(pk=task_pk)

    def _boom(*args, **kwargs):
        raise RuntimeError("interaction store down")

    monkeypatch.setattr(agent_models, "record_interaction", _boom)

    agent_stage._cancel_agent_task_sync(str(task.guid))  # must not raise

    task.refresh_from_db()
    assert task.status == AgentTask.Status.CANCELLED
    assert AgentInteraction.objects.filter(agent_task=task, kind=AgentInteraction.Kind.SIGNAL).count() == 0
