"""
Per-org subdomain + cookie scoping policy (#163, spec 03 §3.4).

When an org configures a vanity subdomain (e.g.
``acme.platform.example.com``), three rules kick in:

1. **IdP enforcement.** Requests landing on the org subdomain must
   authenticate via that org's configured IdP. A user signed into
   org B cannot access the platform UI on org A's subdomain.
2. **Cookie scoping.** Session cookies set on the org subdomain
   carry ``Domain=acme.platform.example.com`` (the exact host —
   not the parent zone) so a leaked cookie from org A is rejected
   by org B's domain-binding check.
3. **Custom domains.** When an org points a CNAME from
   ``platform.acme.com`` to ``acme.platform.example.com``, the
   policy treats requests landing on either as the same org —
   but cookies stay scoped to whichever host issued them.

Pure module — no Django request/response. The middleware that
sets cookies + the OIDC callback that validates IdP both call
into this for the same answers.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Final

# Reserved labels — operators / system / api should never resolve
# to an org subdomain even if a typo lets one through.
RESERVED_SUBDOMAIN_LABELS: Final[frozenset[str]] = frozenset(
    {
        "www",
        "api",
        "admin",
        "auth",
        "platform",
        "static",
        "assets",
        "cdn",
        "status",
        "mail",
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class OrgSubdomainConfig:
    """The per-org subdomain settings the resolver consults.

    ``base_zone`` is the install's well-known public zone
    (``platform.example.com``); ``subdomain_label`` is the org's
    chosen prefix (``acme``). ``custom_domains`` is the list of
    operator-verified CNAMEs that should be treated as alternate
    homes for this org.
    """

    org_id: int
    org_slug: str
    base_zone: str
    subdomain_label: str = ""
    custom_domains: tuple[str, ...] = ()
    idp_id: int | None = None

    def __post_init__(self) -> None:
        if not self.org_slug:
            raise ValueError("org_slug is required")
        if self.subdomain_label and self.subdomain_label.lower() in RESERVED_SUBDOMAIN_LABELS:
            raise ValueError(f"subdomain label {self.subdomain_label!r} is reserved")

    @property
    def fqdn(self) -> str:
        """``<label>.<base_zone>`` — empty when no per-org subdomain
        is configured (the org uses the install's default host)."""
        if not self.subdomain_label:
            return ""
        return f"{self.subdomain_label}.{self.base_zone}"


@dataclasses.dataclass(frozen=True, slots=True)
class HostBinding:
    """The output of resolving a request's Host header to an org."""

    org_id: int | None
    org_slug: str
    host: str
    cookie_domain: str
    requires_idp_id: int | None


class CrossOrgAccessDenied(Exception):
    """Raised when a session cookie from org A is presented on org
    B's subdomain. The middleware turns this into a sign-in redirect
    on the correct subdomain."""

    def __init__(self, *, presented_org_slug: str, host_org_slug: str):
        self.presented_org_slug = presented_org_slug
        self.host_org_slug = host_org_slug
        super().__init__(
            f"session for org {presented_org_slug!r} presented on " f"org {host_org_slug!r}'s subdomain"
        )


def resolve_host(*, host: str, configs: Sequence[OrgSubdomainConfig]) -> HostBinding:
    """Map the request's Host header to an org configuration.

    Order of match:
      1. Exact match on a configured FQDN (org subdomain).
      2. Exact match on a custom domain.
      3. Otherwise → 'install default host' — no org binding,
         caller renders the install's selector / sign-in page.

    The cookie domain returned is the exact host that matched —
    never the parent zone — so a cookie set on
    ``acme.platform.example.com`` is rejected by the browser when
    sent to ``other.platform.example.com``.
    """
    if not host:
        raise ValueError("host is required")
    host = host.lower().split(":")[0]  # strip port

    for cfg in configs:
        if cfg.fqdn and cfg.fqdn == host:
            return HostBinding(
                org_id=cfg.org_id,
                org_slug=cfg.org_slug,
                host=host,
                cookie_domain=host,
                requires_idp_id=cfg.idp_id,
            )
        for custom in cfg.custom_domains:
            if custom.lower() == host:
                return HostBinding(
                    org_id=cfg.org_id,
                    org_slug=cfg.org_slug,
                    host=host,
                    cookie_domain=host,
                    requires_idp_id=cfg.idp_id,
                )

    # No org subdomain match — install default host.
    return HostBinding(
        org_id=None,
        org_slug="",
        host=host,
        cookie_domain=host,
        requires_idp_id=None,
    )


def check_session_against_host(
    *,
    binding: HostBinding,
    session_org_slug: str,
) -> None:
    """Raise :class:`CrossOrgAccessDenied` if a session bound to one
    org is presented on another org's subdomain.

    Sessions on the install default host (no binding) accept any
    session — the user picks which org to enter.
    """
    if binding.org_id is None or not binding.org_slug:
        return  # default host, no enforcement
    if not session_org_slug:
        # Anonymous request on a per-org subdomain — caller redirects
        # to the org's IdP. Not an exception path.
        return
    if session_org_slug != binding.org_slug:
        raise CrossOrgAccessDenied(
            presented_org_slug=session_org_slug,
            host_org_slug=binding.org_slug,
        )


def cookie_attrs_for(binding: HostBinding, *, secure: bool = True) -> dict[str, str | bool]:
    """Cookie kwargs the middleware applies when setting session
    cookies. ``Domain`` matches the host exactly (no leading dot —
    that would extend to subdomains and defeat the isolation)."""
    return {
        "domain": binding.cookie_domain,
        "secure": secure,
        "httponly": True,
        "samesite": "Lax",
        "path": "/",
    }
