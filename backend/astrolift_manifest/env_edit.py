"""Helpers for editing the app-level ``[env]`` table in a stored
TOML manifest while preserving the rest of the document.

Read-side: ``read_app_env(toml_text)`` returns a dict of the
top-level ``[env]`` table. Empty dict if the section is absent.

Write-side: ``set_app_env_keys(toml_text, kvs)`` round-trips the
TOML with ``tomllib`` (parse) → ``tomli_w`` (dump). Comments and
formatting are NOT preserved — that's a known limitation of
``tomli_w``. The platform's manifest is normally generated from
the UI and dev-side hand-editing happens through PRs, so the
trade-off is acceptable for app-secret CRUD.

Used by the GraphQL secrets resolver (#279). Mutations write to
the staging buffer (``manifest_raw_staged``); the user-driven
'Push to Repo' flow re-serializes to the source repo.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping


def read_app_env(toml_text: str) -> dict[str, str]:
    """Return the top-level ``[env]`` table as a flat str→str map.
    Empty dict when the section is absent or empty."""
    if not toml_text or not toml_text.strip():
        return {}
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError:
        return {}
    env = data.get("env", {}) or {}
    if not isinstance(env, Mapping):
        return {}
    return {str(k): str(v) for k, v in env.items()}


def set_app_env_keys(
    toml_text: str,
    kvs: Mapping[str, str],
) -> str:
    """Merge ``kvs`` into the top-level ``[env]`` table, returning
    the new TOML text. Existing keys are overwritten; keys not in
    ``kvs`` stay as-is.

    An empty input ``toml_text`` produces a minimal ``[env]``-only
    document. Caller is responsible for passing the full prior
    text when one exists."""
    import tomli_w

    data: dict = tomllib.loads(toml_text) if toml_text and toml_text.strip() else {}
    env_table = data.get("env", {})
    if not isinstance(env_table, dict):
        env_table = {}
    for key, value in kvs.items():
        env_table[str(key)] = str(value)
    data["env"] = env_table
    return tomli_w.dumps(data)


def delete_app_env_key(toml_text: str, key: str) -> tuple[str, bool]:
    """Remove ``key`` from the top-level ``[env]`` table.

    Returns ``(new_toml, removed)``. ``removed=False`` when the
    key wasn't present — caller surfaces a NOT_FOUND if that
    matters."""
    import tomli_w

    data: dict = tomllib.loads(toml_text) if toml_text and toml_text.strip() else {}
    env_table = data.get("env", {})
    if not isinstance(env_table, dict):
        return toml_text, False
    if key not in env_table:
        return toml_text, False
    del env_table[key]
    if env_table:
        data["env"] = env_table
    else:
        data.pop("env", None)
    return tomli_w.dumps(data), True


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse ``.env``-shape text (KEY=value, comment lines, blank
    lines). Quoted values are unquoted; multi-line values are not
    supported (the platform recommends bulkImport for a flat
    .env paste, not for export blobs)."""
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not _is_valid_env_name(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        out[key] = value
    return out


def _is_valid_env_name(name: str) -> bool:
    """POSIX env-var name shape: leading letter / underscore,
    rest alphanumeric / underscore."""
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)
