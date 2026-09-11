"""Browser logout through the central proxy and the configured IdP (#1741)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit

LOGOUT_PATH = "/auth/logout"
END_SESSION_PATH = "/auth/end-session"
SIGNED_OUT_PATH = "/auth/logged-out"

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST = re.compile(rf"(?=.{{1,253}}\Z){_LABEL}(?:\.{_LABEL})*\Z")


def configured_logout_url(config: Mapping[str, Any] | None) -> str:
    """Validate the optional public, fixed browser redirect. Never echo its value."""
    if config is None:
        return ""
    if not isinstance(config, Mapping):
        raise ValueError("oidcAuthConfig must be an object or null")
    value = config.get("logout_url")
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError("logout_url must be an ASCII HTTPS URL without whitespace")
    try:
        url = urlsplit(value)
        valid = (
            url.scheme == "https"
            and url.hostname
            and _HOST.fullmatch(url.hostname)
            and url.username is None
            and url.password is None
            and not url.fragment
            and (url.port is None or url.port > 0)
            and not any(c in value for c in ('"', "'", "\\", "$", "{", "}", "#"))
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("logout_url must be an HTTPS URL without credentials, fragment or template characters")
    host = config.get("auth_proxy_host")
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        raise ValueError("logout_url requires an auth_proxy_host hostname without a scheme or path")
    if not config.get("client_id") or not config.get("discovery_url"):
        raise ValueError("logout_url requires a complete central OIDC auth configuration")
    return value


def redirect_snippet(path: str, target: str) -> str:
    # These run in the rewrite phase, before auth_request; an expired proxy
    # session must still be able to reach logout without starting a login.
    return (
        f"if ($uri = {path}) {{\n"
        '    add_header Cache-Control "no-store" always;\n'
        f'    return 302 "{quote(target, safe=":/?&=%+@,;!~*-._")}";\n'
        "}"
    )


def central_logout_snippet(config: Mapping[str, Any] | None) -> str:
    logout_url = configured_logout_url(config)
    if not logout_url:
        return ""
    assert config is not None
    # The proxy's rd stays local. The browser reaches the IdP only through
    # our fixed route, so logout needs no broader proxy redirect allowlist.
    return "\n".join(
        (
            redirect_snippet(
                LOGOUT_PATH,
                f"https://{config['auth_proxy_host']}/oauth2/sign_out?rd={quote(END_SESSION_PATH, safe='')}",
            ),
            redirect_snippet(END_SESSION_PATH, logout_url),
            f"if ($uri = {SIGNED_OUT_PATH}) {{\n"
            '    add_header Cache-Control "no-store" always;\n'
            '    return 200 "You have signed out.";\n'
            "}",
        )
    )
