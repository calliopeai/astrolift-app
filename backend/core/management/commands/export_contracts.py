"""Export deterministic public GraphQL and MCP contracts."""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from astrolift_agents.mcp_contract import mcp_contract_document


def _normalized_sdl() -> str:
    from config.schema import schema

    return schema.as_str().rstrip() + "\n"


def _normalized_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


class Command(BaseCommand):
    help = "Export deterministic public GraphQL SDL and MCP capability metadata"

    def add_arguments(self, parser):
        parser.add_argument(
            "--graphql-output",
            default="schema.graphql",
            help="GraphQL SDL path relative to the current directory",
        )
        parser.add_argument(
            "--mcp-output",
            default="contracts/mcp-tools.json",
            help="MCP capability document path relative to the current directory",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Fail if checked-in artifacts differ instead of writing them",
        )

    def handle(self, *args, **options):
        outputs = {
            Path(options["graphql_output"]): _normalized_sdl(),
            Path(options["mcp_output"]): _normalized_json(mcp_contract_document()),
        }
        if options["check"]:
            stale = [str(path) for path, content in outputs.items() if self._read(path) != content]
            if stale:
                joined = ", ".join(stale)
                raise CommandError(f"generated contracts are stale: {joined}; run make contracts")
            self.stdout.write(self.style.SUCCESS("Generated contracts are current"))
            return

        for path, content in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self.stdout.write(f"wrote {path}")

    @staticmethod
    def _read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
