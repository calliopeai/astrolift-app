"""Observed preview resource parsing, formatting and aggregation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.preview_resources import (
    PreviewAggregateResources,
    aggregate_pod_resources,
    format_cpu_cores,
    format_memory_bytes,
    parse_cpu_cores,
    parse_memory_bytes,
)

# ---- unit parsing ---------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", 0.0),
        ("100m", 0.1),
        ("500m", 0.5),
        ("1", 1.0),
        ("2.5", 2.5),
        (" 250m ", 0.25),
    ],
)
def test_parse_cpu_cores(raw, expected):
    assert parse_cpu_cores(raw) == pytest.approx(expected)


def test_parse_cpu_cores_rejects_garbage():
    with pytest.raises(ValueError):
        parse_cpu_cores("nope")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", 0.0),
        ("512Mi", 512 * 1024 * 1024),
        ("1Gi", 1024**3),
        ("100M", 100 * 1000 * 1000),
        ("2K", 2000),
    ],
)
def test_parse_memory_bytes(raw, expected):
    assert parse_memory_bytes(raw) == pytest.approx(expected)


def test_parse_memory_bytes_rejects_garbage():
    with pytest.raises(ValueError):
        parse_memory_bytes("nope")


# ---- formatting -----------------------------------------------------


@pytest.mark.parametrize(
    "cores,expected",
    [
        (0.0, "0 CPU"),
        (0.1, "100m CPU"),
        (0.25, "250m CPU"),
        (1.0, "1 CPU"),
        (1.5, "1.5 CPU"),
        (2.0, "2 CPU"),
    ],
)
def test_format_cpu_cores(cores, expected):
    assert format_cpu_cores(cores) == expected


@pytest.mark.parametrize(
    "byte_count,expected",
    [
        (0, "0 Mi"),
        (1024, "1.0 Ki"),
        (512 * 1024**2, "512 Mi"),
        (int(1.5 * 1024**3), "1.5 Gi"),
    ],
)
def test_format_memory_bytes(byte_count, expected):
    assert format_memory_bytes(byte_count) == expected


# ---- aggregation ----------------------------------------------------


def _fake_pod(*, phase: str = "Running", containers=None):
    containers = containers or []
    statuses = [
        SimpleNamespace(
            name=c.get("name", "main"),
            resources=SimpleNamespace(
                cpu_request=c.get("cpu_request", ""),
                cpu_limit=c.get("cpu_limit", ""),
                memory_request=c.get("memory_request", ""),
                memory_limit=c.get("memory_limit", ""),
            ),
        )
        for c in containers
    ]
    return SimpleNamespace(
        name=f"pod-{id(containers)}",
        phase=phase,
        container_statuses=statuses,
    )


def test_aggregate_sums_across_pods_and_containers():
    pods = [
        _fake_pod(
            containers=[
                {"cpu_request": "100m", "memory_request": "256Mi"},
                {"cpu_request": "50m", "memory_request": "128Mi"},
            ],
        ),
        _fake_pod(
            containers=[
                {"cpu_request": "200m", "memory_request": "512Mi"},
            ],
        ),
    ]
    agg = aggregate_pod_resources(pods)
    assert agg.pod_count == 2
    assert agg.cpu_cores == pytest.approx(0.35)
    assert agg.memory_bytes == pytest.approx((256 + 128 + 512) * 1024**2)


def test_aggregate_skips_terminal_pods():
    pods = [
        _fake_pod(containers=[{"cpu_request": "100m", "memory_request": "256Mi"}]),
        _fake_pod(
            phase="Succeeded",
            containers=[{"cpu_request": "999", "memory_request": "999Gi"}],
        ),
        _fake_pod(
            phase="Failed",
            containers=[{"cpu_request": "999", "memory_request": "999Gi"}],
        ),
    ]
    agg = aggregate_pod_resources(pods)
    assert agg.pod_count == 1
    assert agg.cpu_cores == pytest.approx(0.1)


def test_aggregate_tolerates_bad_resource_strings():
    """One broken pod shouldn't wipe the rest of the aggregate."""
    pods = [
        _fake_pod(containers=[{"cpu_request": "100m", "memory_request": "256Mi"}]),
        _fake_pod(containers=[{"cpu_request": "not-a-number", "memory_request": "256Mi"}]),
    ]
    agg = aggregate_pod_resources(pods)
    # Both pods count toward pod_count (they're both running) — only
    # the parseable resource numbers contribute to the sum.
    assert agg.pod_count == 2
    assert agg.cpu_cores == pytest.approx(0.1)


def test_aggregate_handles_no_pods():
    agg = aggregate_pod_resources([])
    assert agg == PreviewAggregateResources(cpu_cores=0.0, memory_bytes=0.0, pod_count=0)
