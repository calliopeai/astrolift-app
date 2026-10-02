"""Explicit finite return edges, independent of Django and Temporal."""

from __future__ import annotations

import math
import re
from typing import Any

from workflows.stage_limits import validate_stage_attempts

MAX_LOOP_ROUNDS = 20
MAX_STAGE_VISITS = 1000
MAX_EXECUTION_UNITS = 500
SUPPORTED_EXECUTOR_PATTERNS = frozenset({"single", "chained", "fan_out", "review_loop"})
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}\Z")


class LoopContractError(ValueError):
    pass


def validate_back_edge(value: Any, *, kind: str) -> dict:
    if value == {}:
        return {}
    if not isinstance(value, dict):
        raise LoopContractError("back_edge must be an object")
    required = {"to", "when", "max_rounds"}
    allowed = required | {"on_exhausted", "path", "value"}
    if not required <= value.keys() or value.keys() - allowed:
        raise LoopContractError(
            "back_edge requires to, when and max_rounds; unknown fields are invalid"
        )
    target = value["to"]
    if not isinstance(target, str) or not target.strip() or len(target) > 100:
        raise LoopContractError("back_edge.to must be a prior stage output_key")
    rounds = value["max_rounds"]
    if (
        isinstance(rounds, bool)
        or not isinstance(rounds, int)
        or not 1 <= rounds <= MAX_LOOP_ROUNDS
    ):
        raise LoopContractError(
            f"back_edge.max_rounds must be an integer between 1 and {MAX_LOOP_ROUNDS}"
        )
    when = value["when"]
    if when not in ("gate_rejected", "stage_failed", "output_equals"):
        raise LoopContractError(
            "back_edge.when must be gate_rejected, stage_failed or output_equals"
        )
    if when == "gate_rejected" and kind != "human_gate":
        raise LoopContractError("gate_rejected back-edges require a human_gate stage")
    if when == "stage_failed" and kind not in ("agent_dispatch", "workflow"):
        raise LoopContractError(
            "stage_failed back-edges require an agent_dispatch or workflow stage"
        )
    exhausted = value.get("on_exhausted", "fail")
    if exhausted not in ("fail", "escalate"):
        raise LoopContractError("back_edge.on_exhausted must be fail or escalate")
    edge = {"to": target, "when": when, "max_rounds": rounds, "on_exhausted": exhausted}
    if when == "output_equals":
        path = value.get("path")
        if (
            not isinstance(path, str)
            or not _PATH.fullmatch(path)
            or "value" not in value
        ):
            raise LoopContractError(
                "output_equals requires a dotted object field path and an explicit value"
            )
        expected = value["value"]
        if expected is not None and not isinstance(expected, (str, int, float, bool)):
            raise LoopContractError("back_edge.value must be a JSON scalar")
        if isinstance(expected, float) and not math.isfinite(expected):
            raise LoopContractError("back_edge.value must be finite")
        if isinstance(expected, str) and len(expected) > 4096:
            raise LoopContractError("back_edge.value is too long")
        edge.update(path=path, value=expected)
    elif "path" in value or "value" in value:
        raise LoopContractError("path and value are valid only for output_equals")
    return edge


def validate_loop_plan(
    stages: list[dict], *, pattern_kind: str, require_review_loop: bool = True
) -> None:
    """Each edge has its own lifetime budget, including nested return tracks.

    An edge may fire at most max_rounds-1 times across the entire execution,
    never reset when another edge sends control through it again. Therefore
    the conservative bound below also holds for overlapping/nested edges.
    """
    if require_review_loop and pattern_kind not in SUPPORTED_EXECUTOR_PATTERNS:
        raise LoopContractError("workflow pattern is not supported by the executor")
    prior: dict[str, dict] = {}
    visit_counts = [1 for _ in stages]
    extra_visits = 0
    review_edges = 0
    for index, stage in enumerate(stages):
        key = stage.get("output_key") or f"stage_{stage.get('order', index)}"
        if key in prior:
            raise LoopContractError("workflow output_key values must be unique")
        edge = validate_back_edge(stage.get("back_edge", {}), kind=stage["kind"])
        if edge:
            if edge["to"] not in prior:
                raise LoopContractError(
                    "back_edge.to must identify an earlier live stage output_key"
                )
            target = prior[edge["to"]]
            if not stage.get("output_key") or not target["explicit_key"]:
                raise LoopContractError(
                    "return-edge source and target require explicit stable output_key values"
                )
            extra_visits += (index - target["index"] + 1) * (edge["max_rounds"] - 1)
            for repeated in range(target["index"], index + 1):
                visit_counts[repeated] += edge["max_rounds"] - 1
            if edge["when"] == "gate_rejected":
                review_edges += 1
        prior[key] = {
            "index": index,
            "kind": stage["kind"],
            "explicit_key": bool(stage.get("output_key")),
        }
    if len(stages) + extra_visits > MAX_STAGE_VISITS:
        raise LoopContractError(
            f"workflow return edges exceed {MAX_STAGE_VISITS} maximum stage visits"
        )
    execution_units = 0
    for stage, visits in zip(stages, visit_counts, strict=True):
        attempts = (
            validate_stage_attempts(stage.get("max_attempts", 3))
            if stage["kind"] in ("agent_dispatch", "workflow")
            else 1
        )
        fanout_value = stage.get("fan_out", stage.get("fan_out_count"))
        if fanout_value == "dynamic" or stage.get("fan_out_dynamic"):
            fanout = 50
        elif fanout_value is None or fanout_value == 0:
            fanout = 1
        elif (
            isinstance(fanout_value, bool)
            or not isinstance(fanout_value, int)
            or not 1 <= fanout_value <= 50
        ):
            raise LoopContractError("fan-out must be bounded between 1 and 50 branches")
        else:
            fanout = fanout_value
        if (
            stage["kind"] == "agent_dispatch"
            and pattern_kind == "fan_out"
            and stage is stages[0]
        ):
            fanout = max(fanout, 50)
        execution_units += visits * attempts * max(1, fanout)
    if execution_units > MAX_EXECUTION_UNITS:
        raise LoopContractError(
            f"workflow exceeds {MAX_EXECUTION_UNITS} maximum execution units"
        )
    if (
        require_review_loop
        and stages
        and pattern_kind == "review_loop"
        and not review_edges
    ):
        raise LoopContractError(
            "review_loop requires an explicit bounded gate_rejected back-edge"
        )


def edge_matches(edge: dict, output: Any, *, stage_failed: bool = False) -> bool:
    if edge["when"] == "stage_failed":
        return stage_failed
    if edge["when"] == "gate_rejected":
        return isinstance(output, dict) and output.get("human_gate") == "rejected"
    if stage_failed:
        return False
    current = output
    for key in edge["path"].split("."):
        if not isinstance(current, dict) or key not in current:
            raise LoopContractError("back-edge condition output field is unavailable")
        current = current[key]
    expected = edge["value"]
    if type(current) is not type(expected):
        # JSON integers and floating point values can share numeric semantics;
        # booleans never do (Python otherwise considers False equal to zero).
        if not (
            type(current) in (int, float)
            and type(expected) in (int, float)
            and (not isinstance(current, float) or math.isfinite(current))
        ):
            raise LoopContractError(
                "back-edge condition output field has an incompatible type"
            )
    if isinstance(current, float) and not math.isfinite(current):
        raise LoopContractError("back-edge condition output field is not finite")
    return current == expected
