"""Tests for the manifest TOML parser (#40, #114)."""

from __future__ import annotations

import pytest

from astrolift_manifest.parser import ManifestError, parse_raw


def test_parse_minimal_manifest():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""
    raw = parse_raw(toml)
    assert raw.name == "hello"
    assert len(raw.workloads) == 1
    w = raw.workloads[0]
    assert w.name == "web" and w.kind == "deployment"
    assert len(w.containers) == 1
    assert w.containers[0].name == "app" and w.containers[0].port == 8080


def test_parse_rejects_invalid_workload_kind():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "spaceship"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "kind" in str(exc.value).lower()


def test_parse_cronjob_requires_schedule():
    toml = """
name = "hello"

[[workloads]]
name = "nightly"
kind = "cronjob"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    # Path includes the offending key
    assert "schedule" in str(exc.value)


def test_parse_cronjob_with_schedule():
    toml = """
name = "hello"

[[workloads]]
name = "nightly"
kind = "cronjob"
schedule = "0 0 * * *"

  [[workloads.containers]]
  name = "job"
  is_primary = true
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].schedule == "0 0 * * *"
    # Default concurrency policy when key omitted (#427).
    assert raw.workloads[0].concurrency_policy == "forbid"


def test_parse_cronjob_concurrency_policy_round_trips():
    toml = """
name = "hello"

[[workloads]]
name = "nightly"
kind = "cronjob"
schedule = "0 0 * * *"
concurrency_policy = "queue"

  [[workloads.containers]]
  name = "job"
  is_primary = true
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].concurrency_policy == "queue"


def test_parse_cronjob_rejects_unknown_concurrency_policy():
    toml = """
name = "hello"

[[workloads]]
name = "nightly"
kind = "cronjob"
schedule = "0 0 * * *"
concurrency_policy = "spaceship"

  [[workloads.containers]]
  name = "job"
  is_primary = true
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "concurrency_policy" in str(exc.value)


def test_parse_jobs_shorthand_concurrency_policy_default():
    toml = """
name = "hello"

[[jobs]]
name = "nightly"
schedule = "0 0 * * *"
command = ["/bin/run-job"]
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].concurrency_policy == "forbid"


def test_parse_jobs_shorthand_concurrency_policy_override():
    toml = """
name = "hello"

[[jobs]]
name = "nightly"
schedule = "0 0 * * *"
command = ["/bin/run-job"]
concurrency_policy = "replace"
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].concurrency_policy == "replace"


def test_parse_managed_service_block():
    toml = """
name = "hello"

[[managed_services]]
kind = "postgres"
name = "main_db"
variant = "small"

[[managed_services]]
kind = "redis"
"""
    raw = parse_raw(toml)
    assert len(raw.managed_services) == 2
    assert raw.managed_services[0].kind == "postgres"
    assert raw.managed_services[0].name == "main_db"
    assert raw.managed_services[0].variant == "small"


def test_parse_invalid_toml_surfaces_path():
    # Stray bracket — TOML decoder error
    with pytest.raises(ManifestError) as exc:
        parse_raw("name = [unterminated")
    assert "invalid TOML" in str(exc.value)


def test_invalid_toml_carries_line_and_column():
    """tomllib errors expose a position; we surface it on
    ManifestError so editors can highlight the offending row."""
    bad = 'name = "hello"\n[[workloads]]\nkind = (\nname = "x"\n'
    with pytest.raises(ManifestError) as exc:
        parse_raw(bad)
    err = exc.value
    assert err.line is not None
    assert err.column is not None
    # The offending opening paren is on the 3rd line.
    assert err.line == 3


def test_locate_in_source_finds_leaf_key():
    """Best-effort locator for semantic errors (path → line/col)."""
    from astrolift_manifest.parser import locate_in_source

    text = 'name = "hello"\n\n[[workloads]]\nname = "web"\nkind = "spaceship"\n'
    line, col = locate_in_source(text, "workloads[0].kind")
    assert line == 5
    assert col == 1


def test_locate_in_source_returns_none_when_missing():
    from astrolift_manifest.parser import locate_in_source

    line, col = locate_in_source('name = "hello"', "workloads[0].kind")
    assert line is None
    assert col is None


# ---- [[jobs]] desugaring (#115) ---------------------------------------


