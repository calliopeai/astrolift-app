#!/usr/bin/env python3
"""Which backend tests execute which lines, and which tests a change reaches.

The nightly run measures every backend shard with per-test coverage contexts
(``pytest --cov-context=test``). ``build`` folds that data into a map of
source file -> line -> the test files that executed it, at one commit. A pull
request then runs ``select`` against the newest map from main: the test files
that executed the lines the change touches, every test file it adds or edits,
and nothing else.

    python scripts/coverage_map.py build --data .coverage --commit <sha> --out map.json.gz
    python scripts/coverage_map.py select --map map.json.gz --base <sha> --head <sha> [--json]

Run from ``backend/``. ``select`` prints the test files, one per line, or
``ALL`` when coverage cannot answer for the change (settings, Django wiring by
string, migrations, dependency locks, a root conftest, anything that is not
Python). Main and nightly keep running the whole suite; this only decides
what a pull request needs.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

VERSION = 1
BACKEND = "backend/"

# Changes coverage cannot follow: run everything.
BROAD_PREFIXES = (
    "config/",
    "conftest.py",
    "pytest.ini",
    "Pipfile",
    "pyproject.toml",
    "manage.py",
    "startup.py",
    "providers/pyproject.toml",
)
BROAD_NAMES = ("apps.py", "signals.py", "urls.py", "receivers.py", "settings.py")
# Not code, or code no backend test runs.
IGNORED_SUFFIXES = (".md", ".txt", ".rst")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_tests.py") or name == "tests.py"
    )


def backend_relative(path: str) -> str | None:
    """A measured or changed path, relative to backend/; None outside it."""
    path = path.replace("\\", "/")
    if path.startswith(BACKEND):
        return path[len(BACKEND) :]
    marker = f"/{BACKEND}"
    if marker in path:
        return path.rsplit(marker, 1)[1]
    return None


# ---- build ---------------------------------------------------------------------


def build(data_file: str, commit: str) -> dict:
    """Fold combined coverage data into {file: {line: [test indexes]}}.

    A context is ``<nodeid>|<phase>``; the test file is the nodeid before
    ``::``. Lines run while importing at collection have the empty context
    and belong to no test.
    """
    from coverage import CoverageData

    data = CoverageData(basename=data_file)
    data.read()
    tests: dict[str, int] = {}
    files: dict[str, dict[str, list[int]]] = {}
    for measured in data.measured_files():
        rel = backend_relative(measured)
        if rel is None or is_test(rel) or "/migrations/" in rel:
            continue
        lines: dict[str, set[int]] = defaultdict(set)
        for lineno, contexts in (data.contexts_by_lineno(measured) or {}).items():
            for context in contexts:
                nodeid = context.split("|", 1)[0]
                if not nodeid:
                    continue
                test_file = nodeid.split("::", 1)[0]
                lines[str(lineno)].add(tests.setdefault(test_file, len(tests)))
        if lines:
            files[rel] = {line: sorted(ids) for line, ids in lines.items()}
    return {"version": VERSION, "commit": commit, "tests": list(tests), "files": files}


# ---- select --------------------------------------------------------------------


def git(*args: str) -> str:
    """Run git at the repository root: changed paths are repo-relative."""
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return subprocess.run(["git", "-C", top, *args], check=True, capture_output=True, text=True).stdout


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    """(status, repo path) for every change between the merge base and head."""
    out = []
    for line in git("diff", "--name-status", "--no-renames", f"{base}...{head}").splitlines():
        status, _, path = line.partition("\t")
        out.append((status[:1], path))
    return out


def old_lines(base: str, head: str, path: str) -> set[int]:
    """Lines of ``path`` at the merge base that the change touches.

    A pure insertion touches no old line, so the lines on either side of it
    stand in: code inserted into a function runs with its neighbours.
    """
    lines: set[int] = set()
    for line in git("diff", "-U0", f"{base}...{head}", "--", path).splitlines():
        match = _HUNK.match(line)
        if not match:
            continue
        start, count = int(match.group(1)), int(match.group(2) if match.group(2) is not None else 1)
        lines.update(range(start, start + count) if count else (start, start + 1))
    return lines


def unchanged_since(commit: str, base: str, path: str) -> bool:
    try:
        return git("diff", "--name-only", commit, base, "--", path).strip() == ""
    except subprocess.CalledProcessError:
        return False


def select(coverage_map: dict, changes: list[tuple[str, str]], *, line_lookup, unchanged) -> dict:
    """The test files a change reaches.

    ``line_lookup(repo_path)`` gives the touched lines at the merge base;
    ``unchanged(repo_path)`` says whether the file is the same at the merge
    base as at the map's commit (line numbers only line up when it is).
    """
    tests_by_index = coverage_map["tests"]
    files = coverage_map["files"]
    broad: list[str] = []
    notes: list[str] = []
    selected: set[str] = set()
    conftest_dirs: list[str] = []

    for status, repo_path in changes:
        rel = backend_relative(repo_path)
        if rel is None or rel.endswith(IGNORED_SUFFIXES):
            continue
        name = rel.rsplit("/", 1)[-1]
        if rel.startswith(BROAD_PREFIXES) or name in BROAD_NAMES or "/migrations/" in rel:
            broad.append(repo_path)
            continue
        if not rel.endswith(".py"):
            broad.append(repo_path)
            continue
        if name == "conftest.py":
            conftest_dirs.append(rel.rsplit("/", 1)[0] + "/" if "/" in rel else "")
            continue
        if is_test(rel):
            if status != "D":
                selected.add(rel)
            continue
        per_line = files.get(rel)
        if per_line is None:
            notes.append(f"{rel}: {'new, ' if status == 'A' else ''}no test executes it")
            continue
        everything = {tests_by_index[i] for ids in per_line.values() for i in ids}
        if status == "D" or not unchanged(repo_path):
            # Deleted, or moved since the map: its line numbers no longer line up.
            selected |= everything
            notes.append(f"{rel}: file-level ({'deleted' if status == 'D' else 'changed since the map'})")
            continue
        hits = {tests_by_index[i] for line in line_lookup(repo_path) for i in per_line.get(str(line), ())}
        if not hits:
            # Module-level code (imports, constants, decorators) runs at
            # import, outside any test: whoever ran the file stands in.
            selected |= everything
            notes.append(f"{rel}: file-level (no test executed the touched lines)")
            continue
        selected |= hits

    if broad:
        return {"all": True, "reasons": sorted(broad), "tests": [], "notes": notes}
    for directory in conftest_dirs:
        selected |= {t for t in tests_by_index if t.startswith(directory)}
    return {
        "all": False,
        "reasons": [],
        "tests": sorted(t for t in selected if Path(t).exists()),
        "notes": notes,
        "map_commit": coverage_map.get("commit", ""),
        "map_tests": len(tests_by_index),
    }


def load_map(path: str) -> dict:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("version") != VERSION:
        raise SystemExit(f"coverage map version {data.get('version')!r}, expected {VERSION}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="fold combined coverage data into a map")
    b.add_argument("--data", required=True, help="combined .coverage file")
    b.add_argument("--commit", required=True)
    b.add_argument("--out", required=True, help="map path; .gz compresses")
    s = sub.add_parser("select", help="the test files a change reaches")
    s.add_argument("--map", required=True)
    s.add_argument("--base", required=True)
    s.add_argument("--head", required=True)
    s.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "build":
        data = build(args.data, args.commit)
        opener = gzip.open if args.out.endswith(".gz") else open
        with opener(args.out, "wt", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
        print(f"{len(data['files'])} files, {len(data['tests'])} test files, at {args.commit}")
        return 0

    coverage_map = load_map(args.map)
    commit = coverage_map.get("commit", "")
    result = select(
        coverage_map,
        changed_files(args.base, args.head),
        line_lookup=lambda path: old_lines(args.base, args.head, path),
        unchanged=lambda path: (
            bool(commit) and unchanged_since(commit, git("merge-base", args.base, args.head).strip(), path)
        ),
    )
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["all"]:
        print("ALL")
    else:
        print("\n".join(result["tests"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
