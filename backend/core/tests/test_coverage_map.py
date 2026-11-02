"""Coverage-based test selection for pull requests (scripts/coverage_map.py)."""

from __future__ import annotations

import gzip
import json
import os
import subprocess

import pytest

from scripts.coverage_map import always_tests, build, import_time_lines, main, select

MAP = {
    "version": 1,
    "commit": "abc",
    "tests": [
        "core/tests/test_coverage_map.py",
        "astrolift_registry/tests/test_app_access_2132.py",
        "astrolift_manifest/tests/test_persist.py",
    ],
    "files": {
        # set_app_access lines 150-160 run by the access tests; 10-12 by both.
        "core/edge_access.py": {"10": [0, 1], "11": [0, 1], "150": [1], "151": [1]},
        "astrolift_manifest/persist.py": {"700": [2]},
        # Test tooling the tests execute: in the map, and still not selectable.
        "astrolift_manifest/conftest.py": {"3": [2]},
        "astrolift_manifest/tests/helpers.py": {"5": [2]},
        "core/testing/factories.py": {"7": [1]},
        "scripts/coverage_map.py": {"100": [0]},
    },
}


def _select(changes, lines=None, unchanged=True, import_time=()):
    return select(
        MAP,
        changes,
        line_lookup=lambda path: (lines or {}).get(path, set()),
        unchanged=lambda path: unchanged,
        import_time=lambda path: set(import_time),
    )


def test_a_changed_line_selects_only_the_tests_that_executed_it():
    result = _select([("M", "backend/core/edge_access.py")], lines={"backend/core/edge_access.py": {150}})

    assert result["all"] is False
    assert result["tests"] == ["astrolift_registry/tests/test_app_access_2132.py"]


def test_touched_lines_no_test_executed_fall_back_to_the_whole_file():
    """Module-level code runs at import, outside any test's context."""
    result = _select([("M", "backend/core/edge_access.py")], lines={"backend/core/edge_access.py": {3}})

    assert result["tests"] == [
        "astrolift_registry/tests/test_app_access_2132.py",
        "core/tests/test_coverage_map.py",
    ]
    assert any("file-level" in note for note in result["notes"])


def test_a_touched_import_time_line_widens_a_line_hit_to_the_whole_file():
    """A constant edited beside a function body reaches every reader of the
    constant, not only the tests that ran the function."""
    result = _select(
        [("M", "backend/core/edge_access.py")],
        lines={"backend/core/edge_access.py": {3, 150}},
        import_time={3},
    )

    assert result["tests"] == [
        "astrolift_registry/tests/test_app_access_2132.py",
        "core/tests/test_coverage_map.py",
    ]
    assert result["notes"] == ["core/edge_access.py: file-level (import-time code)"]


def test_a_file_changed_since_the_map_is_selected_by_file():
    result = _select(
        [("M", "backend/astrolift_manifest/persist.py")],
        lines={"backend/astrolift_manifest/persist.py": {5}},
        unchanged=False,
    )

    assert result["tests"] == ["astrolift_manifest/tests/test_persist.py"]


def test_a_changed_or_added_test_file_always_runs():
    result = _select([("A", "backend/astrolift_registry/tests/test_app_access_2132.py")])

    assert result["tests"] == ["astrolift_registry/tests/test_app_access_2132.py"]


@pytest.mark.parametrize(
    ("status", "path"),
    [
        ("M", "backend/config/settings.py"),
        ("A", "backend/astrolift_registry/migrations/0099_x.py"),
        ("M", "backend/astrolift_registry/apps.py"),
        ("M", "backend/Pipfile.lock"),
        ("M", "backend/conftest.py"),
        ("M", "backend/astrolift_registry/fixtures/data.json"),
        # Test tooling and the CI scripts, though the map holds their lines.
        ("M", "backend/astrolift_manifest/conftest.py"),
        ("M", "backend/astrolift_manifest/tests/helpers.py"),
        ("M", "backend/core/testing/factories.py"),
        ("M", "backend/scripts/coverage_map.py"),
        # Read by tests, though not Python.
        ("M", "backend/astrolift_agents/data/routes.md"),
        # Nothing in the map says which tests these reach.
        ("M", "backend/core/never_run.py"),
        ("A", "backend/astrolift_manifest/new_module.py"),
    ],
)
def test_what_coverage_cannot_follow_runs_everything(status, path):
    result = _select([(status, path)])

    assert result["all"] is True
    assert result["reasons"] == [path]


