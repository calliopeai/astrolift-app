"""The pipeline TOML declares its dialect, and old files still parse (#65).

`pipelines-dsl-spec.md` lists eight requirements before the declarative
pipeline can be called supported. The first is "one published schema defines
the accepted document and version field", and there was no version field at
all: the writer emitted a document, the reader parsed it, and nothing said
which dialect either spoke. The first breaking change would have announced
itself by failing on an operator's repository.

The tests that matter most here are the backward-compatibility ones. Every
pipeline TOML already sitting in a repo was written before this field
existed, and a version check that rejects those buys a spec at the cost of
breaking every user. So absence means v1, by definition -- and that has to
be pinned, because it is exactly the kind of leniency a later refactor
"tidies up".
"""

from __future__ import annotations

import pytest

from astrolift_ci_convert.common import schema_version
from astrolift_ci_convert.common.toml_reader import TomlReadError, read_toml
from astrolift_ci_convert.common.toml_writer import write_toml
from astrolift_ci_convert.common.types import JobDef, PipelineDef, StepDef

_MINIMAL = """
name = "ci"

[[jobs.build.steps]]
name = "say hello"
run = "echo hello"
"""


# ---------------------------------------------------------------------------
# Backward compatibility: the files already in people's repos
# ---------------------------------------------------------------------------


def test_a_document_with_no_version_is_read_as_v1():
    """The compatibility guarantee. Every pipeline TOML written before this
    field existed must keep working, or the version field is a breaking
    change dressed as a spec."""
    assert schema_version.FIELD not in _MINIMAL

    definition = read_toml(_MINIMAL)

    assert definition.schema_version == 1
    assert definition.name == "ci"
    assert [j.job_id for j in definition.jobs] == ["build"]


def test_the_implied_version_is_stated_not_incidental():
    """Pinned so a refactor cannot quietly change what an unversioned
    document means."""
    assert schema_version.IMPLIED_WHEN_ABSENT == 1
    assert schema_version.coerce(None) == 1


def test_the_current_version_is_one_the_reader_accepts():
    """A build that writes a document it cannot read is broken on its face."""
    assert schema_version.CURRENT in schema_version.SUPPORTED


# ---------------------------------------------------------------------------
# Refusing what we cannot read
# ---------------------------------------------------------------------------


def test_an_unknown_version_is_refused_with_the_supported_set():
    body = f'{schema_version.FIELD} = 99\nname = "ci"\n'

    with pytest.raises(TomlReadError) as exc:
        read_toml(body)

    message = str(exc.value)
    assert schema_version.FIELD in message
    # The message has to carry what IS accepted; "invalid pipeline" alone
    # leaves an operator guessing at 3am.
    assert "1" in message
    assert "99" in message


def test_a_non_integer_version_is_refused():
    with pytest.raises(TomlReadError):
        read_toml(f'{schema_version.FIELD} = "1"\nname = "ci"\n')


def test_a_boolean_version_is_refused_despite_being_an_int_in_python():
    """`isinstance(True, int)` is True, so `schema_version = true` would
    otherwise sail through as version 1."""
    with pytest.raises(schema_version.UnsupportedSchemaVersion):
        schema_version.coerce(True)

    with pytest.raises(TomlReadError):
        read_toml(f'{schema_version.FIELD} = true\nname = "ci"\n')


def test_the_dialect_is_checked_before_anything_else():
    """A document in a dialect we cannot read should say so, rather than
    complaining about a field whose meaning depends on the dialect."""
    body = f"{schema_version.FIELD} = 99\n"  # also missing `name`

    with pytest.raises(TomlReadError) as exc:
        read_toml(body)

    assert schema_version.FIELD in str(exc.value)
    assert "name" not in str(exc.value)


# ---------------------------------------------------------------------------
# Round trip: the writer's output is a document the reader accepts
# ---------------------------------------------------------------------------


def test_the_writer_declares_the_version_and_the_reader_reads_it_back():
    original = PipelineDef(
        name="ci",
        jobs=[
            JobDef(
                job_id="build",
                name="Build",
                steps=[StepDef(name="compile", run="make")],
            )
        ],
    )

    text = write_toml(original)
    assert f"{schema_version.FIELD} = {schema_version.CURRENT}" in text

    parsed = read_toml(text)
    assert parsed.schema_version == schema_version.CURRENT
    assert parsed.name == "ci"
    assert [j.job_id for j in parsed.jobs] == ["build"]
    assert [s.run for s in parsed.jobs[0].steps] == ["make"]


def test_the_version_is_the_first_line_so_a_file_is_identifiable_at_a_glance():
    text = write_toml(PipelineDef(name="ci"))
    first = next(line for line in text.splitlines() if line.strip())

    assert first.startswith(schema_version.FIELD)


def test_a_default_constructed_definition_declares_the_current_version():
    """The GHA/GitLab converters build a PipelineDef directly. They must not
    each have to remember to stamp the dialect."""
    assert PipelineDef(name="ci").schema_version == schema_version.CURRENT


def test_a_read_definition_rewrites_to_the_version_it_declared():
    """Round-tripping must not silently upgrade a document. When v2 lands,
    reading a v1 file and writing it back should still say 1 unless someone
    asked for a migration."""
    parsed = read_toml(_MINIMAL)
    parsed.schema_version = 1

    assert f"{schema_version.FIELD} = 1" in write_toml(parsed)


# ---------------------------------------------------------------------------
# Idempotence: the digest depends on it
# ---------------------------------------------------------------------------


def test_write_read_write_is_byte_identical():
    """`PipelineRun.definition_digest` hashes the document text, so a
    non-idempotent round trip means the same pipeline hashes differently
    depending on how many times it has been converted.

    This caught a real bug the moment it was written: the writer emits a
    command as a multi-line literal (`\'\'\'` newline body newline `\'\'\'`),
    TOML keeps the newline before the closing delimiter, and `make` came
    back as `make\n`. Every re-write then grew another one.
    """
    original = PipelineDef(
        name="ci",
        jobs=[
            JobDef(
                job_id="build",
                name="Build",
                steps=[
                    StepDef(name="one-liner", run="make"),
                    StepDef(name="multi", run="set -e\nmake test\nmake lint"),
                ],
            )
        ],
    )

    once = write_toml(original)
    twice = write_toml(read_toml(once))
    thrice = write_toml(read_toml(twice))

    assert once == twice, "write -> read -> write changed the document"
    assert twice == thrice


def test_a_command_survives_the_round_trip_exactly():
    original = PipelineDef(
        name="ci",
        jobs=[JobDef(job_id="b", name="B", steps=[StepDef(name="s", run="make")])],
    )

    parsed = read_toml(write_toml(original))

    assert [s.run for s in parsed.jobs[0].steps] == ["make"]


def test_a_multi_line_command_keeps_its_interior_newlines():
    """Stripping the writer's trailing newline must not touch the body."""
    script = "set -e\necho a\necho b"
    original = PipelineDef(
        name="ci",
        jobs=[JobDef(job_id="b", name="B", steps=[StepDef(name="s", run=script)])],
    )

    parsed = read_toml(write_toml(original))

    assert parsed.jobs[0].steps[0].run == script


def test_a_basic_string_command_is_untouched():
    """A hand-written `run = "make"` has no trailing newline to strip."""
    body = """
name = "ci"

[[jobs.build.steps]]
name = "s"
run = "make"
"""

    assert read_toml(body).jobs[0].steps[0].run == "make"
