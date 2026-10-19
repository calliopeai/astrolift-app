"""Ordinary CI action runtimes; the CPU model workflow is qualified separately."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
_ORDINARY_WORKFLOWS = ("ci.yml", "build-publish.yml", "agent-host-conformance.yml")
_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")

# These action releases declare runs.using=node24 in their upstream action.yml.
# Adding an action requires reviewing its runtime, not merely counting its uses.
_REVIEWED_NODE24_ACTIONS = {
    "actions/checkout@v5",
    "actions/setup-node@v5",
    "actions/setup-python@v6",
    "actions/cache/restore@v5",
    "actions/cache/save@v5",
    "actions/upload-artifact@v6",
    "docker/setup-qemu-action@v4",
    "docker/setup-buildx-action@v4",
    "docker/login-action@v4",
    "docker/build-push-action@v7",
}


@pytest.mark.parametrize("workflow", _ORDINARY_WORKFLOWS)
def test_ordinary_workflows_use_reviewed_node24_actions(workflow: str) -> None:
    refs = {
        match.group(1)
        for line in (_WORKFLOWS / workflow).read_text(encoding="utf-8").splitlines()
        if (match := _USES.match(line))
    }
    assert refs, f"No action references found in {workflow}"
    assert refs <= _REVIEWED_NODE24_ACTIONS, (
        f"{workflow} uses actions without a reviewed Node 24 runtime: "
        f"{sorted(refs - _REVIEWED_NODE24_ACTIONS)}"
    )
