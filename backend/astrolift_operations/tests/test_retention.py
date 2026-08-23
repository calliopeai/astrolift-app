"""Tests for retention policy + JSONL streaming export (#161)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from astrolift_operations.retention import (
    DEFAULT_RETENTION_DAYS,
    EXPORT_SCHEMA_VERSION,
    RetentionPolicy,
    cutoff_for,
    export_to_sink,
    serialize_jsonl,
    verify_chain,
)

UTC = UTC


# ---- minimal in-memory row stand-ins ---------------------------------
# The retention/serialization code is pure; we stub the model rows so
# tests don't need Django ORM setup. Real Event / AuditEvent rows have
# the same attribute names.


@dataclasses.dataclass
class _FakeEvent:
    guid: object
    occurred_at: datetime
    organization_id: int
    team_id: int | None
    project_id: int | None
    registered_app_id: int | None
    event_type: str
    resource_kind: str
    resource_id: str
    actor_user_id: int | None
    request_id: str
    trace_id: str
    payload: dict


@dataclasses.dataclass
class _FakeAudit:
    """Field-for-field with ``AuditEvent``, which it was not before (#1594).

    It carried ``resource_kind`` / ``resource_id`` / ``payload`` because the
    exporter read those names. The model has ``target_kind`` / ``target_id``
    / ``data``, so the exporter raised AttributeError on the first real row
    it was ever handed while this suite stayed green: the fake was shaped
    like the code rather than like the thing the code exports.

    Keep this in step with ``astrolift_operations/models/audit_event.py``. A
    fake that drifts from its model tests the fake.
    """

    occurred_at: datetime
    organization_id: int
    actor_kind: str
    actor_id: str
    actor_display: str
    action: str
    decision: str
    target_kind: str
    target_id: str
    target_slug: str
    data: dict
    reasoning: list = dataclasses.field(default_factory=list)
    request_id: str = ""
    request_ip: str = ""


def _ev(**kw) -> _FakeEvent:
    base = {
        "guid": uuid4(),
        "occurred_at": datetime(2026, 5, 1, 12, tzinfo=UTC),
        "organization_id": 1,
        "team_id": None,
        "project_id": None,
        "registered_app_id": 42,
        "event_type": "app.deployed",
        "resource_kind": "App",
        "resource_id": "42",
        "actor_user_id": 7,
        "request_id": "req-abc",
        "trace_id": "trace-xyz",
        "payload": {"deployment_id": 99},
    }
    base.update(kw)
    return _FakeEvent(**base)


def _aud(**kw) -> _FakeAudit:
    base = {
        "occurred_at": datetime(2026, 5, 1, 12, tzinfo=UTC),
        "organization_id": 1,
        "actor_kind": "User",
        "actor_id": "7",
        "actor_display": "ada@acme.test",
        "action": "app.create",
        "decision": "allow",
        "target_kind": "App",
        "target_id": "42",
        "target_slug": "hello-app",
        "data": {"reason": "ok"},
    }
    base.update(kw)
    return _FakeAudit(**base)


# ---- retention policy --------------------------------------------------


def test_default_retention_is_90_days():
    policy = RetentionPolicy(stream="event")
    assert policy.retention_days == DEFAULT_RETENTION_DAYS


def test_policy_rejects_bad_stream():
    with pytest.raises(ValueError):
        RetentionPolicy(stream="bogus")


def test_policy_rejects_nonpositive_days():
    with pytest.raises(ValueError):
        RetentionPolicy(stream="event", retention_days=0)


def test_cutoff_for_subtracts_days():
    policy = RetentionPolicy(stream="event", retention_days=30)
    now = datetime(2026, 5, 1, tzinfo=UTC)
    assert cutoff_for(policy, now=now) == now - timedelta(days=30)


def test_cutoff_rejects_naive_now():
    policy = RetentionPolicy(stream="event")
    with pytest.raises(ValueError):
        cutoff_for(policy, now=datetime(2026, 5, 1))


# ---- JSONL serialization (event, unchained) --------------------------


def test_serialize_event_jsonl_one_line_per_row():
    rows = [_ev(), _ev(event_type="app.scaled")]
    out = serialize_jsonl(rows, stream="event")
    lines = out.strip().split(b"\n")
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["schema_version"] == EXPORT_SCHEMA_VERSION
    assert rec0["stream"] == "event"
    assert rec0["event_type"] == "app.deployed"
    assert "integrity" not in rec0  # event stream defaults unchained


def test_serialize_event_iso8601_z_suffix():
    rows = [_ev(occurred_at=datetime(2026, 5, 1, 12, tzinfo=UTC))]
    out = serialize_jsonl(rows, stream="event")
    rec = json.loads(out.strip())
    # 'Z' suffix is the auditor-friendly form; '+00:00' parses but
    # tools like S3 Athena and Loki expect Z.
    assert rec["occurred_at"].endswith("Z")
    assert "T" in rec["occurred_at"]


def test_serialize_audit_includes_chain_by_default():
    rows = [_aud(action="a.x"), _aud(action="a.y"), _aud(action="a.z")]
    out = serialize_jsonl(rows, stream="audit", chained=True)
    lines = [json.loads(raw) for raw in out.strip().split(b"\n")]
    assert lines[0]["integrity"]["hash_prev"] == "genesis"
    # each line's hash_prev == prior line's hash_self
    assert lines[1]["integrity"]["hash_prev"] == lines[0]["integrity"]["hash_self"]
    assert lines[2]["integrity"]["hash_prev"] == lines[1]["integrity"]["hash_self"]


def test_chain_is_deterministic():
    """Same input → byte-identical output. Required so an auditor can
    re-export from the source DB and compare hashes."""
    rows = [_aud(action="a"), _aud(action="b")]
    a = serialize_jsonl(rows, stream="audit", chained=True)
    b = serialize_jsonl(rows, stream="audit", chained=True)
    assert a == b


# ---- chain verification ----------------------------------------------


def test_verify_chain_passes_on_intact_export():
    rows = [_aud(action="a"), _aud(action="b"), _aud(action="c")]
    payload = serialize_jsonl(rows, stream="audit", chained=True)
    assert verify_chain(payload) is True


def test_verify_chain_fails_on_tampered_line():
    """Mutating a serialized field invalidates that line's hash and
    breaks the chain — the whole export fails verification."""
    rows = [_aud(action="a"), _aud(action="b")]
    payload = serialize_jsonl(rows, stream="audit", chained=True)
    # Replace 'allow' with 'deny' on the first line
    tampered = payload.replace(b'"decision":"allow"', b'"decision":"deny"', 1)
    assert verify_chain(tampered) is False


def test_verify_chain_fails_on_missing_integrity():
    """A line without an integrity envelope can't be verified — fail
    closed rather than silently accepting partial chains."""
    rows = [_aud(action="a")]
    payload = serialize_jsonl(rows, stream="audit", chained=False)
    assert verify_chain(payload) is False


# ---- export pipeline / sink contract ---------------------------------


def test_export_to_sink_writes_audit_chain_to_partition_key():
    captured = {}

    def sink(b, key):
        captured["bytes"] = b
        captured["key"] = key

    rows = [_aud(action="a"), _aud(action="b")]
    n = export_to_sink(
        rows,
        stream="audit",
        org_slug="acme",
        partition_date=datetime(2026, 5, 1, tzinfo=UTC),
        sink=sink,
    )
    assert n > 0
    assert captured["key"] == "audit/acme/2026/05/01.jsonl"
    assert verify_chain(captured["bytes"]) is True


def test_export_event_stream_defaults_unchained():
    captured = {}

    def sink(b, key):
        captured["bytes"] = b

    rows = [_ev(), _ev(event_type="x")]
    export_to_sink(
        rows,
        stream="event",
        org_slug="acme",
        partition_date=datetime(2026, 5, 1, tzinfo=UTC),
        sink=sink,
    )
    rec0 = json.loads(captured["bytes"].splitlines()[0])
    assert "integrity" not in rec0


def test_export_with_no_rows_is_noop():
    """No 0-byte sentinel objects in the export bucket. Auditors
    should be able to tell 'silent day' (object missing) from 'worker
    died' (alert fired) — both producing the same 0-byte object would
    erase that distinction."""
    calls = []

    def sink(b, key):
        calls.append((b, key))

    n = export_to_sink(
        [],
        stream="event",
        org_slug="acme",
        partition_date=datetime(2026, 5, 1, tzinfo=UTC),
        sink=sink,
    )
    assert n == 0
    assert calls == []


def test_export_partition_key_includes_stream_and_org():
    captured = []

    def sink(b, key):
        return captured.append(key)

    export_to_sink(
        [_aud(action="a")],
        stream="audit",
        org_slug="org-1",
        partition_date=datetime(2025, 12, 31, tzinfo=UTC),
        sink=sink,
    )
    assert captured == ["audit/org-1/2025/12/31.jsonl"]
