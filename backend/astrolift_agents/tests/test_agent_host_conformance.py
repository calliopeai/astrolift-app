"""Snapshots must match the reducer clients run, including lifecycle cleanup."""

import json
from pathlib import Path

import pytest

from astrolift_agents.services.agent_host_projection import PROTOCOL_VERSION, reduce_chat

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ahp-chat.json").read_text())


@pytest.mark.parametrize("scenario", FIXTURE["scenarios"], ids=lambda s: s["name"])
def test_chat_projection_matches_pinned_upstream(scenario):
    assert FIXTURE["protocolVersion"] == PROTOCOL_VERSION
    state = scenario["initial"]
    for step in scenario["steps"]:
        state = reduce_chat(state, step["action"])
        assert state == step["expected"]
