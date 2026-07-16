"""Smoke tests for the new SDK protocols (#66 trace, #56 build,
#57 event). Verifies the dataclass shapes import and instantiate."""

from __future__ import annotations

from _sdk.build import BuildResult, BuildSpec
from _sdk.event import EventEnvelope, ForwardResult
from _sdk.trace import SpanRef, TraceSummary


def test_build_spec_defaults() -> None:
    spec = BuildSpec(source_uri="git+https://example.com/repo.git")
    assert spec.dockerfile_path == "Dockerfile"
    assert spec.target_platform == "linux/amd64"
    assert spec.build_args == {}
    assert spec.cache_from == []


def test_build_result_failure_path() -> None:
    result = BuildResult(
        success=False,
        image_uri="",
        digest="",
        duration_seconds=12.5,
        errors=["dockerfile not found"],
    )
    assert result.success is False
    assert result.errors == ["dockerfile not found"]


def test_event_envelope_defaults() -> None:
    e = EventEnvelope(
        timestamp="2026-05-09T00:00:00Z",
        severity="warning",
        source="kube-event",
        namespace="acme-api",
        cluster="aws-prod",
        title="Pod restart",
        body="api-0 restarted (CrashLoopBackoff)",
    )
    assert e.labels == {}
    assert e.related == []


def test_forward_result_aggregates() -> None:
    fr = ForwardResult(accepted=10, rejected=2, errors=["dup", "rate"])
    assert fr.accepted == 10
    assert fr.rejected == 2


def test_trace_summary_shape() -> None:
    t = TraceSummary(
        trace_id="abc",
        root_service="api",
        root_operation="GET /v1/users",
        span_count=12,
        duration_ms=145.7,
        status_code="OK",
    )
    assert t.span_count == 12


def test_span_ref_with_attributes() -> None:
    s = SpanRef(
        trace_id="abc",
        span_id="def",
        parent_span_id=None,
        operation="db.query",
        service="api",
        start_time="2026-05-09T00:00:00Z",
        duration_ms=12.0,
        status_code="OK",
        attributes={"db.statement": "SELECT 1"},
    )
    assert s.attributes["db.statement"] == "SELECT 1"
    assert s.parent_span_id is None
