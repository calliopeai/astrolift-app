"""The `alci` command this package's docstring documents (#1601).

`astrolift_ci_convert/__init__.py` advertises::

    alci convert github .github/workflows/ci.yml
    alci convert gitlab .gitlab-ci.yml

and no entry point defined `alci`, so the two converters were reachable only
as a library. Nothing outside the package imported them either -- only
`common.toml_reader` and `common.types` were used elsewhere, by the pipeline
TOML reader -- so the parsers and converters had tests and no caller.

Deliberately argparse and not the platform's usual stack: the package
docstring says "standalone package with zero Django dependencies", and that
is what makes it usable as a one-shot migration tool in a repo that has
never heard of Astrolift. Importing anything heavier here would cost that.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

FLAVOURS = ("github", "gitlab")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alci",
        description="Convert a GitHub Actions or GitLab CI pipeline to Astrolift TOML.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    convert = sub.add_parser("convert", help="convert a CI config to astrolift.toml")
    convert.add_argument("flavour", choices=FLAVOURS, help="source CI system")
    convert.add_argument("path", help="path to the CI config, or - for stdin")
    convert.add_argument(
        "-o",
        "--output",
        default="-",
        help="write TOML here instead of stdout",
    )
    return parser


def _convert(flavour: str, yaml_str: str) -> str:
    """Dispatch to the flavour's parse+convert pair.

    Imported inside the call rather than at module scope so `alci --help`
    does not pay for a YAML parse of either converter's dependencies.
    """
    if flavour == "github":
        from astrolift_ci_convert.gha import convert, parse
    else:
        from astrolift_ci_convert.gitlab import convert, parse

    return convert(parse(yaml_str))


def main(argv: Sequence[str] | None = None) -> int:
    """Exit code, not an exception, for every expected failure.

    A migration tool gets run in a shell loop over a directory of repos, and
    a traceback there is noise the operator has to read past. An unreadable
    file, an unparseable pipeline and an unsupported construct are all
    ordinary outcomes of pointing this at a real repo.
    """
    args = build_parser().parse_args(argv)

    try:
        if args.path == "-":
            yaml_str = sys.stdin.read()
        else:
            with open(args.path, encoding="utf-8") as handle:
                yaml_str = handle.read()
    except OSError as exc:
        print(f"alci: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    try:
        toml_str = _convert(args.flavour, yaml_str)
    except Exception as exc:  # noqa: BLE001 - see the docstring above
        print(f"alci: could not convert {args.path}: {exc}", file=sys.stderr)
        return 1

    if args.output == "-":
        sys.stdout.write(toml_str)
        if not toml_str.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    try:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(toml_str)
            if not toml_str.endswith("\n"):
                handle.write("\n")
    except OSError as exc:
        print(f"alci: cannot write {args.output}: {exc}", file=sys.stderr)
        return 2
    return 0
