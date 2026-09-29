"""Every run records what started it (#2152).

Two halves. The vocabulary in ``core.run_trigger`` (normalising each
source's own trigger word, and ``request_trigger`` telling a token from a
session). And a static guard: every ``AgentTask`` and ``WorkflowRun``
creation in the platform code passes ``trigger_kind``, so a new creation
path cannot land without saying how its runs start. The model default is
``unknown``, which is right for rows from before the column and wrong for
anything created since.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from core.run_trigger import (
    RunTrigger,
    deployment_run_trigger,
    normalize,
    request_trigger,
    source_triggers,
)

BACKEND = Path(__file__).resolve().parents[2]
_MODELS = {"AgentTask", "WorkflowRun"}
_CREATES = {"create", "get_or_create", "update_or_create"}


def test_normalize_maps_each_source_word():
    assert normalize("deployment", "push") == RunTrigger.WEBHOOK
    assert normalize("deployment", "ci") == RunTrigger.API
    assert normalize("deployment", "scheduled") == RunTrigger.SCHEDULE
    assert normalize("deployment", "rollback") == RunTrigger.MANUAL
    assert normalize("job", "scheduled") == RunTrigger.SCHEDULE
    assert normalize("task", "workflow") == RunTrigger.PARENT
    assert normalize("task", "api") == RunTrigger.API
    # Agent and workflow runs store the shared word already.
    assert normalize("agent", "parent") == RunTrigger.PARENT
    assert normalize("workflow", "nonsense") == RunTrigger.UNKNOWN
    assert normalize("deployment", "nonsense") == RunTrigger.UNKNOWN


def test_source_triggers_reverses_normalize():
    assert sorted(source_triggers("deployment", ["manual"])) == ["manual", "promotion", "rollback"]
    assert source_triggers("job", ["webhook"]) == []
    assert source_triggers("agent", ["manual"]) is None


def test_deployment_run_trigger_marks_token_deploys_as_api():
    assert deployment_run_trigger("manual", via_token=False) == RunTrigger.MANUAL
    assert deployment_run_trigger("manual", via_token=True) == RunTrigger.API
    assert deployment_run_trigger("push", via_token=True) == RunTrigger.WEBHOOK


def test_request_trigger_reads_the_api_token(monkeypatch):
    monkeypatch.setattr("astrolift_identity.api_tokens.get_current_api_token", lambda: None)
    assert request_trigger() == RunTrigger.MANUAL
    monkeypatch.setattr("astrolift_identity.api_tokens.get_current_api_token", lambda: SimpleNamespace(pk=1))
    assert request_trigger() == RunTrigger.API


# ---------------------------------------------------------------------------
# Static guard
# ---------------------------------------------------------------------------


def _platform_files() -> list[Path]:
    return sorted(
        p
        for p in BACKEND.rglob("*.py")
        if "tests" not in p.parts and "migrations" not in p.parts and ".venv" not in p.parts
    )


def _creates(tree: ast.AST):
    """``(function, call)`` for every ``AgentTask|WorkflowRun.objects.<create>(...)``."""
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _CREATES
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in {"objects", "all_objects"}
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id in _MODELS
            ):
                yield fn, node


def _dict_names_with_trigger(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Dict)
            and any(isinstance(k, ast.Constant) and k.value == "trigger_kind" for k in node.value.keys)
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _sets_trigger(fn: ast.AST, call: ast.Call) -> bool:
    with_trigger = _dict_names_with_trigger(fn)
    for kw in call.keywords:
        if kw.arg == "trigger_kind":
            return True
        # ``create(**defaults)`` / ``get_or_create(..., defaults=defaults)``.
        if kw.arg in {None, "defaults"} and isinstance(kw.value, ast.Name) and kw.value.id in with_trigger:
            return True
    return False


def test_every_run_creation_sets_trigger_kind():
    found: list[str] = []
    missing: list[str] = []
    for path in _platform_files():
        source = path.read_text()
        if "AgentTask" not in source and "WorkflowRun" not in source:
            continue
        for fn, call in _creates(ast.parse(source)):
            where = f"{path.relative_to(BACKEND)}:{call.lineno}"
            found.append(where)
            if not _sets_trigger(fn, call):
                missing.append(where)
    # Eight agent-task and five workflow-run creation calls at the time of
    # writing; a scan that finds none is a broken scan, not a clean tree.
    assert len(found) >= 10, found
    assert not missing, (
        "These AgentTask / WorkflowRun creations do not set trigger_kind (#2152); "
        "say how the run started (core.run_trigger.RunTrigger):\n  " + "\n  ".join(missing)
    )
