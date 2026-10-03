"""Compatibility against available published source/tag projections, not invented minor releases."""

import re
from pathlib import Path

import pytest
from graphql import build_schema, find_breaking_changes, find_dangerous_changes, parse, validate

from config.schema import schema


@pytest.mark.parametrize("snapshot", ["n1", "n2", "tag"])
def test_published_observability_contract_types_arguments_defaults_preserved(snapshot):
    baseline = build_schema(
        (Path(__file__).parent / "fixtures" / "trace_schema" / f"{snapshot}.graphql").read_text()
    )
    current = schema._schema
    assert find_breaking_changes(baseline, current) == []
    changes = find_dangerous_changes(baseline, current)
    expected = {"environmentId"}
    if snapshot == "tag":
        expected |= {
            "previewId",
            "expectedEnvironmentId",
            "ifMatchPreviewVersion",
            "ifMatchEnvironmentVersion",
        }
    assert {change.description for change in changes} == {
        f"An optional arg {argument} on Query.astroliftAppLogs was added." for argument in expected
    }
    legacy = parse("""query Legacy($app: String!, $start: String!, $end: String!, $from: DateTime!, $to: DateTime!, $trace: String!) {
      astroliftAppTraces(appSlug:$app, since:$start, until:$end) { traceId rootService rootOperation spanCount durationMs statusCode }
      astroliftTraceSpans(appSlug:$app, traceId:$trace) { traceId spanId parentSpanId operation service startTime durationMs statusCode attributes }
      astroliftAppLogs(appSlug:$app,since:$from,until:$to) {items {podName container timestamp message level stream} nextCursor reachedRetention historicalAvailable totalCount reason}
    }""")
    if snapshot == "tag":
        # This tag predates trace roots and the log reason field; verify its real read surface.
        legacy = parse("""query TaggedLogs($app:String!,$from:DateTime!,$to:DateTime!) {
          astroliftAppLogs(appSlug:$app,since:$from,until:$to) {items {podName container timestamp message level stream} nextCursor reachedRetention historicalAvailable totalCount}
        }""")
    assert validate(baseline, legacy) == []
    assert validate(current, legacy) == []


def test_all_actual_new_frontend_operations_validate_against_runtime_schema():
    source = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "graphql"
        / "observability"
        / "explorers.queries.ts"
    )
    operations = re.findall(r"gql`(.*?)`", source.read_text(), re.S)
    assert len(operations) == 5
    for operation in operations:
        assert validate(schema._schema, parse(operation)) == []
