"""One org's app label per shared managed zone (#1930), and the hostname
ledger that makes the *rendered* form unique too (#2012).

Hostnames in a managed zone carry no org (``<label>.<zone>``, previews
``pr-<n>-<slug>.pr.<zone>``), and every org without a zone of its own
resolves to the same shared (org NULL) zone. So another org's app with the
same label would get the same hostname: its Ingress host, its CDN CNAME.
A new label is refused when a live app of another org already claims it in
a shared zone this org would use. Existing apps keep what they have.

``hostname_label_refusal`` compares *labels* (an app's slug/subdomain), so it
cannot see a multi-workload app's suffixed hostname (``<label>-<workload>``)
colliding with another org's plain label of that name — the renderer never
runs before the label check, because at register/rename time the workload
set that will eventually exist isn't known yet. The rest of this module is
the ledger that closes that gap: ``astrolift_registry.models.HostnameClaim``
holds one row per (zone, hostname) a live app/workload/environment actually
renders, with a database-level unique constraint on the rendered string, and
these functions keep it in sync at every point #1930 already checks plus
``setAppSubdomain``'s full rename (which now also gates on the ledger,
because by rename time the app's live workload set — and so its true
rendered hostnames — is fully known).

Two write modes:

* ``sync_workload_hostname_claims`` — best-effort. Called from
  ``persist_manifest`` (every manifest apply: register, resync, agent
  repo scans) and from environment creation (bootstrap, the builder path),
  none of which may fail the caller's operation over a hostname (manifest
  persistence and environment bootstrap are deliberately "must survive"
  paths elsewhere in this codebase). A hostname another app already holds
  is left unclaimed and logged rather than stolen; the report command
  (``report_shared_zone_hostname_collisions``) is the place to go looking
  for what that leaves outstanding.
* ``hostname_claim_refusal`` — hard gate. Called from ``setAppSubdomain``,
  which can refuse cleanly because nothing is written until it passes.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def hostname_label_refusal(label: str, *, organization, managed_domain=None) -> str | None:
    """Why ``organization`` may not take ``label`` in a shared zone, or ``None``.

    ``managed_domain`` is the zone the caller picked explicitly; otherwise the
    org's resolved tenant-app and preview zones are checked. A label is
    claimed by another org's live app whose slug or subdomain is ``label``
    (previews use the slug, so both count) and that serves from, or resolves
    to, the same shared zone.
    """
    from django.db.models import Q

    from astrolift_clusters.models.managed_domain import resolve_managed_domain
    from astrolift_registry.models import RegisteredApp

    label = (label or "").strip().lower()
    if not label or organization is None:
        return None
    purposes = {False: managed_domain} if managed_domain is not None else {}
    for preview in (False, True):
        purposes.setdefault(preview, resolve_managed_domain(organization, for_preview=preview))
    shared = {
        preview: zone
        for preview, zone in purposes.items()
        if zone is not None and zone.organization_id is None
    }
    if not shared:
        return None

    others = (
        RegisteredApp.objects.filter(Q(slug=label) | Q(subdomain=label), deleted_at__isnull=True)
        .exclude(organization_id=organization.pk)
        .select_related("organization")
    )
    for app in others:
        serving = set(
            app.environments.filter(deleted_at__isnull=True, managed_domain__isnull=False).values_list(
                "managed_domain_id", flat=True
            )
        )
        for preview, zone in shared.items():
            resolved = resolve_managed_domain(app.organization, for_preview=preview)
            if zone.pk in serving or (resolved is not None and resolved.pk == zone.pk):
                return (
                    f"{label!r} is already used by another organization's app in {zone.zone}; choose another"
                )
    return None


# ---- the rendered-hostname ledger (#2012) ------------------------------


def _public_workload_manifest(app):
    """Adapter: ``app``'s live Workload rows, shaped as a ``NormalizedManifest``.

    For call sites with no manifest object in hand (``setAppSubdomain``,
    environment creation) — only ``name`` / ``kind`` / ``is_public`` feed
    ``compute_hostnames``, so a minimal per-workload stand-in is enough.
    ``name`` is set from ``Workload.slug``, not ``Workload.name``: manifest
    workloads are keyed by name (``persist_manifest`` writes that same value
    into both ``Workload.name`` and ``Workload.slug`` at creation and never
    revises either on update), so the slug is the stable identity that
    matches what a fresh manifest's ``WorkloadManifest.name`` would carry.
    """
    from astrolift_manifest.types import NormalizedManifest, WorkloadManifest

    workloads = tuple(
        WorkloadManifest(name=w.slug, kind=w.kind, is_public=w.is_public)
        for w in app.workloads.filter(deleted_at__isnull=True).order_by("pk")
    )
    return NormalizedManifest(
        name=app.slug,
        workloads=workloads,
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )


def _desired_claims(app, manifest, *, subdomain: str | None = None):
    """Yield ``(environment, workload_slug, hostname)`` for every live
    *non-preview* environment of ``app`` that has a managed zone, rendering
    ``manifest`` with ``subdomain`` (or the app's own) as the label —
    exactly what ``compute_hostnames`` would put on the wire.

    Excludes preview environments (``previewed_environment`` set): a preview
    renders its own hostname scheme (``pr-<n>-<app>.pr.<zone>`` or the manual
    ``preview-<branch>.<app>.<org>.<zone>`` form), never ``compute_hostnames``,
    so claiming what this function computes for one would ledger a hostname
    nothing actually serves.
    """
    from astrolift_manifest.hostname import HostnameInputs, compute_hostnames

    label = subdomain if subdomain is not None else (app.subdomain or app.slug)
    org_slug = app.organization.slug if app.organization_id else "none"
    envs = app.environments.filter(
        deleted_at__isnull=True,
        managed_domain__isnull=False,
        previewed_environment__isnull=True,
    ).select_related("managed_domain")
    for env in envs:
        for wh in compute_hostnames(
            manifest,
            HostnameInputs(
                app_slug=app.slug,
                org_slug=org_slug,
                base_zone=env.managed_domain.zone,
                subdomain_override=label,
            ),
        ):
            yield env, wh.workload_slug, wh.hostname


def hostname_claim_refusal(app, *, subdomain: str, manifest=None) -> str | None:
    """First reason ``subdomain`` cannot be claimed for ``app``, or ``None``.

    Renders every hostname ``app`` would serve under ``subdomain`` across
    its own live environments (using its current live workloads, or
    ``manifest`` when the caller already has a fresh one) and refuses when
    any of them is already held, in the ledger, by a different app. Unlike
    ``hostname_label_refusal`` this sees the multi-workload suffixed form,
    because the app's full workload set is already known here.

    Read-only — callers that pass still call
    ``sync_workload_hostname_claims`` to commit the ledger.
    """
    from astrolift_registry.models import HostnameClaim

    manifest = manifest if manifest is not None else _public_workload_manifest(app)
    for env, _workload_slug, hostname in _desired_claims(app, manifest, subdomain=subdomain):
        other = (
            HostnameClaim.objects.filter(
                managed_domain_id=env.managed_domain_id,
                hostname=hostname,
                deleted_at__isnull=True,
            )
            .exclude(registered_app_id=app.pk)
            .select_related("organization")
            .first()
        )
        if other is not None:
            return (
                f"{hostname!r} is already used by organization {other.organization.slug!r} "
                f"in {env.managed_domain.zone}; choose another subdomain"
            )
    return None


def sync_workload_hostname_claims(app, manifest=None) -> list[str]:
    """Reconcile the ledger to ``app``'s current desired hostname set.

    Best-effort and never raises: a hostname another app already holds is
    left with its existing owner (never stolen) and reported back as a
    conflict message for the caller to log, not enforced here — the
    callers that reach this (``persist_manifest``, environment bootstrap,
    the builder path, imported agents) are all "must survive" paths
    elsewhere in this codebase, so a hostname collision degrades a claim,
    it does not fail a registration or a resync.

    Releases (soft-deletes) any of ``app``'s own claims that are no longer
    desired — a removed or renamed workload, a narrowed subdomain, a
    workload flipped private — so the ledger only ever describes live
    hostnames. Pass ``manifest`` when the caller already has a freshly
    parsed one (``persist_manifest``); other callers fall back to the
    app's current live ``Workload`` rows.
    """
    from django.db import IntegrityError, transaction
    from django.utils import timezone

    from astrolift_registry.models import HostnameClaim

    manifest = manifest if manifest is not None else _public_workload_manifest(app)
    desired = list(_desired_claims(app, manifest))
    conflicts: list[str] = []
    now = timezone.now()

    with transaction.atomic():
        mine = {
            (row.environment_id, row.workload_id): row
            for row in HostnameClaim.objects.filter(registered_app_id=app.pk, deleted_at__isnull=True)
        }
        keep: set[int] = set()
        for env, workload_slug, hostname in desired:
            workload = app.workloads.filter(slug=workload_slug, deleted_at__isnull=True).only("pk").first()
            if workload is None:
                # Between the manifest snapshot and this call the workload
                # vanished (soft-deleted concurrently) — nothing to attach
                # the claim to; skip rather than claim a hostname no live
                # workload answers on.
                continue
            other = (
                HostnameClaim.objects.filter(
                    managed_domain_id=env.managed_domain_id,
                    hostname=hostname,
                    deleted_at__isnull=True,
                )
                .exclude(registered_app_id=app.pk)
                .select_related("organization", "registered_app")
                .first()
            )
            if other is not None:
                conflicts.append(
                    f"{hostname!r} is already used by organization {other.organization.slug!r}'s "
                    f"app {other.registered_app.slug!r}"
                )
                log.warning(
                    "hostname claim conflict: app %s (org %s) wants %r, held by app %s (org %s)",
                    app.slug,
                    app.organization_id,
                    hostname,
                    other.registered_app.slug,
                    other.organization_id,
                )
                continue

            existing = mine.get((env.pk, workload.pk))
            if (
                existing is not None
                and existing.hostname == hostname
                and existing.managed_domain_id == env.managed_domain_id
            ):
                keep.add(existing.pk)
                continue
            try:
                with transaction.atomic():
                    row = HostnameClaim.objects.create(
                        managed_domain_id=env.managed_domain_id,
                        hostname=hostname,
                        organization=app.organization,
                        registered_app=app,
                        environment=env,
                        workload=workload,
                    )
            except IntegrityError:
                # Lost a race to another transaction between the read above
                # and this insert — the database constraint is the real
                # backstop the ledger exists for. Leave it with the winner.
                conflicts.append(f"{hostname!r} was just claimed by another app")
                log.warning("hostname claim race for %r in zone %s", hostname, env.managed_domain.zone)
                continue
            keep.add(row.pk)

        for (_env_id, _workload_id), row in mine.items():
            if row.pk not in keep:
                row.deleted_at = now
                row.save(update_fields=["deleted_at", "updated_at", "version"])

    return conflicts


def release_app_hostname_claims(app) -> int:
    """Soft-delete every live ledger claim for ``app``. Returns the count.

    Called at app teardown/deregister and at the immediate ``softDeleteApp``
    path — both are "the app is gone" moments, so every claim releases
    together rather than being reconciled workload-by-workload.
    """
    from django.utils import timezone

    from astrolift_registry.models import HostnameClaim

    now = timezone.now()
    released = 0
    for row in HostnameClaim.objects.filter(registered_app_id=app.pk, deleted_at__isnull=True):
        row.deleted_at = now
        row.save(update_fields=["deleted_at", "updated_at", "version"])
        released += 1
    return released
