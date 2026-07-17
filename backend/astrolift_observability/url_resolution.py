"""URL-resolution helpers for the health-probe resolver (#406).

The resolver only probes URLs the app already owns: the per-env
``url`` field, plus the public ingress hosts synthesized from
``<workload.slug>.<app.subdomain>``. Anything else is rejected at
the boundary so a caller with ``APP_READ`` can't turn the platform
into an open-relay HTTP fetcher.

Functions here are pure, take a ``RegisteredApp`` instance, and
return concrete URL strings; the resolver layer is the only caller.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def app_urls(app) -> list[str]:
    """Every URL we'll probe on behalf of ``app``.

    Order is stable (env URLs in created-at order, then the public
    workload subdomain hosts in alphabetical slug order) so callers
    that key on position don't churn between requests.

    The output is deduplicated case-insensitively on host + path so
    a workload host that an operator also wrote into an env URL row
    shows up once.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import Workload

    urls: list[str] = []

    env_qs = (
        AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True)
        .exclude(url="")
        .order_by("created_at")
    )
    for env in env_qs:
        normalized = _normalize(env.url)
        if normalized:
            urls.append(normalized)

    # Managed-domain hostname: <subdomain>.<zone> from the platform ManagedDomain.
    # This is the canonical public URL after the managed-domain setup.
    if app.subdomain:
        try:
            from astrolift_clusters.models import resolve_managed_domain

            org = getattr(app, "organization", None)
            if org is None and app.organization_id:
                from astrolift_identity.models import Organization

                org = Organization.objects.filter(pk=app.organization_id).first()
            md = resolve_managed_domain(org, for_preview=False)
            if md:
                managed_url = _normalize(f"https://{app.subdomain}.{md.zone}/")
                if managed_url:
                    urls.append(managed_url)
        except Exception:
            pass

    if app.subdomain:
        public_workloads = Workload.objects.filter(
            registered_app=app, is_public=True, deleted_at__isnull=True
        ).order_by("slug")
        for workload in public_workloads:
            host = f"{workload.slug}.{app.subdomain}"
            urls.append(_normalize(f"https://{host}/"))

    # Dedup preserving first-seen order. Equality is case-insensitive
    # on scheme + host; path/query is retained as-is.
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        key = _dedup_key(url)
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def normalize_url(url: str) -> str | None:
    """Public wrapper so the resolver can normalize an inbound URL
    before comparing it against ``app_urls`` membership."""
    return _normalize(url)


def resolved_public_host(app) -> str | None:
    """The app's canonical public FQDN (host only, no scheme/path).

    Used by the TLS-certificate resolver to look up the cert covering
    the app: passing the app *slug* never matched a real cert (a
    ``*.zone`` wildcard or a ``zone``-suffixed SAN covers the host
    ``<subdomain>.<zone>``, not the bare slug). Returns the host of the
    first URL :func:`app_urls` resolves — the managed-domain host
    ``<subdomain>.<zone>`` when no explicit env URL is set, which is
    exactly what a platform-issued wildcard/SAN cert is minted for.

    ``None`` when the app has no resolvable public URL (no env URL and
    no managed subdomain) — the caller treats that as "not configured"
    rather than guessing a hostname."""
    for url in app_urls(app):
        parts = urlsplit(url)
        if parts.netloc:
            return parts.netloc.lower()
    return None


def _normalize(url: str) -> str | None:
    """Strip trailing whitespace, default ``/`` path, lowercase scheme
    + host. Returns ``None`` on a URL we can't parse (no scheme, no
    host) so the resolver can reject it cleanly.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return None
    if parts.scheme.lower() not in {"http", "https"}:
        # Don't probe non-HTTP schemes — there's nothing the FE can
        # render a status code for.
        return None
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _dedup_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path or '/'}"