def test_frontend_and_top_level_backend_docs_select_nothing():
    result = _select([("M", "frontend/app/page.tsx"), ("M", "backend/README.md")])

    assert result["all"] is False
    assert result["tests"] == []


def test_import_time_lines_are_module_and_class_bodies_decorators_and_signatures():
    source = "\n".join(
        [
            "import os",  # 1
            "",
            "LIMIT = 3",  # 3
            "",
            "",
            "@decorator(",  # 6
            "    arg=1,",
            ")",
            "def run(",  # 9
            "    a,",
            "    b=LIMIT,",
            "):",  # 12
            '    """Doc."""',  # 13
            "    return a + b",  # 14
            "",
            "",
            "class Thing:",  # 17
            "    size = 4",  # 18
            "",
            "    def method(self):",  # 20
            "        return self.size",  # 21
            "# a comment",  # 22
        ]
    )

    assert import_time_lines(source) == {1, 3, 6, 7, 8, 9, 10, 11, 12, 17, 18, 20}


def test_source_that_does_not_parse_is_import_time_throughout():
    assert import_time_lines("def broken(:\n    pass\n") == {1, 2, 3}


def test_the_always_tier_is_the_platform_roots_and_what_the_map_cannot_place():
    coverage_map = {
        "version": 1,
        "commit": "abc",
        "tests": [
            "core/tests/test_guard.py",
            "astrolift_x/tests/test_placed.py",
            "astrolift_x/tests/test_static.py",
            "astrolift_y/tests/test_stale.py",
        ],
        "files": {
            # Line 1 is a fixture every test runs; only the others place a test.
            "astrolift_x/mod.py": {"1": [0, 1, 2, 3], "2": [1], "3": [3], "4": [0]},
        },
    }
    all_tests = {
        "core/tests/test_guard.py",
        "astrolift_x/tests/test_placed.py",
        "astrolift_x/tests/test_static.py",
        "astrolift_y/tests/test_stale.py",
        "astrolift_z/tests/test_new.py",
    }

    always = always_tests(coverage_map, all_tests, stale={"astrolift_y/tests/test_stale.py"})

    assert always == {
        "core/tests/test_guard.py",  # a platform root
        "astrolift_x/tests/test_static.py",  # runs only what every test runs
        "astrolift_y/tests/test_stale.py",  # edited since the map
        "astrolift_z/tests/test_new.py",  # not in the map at all
    }


def _git(repo, *args):
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,  # no hooks or signing from the caller's config
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _commit(repo, files, message):
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


MODULE = "LIMIT = 3\n\n\ndef double(x):\n    return x * 2\n\n\ndef triple(x):\n    return x * 3\n"


