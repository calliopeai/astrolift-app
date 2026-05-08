"""
Tests for the structured logging stack (P0.5a #178).

These run without a database — they assert formatter behavior and
the env-driven LOGGING config.
"""

from __future__ import annotations

import io
import json
import logging

from core.logging import (
    JsonFormatter,
    SensitiveFieldRedactor,
    TextFormatter,
    build_logging_config,
)
from core.request_context import (
    reset_request_id,
    set_request_id,
)
from core.tenancy import TenantContext, set_current_tenant


def _capture(formatter: logging.Formatter, name: str = "test") -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger(name)
    # Reset so repeated tests don't pile handlers.
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, stream


def _last_json(stream: io.StringIO) -> dict:
    line = stream.getvalue().strip().splitlines()[-1]
    return json.loads(line)


# ---------- JsonFormatter ----------------------------------------


def test_json_formatter_emits_required_keys():
    logger, stream = _capture(JsonFormatter())
    logger.info("hello world")
    payload = _last_json(stream)

    for key in ("timestamp", "level", "logger", "message"):
        assert key in payload, f"missing key {key}: {payload}"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["message"] == "hello world"


def test_json_formatter_extra_fields_passthrough():
    logger, stream = _capture(JsonFormatter())
    logger.info("processing", extra={"app_id": "abc-123", "count": 5})

    payload = _last_json(stream)
    assert payload["app_id"] == "abc-123"
    assert payload["count"] == 5


def test_json_formatter_includes_request_id_from_contextvar():
    token = set_request_id("01TEST_REQUEST_ID_FOR_LOGGING0")
    try:
        logger, stream = _capture(JsonFormatter())
        logger.info("during request")
        payload = _last_json(stream)
        assert payload["request_id"] == "01TEST_REQUEST_ID_FOR_LOGGING0"
    finally:
        reset_request_id(token)


def test_json_formatter_redacts_sensitive_keys_in_extras():
    logger, stream = _capture(JsonFormatter())
    logger.info(
        "auth attempt",
        extra={
            "username": "alice",
            "password": "supersecret",
            "api_key": "k-1234",
            "nested": {"token": "deep", "ok": True},
        },
    )
    payload = _last_json(stream)
    assert payload["username"] == "alice"
    assert payload["password"] == "***REDACTED***"
    assert payload["api_key"] == "***REDACTED***"
    assert payload["nested"]["token"] == "***REDACTED***"
    assert payload["nested"]["ok"] is True


def test_json_formatter_includes_tenant_context():
    token = set_current_tenant(TenantContext(organization_id=42, actor_user_id=7))
    try:
        logger, stream = _capture(JsonFormatter())
        logger.warning("tenant scoped log")
        payload = _last_json(stream)
        assert payload["organization_id"] == 42
        assert payload["actor_user_id"] == 7
    finally:
        # contextvar reset
        from core.tenancy import clear_current_tenant

        clear_current_tenant(token)


def test_json_formatter_serializes_non_native_types():
    class Custom:
        def __repr__(self) -> str:
            return "<Custom>"

    logger, stream = _capture(JsonFormatter())
    logger.info("custom", extra={"obj": Custom()})
    payload = _last_json(stream)
    assert payload["obj"] == "<Custom>"


def test_json_formatter_records_exception():
    logger, stream = _capture(JsonFormatter())
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.exception("explosion")

    payload = _last_json(stream)
    assert "RuntimeError: boom" in payload["exception"]


# ---------- TextFormatter ----------------------------------------


def test_text_formatter_human_readable():
    logger, stream = _capture(TextFormatter())
    logger.info("hello", extra={"app": "demo"})

    line = stream.getvalue().strip().splitlines()[-1]
    assert "INFO" in line
    assert "test:" in line
    assert "hello" in line
    assert "app=demo" in line


def test_text_formatter_redacts_sensitive_keys():
    logger, stream = _capture(TextFormatter())
    logger.info("auth", extra={"password": "topsecret"})

    line = stream.getvalue().strip().splitlines()[-1]
    assert "topsecret" not in line
    assert "***REDACTED***" in line


# ---------- SensitiveFieldRedactor -------------------------------


def test_sensitive_redactor_matches_default_patterns():
    r = SensitiveFieldRedactor()
    assert r.is_sensitive("password")
    assert r.is_sensitive("PASSWORD")
    assert r.is_sensitive("user_password")
    assert r.is_sensitive("api_key")
    assert r.is_sensitive("Authorization")
    assert r.is_sensitive("session_id")
    assert r.is_sensitive("private_key")
    assert not r.is_sensitive("username")
    assert not r.is_sensitive("email")


def test_sensitive_redactor_recurses_into_nested():
    r = SensitiveFieldRedactor()
    out = r.redact(
        {
            "ok": 1,
            "auth": {"token": "x", "ok": 2},
            "items": [{"secret": "y", "ok": 3}],
        }
    )
    assert out["ok"] == 1
    assert out["auth"] == "***REDACTED***"  # whole dict masked because key matches
    assert out["items"][0]["secret"] == "***REDACTED***"
    assert out["items"][0]["ok"] == 3


# ---------- build_logging_config ---------------------------------


def test_build_logging_config_defaults_to_json_stdout():
    cfg = build_logging_config()
    assert cfg["handlers"]["console"]["formatter"] == "json"
    assert cfg["handlers"]["console"]["stream"] == "ext://sys.stdout"
    assert cfg["root"]["level"] == "INFO"


def test_build_logging_config_text_format():
    cfg = build_logging_config(format_="text", level="DEBUG", destination="stderr")
    assert cfg["handlers"]["console"]["formatter"] == "text"
    assert cfg["handlers"]["console"]["stream"] == "ext://sys.stderr"
    assert cfg["root"]["level"] == "DEBUG"


def test_build_logging_config_invalid_format_falls_back_to_json():
    cfg = build_logging_config(format_="bogus")
    assert cfg["handlers"]["console"]["formatter"] == "json"


def test_build_logging_config_ecs_includes_ecs_formatter():
    cfg = build_logging_config(format_="ecs")
    assert "ecs" in cfg["formatters"]
    assert cfg["handlers"]["console"]["formatter"] == "ecs"
