"""`alci` converts a pipeline and reports failures as exit codes (#1601).

The package docstring has advertised `alci convert github ...` since the
converters landed, and no entry point defined `alci`. Nothing outside the
package imported the converters either -- only `common.toml_reader` and
`common.types` were used elsewhere -- so the parsers and converters had
tests and no caller.

The exit-code tests matter as much as the happy path. This is a migration
tool: it gets run in a shell loop over a directory of repos, and an
unreadable file or an unparseable pipeline are ordinary outcomes of pointing
it at real ones. A traceback there is noise the operator reads past.
"""

from __future__ import annotations

import pathlib

from astrolift_ci_convert.cli import build_parser, main

GHA = """
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo hello
"""


def test_it_converts_github_actions_to_toml(tmp_path, capsys):
    src = tmp_path / "ci.yml"
    src.write_text(GHA)

    code = main(["convert", "github", str(src)])

    assert code == 0
    assert capsys.readouterr().out.strip()


def test_it_writes_to_a_file_when_asked(tmp_path):
    src = tmp_path / "ci.yml"
    src.write_text(GHA)
    out = tmp_path / "astrolift.toml"

    assert main(["convert", "github", str(src), "-o", str(out)]) == 0
    assert out.read_text().strip()


def test_output_always_ends_with_a_newline(tmp_path):
    """A TOML file without a trailing newline is a diff that shows up in
    every subsequent commit that touches it."""
    src = tmp_path / "ci.yml"
    src.write_text(GHA)
    out = tmp_path / "astrolift.toml"

    main(["convert", "github", str(src), "-o", str(out)])

    assert out.read_text().endswith("\n")


def test_it_reads_stdin(monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(GHA))

    assert main(["convert", "github", "-"]) == 0
    assert capsys.readouterr().out.strip()


# ---- failures are exit codes, not tracebacks ----------------------------


def test_an_unreadable_file_exits_2(tmp_path, capsys):
    code = main(["convert", "github", str(tmp_path / "nope.yml")])

    assert code == 2
    assert "cannot read" in capsys.readouterr().err


def test_an_unwritable_output_exits_2(tmp_path, capsys):
    src = tmp_path / "ci.yml"
    src.write_text(GHA)

    code = main(["convert", "github", str(src), "-o", str(tmp_path / "no" / "such" / "dir" / "out.toml")])

    assert code == 2
    assert "cannot write" in capsys.readouterr().err


def test_an_unparseable_pipeline_exits_1(tmp_path, capsys):
    src = tmp_path / "ci.yml"
    src.write_text("this: [is: not: valid: yaml")

    code = main(["convert", "github", str(src)])

    assert code == 1
    assert "could not convert" in capsys.readouterr().err


# ---- the interface the docstring promises -------------------------------


def test_both_documented_flavours_are_accepted():
    parser = build_parser()

    for flavour in ("github", "gitlab"):
        args = parser.parse_args(["convert", flavour, "x.yml"])
        assert args.flavour == flavour


def test_the_console_script_is_declared():
    """The actual gap this issue was about. A CLI module nothing installs as
    `alci` is the same shape as the converters themselves: present, tested,
    unreachable."""
    import tomllib

    pyproject = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text())

    assert config["project"]["scripts"]["alci"] == "astrolift_ci_convert.cli:main"