@pytest.fixture
def repository(tmp_path, monkeypatch):
    """A repository whose map (commit ``mapped``) says test_mod ran line 5 of
    app/mod.py and test_other line 9, and a test under core/ ran neither."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    mapped = _commit(
        repo,
        {
            "backend/app/mod.py": MODULE,
            "backend/app/tests/test_mod.py": "def test_double():\n    pass\n",
            "backend/app/tests/test_other.py": "def test_triple():\n    pass\n",
            "backend/core/tests/test_guard.py": "def test_guard():\n    pass\n",
            # Its own job runs it, never a backend shard.
            "backend/providers/tests/test_driver.py": "def test_driver():\n    pass\n",
        },
        "mapped",
    )
    coverage_map = tmp_path / "map.json.gz"
    with gzip.open(coverage_map, "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "version": 1,
                "commit": mapped,
                "tests": ["app/tests/test_mod.py", "app/tests/test_other.py", "core/tests/test_guard.py"],
                "files": {"app/mod.py": {"5": [0], "9": [1]}},
            },
            handle,
        )
    monkeypatch.chdir(repo / "backend")
    return repo, mapped, str(coverage_map)


def _run_select(capsys, coverage_map, base, head):
    capsys.readouterr()
    assert main(["select", "--map", coverage_map, "--base", base, "--head", head, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_select_runs_the_always_tier_and_the_tests_the_changed_line_reaches(repository, capsys):
    repo, mapped, coverage_map = repository
    _git(repo, "checkout", "-q", "-b", "change")
    head = _commit(repo, {"backend/app/mod.py": MODULE.replace("x * 3", "3 * x")}, "triple")

    result = _run_select(capsys, coverage_map, mapped, head)

    assert result["all"] is False
    assert result["selected"] == ["app/tests/test_other.py"]
    assert result["always"] == ["core/tests/test_guard.py"]
    assert result["tests"] == ["app/tests/test_other.py", "core/tests/test_guard.py"]


def test_select_widens_an_edited_constant_to_every_test_of_its_module(repository, capsys):
    """Line 9 alone reaches test_other; the constant beside it reaches both."""
    repo, mapped, coverage_map = repository
    _git(repo, "checkout", "-q", "-b", "change")
    edited = MODULE.replace("LIMIT = 3", "LIMIT = 4").replace("x * 3", "3 * x")
    head = _commit(repo, {"backend/app/mod.py": edited}, "limit")

    result = _run_select(capsys, coverage_map, mapped, head)

    assert result["selected"] == ["app/tests/test_mod.py", "app/tests/test_other.py"]
    assert result["notes"] == ["app/mod.py: file-level (import-time code)"]


def test_select_keeps_a_test_edited_since_the_map(repository, capsys):
    repo, mapped, coverage_map = repository
    base = _commit(repo, {"backend/app/tests/test_mod.py": "def test_double():\n    assert True\n"}, "edit")
    _git(repo, "checkout", "-q", "-b", "change")
    head = _commit(repo, {"backend/app/mod.py": MODULE.replace("x * 3", "3 * x")}, "triple")

    result = _run_select(capsys, coverage_map, base, head)

    assert result["selected"] == ["app/tests/test_other.py"]
    assert result["always"] == ["app/tests/test_mod.py", "core/tests/test_guard.py"]


def test_select_refuses_a_map_from_outside_the_base_history(repository, capsys):
    repo, mapped, coverage_map = repository
    _git(repo, "checkout", "-q", "-b", "elsewhere")
    elsewhere = _commit(repo, {"backend/app/other.py": "X = 1\n"}, "elsewhere")
    _git(repo, "checkout", "-q", "main")
    base = _commit(repo, {"backend/app/third.py": "Y = 1\n"}, "main moves on")
    _git(repo, "checkout", "-q", "-b", "change")
    head = _commit(repo, {"backend/app/mod.py": MODULE.replace("x * 3", "3 * x")}, "triple")
    with gzip.open(coverage_map, "rt", encoding="utf-8") as handle:
        stale_map = json.load(handle)
    with gzip.open(coverage_map, "wt", encoding="utf-8") as handle:
        json.dump({**stale_map, "commit": elsewhere}, handle)

    result = _run_select(capsys, coverage_map, base, head)

    assert result["all"] is True
    assert result["reasons"] == [f"the map's commit {elsewhere[:9]} is not an ancestor of the base"]


def test_build_folds_per_test_contexts_into_test_files(tmp_path, monkeypatch):
    coverage = pytest.importorskip("coverage")
    data = coverage.CoverageData(basename=str(tmp_path / ".coverage"))
    source = "/runner/work/repo/backend/core/edge_access.py"
    data.set_context("core/tests/test_edge_access.py::test_one|run")
    data.add_lines({source: [10, 11]})
    data.set_context("astrolift_registry/tests/test_app_access_2132.py::test_two|run")
    data.add_lines({source: [11, 150]})
    data.set_context("")
    data.add_lines({source: [1, 2]})
    data.set_context("core/tests/test_edge_access.py::test_one|run")
    data.add_lines({"/runner/work/repo/backend/core/tests/test_edge_access.py": [5]})
    data.write()

    built = build(str(tmp_path / ".coverage"), "abc")

    assert set(built["files"]) == {"core/edge_access.py"}  # tests themselves are not mapped
    lines = built["files"]["core/edge_access.py"]
    names = built["tests"]
    assert {names[i] for i in lines["11"]} == {
        "core/tests/test_edge_access.py",
        "astrolift_registry/tests/test_app_access_2132.py",
    }
    assert "1" not in lines  # import-time lines belong to no test
