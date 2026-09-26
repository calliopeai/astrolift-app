"""
``manage.py report_shared_zone_hostname_collisions``: cross-org hostname
collisions in shared managed zones (#2012).

Read-only. Renders every live app's public hostnames in every shared
(``organization`` NULL) managed zone its live environments actually use —
single-workload apps as ``<label>.<zone>``, multi-workload apps as
``<label>-<workload>.<zone>`` per workload — reusing the platform's own
renderer (``astrolift_manifest.hostname.compute_hostnames``) so the report
can never drift from what a deploy would actually produce. Lists every
hostname two or more organizations both render.

    manage.py report_shared_zone_hostname_collisions
    manage.py report_shared_zone_hostname_collisions --format json

#1930 refuses a *new* label collision going forward, comparing labels only —
it cannot see a multi-workload app's suffixed hostname colliding with
another org's plain label, and it does nothing for a collision that
predates the fix. This command is the read side for both: run it before
adopting the hostname ledger (#2012) to see what already collides, and
re-run it any time as an independent check — it recomputes from live app
state rather than reading the ``HostnameClaim`` ledger, so it also catches
a ledger that has drifted from what would actually be rendered.

Preview environments are excluded: a preview renders its own hostname
scheme (``pr-<n>-<app>.pr.<zone>`` or a manual ``preview-<branch>...`` form),
never ``compute_hostnames``, so this command would otherwise report
fictitious collisions.

Never point this at a production database from an untrusted or unreviewed
change — it is read-only, but it does run the full app catalogue through the
renderer, which is as expensive as a fleet-wide dry-run deploy.
"""

from __future__ import annotations

import json
from collections import defaultdict

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "List hostnames two or more organizations render in a shared managed zone (#2012). Read-only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("table", "json"),
            default="table",
            help="Output shape (default: table).",
        )

    def handle(self, *args, **options):
        collisions = find_shared_zone_collisions()
        if options["format"] == "json":
            self.stdout.write(json.dumps(collisions, indent=2, sort_keys=True))
        else:
            for row in collisions:
                self.stdout.write(f"{row['zone']}\t{row['hostname']}")
                for claim in row["claims"]:
                    self.stdout.write(
                        "\t{organization_slug}\t{organization_guid}\t{app_slug}\t{app_guid}\t"
                        "{environment}\t{workload}".format(**claim)
                    )
        self.stdout.write(f"{len(collisions)} colliding hostname(s) across shared zones")


def find_shared_zone_collisions() -> list[dict]:
    """Every (zone, hostname) two or more organizations both render.

    Each returned row is ``{"zone", "hostname", "claims": [...]}``, sorted by
    (zone, hostname); each claim is ``{organization_slug, organization_guid,
    app_slug, app_guid, environment, workload}``, sorted by organization then
    app slug.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_manifest.hostname import HostnameInputs, compute_hostnames
    from astrolift_manifest.types import NormalizedManifest, WorkloadManifest
    from astrolift_registry.models import Workload

    envs = (
        AppEnvironment.objects.filter(
            deleted_at__isnull=True,
            managed_domain__isnull=False,
            managed_domain__organization__isnull=True,
            previewed_environment__isnull=True,
            registered_app__deleted_at__isnull=True,
        )
        .select_related("registered_app__organization", "managed_domain")
        .order_by("registered_app__organization__slug", "registered_app__slug", "pk")
    )

    by_key: dict[tuple[int, str], list[dict]] = defaultdict(list)
    zones: dict[int, str] = {}
    for env in envs:
        app = env.registered_app
        organization = app.organization
        if organization is None:
            continue
        workloads = list(Workload.objects.filter(registered_app=app, deleted_at__isnull=True).order_by("pk"))
        manifest = NormalizedManifest(
            name=app.slug,
            workloads=tuple(
                WorkloadManifest(name=w.slug, kind=w.kind, is_public=w.is_public) for w in workloads
            ),
            managed_services=(),
            defaults_applied=(),
            serialized={},
        )
        label = app.subdomain or app.slug
        zones[env.managed_domain_id] = env.managed_domain.zone

        for wh in compute_hostnames(
            manifest,
            HostnameInputs(
                app_slug=app.slug,
                org_slug=organization.slug,
                base_zone=env.managed_domain.zone,
                subdomain_override=label,
            ),
        ):
            by_key[(env.managed_domain_id, wh.hostname)].append(
                {
                    "organization_slug": organization.slug,
                    "organization_guid": str(organization.guid),
                    "app_slug": app.slug,
                    "app_guid": str(app.guid),
                    "environment": env.name,
                    "workload": wh.workload_slug,
                }
            )

    collisions = []
    for (managed_domain_id, hostname), claims in by_key.items():
        if len({c["organization_slug"] for c in claims}) < 2:
            continue
        claims.sort(key=lambda c: (c["organization_slug"], c["app_slug"]))
        collisions.append({"zone": zones[managed_domain_id], "hostname": hostname, "claims": claims})
    collisions.sort(key=lambda row: (row["zone"], row["hostname"]))
    return collisions
