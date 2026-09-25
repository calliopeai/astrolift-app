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


def test_read_app_env_spells_booleans_the_toml_way() -> None:
    """A deployed literal is the process's env value; Python's "True"
    fails a program's == "true" check."""
    assert read_app_env("[env]\nENABLED = true\nDEBUG = false\n") == {"ENABLED": "true", "DEBUG": "false"}


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
    "environment" happens to contain the substring "env". Structure and
    key names stay, mirroring the TOML ``[env]`` rule -- only the leaf
    values are secret-shaped (#1944 review)."""
    text = json.dumps({"agent": {"name": "demo"}, "env": {"API_KEY": "sk-live-json-env-1"}})
    out = redact_env_values(text)
    assert "sk-live-json-env-1" not in out
    parsed = json.loads(out)
    assert parsed["agent"]["name"] == "demo"
    assert parsed["env"] == {"API_KEY": REDACTED_ENV_VALUE}


def test_redact_env_values_masks_json_secret_looking_keys_without_an_env_key() -> None:
    text = json.dumps({"agent": {"name": "demo"}, "credential": "sk-live-json-2"})
    out = redact_env_values(text)
    assert "sk-live-json-2" not in out


def test_redact_env_values_json_without_env_or_secret_keys_unchanged() -> None:
    text = json.dumps({"agent": {"name": "demo"}, "runtime": {"image": "demo:latest"}})
    assert json.loads(redact_env_values(text)) == json.loads(text)


# ---- redact_env_values: imported-agent package "environment" shape (#1944 review) ----
#
# astrolift_agents.services.imported_agent_registration stores an
# imported agent's whole package (agent_package.py's canonical
# projection) as JSON manifest_raw. Its "environment" object holds
# "values" (a flat name -> scalar map, documented non-secret, and the
# direct JSON equivalent of an [env] table) and "secret_refs" (a list of
# {env_var, uri} pointers the dispatcher resolves at launch -- never a
# literal, and already shown unmasked on AgentEnvironmentSpecType).
# Round 3 masked the whole "environment" value as one string, which
# collapsed this structure and hid the (non-secret) refs along with it.

_IMPORTED_AGENT_PACKAGE = {
    "agent": {"name": "demo-agent"},
    "runtime": {"image": "demo:latest"},
    "environment": {
        "values": {"LOG_LEVEL": "info", "RETRY_COUNT": 3, "DEBUG": False, "NOTE": None},
        "secret_refs": [
            {"env_var": "API_KEY", "uri": "agents/acme/11111111-1111-1111-1111-111111111111"},
            {"env_var": "DB_PASSWORD", "uri": "agents/acme/22222222-2222-2222-2222-222222222222"},
        ],
    },
}


def test_redact_env_values_masks_environment_values_leaves_only() -> None:
    """Every leaf under ``environment.values`` is masked -- including
    non-string scalars and null, same as a TOML [env] value -- but the
    variable names and the dict shape stay."""
    out = redact_env_values(json.dumps(_IMPORTED_AGENT_PACKAGE))
    values = json.loads(out)["environment"]["values"]
    assert set(values.keys()) == {"LOG_LEVEL", "RETRY_COUNT", "DEBUG", "NOTE"}
    assert all(v == REDACTED_ENV_VALUE for v in values.values())


def test_redact_env_values_keeps_secret_refs_visible() -> None:
    """``secret_refs`` entries are references, not literals, and must
    not be swept into masking just because the key's name contains
    "secret"."""
    out = redact_env_values(json.dumps(_IMPORTED_AGENT_PACKAGE))
    assert (
        json.loads(out)["environment"]["secret_refs"] == _IMPORTED_AGENT_PACKAGE["environment"]["secret_refs"]
    )


def test_redact_env_values_does_not_mask_the_word_environment_itself() -> None:
    """ "environment" contains the substring "env" -- the shape that made
    round 2's TOML-fallback masking accidental -- but the key itself is
    not a value holder and must not be replaced wholesale."""
    text = json.dumps({"agent": {"name": "demo"}, "environment": {"values": {}, "secret_refs": []}})
    parsed = json.loads(redact_env_values(text))
    assert parsed["environment"] == {"values": {}, "secret_refs": []}


def test_redact_env_values_imported_agent_shape_round_trips_as_json() -> None:
    """The masked document is still valid JSON, with the same top-level
    keys and the same container types throughout -- never a string
    sentinel standing in for a dict or list."""
    parsed = json.loads(redact_env_values(json.dumps(_IMPORTED_AGENT_PACKAGE)))
    assert set(parsed.keys()) == set(_IMPORTED_AGENT_PACKAGE.keys())
    assert parsed["agent"] == _IMPORTED_AGENT_PACKAGE["agent"]
    assert parsed["runtime"] == _IMPORTED_AGENT_PACKAGE["runtime"]
    assert isinstance(parsed["environment"], dict)
    assert isinstance(parsed["environment"]["values"], dict)
    assert isinstance(parsed["environment"]["secret_refs"], list)
    assert isinstance(parsed["environment"]["secret_refs"][0], dict)


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
