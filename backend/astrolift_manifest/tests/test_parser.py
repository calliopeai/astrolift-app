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


def test_parse_managed_service_lifecycle_contract():
    raw = parse_raw(
        """
name = "hello"

[[workloads]]
name = "api"
kind = "deployment"

  [[workloads.containers]]
  name = "api"
  image_ref = "example/api:sha-123"

[[managed_services]]
kind = "mssql"
name = "records"
variant = "azure_sql_database"
owner_scope = "project"
environment = "production"
bind_workloads = ["api"]
size = "medium"
isolation = "dedicated"
auth_mode = "iam"
deletion_policy = "retain"

  [managed_services.networking]
  private_endpoint = true

  [managed_services.retention]
  backup_retention_days = 14

  [managed_services.backup]
  schedule = "0 2 * * *"

  [managed_services.restore]
  snapshot_id = "snapshot-123"
  source_handle = "/subscriptions/example/databases/source"

  [managed_services.extensions]
  sku_name = "GP_S_Gen5_2"
"""
    )

    service = raw.managed_services[0]
    assert service.owner_scope == "project"
    assert service.bind_workloads == ("api",)
    assert service.size == "medium"
    assert service.networking == {"private_endpoint": True}
    assert service.restore["snapshot_id"] == "snapshot-123"


def test_managed_service_delete_policy_requires_explicit_confirmation():
    with pytest.raises(ManifestError, match="confirm_delete=true"):
        parse_raw(
            """
name = "hello"
[[managed_services]]
kind = "postgres"
deletion_policy = "delete"
"""
        )


def test_managed_service_portable_extensions_are_normalized():
    raw = parse_raw(
        """
name = "extensions"

[[managed_services]]
kind = "postgres"
extensions = ["vector", "pg_trgm", "vector"]
"""
    )

    assert raw.managed_services[0].extensions == {"desired_extensions": ["vector", "pg_trgm"]}


def test_managed_service_rejects_inline_secret_material():
    with pytest.raises(ManifestError, match="inline secret"):
        parse_raw(
            """
name = "hello"
[[managed_services]]
kind = "postgres"
[managed_services.config]
password = "do-not-store-me"
"""
        )


def test_managed_service_binding_selector_must_name_a_workload():
    with pytest.raises(ManifestError, match="unknown workload"):
        parse_raw(
            """
name = "hello"
[[managed_services]]
kind = "postgres"
bind_workloads = ["missing"]
"""
        )


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


def test_parse_rejects_duplicate_workload_names():
    """Two [[workloads]] sharing a name would render onto one Deployment
    and the set-based dedup would silently drop one. Reject at parse time,
    flagging the offending row (#1033)."""
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

[[workloads]]
name = "web"
kind = "deployment"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "duplicate workload name" in str(exc.value)
    assert exc.value.path == "workloads[1].name"


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


def test_agent_run_family_defaults_to_task():
    """An agent that omits ``run_family`` defaults to ``task`` (#1027)."""
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
    w = parse_raw(toml).workloads[0]
    assert w.run_family == "task"


def test_agent_run_family_service_round_trips():
    toml = """
name = "hello"

[[workloads]]
name = "agent"
kind = "agent"
run_family = "service"

  [[workloads.containers]]
  name = "agent"
  is_primary = true
  command = ["python", "-m", "myagent"]
"""
    w = parse_raw(toml).workloads[0]
    assert w.run_family == "service"


def test_agent_run_family_invalid_rejected():
    toml = """
name = "hello"

[[workloads]]
name = "agent"
kind = "agent"
run_family = "daemon"

  [[workloads.containers]]
  name = "agent"
  is_primary = true
  command = ["python", "-m", "myagent"]
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert exc.value.path == "workloads[0].run_family"


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
    (local) mixed with bare strings (catalogue) — parses into typed
    ``SkillRef``s, and ``brief`` into a ``BriefRef``. ``is_local`` stays a
    valid (derived) view of ``kind`` for the Phase-3 callers."""
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
    assert (reviewer.name, reviewer.path, reviewer.kind, reviewer.is_local) == (
        "reviewer",
        "skills/reviewer",
        "local",
        True,
    )
    assert (linter.name, linter.path, linter.kind, linter.is_local) == (
        "linter",
        "skills/linter",
        "local",
        True,
    )
    assert (summarizer.name, summarizer.path, summarizer.kind, summarizer.is_local) == (
        "global-summarizer",
        None,
        "catalogue",
        False,
    )


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


