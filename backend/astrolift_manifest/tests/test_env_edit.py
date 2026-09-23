"""Tests for the [env] block TOML editor (#279)."""

from __future__ import annotations

from astrolift_manifest.env_edit import (
    delete_app_env_key,
    parse_dotenv,
    read_app_env,
    redact_dotenv_values,
    redact_env_values,
    set_app_env_keys,
)

_BASE = """\
astrolift_version = 1
[app]
name = "hello"
slug = "hello-app"

[env]
DATABASE_URL = "postgres://x"
LOG_LEVEL = "info"

[[workloads]]
name = "web"
kind = "service"
"""


def test_read_app_env() -> None:
    env = read_app_env(_BASE)
    assert env == {
        "DATABASE_URL": "postgres://x",
        "LOG_LEVEL": "info",
    }


def test_read_app_env_missing_section() -> None:
    text = '[app]\nname = "x"\nslug = "x"\n'
    assert read_app_env(text) == {}


def test_read_app_env_empty_input() -> None:
    assert read_app_env("") == {}


def test_read_app_env_malformed_toml() -> None:
    """Bad TOML returns empty rather than raising — caller decides
    whether to surface the parse error."""
    assert read_app_env("not [valid toml") == {}


def test_set_app_env_keys_adds_new() -> None:
    out = set_app_env_keys(_BASE, {"NEW_KEY": "new_value"})
    parsed = read_app_env(out)
    assert parsed["NEW_KEY"] == "new_value"
    # Existing keys preserved
    assert parsed["DATABASE_URL"] == "postgres://x"


def test_set_app_env_keys_overwrites_existing() -> None:
    out = set_app_env_keys(_BASE, {"LOG_LEVEL": "debug"})
    parsed = read_app_env(out)
    assert parsed["LOG_LEVEL"] == "debug"
    assert parsed["DATABASE_URL"] == "postgres://x"


def test_set_app_env_keys_creates_section_when_absent() -> None:
    text = '[app]\nname = "x"\nslug = "x"\n'
    out = set_app_env_keys(text, {"K": "V"})
    assert read_app_env(out) == {"K": "V"}


def test_delete_app_env_key_removes() -> None:
    out, removed = delete_app_env_key(_BASE, "LOG_LEVEL")
    assert removed is True
    parsed = read_app_env(out)
    assert "LOG_LEVEL" not in parsed
    assert parsed == {"DATABASE_URL": "postgres://x"}


def test_delete_app_env_key_missing_returns_false() -> None:
    out, removed = delete_app_env_key(_BASE, "NEVER")
    assert removed is False
    assert out == _BASE  # unchanged


def test_delete_app_env_key_drops_empty_section() -> None:
    """Removing the last entry should drop the [env] table."""
    text = '[env]\nONLY = "key"\n'
    out, removed = delete_app_env_key(text, "ONLY")
    assert removed
    assert read_app_env(out) == {}


def test_round_trip_preserves_other_top_level_keys() -> None:
    """The TOML round-trip via tomli_w may reformat but must
    preserve all top-level keys + tables."""
    import tomllib

    out = set_app_env_keys(_BASE, {"K": "V"})
    parsed = tomllib.loads(out)
    assert parsed["app"]["name"] == "hello"
    assert parsed["workloads"][0]["name"] == "web"
    assert "env" in parsed


def test_parse_dotenv_basic() -> None:
    text = """\
# a comment
KEY1=value1
KEY2 = value2
KEY3="quoted"
KEY4='single quoted'

# trailing blanks below

"""
    parsed = parse_dotenv(text)
    assert parsed == {
        "KEY1": "value1",
        "KEY2": "value2",
        "KEY3": "quoted",
        "KEY4": "single quoted",
    }


def test_parse_dotenv_strips_export() -> None:
    text = "export FOO=bar\nexport BAZ=qux\n"
    parsed = parse_dotenv(text)
    assert parsed == {"FOO": "bar", "BAZ": "qux"}


def test_parse_dotenv_skips_invalid_names() -> None:
    text = "1BAD=skip\nGOOD=keep\nBAD-KEY=skip\n"
    parsed = parse_dotenv(text)
    assert parsed == {"GOOD": "keep"}


def test_parse_dotenv_skips_lines_without_equals() -> None:
    text = "no equals here\nKEY=value\n"
    parsed = parse_dotenv(text)
    assert parsed == {"KEY": "value"}


def test_parse_dotenv_empty_value() -> None:
    parsed = parse_dotenv("EMPTY=\n")
    assert parsed == {"EMPTY": ""}


# ---- redact_env_values (#1920) ------------------------------------


def test_redact_env_values_masks_values_keeps_keys() -> None:
    out = redact_env_values(_BASE)
    parsed = read_app_env(out)
    assert set(parsed.keys()) == {"DATABASE_URL", "LOG_LEVEL"}
    assert "postgres://x" not in out
    assert all(v == "[REDACTED]" for v in parsed.values())


def test_redact_env_values_preserves_other_tables() -> None:
    out = redact_env_values(_BASE)
    import tomllib

    parsed = tomllib.loads(out)
    assert parsed["app"]["name"] == "hello"
    assert parsed["app"]["slug"] == "hello-app"
    assert parsed["workloads"][0]["name"] == "web"
    assert parsed["astrolift_version"] == 1


def test_redact_env_values_no_env_section_unchanged() -> None:
    text = '[app]\nname = "x"\nslug = "x"\n'
    assert redact_env_values(text) == text


def test_redact_env_values_empty_input_unchanged() -> None:
    assert redact_env_values("") == ""


def test_redact_env_values_malformed_toml_unchanged() -> None:
    """Bad TOML returns unchanged — same fail-safe posture as
    ``read_app_env``; there's no parsed [env] table to mask."""
    text = "not [valid toml"
    assert redact_env_values(text) == text


# ---- redact_dotenv_values (#1920) ----------------------------------


def test_redact_dotenv_values_masks_values_keeps_keys() -> None:
    text = 'API_KEY=sk-live-secret\nLOG_LEVEL="debug"\n'
    out = redact_dotenv_values(text)
    assert "sk-live-secret" not in out
    assert "debug" not in out
    assert "API_KEY=[REDACTED]" in out
    assert "LOG_LEVEL=[REDACTED]" in out


def test_redact_dotenv_values_preserves_comments_and_blank_lines() -> None:
    text = "# a comment\n\nAPI_KEY=secret\n"
    out = redact_dotenv_values(text)
    lines = out.splitlines()
    assert lines[0] == "# a comment"
    assert lines[1] == ""
    assert lines[2] == "API_KEY=[REDACTED]"


def test_redact_dotenv_values_preserves_export_prefix() -> None:
    text = "export API_KEY=secret\n"
    out = redact_dotenv_values(text)
    assert out == "export API_KEY=[REDACTED]\n"


def test_redact_dotenv_values_leaves_unparseable_lines_untouched() -> None:
    text = "not a kv line\n1INVALID=secret\n"
    assert redact_dotenv_values(text) == text


def test_redact_dotenv_values_empty_input_unchanged() -> None:
    assert redact_dotenv_values("") == ""
