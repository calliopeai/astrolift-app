"""Coverage-based test selection for pull requests (scripts/coverage_map.py)."""

from __future__ import annotations

import pytest

from scripts.coverage_map import build, select

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
    },
}


def _select(changes, lines=None, unchanged=True):
    return select(
        MAP,
        changes,
        line_lookup=lambda path: (lines or {}).get(path, set()),
        unchanged=lambda path: unchanged,
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
    "path",
    [
        "backend/config/settings.py",
        "backend/astrolift_registry/migrations/0099_x.py",
        "backend/astrolift_registry/apps.py",
        "backend/Pipfile.lock",
        "backend/conftest.py",
        "backend/astrolift_registry/fixtures/data.json",
    ],
)
def test_what_coverage_cannot_follow_runs_everything(path):
    result = _select([("M", path)])

    assert result["all"] is True
    assert result["reasons"] == [path]


def test_frontend_docs_and_unmeasured_code_select_nothing():
    result = _select(
        [
            ("M", "frontend/app/page.tsx"),
            ("M", "backend/README.md"),
            ("M", "backend/core/never_run.py"),
        ]
    )

    assert result["all"] is False
    assert result["tests"] == []
    assert any("core/never_run.py" in note for note in result["notes"])


def test_a_nested_conftest_runs_the_tests_under_it():
    result = _select([("M", "backend/astrolift_manifest/tests/conftest.py")])

    assert result["tests"] == ["astrolift_manifest/tests/test_persist.py"]


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
