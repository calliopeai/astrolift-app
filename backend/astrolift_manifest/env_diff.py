"""Which env values a manifest edit changes, across every table a workload reads env from (#1759).

A pod's env comes from the app-wide ``[env]`` table and from each
container's own ``env``: every ``[[workloads.containers]]`` entry, plus
the single container a ``[[jobs]]`` or ``[[tasks]]`` entry desugars to
(``parser._parse_container`` / ``_desugar_job`` / ``_desugar_task``).
Container env is rendered straight into the pod spec and outranks
``[env]`` (``env_injection``'s precedence), so a gate that diffs only
``[env]`` is bypassed by putting the same key on a container.

Reads the TOML document directly, visiting exactly the tables the parser
reads env from. The staged side of a diff has already passed
``parse_raw``; reading the stored side the same way still works when that
text predates a later parser tightening and no longer validates.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from collections.abc import Mapping
from typing import Any

# Where a key lives: () for the app-wide [env] table, else the section plus
# a (name, occurrence) pair per level. Tuples, not a joined string, so
# names containing dots can't make two containers collide and hide a
# change behind each other; occurrence does the same for repeated names.
_Scope = tuple[Any, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class EnvChange:
    """One env key an edit adds, changes or removes.

    ``after`` is the value once the edit applies, ``None`` when the edit
    removes the key (TOML has no null, so ``None`` is never a real value).
    """

    scope: _Scope
    key: str
    after: Any

    @property
    def app_wide(self) -> bool:
        return not self.scope

    @property
    def label(self) -> str:
        """Where the key lives and its name, for an operator or an audit
        row; never the value. ``FOO`` for the app-wide table,
        ``workloads.web.containers.api.env.FOO``, ``jobs.nightly.env.FOO``."""
        if not self.scope:
            return self.key
        section, *levels = self.scope
        parts = [section]
        for depth, (name, occurrence) in enumerate(levels):
            if depth == 1:
                parts.append("containers")
            parts.append(f"{name or '?'}#{occurrence}" if occurrence or not name else name)
        return ".".join([*parts, "env", self.key])


def _load(toml_text: str) -> dict[str, Any]:
    if not toml_text or not toml_text.strip():
        return {}
    try:
        return tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError:
        return {}


def _tables(value: Any) -> list[tuple[tuple[str, int], Mapping]]:
    """``((name, occurrence), table)`` for each table in an array of tables."""
    out: list[tuple[tuple[str, int], Mapping]] = []
    seen: dict[str, int] = {}
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        occurrence = seen.get(name, 0)
        seen[name] = occurrence + 1
        out.append(((name, occurrence), item))
    return out


def _container_env(table: Mapping) -> dict[str, str]:
    env = table.get("env")
    if not isinstance(env, Mapping):
        return {}
    # The parser stores str(value); that string is what the pod gets.
    return {str(key): str(value) for key, value in env.items()}


def _manifest_env(toml_text: str) -> dict[tuple[_Scope, str], Any]:
    data = _load(toml_text)
    out: dict[tuple[_Scope, str], Any] = {}
    app_env = data.get("env")
    if isinstance(app_env, Mapping):
        for key, value in app_env.items():
            out[((), str(key))] = value
    for workload, workload_table in _tables(data.get("workloads")):
        for container, container_table in _tables(workload_table.get("containers")):
            for key, value in _container_env(container_table).items():
                out[(("workloads", workload, container), key)] = value
    for section in ("jobs", "tasks"):
        for entry, table in _tables(data.get(section)):
            for key, value in _container_env(table).items():
                out[((section, entry), key)] = value
    return out


def _canonical(value: Any) -> str:
    # Type-aware: 1, 1.0, true and "1" all differ here, where Python's ==
    # would call 1 and True the same value.
    return json.dumps(value, sort_keys=True, default=str)


def env_changes(before_text: str, after_text: str) -> list[EnvChange]:
    """Every env key that differs between two manifest bodies, app-wide keys first."""
    before = _manifest_env(before_text)
    after = _manifest_env(after_text)
    changed = [
        EnvChange(scope=scope, key=key, after=after.get((scope, key)))
        for scope, key in before.keys() | after.keys()
        if _canonical(before.get((scope, key))) != _canonical(after.get((scope, key)))
    ]
    return sorted(changed, key=lambda change: (bool(change.scope), change.label))


def changed_env_key_names(before_text: str, after_text: str) -> list[str]:
    """``env_changes`` as labels, for a confirm dialog or an audit row."""
    return [change.label for change in env_changes(before_text, after_text)]
