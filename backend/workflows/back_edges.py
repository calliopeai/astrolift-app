"""Explicit finite return edges, independent of Django and Temporal."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from workflows.collections import CollectionContractError, collection_ranges
from workflows.stage_limits import validate_stage_attempts

MAX_LOOP_ROUNDS = 20
MAX_STAGE_VISITS = 1000
MAX_EXECUTION_UNITS = 500
SUPPORTED_EXECUTOR_PATTERNS = frozenset({"single", "chained", "fan_out", "review_loop"})
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}\Z")


class LoopContractError(ValueError):
    pass


def flowise_output_key(node_id: str) -> str:
    """Stable ASCII identity used by the verified source-node mapping."""
    ascii_id = (
        unicodedata.normalize("NFKD", node_id).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^\w\s-]", "", ascii_id.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-_")
    return f"flow_{slug}"[:100]


def validate_back_edge(value: Any, *, kind: str) -> dict:
    if value == {}:
        return {}
    if not isinstance(value, dict):
        raise LoopContractError("back_edge must be an object")
    required = {"to", "when", "max_rounds"}
    allowed = required | {
        "on_exhausted",
        "path",
        "value",
        "source_format",
        "source_target",
        "source_label",
        "fallback_message",
    }
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
    if when not in ("gate_rejected", "stage_failed", "output_equals", "always"):
        raise LoopContractError(
            "back_edge.when must be gate_rejected, stage_failed, output_equals or always"
        )
    if when == "gate_rejected" and kind != "human_gate":
        raise LoopContractError("gate_rejected back-edges require a human_gate stage")
    if when == "stage_failed" and kind not in ("agent_dispatch", "workflow"):
        raise LoopContractError(
            "stage_failed back-edges require an agent_dispatch or workflow stage"
        )
    if when == "always" and kind != "checkpoint":
        raise LoopContractError("always back-edges require a checkpoint control stage")
    exhausted = value.get("on_exhausted", "fail")
    if exhausted not in ("fail", "escalate", "continue"):
        raise LoopContractError(
            "back_edge.on_exhausted must be fail, escalate or supported imported continue"
        )
    edge = {"to": target, "when": when, "max_rounds": rounds, "on_exhausted": exhausted}
    source_required = {"source_format", "source_target", "source_label"}
    source_fields = source_required | {"fallback_message"}
    if source_fields & value.keys():
        if (
            not source_required <= value.keys()
            or when != "always"
            or value["source_format"] != "flowise_loop_1_2"
        ):
            raise LoopContractError("unsupported imported loop output contract")
        if (
            not isinstance(value["source_target"], str)
            or not 1 <= len(value["source_target"]) <= 100
        ):
            raise LoopContractError(
                "imported loop requires its exact source node target"
            )
        if (
            not isinstance(value["source_label"], str)
            or len(value["source_label"]) > 1000
        ):
            raise LoopContractError(
                "imported loop requires its source target display label"
            )
        if target != flowise_output_key(value["source_target"]):
            raise LoopContractError(
                "imported loop source target must match its canonical return output key"
            )
        fallback = value.get("fallback_message")
        if fallback is not None and (
            not isinstance(fallback, str) or len(fallback) > 4096
        ):
            raise LoopContractError(
                "imported loop fallback_message must be a bounded string or null"
            )
        if isinstance(fallback, str) and "{{" in fallback:
            raise LoopContractError(
                "imported fallback variables require source runtime resolution"
            )
        edge.update({field: value[field] for field in source_fields if field in value})
    if exhausted == "continue" and not source_required <= edge.keys():
        raise LoopContractError(
            "continue is available only for the supported imported loop output contract"
        )
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
    try:
        ranges = collection_ranges(stages)
    except CollectionContractError as exc:
        raise LoopContractError(str(exc)) from exc
    if ranges and pattern_kind == "fan_out":
        raise LoopContractError("collection ranges require a serial workflow pattern")
    item_multipliers = [1 for _ in stages]
    for start, end in ranges.items():
        for index in range(start + 1, end + 1):
            item_multipliers[index] = stages[start]["iteration"]["max_items"]
    prior: dict[str, dict] = {}
    visit_counts = [1 for _ in stages]
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
            for repeated in range(target["index"], index + 1):
                visit_counts[repeated] += edge["max_rounds"] - 1
            if edge["when"] == "gate_rejected":
                review_edges += 1
        prior[key] = {
            "index": index,
            "kind": stage["kind"],
            "explicit_key": bool(stage.get("output_key")),
        }
    if (
        sum(
            visits * items
            for visits, items in zip(visit_counts, item_multipliers, strict=True)
        )
        > MAX_STAGE_VISITS
    ):
        raise LoopContractError(
            f"workflow return edges exceed {MAX_STAGE_VISITS} maximum stage visits"
        )
    first_agent = next(
        (stage for stage in stages if stage["kind"] == "agent_dispatch"), None
    )
    execution_units = 0
    for stage, visits, items in zip(
        stages, visit_counts, item_multipliers, strict=True
    ):
        attempts = (
            validate_stage_attempts(stage.get("max_attempts", 3))
            if stage["kind"] in ("agent_dispatch", "workflow")
            else 1
        )
        fanout_value = stage.get("fan_out", stage.get("fan_out_count"))
        dynamic = stage.get("fan_out_dynamic", False)
        if type(dynamic) is not bool or isinstance(fanout_value, bool):
            raise LoopContractError("fan-out must use explicit count and dynamic types")
        # The executor chooses the first agent wherever it appears. Static
        # positive counts take precedence; no explicit count makes that
        # FAN_OUT-pattern agent dynamic, even without a dynamic stage flag.
        if isinstance(fanout_value, int) and 1 <= fanout_value <= 50:
            fanout = fanout_value
        elif fanout_value == "dynamic" or dynamic:
            if fanout_value not in (None, 0, "dynamic"):
                raise LoopContractError(
                    "fan-out must be bounded between 1 and 50 branches"
                )
            fanout = 50
        elif fanout_value is None or fanout_value == 0:
            fanout = 50 if pattern_kind == "fan_out" and stage is first_agent else 1
        else:
            raise LoopContractError("fan-out must be bounded between 1 and 50 branches")
        execution_units += visits * items * attempts * max(1, fanout)
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
    if edge["when"] == "always":
        return not stage_failed
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
