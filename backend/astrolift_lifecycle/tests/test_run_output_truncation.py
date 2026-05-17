"""Tests for the ``output`` field on ScheduledJobRun + CommandRun (#427).

The GraphQL surface caps ``output`` at the last 200 lines of the raw
``log_excerpt`` so the inline row-expand stays snappy regardless of
how chatty the job's container was. The full tail still lives behind
the per-app logs surface — the UI footer flags truncation so the
operator knows the rest is one click away.
"""

from __future__ import annotations

from astrolift_lifecycle.schema.types import (
    _RUN_OUTPUT_LINES,
    _last_n_lines,
)


def test_last_n_lines_returns_text_untouched_when_under_cap():
    text = "one\ntwo\nthree"
    assert _last_n_lines(text) == text


def test_last_n_lines_handles_empty_input():
    assert _last_n_lines("") == ""


def test_last_n_lines_returns_tail_when_over_cap():
    lines = [f"line-{i}" for i in range(_RUN_OUTPUT_LINES + 50)]
    text = "\n".join(lines)
    out = _last_n_lines(text)
    out_lines = out.split("\n")
    assert len(out_lines) == _RUN_OUTPUT_LINES
    assert out_lines[0] == "line-50"
    assert out_lines[-1] == f"line-{_RUN_OUTPUT_LINES + 49}"


def test_last_n_lines_default_is_200():
    assert _RUN_OUTPUT_LINES == 200


def test_last_n_lines_preserves_blank_lines_in_tail():
    """Blank lines inside the tail are meaningful (separators in a
    test runner's output, etc.) — splitlines + join preserves them."""
    head = ["junk"] * (_RUN_OUTPUT_LINES + 5)
    tail_with_blanks = ["start", "", "middle", "", "end"]
    text = "\n".join(head + tail_with_blanks)
    out = _last_n_lines(text)
    assert out.endswith("start\n\nmiddle\n\nend")
