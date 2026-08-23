"""The pipeline TOML document's version contract (#65).

Why this exists as its own module rather than a constant in the reader: a
version field is only worth having if the *rules* around it are written down
in one place, and those rules are the part that has to survive people who
have not read the spec.

The state this replaces: `pipelines-dsl-spec.md` listed eight requirements
before the declarative pipeline could be called supported, and the first was
"one published schema defines the accepted document and version field."
There was no version field at all. The writer emitted a document, the reader
(added 83 days later) parsed it, and nothing said which dialect either spoke
-- so the first breaking change would have had no way to announce itself
except by failing on an operator's repository.

The rules:

* ``CURRENT`` is what the writer emits and what a new document should say.
* ``SUPPORTED`` is every version the reader accepts. Reading is deliberately
  wider than writing: an operator's file outlives our release cadence.
* **A missing version means version 1.** Every document written before this
  field existed is a valid v1 document, and refusing those would break every
  pipeline already in a repository to gain nothing. This is the one piece of
  leniency here and it is bounded: it applies only to absence, never to a
  value we do not recognise.
* An unrecognised version is refused with the supported set in the message,
  because "invalid pipeline" without the accepted values is not actionable.
* A version is an integer, not a semver string. The document either parses
  under a dialect or it does not; there is no meaningful "patch" to a
  document schema, and a string invites `"1.0"` != `"1"` bugs.

Adding version 2, when it comes: add it to ``SUPPORTED``, bump ``CURRENT``,
and add a round-trip test for it. Keep 1 in ``SUPPORTED`` until you have a
migration story for the files already out there -- dropping it is the
breaking change, not adding 2.
"""

from __future__ import annotations

# The version this build writes.
CURRENT: int = 1

# Every version this build can read. Ordered for a stable error message.
SUPPORTED: tuple[int, ...] = (1,)

# The document key.
FIELD: str = "schema_version"

# What a document without the field is taken to mean. See the module
# docstring: pre-versioning files are v1 by definition, not errors.
IMPLIED_WHEN_ABSENT: int = 1


class UnsupportedSchemaVersion(ValueError):
    """The document declares a version this build cannot read."""

    def __init__(self, declared: object) -> None:
        self.declared = declared
        supported = ", ".join(str(v) for v in SUPPORTED)
        super().__init__(
            f"unsupported {FIELD} {declared!r}; this build reads {supported}. "
            "Upgrade Astrolift, or pin the pipeline to a version it accepts."
        )


def coerce(raw: object) -> int:
    """Validate a declared version, or supply the implied one.

    ``None`` (the key absent) yields :data:`IMPLIED_WHEN_ABSENT`. A bool is
    refused before the int check because ``isinstance(True, int)`` is True in
    Python and ``schema_version = true`` should not silently mean version 1.
    """
    if raw is None:
        return IMPLIED_WHEN_ABSENT
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise UnsupportedSchemaVersion(raw)
    if raw not in SUPPORTED:
        raise UnsupportedSchemaVersion(raw)
    return raw
