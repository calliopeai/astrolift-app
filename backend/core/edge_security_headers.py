"""Per-app security headers at the ingress-nginx edge.

An app can ask for a fixed baseline of response headers on every response its
hosts send through the ingress, whether or not the app sets any. Off by
default (``RegisteredApp.security_headers``). HSTS is not here: the controller
applies it cluster-wide.

Only ingress-nginx honours this, through ``configuration-snippet``. Envoy and
every other ingress class render nothing, and the app is unchanged there.

The Content-Security-Policy is added, never replaced: an app's own richer
policy survives, and a browser enforces both. The other three are single-value
headers, so the upstream's copy is hidden before ours is added; two copies of
one of them can make a browser ignore it.

The platform's block is fenced so a snippet that already carries other
content (the auth gate's, or an app's own) is merged into, not overwritten.
"""

from __future__ import annotations

from typing import Any

SNIPPET_KEY = "nginx.ingress.kubernetes.io/configuration-snippet"

BEGIN = "# BEGIN astrolift managed security headers"
END = "# END astrolift managed security headers"

#: Ingress classes that read ``configuration-snippet``.
NGINX_CLASSES = ("nginx", "ingress-nginx")

_BLOCK = "\n".join(
    [
        BEGIN,
        "add_header Content-Security-Policy \"frame-ancestors 'none'\" always;",
        "proxy_hide_header X-Frame-Options;",
        'add_header X-Frame-Options "DENY" always;',
        "proxy_hide_header Referrer-Policy;",
        'add_header Referrer-Policy "same-origin" always;',
        "proxy_hide_header X-Content-Type-Options;",
        'add_header X-Content-Type-Options "nosniff" always;',
        END,
    ]
)


def _strip_block(existing: str) -> str:
    text = existing or ""
    while True:
        start = text.find(BEGIN)
        if start == -1:
            return text.strip("\n")
        end = text.find(END, start)
        if end == -1:
            return text[:start].strip("\n")
        text = text[:start] + text[end + len(END) :]


def security_headers_snippet(enabled: bool, existing: str = "") -> str | None:
    """The ``configuration-snippet`` value, or ``None`` when off.

    ``existing`` is the snippet already on the Ingress; it is kept as it is,
    with any earlier copy of this block replaced, so the result is stable
    across renders.
    """
    if not enabled:
        return None
    foreign = _strip_block(existing)
    return "\n".join(p for p in (foreign, _BLOCK) if p) + "\n"


def honours_security_headers(cluster: Any) -> bool:
    """Whether ``cluster``'s ingress reads the snippet (ingress-nginx only)."""
    return (getattr(cluster, "ingress_class", "") or "") in NGINX_CLASSES


def apply_security_headers(annotations: dict[str, str], app: Any, cluster: Any) -> dict[str, str]:
    """Merge the headers into ``annotations`` when the app asked and the edge honours them."""
    if not honours_security_headers(cluster):
        return annotations
    snippet = security_headers_snippet(
        bool(getattr(app, "security_headers", False)), annotations.get(SNIPPET_KEY, "")
    )
    if snippet is not None:
        annotations[SNIPPET_KEY] = snippet
    return annotations
