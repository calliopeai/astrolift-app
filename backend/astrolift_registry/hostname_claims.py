"""One org's app label per shared managed zone (#1930).

Hostnames in a managed zone carry no org (``<label>.<zone>``, previews
``pr-<n>-<slug>.pr.<zone>``), and every org without a zone of its own
resolves to the same shared (org NULL) zone. So another org's app with the
same label would get the same hostname: its Ingress host, its CDN CNAME.
A new label is refused when a live app of another org already claims it in
a shared zone this org would use. Existing apps keep what they have.
"""

from __future__ import annotations


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
