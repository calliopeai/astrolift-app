"""Unit tests for the app-manifest scanner (#979).

``scan_app_manifests`` walks a fetched repo tree (a ``{path: contents}`` map)
and returns the *app* manifests it finds, in one of two layouts:

  * monorepo — ``apps/<slug>/astrolift.toml``;
  * single   — a root ``astrolift.toml``.

It is the app-side mirror of ``scan_agent_manifests``: it keeps manifests that
declare a deployable (non-agent) workload and leaves pure single-agent
manifests to the agent scanner. These tests pin the glob + parse + split rules
without a database or network — the scanner is pure over the file map. The
DB-backed registration / idempotency / tenancy cases live in
``astrolift_registry/tests/test_app_repo_discovery.py``.
"""

from __future__ import annotations

from astrolift_manifest.discover import scan_app_manifests


def _app_toml(name: str, *, kind: str = "deployment", port: int = 8080) -> str:
    return (
        f'name = "{name}"\n'
        "[[workloads]]\n"
        f'name = "{name}"\n'
        f'kind = "{kind}"\n'
        "is_public = true\n"
        "[[workloads.containers]]\n"
        f'name = "{name}"\n'
        "is_primary = true\n"
        f"port = {port}\n"
    )


_AGENT_TOML = (
    'name = "bot"\n'
    "[[workloads]]\n"
    'name = "bot"\n'
    'kind = "agent"\n'
    "[[workloads.containers]]\n"
    'name = "bot"\n'
    "is_primary = true\n"
    'image_ref = "ecr.example/agent:latest"\n'
)


def test_monorepo_finds_each_app_dir_sorted():
    files = {
        "apps/web/astrolift.toml": _app_toml("web"),
        "apps/api/astrolift.toml": _app_toml("api"),
        # Sibling non-manifest files in an app dir are ignored.
        "apps/web/README.md": "docs",
        "README.md": "repo docs",
    }
    found = scan_app_manifests(files)
    assert [d.manifest_path for d in found] == [
        "apps/api/astrolift.toml",
        "apps/web/astrolift.toml",
    ]
    assert {d.name for d in found} == {"web", "api"}
    # build_context is the manifest's own dir so each service builds from there.
    by_path = {d.manifest_path: d for d in found}
    assert by_path["apps/web/astrolift.toml"].build_context == "apps/web"
    assert by_path["apps/api/astrolift.toml"].build_context == "apps/api"
    assert all(d.workload_count == 1 for d in found)


def test_single_root_manifest_is_found_with_dot_context():
    found = scan_app_manifests({"astrolift.toml": _app_toml("solo"), "Dockerfile": "FROM x"})
    assert [d.manifest_path for d in found] == ["astrolift.toml"]
    assert found[0].name == "solo"
    # A root manifest builds from the repo root.
    assert found[0].build_context == "."


def test_pure_agent_root_manifest_is_ignored():
    # A repo whose root astrolift.toml is a pure agent yields no apps — the
    # agent-registration path owns that manifest.
    assert scan_app_manifests({"astrolift.toml": _AGENT_TOML}) == []


def test_pure_agent_in_apps_dir_is_ignored():
    # Even under apps/, a pure single-agent manifest is skipped (owned by the
    # agent scanner); the real app is still found.
    files = {
        "apps/bot/astrolift.toml": _AGENT_TOML,
        "apps/web/astrolift.toml": _app_toml("web"),
    }
    found = scan_app_manifests(files)
    assert [d.manifest_path for d in found] == ["apps/web/astrolift.toml"]


def test_mixed_workload_manifest_is_an_app():
    # A manifest with a deployable workload PLUS an agent workload is an app
    # (it has a deployable surface) — kept, with the full workload count.
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
    found = scan_app_manifests({"astrolift.toml": mixed})
    assert [d.manifest_path for d in found] == ["astrolift.toml"]
    assert found[0].workload_count == 2


def test_manifest_nested_below_apps_dir_is_ignored():
    # Only apps/<slug>/astrolift.toml (exactly three segments) is an app root;
    # a deeper path (a vendored example/fixture) is not.
    files = {
        "apps/real/astrolift.toml": _app_toml("real"),
        "apps/examples/nested/astrolift.toml": _app_toml("nested"),
    }
    found = scan_app_manifests(files)
    assert [d.manifest_path for d in found] == ["apps/real/astrolift.toml"]


def test_unparseable_manifest_is_skipped_not_raised():
    # A broken TOML body must not blow up the whole scan — just skip it.
    files = {
        "apps/broken/astrolift.toml": 'name = "broken"\n[[workloads]]\nkind = ',
        "apps/ok/astrolift.toml": _app_toml("ok"),
    }
    found = scan_app_manifests(files)
    assert [d.manifest_path for d in found] == ["apps/ok/astrolift.toml"]


def test_manifest_without_workloads_is_skipped():
    # A name-only manifest declares no deployable surface — not an app.
    assert scan_app_manifests({"apps/empty/astrolift.toml": 'name = "empty"\n'}) == []


def test_present_but_unfetched_contents_are_skipped():
    # A path mapped to None (present-but-body-not-fetched) can't be parsed.
    assert scan_app_manifests({"apps/a/astrolift.toml": None}) == []


def test_empty_tree_returns_empty_list():
    assert scan_app_manifests({}) == []
