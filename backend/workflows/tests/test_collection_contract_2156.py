"""Serial iteration bounds match actual body scheduling, without framework IO."""

import pytest

from workflows.back_edges import LoopContractError, validate_loop_plan
from workflows.collections import (
    CollectionContractError,
    bounded_json,
    collection_binding,
    collection_items,
    collection_ranges,
    format_record,
    validate_iteration,
)


def plan(*, cap=3, body_kind="checkpoint", attempts=3):
    return [
        {
            "order": 0,
            "kind": "collection",
            "output_key": "each",
            "iteration": {"max_items": cap, "body_end": "body", "items_path": "items"},
        },
        {"order": 1, "kind": body_kind, "output_key": "body", "max_attempts": attempts},
        {"order": 2, "kind": "checkpoint", "output_key": "done"},
    ]


@pytest.mark.parametrize("cap", [None, False, 0, -1, 51, 3.0, "3"])
def test_collection_refuses_missing_or_invalid_caps(cap):
    with pytest.raises(CollectionContractError, match="max_items"):
        validate_iteration(
            {"max_items": cap, "body_end": "body", "items_path": "items"},
            kind="collection",
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        "items",
        [1],
        [{"x": float("nan")}],
        [{"x": float("inf")}],
        [{"x": object()}],
    ],
)
def test_collection_refuses_unknown_or_nonfinite_data(value):
    with pytest.raises(CollectionContractError):
        collection_items({"max_items": 3, "items_path": "items"}, {"items": value})


def test_empty_collection_is_verified_and_oversize_is_not_truncated():
    assert (
        collection_items({"max_items": 3, "items_path": "items"}, {"items": []}) == []
    )
    with pytest.raises(CollectionContractError, match="exceeds"):
        collection_items({"max_items": 3, "items_path": "items"}, {"items": [{}] * 4})
    with pytest.raises(CollectionContractError, match="unavailable"):
        collection_items({"max_items": 3, "items_path": "missing.items"}, {"items": []})


@pytest.mark.parametrize("kind", ["agent_dispatch", "workflow"])
def test_body_attempts_multiply_all_items_and_review_returns(kind):
    validate_loop_plan(plan(cap=2, body_kind=kind, attempts=20), pattern_kind="chained")
    with pytest.raises(LoopContractError, match="execution units"):
        validate_loop_plan(
            plan(cap=50, body_kind=kind, attempts=20), pattern_kind="chained"
        )
    returned = plan(cap=20, body_kind=kind, attempts=10)
    returned[-1]["back_edge"] = {
        "to": "each",
        "when": "output_equals",
        "path": "again",
        "value": True,
        "max_rounds": 3,
    }
    with pytest.raises(LoopContractError, match="execution units"):
        validate_loop_plan(returned, pattern_kind="chained")


@pytest.mark.parametrize("source,target", [(1, "each"), (2, "body")])
def test_review_returns_cannot_cross_a_serial_body_boundary(source, target):
    stages = plan()
    stages[source]["back_edge"] = {
        "to": target,
        "when": "output_equals",
        "path": "again",
        "value": True,
        "max_rounds": 2,
    }
    with pytest.raises(LoopContractError, match="enter or leave"):
        validate_loop_plan(stages, pattern_kind="chained")


def test_internal_body_review_is_bounded_and_ranges_are_exact():
    stages = plan()
    stages[1]["output_key"] = "draft"
    stages.insert(
        2,
        {
            "order": 2,
            "kind": "human_gate",
            "output_key": "body",
            "back_edge": {"to": "draft", "when": "gate_rejected", "max_rounds": 2},
        },
    )
    stages[-1]["order"] = 3
    assert collection_ranges(stages) == {0: 2}
    validate_loop_plan(stages, pattern_kind="chained")


@pytest.mark.parametrize(
    "field,value", [("body_end", "each"), ("body_end", "missing"), ("items", [])]
)
def test_invalid_or_ambiguous_ranges_are_refused(field, value):
    stages = plan()
    stages[0]["iteration"][field] = value
    with pytest.raises(LoopContractError):
        validate_loop_plan(stages, pattern_kind="chained")


def test_parallel_or_nested_body_is_not_silently_serialized():
    stages = plan()
    stages[1]["fan_out_count"] = 2
    with pytest.raises(LoopContractError, match="serially"):
        validate_loop_plan(stages, pattern_kind="chained")
    stages[1].pop("fan_out_count")
    stages[1].update(
        kind="collection",
        iteration={"max_items": 2, "items_path": "items", "body_end": "done"},
    )
    with pytest.raises(LoopContractError, match="body kind"):
        validate_loop_plan(stages, pattern_kind="chained")


def test_source_parser_preserves_default_missing_fields_literal_braces_and_timestamp():
    assert format_record(
        {
            "source_format": "langflow_parser",
            "pattern": "{{note}} {text} {missing}",
            "separator": "\n",
        },
        {"text": "second"},
        timestamp="2026-10-02 12:00:01.000123 ",
    ) == {"text": "{note} second ", "timestamp": "2026-10-02 12:00:01.000123 "}


@pytest.mark.parametrize(
    "pattern",
    ["{text.__class__}", "{text[0]}", "{text!r}", "{text:100000000}", "{", "{}"],
)
def test_unproved_parser_modes_are_refused(pattern):
    with pytest.raises(CollectionContractError):
        validate_iteration(
            {"source_format": "langflow_parser", "pattern": pattern, "separator": "\n"},
            kind="format_record",
        )


@pytest.mark.parametrize(
    "value", [{1: "key"}, {"value": "x" * 262145}, {"values": [0] * 16384}]
)
def test_inputs_and_aggregate_outputs_have_bounded_json_shape_and_payload(value):
    with pytest.raises(CollectionContractError):
        bounded_json(value)


def test_recursive_or_deep_records_are_refused_without_recursive_validation():
    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(CollectionContractError, match="structure"):
        bounded_json(cycle)


@pytest.mark.parametrize(
    "patch",
    [
        {"owner_order": True},
        {"item_count": True},
        {"item_count": 4},
        {"max_items": "3"},
        {"body_stage_ids": ["1", "1"]},
        {"body_stage_ids": "1"},
        {"body_stage_ids": ["foreign"]},
    ],
)
def test_runtime_parent_binding_is_typed_and_bounded(patch):
    good = {
        "owner_order": 0,
        "item_count": 2,
        "max_items": 3,
        "body_stage_ids": ["1", "2"],
    }
    assert collection_binding({"collection": good}) == good
    assert collection_binding({"collection": {**good, **patch}}) is None
    assert collection_binding([]) is None