def test_jobs_block_desugars_into_cronjob_workload():
    toml = """
name = "hello"

[[jobs]]
name = "nightly"
schedule = "0 0 * * *"
command = ["/bin/run-nightly"]
"""
    raw = parse_raw(toml)
    assert len(raw.workloads) == 1
    w = raw.workloads[0]
    assert w.name == "nightly"
    assert w.kind == "cronjob"
    assert w.schedule == "0 0 * * *"
    # Jobs are private (no ingress) and have a single primary container.
    assert w.is_public is False
    assert len(w.containers) == 1
    c = w.containers[0]
    assert c.is_primary is True
    assert c.command == ("/bin/run-nightly",)
    assert c.port == 0  # jobs don't expose ports
    assert c.healthcheck_kind == "none"  # no liveness/readiness


def test_jobs_block_requires_schedule():
    toml = """
name = "hello"

[[jobs]]
name = "nightly"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "schedule" in str(exc.value).lower()


def test_jobs_block_with_dockerfile_alias():
    """Spec lists ``dockerfile`` as the friendly key; the
    ContainerManifest uses ``dockerfile_path``. Desugaring should
    accept the friendly form."""
    toml = """
name = "hello"

[[jobs]]
name = "nightly"
schedule = "0 0 * * *"
dockerfile = "Dockerfile.jobs"
command = ["/bin/run"]
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].containers[0].dockerfile_path == "Dockerfile.jobs"


def test_jobs_and_workloads_can_coexist():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

[[jobs]]
name = "nightly"
schedule = "0 0 * * *"
command = ["/bin/run"]
"""
    raw = parse_raw(toml)
    names = sorted(w.name for w in raw.workloads)
    assert names == ["nightly", "web"]


def test_jobs_name_collision_with_workload_rejected():
    """A [[jobs]] entry sharing a name with a [[workloads]] entry
    would produce two Workload rows with the same name, which
    collides on the unique constraint and is silently confusing.
    Reject up front."""
    toml = """
name = "hello"

[[workloads]]
name = "shared"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

[[jobs]]
name = "shared"
schedule = "0 0 * * *"
command = ["/bin/run"]
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "collides" in str(exc.value)


def test_jobs_env_pairs_propagate():
    toml = """
name = "hello"

[[jobs]]
name = "nightly"
schedule = "0 0 * * *"
command = ["/bin/run"]
env = { LOG_LEVEL = "info", DRY_RUN = "0" }
"""
    raw = parse_raw(toml)
    env = dict(raw.workloads[0].containers[0].env)
    assert env == {"LOG_LEVEL": "info", "DRY_RUN": "0"}


# ---- [[tasks]] desugaring (#792) --------------------------------------


def test_tasks_block_desugars_into_task_workload():
    toml = """
name = "hello"

[[tasks]]
name = "migrate"
command = ["/bin/migrate"]
"""
    raw = parse_raw(toml)
    assert len(raw.workloads) == 1
    w = raw.workloads[0]
    assert w.name == "migrate"
    assert w.kind == "task"
    # One-shot: no schedule, never rescheduled.
    assert w.schedule is None
    # Tasks are private (no ingress) and have a single primary container.
    assert w.is_public is False
    assert len(w.containers) == 1
    c = w.containers[0]
    assert c.is_primary is True
    assert c.command == ("/bin/migrate",)
    assert c.port == 0  # tasks don't expose ports
    assert c.healthcheck_kind == "none"  # no liveness/readiness


def test_tasks_block_does_not_require_schedule():
    """Unlike [[jobs]] (which desugar to a cronjob and need a
    schedule), a [[tasks]] entry is one-shot and parses fine without
    one."""
    toml = """
name = "hello"

[[tasks]]
name = "migrate"
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].kind == "task"
    assert raw.workloads[0].schedule is None


def test_tasks_block_with_dockerfile_alias():
    toml = """
name = "hello"

[[tasks]]
name = "migrate"
dockerfile = "Dockerfile.migrate"
command = ["/bin/run"]
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].containers[0].dockerfile_path == "Dockerfile.migrate"


def test_tasks_env_pairs_propagate():
    toml = """
name = "hello"

[[tasks]]
name = "migrate"
command = ["/bin/run"]
env = { LOG_LEVEL = "info", DRY_RUN = "0" }
"""
    raw = parse_raw(toml)
    env = dict(raw.workloads[0].containers[0].env)
    assert env == {"LOG_LEVEL": "info", "DRY_RUN": "0"}


def test_tasks_workloads_and_jobs_can_coexist():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

[[jobs]]
name = "nightly"
schedule = "0 0 * * *"
command = ["/bin/run"]

