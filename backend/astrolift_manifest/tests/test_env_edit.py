"""Tests for the [env] block TOML editor (#279)."""

from __future__ import annotations

from astrolift_manifest.env_edit import (
    delete_app_env_key,
    parse_dotenv,
    read_app_env,
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


def test_read_app_env_skips_keys_a_kubernetes_secret_cannot_carry() -> None:
    """A repo manifest can hold keys no mutation writes. They are skipped,
    not passed on to a Secret apply that would reject them (#1758)."""
    text = (
        "[env]\n"
        '"has-a-dash" = "a"\n'
        '"1LEADING_DIGIT" = "b"\n'
        '"HAS SPACE" = "c"\n'
        '"CLÉ" = "d"\n'
        'GOOD_KEY = "z"\n'
    )
    assert read_app_env(text) == {"GOOD_KEY": "z"}


def test_read_app_env_skips_tables_and_arrays() -> None:
    text = '[env]\nGOOD_KEY = "z"\nLIST = [1, 2]\n\n[env.NESTED]\na = 1\n'
    assert read_app_env(text) == {"GOOD_KEY": "z"}


def test_read_app_env_keeps_numeric_scalars() -> None:
    assert read_app_env("[env]\nCOUNT = 3\nRATIO = 1.5\n") == {"COUNT": "3", "RATIO": "1.5"}


def test_parse_dotenv_skips_non_ascii_keys() -> None:
    assert parse_dotenv("CLÉ=x\nKEY=y\n") == {"KEY": "y"}


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
