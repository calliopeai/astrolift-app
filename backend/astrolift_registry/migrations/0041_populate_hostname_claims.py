"""Populate HostnameClaim from existing apps (#2012).

Renders every live app's public hostnames, exactly as
``astrolift_manifest.hostname.compute_hostnames`` would, across every live
non-preview environment that has a managed zone, and records one claim per
(zone, hostname). Preview environments are excluded: they render their own
hostname scheme, never ``compute_hostnames``. ``compute_hostnames`` is pure
Python (no models, no DB) so it is safe to import directly in a migration;
every DB row is read through the historical ``apps.get_model`` graph.

Pre-existing collisions are the entire reason this table needs a data
migration rather than starting empty: two orgs may already render the same
hostname in a shared zone (#1930's own bug report), and this pass must not
fail the deploy over that. The first claimant wins, chosen deterministically
by app creation order (oldest app first), an arbitrary but stable and
auditable rule, and every hostname it would have collided with is logged,
not written. ``report_shared_zone_hostname_collisions`` is the durable,
re-runnable way to find what this migration left out; this migration only
runs once.
"""

from __future__ import annotations

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def populate_hostname_claims(apps, schema_editor):
    from astrolift_manifest.hostname import HostnameInputs, compute_hostnames
    from astrolift_manifest.types import NormalizedManifest, WorkloadManifest

    AppEnvironment = apps.get_model("astrolift_lifecycle", "AppEnvironment")
    HostnameClaim = apps.get_model("astrolift_registry", "HostnameClaim")
    Workload = apps.get_model("astrolift_registry", "Workload")

    envs = (
        AppEnvironment.objects.filter(
            deleted_at__isnull=True,
            managed_domain__isnull=False,
            previewed_environment__isnull=True,
            registered_app__deleted_at__isnull=True,
        )
        .select_related("registered_app", "registered_app__organization", "managed_domain")
        .order_by("registered_app__created_at", "registered_app_id", "pk")
    )

    claimed: dict[tuple[int, str], tuple[str, str]] = {}
    created = 0
    skipped = 0

    for env in envs:
        app = env.registered_app
        organization = app.organization
        if organization is None:
            # An app with no organization is unreachable through any
            # tenant-scoped path; nothing could ever collide with it, and
            # HostnameClaim.organization is required. Skip rather than
            # invent an owner.
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

        for wh in compute_hostnames(
            manifest,
            HostnameInputs(
                app_slug=app.slug,
                org_slug=organization.slug,
                base_zone=env.managed_domain.zone,
                subdomain_override=label,
            ),
        ):
            key = (env.managed_domain_id, wh.hostname)
            if key in claimed:
                holder_org, holder_app = claimed[key]
                logger.warning(
                    "hostname_claim migration: %r in zone %s already claimed by org %s app %s; "
                    "leaving org %s app %s (env %s, workload %s) unclaimed -- see "
                    "report_shared_zone_hostname_collisions",
                    wh.hostname,
                    env.managed_domain.zone,
                    holder_org,
                    holder_app,
                    organization.slug,
                    app.slug,
                    env.name,
                    wh.workload_slug,
                )
                skipped += 1
                continue
            workload = next((w for w in workloads if w.slug == wh.workload_slug), None)
            if workload is None:
                continue
            HostnameClaim.objects.create(
                managed_domain_id=env.managed_domain_id,
                hostname=wh.hostname,
                organization=organization,
                registered_app=app,
                environment=env,
                workload=workload,
            )
            claimed[key] = (organization.slug, app.slug)
            created += 1

    logger.info(
        "hostname_claim migration: created %d claim(s), left %d pre-existing collision(s) unclaimed",
        created,
        skipped,
    )


def clear_hostname_claims(apps, schema_editor):
    HostnameClaim = apps.get_model("astrolift_registry", "HostnameClaim")
    HostnameClaim.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0040_hostname_claim"),
    ]

    operations = [
        migrations.RunPython(populate_hostname_claims, clear_hostname_claims),
    ]
