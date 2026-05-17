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
