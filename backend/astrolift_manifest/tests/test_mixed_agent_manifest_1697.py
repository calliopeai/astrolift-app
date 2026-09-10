"""A root manifest with an agent among its workloads says why it is skipped (#1697).

The agent scan keeps a manifest only when it declares exactly one workload
of kind ``agent`` -- an ``agents/<slug>/astrolift.toml`` describes one
agent, and a root manifest with mixed workloads is an app. The rule is
right; what was wrong is that a near miss went out silently, so
``registerAgentRepo`` on a repo whose root manifest declares
``web`` + ``refresh`` + an agent reported "no agent manifests found" and
the operator had nothing to act on.
"""

from __future__ import annotations

from astrolift_manifest.discover import (
    scan_agent_manifests,
    scan_agent_manifests_with_skips,
)

_AGENT_ONLY = """
name = "triage"

[[workloads]]
name = "brief"
kind = "agent"
"""

_MIXED = """
name = "emr"

[[workloads]]
name = "web"
kind = "deployment"

[[workloads]]
name = "refresh"
kind = "cronjob"
schedule = "0 * * * *"

[[workloads]]
name = "brief"
kind = "agent"
"""

_TWO_AGENTS = """
name = "pair"

[[workloads]]
name = "triage"
kind = "agent"

[[workloads]]
name = "review"
kind = "agent"
"""

_APP_ONLY = """
name = "plain"

[[workloads]]
name = "web"
kind = "deployment"
"""


def test_an_agent_only_manifest_is_kept():
    found, skipped = scan_agent_manifests_with_skips({"agents/triage/astrolift.toml": _AGENT_ONLY})
    assert [row.slug for row in found] == ["brief"]
    assert skipped == []


def test_a_mixed_manifest_is_skipped_and_says_why():
    found, skipped = scan_agent_manifests_with_skips({"astrolift.toml": _MIXED})

    assert found == []
    assert len(skipped) == 1
    reason = skipped[0].reason
    assert skipped[0].manifest_path == "astrolift.toml"
    assert "brief" in reason
    assert "web" in reason and "refresh" in reason
    assert "agents/<slug>/astrolift.toml" in reason


def test_two_agents_in_one_manifest_is_skipped_and_says_why():
    _found, skipped = scan_agent_manifests_with_skips({"astrolift.toml": _TWO_AGENTS})

    assert len(skipped) == 1
    assert "2 agent workloads" in skipped[0].reason
    assert "triage" in skipped[0].reason and "review" in skipped[0].reason


def test_a_plain_app_manifest_is_not_reported_as_a_near_miss():
    """Most repos have a root manifest with no agent in it. Reporting each
    one as a skip would bury the line that matters."""
    found, skipped = scan_agent_manifests_with_skips({"astrolift.toml": _APP_ONLY})

    assert found == []
    assert skipped == []


def test_unparseable_content_is_not_reported_as_a_near_miss():
    _found, skipped = scan_agent_manifests_with_skips({"astrolift.toml": "not [ toml"})

    assert skipped == []


def test_the_list_only_scan_is_unchanged():
    """Existing callers keep the old shape."""
    assert [row.slug for row in scan_agent_manifests({"astrolift.toml": _AGENT_ONLY})] == ["brief"]
    assert scan_agent_manifests({"astrolift.toml": _MIXED}) == []
