"""Unattended entry point: run cells, print the grid, exit non-zero on red.

    python -m _cert.harness --cell happy-path/aws --update-image <ref> \
        --project-id <uuid> --source-repo calliopeai/astrolift-sample-api

The exit code is the whole point of the wiring: this is meant to run from CI and
from the campaign's own scripts, and a harness whose failure has to be read out
of stdout gets skimmed.

Everything cloud-touching is behind ``--i-have-credentials``. Phase 1 of the
campaign is explicitly credential-free, and a runner that reaches a paid account
because someone pasted a command from a spec is the failure mode that flag
exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _cert.campaign import Campaign
from _cert.collection import CAMPAIGN, MANIFEST_ROOT
from _cert.harness.cell import Cell, UnrunnableCell
from _cert.harness.model import Grid
from _cert.harness.platform import CliPlatformClient
from _cert.harness.runner import LifecycleRunner


def _cell_paths(patterns: list[str]) -> list[Path]:
    if not patterns:
        return sorted(MANIFEST_ROOT.glob("happy-path/*.toml"))
    paths: list[Path] = []
    for pattern in patterns:
        candidate = MANIFEST_ROOT / f"{pattern}.toml"
        paths.extend([candidate] if candidate.exists() else sorted(MANIFEST_ROOT.glob(f"{pattern}.toml")))
    return paths


def _scan_for(cloud: str, campaign: Campaign, region: str, project: str, resource_group: str, subscription: str):
    """Bind the cloud's scanner to a live inventory. Imported lazily so the
    module stays importable without any cloud SDK installed."""
    if cloud == "aws":
        from _cert.orphans import aws as scanner
        from _cert.orphans.aws import LiveAwsInventory

        inventory = LiveAwsInventory(region=region)
    elif cloud == "gcp":
        from _cert.orphans import gcp as scanner
        from _cert.orphans.gcp import LiveGcpInventory

        inventory = LiveGcpInventory(project_id=project)
    elif cloud == "azure":
        from _cert.orphans import azure as scanner
        from _cert.orphans.azure import LiveAzureInventory

        inventory = LiveAzureInventory(subscription_id=subscription, resource_group=resource_group)
    else:
        raise SystemExit(f"no orphan scanner for cloud {cloud!r}; a cell cannot be certified without one")
    return lambda: scanner.scan(inventory, campaign)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m _cert.harness", description=__doc__)
    parser.add_argument(
        "--cell",
        action="append",
        default=[],
        metavar="PATH",
        help="cell path under verification/manifests, without .toml (repeatable, globs allowed). "
        "Default: the three happy-path cells.",
    )
    parser.add_argument("--update-image", default="", help="the second published image reference UPDATE rolls to")
    parser.add_argument("--project-id", default="", help="Astrolift project the campaign apps are registered into")
    parser.add_argument("--source-repo", default="calliopeai/astrolift-sample-api")
    parser.add_argument("--campaign", default=CAMPAIGN.slug)
    parser.add_argument("--region", default="", help="AWS region for the orphan scan")
    parser.add_argument("--gcp-project", default="")
    parser.add_argument("--azure-subscription", default="")
    parser.add_argument("--azure-resource-group", default="")
    parser.add_argument("--no-reproduce", action="store_true", help="run one pass only; step 7 reports as skipped")
    parser.add_argument("--no-rollback", action="store_true", help="skip the rollback assertion inside UPDATE")
    parser.add_argument(
        "--estimate-logged",
        action="store_true",
        help="assert that every selected class-C variant has a live-pricing estimate logged on the campaign issue",
    )
    parser.add_argument("--json", metavar="PATH", default="", help="also write the grid as JSON")
    parser.add_argument(
        "--i-have-credentials",
        action="store_true",
        help="required. Every cycle provisions billable cloud resources through a real control plane.",
    )
    args = parser.parse_args(argv)

    if not args.i_have_credentials:
        parser.error(
            "refusing to run without --i-have-credentials: every cycle provisions billable resources "
            "through a live control plane, and Phase 1 of the campaign is credential-free by design"
        )
    if not args.project_id:
        parser.error("--project-id is required; it is the project the campaign apps are registered into")

    campaign = Campaign(args.campaign)
    paths = _cell_paths(args.cell)
    if not paths:
        parser.error(f"no manifests matched {args.cell or 'happy-path/*'} under {MANIFEST_ROOT.name}/")

    grid = Grid()
    for path in paths:
        try:
            cell = Cell.load(path)
        except UnrunnableCell as exc:
            print(f"skipping {path.name}: {exc}", file=sys.stderr)
            continue
        runner = LifecycleRunner(
            platform=CliPlatformClient(project_id=args.project_id, source_repo=args.source_repo),
            scan=_scan_for(
                cell.cloud,
                campaign,
                args.region,
                args.gcp_project,
                args.azure_resource_group,
                args.azure_subscription,
            ),
            update_image_ref=args.update_image,
            include_rollback=not args.no_rollback,
            estimate_logged=args.estimate_logged,
        )
        grid.add(runner.run_cell(cell, reproduce=not args.no_reproduce))

    print(grid.render())
    if args.json:
        Path(args.json).write_text(json.dumps(grid.as_dict(), indent=2), encoding="utf-8")
    return 0 if grid.is_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
