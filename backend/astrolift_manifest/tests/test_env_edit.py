"""Tests for the [env] block TOML editor (#279)."""

from __future__ import annotations

import json
import tomllib

from astrolift_manifest.env_edit import (
    REDACTED_DOCUMENT,
    REDACTED_ENV_VALUE,
    REDACTED_LINE,
    delete_app_env_key,
    parse_dotenv,
    read_app_env,
    redact_dotenv_values,
    redact_env_values,
    resolve_masked_env_values,
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

_V = REDACTED_ENV_VALUE


def _marker(ordinal: int) -> str:
    return f"# [ASTROLIFT_REDACTED_ENV_COMMENT:{ordinal}]"


# Every [env] value shape the scanner has to place, comments inside and
# outside the section, odd spacing, and a multi-line string elsewhere
# whose text looks like an [env] section.
_HAND_EDITED = '''\
# astrolift.toml, hand-edited
astrolift_version = 1
name = "hello"   # the app

[env]
# rotated 2026-09-01; old value was sk-live-OLD-000
API_KEY   =   "sk-live-NEW-111"   # prod key
DB_URL = 'postgres://app:pw-222@db/app'
PEM = """
-----BEGIN PRIVATE KEY-----
MIIEv333
-----END PRIVATE KEY-----
"""
ESCAPED = "tab\\there \\"444\\""
"QUOTED-KEY" = "555"
PORT = 8080

[[workloads]]
name = "web"
kind = "deployment"
# not env: keep me
command = """
[env]
X = "not-a-secret"
"""
'''

_HAND_EDITED_MASKED = f'''\
# astrolift.toml, hand-edited
astrolift_version = 1
name = "hello"   # the app

[env]
{_marker(1)}
API_KEY   =   "{_V}"   {_marker(2)}
DB_URL = "{_V}"
PEM = "{_V}"
ESCAPED = "{_V}"
"QUOTED-KEY" = "{_V}"
PORT = "{_V}"

[[workloads]]
name = "web"
kind = "deployment"
# not env: keep me
command = """
[env]
X = "not-a-secret"
"""
'''

_SECRETS = ("sk-live-OLD-000", "sk-live-NEW-111", "pw-222", "MIIEv333", "444", "555", "8080")


def test_redact_env_values_masks_values_keeps_keys() -> None:
    out = redact_env_values(_BASE)
    parsed = read_app_env(out)
    assert set(parsed.keys()) == {"DATABASE_URL", "LOG_LEVEL"}
    assert "postgres://x" not in out
    assert all(v == REDACTED_ENV_VALUE for v in parsed.values())


def test_redact_env_values_preserves_other_tables() -> None:
    parsed = tomllib.loads(redact_env_values(_BASE))
    assert parsed["app"]["name"] == "hello"
    assert parsed["app"]["slug"] == "hello-app"
    assert parsed["workloads"][0]["name"] == "web"
    assert parsed["astrolift_version"] == 1


def test_redact_env_values_no_env_section_unchanged() -> None:
    text = '[app]\nname = "x"\nslug = "x"\n'
    assert redact_env_values(text) == text


def test_redact_env_values_empty_input_unchanged() -> None:
    assert redact_env_values("") == ""


def test_redact_env_values_edits_value_literals_in_place() -> None:
    """Only the [env] value literals and the [env] section's comments
    change; the rest is byte-identical, so a masked read can be saved
    back without reformatting the document."""
    assert redact_env_values(_HAND_EDITED) == _HAND_EDITED_MASKED


def test_redact_env_values_masks_env_comments() -> None:
    out = redact_env_values(_HAND_EDITED)
    for secret in _SECRETS:
        assert secret not in out
    assert "# the app" in out
    assert "# not env: keep me" in out
    assert 'X = "not-a-secret"' in out


def test_redact_env_values_falls_back_to_a_masked_rebuild() -> None:
    """A shape the in-place scan does not place (a nested env table)
    still comes back masked, via a full rebuild."""
    text = '[env]\nAPI_KEY = "sk-live-nested-1"\n\n[env.extra]\nINNER = "sk-live-nested-2"\n'
    out = redact_env_values(text)
    assert "sk-live-nested-1" not in out
    assert "sk-live-nested-2" not in out
    assert tomllib.loads(out)["env"] == {"API_KEY": _V, "extra": _V}


# ---- redact_env_values: a root `env` that isn't a table (#1944) ----


def test_redact_env_values_masks_a_non_table_root_env_string() -> None:
    """``env = "..."`` parses fine as TOML -- it's a root string, not
    the [env] table -- so the old ``isinstance(env_table, Mapping)``
    check fell through and returned the secret verbatim."""
    text = 'name = "hello"\nenv = "sk-live-root-env-string"\n'
    out = redact_env_values(text)
    assert "sk-live-root-env-string" not in out
    assert tomllib.loads(out)["name"] == "hello"


def test_redact_env_values_masks_a_non_table_root_env_list() -> None:
    text = 'name = "hello"\nenv = ["sk-live-root-a", "sk-live-root-b"]\n'
    out = redact_env_values(text)
    assert "sk-live-root-a" not in out
    assert "sk-live-root-b" not in out


# ---- redact_env_values: JSON manifest_raw (imported agents, #1944) -


def test_redact_env_values_masks_a_json_env_key() -> None:
    """An imported agent's ``manifest_raw`` is JSON (#1944): the
    [env]-table equivalent is a top-level "env" key and must be masked
    deliberately, not by accident because a sibling key like
    "environment" happens to contain the substring "env"."""
    text = json.dumps({"agent": {"name": "demo"}, "env": {"API_KEY": "sk-live-json-env-1"}})
    out = redact_env_values(text)
    assert "sk-live-json-env-1" not in out
    parsed = json.loads(out)
    assert parsed["agent"]["name"] == "demo"
    assert parsed["env"] == REDACTED_ENV_VALUE


def test_redact_env_values_masks_json_secret_looking_keys_without_an_env_key() -> None:
    text = json.dumps({"agent": {"name": "demo"}, "credential": "sk-live-json-2"})
    out = redact_env_values(text)
    assert "sk-live-json-2" not in out


def test_redact_env_values_masks_json_even_without_the_substring_env() -> None:
    """Before #1944 this depended on the accident of the substring
    "env" appearing anywhere and tripping the unparseable-text scan's
    regex; a payload naming its secrets only "secret_refs" used to come
    back unmasked."""
    text = json.dumps({"agent": {"name": "demo"}, "secret_refs": {"API_KEY": "sk-live-json-3"}})
    out = redact_env_values(text)
    assert "sk-live-json-3" not in out


def test_redact_env_values_json_without_env_or_secret_keys_unchanged() -> None:
    text = json.dumps({"agent": {"name": "demo"}, "runtime": {"image": "demo:latest"}})
    assert json.loads(redact_env_values(text)) == json.loads(text)


# ---- redact_env_values: text tomllib rejects ----------------------

_BROKEN = """\
name = "hello"
replicas = = 2

[env]
# old: sk-old-999
API_KEY = "sk-live-777"   # current

[[workloads]]
name = "web"
"""


def test_redact_env_values_unparseable_masks_env_lines() -> None:
    """A syntax error elsewhere does not make the [env] section any less
    secret; before #1920's review this text came back verbatim."""
    assert (
        redact_env_values(_BROKEN)
        == f"""\
name = "hello"
replicas = = 2

[env]
{_marker(1)}
API_KEY = "{_V}"   {_marker(2)}

[[workloads]]
name = "web"
"""
    )


def test_redact_env_values_unparseable_fails_closed() -> None:
    cases = [
        # An [env] value the line scan cannot read.
        "[env]\nAPI_KEY = sk-unquoted-888\n",
        # A multi-line [env] value in a broken document.
        '[env]\nPEM = """\nsk-891\n"""\nbad = = 1\n',
        # A malformed [env] header.
        '[env\nAPI_KEY = "sk-889"\n',
        # env defined through a dotted key at the root.
        'name = "x"\nenv.API_KEY = "sk-890"\nbad = = 1\n',
        # A line mentioning env that the scan cannot place.
        'name = "x"\n"env" .API_KEY "sk-892"\n',
        # A key repeated in [env], the usual reason such text does not parse.
        '[env]\nAPI_KEY = "sk-old-893"\nAPI_KEY = "sk-new-894"\n',
    ]
    for text in cases:
        assert redact_env_values(text) == REDACTED_DOCUMENT, text


def test_redact_env_values_unparseable_without_env_unchanged() -> None:
    text = 'name = "x"\nreplicas = = 2\n'
    assert redact_env_values(text) == text


# ---- resolve_masked_env_values (#1920) ----------------------------


def test_masked_round_trip_restores_stored_text_byte_for_byte() -> None:
    """Load masked, save unchanged: the stored document, comments,
    spacing and every value literal included, comes back exactly."""
    masked = redact_env_values(_HAND_EDITED)
    assert resolve_masked_env_values(masked, fallback_text=_HAND_EDITED) == (_HAND_EDITED, [])


def test_masked_round_trip_keeps_the_callers_own_edits() -> None:
    edited = (
        redact_env_values(_HAND_EDITED)
        .replace('name = "hello"', 'name = "hello-2"')
        .replace(f'DB_URL = "{_V}"', 'DB_URL = "postgres://new"')
        .replace('PORT = "', 'NEW_KEY = "typed"\nPORT = "')
    )
    resolved, missing = resolve_masked_env_values(edited, fallback_text=_HAND_EDITED)
    assert missing == []
    assert resolved == (
        _HAND_EDITED.replace('name = "hello"', 'name = "hello-2"')
        .replace("DB_URL = 'postgres://app:pw-222@db/app'", 'DB_URL = "postgres://new"')
        .replace("PORT = 8080", 'NEW_KEY = "typed"\nPORT = 8080')
    )


def test_resolve_rejects_a_placeholder_with_no_stored_value() -> None:
    edited = redact_env_values(_HAND_EDITED).replace("PORT = ", f'GHOST = "{_V}"\nPORT = ')
    assert resolve_masked_env_values(edited, fallback_text=_HAND_EDITED) == (None, ["GHOST"])


def test_resolve_rejects_every_placeholder_without_a_stored_document() -> None:
    masked = redact_env_values(_BASE)
    assert resolve_masked_env_values(masked, fallback_text="") == (None, ["DATABASE_URL", "LOG_LEVEL"])


def test_resolve_only_restores_env_positions() -> None:
    """A placeholder or comment marker moved out of [env] must not pull
    the stored secret somewhere a masked read would show it."""
    masked = redact_env_values(_HAND_EDITED)
    moved = masked.replace(f"{_marker(1)}\n", "").replace(
        "# not env: keep me", f'{_marker(1)}\nnote = "{_V}"'
    )
    resolved, missing = resolve_masked_env_values(moved, fallback_text=_HAND_EDITED)
    assert missing == []
    assert "sk-live-OLD-000" not in resolved
    assert _marker(1) in resolved
    assert tomllib.loads(resolved)["workloads"][0]["note"] == _V
    assert tomllib.loads(resolved)["env"]["API_KEY"] == "sk-live-NEW-111"


def test_resolve_leaves_text_without_placeholders_untouched() -> None:
    assert resolve_masked_env_values(_HAND_EDITED, fallback_text=_BASE) == (_HAND_EDITED, [])


def test_resolve_restores_from_an_unparseable_stored_document() -> None:
    fixed = redact_env_values(_BROKEN).replace("replicas = = 2", "replicas = 2")
    resolved, missing = resolve_masked_env_values(fixed, fallback_text=_BROKEN)
    assert missing == []
    assert resolved == _BROKEN.replace("replicas = = 2", "replicas = 2")


def test_resolve_falls_back_to_a_rebuild_it_can_prove() -> None:
    """Placeholders the in-place edit cannot place (a nested env table
    in the incoming text) are still restored, by a full rebuild."""
    stored = '[env]\nAPI_KEY = "sk-live-rebuild"\n'
    incoming = f'[env]\nAPI_KEY = "{_V}"\n\n[env.extra]\nINNER = "x"\n'
    resolved, missing = resolve_masked_env_values(incoming, fallback_text=stored)
    assert missing == []
    assert tomllib.loads(resolved)["env"] == {"API_KEY": "sk-live-rebuild", "extra": {"INNER": "x"}}


# ---- redact_dotenv_values (#1920) ----------------------------------

_L = REDACTED_LINE


def test_redact_dotenv_values_masks_values_keeps_keys() -> None:
    text = 'API_KEY=sk-live-secret\nLOG_LEVEL="debug"\nexport TOKEN=tok-1\n'
    assert redact_dotenv_values(text) == f"API_KEY={_V}\nLOG_LEVEL={_V}\nexport TOKEN={_V}\n"


def test_redact_dotenv_values_masks_every_line_it_cannot_key() -> None:
    """Comments, lines with no '=', names the importer rejects, and the
    base64 body of a pasted PEM all used to pass through verbatim."""
    text = (
        "# OLD_KEY=old-secret-1\n"
        "\n"
        "not a kv line secret-2\n"
        "1INVALID=secret-3\n"
        "export\tTAB_KEY=secret-4\n"
        "ab+cd/ef==\n"
        "KEY=v\n"
    )
    assert redact_dotenv_values(text) == f"{_L}\n\n{_L}\n{_L}\n{_L}\n{_L}\nKEY={_V}\n"


def test_redact_dotenv_values_keeps_exactly_the_keys_parse_dotenv_imports() -> None:
    text = (
        "A=1\n export B = 2\nexport\tC=3\n# D=4\n5E=5\nF_6='6'\nG\nH==\n"
        "  I = x = y\nJ-K=1\nL.M=1\né=1\nexport N\n"
    )
    kept = set()
    for line in redact_dotenv_values(text).splitlines():
        if line.endswith(f"={_V}"):
            kept.add(line.removesuffix(f"={_V}").removeprefix("export "))
    assert kept == set(parse_dotenv(text))


def test_redact_dotenv_values_empty_input_unchanged() -> None:
    assert redact_dotenv_values("") == ""
