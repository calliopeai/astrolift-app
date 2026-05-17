"""
Audit payload redaction.

The audit log's ``data`` JSON field carries the mutation context —
permissions, durations, error messages, plus per-mutation ``extras``
that may include secrets, tokens, passwords. Compliance teams want to
see *what changed*, not the raw secret values.

This module exposes one entry point: :func:`scrub_payload`. It walks a
JSON-compatible value (dict / list / scalar) and replaces any value
whose key matches the universal sensitive-key list with the literal
``"***redacted***"`` sentinel. Matching is case-insensitive and
substring-based so ``plaintext_secret``, ``api_key``, ``authToken``,
``X-Authorization`` all redact.

The scrubber is value-preserving: it doesn't drop the key (so the
existence of the field is still visible), it only swaps the value.
The literal sentinel is identical regardless of original type so the
UI's JSON pretty-printer renders consistently.
"""

from __future__ import annotations

from typing import Any

# Substring matches: any key that contains one of these tokens (case
# insensitive) gets its value swapped. Conservative on purpose — false
# positives are cheap (one redacted scalar), missed secrets are not.
_SENSITIVE_KEY_TOKENS: tuple[str, ...] = (
    "secret",
    "password",
    "passwd",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "private_key",
    "privatekey",
    "credential",
    "session_key",
    "client_secret",
)

REDACTED_SENTINEL = "***redacted***"

# Cap recursion so a pathological cyclic-via-dict payload (shouldn't
# happen for real audit rows, but defense-in-depth) doesn't blow the
# stack. 32 levels covers any realistic mutation extras dict.
_MAX_DEPTH = 32


def _key_is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(token in normalized for token in _SENSITIVE_KEY_TOKENS)


def scrub_payload(value: Any, *, _depth: int = 0) -> Any:
    """Return a deep-copied version of ``value`` with sensitive values
    replaced by ``REDACTED_SENTINEL``.

    Behaviour:

    * dict: every key kept; values for sensitive keys swapped, others
      recursed into.
    * list / tuple: each element recursed (returned as list — JSON
      doesn't distinguish).
    * scalars: returned as-is.
    * unsupported types (datetime, custom objects): coerced via
      ``str()`` so the result is always JSON-serializable. The
      ``data`` JSONField only ever stores JSON primitives in practice,
      but we never want a serialization error from a malformed extras
      dict to leak the raw payload back to the resolver.
    """
    if _depth >= _MAX_DEPTH:
        return REDACTED_SENTINEL

    if isinstance(value, dict):
        scrubbed: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if _key_is_sensitive(key):
                # Preserve type-shape hints for the UI: a sensitive
                # list/dict still shows "[redacted]" / "{redacted}"
                # rather than the literal scalar so reviewers can tell
                # how complex the redacted blob was.
                if isinstance(v, list):
                    scrubbed[key] = [REDACTED_SENTINEL]
                elif isinstance(v, dict):
                    scrubbed[key] = {"__redacted__": True}
                else:
                    scrubbed[key] = REDACTED_SENTINEL
            else:
                scrubbed[key] = scrub_payload(v, _depth=_depth + 1)
        return scrubbed

    if isinstance(value, (list, tuple)):
        return [scrub_payload(item, _depth=_depth + 1) for item in value]

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    # Fallback: coerce to string so JSON serialization never explodes.
    return str(value)
