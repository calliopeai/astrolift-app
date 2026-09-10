"""The agent CI validator has to accept the manifest it was selected for (#1697).

The agent workflow template is chosen *because* the app has a
`kind = "agent"` workload. It then validated:

    if len(agents) != 1 or len(data.get('workloads', [])) != 1:
        raise SystemExit('selected manifest must declare exactly one agent workload')

so a root manifest with `web` + `refresh` + `brief` always failed — the
workflow contradicted the reason it was written, and the only way through
was to split the agent into `agents/<slug>/astrolift.toml`.

These tests extract the validator out of the rendered workflow and run it,
rather than asserting on the string. A validator that is only ever
asserted as text is exactly how a rule this wrong survives: the previous
test asserted the message and passed while the rule rejected every real
app+agent repo.
"""

from __future__ import annotations

import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from astrolift_scm.services.workflow_sync import render_astrolift_ci_workflow

MIXED = """
name = "quake"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true

[[workloads]]
name = "refresh"
kind = "cronjob"
schedule = "0 */4 * * *"

  [[workloads.containers]]
  name = "job"
  is_primary = true

[[workloads]]
name = "brief"
kind = "agent"
"""

AGENT_ONLY = """
name = "brief"

[[workloads]]
name = "brief"
kind = "agent"
"""

NO_AGENT = """
name = "quake"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
"""

TWO_AGENTS = """
name = "pair"

[[workloads]]
name = "brief"
kind = "agent"

[[workloads]]
name = "triage"
kind = "agent"
"""


def _app():
    return SimpleNamespace(
        slug="hello-app",
        name="Hello App",
        registry_repo_uri="",
        deploy_branch="main",
        default_branch="main",
        manifest_path="astrolift.toml",
        source_kind="github",
        is_agent=True,
    )


def _validator_source(body: str) -> str:
    """The python heredoc the workflow runs, lifted out verbatim."""

    match = re.search(r"python3 - <<'PY'\n(.*?)\n\s*PY\n", body, re.DOTALL)
    assert match, "validator heredoc not found in the rendered workflow"
    return "\n".join(line[10:] for line in match.group(1).splitlines())


def _run_validator(body: str, manifest_text: str, tmp_path, manifest_name="astrolift.toml"):
    path = tmp_path / manifest_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", _validator_source(body)],
        env={"ASTROLIFT_AGENT_MANIFEST": str(path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def body(settings):
    settings.PLATFORM_API_URL = "https://astro.example"
    return render_astrolift_ci_workflow(_app())


def test_a_mixed_app_and_agent_manifest_passes(body, tmp_path):
    """The bug: this failed, so an app+agent repo could not pass its own CI."""

    result = _run_validator(body, MIXED, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "validated agent package" in result.stdout
    assert "brief" in result.stdout


def test_a_standalone_agent_manifest_still_passes(body, tmp_path):
    result = _run_validator(body, AGENT_ONLY, tmp_path)

    assert result.returncode == 0, result.stderr


def test_two_agents_in_one_manifest_pass(body, tmp_path):
    """An app may declare more than one agent; nothing about the template
    depends on there being exactly one."""

    result = _run_validator(body, TWO_AGENTS, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "brief" in result.stdout
    assert "triage" in result.stdout


def test_a_manifest_with_no_agent_is_rejected(body, tmp_path):
    """The rule that is actually worth enforcing, and it says what it saw."""

    result = _run_validator(body, NO_AGENT, tmp_path)

    assert result.returncode != 0
    assert "declares no agent workload" in result.stderr
    assert "1 workload(s) found" in result.stderr


def test_a_missing_manifest_is_rejected(body, tmp_path):
    result = _run_validator(body, MIXED, tmp_path, manifest_name="astrolift.toml")
    assert result.returncode == 0

    missing = subprocess.run(
        [sys.executable, "-c", _validator_source(body)],
        env={"ASTROLIFT_AGENT_MANIFEST": str(tmp_path / "nope.toml"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    assert "agent manifest not found" in missing.stderr
