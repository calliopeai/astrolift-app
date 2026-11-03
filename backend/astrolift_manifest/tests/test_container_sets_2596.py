"""Container sets in one workload are checked at manifest sync time (#2596).

A duplicate container name or two primary containers used to get past the
parser and fail later, inside the apply dry-run, with no pointer back to the
manifest. Both are refused by ``parse_raw`` now. A valid two-container
workload (public web plus internal agent) still parses.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.parser import ManifestError, parse_raw

_HEADER = 'name = "hello"\n\n[[workloads]]\nname = "app"\nkind = "deployment"\n'


def _two_containers(
    second_name: str = "agent", *, first_primary: bool = True, second_primary: bool = False
) -> str:
    return (
        _HEADER
        + "\n[[workloads.containers]]\n"
        + 'name = "web"\nport = 3000\n'
        + f"is_primary = {str(first_primary).lower()}\n"
        + "\n[[workloads.containers]]\n"
        + f'name = "{second_name}"\nport = 4000\ncommand = ["python", "agent.py"]\n'
        + f"is_primary = {str(second_primary).lower()}\n"
        + '[workloads.containers.healthcheck]\nkind = "http"\nvalue = "/health"\n'
    )


def test_a_two_container_workload_with_distinct_names_parses():
    raw = parse_raw(_two_containers())

    names = [c.name for c in raw.workloads[0].containers]
    assert names == ["web", "agent"]


def test_a_repeated_container_name_is_refused_at_sync_time():
    with pytest.raises(ManifestError, match="declares container name\\(s\\) more than once: web"):
        parse_raw(_two_containers(second_name="web"))


def test_two_primary_containers_are_refused_at_sync_time():
    with pytest.raises(ManifestError, match="more than one container primary: web, agent"):
        parse_raw(_two_containers(first_primary=True, second_primary=True))
