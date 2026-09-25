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

Redact-side (#1920, #1948): ``redact_env_values(toml_text)`` masks
every env value the parser reads, and every comment inside those env
sections, for a caller who has not proven ``secret.read`` plus step-up
elevation: the app-wide ``[env]`` table and the ``env`` of every
``[[workloads.containers]]``, ``[[jobs]]`` and ``[[tasks]]`` entry. It
edits the value literals in place, so the rest of the document is
byte-identical and a masked read can be saved back without
reformatting anything. ``redact_dotenv_values(text)`` masks a
bulk-import ``.env`` paste, and ``redact_container_env(manifest)`` masks
a parsed manifest for the rendered-manifest previews.

Restore-side (#1920, #1948): ``resolve_masked_env_values(incoming_text,
fallback_text=...)`` undoes the masking on save. A masked placeholder
under an env key is replaced by the stored literal of the same key in the
same table, so an editor that loads the masked text, changes something
unrelated, and saves the whole document leaves every stored secret as it
was.

Used by the GraphQL secrets resolver (#279). Mutations write to
the staging buffer (``manifest_raw_staged``); the user-driven
'Push to Repo' flow re-serializes to the source repo.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import string
import tomllib
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple


def read_app_env(toml_text: str) -> dict[str, str]:
    """Return the top-level ``[env]`` table as a flat str→str map.
    Empty dict when the section is absent or empty.

    Skips keys that are not env-var names and values that are tables or
    arrays. The mutations never write either, but a hand-edited
    ``astrolift.toml`` synced from a repo can, and this map is what the
    deploy path materializes into a Kubernetes Secret (#1758): one key
    the API server rejects would fail the whole apply."""
    if not toml_text or not toml_text.strip():
        return {}
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError:
        return {}
    env = data.get("env", {}) or {}
    if not isinstance(env, Mapping):
        return {}
    return {
        str(k): _env_value(v)
        for k, v in env.items()
        if _is_valid_env_name(str(k)) and not isinstance(v, (Mapping, list))
    }


def _env_value(value: object) -> str:
    """A TOML scalar as the string a process sees in its environment.

    Booleans keep TOML's spelling: ``str(True)`` is Python's ``"True"``,
    which a program checking ``== "true"`` reads as false."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


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


# Long and namespaced on purpose: on save, an [env] value equal to this
# string means "keep the stored value", so it must never be mistaken for
# a value somebody meant to set.
REDACTED_ENV_VALUE = "[ASTROLIFT_REDACTED_ENV_VALUE]"
# A dotenv line that carries no key the importer would create.
REDACTED_LINE = "[ASTROLIFT_REDACTED_LINE]"
# The whole document, when it does not parse and the line scan cannot
# rule out a secret somewhere in it.
REDACTED_DOCUMENT = "# [ASTROLIFT_REDACTED_UNPARSEABLE_MANIFEST]\n"

_MASKED_LITERAL = f'"{REDACTED_ENV_VALUE}"'
# Comments in the [env] section can hold a commented-out secret. Each is
# replaced by a numbered marker, and the number is how a save puts the
# original comment back (see resolve_masked_env_values).
_COMMENT_MARKER = "# [ASTROLIFT_REDACTED_ENV_COMMENT:{}]"
_COMMENT_MARKER_RE = re.compile(r"# \[ASTROLIFT_REDACTED_ENV_COMMENT:([1-9][0-9]*)\]")

# An imported agent's manifest_raw is JSON, not TOML (#1944): tomllib
# happens to reject most JSON, and the unparseable-text line scan below
# then masked it whole -- but only by accident, because a sibling key
# such as "environment" usually contains the substring "env" and trips
# that scan's conservative regex. A payload that never spells "env" came
# back unmasked. JSON gets its own explicit, deliberate rule instead.
#
# ``environment`` itself is not one of these: an agent package's
# ``environment`` object holds ``values`` (a flat name -> scalar map,
# the JSON shape of an ``[env]`` table) *and* ``secret_refs`` (a list of
# ``{env_var, uri}`` pointers -- never a literal, resolved by the
# dispatcher at launch, and already shown unmasked everywhere else this
# app surfaces a spec's secret_refs, e.g. AgentEnvironmentSpecType). A
# key ending in "_ref"/"_refs" is a pointer, not a value holder, and is
# never treated as sensitive here even if its name also contains one of
# these tokens (``secret_refs`` contains "secret").
_JSON_ENV_CONTAINER_KEYS = frozenset({"env", "values", "value", "env_vars", "envvars", "plaintext"})
_JSON_SECRET_NAME_TOKENS = (
    "secret",
    "password",
    "token",
    "credential",
    "private_key",
    "privatekey",
    "pin",
)
_JSON_REFERENCE_NAME_SUFFIXES = ("_ref", "_refs")


def _is_json_reference_name(name: str) -> bool:
    return name.endswith(_JSON_REFERENCE_NAME_SUFFIXES)


def _is_json_secret_name(name: str) -> bool:
    if _is_json_reference_name(name):
        return False
    return name in _JSON_ENV_CONTAINER_KEYS or any(token in name for token in _JSON_SECRET_NAME_TOKENS)


def _mask_json_leaves(value: object) -> object:
    """``value`` with every scalar leaf replaced by the sentinel.

    Dict keys and list length are kept, mirroring how every ``[env]``
    value is masked in place while its keys stay put (#1944): once a
    key is judged sensitive, everything under it is secret-shaped,
    regardless of what its own children happen to be named."""
    if isinstance(value, dict):
        return {key: _mask_json_leaves(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_mask_json_leaves(item) for item in value]
    return REDACTED_ENV_VALUE


def _redact_json_value(value: object) -> object:
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            name = str(key).lower()
            if _is_json_secret_name(name):
                redacted[key] = _mask_json_leaves(child)
            else:
                redacted[key] = _redact_json_value(child)
        return redacted
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    return value


def _redact_json_manifest(text: str) -> str | None:
    """``text`` masked as JSON, or None when it is not JSON (the caller
    falls back to the TOML path). A key named ``env`` or that looks
    secret-shaped has its scalar leaves masked, structure kept, at any
    depth; a reference (``*_ref``/``*_refs``) is left visible."""
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return json.dumps(_redact_json_value(data), sort_keys=True)


def redact_env_values(toml_text: str) -> str:
    """Return ``toml_text`` with every env value, and every comment in an
    env section, masked. Keys and the rest of the document are left
    exactly as they were.

    "Env" is every table the parser reads env from (#1948): the
    app-wide ``[env]`` and the ``env`` of each ``[[workloads.containers]]``,
    ``[[jobs]]`` and ``[[tasks]]`` entry. Container env is rendered
    straight into the pod spec, so it holds secrets as often as ``[env]``
    does. An ``env`` that is not a table at all (a string, a list) is
    masked whole.

    The masked text is re-parsed and must equal the original with only
    the env values replaced; if the in-place edit cannot prove that,
    the document is rebuilt with ``tomli_w`` instead (still masked,
    formatting lost). Text that does not parse is masked line by line,
    or replaced whole by :data:`REDACTED_DOCUMENT` when the line scan
    cannot account for every line that might hold a secret. A rejected
    ``updateManifest``/``registerApp`` input is exactly that kind of
    text, and the mutation audit log records it.

    An imported agent's ``manifest_raw`` is JSON, not TOML; see
    :func:`_redact_json_manifest`, tried first since JSON is rarely
    also valid TOML."""
    if not toml_text or not toml_text.strip():
        return toml_text
    json_masked = _redact_json_manifest(toml_text)
    if json_masked is not None:
        return json_masked
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError:
        layout = _scan_unparseable_env(toml_text)
        if layout is None:
            return REDACTED_DOCUMENT
        return _mask(toml_text, layout.values.values(), layout.comments)
    if not any("env" in table for _, _, table in _env_owners(data)):
        return toml_text
    positions = _env_positions(data)
    masked_data = _with_values(data, dict.fromkeys((p.path for p in positions), REDACTED_ENV_VALUE))
    layout = _scan_env_layout(toml_text)
    if layout is not None and all(p.path in layout.values for p in positions):
        masked = _mask(toml_text, [layout.values[p.path] for p in positions], layout.comments)
        if _loads_or_none(masked) == masked_data:
            return masked
    import tomli_w

    return tomli_w.dumps(masked_data)


def resolve_masked_env_values(incoming_text: str, *, fallback_text: str) -> tuple[str | None, list[str]]:
    """Put stored env values back where ``incoming_text`` carries the
    masked placeholder (#1920, #1948).

    ``fallback_text`` is the stored document the caller's masked read
    was derived from. Each env key whose incoming value is
    :data:`REDACTED_ENV_VALUE` gets the stored literal of the same key in
    the same table, byte for byte, and each numbered comment marker in an
    env section gets the stored comment it replaced. Nothing else in
    ``incoming_text`` changes. Only env positions are restored, so a
    caller cannot move a stored secret anywhere a masked read would show
    it. A container, job or task table is matched by name, never by
    position: reordering them in the editor keeps each value with its
    own table, and a placeholder cannot pull another table's value.

    Returns ``(None, labels)`` when a placeholder sits where the stored
    document has no value (a new key, a renamed container); the caller
    must reject the write rather than store the placeholder as if it
    were a secret. Returns ``(incoming_text, [])`` untouched when there
    is nothing to restore, including text that does not parse (the
    caller's own validation rejects that)."""
    if not incoming_text or not incoming_text.strip():
        return incoming_text, []
    try:
        incoming_data = tomllib.loads(incoming_text)
    except tomllib.TOMLDecodeError:
        return incoming_text, []
    masked = _masked_positions(incoming_data)
    layout = _scan_env_layout(incoming_text)
    markers = _comment_markers(incoming_text, layout)
    if not masked and not markers:
        return incoming_text, []

    stored = _stored_env(fallback_text)
    missing = sorted(p.label for p in masked if p.identity not in stored.values)
    if missing:
        return None, missing

    restored_data = _with_values(incoming_data, {p.path: stored.values[p.identity] for p in masked})
    replacements = _restore_replacements(masked, markers, layout, stored)
    if replacements is not None:
        resolved = _splice(incoming_text, replacements)
        if _loads_or_none(resolved) == restored_data:
            return resolved, []
    if not masked:
        # Only comment markers, and they could not be put back in place:
        # leave them as the harmless comments they are rather than
        # reformat a document whose values need nothing.
        return incoming_text, []
    import tomli_w

    return tomli_w.dumps(restored_data), []


def masked_env_positions(data: Mapping[str, Any]) -> list[_EnvPosition]:
    """The env positions in parsed manifest ``data`` that still hold the
    masked placeholder, for ``parse_raw`` to refuse (#1920, #1948)."""
    return [p for p in _env_positions(data) if _value_at(data, p.path) == REDACTED_ENV_VALUE]


def redact_container_env(manifest: Any) -> Any:
    """``manifest`` (a ``NormalizedManifest``) with every container env
    value masked and the keys kept (#1948).

    For the rendered-manifest previews: the renderer copies each
    container's env into the pod spec's ``env:`` entries, the same
    values :func:`redact_env_values` masks in the text (a ``[[jobs]]`` or
    ``[[tasks]]`` entry is one container here). Env the platform injects
    while rendering is not in the manifest and is left alone."""

    def _container(container: Any) -> Any:
        return dataclasses.replace(
            container, env=tuple((key, REDACTED_ENV_VALUE) for key, _ in container.env)
        )

    workloads = tuple(
        dataclasses.replace(workload, containers=tuple(_container(c) for c in workload.containers))
        for workload in manifest.workloads
    )
    serialized = copy.deepcopy(manifest.serialized)
    for workload in serialized.get("workloads", []):
        for container in workload.get("containers", []):
            container["env"] = dict.fromkeys(container.get("env", {}), REDACTED_ENV_VALUE)
    return dataclasses.replace(manifest, workloads=workloads, serialized=serialized)


# The sections whose entries each desugar to one container (see
# parser._desugar_job / _desugar_task); their ``env`` is that container's.
_ENTRY_SECTIONS = ("jobs", "tasks")


class _EnvPosition(NamedTuple):
    """One env value in a parsed manifest.

    ``path`` locates it in the parsed document, list indices included.
    ``scope`` names the table that owns it by name rather than position:
    ``()`` for the app-wide ``[env]``, ``("workloads", workload,
    container)`` for a container and ``(section, entry)`` for a
    ``[[jobs]]``/``[[tasks]]`` entry, each level a ``(name, occurrence)``
    pair so two tables sharing a name stay apart (the scheme #1945's
    ``env_diff`` uses). ``key`` is None when ``env`` itself is not a
    table; that value is masked whole and never restored."""

    path: tuple[str | int, ...]
    scope: tuple[Any, ...]
    key: str | None

    @property
    def identity(self) -> tuple[Any, ...]:
        return self.scope, self.key

    @property
    def label(self) -> str:
        """Where the value lives, never the value: ``KEY`` for ``[env]``,
        ``workloads.web.containers.app.env.KEY``, ``jobs.nightly.env.KEY``."""
        if not self.scope:
            return "env" if self.key is None else self.key
        section, *levels = self.scope
        parts = [section]
        for depth, (name, occurrence) in enumerate(levels):
            if depth == 1:
                parts.append("containers")
            parts.append(f"{name or '?'}#{occurrence}" if occurrence or not name else name)
        parts.append("env")
        if self.key is not None:
            parts.append(self.key)
        return ".".join(parts)

    @property
    def error_path(self) -> str:
        """``path`` in ``ManifestError.path`` notation: ``workloads[0].containers[1].env.KEY``."""
        out = ""
        for part in self.path:
            if isinstance(part, int):
                out += f"[{part}]"
            else:
                out += f".{part}" if out else part
        return out


def _named_tables(value: object) -> list[tuple[int, tuple[str, int], Mapping[str, Any]]]:
    """``(index, (name, occurrence), table)`` for each table in an array of tables."""
    out = []
    seen: dict[str, int] = {}
    for index, item in enumerate(value if isinstance(value, list) else ()):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        occurrence = seen.get(name, 0)
        seen[name] = occurrence + 1
        out.append((index, (name, occurrence), item))
    return out


def _env_owners(
    data: Mapping[str, Any],
) -> list[tuple[tuple[str | int, ...], tuple[Any, ...], Mapping[str, Any]]]:
    """``(path, scope, table)`` for every table whose ``env`` the parser
    reads: the document root, each ``[[workloads.containers]]`` entry, and
    each ``[[jobs]]``/``[[tasks]]`` entry."""
    owners: list[tuple[tuple[str | int, ...], tuple[Any, ...], Mapping[str, Any]]] = [((), (), data)]
    for w_index, w_scope, workload in _named_tables(data.get("workloads")):
        for c_index, c_scope, container in _named_tables(workload.get("containers")):
            owners.append(
                (("workloads", w_index, "containers", c_index), ("workloads", w_scope, c_scope), container)
            )
    for section in _ENTRY_SECTIONS:
        for index, scope, entry in _named_tables(data.get(section)):
            owners.append(((section, index), (section, scope), entry))
    return owners


def _env_positions(data: Mapping[str, Any]) -> list[_EnvPosition]:
    positions: list[_EnvPosition] = []
    for path, scope, table in _env_owners(data):
        if "env" not in table:
            continue
        env = table["env"]
        if isinstance(env, Mapping):
            positions.extend(_EnvPosition((*path, "env", key), scope, key) for key in env)
        else:
            positions.append(_EnvPosition((*path, "env"), scope, None))
    return positions


def _masked_positions(data: Mapping[str, Any]) -> list[_EnvPosition]:
    """Placeholders a save can restore: env keys, not a whole non-table ``env``."""
    return [p for p in masked_env_positions(data) if p.key is not None]


def _is_env_table(path: tuple[str | int, ...]) -> bool:
    """Whether ``path``, as the layout scan resolves it, is a table
    :func:`_env_owners` reads env from."""
    if not path or path[-1] != "env":
        return False
    owner = path[:-1]
    if not owner:
        return True
    if len(owner) == 4:
        return (
            owner[0] == "workloads"
            and owner[2] == "containers"
            and isinstance(owner[1], int)
            and isinstance(owner[3], int)
        )
    return len(owner) == 2 and owner[0] in _ENTRY_SECTIONS and isinstance(owner[1], int)


def _in_env_table(path: tuple[str | int, ...]) -> bool:
    return any(_is_env_table(path[:end]) for end in range(1, len(path) + 1))


def _value_at(data: Any, path: tuple[str | int, ...]) -> Any:
    for part in path:
        data = data[part]
    return data


def _with_values(data: dict[str, Any], values: Mapping[tuple[str | int, ...], object]) -> dict[str, Any]:
    """A copy of ``data`` with the value at each path replaced."""
    out = copy.deepcopy(data)
    for path, value in values.items():
        _value_at(out, path[:-1])[path[-1]] = value
    return out


class _EnvLayout(NamedTuple):
    # value path -> (start, end) of its value literal. The parsed-text scan
    # records every value in the document, keyed by its path in the parsed
    # data; the line scan for unparseable text records only env values.
    values: dict[tuple[str | int, ...], tuple[int, int]]
    # (start, end) of each comment in an env section, in document order
    comments: list[tuple[int, int]]


class _StoredEnv(NamedTuple):
    # keyed by _EnvPosition.identity
    values: dict[tuple[Any, ...], object]
    literals: dict[tuple[Any, ...], str]
    comments: list[str]


def _mask(text: str, spans: Iterable[tuple[int, int]], comments: list[tuple[int, int]]) -> str:
    replacements = [(start, end, _MASKED_LITERAL) for start, end in spans]
    replacements += [
        (start, end, _COMMENT_MARKER.format(ordinal))
        for ordinal, (start, end) in enumerate(comments, start=1)
    ]
    return _splice(text, replacements)


def _comment_markers(text: str, layout: _EnvLayout | None) -> list[tuple[int, int, int]]:
    if layout is None:
        return []
    markers = []
    for start, end in layout.comments:
        match = _COMMENT_MARKER_RE.fullmatch(text[start:end].rstrip(" \t"))
        if match:
            markers.append((start, end, int(match.group(1))))
    return markers


def _restore_replacements(
    masked: list[_EnvPosition],
    markers: list[tuple[int, int, int]],
    layout: _EnvLayout | None,
    stored: _StoredEnv,
) -> list[tuple[int, int, str]] | None:
    if layout is None:
        return None
    replacements = []
    for position in masked:
        literal = stored.literals.get(position.identity) or _toml_literal(stored.values[position.identity])
        span = layout.values.get(position.path)
        if literal is None or span is None:
            return None
        replacements.append((*span, literal))
    for start, end, ordinal in markers:
        if ordinal <= len(stored.comments):
            replacements.append((start, end, stored.comments[ordinal - 1]))
    return replacements


def _stored_env(text: str) -> _StoredEnv:
    if not text or not text.strip():
        return _StoredEnv({}, {}, [])
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        # A stored document can fail to parse (registerApp keeps the raw
        # text even when it is broken). Restore only what the same line
        # scan that masked it could read, and only for [env]: without a
        # parse there is no telling which container a value belongs to.
        layout = _scan_unparseable_env(text)
        if layout is None:
            return _StoredEnv({}, {}, [])
        literals = {
            ((), path[-1]): text[start:end]
            for path, (start, end) in layout.values.items()
            if path[0] == "env"
        }
        values = {identity: _decode_literal(literal) for identity, literal in literals.items()}
        return _StoredEnv(values, literals, [text[start:end] for start, end in layout.comments])
    positions = [p for p in _env_positions(data) if p.key is not None]
    values = {p.identity: _value_at(data, p.path) for p in positions}
    layout = _scan_env_layout(text)
    if layout is None:
        return _StoredEnv(values, {}, [])
    literals = {}
    for position in positions:
        span = layout.values.get(position.path)
        if span is not None:
            literals[position.identity] = text[span[0] : span[1]]
    return _StoredEnv(values, literals, [text[start:end] for start, end in layout.comments])


def _splice(text: str, replacements: list[tuple[int, int, str]]) -> str:
    parts: list[str] = []
    cursor = 0
    for start, end, new in sorted(replacements):
        parts.append(text[cursor:start])
        parts.append(new)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _loads_or_none(text: str) -> dict | None:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


class _Undecodable:
    pass


_UNDECODABLE = _Undecodable()


def _decode_literal(literal: str) -> object:
    try:
        return tomllib.loads(f"v = {literal}")["v"]
    except tomllib.TOMLDecodeError:
        return _UNDECODABLE


def _toml_literal(value: object) -> str | None:
    """``value`` as a one-line TOML literal, or None when it has no such form."""
    import tomli_w

    if isinstance(value, Mapping):
        return None
    line = tomli_w.dumps({"v": value})
    if not line.startswith("v = ") or not line.endswith("\n") or "\n" in line[:-1]:
        return None
    return line[len("v = ") : -1]


class _ScanError(Exception):
    pass


_BARE_KEY_CHARS = frozenset(string.ascii_letters + string.digits + "_-")


class _EnvScanner:
    """Finds where value literals and env-section comments sit in TOML text.

    Only a locator: every caller checks the edited text against a real
    parse, so a shape this does not model costs a fallback, never a
    wrong answer. Raises :class:`_ScanError` on anything unexpected.
    """

    def __init__(self, text: str) -> None:
        self.s = text
        self.n = len(text)
        self.i = 0

    def scan(self) -> _EnvLayout:
        """Every value's span, keyed by its path in the parsed data (an
        array-of-tables element by its index), and every comment in an
        env section: under an env table header, or trailing a line that
        sets an env value."""
        values: dict[tuple[str | int, ...], tuple[int, int]] = {}
        comments: list[tuple[int, int]] = []
        table: tuple[str | int, ...] = ()
        arrays: dict[tuple[str | int, ...], int] = {}
        while True:
            self._skip_ws()
            if self.i >= self.n:
                return _EnvLayout(values, comments)
            char = self.s[self.i]
            if char in "\r\n":
                self._newline()
                continue
            if char == "#":
                span = self._comment()
                if _is_env_table(table):
                    comments.append(span)
                self._end_of_line()
                continue
            if char == "[":
                keys, is_array = self._table_header()
                table = _resolve_table(keys, is_array, arrays)
                trailing = self._line_tail()
                if trailing is not None and _is_env_table(table):
                    comments.append(trailing)
                continue
            path = table + self._key()
            self._skip_ws()
            self._expect("=")
            self._skip_ws()
            self._value(path, values)
            trailing = self._line_tail()
            if trailing is not None and _in_env_table(path):
                comments.append(trailing)

    def header_line(self) -> tuple[tuple[str, ...], bool, tuple[int, int] | None] | None:
        try:
            self._skip_ws()
            path, is_array = self._table_header()
            trailing = self._line_tail()
        except _ScanError:
            return None
        if self.i != self.n:
            return None
        return path, is_array, trailing

    def key_line(self) -> tuple[str, ...] | None:
        try:
            self._skip_ws()
            path = self._key()
            self._skip_ws()
            self._expect("=")
        except _ScanError:
            return None
        return path

    def string_entry_line(self) -> tuple[tuple[str, ...], tuple[int, int], tuple[int, int] | None] | None:
        """``key = "one-line string"  # optional comment``, nothing else."""
        try:
            path = self.key_line()
            if path is None:
                return None
            self._skip_ws()
            start = self.i
            if self.s.startswith('"', start) and not self.s.startswith('"""', start):
                self._basic_string()
            elif self.s.startswith("'", start) and not self.s.startswith("'''", start):
                self._literal_string()
            else:
                return None
            end = self.i
            trailing = self._line_tail()
        except _ScanError:
            return None
        if self.i != self.n or _decode_literal(self.s[start:end]) is _UNDECODABLE:
            return None
        return path, (start, end), trailing

    def _skip_ws(self) -> None:
        while self.i < self.n and self.s[self.i] in " \t":
            self.i += 1

    def _skip_ws_newlines_comments(self) -> None:
        while self.i < self.n:
            char = self.s[self.i]
            if char in " \t\r\n":
                self.i += 1
            elif char == "#":
                self._comment()
            else:
                return

    def _expect(self, token: str) -> None:
        if not self.s.startswith(token, self.i):
            raise _ScanError
        self.i += len(token)

    def _newline(self) -> None:
        if self.s.startswith("\r\n", self.i):
            self.i += 2
        elif self.s.startswith("\n", self.i):
            self.i += 1
        else:
            raise _ScanError

    def _end_of_line(self) -> None:
        if self.i < self.n:
            self._newline()

    def _comment(self) -> tuple[int, int]:
        start = self.i
        while self.i < self.n and self.s[self.i] not in "\r\n":
            self.i += 1
        return start, self.i

    def _line_tail(self) -> tuple[int, int] | None:
        self._skip_ws()
        span = None
        if self.i < self.n and self.s[self.i] == "#":
            span = self._comment()
        self._end_of_line()
        return span

    def _table_header(self) -> tuple[tuple[str, ...], bool]:
        is_array = self.s.startswith("[[", self.i)
        self.i += 2 if is_array else 1
        self._skip_ws()
        path = self._key()
        self._skip_ws()
        self._expect("]]" if is_array else "]")
        return path, is_array

    def _key(self) -> tuple[str, ...]:
        parts = [self._simple_key()]
        while True:
            save = self.i
            self._skip_ws()
            if self.i < self.n and self.s[self.i] == ".":
                self.i += 1
                self._skip_ws()
                parts.append(self._simple_key())
            else:
                self.i = save
                return tuple(parts)

    def _simple_key(self) -> str:
        start = self.i
        if self.s.startswith('"', start) and not self.s.startswith('"""', start):
            self._basic_string()
        elif self.s.startswith("'", start) and not self.s.startswith("'''", start):
            self._literal_string()
        else:
            while self.i < self.n and self.s[self.i] in _BARE_KEY_CHARS:
                self.i += 1
            if self.i == start:
                raise _ScanError
            return self.s[start : self.i]
        decoded = _decode_literal(self.s[start : self.i])
        if not isinstance(decoded, str):
            raise _ScanError
        return decoded

    def _value(
        self, path: tuple[str | int, ...], values: dict[tuple[str | int, ...], tuple[int, int]]
    ) -> None:
        start = self.i
        if self.s.startswith('"""', self.i):
            self._multiline_string('"')
        elif self.s.startswith("'''", self.i):
            self._multiline_string("'")
        elif self.s.startswith('"', self.i):
            self._basic_string()
        elif self.s.startswith("'", self.i):
            self._literal_string()
        elif self.s.startswith("[", self.i):
            self._array(path, values)
        elif self.s.startswith("{", self.i):
            self._inline_table(path, values)
        else:
            self._bare_scalar()
        values[path] = (start, self.i)

    def _basic_string(self) -> None:
        j = self.i + 1
        while j < self.n:
            char = self.s[j]
            if char == "\\":
                if j + 1 < self.n and self.s[j + 1] in "\r\n":
                    raise _ScanError
                j += 2
                continue
            if char == '"':
                self.i = j + 1
                return
            if char in "\r\n":
                break
            j += 1
        raise _ScanError

    def _literal_string(self) -> None:
        j = self.i + 1
        while j < self.n:
            char = self.s[j]
            if char == "'":
                self.i = j + 1
                return
            if char in "\r\n":
                break
            j += 1
        raise _ScanError

    def _multiline_string(self, quote: str) -> None:
        delimiter = quote * 3
        j = self.i + 3
        while j < self.n:
            if quote == '"' and self.s[j] == "\\":
                j += 2
                continue
            if self.s.startswith(delimiter, j):
                end = j + 3
                # Up to two quote characters may sit right before the closing
                # delimiter; they are part of the string.
                while end < self.n and end - j < 5 and self.s[end] == quote:
                    end += 1
                self.i = end
                return
            j += 1
        raise _ScanError

    def _array(
        self, path: tuple[str | int, ...], values: dict[tuple[str | int, ...], tuple[int, int]]
    ) -> None:
        self.i += 1
        index = 0
        while True:
            self._skip_ws_newlines_comments()
            if self.s.startswith("]", self.i):
                self.i += 1
                return
            self._value((*path, index), values)
            index += 1
            self._skip_ws_newlines_comments()
            if self.s.startswith(",", self.i):
                self.i += 1
            elif self.s.startswith("]", self.i):
                self.i += 1
                return
            else:
                raise _ScanError

    def _inline_table(
        self, path: tuple[str | int, ...], values: dict[tuple[str | int, ...], tuple[int, int]]
    ) -> None:
        self.i += 1
        self._skip_ws()
        if self.s.startswith("}", self.i):
            self.i += 1
            return
        while True:
            self._skip_ws()
            key = self._key()
            self._skip_ws()
            self._expect("=")
            self._skip_ws()
            self._value(path + key, values)
            self._skip_ws()
            if self.s.startswith(",", self.i):
                self.i += 1
            elif self.s.startswith("}", self.i):
                self.i += 1
                return
            else:
                raise _ScanError

    def _bare_scalar(self) -> None:
        j = self.i
        while j < self.n and self.s[j] not in "#,]}\r\n":
            j += 1
        while j > self.i and self.s[j - 1] in " \t":
            j -= 1
        if j == self.i:
            raise _ScanError
        self.i = j


def _resolve_table(
    keys: tuple[str, ...], is_array: bool, arrays: dict[tuple[str | int, ...], int]
) -> tuple[str | int, ...]:
    """A table header's path in the parsed data.

    ``[[workloads.containers]]`` appends an element to the ``containers``
    of the latest ``[[workloads]]`` element, and ``[workloads.containers.env]``
    names the latest container's ``env``, so every key that is an array of
    tables resolves to its current element's index. ``arrays`` counts the
    elements of each array by its resolved path."""
    path: tuple[str | int, ...] = ()
    for depth, key in enumerate(keys):
        path = (*path, key)
        if is_array and depth == len(keys) - 1:
            index = arrays.get(path, 0)
            arrays[path] = index + 1
            path = (*path, index)
        elif path in arrays:
            path = (*path, arrays[path] - 1)
    return path


def _scan_env_layout(text: str) -> _EnvLayout | None:
    """Layout of text ``tomllib`` accepts, or None when the scanner cannot
    place it."""
    try:
        return _EnvScanner(text).scan()
    except _ScanError:
        return None


# Spellings that can name a key "env" in a line this scan does not
# otherwise understand: the word itself, or a TOML escape inside a
# quoted key.
_MAY_NAME_ENV_RE = re.compile(r"env|\\[uUx]")


def _scan_unparseable_env(text: str) -> _EnvLayout | None:
    """Line-by-line env scan for text ``tomllib`` rejects.

    An env section is any table header whose last key is ``env``: the
    app-wide ``[env]``, ``[workloads.containers.env]``, ``[jobs.env]``,
    ``[tasks.env]``. Without a parse there is no telling which container a
    header belongs to, so every such section is treated as secret. Inside
    one, every line must be blank, a comment, a table header, or
    ``KEY = "one-line string"``. Outside, any line that could define env
    another way (``env = {...}``, ``env.KEY = ...``, a header nesting
    under ``env``) must be understood too. Anything else returns None:
    without a parse there is no other way to rule out a secret hiding in
    a shape the scan does not model.

    Values are keyed ``("env", KEY)`` for the app-wide table, the only
    ones a save can restore from such text, and by header plus section
    ordinal for the rest."""
    values: dict[tuple[str | int, ...], tuple[int, int]] = {}
    comments: list[tuple[int, int]] = []
    table: tuple[str, ...] = ()
    sections = 0
    pos = 0
    while pos < len(text):
        newline = text.find("\n", pos)
        line_end = len(text) if newline == -1 else newline
        body_end = line_end - 1 if line_end > pos and text[line_end - 1] == "\r" else line_end
        body = text[pos:body_end]
        base = pos
        pos = line_end + 1
        stripped = body.strip(" \t")
        if not stripped:
            continue
        in_env = table[-1:] == ("env",)
        if stripped.startswith("#"):
            if in_env:
                comments.append((base + body.index("#"), base + len(body)))
            continue
        if stripped.startswith("["):
            header = _EnvScanner(body).header_line()
            if header is None:
                if in_env or _MAY_NAME_ENV_RE.search(body):
                    return None
                continue
            table, is_array, trailing = header
            if "env" in table:
                if is_array or table.index("env") != len(table) - 1:
                    return None
                sections += 1
                if trailing is not None:
                    comments.append((base + trailing[0], base + trailing[1]))
            continue
        if in_env:
            entry = _EnvScanner(body).string_entry_line()
            if entry is None or len(entry[0]) != 1:
                return None
            path, (start, end), trailing = entry
            key = ("env", path[0]) if table == ("env",) else (*table, sections, path[0])
            # A repeated key (often why the text does not parse) has one
            # span per occurrence, and only one fits in ``values``.
            if key in values:
                return None
            values[key] = (base + start, base + end)
            if trailing is not None:
                comments.append((base + trailing[0], base + trailing[1]))
            continue
        path = _EnvScanner(body).key_line()
        if path is None:
            if _MAY_NAME_ENV_RE.search(body):
                return None
            continue
        if "env" in path:
            return None
    return _EnvLayout(values, comments)


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


def redact_dotenv_values(text: str) -> str:
    """Mask a ``.env`` paste for the mutation audit log (#1920).

    A line keeps its key only when :func:`parse_dotenv` would import
    that key (``KEY=`` and ``export KEY=`` with a valid name), and its
    value is masked. Every other non-blank line is masked whole:
    comments (``# OLD_KEY=old-secret``), lines with no ``=``, and lines
    whose text before ``=`` is not a valid name, such as the base64
    body of a pasted PEM. Blank lines are kept."""
    if not text:
        return text
    out_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            out_lines.append(raw_line)
            continue
        key = _dotenv_key(line)
        out_lines.append(REDACTED_LINE if key is None else f"{key}={REDACTED_ENV_VALUE}")
    result = "\n".join(out_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


def _dotenv_key(line: str) -> str | None:
    """The ``[export ]KEY`` prefix :func:`parse_dotenv` imports from a stripped line, or None."""
    if line.startswith("#"):
        return None
    prefix = ""
    if line.startswith("export "):
        prefix = "export "
        line = line[len("export ") :].lstrip()
    if "=" not in line:
        return None
    key = line.partition("=")[0].strip()
    if not _is_valid_env_name(key):
        return None
    return f"{prefix}{key}"


def _is_valid_env_name(name: str) -> bool:
    """POSIX env-var name shape: leading letter / underscore,
    rest alphanumeric / underscore.

    ASCII only: ``str.isalnum`` also accepts letters like ``É``, which a
    Kubernetes Secret data key (``[-._a-zA-Z0-9]+``) does not."""
    if not name or not name.isascii():
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)