[[tasks]]
name = "migrate"
command = ["/bin/migrate"]
"""
    raw = parse_raw(toml)
    names = sorted(w.name for w in raw.workloads)
    assert names == ["migrate", "nightly", "web"]
    kinds = {w.name: w.kind for w in raw.workloads}
    assert kinds == {"web": "deployment", "nightly": "cronjob", "migrate": "task"}


def test_tasks_name_collision_with_workload_rejected():
    toml = """
name = "hello"

[[workloads]]
name = "shared"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

[[tasks]]
name = "shared"
command = ["/bin/run"]
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "collides" in str(exc.value)


def test_tasks_name_collision_with_job_rejected():
    """A [[tasks]] entry sharing a name with a [[jobs]] entry would
    produce two Workload rows with the same name — reject it the same
    way a [[workloads]] collision is rejected."""
    toml = """
name = "hello"

[[jobs]]
name = "shared"
schedule = "0 0 * * *"
command = ["/bin/run"]

[[tasks]]
name = "shared"
command = ["/bin/run"]
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "collides" in str(exc.value)


def test_task_kind_accepted_on_bare_workload():
    """``kind = "task"`` is valid on a [[workloads]] entry, not just
    via the [[tasks]] shorthand."""
    toml = """
name = "hello"

[[workloads]]
name = "migrate"
kind = "task"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  command = ["/bin/migrate"]
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].kind == "task"
    assert raw.workloads[0].schedule is None


# ---- agent kind (#795) ------------------------------------------------


def test_agent_kind_accepted_with_dispatch_fields():
    """``kind = "agent"`` parses and its three dispatch fields round-trip."""
    toml = """
name = "hello"

[[workloads]]
name = "data-pipeline-agent"
kind = "agent"
replicas = 1
cpu_request = "500m"
memory_request = "1Gi"
max_retries = 8
tool_timeout_seconds = 120
result_ttl_hours = 24

  [[workloads.containers]]
  name = "agent"
  is_primary = true
  command = ["python", "-m", "myagent"]
"""
    raw = parse_raw(toml)
    w = raw.workloads[0]
    assert w.kind == "agent"
    assert w.max_retries == 8
    assert w.tool_timeout_seconds == 120
    assert w.result_ttl_hours == 24
    assert w.containers[0].command == ("python", "-m", "myagent")


def test_agent_dispatch_fields_default_when_omitted():
    """An agent that omits the dispatch keys gets the manifest-spec
    defaults (5 / 300 / 72), so a minimal agent manifest is valid."""
    toml = """
name = "hello"

[[workloads]]
name = "agent"
kind = "agent"

  [[workloads.containers]]
  name = "agent"
  is_primary = true
  command = ["python", "-m", "myagent"]
"""
    raw = parse_raw(toml)
    w = raw.workloads[0]
    assert w.max_retries == 5
    assert w.tool_timeout_seconds == 300
    assert w.result_ttl_hours == 72


def test_parse_missing_name_is_error():
    toml = """
[[workloads]]
name = "web"
kind = "deployment"
"""
    with pytest.raises(ManifestError):
        parse_raw(toml)


def test_parse_healthcheck_kind_validation():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true

    [workloads.containers.healthcheck]
    kind = "telepathy"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "healthcheck" in str(exc.value).lower()


# ---- workflow worker kind (#796) --------------------------------------


def test_parse_workflow_worker_fields():
    """A ``kind = "workflow"`` workload carries the Temporal worker
    config; the optional concurrency caps + namespace default when
    omitted."""
    toml = """
name = "hello"

[[workloads]]
name = "approval-worker"
kind = "workflow"
replicas = 2
workflow_type = "ApprovalWorkflow"
task_queue = "approvals"

  [[workloads.containers]]
  name = "worker"
  is_primary = true
  command = ["python", "-m", "myapp.workers.approvals"]
"""
    raw = parse_raw(toml)
    w = raw.workloads[0]
    assert w.kind == "workflow"
    assert w.replicas == 2
    assert w.workflow_type == "ApprovalWorkflow"
    assert w.task_queue == "approvals"
    # Optional fields take their documented defaults when omitted.
    assert w.temporal_namespace == "default"
    assert w.max_concurrent_activities == 20
    assert w.max_concurrent_workflows == 10


# ---- agent brief + skills (spec 38, Phase 1) --------------------------


