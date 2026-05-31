"""Tests for PipelineRunWorkflow pure helpers (#68).

The topological_sort helper is a pure Python function with no Django or
Temporal dependencies, so these run without any platform context.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.workflows.pipeline_run import topological_sort

# ---------------------------------------------------------------------------
# Helper to build minimal job dicts
# ---------------------------------------------------------------------------


def _job(job_id: str, needs: list[str] | None = None) -> dict:
    return {"job_id": job_id, "needs": needs or []}


# ---------------------------------------------------------------------------
# Linear chain: A → B → C
# ---------------------------------------------------------------------------


def test_linear_chain_returns_three_tiers():
    """A → B → C must produce three sequential tiers."""
    jobs = [
        _job("A"),
        _job("B", ["A"]),
        _job("C", ["B"]),
    ]
    tiers = topological_sort(jobs)
    assert len(tiers) == 3
    assert [j["job_id"] for j in tiers[0]] == ["A"]
    assert [j["job_id"] for j in tiers[1]] == ["B"]
    assert [j["job_id"] for j in tiers[2]] == ["C"]


# ---------------------------------------------------------------------------
# Diamond: A → B, C → D
# ---------------------------------------------------------------------------


def test_diamond_puts_b_and_c_in_same_tier():
    """Diamond dependency: A → {B, C} → D must put B + C in the same tier."""
    jobs = [
        _job("A"),
        _job("B", ["A"]),
        _job("C", ["A"]),
        _job("D", ["B", "C"]),
    ]
    tiers = topological_sort(jobs)
    assert len(tiers) == 3
    assert [j["job_id"] for j in tiers[0]] == ["A"]
    tier1_ids = sorted(j["job_id"] for j in tiers[1])
    assert tier1_ids == ["B", "C"]
    assert [j["job_id"] for j in tiers[2]] == ["D"]


# ---------------------------------------------------------------------------
# No dependencies — all jobs in one tier
# ---------------------------------------------------------------------------


def test_no_dependencies_single_tier():
    """Jobs with no needs must all land in a single tier."""
    jobs = [_job("X"), _job("Y"), _job("Z")]
    tiers = topological_sort(jobs)
    assert len(tiers) == 1
    assert sorted(j["job_id"] for j in tiers[0]) == ["X", "Y", "Z"]


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_empty_jobs_returns_empty_tiers():
    assert topological_sort([]) == []


# ---------------------------------------------------------------------------
# Single job
# ---------------------------------------------------------------------------


def test_single_job_one_tier():
    tiers = topological_sort([_job("solo")])
    assert len(tiers) == 1
    assert tiers[0][0]["job_id"] == "solo"


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_cycle_raises_value_error():
    """A → B → A is a cycle and must raise ValueError."""
    jobs = [
        _job("A", ["B"]),
        _job("B", ["A"]),
    ]
    with pytest.raises(ValueError, match="cycle"):
        topological_sort(jobs)


def test_self_loop_raises_value_error():
    """A job that depends on itself is a degenerate cycle."""
    jobs = [_job("A", ["A"])]
    with pytest.raises(ValueError, match="cycle"):
        topological_sort(jobs)


def test_unknown_dependency_raises_value_error():
    """A needs dep that doesn't exist in the job list."""
    jobs = [_job("A", ["nonexistent"])]
    with pytest.raises(ValueError, match="no such job"):
        topological_sort(jobs)


# ---------------------------------------------------------------------------
# Wide flat graph — multiple independent chains in parallel
# ---------------------------------------------------------------------------


def test_two_independent_chains():
    """Two independent linear chains run in parallel within each tier."""
    # chain1: P → Q
    # chain2: X → Y
    jobs = [
        _job("P"),
        _job("Q", ["P"]),
        _job("X"),
        _job("Y", ["X"]),
    ]
    tiers = topological_sort(jobs)
    assert len(tiers) == 2
    tier0_ids = sorted(j["job_id"] for j in tiers[0])
    assert tier0_ids == ["P", "X"]
    tier1_ids = sorted(j["job_id"] for j in tiers[1])
    assert tier1_ids == ["Q", "Y"]


# ---------------------------------------------------------------------------
# Fan-in: multiple roots converge into one final job
# ---------------------------------------------------------------------------


def test_fan_in_topology():
    """A, B, C (independent) → D is two tiers."""
    jobs = [
        _job("A"),
        _job("B"),
        _job("C"),
        _job("D", ["A", "B", "C"]),
    ]
    tiers = topological_sort(jobs)
    assert len(tiers) == 2
    assert sorted(j["job_id"] for j in tiers[0]) == ["A", "B", "C"]
    assert [j["job_id"] for j in tiers[1]] == ["D"]
