"""A workload block is a contract, and unknown keys break it (#1680).

`[[workloads]]` accepted anything. A manifest could declare

    run_mode = "schedule"
    run_cron_expression = "0 * * * *"
    totally_not_a_field = "ignored?"

and parse clean with none of them set. The sibling `_parse_managed_service`
has always been strict, and the asymmetry is the whole problem: the
operator learned nothing until the workload did not behave.

`run_mode` was the case that surfaced it. `Workload.RunMode` has had
once / loop / schedule / trigger / persistent since #795, and
`WorkloadManifest` had neither it nor `run_cron_expression` -- so an
agent's trigger mode could be set only through the registration API and
never declared in the repo that defines the agent. Spec 42 §5 specifies
the reverse.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.parser import ManifestError, parse_raw

_BASE = """
name = "hello"

[[workloads]]
name = "w"
kind = "agent"
"""


def _agent(*extra_lines: str) -> str:
    return _BASE + "".join(f"{line}\n" for line in extra_lines)


def _parse(toml: str):
    return parse_raw(toml).workloads[0]


# ---- unknown keys are refused ----------------------------------------


def test_an_unknown_workload_key_is_refused():
    """The bug: this parsed clean."""

    with pytest.raises(ManifestError, match="unknown workload field"):
        parse_raw(_agent('totally_not_a_field = "ignored?"'))


def test_the_message_names_every_unknown_key():
    with pytest.raises(ManifestError) as excinfo:
        parse_raw(_agent('zebra = "z"', 'apple = "a"'))

    # Sorted, so the message is stable across dict ordering.
    assert "apple, zebra" in str(excinfo.value)


def test_a_typo_of_a_real_key_is_refused():
    """The case that costs the most time: it looks right."""

    with pytest.raises(ManifestError, match="unknown workload field"):
        parse_raw(_agent("replica = 3"))


def test_every_documented_key_still_parses():
    """The guard must not reject a manifest that was valid yesterday."""

    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true
replicas = 2
cpu_request = "100m"
cpu_limit = "1"
memory_request = "128Mi"
memory_limit = "1Gi"
hpa_min = 1
hpa_max = 5
hpa_target_cpu_pct = 70
storage_class = "gp3"
storage_size = "10Gi"
fs_group = 1000

  [workloads.metrics]
  port = 9090

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""
    workload = _parse(toml)
    assert workload.replicas == 2
    assert workload.metrics_port == 9090


# ---- run_mode ---------------------------------------------------------


def test_run_mode_matches_the_model():
    """The parser keeps a literal so it stays Django-free; this is what
    stops the two drifting."""

    from astrolift_manifest.parser import _VALID_RUN_MODES
    from astrolift_registry.models import Workload

    assert _VALID_RUN_MODES == {m.value for m in Workload.RunMode}


@pytest.mark.parametrize("mode", ["once", "loop", "trigger", "persistent"])
def test_a_declared_run_mode_survives_the_parse(mode):
    assert _parse(_agent(f'run_mode = "{mode}"')).run_mode == mode


def test_schedule_mode_carries_its_cron():
    workload = _parse(_agent('run_mode = "schedule"', 'run_cron_expression = "0 * * * *"'))
    assert workload.run_mode == "schedule"
    assert workload.run_cron_expression == "0 * * * *"


def test_an_undeclared_run_mode_is_empty_not_a_default():
    """Empty means "not declared", so registration leaves whatever the
    model default or an operator already set."""

    assert _parse(_agent()).run_mode == ""


def test_an_invalid_run_mode_is_refused():
    with pytest.raises(ManifestError, match="run_mode must be one of"):
        parse_raw(_agent('run_mode = "whenever"'))


def test_schedule_without_a_cron_is_refused():
    with pytest.raises(ManifestError, match="requires a 'run_cron_expression'"):
        parse_raw(_agent('run_mode = "schedule"'))


def test_a_malformed_cron_is_refused_by_the_platform_validator():
    """A manifest must not be able to declare a schedule the scheduler
    will later refuse."""

    with pytest.raises(ManifestError, match="not a valid cron expression"):
        parse_raw(_agent('run_mode = "schedule"', 'run_cron_expression = "0 * * *"'))


def test_a_cron_without_schedule_mode_is_refused():
    """Silently ignoring it is the failure this issue is about."""

    with pytest.raises(ManifestError, match="only read for"):
        parse_raw(_agent('run_mode = "once"', 'run_cron_expression = "0 * * * *"'))


def test_run_mode_on_a_non_agent_workload_is_refused():
    """Bound to kind the way `schedule` is bound to cronjob: declaring it
    on a workload that can never dispatch is a mistake worth naming."""

    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
run_mode = "schedule"

  [[workloads.containers]]
  name = "app"
  is_primary = true
"""
    with pytest.raises(ManifestError, match="agent workloads only"):
        parse_raw(toml)


# ---- containers are strict too ----------------------------------------


def test_an_unknown_container_key_is_refused():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  prot = 8080
"""
    with pytest.raises(ManifestError, match="unknown container field"):
        parse_raw(toml)


def test_every_documented_container_key_still_parses():
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
  image_ref = "acme/app:1"
  command = ["python"]
  args = ["app.py"]
  cpu_request = "100m"
  cpu_limit = "1"
  memory_request = "128Mi"
  memory_limit = "1Gi"
  dockerfile = "Dockerfile"
  build_context = "."
  env = { FOO = "bar" }

    [workloads.containers.healthcheck]
    kind = "http"
    value = "/healthz"
"""
    container = parse_raw(toml).workloads[0].containers[0]
    assert container.port == 8080
    assert container.command == ("python",)