# ---------------------------------------------------------------------------
# spec 39d — org-repo skill refs ("<alias>/<skill-path>@<ref>")
# ---------------------------------------------------------------------------


def _agent_with_skills(*entries: str) -> str:
    body = ",\n  ".join(entries)
    return f"""
name = "triage-agent"
skills = [
  {body}
]

[[workloads]]
name = "triage-agent"
kind = "agent"

  [[workloads.containers]]
  name = "agent"
  is_primary = true
"""


def test_parse_skill_org_repo_ref_with_pin():
    """A bare string containing a ``/`` is an org-repo ref; the first segment
    is the alias, the rest is the skill path, and ``@ref`` is the pin."""
    raw = parse_raw(_agent_with_skills('"acme/dev-skills/pr-review@v2"'))
    (s,) = raw.skills
    assert s.kind == "org_repo"
    assert s.is_local is False
    assert s.repo_alias == "acme"
    assert s.skill_subpath == "dev-skills/pr-review"
    assert s.ref == "v2"
    # Skill name = the last path segment (the agentskills.io folder name).
    assert s.name == "pr-review"


def test_parse_skill_org_repo_ref_without_pin():
    """``@ref`` is optional on an org-repo ref — ``ref`` is empty (the repo's
    default_ref is used at resolution time)."""
    raw = parse_raw(_agent_with_skills('"acme/pr-review"'))
    (s,) = raw.skills
    assert s.kind == "org_repo"
    assert s.repo_alias == "acme"
    assert s.skill_subpath == "pr-review"
    assert s.ref == ""
    assert s.name == "pr-review"


def test_parse_skill_catalogue_ref_splits_pin_off_name():
    """A bare name with no ``/`` is a catalogue ref; an ``@`` pin is split off
    into ``ref`` so ``name`` stays the clean catalogue folder name."""
    raw = parse_raw(_agent_with_skills('"pr-review@1.2.0"'))
    (s,) = raw.skills
    assert s.kind == "catalogue"
    assert s.name == "pr-review"
    assert s.ref == "1.2.0"


def test_parse_skill_bare_catalogue_ref_no_pin():
    raw = parse_raw(_agent_with_skills('"pr-review"'))
    (s,) = raw.skills
    assert s.kind == "catalogue"
    assert s.name == "pr-review"
    assert s.ref == ""


def test_parse_skill_local_dot_slash_string():
    """A leading ``./`` string is a LOCAL ref (a path in the agent's repo),
    not an org-repo ref — even though it contains a ``/``."""
    raw = parse_raw(_agent_with_skills('"./skills/custom-review"'))
    (s,) = raw.skills
    assert s.kind == "local"
    assert s.is_local is True
    assert s.path == "./skills/custom-review"
    assert s.name == "custom-review"


def test_parse_skill_local_table_still_local():
    """The table form stays local (regression: kind discriminator)."""
    raw = parse_raw(_agent_with_skills('{ reviewer = "skills/reviewer" }'))
    (s,) = raw.skills
    assert s.kind == "local"
    assert s.is_local is True
    assert s.path == "skills/reviewer"


def test_parse_skill_org_repo_empty_pin_rejected():
    """A trailing ``@`` (empty pin) is an operator paste-error → ManifestError."""
    with pytest.raises(ManifestError) as exc:
        parse_raw(_agent_with_skills('"acme/pr-review@"'))
    assert exc.value.path == "skills[0]"
    assert "empty" in str(exc.value).lower()


def test_parse_skill_double_pin_rejected():
    """More than one ``@`` is ambiguous → ManifestError."""
    with pytest.raises(ManifestError) as exc:
        parse_raw(_agent_with_skills('"acme/pr-review@a@b"'))
    assert exc.value.path == "skills[0]"
    assert "@" in str(exc.value)


def test_parse_skill_org_repo_empty_alias_rejected():
    """A leading ``/`` means an empty alias → ManifestError."""
    with pytest.raises(ManifestError) as exc:
        parse_raw(_agent_with_skills('"/pr-review"'))
    assert exc.value.path == "skills[0]"
    assert "alias" in str(exc.value).lower()


def test_parse_skill_org_repo_empty_path_rejected():
    """A trailing ``/`` (alias with no skill path) → ManifestError."""
    with pytest.raises(ManifestError) as exc:
        parse_raw(_agent_with_skills('"acme/"'))
    assert exc.value.path == "skills[0]"
    assert "path" in str(exc.value).lower()


