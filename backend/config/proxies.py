"""The proxies whose forwarding headers the backend believes (#2431).

Behind a load balancer every request arrives from the balancer, so a limit
keyed on the peer address (``key="ip"``: sign-in at 10 a minute, the
invitation preview at 30) counted everyone behind it as one client: a burst
of sign-ins locked everyone out, and no single caller could be limited. The
balancer names the client in ``X-Forwarded-For``, but anyone can send that
header. ``config.asgi`` reads it only when the peer is listed here, from the
right, and stops at the first address that is not, which is the client the
outermost trusted proxy saw. Entries a client wrote itself sit to the left
of that one and are never read.

``ASTROLIFT_TRUSTED_PROXIES`` lists the addresses or CIDR ranges of the
proxies in front of the backend, comma-separated: on an AWS install, the
subnets the load balancer sits in. Loopback is always trusted, as uvicorn
trusted it before, for the nginx sidecar. Trusting every peer (``*``, or a
``/0`` range) would let any client choose its own address, so those refuse
to start the app, and so does an entry that is not an address or range.
"""

from __future__ import annotations

import ipaddress

from django.core.exceptions import ImproperlyConfigured

SETTING = "ASTROLIFT_TRUSTED_PROXIES"
LOOPBACK = "127.0.0.1"


def parse_trusted_proxies(raw: str | None) -> tuple[str, ...]:
    """Loopback plus each entry of ``raw``, normalised; ImproperlyConfigured on a bad one."""
    trusted = [LOOPBACK]
    for entry in (item.strip() for item in (raw or "").split(",")):
        if not entry:
            continue
        if entry == "*":
            raise ImproperlyConfigured(
                f"{SETTING} may not be '*': every client could choose its own address."
            )
        try:
            if "/" in entry:
                network = ipaddress.ip_network(entry, strict=False)
                if network.prefixlen == 0:
                    raise ImproperlyConfigured(
                        f"{SETTING} may not trust {entry!r}: every client could choose its own address."
                    )
                trusted.append(str(network))
            else:
                trusted.append(str(ipaddress.ip_address(entry)))
        except ValueError:
            raise ImproperlyConfigured(f"{SETTING}: {entry!r} is not an IP address or CIDR range.") from None
    return tuple(dict.fromkeys(trusted))


def client_address(request) -> str | None:
    """The client's address: ``REMOTE_ADDR``, or None when it is not an address (#2440).

    ``config.asgi`` sets it from ``X-Forwarded-For`` as the trusted proxies
    report it, so the audit trail, session tracking, ABAC and the rate limits
    all see one client. The raw header is never read: its left-most entries
    are whatever the client wrote.
    """
    meta = getattr(request, "META", None) or {}
    try:
        return str(ipaddress.ip_address(meta.get("REMOTE_ADDR") or ""))
    except ValueError:
        return None
