"""Where in the backend does a pipeline run or job run change status?

Shared by two ratchets: the metrics one (#98) and the commit-status one
(#104). It exists because the first version of the metrics ratchet parsed a
single module, and a fourth terminal transition -- the cancel mutation in
`astrolift_pipelines/schema/mutations.py` -- sat outside it, recording
nothing. `pipeline_runs_total` undercounted every cancellation and every
test passed.

So the discovery is a filesystem sweep rather than a hand-listed module.
A new transition in a module nobody thought of is exactly the case that
goes dark, and enumerating modules by hand is what let it happen once.

Parses files by path rather than importing them: this runs over app code
that expects Django to be configured a particular way, and a ratchet must
not be the thing that breaks when an unrelated module grows an import.
"""

from __future__ import annotations

import ast
import pathlib

# Which models have a metric and a commit status. `StepRun` deliberately
# absent: there is no per-step series and no per-step commit status, and an
# earlier matcher that accepted any `*.Status.TERMINAL` flagged the
# step-settling helper for not recording something that should not exist.
METERED_MODELS = frozenset({"JobRun", "PipelineRun"})

# Only PipelineRun maps onto an SCM commit status. A job is an internal
# subdivision of a run; GitHub gets one check per pipeline, not per job.
COMMIT_STATUS_MODELS = frozenset({"PipelineRun"})

TERMINAL_STATUSES = frozenset({"SUCCESS", "FAILURE", "CANCELLED"})

# RUNNING is not terminal, but it is a transition a commit status must
# reflect: a branch-protection rule cannot be satisfied by a check that
# never appears as pending.
REPORTABLE_STATUSES = TERMINAL_STATUSES | {"RUNNING"}

_PACKAGES = ("astrolift_pipelines", "astrolift_workflows")

_BACKEND = pathlib.Path(__file__).resolve().parents[2]


def _source_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for package in _PACKAGES:
        for path in sorted((_BACKEND / package).rglob("*.py")):
            parts = set(path.parts)
            if "tests" in parts or "migrations" in parts:
                continue
            if path.name.startswith("test_"):
                continue
            out.append(path)
    return out


def _assigns_status(node: ast.AST, *, models: frozenset[str], statuses: frozenset[str]) -> bool:
    """True when ``node`` contains `<x>.status = <Model>.Status.<STATUS>`.

    Matched on the full shape, including which model's enum it came from,
    so a match tells you the transition is one that has a producer to wire.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Assign):
            continue
        value = sub.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr in statuses
            and isinstance(value.value, ast.Attribute)
            and value.value.attr == "Status"
            and isinstance(value.value.value, ast.Name)
            and value.value.value.id in models
        ):
            return True
    return False


def find_sites(
    *,
    models: frozenset[str] = METERED_MODELS,
    statuses: frozenset[str] = TERMINAL_STATUSES,
) -> dict[str, ast.FunctionDef]:
    """Return ``{"module.function": node}`` for every matching transition.

    Keyed by module path so a failure message names the file to open. Both
    plain functions and methods are found: the walk descends into classes,
    which is what catches the GraphQL cancel mutation.
    """
    found: dict[str, ast.FunctionDef] = {}
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        rel = path.relative_to(_BACKEND).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if _assigns_status(node, models=models, statuses=statuses):
                found[f"{rel}::{node.name}"] = node
    return found


def calls_any(fn: ast.FunctionDef, names: frozenset[str]) -> bool:
    """True when ``fn`` calls any function in ``names`` (by bare or attr name)."""
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Call):
            continue
        called = ""
        if isinstance(sub.func, ast.Name):
            called = sub.func.id
        elif isinstance(sub.func, ast.Attribute):
            called = sub.func.attr
        if called in names:
            return True
    return False
