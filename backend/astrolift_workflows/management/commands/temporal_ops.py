"""
temporal_ops — Temporal workflow visibility and control from the CLI.

Usage:
    python manage.py temporal_ops list [--limit N] [--status running|all]
    python manage.py temporal_ops terminate <workflow-id> [--reason TEXT]
    python manage.py temporal_ops describe <workflow-id>
"""

from __future__ import annotations

import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _client():
    from temporalio.client import Client

    address = getattr(settings, "TEMPORAL_ADDRESS", "localhost:7233")
    namespace = getattr(settings, "TEMPORAL_NAMESPACE", "default")
    return Client.connect(address, namespace=namespace)


class Command(BaseCommand):
    help = "Temporal workflow visibility and control."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="subcmd")

        ls = sub.add_parser("list", help="List recent workflow executions.")
        ls.add_argument("--limit", type=int, default=20)
        ls.add_argument("--status", choices=["running", "all"], default="running")

        term = sub.add_parser("terminate", help="Terminate a running workflow.")
        term.add_argument("workflow_id")
        term.add_argument("--reason", default="operator-terminated via temporal_ops")

        desc = sub.add_parser("describe", help="Describe a workflow execution.")
        desc.add_argument("workflow_id")

    def handle(self, *args, **options):
        subcmd = options.get("subcmd")
        if subcmd == "list":
            asyncio.run(self._list(options))
        elif subcmd == "terminate":
            asyncio.run(self._terminate(options))
        elif subcmd == "describe":
            asyncio.run(self._describe(options))
        else:
            raise CommandError("Subcommand required: list | terminate | describe")

    async def _list(self, options):
        client = await _client()
        query = 'ExecutionStatus="Running"' if options["status"] == "running" else ""
        count = 0
        async for wf in client.list_workflows(query, page_size=options["limit"]):
            self.stdout.write(f"{wf.id}  {wf.status.name}  {wf.start_time}")
            count += 1
            if count >= options["limit"]:
                break
        self.stdout.write(self.style.SUCCESS(f"Total: {count}"))

    async def _terminate(self, options):
        client = await _client()
        wf_id = options["workflow_id"]
        handle = client.get_workflow_handle(wf_id)
        await handle.terminate(reason=options["reason"])
        self.stdout.write(self.style.SUCCESS(f"Terminated: {wf_id}"))

    async def _describe(self, options):
        client = await _client()
        handle = client.get_workflow_handle(options["workflow_id"])
        desc = await handle.describe()
        self.stdout.write(f"status:   {desc.status.name}")
        self.stdout.write(f"run_id:   {desc.run_id}")
        self.stdout.write(f"started:  {desc.start_time}")
        self.stdout.write(f"closed:   {desc.close_time}")
