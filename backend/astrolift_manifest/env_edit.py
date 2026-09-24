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

Redact-side (#1920): ``redact_env_values(toml_text)`` masks every
``[env]`` value, and every comment inside the ``[env]`` section, for
a caller who has not proven ``secret.read`` plus step-up elevation.
It edits the value literals in place, so the rest of the document is
byte-identical and a masked read can be saved back without
reformatting anything. ``redact_dotenv_values(text)`` masks a
bulk-import ``.env`` paste.

Restore-side (#1920): ``resolve_masked_env_values(incoming_text,
fallback_text=...)`` undoes the masking on save. A masked placeholder
under an ``[env]`` key is replaced by that key's stored literal, so an
editor that loads the masked text, changes something unrelated, and
saves the whole document leaves every stored secret as it was.

Used by the GraphQL secrets resolver (#279). Mutations write to
the staging buffer (``manifest_raw_staged``); the user-driven
'Push to Repo' flow re-serializes to the source repo.
"""

from __future__ import annotations

import re
import string
import tomllib
from collections.abc import Mapping
from typing import NamedTuple


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


def redact_env_values(toml_text: str) -> str:
    """Return ``toml_text`` with every top-level ``[env]`` value, and
    every comment in the ``[env]`` section, masked. Keys and the rest
    of the document are left exactly as they were.

    The masked text is re-parsed and must equal the original with only
    the ``[env]`` values replaced; if the in-place edit cannot prove
    that, the document is rebuilt with ``tomli_w`` instead (still
    masked, formatting lost). Text that does not parse is masked line
    by line, or replaced whole by :data:`REDACTED_DOCUMENT` when the
    line scan cannot account for every line that might hold a secret.
    A rejected ``updateManifest``/``registerApp`` input is exactly
    that kind of text, and the mutation audit log records it."""
    if not toml_text or not toml_text.strip():
        return toml_text
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError:
        layout = _scan_unparseable_env(toml_text)
        if layout is None:
            return REDACTED_DOCUMENT
        return _mask(toml_text, layout)
    env_table = data.get("env")
    if not isinstance(env_table, Mapping):
        return toml_text
    masked_data = {**data, "env": dict.fromkeys(env_table, REDACTED_ENV_VALUE)}
    layout = _scan_env_layout(toml_text)
    if layout is not None and set(layout.values) == set(env_table):
        masked = _mask(toml_text, layout)
        if _loads_or_none(masked) == masked_data:
            return masked
    import tomli_w

    return tomli_w.dumps(masked_data)


def resolve_masked_env_values(incoming_text: str, *, fallback_text: str) -> tuple[str | None, list[str]]:
    """Put stored ``[env]`` values back where ``incoming_text`` carries
    the masked placeholder (#1920).

    ``fallback_text`` is the stored document the caller's masked read
    was derived from. Each ``[env]`` key whose incoming value is
    :data:`REDACTED_ENV_VALUE` gets that key's stored literal, byte for
    byte, and each numbered comment marker in the ``[env]`` section gets
    the stored comment it replaced. Nothing else in ``incoming_text``
    changes. Only ``[env]`` positions are restored, so a caller cannot
    move a stored secret anywhere a masked read would show it.

    Returns ``(None, keys)`` when a placeholder names a key the stored
    document has no value for; the caller must reject the write rather
    than store the placeholder as if it were a secret. Returns
    ``(incoming_text, [])`` untouched when there is nothing to restore,
    including text that does not parse (the caller's own validation
    rejects that)."""
    if not incoming_text or not incoming_text.strip():
        return incoming_text, []
    try:
        incoming_data = tomllib.loads(incoming_text)
    except tomllib.TOMLDecodeError:
        return incoming_text, []
    incoming_env = incoming_data.get("env")
    if not isinstance(incoming_env, Mapping):
        return incoming_text, []
    masked_keys = [key for key, value in incoming_env.items() if value == REDACTED_ENV_VALUE]
    layout = _scan_env_layout(incoming_text)
    markers = _comment_markers(incoming_text, layout)
    if not masked_keys and not markers:
        return incoming_text, []

    stored = _stored_env(fallback_text)
    missing = sorted(key for key in masked_keys if key not in stored.values)
    if missing:
        return None, missing

    restored_data = {
        **incoming_data,
        "env": {**incoming_env, **{key: stored.values[key] for key in masked_keys}},
    }
    replacements = _restore_replacements(masked_keys, markers, layout, stored)
    if replacements is not None:
        resolved = _splice(incoming_text, replacements)
        if _loads_or_none(resolved) == restored_data:
            return resolved, []
    if not masked_keys:
        # Only comment markers, and they could not be put back in place:
        # leave them as the harmless comments they are rather than
        # reformat a document whose values need nothing.
        return incoming_text, []
    import tomli_w

    return tomli_w.dumps(restored_data), []


class _EnvLayout(NamedTuple):
    # env key -> (start, end) of its value literal
    values: dict[str, tuple[int, int]]
    # (start, end) of each comment in the [env] section, in document order
    comments: list[tuple[int, int]]


class _StoredEnv(NamedTuple):
    values: dict[str, object]
    literals: dict[str, str]
    comments: list[str]


def _mask(text: str, layout: _EnvLayout) -> str:
    replacements = [(start, end, _MASKED_LITERAL) for start, end in layout.values.values()]
    replacements += [
        (start, end, _COMMENT_MARKER.format(ordinal))
        for ordinal, (start, end) in enumerate(layout.comments, start=1)
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
    masked_keys: list[str],
    markers: list[tuple[int, int, int]],
    layout: _EnvLayout | None,
    stored: _StoredEnv,
) -> list[tuple[int, int, str]] | None:
    if layout is None:
        return None
    replacements = []
    for key in masked_keys:
        literal = stored.literals.get(key) or _toml_literal(stored.values[key])
        if literal is None or key not in layout.values:
            return None
        start, end = layout.values[key]
        replacements.append((start, end, literal))
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
        # scan that masked it could read.
        layout = _scan_unparseable_env(text)
        if layout is None:
            return _StoredEnv({}, {}, [])
        literals = {key: text[start:end] for key, (start, end) in layout.values.items()}
        values = {key: _decode_literal(literal) for key, literal in literals.items()}
        return _StoredEnv(values, literals, [text[start:end] for start, end in layout.comments])
    env_table = data.get("env")
    values = dict(env_table) if isinstance(env_table, Mapping) else {}
    layout = _scan_env_layout(text)
    if layout is None:
        return _StoredEnv(values, {}, [])
    return _StoredEnv(
        values,
        {key: text[start:end] for key, (start, end) in layout.values.items()},
        [text[start:end] for start, end in layout.comments],
    )


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
    """Finds where the ``[env]`` value literals and comments sit in TOML text.

    Only a locator: every caller checks the edited text against a real
    parse, so a shape this does not model costs a fallback, never a
    wrong answer. Raises :class:`_ScanError` on anything unexpected.
    """

    def __init__(self, text: str) -> None:
        self.s = text
        self.n = len(text)
        self.i = 0

    def scan(self) -> _EnvLayout:
        values: dict[str, tuple[int, int]] = {}
        comments: list[tuple[int, int]] = []
        table: tuple[str, ...] = ()
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
                if table == ("env",):
                    comments.append(span)
                self._end_of_line()
                continue
            if char == "[":
                table, is_array = self._table_header()
                if table[:1] == ("env",) and (is_array or len(table) != 1):
                    raise _ScanError
                trailing = self._line_tail()
                if trailing is not None and table == ("env",):
                    comments.append(trailing)
                continue
            path = table + self._key()
            self._skip_ws()
            self._expect("=")
            self._skip_ws()
            start = self.i
            self._value()
            is_env_entry = path[0] == "env"
            if is_env_entry:
                if len(path) != 2 or path[1] in values:
                    raise _ScanError
                values[path[1]] = (start, self.i)
            trailing = self._line_tail()
            if trailing is not None and is_env_entry:
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

    def _value(self) -> None:
        if self.s.startswith('"""', self.i):
            self._multiline_string('"')
        elif self.s.startswith("'''", self.i):
            self._multiline_string("'")
        elif self.s.startswith('"', self.i):
            self._basic_string()
        elif self.s.startswith("'", self.i):
            self._literal_string()
        elif self.s.startswith("[", self.i):
            self._array()
        elif self.s.startswith("{", self.i):
            self._inline_table()
        else:
            self._bare_scalar()

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

    def _array(self) -> None:
        self.i += 1
        while True:
            self._skip_ws_newlines_comments()
            if self.s.startswith("]", self.i):
                self.i += 1
                return
            self._value()
            self._skip_ws_newlines_comments()
            if self.s.startswith(",", self.i):
                self.i += 1
            elif self.s.startswith("]", self.i):
                self.i += 1
                return
            else:
                raise _ScanError

    def _inline_table(self) -> None:
        self.i += 1
        self._skip_ws()
        if self.s.startswith("}", self.i):
            self.i += 1
            return
        while True:
            self._skip_ws()
            self._key()
            self._skip_ws()
            self._expect("=")
            self._skip_ws()
            self._value()
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


def _scan_env_layout(text: str) -> _EnvLayout | None:
    """Layout of text ``tomllib`` accepts, or None when the scanner cannot
    place it (nested ``[env.*]`` tables, an inline ``env = {...}``, ...)."""
    try:
        return _EnvScanner(text).scan()
    except _ScanError:
        return None


# Spellings that can name a key "env" in a line this scan does not
# otherwise understand: the word itself, or a TOML escape inside a
# quoted key.
_MAY_NAME_ENV_RE = re.compile(r"env|\\[uUx]")


def _scan_unparseable_env(text: str) -> _EnvLayout | None:
    """Line-by-line ``[env]`` scan for text ``tomllib`` rejects.

    Inside ``[env]`` every line must be blank, a comment, a table header,
    or ``KEY = "one-line string"``. Outside it, any line that could
    define or reopen ``env`` must be understood too. Anything else
    returns None: without a parse there is no other way to rule out a
    secret hiding in a shape the scan does not model."""
    values: dict[str, tuple[int, int]] = {}
    comments: list[tuple[int, int]] = []
    table: tuple[str, ...] = ()
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
        in_env = table == ("env",)
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
            if table[:1] == ("env",):
                if is_array or len(table) != 1:
                    return None
                if trailing is not None:
                    comments.append((base + trailing[0], base + trailing[1]))
            continue
        if in_env:
            entry = _EnvScanner(body).string_entry_line()
            if entry is None:
                return None
            path, (start, end), trailing = entry
            # A repeated key (often why the text does not parse) has one
            # span per occurrence, and only one fits in ``values``.
            if len(path) != 1 or path[0] in values:
                return None
            values[path[0]] = (base + start, base + end)
            if trailing is not None:
                comments.append((base + trailing[0], base + trailing[1]))
            continue
        path = _EnvScanner(body).key_line()
        if path is None:
            if _MAY_NAME_ENV_RE.search(body):
                return None
            continue
        if (table + path)[:1] == ("env",):
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
    rest alphanumeric / underscore."""
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)
