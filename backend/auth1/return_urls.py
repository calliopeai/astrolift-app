"""Where a finished sign-in may send the browser.

The IdP callback hands the frontend a one-time code for its new session
(``code=``, see :mod:`auth1.handoff`) on the return address, so the address
decides who receives the sign-in. Two shapes qualify: a path on this host, or
the frontend's ``/auth/callback`` on an origin this install serves its
frontend from. Anything else is refused and the caller falls back to its
default.

Browsers read a URL by the WHATWG rules, not Python's: they drop tab and
newline anywhere and treat ``\\`` as ``/``. A return address is plain ASCII
(the frontend builds it with ``encodeURIComponent``), so a control character,
space, backslash or non-ASCII character refuses it outright.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

FRONTEND_CALLBACK_PATH = "/auth/callback"
# Query keys the callback writes itself, or once wrote; a return address may
# not bring them.
CALLBACK_RESULT_KEYS = frozenset({"code", "token", "error"})
_MAX_LENGTH = 2048
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _plain(value: object) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= _MAX_LENGTH or "\\" in value:
        return None
    if not all(0x21 <= ord(ch) <= 0x7E for ch in value):
        return None
    return value


def _origin(url: str) -> tuple[str, str, int] | None:
    """``(scheme, host, port)`` of an absolute http(s) URL without userinfo, else None."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    return parts.scheme, parts.hostname.lower(), port or _DEFAULT_PORTS[parts.scheme]


def frontend_origins(own_origins: tuple[str, ...] = ()) -> set[tuple[str, str, int]]:
    """The origins a return address may name: the configured frontend and CORS
    origins, plus ``own_origins`` (the host serving this request)."""
    candidates = [settings.FRONTEND_URL, *settings.CORS_ALLOWED_ORIGINS, *own_origins]
    return {origin for origin in (_origin(c) for c in candidates if c) if origin}


def safe_relative_path(value: object) -> str | None:
    """``value`` when it is a path on this host, else None."""
    path = _plain(value)
    if path is None or not path.startswith("/") or path.startswith("//"):
        return None
    if not url_has_allowed_host_and_scheme(path, allowed_hosts=set()):
        return None
    parts = urlsplit(path)
    if parts.scheme or parts.netloc:
        return None
    return path


def safe_return_url(value: object, own_origins: tuple[str, ...] = ()) -> str | None:
    """The return address a sign-in may send its session to, or None.

    A path on this host passes as given. An absolute URL must be the frontend
    callback on an allowed origin (scheme, host and port exactly); it comes
    back rebuilt from its parts, without a fragment and without the keys the
    callback writes.
    """
    relative = safe_relative_path(value)
    if relative is not None:
        return relative
    url = _plain(value)
    if url is None:
        return None
    allowed = frontend_origins(own_origins)
    if _origin(url) not in allowed:
        return None
    hosts = {f"{host}:{port}" for _, host, port in allowed}
    hosts |= {host for scheme, host, port in allowed if _DEFAULT_PORTS[scheme] == port}
    if not url_has_allowed_host_and_scheme(url, allowed_hosts=hosts):
        return None
    parts = urlsplit(url)
    if parts.path != FRONTEND_CALLBACK_PATH:
        return None
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if key not in CALLBACK_RESULT_KEYS
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc.lower(), FRONTEND_CALLBACK_PATH, query, ""))