def test_parse_brief_and_skills_local_and_named():
    """The spec's heterogeneous ``skills`` array — single-key inline tables
    (local) mixed with bare strings (named-global) — parses into typed
    ``SkillRef``s, and ``brief`` into a ``BriefRef``."""
    toml = """
name = "triage-agent"
brief = "brief/README.md"
skills = [
  { reviewer = "skills/reviewer" },
  { linter = "skills/linter" },
  "global-summarizer",
]

[[workloads]]
name = "triage-agent"
kind = "agent"

  [[workloads.containers]]
  name = "agent"
  is_primary = true
  command = ["python", "-m", "agent"]
"""
    raw = parse_raw(toml)
    assert raw.brief is not None
    assert raw.brief.path == "brief/README.md"
    assert len(raw.skills) == 3
    # Order is preserved from the array.
    reviewer, linter, summarizer = raw.skills
    assert (reviewer.name, reviewer.path, reviewer.is_local) == ("reviewer", "skills/reviewer", True)
    assert (linter.name, linter.path, linter.is_local) == ("linter", "skills/linter", True)
    assert (summarizer.name, summarizer.path, summarizer.is_local) == ("global-summarizer", None, False)


def test_parse_brief_and_skills_default_when_absent():
    """A manifest with neither key parses to ``brief=None`` / ``skills=()``,
    so non-agent manifests round-trip unchanged."""
    toml = """
name = "web"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""
    raw = parse_raw(toml)
    assert raw.brief is None
    assert raw.skills == ()


def test_parse_brief_must_be_string():
    toml = """
name = "agent"
brief = 42
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert exc.value.path == "brief"
    assert "brief" in str(exc.value).lower()


def test_parse_skills_must_be_list():
    toml = """
name = "agent"
skills = "not-a-list"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert exc.value.path == "skills"


def test_parse_skill_entry_int_rejected():
    """A non-string / non-table entry is a malformed skill and surfaces an
    indexed path pointing at the offending row."""
    toml = """
name = "agent"
skills = [ 42 ]
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert exc.value.path == "skills[0]"


def test_parse_skill_local_entry_multikey_rejected():
    """A local skill table must have exactly one key (name = path); a
    two-key table is ambiguous and rejected with a good path."""
    toml = """
name = "agent"
skills = [ { reviewer = "a", linter = "b" } ]
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert exc.value.path == "skills[0]"
    assert "single-key" in str(exc.value)


def test_parse_skill_local_entry_empty_path_rejected():
    toml = """
name = "agent"
skills = [ { reviewer = "" } ]
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert exc.value.path == "skills[0]"
    assert "path" in str(exc.value).lower()


def test_parse_skill_named_entry_empty_rejected():
    toml = """
name = "agent"
skills = [ "" ]
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert exc.value.path == "skills[0]"


def test_parse_skill_duplicate_name_rejected():
    """Two skill entries resolving to the same name would upsert onto one
    Skill row at registration — reject the ambiguity at parse time."""
    toml = """
name = "agent"
skills = [
  { reviewer = "skills/reviewer" },
  "reviewer",
]
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert exc.value.path == "skills[1]"
    assert "duplicate" in str(exc.value)


def test_parse_workflow_worker_optional_fields_round_trip():
    """The optional caps + namespace are read when present."""
    toml = """
name = "hello"

[[workloads]]
name = "approval-worker"
kind = "workflow"
workflow_type = "ApprovalWorkflow"
task_queue = "approvals"
temporal_namespace = "prod"
max_concurrent_activities = 50
max_concurrent_workflows = 25

  [[workloads.containers]]
  name = "worker"
  is_primary = true
"""
    raw = parse_raw(toml)
    w = raw.workloads[0]
    assert w.temporal_namespace == "prod"
    assert w.max_concurrent_activities == 50
    assert w.max_concurrent_workflows == 25


def test_parse_workflow_worker_requires_workflow_type():
    """``workflow_type`` is required for a workflow workload — a worker
    that doesn't know which workflow type to register is a silent
    no-op, so reject it at parse time."""
    toml = """
name = "hello"

[[workloads]]
name = "approval-worker"
kind = "workflow"
task_queue = "approvals"

  [[workloads.containers]]
  name = "worker"
  is_primary = true
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "workflow_type" in str(exc.value)


def test_parse_workflow_worker_requires_task_queue():
    """``task_queue`` is required for a workflow workload — a worker
    with no queue to poll never receives work."""
    toml = """
name = "hello"

[[workloads]]
name = "approval-worker"
kind = "workflow"
workflow_type = "ApprovalWorkflow"

  [[workloads.containers]]
  name = "worker"
  is_primary = true
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "task_queue" in str(exc.value)
