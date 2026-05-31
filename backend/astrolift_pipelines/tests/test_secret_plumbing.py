"""Tests for pipeline secret plumbing — resolution, K8s materialization, redaction (#84)."""

from __future__ import annotations

import pytest

from astrolift_pipelines.secret_plumbing import (
    SECRET_REDACTION_MARKER,
    SecretRedactor,
    SecretResolutionError,
    build_redactor_for_job,
    make_env_from_refs,
)


# ---------------------------------------------------------------------------
# SecretRedactor
# ---------------------------------------------------------------------------


def test_redactor_replaces_known_value():
    redactor = SecretRedactor(["super-secret-value"])
    assert redactor.redact("the value is super-secret-value ok") == f"the value is {SECRET_REDACTION_MARKER} ok"


def test_redactor_longer_secrets_first():
    # If a long secret contains a short one, the long one must be replaced first.
    redactor = SecretRedactor(["abc", "abcdefgh"])
    result = redactor.redact("key=abcdefgh")
    # Should redact the longer match first, not leave "***efgh"
    assert "abcdefgh" not in result
    assert result == f"key={SECRET_REDACTION_MARKER}"


def test_redactor_multiple_secrets_in_one_line():
    redactor = SecretRedactor(["token1", "pass2"])
    line = "using token1 and pass2 together"
    result = redactor.redact(line)
    assert "token1" not in result
    assert "pass2" not in result


def test_redactor_leaves_non_secret_values():
    redactor = SecretRedactor(["mysecret"])
    assert redactor.redact("just a normal log line") == "just a normal log line"


def test_redactor_empty_secret_values_ignored():
    redactor = SecretRedactor(["", "real-secret"])
    result = redactor.redact("contains real-secret here")
    assert "real-secret" not in result


def test_redact_lines_handles_multiline():
    redactor = SecretRedactor(["TOKEN"])
    text = "line1\nline with TOKEN\nline3"
    result = redactor.redact_lines(text)
    lines = result.splitlines()
    assert lines[0] == "line1"
    assert SECRET_REDACTION_MARKER in lines[1]
    assert "TOKEN" not in lines[1]
    assert lines[2] == "line3"


def test_build_redactor_from_bundle():
    bundle = {"DB_PASS": "s3cr3t", "API_KEY": "hunter2"}
    redactor = build_redactor_for_job(bundle)
    assert redactor.redact("db pass is s3cr3t") == f"db pass is {SECRET_REDACTION_MARKER}"
    assert redactor.redact("key=hunter2") == f"key={SECRET_REDACTION_MARKER}"


# ---------------------------------------------------------------------------
# make_env_from_refs
# ---------------------------------------------------------------------------


def test_make_env_from_refs_structure():
    refs = make_env_from_refs("my-secret-name", ["DB_URL", "API_KEY"])
    assert len(refs) == 2
    db = next(r for r in refs if r["name"] == "DB_URL")
    assert db["valueFrom"]["secretKeyRef"]["name"] == "my-secret-name"
    assert db["valueFrom"]["secretKeyRef"]["key"] == "DB_URL"


def test_make_env_from_refs_empty():
    assert make_env_from_refs("secret", []) == []