def test_parse_skill_org_repo_deep_subpath():
    """A multi-segment skill path keeps the full subpath; name = last segment."""
    raw = parse_raw(_agent_with_skills('"acme/team/skills/pr-review@main"'))
    (s,) = raw.skills
    assert s.kind == "org_repo"
    assert s.repo_alias == "acme"
    assert s.skill_subpath == "team/skills/pr-review"
    assert s.name == "pr-review"
    assert s.ref == "main"


def test_parse_inline_brief_skills_table_is_ignored_not_rejected():
    """An agent brief uses ``[skills.<slug>]`` inline tables (system_prompt +
    tools), consumed by the brief assembler at dispatch — not the spec-38
    top-level skill *reference* list. The workload/manifest parser must accept
    such a manifest (skills empty) rather than raising 'skills must be a list',
    so the agent still registers. Regression for agent-brief registration."""
    toml = """
name = "emr-bug-triage"

[[workloads]]
name = "emr-bug-triage"
kind = "agent"

  [[workloads.containers]]
  name = "emr-bug-triage"
  is_primary = true
  image_ref = "ecr.example/agent:latest"

[skills.emr-bug-triage]
system_prompt = "You triage bugs."
tools = ["jira-create"]
"""
    raw = parse_raw(toml)
    assert raw.name == "emr-bug-triage"
    assert len(raw.workloads) == 1
    assert raw.workloads[0].kind == "agent"
    # Inline brief tables are the brief assembler's concern — not manifest refs.
    assert raw.skills == ()


# --- container-level dockerfile_path / build_context safety (#1756
# adversarial review, H3 / M1) -------------------------------------------
#
# Both end up as --dockerfile / --context-sub-path arguments to the
# in-cluster kaniko build (build_image.py), verbatim. An absolute path
# is never valid input to that resolution, whatever the base ends up
# being, so it's rejected here rather than reaching the build unchecked.
# Only one image is ever built for an app, so two containers declaring
# different non-default values would have one silently ignored, the
# same silent-drop class this feature exists to close.


def test_parse_rejects_an_absolute_container_dockerfile_path():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  dockerfile_path = "/etc/passwd"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "dockerfile_path" in str(exc.value)


def test_parse_rejects_an_absolute_container_build_context():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  build_context = "/abs"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "build_context" in str(exc.value)


def test_parse_rejects_conflicting_dockerfile_paths_across_containers():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "a"
  is_primary = true
  dockerfile_path = "Dockerfile.a"

  [[workloads.containers]]
  name = "b"
  dockerfile_path = "Dockerfile.b"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "dockerfile_path" in str(exc.value)


def test_parse_rejects_conflicting_build_contexts_across_containers():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "a"
  is_primary = true
  build_context = "apps/a"

  [[workloads.containers]]
  name = "b"
  build_context = "apps/b"
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "build_context" in str(exc.value)


def test_parse_allows_only_one_container_to_customize_build_fields():
    """One container customizing a field is not a conflict -- the
    other container simply reads back at the parser's own default."""
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "a"
  is_primary = true
  dockerfile_path = "Dockerfile.a"

  [[workloads.containers]]
  name = "b"
"""
    raw = parse_raw(toml)
    containers = raw.workloads[0].containers
    assert containers[0].dockerfile_path == "Dockerfile.a"
    assert containers[1].dockerfile_path == "Dockerfile"


# ---- the masked-read placeholder (#1920) ---------------------------

_STAGED_WITH_PLACEHOLDER = """\
name = "hello"

[env]
API_KEY = "[ASTROLIFT_REDACTED_ENV_VALUE]"
LOG_LEVEL = "info"

[[workloads]]
name = "web"
kind = "deployment"
"""


def test_parse_raw_refuses_the_masked_env_placeholder() -> None:
    """A staged draft saved from a masked read with its values never put
    back must not be applied or deployed: applyStagedManifest, repo sync
    and the deploy renderer all parse through here, and applying it would
    replace the stored secret with the placeholder."""
    with pytest.raises(ManifestError) as exc_info:
        parse_raw(_STAGED_WITH_PLACEHOLDER)
    assert exc_info.value.path == "env.API_KEY"
    assert "masked placeholder" in str(exc_info.value)


def test_parse_raw_takes_the_placeholder_text_inside_a_real_value() -> None:
    raw = parse_raw(_STAGED_WITH_PLACEHOLDER.replace('"[ASTROLIFT', '"prefix-[ASTROLIFT'))
    assert raw.raw["env"]["API_KEY"] == "prefix-[ASTROLIFT_REDACTED_ENV_VALUE]"
