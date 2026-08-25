"""The cross-domain session handoff exchange endpoint (#1631).

Step two of the handoff. The auth host completes the IdP flow at its
existing single callback, mints a `DomainSessionHandoff`, and redirects the
browser to the custom domain carrying an opaque id. The domain's own proxy
then calls **this** endpoint, server to server, to trade that id for the
verified identity, and sets its own first-party cookie.

**On the control plane rather than the auth host**, which was one of the two
open questions on the issue. Built to the recommendation I gave there: "who
established a session on which custom domain and when" is an audit
question, the auth host has no audit log, and the control plane already has
one. If that call is reversed the view moves; nothing else does.

The other open question -- whether the domain's proxy mints its own session
or maps back to the auth host's -- does not reach this endpoint. It returns
a verified identity either way; what the caller does with it is where the
single-logout decision lives.

**Server-to-server only.** The browser never calls this. It carries an
opaque id in a redirect and nothing else, so a leaked URL costs a spent
grant rather than an identity. That the caller is the proxy rather than the
browser is what lets the response contain a subject at all.
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_POST

log = logging.getLogger("astrolift.lifecycle.domain_handoff")

# Every rejection answers the same way. An endpoint that distinguishes "no
# such token" from "expired" from "wrong audience" tells an attacker which
# of those they got wrong; the real reason goes to the log and the audit
# trail, never to the caller.
_REFUSED = {"ok": False, "error": "handoff_refused"}


@require_POST
def exchange_domain_handoff(request: HttpRequest) -> HttpResponse:
    """Spend a handoff token and return the identity it grants.

    ``POST {"token": "...", "hostname": "shop.customer.com"}``

    Answers 200 with ``{"ok": true, "subject": ..., "email": ...}`` or 401
    with a uniform refusal. Never 500s on a bad token: a 500 on the auth
    path is itself an oracle, and the shapes that would raise -- malformed
    JSON, a missing field, a token that is not a string -- are exactly the
    ones an attacker probes with.
    """
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse(_REFUSED, status=401)

    if not isinstance(payload, dict):
        return JsonResponse(_REFUSED, status=401)

    token = payload.get("token")
    hostname = payload.get("hostname")
    if not isinstance(token, str) or not isinstance(hostname, str):
        return JsonResponse(_REFUSED, status=401)
    if not token.strip() or not hostname.strip():
        return JsonResponse(_REFUSED, status=401)

    from core.domain_handoff import consume_domain_handoff

    identity, reason = consume_domain_handoff(token, hostname=hostname)
    if identity is None:
        # The reason is the useful half and it stays server-side. Logged
        # with the hostname, never the token: the token is a credential
        # right up until it is spent, and a log line is a place it would
        # outlive its 60 seconds.
        log.warning(
            "domain handoff refused for %s: %s",
            hostname.strip().lower(),
            getattr(reason, "value", reason),
        )
        return JsonResponse(_REFUSED, status=401)

    log.info("domain handoff exchanged for %s", identity.hostname)
    return JsonResponse(
        {
            "ok": True,
            "subject": identity.subject,
            "email": identity.email,
            "hostname": identity.hostname,
        }
    )
