"""Unit tests for the agent-manifest scanner (spec 33, PR-3).

``scan_agent_manifests`` walks a fetched repo tree (a ``{path: contents}``
map) and returns the agent manifests it finds, in one of two layouts:

  * monorepo — ``agents/<slug>/astrolift.toml``;
  * single   — a root ``astrolift.toml``.

These tests pin the glob + parse rules without a database or network: the
scanner is pure over the file map, and the zipball unpacker is pure over
archive bytes. The DB-backed registration/idempotency/tenancy cases live in
``astrolift_registry/tests/test_agent_repo_discovery.py``.
"""

from __future__ import annotations

import io
import zipfile

from astrolift_manifest.discover import scan_agent_manifests


def _agent_toml(name: str, *, kind: str = "agent") -> str:
    return (
        f'name = "{name}"\n'
        "[[workloads]]\n"
        f'name = "{name}"\n'
        f'kind = "{kind}"\n'
        "[[workloads.containers]]\n"
        f'name = "{name}"\n'
        "is_primary = true\n"
        'image_ref = "ecr.example/agent:latest"\n'
    )


_WEB_TOML = (
    'name = "web"\n'
    "[[workloads]]\n"
    'name = "web"\n'
    'kind = "deployment"\n'
    "is_public = true\n"
    "[[workloads.containers]]\n"
    'name = "web"\n'
    "is_primary = true\n"
    "port = 8080\n"
)


def test_monorepo_finds_each_agent_dir_sorted():
    files = {
        "agents/triage/astrolift.toml": _agent_toml("triage"),
        "agents/summarize/astrolift.toml": _agent_toml("summarize"),
        # Sibling non-manifest files in an agent dir are ignored.
        "agents/triage/README.md": "docs",
        "README.md": "repo docs",
    }
    found = scan_agent_manifests(files)
    assert [d.manifest_path for d in found] == [
        "agents/summarize/astrolift.toml",
        "agents/triage/astrolift.toml",
    ]
    assert {d.slug for d in found} == {"triage", "summarize"}
    assert all(d.workload_kind == "agent" for d in found)
    # ``name`` is the manifest top-level name; ``slug`` is the workload name.
    by_path = {d.manifest_path: d for d in found}
    assert by_path["agents/triage/astrolift.toml"].name == "triage"


def test_single_root_manifest_is_found():
    found = scan_agent_manifests({"astrolift.toml": _agent_toml("solo"), "Dockerfile": "FROM x"})
    assert [d.manifest_path for d in found] == ["astrolift.toml"]
    assert found[0].slug == "solo"


def test_non_agent_root_manifest_is_ignored():
    # A repo whose root astrolift.toml is a web app (not an agent) yields no
    # agents — the app-registration path owns that manifest.
    assert scan_agent_manifests({"astrolift.toml": _WEB_TOML}) == []


def test_non_agent_workload_in_agents_dir_is_ignored():
    # Even under agents/, a manifest whose workload isn't kind=agent is skipped.
    files = {
        "agents/web/astrolift.toml": _WEB_TOML,
        "agents/triage/astrolift.toml": _agent_toml("triage"),
    }
    found = scan_agent_manifests(files)
    assert [d.manifest_path for d in found] == ["agents/triage/astrolift.toml"]


def test_manifest_nested_below_agent_dir_is_ignored():
    # Only agents/<slug>/astrolift.toml (exactly three segments) is an agent
    # root; a deeper path (a vendored example/fixture) is not.
    files = {
        "agents/real/astrolift.toml": _agent_toml("real"),
        "agents/examples/nested/astrolift.toml": _agent_toml("nested"),
    }
    found = scan_agent_manifests(files)
    assert [d.manifest_path for d in found] == ["agents/real/astrolift.toml"]


def test_mixed_workload_root_is_not_an_agent():
    # A manifest with an agent workload PLUS another workload is an app, not a
    # single-agent manifest — discovery requires exactly one agent workload.
    mixed = (
        'name = "mixed"\n'
        "[[workloads]]\n"
        'name = "api"\n'
        'kind = "deployment"\n'
        "[[workloads.containers]]\n"
        'name = "api"\n'
        "is_primary = true\n"
        "port = 8080\n"
        "[[workloads]]\n"
        'name = "bot"\n'
        'kind = "agent"\n'
        "[[workloads.containers]]\n"
        'name = "bot"\n'
        "is_primary = true\n"
        'image_ref = "ecr.example/agent:latest"\n'
    )
    assert scan_agent_manifests({"astrolift.toml": mixed}) == []


def test_unparseable_manifest_is_skipped_not_raised():
    # A broken TOML body must not blow up the whole scan — just skip it.
    files = {
        "agents/broken/astrolift.toml": 'name = "broken"\n[[workloads]]\nkind = ',
        "agents/ok/astrolift.toml": _agent_toml("ok"),
    }
    found = scan_agent_manifests(files)
    assert [d.manifest_path for d in found] == ["agents/ok/astrolift.toml"]


def test_present_but_unfetched_contents_are_skipped():
    # A path mapped to None (present-but-body-not-fetched) can't be parsed, so
    # it is skipped rather than treated as an agent.
    found = scan_agent_manifests({"agents/a/astrolift.toml": None})
    assert found == []


def test_empty_tree_returns_empty_list():
    assert scan_agent_manifests({}) == []


# ---- zipball unpacker -------------------------------------------------


def test_zipball_unpack_strips_top_level_dir():
    from astrolift_scm.providers.repo_tree import repo_tree_from_zipball_bytes

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Hosts nest everything under one top-level dir; the unpacker strips it.
        zf.writestr("owner-repo-deadbeef/agents/a/astrolift.toml", _agent_toml("a"))
        zf.writestr("owner-repo-deadbeef/README.md", "hi")
        zf.writestr("owner-repo-deadbeef/", "")  # bare dir entry — dropped

    tree = repo_tree_from_zipball_bytes(buf.getvalue())
    assert sorted(tree) == ["README.md", "agents/a/astrolift.toml"]
    # And the stripped tree feeds the scanner end-to-end.
    assert [d.manifest_path for d in scan_agent_manifests(tree)] == ["agents/a/astrolift.toml"]


def test_zipball_unpack_drops_binary_files():
    from astrolift_scm.providers.repo_tree import repo_tree_from_zipball_bytes

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("r-sha/agents/a/astrolift.toml", _agent_toml("a"))
        # Invalid UTF-8 → dropped (not a manifest anyway).
        zf.writestr("r-sha/logo.png", b"\xff\xd8\xff\xe0\x00\x10binary")

    tree = repo_tree_from_zipball_bytes(buf.getvalue())
    assert "logo.png" not in tree
    assert "agents/a/astrolift.toml" in tree
