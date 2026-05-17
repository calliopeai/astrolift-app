"""Minimal CBOR decoder for App Attest attestation objects (#496).

The Apple App Attest attestation object is CBOR-encoded — a
``{"fmt", "attStmt", "authData"}`` map at the top level. Vendored
inline so this codebase doesn't grow a ``cbor2`` dep just for one
ceremony (the workspace already prefers a few hundred lines of
pure-Python over a Pipfile bump, per the ``vendor-over-dep`` rule).

This is a *decoder only* and handles the subset Apple's attestation
object uses:

* unsigned + negative integers
* byte strings + text strings (definite length)
* arrays + maps (definite length)
* tagged values (passed through; we don't need to act on the tag)
* the simple values true / false / null

Indefinite-length items, floats, and unicode strings beyond the
basic plane aren't used by Apple and aren't supported. A
:class:`CborDecodeError` is raised on any unsupported major type or
truncated input.

Reference: RFC 8949 §3.
"""

from __future__ import annotations

import struct
from typing import Any


class CborDecodeError(Exception):
    pass


def decode(data: bytes) -> Any:
    """Decode the single top-level CBOR value in ``data``.

    Trailing bytes after the value are tolerated — Apple's attestation
    objects are sometimes padded depending on the SDK build. Returns
    the decoded Python object (dict / list / bytes / str / int / bool
    / None).
    """
    value, _ = _decode_at(data, 0)
    return value


def _decode_at(data: bytes, pos: int) -> tuple[Any, int]:
    if pos >= len(data):
        raise CborDecodeError("unexpected end of input")
    initial = data[pos]
    pos += 1
    major = initial >> 5
    minor = initial & 0x1F

    arg, pos = _read_arg(data, pos, minor)

    if major == 0:  # unsigned int
        return arg, pos
    if major == 1:  # negative int
        return -1 - arg, pos
    if major == 2:  # byte string
        end = pos + arg
        if end > len(data):
            raise CborDecodeError("byte string truncated")
        return data[pos:end], end
    if major == 3:  # text string
        end = pos + arg
        if end > len(data):
            raise CborDecodeError("text string truncated")
        return data[pos:end].decode("utf-8"), end
    if major == 4:  # array
        out: list[Any] = []
        for _ in range(arg):
            value, pos = _decode_at(data, pos)
            out.append(value)
        return out, pos
    if major == 5:  # map
        out_map: dict[Any, Any] = {}
        for _ in range(arg):
            key, pos = _decode_at(data, pos)
            value, pos = _decode_at(data, pos)
            out_map[key] = value
        return out_map, pos
    if major == 6:  # tag — pass through
        value, pos = _decode_at(data, pos)
        return value, pos
    if major == 7:  # simple / float
        if arg == 20:
            return False, pos
        if arg == 21:
            return True, pos
        if arg == 22 or arg == 23:
            return None, pos
        raise CborDecodeError(f"unsupported simple value {arg!r}")

    raise CborDecodeError(f"unsupported major type {major}")


def _read_arg(data: bytes, pos: int, minor: int) -> tuple[int, int]:
    if minor < 24:
        return minor, pos
    if minor == 24:
        if pos + 1 > len(data):
            raise CborDecodeError("truncated 1-byte arg")
        return data[pos], pos + 1
    if minor == 25:
        if pos + 2 > len(data):
            raise CborDecodeError("truncated 2-byte arg")
        return struct.unpack_from(">H", data, pos)[0], pos + 2
    if minor == 26:
        if pos + 4 > len(data):
            raise CborDecodeError("truncated 4-byte arg")
        return struct.unpack_from(">I", data, pos)[0], pos + 4
    if minor == 27:
        if pos + 8 > len(data):
            raise CborDecodeError("truncated 8-byte arg")
        return struct.unpack_from(">Q", data, pos)[0], pos + 8
    raise CborDecodeError(f"indefinite-length items not supported (minor={minor})")


__all__ = ["CborDecodeError", "decode"]
